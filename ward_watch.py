"""เฝ้าระวังผู้ป่วยในหอผู้ป่วย — สัญญาณชีพที่พยาบาลบันทึกใน KIPS เข้าเกณฑ์ SOS > 3 ร่วมกับไข้/ตัวเย็น

เกณฑ์เดียวกับหน้าต่างเตือน ER (hosxp_alert): SOS จาก T, SBP, ชีพจร, RR (สูตรเดิมของ รพ.วังน้ำเขียว)
และ T ≥ 38 °C หรือ < 36 °C · ผู้ป่วยอายุ ≥ 15 ปี ที่ยังไม่จำหน่าย

KIPS เก็บปรอทวัดไข้ของหอผู้ป่วยใน ipd_nurse_note (ไม่ใช่ kts_vital_sign ซึ่งกรอกอุณหภูมิแค่ ~20%)
ข้อมูล 30 วัน (ต.ค. 2026): เข้าเกณฑ์ ~2.8 ครั้ง/วัน (นับผู้ป่วยละไม่เกินวันละครั้ง)

ไม่เตือนผู้ป่วยที่ทีมดูแล sepsis อยู่แล้วในการนอนครั้งนี้ (นับตั้งแต่มา ER) — มีอย่างใดอย่างหนึ่ง:
วินิจฉัย sepsis, สั่งยาปฏิชีวนะ, สั่ง/เจาะ Hemoculture, หรือพยาบาลบันทึก load/bolus สารน้ำ
(ไม่นับสั่งสารน้ำถุงใหญ่ เพราะผู้ป่วยในส่วนใหญ่ได้เป็น maintenance) · ปิดด้วย SEPSIS_WARD_SKIP_MANAGED=0
"""

import os
from datetime import datetime, timedelta

import nurse_notes
import queries
from sos_score import _num, fever, sos_parts  # noqa: F401  สูตร SOS/ไข้ ใช้ร่วมกับ queries.time_zero_info

ENABLED = os.getenv("SEPSIS_WARD_ALERT", "1").strip() != "0"
SOS_GT = int(os.getenv("SEPSIS_WARD_SOS_GT", "3"))
# บันทึกที่เก่ากว่านี้ไม่เตือน — กันการ์ดย้อนหลังท่วมกลุ่มตอนเริ่มใช้/หลังระบบล่ม
MAX_AGE = timedelta(minutes=int(os.getenv("SEPSIS_WARD_ALERT_MAX_AGE_MINUTES", "180")))
MIN_AGE_Y = 15
SKIP_MANAGED = os.getenv("SEPSIS_WARD_SKIP_MANAGED", "1").strip() != "0"


def _kips_texts(ans):
    """{an: [(เวลาบันทึก, ข้อความ)]} บันทึกการพยาบาล KIPS — ตาราง focus_list ไม่มี index ที่ an
    (สแกนทั้งตาราง ~0.5 วินาที) จึงดึงหลาย AN ในคำสั่งเดียว"""
    out = {an: [] for an in ans}
    if not ans or not queries._has("kips_ipd_nurse_focus_detail"):
        return out
    for r in queries.fetch("""
        select f.an, d.record_date, d.record_time, concat_ws(' ', d.assessment, d.intervention, d.evaluation) txt
        from kips_ipd_nurse_focus_list f
        join kips_ipd_nurse_focus_detail d on d.kips_ipd_nurse_focus_list_id = f.kips_ipd_nurse_focus_list_id
        where f.an in %(ans)s
    """, {"ans": sorted(ans)}):
        out.setdefault(r["an"], []).append(
            (queries._kips_written(r.get("record_date"), r.get("record_time")), r.get("txt") or ""))
    return out


