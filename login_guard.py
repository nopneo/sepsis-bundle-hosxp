"""กันการเดารหัส (หน้า /unlock) — นับครั้งที่กรอกผิดใน SQLite ใต้ data/ ใช้ร่วมกันทุก worker

  - IP เดียวกรอกผิด PER_IP ครั้งใน WINDOW วินาที → IP นั้นถูกพักจนครบช่วง
  - ทั้งระบบกรอกผิดรวม GLOBAL ครั้งใน WINDOW วินาที → พักทุกคน (กันเดาจากหลายเครื่องพร้อมกัน)
  - กรอกถูก = ล้างประวัติผิดของ IP นั้น

เดิมใช้ time.sleep(1) ต่อครั้งที่ผิด ซึ่งยิงพร้อมกันหลายคำขอก็เดาได้เร็วเท่าเดิม และ sleep ยังกิน worker
(gunicorn มีแค่ 2 ตัว) ทำให้แดชบอร์ดค้างระหว่างถูกเดารหัส
"""

import os
import sqlite3
import time
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "login_attempts.sqlite")
WINDOW = int(os.getenv("SEPSIS_LOGIN_WINDOW_SECONDS", "900"))
PER_IP = int(os.getenv("SEPSIS_LOGIN_MAX_FAILS", "5"))
GLOBAL = int(os.getenv("SEPSIS_LOGIN_MAX_FAILS_TOTAL", "30"))


@contextmanager
def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5)
    try:
        conn.execute("create table if not exists fails (ip text, t real)")
        yield conn
        conn.commit()
    finally:
        conn.close()


def wait_seconds(ip, now=None):
    """ต้องรออีกกี่วินาทีจึงกรอกรหัสได้ (0 = กรอกได้)"""
    now = now or time.time()
    since = now - WINDOW
    with _conn() as conn:
        mine = conn.execute("select count(*), min(t) from fails where ip = ? and t > ?", (ip, since)).fetchone()
        total = conn.execute("select count(*), min(t) from fails where t > ?", (since,)).fetchone()
    waits = [first + WINDOW - now for n, first, limit in ((*mine, PER_IP), (*total, GLOBAL))
             if n >= limit and first is not None]
    return max(0, int(max(waits))) + 1 if waits else 0


def record_failure(ip, now=None):
    now = now or time.time()
    with _conn() as conn:
        conn.execute("insert into fails (ip, t) values (?, ?)", (ip, now))
        conn.execute("delete from fails where t <= ?", (now - WINDOW,))


def reset(ip):
    with _conn() as conn:
        conn.execute("delete from fails where ip = ?", (ip,))
