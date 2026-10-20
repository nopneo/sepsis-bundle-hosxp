"""ตรวจความพร้อมก่อนใช้งาน + ค้นหารหัส lab/ยาของโรงพยาบาล — รันหลังกรอกค่า DB ใน .env

    python setup_check.py           ตรวจระบบ + แสดงรหัสที่แนะนำ
    python setup_check.py --write   เขียนรหัสที่แนะนำลง .env (เฉพาะค่าที่ยังว่างอยู่)

อ่านฐานข้อมูลอย่างเดียว ไม่แก้ข้อมูลใน HOSxP
รหัสที่แนะนำมาจากการค้นชื่อรายการ + จำนวนครั้งที่ใช้จริงใน 1 ปี — ต้องให้ทีมตรวจทานก่อนใช้เสมอ
"""

import argparse
import os
import sys
from datetime import date, datetime, timedelta

import config
import db
import queries

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SINCE = (date.today() - timedelta(days=365)).isoformat()
ENV_PATH = os.path.join(config.BASE_DIR, ".env")
problems = []


def head(title):
    print(f"\n=== {title} " + "=" * max(0, 60 - len(title)))


def ok(msg):
    print(f"  [ OK ] {msg}")


def warn(msg):
    print(f"  [ !! ] {msg}")
    problems.append(msg)


def like_any(column, patterns):
    return "(" + " or ".join(f"{column} like '{p}'" for p in patterns) + ")"


# ------------------------------------------------------------------ ตรวจระบบ

def check_db():
    head("1. เชื่อมต่อฐานข้อมูล HOSxP")
    print(f"  โหมด {db.MODE} · {db.DB_USER or '-'}@{db.DB_HOST}:{db.DB_PORT}/{db.DB_NAME}")
    try:
        row = db.fetch_one("select version() v, now() t")
    except Exception as exc:
        print(f"  [FAIL] ต่อฐานข้อมูลไม่ได้: {exc}")
        print("         ตรวจ DB_HOST / DB_USER / DB_PASS ใน .env และสิทธิ์ GRANT SELECT ของ user")
        sys.exit(1)
    ok(f"ต่อได้ — MariaDB/MySQL {row['v']}")
    db_now = queries._to_dt_single(row["t"])
    if db_now and abs((datetime.now() - db_now).total_seconds()) > 300:
        warn(f"เวลาเครื่องนี้ ({datetime.now():%H:%M}) ต่างจากเวลาฐานข้อมูล ({db_now:%H:%M}) เกิน 5 นาที "
             "— ตั้ง time zone เป็น Asia/Bangkok ให้ตรงกัน")
    else:
        ok("เวลาเครื่องนี้ตรงกับเวลาฐานข้อมูล")


def check_schema():
    head("2. ตาราง/คอลัมน์ที่ระบบใช้")
    required = [("ovst", "ovstost"), ("ovstdiag", "icd10"), ("ipt", "an"), ("iptdiag", "icd10"),
                ("ward", "name"), ("er_regist", "enter_er_time"), ("er_regist", "er_dch_type"),
                ("lab_head", "order_time"), ("lab_order", "lab_items_code"), ("lab_items", "lab_items_name"),
                ("drugitems", "antibiotic"), ("nondrugitems", "icode"), ("opitemrece", "item_type"),
                ("opdscreen", "hpi"), ("opdscreen", "bps"), ("opdscreen", "bpd"), ("opdscreen", "pulse"),
                ("opdscreen", "rr"), ("opdscreen", "temperature"), ("opdscreen", "bw"),
                ("referout", "refer_hospcode"), ("hospcode", "name")]
    missing = [f"{t}.{c}" for t, c in required if not queries._has(t, c)]
    if missing:
        warn("ไม่พบ " + ", ".join(missing) + " — HOSxP รุ่นนี้ใช้กับระบบไม่ได้ ติดต่อผู้พัฒนา")
    else:
        ok(f"ครบ {len(required)} รายการ")
    optional = [("er_regist", "sepsis_recognition_datetime", "เวลารับรู้ sepsis (ใช้เป็น time-zero ถ้ากรอก)"),
                ("er_regist", "triage_datetime", "เวลา triage"),
                ("er_regist", "antibiotics_datetime", "เวลาให้ยาปฏิชีวนะที่ ER บันทึก"),
                ("opitemrece_log", None, "ประวัติการบันทึกยา (ใช้หาเวลาสั่งยาที่ ER)"),
                ("opdscreen", "o2sat", "SpO₂ แรกรับ (NEWS2)"),
                ("er_regist", "news2_score", "คะแนน NEWS2 ที่ ER กรอก"),
                ("er_regist", "consciousness_level_type_id", "ระดับความรู้สึกตัว AVPU (NEWS2)")]
    for table, column, desc in optional:
        name = f"{table}.{column}" if column else table
        if queries._has(table, column):
            ok(f"มี {name} — {desc}")
        else:
            print(f"  [ -- ] ไม่มี {name} — ข้ามส่วน '{desc}' (ระบบยังทำงานได้)")


