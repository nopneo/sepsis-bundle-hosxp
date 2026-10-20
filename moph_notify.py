"""ไคลเอนต์ MOPH Notify (หมอพร้อม) — ส่งข้อความเข้า LINE group ของหน่วยบริการ

อ้างอิงเอกสารในโฟลเดอร์ "คู่มือการใช้งานสำหรับ MOPH Notify":
  - API Document MOPH Notify.docx : POST {URL}/api/notify/send, header client-key/secret-key
  - User Manual MOPH Notify.pdf    : สร้าง Bot Group, invite หมอพร้อม, Admin อนุมัติ, ดู Client_ID/Secret

ขั้นตอนเตรียมก่อนใช้ (ทำครั้งเดียว):
  1. สร้าง LINE group ตั้งชื่อลงท้าย "HC" + รหัสหน่วยบริการ 5 หลัก (เช่น "Sepsis IPD HC10954")
     ต้องตั้งชื่อก่อนเชิญหมอพร้อม ไม่งั้น chatbot จะไม่รับรู้กลุ่ม
  2. เชิญ LINE "หมอพร้อม" เข้ากลุ่ม
  3. Admin หน่วยบริการอนุมัติกลุ่มใน CMS MOPH Notify
  4. ดู Client_ID / Secret ของกลุ่มที่อนุมัติแล้วในเมนูกลุ่มบอท แล้วมากรอกที่หน้า /settings
"""

import json
import logging
import os
import tempfile
from urllib.parse import urlsplit

import requests

log = logging.getLogger(__name__)

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "moph_settings.json")

DEFAULT_BASE_URL = "https://morpromt2f.moph.go.th"

DEFAULT_SETTINGS = {
    "base_url": DEFAULT_BASE_URL,
    "client_key": "",
    "secret_key": "",
}

# คำอธิบาย error ตามตาราง Response ในคู่มือ API
ERROR_HINTS = {
    "Unauthorized": "client-key หรือ secret-key ไม่ถูกต้อง",
    "messages error": "รูปแบบข้อความไม่ถูกต้องตามที่ LINE กำหนด",
    "Send messages error": "ระบบหมอพร้อมส่งข้อความไม่สำเร็จ",
    "require messages": "ไม่ได้ส่ง messages มาใน body",
}


def allowed_base_url(url):
    """ปลายทางต้องเป็น https ของ moph.go.th — กันการเปลี่ยน URL ไปเซิร์ฟเวอร์อื่นแล้วได้ key + ข้อมูลเคสไป"""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        return False
    return parts.scheme == "https" and (host == "moph.go.th" or host.endswith(".moph.go.th"))


def load_settings():
    """ไฟล์ไม่มี/เสีย = ค่าเริ่มต้น (บันทึก log) — ไม่ให้ scheduler และหน้าเว็บล่มเพราะไฟล์ตั้งค่า"""
    if not os.path.exists(SETTINGS_PATH):
        return dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("ไม่ใช่ JSON object")
    except (OSError, ValueError) as exc:
        log.error("อ่าน %s ไม่ได้ (%s) — ใช้ค่าเริ่มต้น ต้องตั้งค่า MOPH Notify ใหม่ที่หน้า /settings",
                  SETTINGS_PATH, exc)
        return dict(DEFAULT_SETTINGS)
    merged = dict(DEFAULT_SETTINGS)
    merged.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
    return merged


def save_settings(values):
    """บันทึกทับบางส่วน — ไม่ส่ง secret_key มา = คงค่าเดิม (ช่องว่างในฟอร์ม)"""
    current = load_settings()
    current.update(values)
    folder = os.path.dirname(SETTINGS_PATH)
    os.makedirs(folder, exist_ok=True)
    # เขียนลงไฟล์ชั่วคราวแล้วค่อยแทนที่ — ไฟล์จริงจึงไม่มีวันเหลือครึ่งเดียว (เครื่องดับ/ดิสก์เต็มกลางคัน)
    # mkstemp สร้างไฟล์สิทธิ์ 600 ตั้งแต่แรก ไม่มีช่วงที่ user อื่นอ่าน secret ได้
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".moph_settings.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, SETTINGS_PATH)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return current


def is_configured(settings=None):
    s = settings or load_settings()
    return bool(s.get("base_url") and s.get("client_key") and s.get("secret_key"))


def send_messages(messages, settings=None):
    """ส่ง LINE message objects (text/flex ฯลฯ) เข้ากลุ่มที่ผูกกับ client-key นี้"""
    s = settings or load_settings()
    if not is_configured(s):
        raise RuntimeError("ยังไม่ได้ตั้งค่า MOPH Notify (client-key / secret-key) ที่หน้า /settings")
    if not allowed_base_url(s["base_url"]):
        raise RuntimeError(f"URL ของ MOPH Notify ไม่ใช่ https://*.moph.go.th ({s['base_url']}) — แก้ที่หน้า /settings")

    resp = requests.post(
        f"{s['base_url'].rstrip('/')}/api/notify/send",
        headers={
            "Content-Type": "application/json",
            "client-key": s["client_key"],
            "secret-key": s["secret_key"],
        },
        json={"messages": messages},
        timeout=15,
    )
    try:
        data = resp.json()
    except ValueError:
        resp.raise_for_status()
        raise RuntimeError(f"MOPH Notify ตอบกลับไม่ใช่ JSON (HTTP {resp.status_code})")

    # API ตอบ HTTP 200 เสมอแม้ error จึงต้องเช็คโค้ดใน body; คู่มือใช้ message_code
    # แต่ของจริง (ทดสอบ ต.ค. 2026) กรณี key ผิดตอบเป็น {"status":401,...} จึงรับทั้งสองชื่อ
    code = data.get("message_code", data.get("status"))
    if code != 200:
        msg = data.get("message", "")
        raise RuntimeError(f"{msg} — {ERROR_HINTS.get(msg, 'ไม่ทราบสาเหตุ')} (code {code})")
    return data


def send_text(text, settings=None):
    return send_messages([{"type": "text", "text": text[:5000]}], settings=settings)
