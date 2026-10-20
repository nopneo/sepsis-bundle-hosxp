"""ค้นหาเคส Sepsis จาก ICD10 และคำนวณความครบถ้วนของ Sepsis Six Bundle

อ้างอิงสคีมา/ข้อมูลจริงของ HOSxP ที่ตรวจสอบแล้ว (ต.ค. 2026):

เคส = visit ที่มาถึง ER (ovst.vn) ที่มี ICD10 sepsis ใน ovstdiag (ER/OPD) หรือ iptdiag (หลัง admit)
  รวมเคสที่ไม่ได้ admit ด้วย (refer/กลับบ้าน/เสียชีวิตที่ ER)

time-zero (โหมด er, ค่าเริ่มต้น) = ค่าแรกที่มีของ
  er_regist.sepsis_recognition_datetime -> triage_datetime -> enter_er_time -> ovst (เวลาลงทะเบียน)
  ปัจจุบัน ER ไม่ได้กรอกสองช่องแรก จึงได้ enter_er_time เป็นส่วนใหญ่

เวลาของแต่ละ element คือ "เวลาสั่ง/บันทึกคำสั่ง" ไม่ใช่เวลาให้ยาจริง — ระบบไม่มีบันทึกการให้ยา
  (ipd_mar = ตารางคำสั่งยา เวลาเท่ากับเวลาสั่ง ไม่ใช่เวลาพยาบาลให้)
  - ยาปฏิชีวนะ / IV fluid: เร็วที่สุดจาก
      er_regist.antibiotics_datetime (ถ้า ER บันทึก)
      opitemrece_log ของ visit ER — เวลาบันทึกครั้งแรกที่รายการยานั้นปรากฏ (XML delta)
      opitemrece ของ AN ที่ item_type='P' (คำสั่งบนหอ) — rxdate/rxtime
    ไม่ใช้แถว opitemrece ที่โอนมาจาก ER เพราะ vsttime/rxtime ติดเป็นเวลาลงทะเบียน visit
  - Lactate: lab_head.order_date/order_time จับคู่ด้วย hn + ช่วงเวลา
    (lab_head.vn เก็บ vn ตอน ER และเก็บ AN ตอนอยู่หอ)
  - Hemoculture: รพ. สั่งเป็นรายการค่าบริการ icode 3071625 (ไม่ผ่านระบบ lab) ใช้วิธีเดียวกับยา
    และยังดู lab_items 1986/727-729 เผื่ออนาคตสั่งผ่าน lab

ไม่มีข้อมูลโครงสร้างของ ออกซิเจน/Urine Output (er_regist_vital_sign_list.o2sat ว่างทั้งหมด)
"""

import os
import re
from functools import lru_cache
from datetime import date, datetime, time as dtime, timedelta

import nurse_notes
from db import fetch, fetch_one

# ---------------------------------------------------------------- เกณฑ์ ICD10
# HOSxP เก็บรหัสแบบไม่มีจุด เช่น A419, R572

SEPSIS_ICD10_PREFIXES = ("A40", "A41")   # Sepsis
SEPSIS_ICD10_EXACT = ("R572",)           # Septic shock


def _icd10_cond(alias="d"):
    prefixes = " or ".join(f"{alias}.icd10 like '{p}%'" for p in SEPSIS_ICD10_PREFIXES)
    exacts = " or ".join(f"{alias}.icd10 = '{e}'" for e in SEPSIS_ICD10_EXACT)
    return f"(({prefixes}) or ({exacts}))"


def fmt_icd10(code):
    """A419 -> A41.9"""
    code = (code or "").strip()
    return f"{code[:3]}.{code[3:]}" if len(code) > 3 and "." not in code else code


# ------------------------------------------------------- รหัสที่ใช้ระบุ element

def _codes(env_name, default_csv):
    raw = os.getenv(env_name, default_csv)
    return [c.strip() for c in raw.split(",") if c.strip()]


# รหัสต่างกันในแต่ละ รพ. จึงไม่มีค่าเริ่มต้น — หาได้ด้วย `python setup_check.py` แล้วใส่ใน .env
# (รพ.วังน้ำเขียว: lactate 2240, hemoculture lab 1986,727,728,729, icode 3071625, fluid 1000187,1000183)
LACTATE_LAB_ITEMS = _codes("SEPSIS_LACTATE_LAB_ITEMS", "")
HEMOCULTURE_LAB_ITEMS = _codes("SEPSIS_HEMOCULTURE_LAB_ITEMS", "")
# บาง รพ. สั่ง Hemoculture เป็นรายการค่าบริการ (nondrugitems) ไม่ผ่านระบบ lab
HEMOCULTURE_ICODES = _codes("SEPSIS_HEMOCULTURE_ICODES", "")
# crystalloid ถุงใหญ่เท่านั้น — ไม่รวม NSS 100 ml ซึ่งใช้ผสมยา ATB ไม่ใช่ fluid bolus
IV_FLUID_ICODES = _codes("SEPSIS_IV_FLUID_ICODES", "")

# ยากระตุ้นความดัน (SSC 2026 Rec 53: norepinephrine เป็นตัวแรก) — รพ.วังน้ำเขียว: NE 1611001, dopamine 1000119,1000342
NOREPINEPHRINE_ICODES = _codes("SEPSIS_NOREPINEPHRINE_ICODES", "")
DOPAMINE_ICODES = _codes("SEPSIS_DOPAMINE_ICODES", "")
# รหัส ward ที่เป็น ICU (ถ้ามี) — ใช้นับ "เข้า ICU ภายใน 6 ชม." (SSC 2026 Rec 15) ร่วมกับการ refer
ICU_WARDS = set(_codes("SEPSIS_ICU_WARDS", ""))

