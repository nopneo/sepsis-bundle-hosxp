"""แจ้งเตือนผ่าน LINE Messaging API (push message)

หมายเหตุสำคัญ: LINE Notify (บริการเดิมที่ยิง token ตรง ๆ) ปิดให้บริการแล้วตั้งแต่
ปลายเดือนมีนาคม 2025 — ต้องสร้าง LINE Official Account + Messaging API Channel เอง
แล้วเอา Channel access token (long-lived) มาใส่ .env และหา userId/groupId ปลายทาง

ห้ามใส่ชื่อ-นามสกุลผู้ป่วยในข้อความ ส่งแค่ HN/AN/ward/เตียง ตามหลักความเป็นส่วนตัว
(ข้อมูลผู้ป่วยต้องไม่ออกนอกระบบ รพ. โดยไม่จำเป็น)
"""

import os

import requests

import config  # noqa: F401  โหลด .env ก่อนอ่าน token

LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_TO = os.getenv("LINE_TO", "")  # userId หรือ groupId ปลายทาง (ขอจาก LINE Developers Console)

ICON = {True: "✅", False: "❌", None: "➖"}


def send_line(text):
    if not LINE_TOKEN or not LINE_TO:
        raise RuntimeError("ยังไม่ได้ตั้งค่า LINE_CHANNEL_ACCESS_TOKEN / LINE_TO ใน .env")
    resp = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"},
        json={"to": LINE_TO, "messages": [{"type": "text", "text": text[:4900]}]},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json() if resp.text else {}


def format_new_case_message(case):
    return (
        "🚨 พบผู้ป่วยสงสัย Sepsis รายใหม่\n"
        f"{'AN' if case.get('an') else 'VN'}: {case['case_key']}  HN: {case['hn']}\n"
        f"Ward: {case.get('ward_name') or '-'}  เตียง: {case.get('cur_bedno') or '-'}\n"
        f"ICD10: {case.get('icd10_list')}"
    )


def format_bundle_message(result):
    lines = [
        "🔔 ผล Sepsis Six Bundle",
        f"{'AN' if result.get('an') else 'VN'}: {result['case_key']}  HN: {result['hn']}",
        f"Ward: {result.get('ward_name') or '-'}  เตียง: {result.get('bed') or '-'}",
        f"ICD10: {result.get('icd10')}",
        f"Time-zero ({result['time_zero_mode']}): {result['time_zero']}",
        f"เป้าหมาย: ภายใน {result['bundle_window_min']} นาที",
        "",
    ]
    for el in result["elements"].values():
        mark = ICON[el["compliant"]]
        detail = f"{el['elapsed_min']} นาที" if el["elapsed_min"] is not None else (el.get("note") or "ไม่มีข้อมูล")
        lines.append(f"{mark} {el['label']}: {detail}")
    if result.get("culture_before_antibiotic") is False:
        lines.append("⚠️ ให้ยาปฏิชีวนะก่อนเก็บ Hemoculture (ลำดับไม่ถูกต้อง)")
    lines.append("")
    verdict = "ผ่านเกณฑ์" if result["auto_compliant"] else "ไม่ผ่านเกณฑ์ — ควรทบทวน"
    lines.append(f"สรุป (เฉพาะรายการที่ดึงอัตโนมัติได้): {verdict}")
    lines.append("* ออกซิเจน/Urine Output ต้องให้พยาบาลยืนยันเพิ่มเอง ระบบดึงอัตโนมัติไม่ได้")
    return "\n".join(lines)
