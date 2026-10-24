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
    assert "e *[1-4] *v *[1-5t] *m *[1-6]" in text and "o.hpi, o.pe" in text and "o.note" not in text
    none = install_alert.build_script("011", er_gcs=False, spo2_cols=set())
    assert "e *[1-4]" not in none


def test_script_code_has_no_param_or_comment_chars():
    """script ที่ใช้งานได้จริงไม่มี ? { } ในโค้ด — ? ถูกตัวเชื่อมฐานตีความเป็นพารามิเตอร์ / { } เป็นคอมเมนต์ Pascal
    (23 ต.ค. 2026: regex ที่มี ? และ {0,12} ทำให้ script ใน hos_test ไม่ขึ้นอะไรเลย)"""
    text = install_alert.build_script("011", avpu=True, er_gcs=True)
    for n, ln in enumerate(text.splitlines(), 1):
        code = ln.split("//")[0] if not ln.lstrip().startswith("'") else ln
        assert not any(ch in code for ch in "?{}"), (n, ln)


def test_script_literals_fit_hosxp_limit():
    """literal '...' ยาวเกิน 255 ทำให้ script หยุดเงียบ (23 ต.ค. 2026: SQL SpO2/qSOFA 335-599 ตัว)"""
    for kw in ({}, {"avpu": True, "er_gcs": True}):
        text = install_alert.build_script("011", **kw)
        for n, ln in enumerate(text.splitlines(), 1):
            if ln.lstrip().startswith("//"):
                continue
            for lit in re.findall(r"'(?:[^'\n]|'')*'", ln):
                assert len(lit) - 2 <= 250, (n, ln[:80])   # script SOS เดิมที่ใช้ได้จริงยาวสุด 238


def test_numeric_queries_return_double():
    """getsqldata ที่คืนจำนวนเต็มแล้วนำไปต่อข้อความ = script หยุดเงียบ (23 ต.ค. 2026: MEWS ทำให้ hos_test ไม่ขึ้นอะไร)
    ค่าที่ใช้ได้จริง (SOS, T) เป็นทศนิยม — ห่อ concat(...) + 0 ทุกค่าที่เป็นผลคำนวณ"""
    text = install_alert.build_script("011", avpu=True, er_gcs=True)
    for alias in ("mews", "spo2", "q", "k"):
        joined = text.replace("'+\r\n'", "")
        end = joined.index(f" as {alias} from")
        expr = joined[joined.rindex("getsqldata('select ", 0, end) + len("getsqldata('select "):end]
        assert expr.startswith("concat(") and expr.endswith(") + 0"), alias


def test_mews_qsofa_headline_red():
    """MEWS / qSOFA เป็นแถวตัวใหญ่ขนาดเดียวกับ SOS Score สีแดงในทั้งหน้าต่างแดงและ ER (ผู้ใช้ขอ 23 ต.ค. 2026)"""
    text = install_alert.build_script("011", avpu=True, er_gcs=True)
    assert re.findall(r"lbScore2\.Font\.Color := (\d+);", text) == ["1315980", "1315980"]
    assert text.count("lbScore2.Font.Height := -28;") == 2   # MEWS + qSOFA + SIRS + MAP แถวเดียว กรอบกว้าง 840 px
    assert text.count("pnRed.Width := 840;") == 2 and text.count("frm.Width := 900;") == 2
    assert text.count("lbScore2.Caption := 'MEWS = ' + fmews + '   ' + fqtext + '   SIRS = ' + fsirs + '/4   ' + fmaptext;") == 2
    assert "'   MEWS ' + fmews" not in text                    # ไม่ซ้ำในบรรทัดสัญญาณชีพ


def test_split_long_literals_keeps_sql():
    sql = 'select ' + ' + '.join(f'(case when x{i} regexp "a *b c" then 1 else 0 end)' for i in range(20))
    out = install_alert.split_long_literals(f"f := getsqldata('{sql}');")
    pieces = re.findall(r"'((?:[^'\n]|'')*)'", out)
    assert "".join(pieces) == sql and all(len(p) <= install_alert.LITERAL_MAX for p in pieces)
    assert all(p.count('"') % 2 == 0 for p in pieces)     # ไม่ตัดกลาง "..."


