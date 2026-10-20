"""งานประจำที่ดึงเคส sepsis ใหม่และยิงแจ้งเตือน — ตั้ง cron ให้รันทุก ~15 นาที

ช่องทางแจ้งเตือน: ใช้ MOPH Notify (หมอพร้อม) ถ้าตั้งค่าไว้ที่หน้า /settings แล้ว
ถ้ายังไม่ได้ตั้งค่า จะ fallback ไปใช้ LINE Messaging API (notify.py) แทนอัตโนมัติ

เก็บสถานะว่าแจ้งไปแล้วหรือยังใน SQLite ไฟล์เดียว (data/sepsis_bundle.sqlite)
กันไม่ให้ยิงซ้ำ ไม่ต้องมี DB แยกต่างหาก

ตัวอย่าง crontab (ทุก 15 นาที):
    */15 * * * * cd /opt/sepsis_bundle && .venv/bin/python scheduler.py >> data/scheduler.log 2>&1
"""

import os
import sqlite3
from datetime import datetime, timedelta

import flex
import moph_notify
import notify
from queries import (BUNDLE_WINDOW_MIN, TIME_ZERO_LABEL, bundle_status, find_sepsis_cases,
                     time_zero_info, triage_info)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sepsis_bundle.sqlite")
GRACE_MIN = int(os.getenv("SEPSIS_NOTIFY_GRACE_MINUTES", "10"))
# เคสที่เก่ากว่านี้ไม่แจ้ง — กันการ์ดเคสย้อนหลังหลายวันท่วมกลุ่มตอนเริ่มรันครั้งแรก/หลังระบบล่ม
MAX_ALERT_AGE = timedelta(hours=int(os.getenv("SEPSIS_MAX_ALERT_AGE_HOURS", "6")))
# ปิดไว้ก่อน: เวลาใน opitemrece/lab_head ยังไม่สะท้อนเวลาทำจริง (คำสั่งจาก ER ติดเวลา visit,
# time-zero = เวลา admit ช้ากว่าเวลามาถึง ER ~1.5 ชม.) การ์ดผลจะบอก "ไม่ผ่าน" ผิด ๆ
# เปิดใช้ด้วย SEPSIS_SEND_BUNDLE_RESULT=1 ใน .env หลังตรวจสอบตรรกะเวลากับทีมแล้ว
SEND_BUNDLE_RESULT = os.getenv("SEPSIS_SEND_BUNDLE_RESULT", "0") == "1"


def _dispatch(flex_message, text):
    """MOPH Notify (Flex card) ถ้าตั้งค่าไว้แล้ว ไม่งั้น fallback เป็นข้อความล้วนทาง LINE"""
    moph_settings = moph_notify.load_settings()
    if moph_notify.is_configured(moph_settings):
        moph_notify.send_messages([flex_message], settings=moph_settings)
    else:
        notify.send_line(text)


def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""create table if not exists notify_log (
        an text, notify_type text, sent_at text,
        primary key (an, notify_type))""")
    return conn


def _already_notified(conn, keys, notify_type):
    marks = ",".join("?" * len(keys))
    return conn.execute(
        f"select 1 from notify_log where an in ({marks}) and notify_type=?", (*keys, notify_type)
    ).fetchone() is not None


def _mark_notified(conn, key, notify_type):
    conn.execute(
        "insert or replace into notify_log (an, notify_type, sent_at) values (?,?,?)",
        (key, notify_type, datetime.now().isoformat()),
    )
    conn.commit()


def run(lookback_days=3):
    conn = _conn()
    cases = find_sepsis_cases(lookback_days)
    now = datetime.now()
    for case in cases:
        # คีย์ถาวรคือ VN ของ ER — เคสที่ลง ICD ตั้งแต่ ER แล้วค่อย admit จะได้ไม่ถูกแจ้งซ้ำด้วย AN
        # (ยังเช็ค AN ด้วย เพราะประวัติรุ่นแรกใน notify_log บันทึกด้วย AN)
        an = case["vn"]
        keys = [case["vn"]] + ([case["an"]] if case.get("an") else [])
        tz, tz_src = time_zero_info(case)
        if now - tz > MAX_ALERT_AGE + timedelta(minutes=BUNDLE_WINDOW_MIN + GRACE_MIN):
            continue

        if not _already_notified(conn, keys, "new_case") and now - tz <= MAX_ALERT_AGE:
            try:
                triage = triage_info(case, tz)
            except Exception as exc:   # ข้อมูลประกอบ — ล้มเหลวก็ยังต้องส่งการ์ดเคสใหม่
                print(f"[sepsis_bundle] triage_info ล้มเหลว an={an}: {exc}")
                triage = None
            try:
                _dispatch(flex.new_case(case, tz, BUNDLE_WINDOW_MIN, tz_label=TIME_ZERO_LABEL[tz_src],
                                        triage=triage),
                          notify.format_new_case_message(case))
            except Exception as exc:
                print(f"[sepsis_bundle] แจ้งเตือน new_case ล้มเหลว an={an}: {exc}")
            else:
                _mark_notified(conn, an, "new_case")

        due_at = tz + timedelta(minutes=BUNDLE_WINDOW_MIN + GRACE_MIN)
        if SEND_BUNDLE_RESULT and now >= due_at and not _already_notified(conn, keys, "bundle_result"):
            result = bundle_status(case)
            try:
                _dispatch(flex.bundle_result(result), notify.format_bundle_message(result))
            except Exception as exc:
                print(f"[sepsis_bundle] แจ้งเตือน bundle_result ล้มเหลว an={an}: {exc}")
            else:
                _mark_notified(conn, an, "bundle_result")

    conn.close()


if __name__ == "__main__":
    run()
