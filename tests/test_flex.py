"""การ์ด LINE (flex.py)"""

import json

import flex


def test_vitals_missing_shown_in_line():
    """ค่าที่ยังไม่กรอกขึ้น missing + บรรทัดเตือนให้วัด/บันทึก (23 ต.ค. 2026 ผู้ใช้ขอ)"""
    v = {"sbp": 86, "dbp": 50, "hr": 124, "rr": None, "temp": 38.9, "spo2": None, "weight": 60}
    assert flex._vitals_text(v) == "BP 86/50 · HR 124 · RR missing · T 38.9 · SpO₂ missing"
    card = json.dumps(flex._triage_card({"vitals": v, "news2": None}), ensure_ascii=False)
    assert "ยังไม่บันทึก RR, SpO₂" in card
    full = {"sbp": 120, "dbp": 80, "hr": 80, "rr": 18, "temp": 37, "spo2": 98}
    assert "missing" not in flex._vitals_text(full)
    assert "ยังไม่บันทึก" not in json.dumps(flex._triage_card({"vitals": full}), ensure_ascii=False)