BUNDLE_WINDOW_MIN = int(os.getenv("SEPSIS_BUNDLE_MINUTES", "60"))
# เป้าตาม SSC 2026: ยาปฏิชีวนะ shock ภายใน 1 ชม. (Rec 16) / ไม่ shock ภายใน 3 ชม. (Rec 18)
ATB_SHOCK_MIN, ATB_NONSHOCK_MIN = 60, 180
FLUID_HOURS = 3                 # crystalloid >= 30 mL/kg ใน 3 ชม.แรก (Rec 10, conditional)
FLUID_ML_PER_KG = 30
LACTATE_REPEAT_HOURS = int(os.getenv("SEPSIS_LACTATE_REPEAT_HOURS", "6"))
ESCALATION_HOURS = 6            # refer / เข้า ICU ภายใน 6 ชม. (Rec 15)
WINDOW_END_HOURS = int(os.getenv("SEPSIS_WINDOW_HOURS", "24"))
WINDOW_BEFORE_MIN = 30   # คำสั่งก่อน time-zero เล็กน้อย (เช่นสั่งตั้งแต่ลงทะเบียน) ยังนับ
TIME_ZERO_MODE = os.getenv("SEPSIS_TIME_ZERO", "er")  # er | admission

TIME_ZERO_LABEL = {
    "sepsis_recognition": "เวลารับรู้ sepsis (ER)",
    "triage": "เวลา triage (ER)",
    "enter_er": "เวลาเข้า ER",
    "visit": "เวลาลงทะเบียน",
    "admission": "เวลา admit",
}


# --------------------------------------------------------------- ตัวช่วยเวลา

def _to_dt(d, t=None):
    """รวม date/time (object หรือ string จากทั้งสองโหมดเชื่อมต่อ) เป็น datetime เดียว"""
    if d is None:
        return None
    if isinstance(d, datetime):
        return d
    dd = d if isinstance(d, date) else datetime.strptime(str(d)[:10], "%Y-%m-%d").date()
    if t is None:
        tt = dtime()
    elif isinstance(t, dtime):
        tt = t
    elif isinstance(t, timedelta):  # pymysql คืน TIME เป็น timedelta
        tt = (datetime.min + t).time()
    else:
        s = str(t)[:8]
        tt = datetime.strptime(s, "%H:%M:%S").time() if s else dtime()
    return datetime.combine(dd, tt)


def _to_dt_single(v):
    """datetime จาก DB (object หรือ 'YYYY-MM-DD HH:MM:SS') -> datetime, ค่าว่าง/0000 -> None"""
    if v is None or v == "" or str(v).startswith("0000"):
        return None
    if isinstance(v, datetime):
        return v
    return datetime.strptime(str(v)[:19], "%Y-%m-%d %H:%M:%S")


def _sql_dt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------- คอลัมน์ที่มีเฉพาะ HOSxP รุ่นใหม่

_schema_cache = {}


def _has(table, column=None):
    """มีตาราง/คอลัมน์นี้ในฐานข้อมูลไหม — HOSxP รุ่นเก่าบางแห่งยังไม่มีช่องเวลา sepsis ใน er_regist"""
    key = (table, column)
    if key not in _schema_cache:
        cond = f"and column_name = '{column}'" if column else ""
        _schema_cache[key] = bool(fetch_one(f"""
            select 1 x from information_schema.columns
            where table_schema = database() and table_name = '{table}' {cond} limit 1"""))
    return _schema_cache[key]


def _opt(alias, table, column, as_name=None):
    as_name = as_name or column
    return f"{alias}.{column} {as_name}" if _has(table, column) else f"null {as_name}"


# --------------------------------------------------------- 1. ค้นหาเคส sepsis

def find_sepsis_cases(lookback_days=7, d1=None, d2=None):
    """visit ER/OPD ที่มี ICD10 sepsis (ที่ ER หรือหลัง admit)

    ช่วงวันอิง "วันที่มาถึง ER" (ovst.vstdate) — ระบุ d1/d2 (date) หรือใช้ N วันล่าสุด
    ฝั่ง iptdiag ขยายไป d2+1 วัน เผื่อมา ER ก่อนเที่ยงคืนแล้ว admit หลังเที่ยงคืน
    """
    if d1 is None or d2 is None:
        d2 = date.today()
        d1 = d2 - timedelta(days=int(lookback_days))
    a, b, b1 = d1.isoformat(), d2.isoformat(), (d2 + timedelta(days=1)).isoformat()
    rows = fetch(f"""
        select o.vn, o.hn, i.an, i.ward, w.name ward_name, i.cur_bedno,
               o.vstdate, o.vsttime, i.regdate, i.regtime,
               e.enter_er_time, {_opt('e', 'er_regist', 'triage_datetime')},
               {_opt('e', 'er_regist', 'sepsis_recognition_datetime')},
               {_opt('e', 'er_regist', 'antibiotics_datetime', 'er_antibiotics_datetime')},
               e.vn er_vn, e.er_dch_type, o.ovstost,
               s.bw, s.bps, s.bpd, s.pulse, s.rr, s.temperature, {_opt('s', 'opdscreen', 'o2sat')},
               {_opt('s', 'opdscreen', 'news2_score', 's_news2')},
               {_opt('s', 'opdscreen', 'consciousness_level_type_id', 's_consc')},
               {_opt('e', 'er_regist', 'news2_score', 'e_news2')},
               {_opt('e', 'er_regist', 'consciousness_level_type_id', 'e_consc')},
               (select min(timestamp(r.refer_date, r.refer_time)) from referout r
                 where r.vn = o.vn) refer_dt,
               (select h.name from referout r join hospcode h on h.hospcode = r.refer_hospcode
                 where r.vn = o.vn order by r.referout_id limit 1) refer_hosp,
               concat_ws(',',
                 (select group_concat(distinct od.icd10) from ovstdiag od
                   where od.vn = o.vn and {_icd10_cond('od')}),
                 (select group_concat(distinct d.icd10) from iptdiag d
                   where d.an = i.an and {_icd10_cond('d')})) icd10_raw
        from (
            select od.vn from ovstdiag od
             where od.vstdate between '{a}' and '{b}' and {_icd10_cond('od')}
            union
            select ii.vn from iptdiag d join ipt ii on ii.an = d.an
             where ii.regdate between '{a}' and '{b1}' and {_icd10_cond('d')}
        ) v
        join ovst o on o.vn = v.vn and o.vstdate between '{a}' and '{b}'
        left join ipt i on i.vn = o.vn
        left join ward w on w.ward = i.ward
        left join er_regist e on e.vn = o.vn
        left join opdscreen s on s.vn = o.vn
        order by o.vstdate desc, o.vsttime desc
    """)
    for r in rows:
        codes = sorted({c for c in (r.get("icd10_raw") or "").split(",") if c})
        r["icd10_list"] = ", ".join(fmt_icd10(c) for c in codes)
        r["case_key"] = r.get("an") or r["vn"]
        if not r.get("an"):
            r["ward_name"] = "ER" if r.get("er_vn") else "OPD (ไม่ผ่าน ER)"
        r["disposition"] = disposition(r)
    return rows