def sepsis_care(an, until, kips=None):
    """สิ่งที่บอกว่าทีมดูแล sepsis/ติดเชื้ออยู่แล้ว ตั้งแต่มา ER ของการนอนครั้งนี้จนถึง until — [] = ยังไม่มี

    kips: ผลของ _kips_texts() ที่ดึงไว้แล้ว (ไม่ส่ง = ดึงเฉพาะ AN นี้)
    """
    adm = queries.fetch_one("""
        select i.vn, i.hn, o.vstdate, o.vsttime, i.regdate, i.regtime
        from ipt i left join ovst o on o.vn = i.vn
        where i.an = %(an)s
    """, {"an": an})
    if not adm:
        return []
    start = (queries._to_dt(adm.get("vstdate"), adm.get("vsttime"))
             or queries._to_dt(adm.get("regdate"), adm.get("regtime")))
    if start is None:
        return []
    p = {**queries.ICD10_PARAMS, "an": an, "vn": adm.get("vn"), "hn": adm.get("hn"),
         "start": queries._sql_dt(start), "until": queries._sql_dt(until)}
    found = []

    dx = queries.fetch_one(f"""
        select (select group_concat(distinct od.icd10) from ovstdiag od
                 where od.vn = %(vn)s and {queries._icd10_cond('od')}) er_dx,
               (select group_concat(distinct d.icd10) from iptdiag d
                 where d.an = %(an)s and {queries._icd10_cond('d')}) ipd_dx
    """, p)
    codes = sorted({c for c in f"{dx.get('er_dx') or ''},{dx.get('ipd_dx') or ''}".split(",") if c})
    if codes:
        found.append("วินิจฉัย " + ", ".join(queries.fmt_icd10(c) for c in codes))

    # รายการที่สั่งใน visit ER และระหว่างนอน (ยา ATB จาก drugitems.antibiotic, Hemoculture ที่สั่งเป็นค่าบริการ)
    hc_icodes = sorted(queries.HEMOCULTURE_ICODES) or ["-"]
    items = queries.fetch_one("""
        select max(d.antibiotic = 'Y') atb, max(o.icode in %(hc)s) hc
        from opitemrece o left join drugitems d on d.icode = o.icode
        where (o.vn = %(vn)s or o.an = %(an)s)
          and (d.antibiotic = 'Y' or o.icode in %(hc)s)
          and timestamp(o.rxdate, o.rxtime) between %(start)s and %(until)s
    """, {**p, "hc": hc_icodes})
    if items.get("atb"):
        found.append("สั่งยาปฏิชีวนะ")
    hc = bool(items.get("hc"))
    if not hc and queries.HEMOCULTURE_LAB_ITEMS:
        hc = queries._lab_event(adm.get("hn"), queries.HEMOCULTURE_LAB_ITEMS, p["start"], p["until"])[0] is not None
    if hc:
        found.append("สั่ง Hemoculture")

    # load/bolus — บันทึกพยาบาล ER (opdscreen) และบันทึกการพยาบาล KIPS ของหอผู้ป่วย
    texts = [queries._nurse_text(adm.get("vn"))] if adm.get("vn") else []
    kips = kips if kips is not None else _kips_texts([an])
    texts += [txt for written, txt in kips.get(an, []) if written is None or written <= until]
    if any(nurse_notes.LOAD_VOL.search(t) or nurse_notes.TOTAL_VOL.search(t) for t in texts):
        found.append("load สารน้ำ")
    return found


DCH_TYPE = {"01": "กลับบ้าน", "02": "ไม่สมัครใจอยู่", "03": "หนี", "04": "ส่งต่อ",
            "05": "ส่งต่อ", "08": "เสียชีวิต", "09": "เสียชีวิต"}


def suspected_cases(d1, d2, sent_ans=(), now=None):
    """ผู้ป่วยในที่สัญญาณชีพเข้าเกณฑ์การ์ดหอผู้ป่วยในช่วง d1-d2 แต่ยังไม่มี ICD10 sepsis (หน้า "เคสสงสัย")

    คืน (cases, diagnosed) — cases เรียงจากเข้าเกณฑ์ครั้งแรกล่าสุด, diagnosed = จำนวน AN ที่ลงวินิจฉัย sepsis แล้ว
    แต่ละเคส: an, hn, ward_name, bed, first_at, first {vitals, sos, sos_parts}, max_sos, hits (ครั้ง), days (วัน),
    care [ยาปฏิชีวนะ/H/C/load ถึงตอนนี้], sent (ส่งการ์ดแล้ว), dch_at, dch_label, pdx, pdx_name
    """
    now = now or datetime.now()
    rows = queries.fetch("""
        select n.an, i.hn, n.note_date, n.note_time, n.temperature,
               coalesce(nullif(n.pulse, 0), n.heart_rate) pulse,
               n.respiratory_rate rr, n.bp_systolic bps, n.bp_diastolic bpd,
               w.name ward_name, coalesce(nullif(i.cur_bedno, ''), m.bedno) bedno,
               i.dchdate, i.dchtime, i.dchtype
        from ipd_nurse_note n
        join ipt i on i.an = n.an
        left join ward w on w.ward = i.ward
        left join iptadm m on m.an = n.an
        join an_stat a on a.an = n.an
        where n.note_date between %(d1)s and %(d2)s and a.age_y >= %(age)s
        order by n.note_date, n.note_time
    """, {"d1": d1.isoformat(), "d2": d2.isoformat(), "age": MIN_AGE_Y})
    by_an = {}
    for r in rows:
        parts = sos_parts(r.get("temperature"), r.get("bps"), r.get("pulse"), r.get("rr"))
        score = sum(parts.values())
        if score <= SOS_GT or not fever(r.get("temperature")):
            continue
        at = queries._to_dt(r.get("note_date"), r.get("note_time"))
        if at is None:
            continue
        c = by_an.get(r["an"])
        if c is None:
            dch = queries._to_dt(r.get("dchdate"), r.get("dchtime"))
            c = by_an[r["an"]] = {
                "an": r["an"], "hn": r.get("hn"), "ward_name": r.get("ward_name"), "bed": r.get("bedno"),
                "first_at": at, "max_sos": 0, "hits": 0, "dates": set(),
                "first": {"sos": score, "sos_parts": parts,
                          "vitals": {"temp": _num(r.get("temperature")), "sbp": _num(r.get("bps")),
                                     "dbp": _num(r.get("bpd")), "hr": _num(r.get("pulse")),
                                     "rr": _num(r.get("rr"))}},
                "dch_at": dch,
                "dch_label": (DCH_TYPE.get(r.get("dchtype") or "", "จำหน่ายแล้ว") if dch else None),
            }
        c["max_sos"] = max(c["max_sos"], score)
        c["hits"] += 1
        c["dates"].add(at.date())
    if not by_an:
        return [], 0

    pdx = {r["an"]: r for r in queries.fetch("""
        select d.an, d.icd10, c.name from iptdiag d left join icd101 c on c.code = d.icd10
        where d.an in %(ans)s and d.diagtype = '1'
    """, {"ans": sorted(by_an)})}
    sent = {str(k).split("|")[0] for k in sent_ans}
    kips = _kips_texts(list(by_an))
    cases, diagnosed = [], 0
    for an, c in by_an.items():
        care = sepsis_care(an, now, kips)
        if any(x.startswith("วินิจฉัย") for x in care):
            diagnosed += 1
            continue
        p = pdx.get(an) or {}
        c.update(care=care, sent=an in sent, days=len(c.pop("dates")),
                 pdx=queries.fmt_icd10(p.get("icd10")) if p.get("icd10") else None, pdx_name=p.get("name"))
        cases.append(c)
    cases.sort(key=lambda x: x["first_at"], reverse=True)
    return cases, diagnosed


