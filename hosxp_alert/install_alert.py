"""ตัวติดตั้งหน้าต่างเตือน "สงสัย Sepsis" ในหน้าซักประวัติ HOSxP (SYS_VN_SCRIPT)

    python install_alert.py check    --host <ip> [--db hos] --user <user>
    python install_alert.py install  --host <ip> [--db hos] --user <user> --er-deps 011[,012]
    python install_alert.py rollback --host <ip> [--db hos] --user <user> --backup backups/<ไฟล์>.pas

ถามรหัสผ่านแบบซ่อน ไม่เก็บรหัสในไฟล์ · ทดสอบกับฐานทดสอบก่อนฐานจริงเสมอ

check    : script ปัจจุบัน, รายการแผนกที่รัน VN script, รหัสแผนก ER ที่แนะนำ (จาก opdscreen.screen_dep
           ของ visit ER), ประมาณจำนวนการเตือนต่อวันจากข้อมูล 90 วัน — อ่านอย่างเดียว
install  : สำรอง script เดิมลง backups/ อัตโนมัติ แล้วติดตั้ง (เขียนเฉพาะเมื่อ script ไม่เปลี่ยนระหว่างนั้น)
           ถ้า script เดิมของ รพ. ไม่ใช่ script SOS แบบที่ระบบนี้รู้จัก จะไม่ทับให้ — ต้องรวมเอง หรือใส่ --replace
rollback : คืน script จากไฟล์สำรอง (เขียนเฉพาะเมื่อ script ปัจจุบันเป็นของระบบนี้)

สิทธิ์ที่ต้องใช้: SELECT, UPDATE (และ INSERT ถ้ายังไม่มีแถว SYS_VN_SCRIPT) บน hos.sys_app_script
และ SELECT บน opdscreen, ovst, er_regist, vn_stat, kskdepartment, depcode_vn_script_allow (สำหรับ check)
"""

import argparse
import getpass
import hashlib
import os
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "_vendor"))
try:
    import pymysql
except ImportError:
    sys.exit("ต้องติดตั้ง pymysql ก่อน: pip install pymysql")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TEMPLATE = os.path.join(HERE, "SYS_VN_SCRIPT_sepsis_template.pas")
BACKUPS = os.path.join(HERE, "backups")
MARKER = "SepsisNoticeForm"            # มีใน script ของระบบนี้ทุกฉบับ
SOS_MARKER = "sos_score"               # script SOS เดิมแบบ รพ.วังน้ำเขียว
SCRIPT_NAME = "SYS_VN_SCRIPT"


def build_script(er_deps):
    codes = [c.strip() for c in er_deps.split(",") if c.strip()]
    if not codes or not all(re.fullmatch(r"[0-9A-Za-z]{1,6}", c) for c in codes):
        sys.exit("--er-deps ต้องเป็นรหัสแผนก คั่นด้วยจุลภาค เช่น 011 หรือ 011,012")
    text = open(TEMPLATE, encoding="utf-8").read()
    text = text.replace("{{ER_DEPS_SQL}}", ",".join(f'"{c}"' for c in codes))
    text = text.replace("{{ER_DEPS_TEXT}}", "/".join(codes))
    text.encode("tis-620")          # ฐาน HOSxP ส่วนใหญ่เก็บ tis620 — อักษรนอกชุดนี้บันทึกไม่ได้
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


def connect(args):
    # ALERT_DB_PASS ใช้กับงานอัตโนมัติได้ (ตั้งเฉพาะในหน้าต่างคำสั่งนั้น ห้ามเขียนลงไฟล์) ไม่งั้นถามแบบซ่อน
    pw = os.environ.get("ALERT_DB_PASS") or getpass.getpass(f"รหัสผ่าน {args.user}@{args.host} (พิมพ์แล้วไม่แสดง): ")
    try:
        c = pymysql.connect(host=args.host, port=args.port, user=args.user, password=pw, database=args.db,
                            charset="tis620", connect_timeout=10, autocommit=False)
    finally:
        pw = None
    return c


def q(cur, sql, params=None):
    cur.execute(sql, params)
    return cur.fetchall()


def current(cur):
    rows = q(cur, "select app_script_id, script_text, md5(script_text) from sys_app_script "
                  "where script_name=%s order by app_script_id", (SCRIPT_NAME,))
    if len(rows) > 1:
        sys.exit(f"!! มีแถว {SCRIPT_NAME} มากกว่า 1 แถว ({[r[0] for r in rows]}) — ตรวจด้วยตัวเองก่อน")
    return rows[0] if rows else None