def qsofa_text(cc="", hpi="", text_cols=("cc", "hpi")):
    """qSOFA ข้อสติจากวลีใน CC/HPI (sqlite + regexp ของ Python ซึ่งให้ผลเหมือน MariaDB กับ regex ชุดนี้)"""
    db = sqlite3.connect(":memory:")
    db.create_function("regexp", 2, lambda p, s: 1 if re.search(p, s or "") else 0)
    db.create_function("concat_ws", -1, lambda sep, *a: sep.join(x for x in a if x is not None))
    db.create_function("regexp_substr", 2, lambda s, p: (re.search(p, s or "") or [""])[0])
    db.execute("create table opdscreen (vn, rr, bps, cc, hpi)")
    db.execute("insert into opdscreen values ('1', 24, 120, ?, ?)", (cc, hpi))
    q = db.execute(f"select {install_alert.qsofa_sql(False, False, set(text_cols))} from opdscreen o").fetchone()[0]
    k = db.execute(f"select {install_alert.qsofa_known_sql(False, False, set(text_cols))} from opdscreen o").fetchone()[0]
    return q, k


@pytest.mark.parametrize("cc,hpi,expected", [
    ("ไข้ ซึมมา 2 วัน", "", (2, 1)),
    ("ไข้ซึมลง", "", (2, 1)),
    ("", "ญาติว่าดูซึม เรียกไม่ค่อยตอบ", (2, 1)),
    ("", "สับสน พูดไม่รู้เรื่อง", (2, 1)),
    ("", "pt drowsy, confused", (2, 1)),
    ("เรียกไม่ตื่น", "", (2, 1)),
    ("ไม่รู้สึกตัว", "", (2, 1)),
    ("", "ไม่สับสน รู้สึกตัวดี", (1, 1)),            # เชิงปฏิเสธ = ประเมินแล้ว ปกติ
    ("", "no confusion", (1, 1)),
    ("แผลที่ขา มีเลือดซึมมา", "", (1, 0)),           # ของเหลวซึม ไม่ใช่สติ
    ("แผลซึม", "", (1, 0)),
    ("ซึมเศร้า", "", (1, 0)),
    ("ไข้ ไอ", "", (1, 0)),
    ("ซึมมา 1 วัน", "", (2, 1)),
])
def test_qsofa_mentation_phrases(cc, hpi, expected):
    assert qsofa_text(cc, hpi) == expected


def test_qsofa_phrases_skip_missing_columns():
    assert "o.cc" not in install_alert.qsofa_sql(False, False, {"hpi"})
    assert "สับสน" not in install_alert.qsofa_sql(False, False, set())


def sirs(t=None, p=None, rr=None, labs=(), wbc="35", band="2015"):
    """SIRS ด้วยนิพจน์ SQL ตัวเดียวกับใน script (sqlite) — labs = [(lab_items_code, ผล)] ของ visit นี้"""
    db = sqlite3.connect(":memory:")
    db.create_function("regexp", 2, lambda pat, s: 1 if re.search(pat, s or "") else 0)
    db.create_function("if", 3, lambda c, a, b: a if c else b)
    db.execute("create table opdscreen (vn, temperature, pulse, rr)")
    db.execute("create table lab_head (lab_order_number, vn)")
    db.execute("create table lab_order (lab_order_number, lab_items_code, lab_order_result)")
    db.execute("insert into opdscreen values ('1', ?, ?, ?)", (t, p, rr))
    db.execute("insert into lab_head values (9, '1')")
    db.executemany("insert into lab_order values (9, ?, ?)", labs)
    s = db.execute(f"select {install_alert.sirs_sql(wbc, band)} from opdscreen o").fetchone()[0]
    k = db.execute(f"select {install_alert.sirs_wbc_known_sql(wbc)} from opdscreen o").fetchone()[0]
    return s, k