def check_cases():
    head("3. เคส sepsis ใน 1 ปีที่ผ่านมา (A40.x, A41.x, R57.2)")
    row = db.fetch_one(f"""
        select count(distinct vn) n from (
            select od.vn from ovstdiag od where od.vstdate >= '{SINCE}' and {queries._icd10_cond('od')}
            union
            select ii.vn from iptdiag d join ipt ii on ii.an = d.an
             where ii.regdate >= '{SINCE}' and {queries._icd10_cond('d')}) v""")
    n = db.num(row.get("n"))
    if n:
        ok(f"พบ {n} visit")
    else:
        warn("ไม่พบเคสเลย — ตรวจว่า รพ. ลงรหัส ICD10 sepsis หรือใช้รหัสแบบมีจุด")


def check_antibiotics():
    head("4. ยาปฏิชีวนะ (drugitems.antibiotic = 'Y')")
    n = db.num(db.fetch_one("select count(*) n from drugitems where antibiotic = 'Y'").get("n"))
    if n:
        ok(f"ตั้งค่าไว้ {n} รายการ")
    else:
        warn("ไม่มียาที่ติ๊ก antibiotic = 'Y' — ให้ห้องยาตั้งค่าใน HOSxP ก่อน ไม่งั้นระบบหายาปฏิชีวนะไม่เจอ")


# ------------------------------------------------------------ ค้นหารหัสรายการ

def _show(rows, current):
    picked = []
    for r in rows:
        code, n = str(r["code"]), db.num(r.get("n"))
        mark = "*" if code in current else " "
        print(f"   {mark} {code:>10}  ใช้ {n:>5} ครั้ง  {r['name']}")
        if n:
            picked.append(code)
    if not rows:
        print("     (ไม่พบรายการที่ชื่อเข้าเค้า — ต้องหารหัสเองใน HOSxP)")
    return picked


def _lab_candidates(patterns, exclude=()):
    cond = like_any("l.lab_items_name", patterns)
    for p in exclude:
        cond += f" and l.lab_items_name not like '{p}'"
    items = db.fetch(f"select l.lab_items_code code, l.lab_items_name name from lab_items l where {cond}")
    if not items:
        return []
    codes = ",".join(f"'{r['code']}'" for r in items)
    used = {str(r["code"]): r["n"] for r in db.fetch(f"""
        select o.lab_items_code code, count(*) n from lab_order o
        join lab_head h on h.lab_order_number = o.lab_order_number
        where o.lab_items_code in ({codes}) and h.order_date >= '{SINCE}'
        group by o.lab_items_code""")}
    for r in items:
        r["n"] = used.get(str(r["code"]), 0)
    return sorted(items, key=lambda r: -db.num(r["n"]))


def _item_candidates(table, cond, name_sql="name"):
    items = db.fetch(f"select icode code, {name_sql} name from {table} where {cond}")
    if not items:
        return []
    codes = ",".join(f"'{r['code']}'" for r in items)
    used = {str(r["code"]): r["n"] for r in db.fetch(f"""
        select icode code, count(*) n from opitemrece
        where icode in ({codes}) and rxdate >= '{SINCE}' group by icode""")}
    for r in items:
        r["n"] = used.get(str(r["code"]), 0)
    return sorted(items, key=lambda r: -db.num(r["n"]))