def kind(text):
    if text is None:
        return "ไม่มี"
    if MARKER in text:
        return "ของระบบนี้ (เตือนสงสัย Sepsis)"
    if SOS_MARKER in text:
        return "script SOS เดิม (แบบที่ระบบนี้รู้จัก)"
    if not text.strip():
        return "ว่าง"
    return "script อื่นของ รพ. (ต้องรวมเอง)"


def cmd_check(args):
    c = connect(args)
    cur = c.cursor()
    print(f"\n=== ฐาน {args.db} @ {args.host} ===")
    print("MariaDB/MySQL", q(cur, "select version()")[0][0])
    row = current(cur)
    if row:
        print(f"\nSYS_VN_SCRIPT id {row[0]} · ยาว {len(row[1] or '')} ตัวอักษร · md5 {row[2]} · {kind(row[1])}")
        print("  10 บรรทัดแรก:")
        for ln in (row[1] or "").splitlines()[:10]:
            print("   |", ln[:110])
    else:
        print("\nยังไม่มีแถว SYS_VN_SCRIPT (ติดตั้งจะเพิ่มแถวใหม่ ต้องมีสิทธิ์ INSERT)")
    try:
        allow = [r[0] for r in q(cur, "select depcode from depcode_vn_script_allow order by depcode")]
        print("\nแผนกใน depcode_vn_script_allow:", ", ".join(a for a in allow if a) or "(ว่าง)")
    except pymysql.MySQLError as exc:
        print("\nอ่าน depcode_vn_script_allow ไม่ได้:", exc.args[-1])

    print("\nแผนกที่ซักประวัติ visit ER (30 วัน) — ใช้เลือก --er-deps:")
    for dep, name, n in q(cur, """
            select s.screen_dep, k.department, count(*) n from er_regist e
            join opdscreen s on s.vn = e.vn left join kskdepartment k on k.depcode = s.screen_dep
            where e.vstdate >= curdate() - interval 30 day and s.bps > 0
            group by s.screen_dep, k.department order by n desc limit 6"""):
        print(f"   {dep or '(ว่าง)':>6}  {n:>5} visit  {name or ''}")

    est = q(cur, """
        select count(*) from (
          select s.temperature t,
           if(s.temperature<=20,0,if(s.temperature between 36.1 and 38,0,if(s.temperature between 35.1 and 36,1,
             if(s.temperature between 38.1 and 38.4,1,if(s.temperature<=35 or s.temperature>=38.5,2,0)))))
          + if(s.bps<=50,0,if(s.bps between 101 and 180,0,if(s.bps between 91 and 100,1,if(s.bps between 181 and 199,1,
             if(s.bps between 81 and 90,2,if(s.bps>=200,2,if(s.bps<=80,3,0)))))))
          + if(s.pulse<=20,0,if(s.pulse between 51 and 100,0,if(s.pulse between 41 and 50,1,if(s.pulse between 100 and 120,1,
             if(s.pulse between 121 and 139,2,if(s.pulse<=40,3,if(s.pulse>=140,3,0)))))))
          + if(s.rr<=2,0,if(s.rr between 9 and 20,0,if(s.rr between 21 and 25,1,if(s.rr between 26 and 35,2,
             if(s.rr>=35 or s.rr<=8,3,0))))) sos
          from opdscreen s join vn_stat v on v.vn = s.vn
          where s.vstdate >= curdate() - interval 90 day and v.age_y >= 15
            and s.temperature > 0 and s.bps > 0 and s.pulse > 0 and s.rr > 0) x
        where sos > 3 and (t >= 38 or (t > 20 and t < 36))""")[0][0]
    print(f"\nประมาณการ: จะเตือนสงสัย Sepsis {est} ครั้งใน 90 วัน (~{est / 90:.1f} ครั้ง/วัน)")
    c.close()


