"""ตัวอ่านบันทึกพยาบาล ER — ตัวอย่างตามรูปแบบที่พบจริง (ไม่มีข้อมูลผู้ป่วย)"""

from datetime import datetime

import nurse_notes

TZ = datetime(2026, 10, 16, 11, 30)

NOTE = ("รายงานแพทย์ at 11.58 น. - lactate - H/C*2 - 0.9%NaCl 1000 ml iv drip 60 cc/hr at 12.15 น. "
        "- Cef -3 2 g iv drip at 12.40 น. - On O2 canular 3 LPM >> SpO2 97 % - BP 85/50 - Cr 6.14 "
        "- 0.9%NaCl iv load 1,000 ml at 11.50 น. - foley cath")


def test_full_note():
    r = nurse_notes.parse(NOTE, TZ)
    assert r["antibiotic"]["dt"] == datetime(2026, 10, 16, 12, 40)
    assert r["antibiotic"]["snippet"].startswith("Cef -3")          # " -3" ไม่ถูกตัดเป็นรายการใหม่
    assert r["iv_fluid"]["dt"] == datetime(2026, 10, 16, 11, 50)   # เลือกเวลาที่เร็วที่สุด
    assert r["culture"]["dt"] is None                               # H/C ไม่มีเวลากำกับ
    assert r["oxygen"]["snippet"].startswith("On O2")
    assert "urine_output" in r
    assert r["fluid_bolus_ml"] == 1000 and r["fluid_types"] == {"saline"}
    assert r["min_sbp"] == 85 and r["first_spo2"] == 97


def test_lab_values_are_not_times():
    r = nurse_notes.parse("Cr 6.14 INR 2.91 - ceftriaxone 2 g", TZ)
    assert r["antibiotic"]["dt"] is None


def test_time_written_before_drug():
    r = nurse_notes.parse("15.00 น ให้ยา Cef -3 2 gm v", datetime(2026, 10, 16, 14, 0))
    assert r["antibiotic"]["dt"] == datetime(2026, 10, 16, 15, 0)


def test_stat_after_midnight():
    r = nurse_notes.parse("Clindamycin 600 mg iv drip stat 02.50 น.", datetime(2026, 10, 16, 23, 50))
    assert r["antibiotic"]["dt"] == datetime(2026, 10, 17, 2, 50)


def test_levophed_is_vasopressor_not_antibiotic():
    r = nurse_notes.parse("Levophed 4 mg in 5%DW 250 ml iv drip at 12.00 น.", TZ)
    assert "antibiotic" not in r
    assert r["vasopressor"]["agent"] == "Norepinephrine"


def test_carrier_fluid_is_not_bolus():
    # NSS 100 ml ใช้ผสมยา ไม่ใช่สารน้ำ
    r = nurse_notes.parse("Cef -3 2 g + NSS 100 ml iv drip at 12.40 น.", TZ)
    assert "fluid_types" not in r and "fluid_bolus_ml" not in r


def test_empty_text():
    assert nurse_notes.parse("", TZ) == {}
    assert nurse_notes.parse(None, TZ) == {}


import pytest  # noqa: E402


@pytest.mark.parametrize("text,spo2", [
    ("O2 sat 96", 96),                      # แบบที่พบมากที่สุด ไม่มี %
    ("O2 sat : 96 %", 96),
    ("o2sat 97%", 97),
    ("O2 sat RA =97 %", 97),
    ("O2 sat RA 99 %", 99),
    ("o2satRA98%", 98),
    ("SpO2 95%", 95),
    ("O2 sat = 98%", 98),
    ("BP 120/80 P 88 O2sat room air 94", 94),
])
def test_spo2_patterns(text, spo2):
    assert nurse_notes.parse(text, TZ)["first_spo2"] == spo2


@pytest.mark.parametrize("text", ["O2 sat 9", "O2 sat 120", "On O2 cannula 3 LPM"])
def test_spo2_rejects_non_values(text):
    assert "first_spo2" not in nurse_notes.parse(text, TZ)