CULTURE_PATTERNS = ["%hemo%cul%", "%haemo%cul%", "%blood%cul%", "%blood%c/s%", "%H/C%"]


def find_codes():
    head("5. รหัสรายการของ รพ. (* = ตั้งไว้ใน .env แล้ว)")
    suggest = {}

    print("\n  Lactate (lab_items) → SEPSIS_LACTATE_LAB_ITEMS")
    rows = _lab_candidates(["%lactat%"], exclude=["%dehydrogenase%", "%LDH%", "%ringer%"])
    suggest["SEPSIS_LACTATE_LAB_ITEMS"] = _show(rows, queries.LACTATE_LAB_ITEMS)

    print("\n  Hemoculture แบบสั่งผ่าน lab (lab_items) → SEPSIS_HEMOCULTURE_LAB_ITEMS")
    rows = _lab_candidates(CULTURE_PATTERNS)
    suggest["SEPSIS_HEMOCULTURE_LAB_ITEMS"] = _show(rows, queries.HEMOCULTURE_LAB_ITEMS)

    print("\n  Hemoculture แบบรายการค่าบริการ (nondrugitems) → SEPSIS_HEMOCULTURE_ICODES")
    rows = _item_candidates("nondrugitems", like_any("name", CULTURE_PATTERNS))
    suggest["SEPSIS_HEMOCULTURE_ICODES"] = _show(rows, queries.HEMOCULTURE_ICODES)

    print("\n  IV fluid crystalloid 1000 ml (drugitems) → SEPSIS_IV_FLUID_ICODES")
    full = "concat_ws(' ', name, strength, units)"
    cond = (like_any("name", ["%NSS%", "%0.9\\%%", "%NaCl%", "%normal saline%", "%RLS%", "%LRS%",
                              "%ringer%", "%acetar%"])
            + " and " + like_any(full, ["%1000%", "%1,000%", "%1 L%", "%1L%"])
            + "".join(f" and name not like '{x}'" for x in
                      ("%D5%", "%dextrose%", "%irrigat%", "%dextran%", "%glycine%", "%water%")))
    rows = _item_candidates("drugitems", cond, name_sql=full)
    suggest["SEPSIS_IV_FLUID_ICODES"] = _show(rows, queries.IV_FLUID_ICODES)
    print("\n  หมายเหตุ: นับเฉพาะถุงใหญ่ 1000 ml — ไม่รวม NSS 100 ml ที่ใช้ผสมยา")

    print("\n  Norepinephrine (drugitems) → SEPSIS_NOREPINEPHRINE_ICODES")
    rows = _item_candidates("drugitems", like_any("name", ["%norepi%", "%nor-epi%", "%levophed%", "%noradren%"]),
                            name_sql=full)
    suggest["SEPSIS_NOREPINEPHRINE_ICODES"] = _show(rows, queries.NOREPINEPHRINE_ICODES)

    print("\n  Dopamine (drugitems) → SEPSIS_DOPAMINE_ICODES")
    rows = _item_candidates("drugitems", like_any("name", ["%dopamine%"]), name_sql=full)
    suggest["SEPSIS_DOPAMINE_ICODES"] = _show(rows, queries.DOPAMINE_ICODES)
    return {k: v for k, v in suggest.items() if v}


def show_dispositions():
    head("6. รหัสสถานะจำหน่าย (ตรวจว่าตรงกับ รพ.)")
    status_of = {}
    for status, codes in queries.OST.items():
        for c in codes:
            status_of[("ost", c)] = status
    for status, codes in queries.ER_DCH.items():
        for c in codes:
            status_of[("er", c)] = status
    th = {k: v[1] for k, v in queries._DISPO.items()}
    print("  ovstost (ผลการตรวจ OPD)")
    for r in db.fetch("select ovstost code, name from ovstost order by ovstost"):
        s = status_of.get(("ost", str(r["code"])))
        print(f"   {str(r['code']):>4}  {r['name']}" + (f"   → {th[s]}" if s else ""))
    print("  er_dch_type (การจำหน่ายจาก ER)")
    for r in db.fetch("select er_dch_type code, name from er_dch_type order by er_dch_type"):
        s = status_of.get(("er", str(r["code"])))
        print(f"   {str(r['code']):>4}  {r['name']}" + (f"   → {th[s]}" if s else ""))
    print("  ถ้ารหัสของ รพ. ต่างไป ตั้งใน .env เช่น SEPSIS_OVSTOST_REFER=54 หรือ SEPSIS_ER_DCH_DEATH=4,6")