@pytest.mark.parametrize("kw,expected", [
    (dict(t=37, p=80, rr=18), (0, 0)),
    (dict(t=38.5, p=95, rr=22), (3, 0)),                          # T > 38, HR > 90, RR > 20
    (dict(t=38.0, p=90, rr=20), (0, 0)),                          # ขอบเขต: ต้องมากกว่า ไม่ใช่เท่ากับ
    (dict(t=35.5, p=80, rr=18), (1, 0)),                          # ตัวเย็น < 36
    (dict(t=0, p=0, rr=0), (0, 0)),                               # ช่องว่าง/0 ไม่นับ
    (dict(t=37, p=80, rr=18, labs=[(35, "15.2")]), (1, 1)),       # WBC x10^3 > 12
    (dict(t=37, p=80, rr=18, labs=[(35, "3.1")]), (1, 1)),        # WBC < 4
    (dict(t=37, p=80, rr=18, labs=[(35, "9.6")]), (0, 1)),        # มีผล ปกติ
    (dict(t=37, p=80, rr=18, labs=[(35, "15200")]), (1, 1)),      # หน่วย cell
    (dict(t=37, p=80, rr=18, labs=[(35, "pending")]), (0, 0)),    # ไม่ใช่ตัวเลข = ยังไม่มีผล
    (dict(t=37, p=80, rr=18, labs=[(35, "8"), (2015, "12")]), (1, 1)),   # Band > 10%
    (dict(t=39, p=120, rr=30, labs=[(35, "18")]), (4, 1)),
])
def test_sirs(kw, expected):
    assert sirs(**kw) == expected


def test_sirs_without_lab_codes():
    assert sirs(t=39, p=120, rr=30, labs=[(35, "18")], wbc="", band="") == (3, 0)
    assert "lab_head" not in install_alert.sirs_sql("", "")


def test_build_script_shows_sirs():
    text = install_alert.build_script("011", wbc_items="35", band_items="2015")
    assert "lab_items_code in (35)" in text and "lab_items_code in (2015)" in text
    assert "(SIRS ยังไม่มีผล WBC)" in text and "{{" not in text


def test_skip_alert_in_lab_order_window():
    """HOSxP เรียก script ทุกครั้งที่บันทึก visit รวมหน้าสั่ง Lab (frmLabOrder) — หน้าซักประวัติต้องเตือนทุกครั้ง (23 ต.ค. 2026)"""
    text = install_alert.build_script("011")
    assert "fform := Screen.ActiveForm.Name;" in text
    assert "if (fform = 'frmLabOrder') or (fform = 'ERDetailEntryForm') then\r\nbegin\r\n  fage := '0';" in text
    assert text.index("fform := Screen.ActiveForm.Name;") < text.index("if fage > 14 then")
    assert "SetGlobalValue" not in text and "{{" not in text
    two = install_alert.build_script("011", skip_forms="frmLabOrder, frmDrugOrder")
    assert "if (fform = 'frmLabOrder') or (fform = 'frmDrugOrder') then" in two
    with pytest.raises(SystemExit):
        install_alert.build_script("011", skip_forms="frm'; x")



@pytest.mark.parametrize("bps,bpd,expected", [
    (80, 40, 53), (120, 80, 93), (90, 60, 70),
    (0, 0, 0), (None, None, 0), (120, 0, 0),        # ไม่ได้วัด = 0 -> script แสดง "MAP = missing"
])
def test_map(bps, bpd, expected):
    """MAP = (SBP + 2 x DBP) / 3 ด้วยนิพจน์ใน script (sqlite)"""
    text = install_alert.build_script("011")
    sql = re.search(r"fmap := getsqldata\('select (.*?) as m '", text).group(1)
    db = sqlite3.connect(":memory:")
    db.create_function("if", 3, lambda c, a, b: a if c else b)
    db.create_function("concat", 1, lambda x: str(x))
    db.execute("create table opdscreen (bps, bpd)")
    db.execute("insert into opdscreen values (?, ?)", (bps, bpd))
    assert float(db.execute(f"select {sql} from opdscreen").fetchone()[0]) == expected
    assert "if fmap < 65 then" in text and "'MAP = missing'" in text


def news2(rr=None, sp=0, bps=None, pulse=None, temperature=None, consc=None):
    """คำนวณ NEWS2 ด้วยนิพจน์ SQL ตัวเดียวกับที่ใส่ใน script (sqlite, ไม่มีคอลัมน์ข้อความ)"""
    db = sqlite3.connect(":memory:")
    db.execute("create table opdscreen (vn, rr, bps, pulse, temperature, consciousness_level_type_id)")
    db.execute("create table er_nursing_detail (vn, gcs_e, gcs_v, gcs_m)")
    db.execute("insert into opdscreen values ('1', ?, ?, ?, ?, ?)", (rr, bps, pulse, temperature, consc))
    sql = f"select {install_alert.news2_sql(True, False)} from (select *, ? sp from opdscreen) o"
    return db.execute(sql, (sp,)).fetchone()[0]


