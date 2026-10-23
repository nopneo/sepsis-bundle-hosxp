"""บันทึกพยาบาลหอผู้ป่วยจาก KIPS (kips_ipd_nurse_focus_detail) — ตัวอย่างตามรูปแบบที่พบจริง ไม่มีข้อมูลผู้ป่วย"""

from datetime import date, datetime, timedelta

import pytest

import db
import nurse_notes
import queries

# เคสลงรหัส sepsis หลัง admit · time-zero = เวลามาถึง ER 11:32
CASE = {
    "vn": "691016113000", "hn": "0012345", "an": "690001234", "case_key": "690001234",
    "vstdate": "2026-10-16", "vsttime": "11:30:00", "enter_er_time": "2026-10-16 11:32:00",
    "regdate": "2026-10-16", "regtime": "11:50:00", "icd10_list": "A41.9",
    "ward": "01", "ward_name": "อายุรกรรม", "cur_bedno": "5",
}


def test_written_at_resolves_time_backwards():
    """เวลาในบันทึกที่เขียนหลังเที่ยงคืน หมายถึงเวลาก่อนหน้าที่เขียน ไม่ใช่วันถัดไป"""
    r = nurse_notes.parse("- Ceftriaxone 2 g iv drip at 23.40 น.", datetime(2026, 10, 16, 23, 0),
                          written_at=datetime(2026, 10, 17, 0, 20))
    assert r["antibiotic"]["dt"] == datetime(2026, 10, 16, 23, 40)


@pytest.fixture
def kips_db(monkeypatch):
    """ฐานข้อมูลปลอมที่มีตาราง KIPS — ใส่แถวบันทึกใน rows["kips"] / คำสั่งยาบนหอใน rows["ward"]"""
    rows = {"kips": [], "ward": None, "sql": []}

    def fetch(sql, params=None):
        db._bind(sql, params)
        rows["sql"].append((sql, params))
        if "information_schema" in sql:
            return [{"x": 1}] if (params or {}).get("table", "").startswith("kips_") else []
        if "kips_ipd_nurse_focus_detail" in sql:
            return rows["kips"]
        if "item_type = 'P'" in sql and rows["ward"]:
            return [{"t": rows["ward"]}]
        if "from drugitems" in sql:
            return [{"icode": "1500001"}]
        return []

    monkeypatch.setattr(queries, "fetch", fetch)
    monkeypatch.setattr(queries, "fetch_one", lambda sql, params=None: (fetch(sql, params) or [{}])[0])
    monkeypatch.setattr(queries, "_schema_cache", {})
    monkeypatch.setattr(queries, "_antibiotic_icodes", None)
    queries._er_logs.cache_clear()
    return rows


def _row(d, t, text):
    """แถวแบบที่ pymysql คืน: record_date เป็น date, record_time เป็น timedelta"""
    h, m = map(int, t.split(":"))
    return {"record_date": d, "record_time": timedelta(hours=h, minutes=m), "txt": text}


def test_kips_timed_note_gives_antibiotic_time(kips_db):
    kips_db["kips"] = [_row(date(2026, 10, 16), "13:10",
                            "ประเมินสภาพแรกรับ\n- Cef-3 2 gm iv drip at 12.20 น.\n- Record V/S q 1 hr")]
    r = queries.bundle_status(dict(CASE))
    atb = r["elements"]["antibiotic"]
    assert atb["at"] == "2026-10-16 12:20" and atb["elapsed_min"] == 48 and atb["compliant"] is True
    assert "KIPS" in atb["source"] and atb["detail"].startswith("Cef-3")
    assert r["data"]["nurse_atb_time"] is True


def test_kips_untimed_note_uses_record_time_as_upper_bound(kips_db):
    """บันทึกแผนการพยาบาลที่ไม่ระบุเวลา = ให้แล้ว "ไม่ช้ากว่า" เวลาบันทึก — ใช้เมื่อเร็วกว่าเวลาสั่ง"""
    kips_db["kips"] = [_row(date(2026, 10, 16), "12:25",
                            "- ให้ยา Cef-3 2 gm v OD\n- on 0.9% NSS 1000 ml IV rate 80 ml/hr")]
    kips_db["ward"] = "2026-10-16 12:50:00"
    r = queries.bundle_status(dict(CASE))
    atb, fluid = r["elements"]["antibiotic"], r["elements"]["iv_fluid"]
    assert atb["at"] == "2026-10-16 12:25" and "เวลาบันทึก" in atb["source"]
    assert fluid["at"] == "2026-10-16 12:25" and "KIPS" in fluid["source"]
    assert r["data"]["nurse_atb_time"] is False   # ไม่ใช่เวลาให้จริงที่พยาบาลเขียน


def test_order_earlier_than_untimed_kips_note_wins(kips_db):
    kips_db["kips"] = [_row(date(2026, 10, 16), "16:00", "- ให้ยา Cef-3 2 gm v OD")]
    kips_db["ward"] = "2026-10-16 12:10:00"
    atb = queries.bundle_status(dict(CASE))["elements"]["antibiotic"]
    assert atb["at"] == "2026-10-16 12:10" and atb["source"] == "สั่งยาบนหอผู้ป่วย"


def test_kips_oxygen_and_urine(kips_db):
    kips_db["kips"] = [_row(date(2026, 10, 16), "12:30", "- On O2 canular 3 LPM\n- retained foley cath")]
    r = queries.bundle_status(dict(CASE))
    o2, uo = r["elements"]["oxygen"], r["elements"]["urine_output"]
    assert o2["compliant"] is True and "KIPS" in o2["source"] and "12:30" in o2["note"]
    assert uo["compliant"] is True and "KIPS" in uo["source"]


def test_kips_buddhist_year_and_out_of_window(kips_db):
    kips_db["kips"] = [
        _row(date(2569, 10, 16), "12:40", "- Ceftriaxone 2 g iv at 12.35 น."),   # ปี พ.ศ. ในช่อง record_date
        _row(date(2026, 10, 20), "09:00", "- On O2 canular 3 LPM"),              # พ้นช่วงค้นหา 24 ชม.
    ]
    r = queries.bundle_status(dict(CASE))
    assert r["elements"]["antibiotic"]["at"] == "2026-10-16 12:35"
    assert r["elements"]["oxygen"]["compliant"] is None


def test_kips_query_uses_parameters(kips_db):
    queries.bundle_status(dict(CASE))
    sql, params = next((s, p) for s, p in kips_db["sql"] if "kips_ipd_nurse_focus_detail" in s)
    assert CASE["an"] not in sql and params["an"] == CASE["an"]


def test_no_kips_table_no_kips_query(fake_db):
    """ฐานที่ไม่มีตาราง KIPS (รพ.อื่น) ต้องไม่ยิงคำสั่ง KIPS เลย"""
    queries.bundle_status(dict(CASE))
    assert not any("kips_ipd_nurse_focus_detail" in sql for sql, _ in fake_db)