# รหัสมาตรฐานของ ovstost / er_dch_type ใน HOSxP — ถ้า รพ. ตั้งรหัสต่างไป แก้ใน .env
# (ดูรายการของ รพ. ด้วย `python setup_check.py`)
OST = {k: set(_codes(f"SEPSIS_OVSTOST_{k.upper()}", v)) for k, v in
       (("refer", "54"), ("death", "52,53"), ("refuse", "96"), ("escape", "97"), ("home", "99"))}
ER_DCH = {k: set(_codes(f"SEPSIS_ER_DCH_{k.upper()}", v)) for k, v in
          (("refer", "2"), ("death", "4,6"), ("refuse", "7"), ("escape", "8"), ("home", "1"))}
_DISPO = {
    "admit":   ("🛏️", "Admit"),
    "refer":   ("🚑", "Refer"),
    "death":   ("🕯️", "เสียชีวิต"),
    "refuse":  ("✋", "ปฏิเสธการรักษา"),
    "escape":  ("🚪", "หนีกลับ"),
    "home":    ("🏠", "กลับบ้าน"),
    "pending": ("⏳", "ยังไม่จำหน่าย"),
}


def disposition(case):
    """สถานะจำหน่ายของ visit: admit / refer (รพ.ปลายทาง + เวลา) / เสียชีวิต / กลับบ้าน ฯลฯ"""
    ost = str(case.get("ovstost") or "")
    erd = str(case.get("er_dch_type") or "")
    refer_dt = _to_dt_single(case.get("refer_dt"))
    if case.get("an"):
        code = "admit"
    elif refer_dt:
        code = "refer"
    else:
        code = next((k for k in ("refer", "death", "refuse", "escape", "home")
                     if ost in OST[k] or erd in ER_DCH[k]), "pending")
    icon, label = _DISPO[code]
    detail, at = None, None
    if code == "admit":
        detail = case.get("ward_name")
    elif code == "refer":
        detail = case.get("refer_hosp")
        at = refer_dt.strftime("%Y-%m-%d %H:%M") if refer_dt else None
    return {"code": code, "icon": icon, "label": label, "detail": detail, "at": at}


def time_zero_info(case):
    """(datetime, ชื่อแหล่ง) ของเวลาเริ่มนับ bundle"""
    if TIME_ZERO_MODE == "admission" and case.get("regdate"):
        return _to_dt(case["regdate"], case.get("regtime")), "admission"
    for key, src in (("sepsis_recognition_datetime", "sepsis_recognition"),
                     ("triage_datetime", "triage"), ("enter_er_time", "enter_er")):
        dt = _to_dt_single(case.get(key))
        if dt:
            return dt, src
    return _to_dt(case["vstdate"], case.get("vsttime")), "visit"


def time_zero(case):
    return time_zero_info(case)[0]


def _window(tz):
    return (_sql_dt(tz - timedelta(minutes=WINDOW_BEFORE_MIN)),
            _sql_dt(tz + timedelta(hours=WINDOW_END_HOURS)))


# -------------------------------------------------------- 2. element แต่ละตัว

_antibiotic_icodes = None
_ROW_ICODE = re.compile(r'<ROW [^>]*\bicode="(\d+)"')


def _antibiotics():
    global _antibiotic_icodes
    if _antibiotic_icodes is None:
        _antibiotic_icodes = {r["icode"] for r in fetch("select icode from drugitems where antibiotic='Y'")}
    return _antibiotic_icodes


@lru_cache(maxsize=64)
def _er_logs(vn, start, end):
    """[(เวลา, {icode})] ของ opitemrece_log — ใช้ซ้ำหลายรายการในเคสเดียว (ล้างแคชทุกครั้งที่คำนวณเคสใหม่)"""
    if not _has("opitemrece_log"):
        return ()
    logs = fetch(f"""
        select l.event_date_time t, replace(replace(l.new_delta, char(13), ''), char(10), ' ') x
        from opitemrece_log l
        where l.vn = '{vn}' and l.event_date_time between '{start}' and '{end}'
        order by l.event_date_time
    """)
    return tuple((_to_dt_single(lg["t"]), frozenset(_ROW_ICODE.findall(lg.get("x") or ""))) for lg in logs)


def _er_log_first(vn, icodes, start, end):
    """เวลาบันทึกครั้งแรกที่รายการยาใน icodes ปรากฏใน opitemrece_log ของ visit นี้"""
    for t, codes in _er_logs(vn, start, end):
        if codes & icodes:
            return t
    return None


def _ward_order_first(an, icode_sql, start, end):
    """คำสั่งยาบนหอ (item_type='P') ครั้งแรก — ไม่ใช้แถวที่โอนจาก ER ซึ่งเวลาเป็นเวลาลงทะเบียน"""
    if not an:
        return None
    row = fetch_one(f"""
        select min(timestamp(o.rxdate, o.rxtime)) t from opitemrece o
        where o.an = '{an}' and o.item_type = 'P' and o.icode in ({icode_sql})
          and timestamp(o.rxdate, o.rxtime) between '{start}' and '{end}'
    """)
    return _to_dt_single(row.get("t"))