def find_ward_alerts(now=None):
    """บันทึกสัญญาณชีพหอผู้ป่วยที่เข้าเกณฑ์ ภายใน MAX_AGE — เรียงตามเวลาวัด

    คืน list ของ dict: an, ward, ward_name, bed, at (datetime), key (AN|วันที่ ใช้กันส่งซ้ำ),
    vitals {temp, sbp, dbp, hr, rr, spo2, on_o2}, sos, sos_parts
    """
    if not ENABLED or not queries._has("ipd_nurse_note"):
        return []
    now = now or datetime.now()
    lo = now - MAX_AGE
    rows = queries.fetch("""
        select n.an, n.note_date, n.note_time, n.temperature,
               coalesce(nullif(n.pulse, 0), n.heart_rate) pulse,
               n.respiratory_rate rr, n.bp_systolic bps, n.bp_diastolic bpd,
               coalesce(nullif(n.spo2_o2, 0), nullif(n.spo2_ra, 0)) spo2, n.spo2_o2,
               i.ward, w.name ward_name, coalesce(nullif(i.cur_bedno, ''), m.bedno) bedno
        from ipd_nurse_note n
        join ipt i on i.an = n.an
        left join ward w on w.ward = i.ward
        left join iptadm m on m.an = n.an
        join an_stat a on a.an = n.an
        where n.note_date between %(d1)s and %(d2)s
          and i.dchdate is null and a.age_y >= %(age)s
        order by n.note_date, n.note_time
    """, {"d1": lo.date().isoformat(), "d2": now.date().isoformat(), "age": MIN_AGE_Y})
    found = []
    care = {}
    for r in rows:
        at = queries._to_dt(r.get("note_date"), r.get("note_time"))
        # เวลาในอนาคตเกิน 10 นาที = พิมพ์เวลาผิด ไม่เตือน
        if at is None or not lo <= at <= now + timedelta(minutes=10):
            continue
        parts = sos_parts(r.get("temperature"), r.get("bps"), r.get("pulse"), r.get("rr"))
        score = sum(parts.values())
        if score <= SOS_GT or not fever(r.get("temperature")):
            continue
        if SKIP_MANAGED:
            if r["an"] not in care:
                care[r["an"]] = sepsis_care(r["an"], now)
            if care[r["an"]]:
                continue
        found.append({
            "an": r["an"], "ward": r.get("ward"), "ward_name": r.get("ward_name"),
            "bed": r.get("bedno"), "at": at, "key": f"{r['an']}|{at:%Y-%m-%d}",
            "vitals": {"temp": _num(r.get("temperature")), "sbp": _num(r.get("bps")),
                       "dbp": _num(r.get("bpd")), "hr": _num(r.get("pulse")),
                       "rr": _num(r.get("rr")), "spo2": _num(r.get("spo2")),
                       "on_o2": bool(_num(r.get("spo2_o2")))},
            "sos": score, "sos_parts": parts,
        })
    return found