def check_notify():
    head("7. ช่องทางแจ้งเตือน")
    import moph_notify
    import requests

    settings = moph_notify.load_settings()
    if moph_notify.is_configured(settings):
        ok("ตั้งค่า MOPH Notify แล้ว (data/moph_settings.json)")
    else:
        print("  [ -- ] ยังไม่ได้ตั้งค่า MOPH Notify — กรอกที่หน้า /settings ของแอปหลังติดตั้ง")
    try:
        requests.get(settings.get("base_url") or moph_notify.DEFAULT_BASE_URL, timeout=10)
        ok("เครื่องนี้ออกอินเทอร์เน็ตไป MOPH Notify ได้")
    except Exception as exc:
        warn(f"ออกเน็ตไป MOPH Notify ไม่ได้ ({type(exc).__name__}) — ต้องเปิดให้เครื่องนี้ออก https ได้")


def write_env(suggest):
    lines = open(ENV_PATH, encoding="utf-8").read().splitlines() if os.path.exists(ENV_PATH) else []
    current = {ln.split("=", 1)[0].strip(): i for i, ln in enumerate(lines)
               if "=" in ln and not ln.lstrip().startswith("#")}
    changed = []
    for key, codes in suggest.items():
        value = ",".join(codes)
        if key in current:
            if lines[current[key]].split("=", 1)[1].strip():
                continue  # มีค่าอยู่แล้ว ไม่ทับ
            lines[current[key]] = f"{key}={value}"
        else:
            lines.append(f"{key}={value}")
        changed.append(f"{key}={value}")
    if changed:
        with open(ENV_PATH, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(lines) + "\n")
        print("\n  เขียนลง .env แล้ว:\n    " + "\n    ".join(changed))
    else:
        print("\n  .env มีค่ารหัสครบแล้ว ไม่ได้เขียนทับ")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="เขียนรหัสที่แนะนำลง .env (เฉพาะค่าที่ยังว่าง)")
    args = parser.parse_args()

    print(f"ตรวจความพร้อม Sepsis Six Bundle — {config.HOSPITAL_NAME}")
    check_db()
    check_schema()
    check_cases()
    check_antibiotics()
    suggest = find_codes()
    have = lambda key, current: bool(current or suggest.get(key))  # noqa: E731
    if not have("SEPSIS_LACTATE_LAB_ITEMS", queries.LACTATE_LAB_ITEMS):
        warn("ยังไม่มีรหัส Lactate — หาใน HOSxP แล้วใส่ SEPSIS_LACTATE_LAB_ITEMS ใน .env")
    if not (have("SEPSIS_HEMOCULTURE_LAB_ITEMS", queries.HEMOCULTURE_LAB_ITEMS)
            or have("SEPSIS_HEMOCULTURE_ICODES", queries.HEMOCULTURE_ICODES)):
        warn("ยังไม่มีรหัส Hemoculture — ใส่ SEPSIS_HEMOCULTURE_LAB_ITEMS หรือ SEPSIS_HEMOCULTURE_ICODES ใน .env")
    if not have("SEPSIS_IV_FLUID_ICODES", queries.IV_FLUID_ICODES):
        warn("ยังไม่มีรหัสสารน้ำ — ใส่ SEPSIS_IV_FLUID_ICODES ใน .env")
    show_dispositions()
    check_notify()

    head("สรุป")
    if args.write:
        write_env(suggest)
    elif suggest:
        print("  รหัสที่แนะนำ (มีการใช้งานจริงใน 1 ปี) — ตรวจทานแล้วใส่ใน .env หรือรันซ้ำด้วย --write")
        for key, codes in suggest.items():
            print(f"    {key}={','.join(codes)}")
    for p in problems:
        print(f"  [ !! ] {p}")
    print("  พร้อมใช้งาน" if not problems else f"  มี {len(problems)} เรื่องที่ต้องแก้ก่อนใช้งานจริง")


if __name__ == "__main__":
    main()