def backup(row, args):
    os.makedirs(BACKUPS, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(BACKUPS, f"SYS_VN_SCRIPT_{args.db}_{args.host}_{stamp}.pas")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(row[1] if row else "")
    if not row:
        open(path + ".none", "w").close()       # บอก rollback ว่าเดิมไม่มีแถว
    return path


def confirm(args, action):
    if args.yes:
        return
    ans = input(f"\n{action} บนฐาน {args.db} @ {args.host} — พิมพ์ YES เพื่อยืนยัน: ")
    if ans.strip() != "YES":
        sys.exit("ยกเลิก")


def cmd_install(args):
    new = build_script(args.er_deps)
    c = connect(args)
    cur = c.cursor()
    row = current(cur)
    k = kind(row[1] if row else None)
    print(f"script ปัจจุบัน: {k}")
    if row and row[1].replace("\r\n", "\n") == new.replace("\r\n", "\n"):
        sys.exit("ฉบับนี้ติดตั้งอยู่แล้ว — ไม่ต้องทำอะไร")
    if k.startswith("script อื่น") and not args.replace:
        sys.exit("!! script เดิมของ รพ. ไม่ใช่แบบที่ระบบนี้รู้จัก — ติดตั้งจะลบการทำงานเดิมทั้งหมด\n"
                 "   ให้รวมเอง (ดูคู่มือ) หรือใส่ --replace ถ้าตั้งใจแทนที่ทั้งหมด")
    path = backup(row, args)
    print("สำรอง script เดิมไว้ที่", path)
    confirm(args, f"ติดตั้งหน้าต่างเตือน Sepsis (รหัส ER {args.er_deps})")
    if row:
        n = cur.execute("update sys_app_script set script_text=%s, update_date_time=now() "
                        "where app_script_id=%s and md5(script_text)=%s", (new, row[0], row[2]))
    else:
        new_id = q(cur, "select coalesce(max(app_script_id), 0) + 1 from sys_app_script")[0][0]
        n = cur.execute("insert into sys_app_script (app_script_id, script_name, script_text, update_date_time) "
                        "values (%s, %s, %s, now())", (new_id, SCRIPT_NAME, new))
    if n != 1:
        c.rollback()
        sys.exit(f"!! เขียนได้ {n} แถว (ต้องเป็น 1) — script อาจถูกแก้ระหว่างนี้ ยกเลิก")
    c.commit()
    after = current(cur)
    ok = after and after[1].replace("\r\n", "\n") == new.replace("\r\n", "\n")
    print("ติดตั้งแล้ว — อ่านกลับตรงกับต้นฉบับ" if ok else "!! อ่านกลับไม่ตรง — ให้ rollback ทันที")
    print(f"\nเปิด HOSxP ใหม่แล้วทดสอบ · ย้อนกลับ: python install_alert.py rollback --host {args.host} "
          f"--db {args.db} --user {args.user} --backup \"{path}\"")
    c.close()


def cmd_rollback(args):
    text = open(args.backup, encoding="utf-8", newline="").read()   # คง CRLF ตามที่ HOSxP เก็บ
    was_none = os.path.exists(args.backup + ".none")
    c = connect(args)
    cur = c.cursor()
    row = current(cur)
    if not row:
        sys.exit("ไม่มีแถว SYS_VN_SCRIPT — ไม่มีอะไรให้ย้อนกลับ")
    if MARKER not in (row[1] or "") and not args.force:
        sys.exit("!! script ปัจจุบันไม่ใช่ของระบบนี้ (อาจมีคนแก้ไปแล้ว) — ไม่ทับ ใส่ --force ถ้าตั้งใจ")
    confirm(args, "ย้อนกลับ SYS_VN_SCRIPT เป็นฉบับก่อนติดตั้ง" + (" (ลบแถวที่ระบบนี้เพิ่ม)" if was_none else ""))
    if was_none:
        n = cur.execute("delete from sys_app_script where app_script_id=%s and md5(script_text)=%s", (row[0], row[2]))
    else:
        n = cur.execute("update sys_app_script set script_text=%s, update_date_time=now() "
                        "where app_script_id=%s and md5(script_text)=%s", (text, row[0], row[2]))
    if n != 1:
        c.rollback()
        sys.exit(f"!! เขียนได้ {n} แถว — ยกเลิก")
    c.commit()
    print("ย้อนกลับแล้ว — เปิด HOSxP ใหม่เพื่อใช้ script เดิม")
    c.close()


def main():
    ap = argparse.ArgumentParser(description="ติดตั้งหน้าต่างเตือนสงสัย Sepsis ใน HOSxP (SYS_VN_SCRIPT)")
    ap.add_argument("command", choices=["check", "install", "rollback"])
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, default=3306)
    ap.add_argument("--db", default="hos")
    ap.add_argument("--user", required=True)
    ap.add_argument("--er-deps", help="รหัสแผนก ER (opdscreen.screen_dep) เช่น 011 หรือ 011,012")
    ap.add_argument("--backup", help="ไฟล์สำรองสำหรับ rollback")
    ap.add_argument("--replace", action="store_true", help="แทนที่ script อื่นของ รพ. ทั้งหมด")
    ap.add_argument("--force", action="store_true", help="rollback แม้ script ปัจจุบันไม่ใช่ของระบบนี้")
    ap.add_argument("--yes", action="store_true", help="ไม่ถามยืนยัน")
    args = ap.parse_args()
    if args.command == "install" and not args.er_deps:
        ap.error("install ต้องระบุ --er-deps (ดูคำแนะนำจากคำสั่ง check)")
    if args.command == "rollback" and not args.backup:
        ap.error("rollback ต้องระบุ --backup")
    {"check": cmd_check, "install": cmd_install, "rollback": cmd_rollback}[args.command](args)


if __name__ == "__main__":
    main()
