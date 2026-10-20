"""ชั้นเชื่อมต่อฐานข้อมูล HOSxP (อ่านอย่างเดียว)

รองรับ 2 โหมดเหมือนกับ ipd_dashboard
  - direct : ต่อ MariaDB ตรง ๆ ด้วย pymysql อ่านค่าจาก .env  (ใช้บนเซิร์ฟเวอร์ production)
  - ssh    : ยิงคำสั่งผ่าน `ssh <host> mariadb hos` แล้วอ่าน TSV  (ใช้ตอน dev บนเครื่องที่ไม่มีรหัส DB)

เลือกโหมดด้วย env `SEPSIS_DB_MODE` (default: direct ถ้ามี DB_PASS, ไม่งั้น ssh)
ข้อมูลใน HOSxP เก็บเป็น tis-620 จึงต้องระบุ charset ให้ถูกทั้งสองทาง
"""

import os
import shlex
import subprocess
from decimal import Decimal

import config  # noqa: F401  โหลด .env

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_NAME = os.getenv("DB_NAME", "hos")
DB_USER = os.getenv("DB_USER", "")
DB_PASS = os.getenv("DB_PASS", "")
SSH_HOST = os.getenv("SEPSIS_SSH_HOST", "")

MODE = os.getenv("SEPSIS_DB_MODE") or ("direct" if DB_PASS else "ssh")


def _fetch_direct(sql, params=None):
    import pymysql

    conn = pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        charset="tis620",
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with conn.cursor() as cur:
            # ส่ง None เมื่อไม่มี params — pymysql จะไม่ทำ % formatting จึงใช้ like 'A40%' ใน sql ได้
            cur.execute(sql, params or None)
            return cur.fetchall()
    finally:
        conn.close()


def _fetch_ssh(sql):
    """รันผ่าน ssh แล้วแปลง TSV กลับเป็น list[dict]

    mariadb client ส่ง \\t \\n \\\\ แบบ escape มาใน batch mode จึงต้อง unescape กลับ
    """
    remote = (
        "mariadb --default-character-set=tis620 --batch --raw "
        f"{shlex.quote(DB_NAME)} -e {shlex.quote(' '.join(sql.split()))}"
    )
    if not SSH_HOST:
        raise RuntimeError("ยังไม่ได้ตั้งค่า DB_PASS (โหมด direct) หรือ SEPSIS_SSH_HOST (โหมด ssh) ใน .env")
    proc = subprocess.run(["ssh", SSH_HOST, remote], capture_output=True)
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
    """params ใช้ได้เฉพาะโหมด direct (pymysql paramstyle %s)

    โหมด ssh ไม่รองรับ params — ต้อง escape ค่าที่แปรผันเอง (ตัวเลข/วันที่ formatted)
    ก่อนประกอบเป็น sql แล้วเรียก fetch(sql) เฉย ๆ
    """
    if MODE == "direct":
        rows = _fetch_direct(sql, params)
    else:
        if params:
            raise RuntimeError("โหมด ssh ไม่รองรับ params — ประกอบ sql ให้สมบูรณ์ก่อนเรียก")
        rows = _fetch_ssh(sql)
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