def _earliest(candidates):
    found = [(dt, src) for dt, src in candidates if dt]
    return min(found) if found else (None, None)


def _order_event(case, icodes, start, end, er_recorded=None, what="ยา"):
    """เวลาสั่งรายการใน icodes ครั้งแรก (ยา หรือรายการค่าบริการ เช่น Hemoculture)"""
    if not icodes:
        return None, None
    icode_sql = ",".join(f"'{c}'" for c in sorted(icodes))
    lo, hi = _to_dt_single(start), _to_dt_single(end)
    er_rec = er_recorded if er_recorded and lo <= er_recorded <= hi else None
    return _earliest([
        (er_rec, f"ER บันทึกเวลาให้{what}"),
        (_er_log_first(case["vn"], icodes, start, end), f"สั่ง{what}ที่ ER"),
        (_ward_order_first(case.get("an"), icode_sql, start, end), f"สั่ง{what}บนหอผู้ป่วย"),
    ])


def _lab_event(hn, item_codes, start, end):
    if not item_codes:
        return None, None
    codes = ",".join(f"'{c}'" for c in item_codes)
    row = fetch_one(f"""
        select min(timestamp(h.order_date, h.order_time)) ordered_at
        from lab_head h
        join lab_order o on o.lab_order_number = h.lab_order_number
        where h.hn = '{hn}' and o.lab_items_code in ({codes})
          and h.order_date is not null and h.order_time is not null
          and timestamp(h.order_date, h.order_time) between '{start}' and '{end}'
    """)
    dt = _to_dt_single(row.get("ordered_at"))
    return dt, ("สั่ง Lab" if dt else None)


def _lab_results(hn, item_codes, start, end):
    """[(เวลาสั่ง, ค่าผล float|None)] เรียงตามเวลา — ใช้กับ Lactate (ค่าแรก + การวัดซ้ำ)"""
    if not item_codes:
        return []
    codes = ",".join(f"'{c}'" for c in item_codes)
    rows = fetch(f"""
        select timestamp(h.order_date, h.order_time) t, o.lab_order_result r
        from lab_head h
        join lab_order o on o.lab_order_number = h.lab_order_number
        where h.hn = '{hn}' and o.lab_items_code in ({codes})
          and h.order_date is not null and h.order_time is not null
          and timestamp(h.order_date, h.order_time) between '{start}' and '{end}'
        order by t
    """)
    return [(_to_dt_single(r["t"]), _num_or_none(r.get("r"))) for r in rows]


def _num_or_none(v, lo=None, hi=None):
    """'3.4' / '<0.5' / 3.4 -> float; ว่าง/ไม่ใช่ตัวเลข/นอกช่วง -> None"""
    m = re.search(r"\d+(?:\.\d+)?", str(v)) if v not in (None, "") else None
    if not m:
        return None
    x = float(m.group())
    if (lo is not None and x < lo) or (hi is not None and x > hi):
        return None
    return x


def _oxygen_element(notes, tz, lo, hi):
    """Sepsis Six: ให้ O2 ให้ SpO2 > 94% — ถ้า SpO2 แรกปกติและไม่ได้ให้ O2 ถือว่าไม่จำเป็น (ผ่าน)"""
    base = {"label": "ออกซิเจน (SpO2 target)", "at": None, "elapsed_min": None, "source": None, "detail": None}
    o2, spo2 = notes.get("oxygen"), notes.get("first_spo2")
    if o2:
        base.update(compliant=True, source="พยาบาลบันทึกให้ O2 (ER)", detail=o2["snippet"],
                    note="บันทึกว่าให้ O2" + ("" if o2["dt"] else " (ไม่ระบุเวลา)"))
        if o2["dt"] and lo <= o2["dt"] <= hi:
            base.update(at=o2["dt"].strftime("%Y-%m-%d %H:%M"),
                        elapsed_min=round((o2["dt"] - tz).total_seconds() / 60))
        return base
    if spo2 is not None:
        if spo2 >= 94:
            base.update(compliant=True, note=f"SpO2 แรก {spo2}% — ไม่จำเป็นต้องให้ O2")
        else:
            base.update(compliant=False, note=f"SpO2 แรก {spo2}% แต่ไม่พบบันทึกให้ O2")
        return base
    base.update(compliant=None, note="ไม่พบบันทึก SpO2/O2 — ต้องให้พยาบาลยืนยันเอง")
    return base


def _urine_element(notes):
    base = {"label": "ติดตาม Urine Output", "at": None, "elapsed_min": None, "source": None, "detail": None}
    u = notes.get("urine_output")
    if u:
        base.update(compliant=True, source="พยาบาลบันทึก (ER)", detail=u["snippet"],
                    note="บันทึกการคาสายสวน/ติดตามปัสสาวะ")
    else:
        base.update(compliant=None, note="ไม่พบบันทึก — ต้องให้พยาบาลยืนยันเอง")
    return base


def _nurse_text(vn):
    """บันทึกซักประวัติ/การพยาบาลของ visit ER (พยาบาลเขียนลำดับการดูแลพร้อมเวลาไว้ใน hpi)"""
    row = fetch_one(f"""
        select replace(replace(concat_ws(' - ', o.hpi, o.note), char(13), ' '), char(10), ' ') t
        from opdscreen o where o.vn = '{vn}'
    """)
    return row.get("t") or ""


NURSE_SOURCE = "พยาบาลบันทึกเวลาให้ (ER)"


# ----------------------------------------------- 2b. ตัวชี้วัดตาม SSC 2026

# NEWS2 (Royal College of Physicians 2017) — (ค่าสูงสุดของช่วง, คะแนน) เรียงจากน้อยไปมาก
NEWS2_RULES = {
    "rr": [(8, 3), (11, 1), (20, 0), (24, 2), (999, 3)],
    "spo2": [(91, 3), (93, 2), (95, 1), (100, 0)],
    "sbp": [(90, 3), (100, 2), (110, 1), (219, 0), (999, 3)],
    "hr": [(40, 3), (50, 1), (90, 0), (110, 1), (130, 2), (999, 3)],
    "temp": [(35.0, 3), (36.0, 1), (38.0, 0), (39.0, 1), (99, 2)],
}
NEWS2_LABEL = {"rr": "RR", "spo2": "SpO₂", "o2": "O₂", "sbp": "SBP", "hr": "HR",
               "consc": "ความรู้สึกตัว", "temp": "T"}