NORMAL = dict(rr=16, sp=97, bps=120, pulse=80, temperature=37.0)


@pytest.mark.parametrize("change,score", [
    ({}, 0),
    (dict(rr=8), 3), (dict(rr=9), 1), (dict(rr=11), 1), (dict(rr=21), 2), (dict(rr=24), 2), (dict(rr=25), 3),
    (dict(sp=91), 3), (dict(sp=92), 2), (dict(sp=93), 2), (dict(sp=94), 1), (dict(sp=95), 1), (dict(sp=0), 0),
    (dict(bps=90), 3), (dict(bps=91), 2), (dict(bps=100), 2), (dict(bps=105), 1), (dict(bps=219), 0),
    (dict(bps=220), 3),
    (dict(pulse=40), 3), (dict(pulse=45), 1), (dict(pulse=91), 1), (dict(pulse=111), 2), (dict(pulse=130), 2),
    (dict(pulse=131), 3),
    (dict(temperature=35.0), 3), (dict(temperature=35.5), 1), (dict(temperature=38.0), 0),
    (dict(temperature=38.5), 1), (dict(temperature=39.1), 2),
    (dict(consc=2), 3), (dict(consc=1), 0),
])
def test_news2_standard_table(change, score):
    assert news2(**{**NORMAL, **change}) == score


def test_news2_blank_values_score_zero():
    assert news2() == 0
    assert news2(rr=0, sp=0, bps=0, pulse=0, temperature=0) == 0


def test_news2_sum():
    assert news2(rr=22, sp=93, bps=95, pulse=115, temperature=39.2, consc=2) == 2 + 2 + 2 + 2 + 2 + 3


def test_build_script_news2():
    text = install_alert.build_script("011", avpu=True, er_gcs=True, news2_alert=5)
    assert "fnews" in text and "NEWS2" in text and "if fnews > 4 then" in text
    assert "on *o2" in text                                 # ให้ออกซิเจน (+2) อ่านจากข้อความ
    off = install_alert.build_script("011", news2_alert=0)
    assert "if fnews > 98 then" in off


def test_news2_big_on_sos_row():
    """NEWS2 ตัวใหญ่แถวเดียวกับ SOS Score ต่อจาก T ทั้งหน้าต่างแดงและ ER (ผู้ใช้ขอ 24 ต.ค. 2026) ไม่ซ้ำในบรรทัดสัญญาณชีพ"""
    text = install_alert.build_script("011", avpu=True, er_gcs=True)
    assert text.count("lbScore.Caption := 'SOS Score = ' + fptname + '     T ' + ftemp + ' C     NEWS2 = ' + fnews;") == 2
    assert "'   NEWS2 ' + fnews" not in text


def test_shock_index_in_vital_line():
    """Shock Index = ชีพจร / SBP ในบรรทัดสัญญาณชีพ (ผู้ใช้ขอ 24 ต.ค. 2026) · สูงเมื่อ >= 1.0 = ชีพจร >= SBP
    แถวตัวใหญ่กว้างไม่พอ (MEWS/qSOFA/SIRS/MAP + SI เกินกรอบ 840 px)"""
    text = install_alert.build_script("011", avpu=True, er_gcs=True)
    assert "round(pulse / bps, 2)" in text and "pulse >= bps" in text
    assert "fvital := fvital + '   SI ' + fsi + ' สูง';" in text
    assert text.count("fsi :=") == 1 and text.count("fsihigh :=") == 1


def test_shock_index_sql_values():
    db = sqlite3.connect(":memory:")
    db.execute("create table opdscreen (vn, pulse, bps)")
    rows = [("1", 140.0, 128.0), ("2", 80.0, 120.0), ("3", 0.0, 120.0), ("4", 120.0, 0.0)]   # HOSxP เก็บเป็น double
    db.executemany("insert into opdscreen values (?, ?, ?)", rows)
    si = dict(db.execute(f"select vn, {install_alert.SI_SQL} from opdscreen").fetchall())
    hi = dict(db.execute(f"select vn, {install_alert.SI_HIGH_SQL} from opdscreen").fetchall())
    assert si == {"1": 1.09, "2": 0.67, "3": 0, "4": 0}
    assert hi == {"1": "1", "2": "0", "3": "0", "4": "0"}
