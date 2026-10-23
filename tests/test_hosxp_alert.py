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


def test_build_script_spo2():
    text = install_alert.build_script("011", avpu=False, mews_alert=5, spo2_alert=92)
    assert "o2sat" in text and "regexp_substr" in text
    assert "if fspo2 < 92 then" in text
    assert "'" not in install_alert.SPO2_SQL
    off = install_alert.build_script("011", avpu=False, mews_alert=5, spo2_alert=0)
    assert "if fspo2 < 0 then" in off                    # 0 = ไม่เตือน SpO2 แยก (ไม่มีค่าใดต่ำกว่า 0)


def qsofa(rr=None, bps=None, consc=None, gcs=None, avpu=True, er_gcs=True):
    """คำนวณ qSOFA ด้วยนิพจน์ SQL ตัวเดียวกับที่ใส่ใน script (รันบน sqlite) — คืน (คะแนน, ประเมินสติได้ไหม)"""
    db = sqlite3.connect(":memory:")
    db.execute("create table opdscreen (vn, rr, bps, consciousness_level_type_id)")
    db.execute("create table er_nursing_detail (vn, gcs_e, gcs_v, gcs_m)")
    db.execute("insert into opdscreen values ('1', ?, ?, ?)", (rr, bps, consc))
    if gcs:
        db.execute("insert into er_nursing_detail values ('1', ?, ?, ?)", gcs)
    score = db.execute(f"select {install_alert.qsofa_sql(avpu, er_gcs)} from opdscreen o").fetchone()[0]
    known = db.execute(f"select {install_alert.qsofa_known_sql(avpu, er_gcs)} from opdscreen o").fetchone()[0]
    return score, known


@pytest.mark.parametrize("kw,expected", [
    (dict(rr=20, bps=120), (0, 0)),
    (dict(rr=22, bps=120), (1, 0)),                       # RR >= 22
    (dict(rr=22, bps=100), (2, 0)),                       # SBP <= 100
    (dict(rr=22, bps=0), (1, 0)),                         # BP 0 = ไม่ได้วัด ไม่นับ
    (dict(rr=None, bps=None), (0, 0)),
    (dict(rr=24, bps=90, consc=2), (3, 1)),               # AVPU = V
    (dict(rr=18, bps=120, consc=1), (0, 1)),              # AVPU = A ประเมินแล้ว ปกติ
    (dict(rr=24, bps=90, gcs=(3, 4, 6)), (3, 1)),         # GCS 13 < 15
    (dict(rr=24, bps=90, gcs=(4, 5, 6)), (2, 1)),         # GCS 15 ปกติ
    (dict(rr=24, bps=90, gcs=(98, 5, 6)), (2, 0)),        # ค่าผิด (E 98) ไม่นับ
])
def test_qsofa(kw, expected):
    assert qsofa(**kw) == expected


def test_qsofa_without_consciousness_columns():
    assert qsofa(rr=24, bps=90, consc=3, gcs=(1, 1, 1), avpu=False, er_gcs=False) == (2, 0)


def test_build_script_shows_qsofa_in_sepsis_window():
    text = install_alert.build_script("011", avpu=True, mews_alert=5, er_gcs=True)
    assert "er_nursing_detail" in text and "qSOFA" in text
    assert "'" not in install_alert.qsofa_sql(True, True) + install_alert.qsofa_known_sql(True, True)
    old = install_alert.build_script("011", avpu=False, mews_alert=5, er_gcs=False)
    assert "er_nursing_detail" not in old and "consciousness_level_type_id" not in old


def test_build_script_reads_gcs_from_text():
    """GCS ส่วนใหญ่พิมพ์ใน HPI/PE เช่น E4V5M6 — script ต้องอ่านจากข้อความด้วย และไม่อ้างคอลัมน์ที่ไม่มี"""
    text = install_alert.build_script("011", er_gcs=False, spo2_cols={"hpi", "pe"})
    assert "e ?[1-4] ?v ?[1-5t] ?m ?[1-6]" in text and "o.hpi, o.pe" in text and "o.note" not in text
    none = install_alert.build_script("011", er_gcs=False, spo2_cols=set())
    assert "e ?[1-4]" not in none