def _news2_points(key, value):
    for upper, pts in NEWS2_RULES[key]:
        if value <= upper:
            return pts
    return NEWS2_RULES[key][-1][1]


def vitals(case, notes):
    """ชีพจรแรกรับจาก opdscreen (+ SpO2/BP ต่ำสุดจากบันทึกพยาบาลเมื่อช่องโครงสร้างว่าง)"""
    v = {
        "sbp": _num_or_none(case.get("bps"), 30, 300), "dbp": _num_or_none(case.get("bpd"), 10, 200),
        "hr": _num_or_none(case.get("pulse"), 20, 250), "rr": _num_or_none(case.get("rr"), 4, 80),
        "temp": _num_or_none(case.get("temperature"), 30, 45), "weight": _num_or_none(case.get("bw"), 1, 300),
        "spo2": _num_or_none(case.get("o2sat"), 40, 100), "spo2_src": "HOSxP",
        "min_sbp": notes.get("min_sbp"),
    }
    if v["spo2"] is None and notes.get("first_spo2"):
        v["spo2"], v["spo2_src"] = notes["first_spo2"], "บันทึกพยาบาล"
    if v["spo2"] is None:
        v["spo2_src"] = None
    v["map"] = round((v["sbp"] + 2 * v["dbp"]) / 3) if v["sbp"] and v["dbp"] else None
    return v


def news2(case, v, notes, tz):
    """NEWS2 แรกรับ — ใช้ค่าที่ ER กรอกใน HOSxP ถ้ามี ไม่งั้นคำนวณจากข้อมูลที่มี (บอกว่าขาดตัวไหน)"""
    consc_id = str(case.get("e_consc") or case.get("s_consc") or "")
    consc = None if not consc_id else ("A" if consc_id == "1" else "VPU")   # 1 = รู้สึกตัวดี (A)
    o2 = notes.get("oxygen")
    if o2 is None:
        on_o2 = False if notes.get("_has_text") else None
    elif o2["dt"] is None:
        on_o2 = None                       # ให้ O2 แต่ไม่รู้ว่าตั้งแต่แรกรับหรือไม่
    else:
        on_o2 = o2["dt"] <= tz + timedelta(minutes=15)

    parts, missing, score, any3 = [], [], 0, False
    for key in ("rr", "spo2", "o2", "sbp", "hr", "consc", "temp"):
        if key == "o2":
            value, pts = (None, None) if on_o2 is None else ("O₂" if on_o2 else "air", 2 if on_o2 else 0)
        elif key == "consc":
            value, pts = (None, None) if consc is None else (consc, 0 if consc == "A" else 3)
        else:
            value = v.get(key)
            pts = None if value is None else _news2_points(key, value)
        if pts is None:
            missing.append(NEWS2_LABEL[key])
            continue
        score += pts
        any3 = any3 or pts == 3
        parts.append({"key": key, "label": NEWS2_LABEL[key], "value": value, "points": pts})

    hosxp = _num_or_none(case.get("e_news2") if case.get("e_news2") not in (None, "") else case.get("s_news2"))
    source = "HOSxP" if hosxp is not None else "คำนวณ"
    if hosxp is not None:
        score, missing = int(hosxp), []
    if not parts and hosxp is None:
        return None
    if score >= 7:
        band, label = "high", "สูง"
    elif score >= 5:
        band, label = "medium", "ปานกลาง"
    elif any3:
        band, label = "low-medium", "ต่ำ–ปานกลาง (มีค่า 3)"
    else:
        band, label = "low", "ต่ำ"
    return {"score": score, "band": band, "label": label, "parts": parts, "missing": missing,
            "n": 7 - len(missing), "source": source, "partial": bool(missing)}


def _vasopressor(case, notes, start, end, lo, hi):
    """ยากระตุ้นความดันตัวแรก: บันทึกพยาบาล + คำสั่ง NE/dopamine (เร็วที่สุด)"""
    cands = []
    n = notes.get("vasopressor")
    if n and (n["dt"] is None or lo <= n["dt"] <= hi):
        cands.append((n["dt"], n.get("agent"), NURSE_SOURCE, n["snippet"]))
    for agent, icodes in (("Norepinephrine", NOREPINEPHRINE_ICODES), ("Dopamine", DOPAMINE_ICODES)):
        dt, src = _order_event(case, set(icodes), start, end, what="ยา")
        if dt:
            cands.append((dt, agent, src, None))
    if not cands:
        return None
    timed = sorted((c for c in cands if c[0]), key=lambda c: c[0])
    return timed[0] if timed else cands[0]


