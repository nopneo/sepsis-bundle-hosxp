"""ชั้นเชื่อมต่อฐานข้อมูล HOSxP (อ่านอย่างเดียว)

รองรับ 2 โหมดเหมือนกับ ipd_dashboard
  - direct : ต่อ MariaDB ตรง ๆ ด้วย pymysql อ่านค่าจาก .env  (ใช้บนเซิร์ฟเวอร์ production)
  - ssh    : ยิงคำสั่งผ่าน `ssh <host> mariadb hos` แล้วอ่าน TSV  (ใช้ตอน dev บนเครื่องที่ไม่มีรหัส DB)

เลือกโหมดด้วย env `SEPSIS_DB_MODE` (default: direct ถ้ามี DB_PASS, ไม่งั้น ssh)
ข้อมูลใน HOSxP เก็บเป็น tis-620 จึงต้องระบุ charset ให้ถูกทั้งสองทาง

ค่าที่แปรผันส่งเป็นพารามิเตอร์เสมอ (ทั้งสองโหมด): fetch("... where vn = %(vn)s", {"vn": vn})
เมื่อส่งพารามิเตอร์ เครื่องหมาย % ที่เป็นตัวอักษรใน SQL ต้องเขียนเป็น %%
"""

import os
import shlex
import subprocess
import threading
from decimal import Decimal

import config  # noqa: F401  โหลด .env

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_NAME = os.getenv("DB_NAME", "hos")
DB_USER = os.getenv("DB_USER", "")
DB_PASS = os.getenv("DB_PASS", "")
SSH_HOST = os.getenv("SEPSIS_SSH_HOST", "")
#: เวลาสูงสุดต่อคำสั่ง SQL (วินาที) — กัน query ค้างจนกิน worker และภาระของ DB HOSxP
QUERY_TIMEOUT = int(os.getenv("SEPSIS_DB_TIMEOUT", "60"))

MODE = os.getenv("SEPSIS_DB_MODE") or ("direct" if DB_PASS else "ssh")


#: connection ต่อเธรด — ใช้ซ้ำทุกคำสั่งใน request/รอบ scheduler แล้วปิดด้วย close()
#: (เดิมเปิดใหม่ทุกคำสั่ง: ดูช่วงปีงบ ~150 เคส × ~12 คำสั่ง = เปิด-ปิดราว 2,000 ครั้งต่อหน้า)
_local = threading.local()


def _conn():
    import pymysql

    conn = getattr(_local, "conn", None)
    if conn is not None:
        return conn
    conn = pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        charset="tis620",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
        read_timeout=QUERY_TIMEOUT + 5,
    )
    # ให้ MariaDB หยุด query เองเมื่อเกินเวลา (read_timeout ตัดแค่ฝั่ง client, query ยังรันต่อใน DB)
    # MySQL ไม่มีตัวแปรนี้ — ข้ามได้ ยังมี read_timeout กันอยู่
    try:
        with conn.cursor() as cur:
            cur.execute(f"set session max_statement_time = {QUERY_TIMEOUT:d}")
    except pymysql.MySQLError:
        pass
    _local.conn = conn
    return conn


def close():
    """ปิด connection ของเธรดนี้ (app เรียกตอนจบทุก request, scheduler เรียกตอนจบรอบ)"""
    conn = getattr(_local, "conn", None)
    _local.conn = None
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


def _fetch_direct(sql, params=None):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            # ส่ง None เมื่อไม่มี params — pymysql จะไม่ทำ % formatting จึงใช้ like 'A40%' ใน sql ได้
            cur.execute(sql, params or None)
            return cur.fetchall()
    except Exception:
        close()                 # connection อาจเสีย/หมดเวลา — คำสั่งถัดไปเปิดใหม่
        raise


def _bind(sql, params):
    """แทนพารามิเตอร์ลง SQL สำหรับโหมด ssh — escape แบบเดียวกับ pymysql"""
    if not params:
        return sql
    from pymysql.converters import escape_item

    if isinstance(params, dict):
        return sql % {k: escape_item(v, "utf8") for k, v in params.items()}
    return sql % tuple(escape_item(v, "utf8") for v in params)


def _fetch_ssh(sql, params=None):
    """รันผ่าน ssh แล้วแปลง TSV กลับเป็น list[dict]

    mariadb client ส่ง \\t \\n \\\\ แบบ escape มาใน batch mode จึงต้อง unescape กลับ
    """
    sql = _bind(sql, params)
    remote = (
        "mariadb --default-character-set=tis620 --batch --raw "
        f"{shlex.quote(DB_NAME)} -e {shlex.quote(' '.join(sql.split()))}"
    )
    if not SSH_HOST:
        raise RuntimeError("ยังไม่ได้ตั้งค่า DB_PASS (โหมด direct) หรือ SEPSIS_SSH_HOST (โหมด ssh) ใน .env")
    # stdin=DEVNULL: ssh ของ Windows ที่ไม่มี console จะค้างรอ stdin
    # BatchMode: ห้ามถามรหัส/host key — ล้มทันทีดีกว่าค้างทั้ง request
    proc = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", SSH_HOST, remote],
                          capture_output=True, stdin=subprocess.DEVNULL, timeout=QUERY_TIMEOUT + 30)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace"))

    text = proc.stdout.decode("tis-620", "replace")
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if not lines:
        return []
    header = lines[0].split("\t")
    rows = []
    for ln in lines[1:]:
        cells = ln.split("\t")
        row = {}
        for k, v in zip(header, cells):
            row[k] = None if v == "NULL" else v
        rows.append(row)
    return rows


def fetch(sql, params=None):
    """params แบบ pymysql (%s หรือ %(ชื่อ)s) ใช้ได้ทั้งโหมด direct และ ssh"""
    rows = _fetch_direct(sql, params) if MODE == "direct" else _fetch_ssh(sql, params)
    return [{k: _clean(v) for k, v in r.items()} for r in rows]


def fetch_one(sql, params=None):
    rows = fetch(sql, params)
    return rows[0] if rows else {}


def _clean(v):
    if isinstance(v, Decimal):
        return float(v)
    return v


def num(v, default=0):
    """แปลงค่าที่ได้จาก DB (อาจเป็น str จากโหมด ssh) ให้เป็นตัวเลขเสมอ"""
    if v is None or v == "":
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return int(f) if f == int(f) else f
