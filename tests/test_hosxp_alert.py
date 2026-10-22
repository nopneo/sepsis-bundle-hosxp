"""script เตือนใน HOSxP (hosxp_alert/) — คะแนน MEWS และการประกอบ script จากต้นแบบ (ไม่ต่อฐานจริง)"""

import os
import re
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hosxp_alert"))
import install_alert  # noqa: E402


def mews(rr=None, pulse=None, bps=None, temperature=None, consc=None, avpu=True):
    """คำนวณ MEWS ด้วยนิพจน์ SQL ตัวเดียวกับที่ใส่ใน script (รันบน sqlite)"""
    db = sqlite3.connect(":memory:")
    db.execute("create table opdscreen (rr, pulse, bps, temperature, consciousness_level_type_id)")
    db.execute("insert into opdscreen values (?, ?, ?, ?, ?)", (rr, pulse, bps, temperature, consc))
    return db.execute(f"select {install_alert.mews_sql(avpu)} from opdscreen").fetchone()[0]


@pytest.mark.parametrize("kw,score", [
    (dict(rr=16, pulse=80, bps=120, temperature=37.0), 1),       # RR 15-20 = 1
    (dict(rr=12, pulse=80, bps=120, temperature=37.0), 0),
    (dict(rr=8, pulse=80, bps=120, temperature=37.0), 2),
    (dict(rr=25, pulse=80, bps=120, temperature=37.0), 2),
    (dict(rr=30, pulse=80, bps=120, temperature=37.0), 3),
    (dict(rr=12, pulse=40, bps=120, temperature=37.0), 2),
    (dict(rr=12, pulse=45, bps=120, temperature=37.0), 1),
    (dict(rr=12, pulse=105, bps=120, temperature=37.0), 1),
    (dict(rr=12, pulse=120, bps=120, temperature=37.0), 2),
    (dict(rr=12, pulse=130, bps=120, temperature=37.0), 3),
    (dict(rr=12, pulse=80, bps=70, temperature=37.0), 3),
    (dict(rr=12, pulse=80, bps=75, temperature=37.0), 2),
    (dict(rr=12, pulse=80, bps=90, temperature=37.0), 1),
    (dict(rr=12, pulse=80, bps=200, temperature=37.0), 2),
    (dict(rr=12, pulse=80, bps=120, temperature=35.0), 2),
    (dict(rr=12, pulse=80, bps=120, temperature=38.4), 0),
    (dict(rr=12, pulse=80, bps=120, temperature=38.5), 2),
    (dict(rr=28, pulse=120, bps=90, temperature=37.0), 5),      # ตัวอย่างในโปสเตอร์ คิดถูกได้ 5
])
def test_mews_standard_table(kw, score):
    assert mews(**kw) == score


def test_mews_blank_values_score_zero():
    """ช่องว่าง/ค่าผิด (เช่น T 0, BP 0) ต้องไม่ถูกนับเป็นค่าต่ำสุดแล้วได้ 3 คะแนน"""
    assert mews() == 0
    assert mews(rr=0, pulse=0, bps=0, temperature=0) == 0


def test_mews_avpu():
    base = dict(rr=12, pulse=80, bps=120, temperature=37.0)
    assert [mews(**base, consc=c) for c in (1, 2, 3, 4, None)] == [0, 1, 2, 3, 0]
    assert mews(**base, consc=4, avpu=False) == 0     # HOSxP ที่ไม่มีช่อง AVPU


def test_build_script_fills_placeholders():
    text = install_alert.build_script("011,012", avpu=True, mews_alert=5)
    assert "{{" not in text and "MEWS" in text
    assert '"011","012"' in text
    assert "consciousness_level_type_id" in text
    assert "if fmews > 4 then" in text
    text.encode("tis-620")                     # อักษรนอก TIS-620 ทำให้ script บันทึกไม่ได้
    assert "\r\n" in text and "\n" not in text.replace("\r\n", "")


def test_build_script_without_avpu_and_mews_off():
    text = install_alert.build_script("011", avpu=False, mews_alert=0)
    assert "consciousness_level_type_id" not in text   # คอลัมน์ไม่มีใน HOSxP รุ่นเก่า = script พังเงียบ
    assert "if fmews > 98 then" in text                 # 0 = ไม่เตือน MEWS แยก


def test_script_strings_have_no_single_quote_inside_sql():
    """นิพจน์ MEWS อยู่ในสตริง Pascal '...' — ห้ามมี ' ในนิพจน์"""
    assert "'" not in install_alert.mews_sql(True)


def test_pascal_blocks_balanced():
    """begin/end ต้องคู่กัน — HOSxP ไม่แสดง error ถ้าแปลไม่ผ่าน (หน้าต่างเตือนหายเงียบ)"""
    text = install_alert.build_script("011", avpu=True, mews_alert=5)
    code = "\n".join(re.sub(r"'[^']*'", "''", ln.split("//")[0]) for ln in text.splitlines())
    begins = len(re.findall(r"\bbegin\b", code, re.I))
    ends = len(re.findall(r"\bend\b", code, re.I))
    assert begins == ends