def ssc_status(case, tz, notes, start, end, lo, hi, elements, dispo):
    """ตัวชี้วัดเพิ่มตาม SSC 2026 — ไม่กระทบเกณฑ์ "ครบ 4 ข้อ" เดิม"""
    v = vitals(case, notes)
    score = news2(case, v, notes, tz)
    elapsed = lambda dt: round((dt - tz).total_seconds() / 60) if dt else None  # noqa: E731

    # Lactate ค่าแรก + วัดซ้ำ (Rec 8, 51)
    labs = _lab_results(case["hn"], LACTATE_LAB_ITEMS, start, end)
    lac = {"value": None, "level": None, "repeat_needed": False, "repeat_ok": None,
           "repeat_at": None, "repeat_value": None, "repeat_elapsed": None}
    if labs:
        first_dt, first_val = labs[0]
        lac["value"] = first_val
        if first_val is not None:
            lac["level"] = "very_high" if first_val >= 4 else ("high" if first_val > 2 else "normal")
        if first_val is not None and first_val > 2:
            lac["repeat_needed"] = True
            limit = tz + timedelta(hours=LACTATE_REPEAT_HOURS)
            # ครั้งถัดไปที่ห่างจากครั้งแรก >= 30 นาที (กันสั่งซ้ำซ้อน) — แสดงแม้ช้าเกินกำหนด
            rep = next(((dt, val) for dt, val in labs[1:]
                        if dt and dt >= first_dt + timedelta(minutes=30)), None)
            lac["repeat_ok"] = rep is not None and rep[0] <= limit
            if rep:
                lac.update(repeat_at=rep[0].strftime("%Y-%m-%d %H:%M"), repeat_value=rep[1],
                           repeat_elapsed=elapsed(rep[0]))

    # ยากระตุ้นความดัน (Rec 53: NE เป็นตัวแรก)
    vaso = _vasopressor(case, notes, start, end, lo, hi)
    vp = None
    if vaso:
        dt, agent, src, detail = vaso
        vp = {"agent": agent or "ไม่ระบุ", "at": dt.strftime("%Y-%m-%d %H:%M") if dt else None,
              "elapsed_min": elapsed(dt), "source": src, "detail": detail,
              "ne_first": True if agent == "Norepinephrine" else (False if agent == "Dopamine" else None)}

    # shock / hypoperfusion — ใช้กำหนดเป้ายา 1 ชม. และเป้าสารน้ำ 30 mL/kg
    reasons = []
    if "R572" in (case.get("icd10_raw") or ""):
        reasons.append("R57.2")
    if v["sbp"] is not None and v["sbp"] < 90:
        reasons.append(f"SBP แรก {v['sbp']:.0f}")
    elif v["map"] is not None and v["map"] < 65:
        reasons.append(f"MAP {v['map']}")
    if v["min_sbp"] is not None and v["min_sbp"] < 90 and not (v["sbp"] is not None and v["sbp"] < 90):
        reasons.append(f"SBP ต่ำสุดใน ER {v['min_sbp']}")
    if lac["value"] is not None and lac["value"] >= 4:
        reasons.append(f"Lactate {lac['value']:g}")
    if vp:
        reasons.append(f"ได้ {vp['agent']}")
    shock = bool(reasons)

    # ยาปฏิชีวนะตามเป้า SSC
    atb = elements["antibiotic"]
    target = ATB_SHOCK_MIN if shock else ATB_NONSHOCK_MIN
    atb_ssc = {"target_min": target, "elapsed_min": atb["elapsed_min"],
               "ok": atb["elapsed_min"] is not None and atb["elapsed_min"] <= target}

    # สารน้ำ 30 mL/kg ใน 3 ชม. (ข้อมูลประกอบ — conditional recommendation, ต้องประเมินซ้ำเสมอ)
    given = notes.get("fluid_bolus_ml")
    target_ml = round(FLUID_ML_PER_KG * v["weight"]) if v["weight"] else None
    types = notes.get("fluid_types") or set()
    fluid = {"applies": shock, "weight": v["weight"], "target_ml": target_ml, "given_ml": given,
             "pct": round(given * 100 / target_ml) if given and target_ml else None,
             "met": (given or 0) >= target_ml if (shock and target_ml) else None,
             "types": sorted(types), "balanced": ("balanced" in types) if types else None}

    # refer / ICU ภายใน 6 ชม. (Rec 15) — เฉพาะเคส shock
    esc = {"applies": shock, "ok": None, "elapsed_min": None, "note": None}
    if shock:
        limit = ESCALATION_HOURS * 60
        icu_dt = _to_dt(case["regdate"], case.get("regtime")) \
            if case.get("an") and str(case.get("ward")) in ICU_WARDS and case.get("regdate") else None
        if dispo.get("code") == "refer" and dispo.get("elapsed_min") is not None:
            esc.update(ok=dispo["elapsed_min"] <= limit, elapsed_min=dispo["elapsed_min"],
                       note=f"Refer → {dispo.get('detail') or 'ไม่ระบุ รพ.'}")
        elif icu_dt:
            esc.update(ok=elapsed(icu_dt) <= limit, elapsed_min=elapsed(icu_dt), note=f"เข้า {case.get('ward_name')}")
        else:
            esc["note"] = {"admit": f"Admit {case.get('ward_name') or ''} (ไม่ได้ส่งต่อ/ไม่ใช่ ICU)".replace("  ", " "),
                           "death": "เสียชีวิต", "home": "กลับบ้าน", "pending": "ยังไม่จำหน่าย",
                           "refer": "Refer (ไม่ทราบเวลา)"}.get(dispo.get("code"), dispo.get("label"))

    return {"vitals": v, "news2": score, "shock": shock, "shock_reasons": reasons,
            "lactate": lac, "atb": atb_ssc, "fluid": fluid, "vasopressor": vp, "escalation": esc}


def triage_info(case, tz):
    """ข้อมูลสำหรับการ์ด "เคสใหม่": ชีพจร, NEWS2, สัญญาณ shock เบื้องต้น, เป้าสารน้ำตามน้ำหนัก"""
    text = _nurse_text(case["vn"])
    notes = nurse_notes.parse(text, tz)
    notes["_has_text"] = bool(text.strip())
    v = vitals(case, notes)
    shock = ("R572" in (case.get("icd10_raw") or "") or (v["sbp"] is not None and v["sbp"] < 90)
             or (v["map"] is not None and v["map"] < 65) or (v["min_sbp"] is not None and v["min_sbp"] < 90))
    return {"vitals": v, "news2": news2(case, v, notes, tz), "shock": shock,
            "fluid_target_ml": round(FLUID_ML_PER_KG * v["weight"]) if v["weight"] else None}


# ------------------------------------------------------ 3. ประกอบผล 1 เคส

