"""งานประจำที่ดึงเคส sepsis ใหม่และยิงแจ้งเตือน — ตั้ง cron ให้รันทุก ~15 นาที

ช่องทางแจ้งเตือน: ใช้ MOPH Notify (หมอพร้อม) ถ้าตั้งค่าไว้ที่หน้า /settings แล้ว
ถ้ายังไม่ได้ตั้งค่า จะ fallback ไปใช้ LINE Messaging API (notify.py) แทนอัตโนมัติ

เก็บสถานะว่าแจ้งไปแล้วหรือยังใน SQLite ไฟล์เดียว (data/sepsis_bundle.sqlite)
กันไม่ให้ยิงซ้ำ ไม่ต้องมี DB แยกต่างหาก

ตัวอย่าง crontab (ทุก 15 นาที):
    */15 * * * * cd /opt/sepsis_bundle && .venv/bin/python scheduler.py >> data/scheduler_error.log 2>&1

บันทึกการทำงานอยู่ที่ data/scheduler.log (หมุนไฟล์เองเมื่อเกิน 1 MB เก็บ 5 ไฟล์)
รอบก่อนยังไม่จบ = รอบนี้ข้ามไปเลย (ล็อกไฟล์ data/scheduler.lock) — กันการ์ดเคสเดียวกันถูกส่งซ้ำ
"""

import logging
import logging.handlers
import os
import sqlite3
from datetime import datetime, timedelta

try:
    import fcntl                # Linux/macOS
except ImportError:             # Windows
    fcntl = None
    import msvcrt

import db
import flex
import moph_notify
import notify
import ward_watch
from queries import (BUNDLE_WINDOW_MIN, TIME_ZERO_LABEL, bundle_status, find_sepsis_cases,
                     time_zero_info, triage_info)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DATA_DIR, "sepsis_bundle.sqlite")
LOCK_PATH = os.path.join(DATA_DIR, "scheduler.lock")
LOG_PATH = os.path.join(DATA_DIR, "scheduler.log")

log = logging.getLogger("sepsis_bundle")
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
                log.warning("triage_info ล้มเหลว an=%s: %s", an, exc)
                triage = None
            try:
                _dispatch(flex.new_case(case, tz, BUNDLE_WINDOW_MIN, tz_label=TIME_ZERO_LABEL[tz_src],
                                        triage=triage),
                          notify.format_new_case_message(case))
            except Exception as exc:
                log.error("แจ้งเตือน new_case ล้มเหลว an=%s: %s", an, exc)
            else:
                _mark_notified(conn, an, "new_case")
                log.info("ส่งการ์ดเคสใหม่ %s", an)

        due_at = tz + timedelta(minutes=BUNDLE_WINDOW_MIN + GRACE_MIN)
        if SEND_BUNDLE_RESULT and now >= due_at and not _already_notified(conn, keys, "bundle_result"):
            result = bundle_status(case)
            try:
                _dispatch(flex.bundle_result(result), notify.format_bundle_message(result))
            except Exception as exc:
                log.error("แจ้งเตือน bundle_result ล้มเหลว an=%s: %s", an, exc)
            else:
                _mark_notified(conn, an, "bundle_result")
                log.info("ส่งการ์ดผล bundle %s", an)

    # หอผู้ป่วยแยก try — ฝั่งนี้ล้มเหลวต้องไม่กระทบการแจ้งเคส ER ด้านบน
    try:
        _run_ward(conn, now)
    except Exception:
        log.exception("เฝ้าระวังหอผู้ป่วยล้มเหลว")

    conn.close()
    log.info("ตรวจแล้ว %d เคส", len(cases))


def _run_ward(conn, now):
    """สัญญาณชีพหอผู้ป่วยเข้าเกณฑ์ SOS > 3 + ไข้ — ผู้ป่วยละไม่เกินวันละ 1 การ์ด"""
    alerts = ward_watch.find_ward_alerts(now)
    for alert in alerts:
        key = alert["key"]
        if _already_notified(conn, [key], "ward_sos"):
            continue
        try:
            _dispatch(flex.ward_alert(alert), notify.format_ward_alert_message(alert))
        except Exception as exc:
            log.error("แจ้งเตือน ward_sos ล้มเหลว %s: %s", key, exc)
        else:
            _mark_notified(conn, key, "ward_sos")
            log.info("ส่งการ์ดหอผู้ป่วย %s SOS %s", key, alert["sos"])


def _try_lock():
    """ล็อกไฟล์แบบไม่รอ — คืน file object (ต้องถือไว้จนจบ) หรือ None ถ้ารอบก่อนยังทำงานอยู่"""
    os.makedirs(DATA_DIR, exist_ok=True)
    f = open(LOCK_PATH, "a+")
    try:
        if fcntl:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        f.close()
        return None
    return f


def _setup_logging():
    os.makedirs(DATA_DIR, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=5,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def main():
    _setup_logging()
    lock = _try_lock()
    if lock is None:
        log.warning("รอบก่อนยังทำงานไม่เสร็จ — ข้ามรอบนี้")
        return
    try:
        run()
    except Exception:
        log.exception("scheduler ล้มเหลว")
        raise
    finally:
        db.close()
        lock.close()


if __name__ == "__main__":
    main()