def bundle_status(case):
    tz, tz_src = time_zero_info(case)
    start, end = _window(tz)
    lo, hi = _to_dt_single(start), _to_dt_single(end)
    _er_logs.cache_clear()   # ข้อมูลเคสที่ยังดูแลอยู่เปลี่ยนได้ตลอด — แคชใช้แค่ภายในการคำนวณครั้งนี้
    text = _nurse_text(case["vn"])
    notes = nurse_notes.parse(text, tz, fluid_hours=FLUID_HOURS)
    notes["_has_text"] = bool(text.strip())

    def documented(key):
        """เวลาให้จริงจากบันทึกพยาบาล (ถ้ามีและอยู่ในช่วงค้นหา) — ใช้ก่อนเวลาสั่ง"""
        n = notes.get(key)
        if n and n["dt"] and lo <= n["dt"] <= hi:
            return n["dt"], NURSE_SOURCE, n["snippet"]
        return None

    def prefer_documented(key, ordered):
        doc = documented(key)
        return doc if doc else (ordered[0], ordered[1], None)

    lactate = _lab_event(case["hn"], LACTATE_LAB_ITEMS, start, end) + (None,)
    culture = _earliest([
        _lab_event(case["hn"], HEMOCULTURE_LAB_ITEMS, start, end),
        _order_event(case, set(HEMOCULTURE_ICODES), start, end, what="เพาะเชื้อ"),
    ])
    doc_culture = documented("culture")
    culture = doc_culture if doc_culture and (culture[0] is None or doc_culture[0] < culture[0]) \
        else (culture[0], culture[1], None)
    atb = prefer_documented("antibiotic", _order_event(
        case, _antibiotics(), start, end, _to_dt_single(case.get("er_antibiotics_datetime"))))
    fluid = prefer_documented("iv_fluid", _order_event(case, set(IV_FLUID_ICODES), start, end))

    def elem(label, event):
        event_dt, source, detail = event
        if event_dt is None:
            return {"label": label, "at": None, "elapsed_min": None, "compliant": False,
                    "source": None, "detail": None, "note": "ไม่พบคำสั่งหรือบันทึกใน HOSxP ภายในช่วงเวลาที่ค้นหา"}
        elapsed = round((event_dt - tz).total_seconds() / 60)
        return {"label": label, "at": event_dt.strftime("%Y-%m-%d %H:%M"), "elapsed_min": elapsed,
                "compliant": elapsed <= BUNDLE_WINDOW_MIN, "source": source, "detail": detail, "note": None}

    elements = {
        "lactate": elem("วัดระดับ Lactate", lactate),
        "culture": elem("เพาะเชื้อก่อนให้ยา (Hemoculture)", culture),
        "antibiotic": elem("ยาปฏิชีวนะ (Antibiotic)", atb),
        "iv_fluid": elem("สารน้ำ IV Fluid", fluid),
        "oxygen": _oxygen_element(notes, tz, lo, hi),
        "urine_output": _urine_element(notes),
    }

    culture_before_antibiotic = None
    if culture[0] and atb[0]:
        # เทียบระดับนาที — บันทึกพยาบาลไม่มีวินาที (สั่ง 11:00:43 กับให้ยา 11:00 = นาทีเดียวกัน)
        culture_before_antibiotic = culture[0].replace(second=0) <= atb[0].replace(second=0)

    auto = [elements[k] for k in ("lactate", "culture", "antibiotic", "iv_fluid")]
    dispo = dict(case.get("disposition") or disposition(case))
    if dispo.get("at"):
        dispo["elapsed_min"] = round((_to_dt_single(dispo["at"] + ":00") - tz).total_seconds() / 60)
    ssc = ssc_status(case, tz, notes, start, end, lo, hi, elements, dispo)
    v = ssc["vitals"]
    # ความครบถ้วนของข้อมูลต้นทาง — ใช้ติดตามคุณภาพการบันทึก (SSC 2026 Rec 1: performance improvement)
    data = {
        "weight": v["weight"] is not None,
        "vitals": all(v[k] is not None for k in ("sbp", "hr", "rr", "temp")),
        "spo2_hosxp": v["spo2_src"] == "HOSxP",
        "spo2_any": v["spo2"] is not None,
        "consciousness": bool(case.get("e_consc") or case.get("s_consc")),
        "news2_hosxp": bool(ssc["news2"] and ssc["news2"]["source"] == "HOSxP"),
        "recognition_time": bool(_to_dt_single(case.get("sepsis_recognition_datetime"))),
        "triage_time": bool(_to_dt_single(case.get("triage_datetime"))),
        "nurse_atb_time": elements["antibiotic"]["source"] == NURSE_SOURCE,
        "nurse_fluid_time": elements["iv_fluid"]["source"] == NURSE_SOURCE,
        "lactate_value": ssc["lactate"]["value"] is not None,
    }
    return {
        "disposition": dispo,
        "case_key": case["case_key"], "an": case.get("an"), "vn": case["vn"], "hn": case["hn"],
        "ward": case.get("ward"), "ward_name": case.get("ward_name"), "bed": case.get("cur_bedno"),
        "icd10": case.get("icd10_list"),
        "time_zero": tz.strftime("%Y-%m-%d %H:%M"),
        "time_zero_mode": TIME_ZERO_LABEL[tz_src],
        "bundle_window_min": BUNDLE_WINDOW_MIN,
        "elements": elements,
        "culture_before_antibiotic": culture_before_antibiotic,
        "auto_compliant": all(e["compliant"] for e in auto),
        "ssc": ssc,
        "data": data,
    }


def build(lookback_days=7, d1=None, d2=None):
    return [bundle_status(c) for c in find_sepsis_cases(lookback_days, d1, d2)]


AUTO_ELEMENTS = [("lactate", "Lactate"), ("culture", "Hemoculture"),
                 ("antibiotic", "ยาปฏิชีวนะ"), ("iv_fluid", "IV Fluid")]


def summary(results):
    """ตัวเลขสรุปบนแดชบอร์ด — คำนวณจาก results ล้วน ไม่มีค่าตั้งเอง
    ตัวหารของแต่ละข้อ = จำนวนเคสทั้งหมด (ไม่พบคำสั่ง = ไม่ได้ทำภายในเวลา)
    """
    n = len(results)
    pct = lambda part: round(part * 100 / n) if n else 0
    elements = []
    for key, label in AUTO_ELEMENTS:
        ok = sum(1 for r in results if r["elements"][key]["compliant"])
        found = sum(1 for r in results if r["elements"][key]["elapsed_min"] is not None)
        elements.append({"key": key, "label": label, "ok": ok, "found": found, "pct": pct(ok)})
    passed = sum(1 for r in results if r["auto_compliant"])
    dispo = {}
    for r in results:
        code = r["disposition"]["code"]
        dispo[code] = dispo.get(code, 0) + 1
    return {
        "dispositions": [(_DISPO[c][0], _DISPO[c][1], dispo[c]) for c in _DISPO if dispo.get(c)],
        "cases": n,
        "admitted": sum(1 for r in results if r["an"]),
        "er_only": sum(1 for r in results if not r["an"]),
        "passed": passed, "passed_pct": pct(passed),
        "culture_after_atb": sum(1 for r in results if r["culture_before_antibiotic"] is False),
        "elements": elements,
        "by_key": {e["key"]: e for e in elements},
        "ssc": ssc_summary(results),
        "monthly": monthly(results),
        "completeness": completeness(results),
    }


def _rate(ok, n):
    return {"ok": ok, "n": n, "pct": round(ok * 100 / n) if n else None}


def ssc_summary(results):
    """ตัวชี้วัด SSC 2026 รวมทั้งช่วง — ตัวหารเฉพาะเคสที่ข้อนั้นใช้ได้ (เช่น vasopressor เฉพาะเคส shock)"""
    sc = [r["ssc"] for r in results]
    shock = [x for x in sc if x["shock"]]
    nonshock = [x for x in sc if not x["shock"]]
    high_lac = [x for x in sc if x["lactate"]["repeat_needed"]]
    fluid_app = [x for x in shock if x["fluid"]["target_ml"]]
    typed = [x for x in sc if x["fluid"]["balanced"] is not None]
    vaso = [x for x in shock if x["vasopressor"]]
    n2 = [x["news2"] for x in sc if x["news2"]]
    return {
        "shock": len(shock), "nonshock": len(nonshock),
        "atb_all": _rate(sum(x["atb"]["ok"] for x in sc), len(sc)),
        "atb_shock": _rate(sum(x["atb"]["ok"] for x in shock), len(shock)),
        "atb_nonshock": _rate(sum(x["atb"]["ok"] for x in nonshock), len(nonshock)),
        "lactate_repeat": _rate(sum(bool(x["lactate"]["repeat_ok"]) for x in high_lac), len(high_lac)),
        "lactate_very_high": sum(x["lactate"]["level"] == "very_high" for x in sc),
        "fluid_30": _rate(sum(bool(x["fluid"]["met"]) for x in fluid_app), len(fluid_app)),
        "balanced": _rate(sum(bool(x["fluid"]["balanced"]) for x in typed), len(typed)),
        "vaso_given": _rate(len(vaso), len(shock)),
        "ne_first": _rate(sum(x["vasopressor"]["ne_first"] is True for x in vaso), len(vaso)),
        "dopamine_first": sum(x["vasopressor"]["ne_first"] is False for x in vaso),
        "escalation": _rate(sum(x["escalation"]["ok"] is True for x in shock), len(shock)),
        "news2_high": sum(x["band"] == "high" for x in n2),
        "news2_medium": sum(x["band"] == "medium" for x in n2),
        "news2_n": len(n2),
    }


TH_MONTH_SHORT = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
                  "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


def monthly(results):
    """แนวโน้มรายเดือน (ตามเดือนของ time-zero) เรียงเก่า -> ใหม่"""
    groups = {}
    for r in results:
        groups.setdefault(r["time_zero"][:7], []).append(r)
    rows = []
    for ym in sorted(groups):
        rs = groups[ym]
        y, m = int(ym[:4]), int(ym[5:7])
        both = [r for r in rs if r["culture_before_antibiotic"] is not None]
        rows.append({
            "ym": ym, "label": f"{TH_MONTH_SHORT[m - 1]} {(y + 543) % 100:02d}", "n": len(rs),
            "shock": sum(r["ssc"]["shock"] for r in rs),
            "passed": _rate(sum(r["auto_compliant"] for r in rs), len(rs)),
            "atb60": _rate(sum(bool(r["elements"]["antibiotic"]["compliant"]) for r in rs), len(rs)),
            "atb_ssc": _rate(sum(r["ssc"]["atb"]["ok"] for r in rs), len(rs)),
            "culture_first": _rate(sum(r["culture_before_antibiotic"] is True for r in both), len(both)),
            "lactate60": _rate(sum(bool(r["elements"]["lactate"]["compliant"]) for r in rs), len(rs)),
        })
    return rows


COMPLETENESS = [
    ("weight", "น้ำหนัก (คำนวณ 30 mL/kg)"),
    ("vitals", "ชีพจรแรกรับครบ (BP/HR/RR/T)"),
    ("spo2_any", "SpO₂ (ช่อง HOSxP หรือบันทึกพยาบาล)"),
    ("spo2_hosxp", "SpO₂ ในช่องของ HOSxP"),
    ("consciousness", "ระดับความรู้สึกตัว (AVPU)"),
    ("news2_hosxp", "คะแนน NEWS2 ที่ ER กรอกใน HOSxP"),
    ("triage_time", "เวลา triage"),
    ("recognition_time", "เวลารับรู้ sepsis"),
    ("nurse_atb_time", "พยาบาลบันทึกเวลาให้ยาปฏิชีวนะ"),
    ("nurse_fluid_time", "พยาบาลบันทึกเวลาให้สารน้ำ"),
    ("lactate_value", "มีค่าผล Lactate"),
]


def completeness(results):
    n = len(results)
    return [{"key": k, "label": label, **_rate(sum(r["data"][k] for r in results), n)}
            for k, label in COMPLETENESS]
