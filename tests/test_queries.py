from datetime import date, datetime, timedelta

import queries
import scheduler

CASE = {
    "vn": "691016113000", "hn": "0012345", "an": "690001234", "case_key": "690001234",
    "vstdate": "2026-10-16", "vsttime": "11:30:00", "enter_er_time": "2026-10-16 11:32:00",
    "regdate": "2026-10-16", "regtime": "13:00:00", "icd10_list": "A41.9",
    "ward": "01", "ward_name": "อายุรกรรม", "cur_bedno": "5",
}


def test_case_queries_use_parameters(fake_db):
    """ค่าของเคส (vn/hn/an/วันที่/รหัส) ต้องไปทางพารามิเตอร์ ไม่ถูกต่อเข้า SQL ตรง ๆ"""
    queries.find_sepsis_cases(d1=date(2026, 10, 1), d2=date(2026, 10, 16))
    queries.bundle_status(dict(CASE))
    assert fake_db
    for sql, _ in fake_db:
        for value in (CASE["vn"], CASE["hn"], CASE["an"], "2026-10"):
            assert value not in sql, sql
    icd = next(p for sql, p in fake_db if "icd_p0" in sql)
    assert icd["icd_p0"] == "A40%" and icd["icd_e0"] == "R572"


def test_bundle_status_with_no_data(fake_db):
    r = queries.bundle_status(dict(CASE))
    assert r["auto_compliant"] is False
    assert all(r["elements"][k]["at"] is None for k in ("lactate", "culture", "antibiotic", "iv_fluid"))


def test_closed_cases_are_cached(fake_db, monkeypatch):
    calls = []
    monkeypatch.setattr(queries, "bundle_status", lambda c: calls.append(c["vn"]) or {"vn": c["vn"]})
    tz = datetime(2026, 10, 16, 11, 32)
    later = tz + timedelta(hours=queries.WINDOW_END_HOURS + 2)
    queries._cached_bundle_status(dict(CASE), now=later)
    queries._cached_bundle_status(dict(CASE), now=later)
    assert len(calls) == 1
    # ICD10 เปลี่ยน = คำนวณใหม่
    queries._cached_bundle_status(dict(CASE, icd10_list="A41.9, R57.2"), now=later)
    assert len(calls) == 2


def test_open_cases_are_not_cached(fake_db, monkeypatch):
    calls = []
    monkeypatch.setattr(queries, "bundle_status", lambda c: calls.append(c["vn"]) or {"vn": c["vn"]})
    soon = datetime(2026, 10, 16, 12, 0)
    queries._cached_bundle_status(dict(CASE), now=soon)
    queries._cached_bundle_status(dict(CASE), now=soon)
    assert len(calls) == 2


def test_antibiotic_list_reloads_after_ttl(fake_db, monkeypatch):
    queries._antibiotics()
    queries._antibiotics()
    n = sum("antibiotic='Y'" in sql for sql, _ in fake_db)
    assert n == 1
    monkeypatch.setattr(queries, "_antibiotic_loaded", 0.0)
    queries._antibiotics()
    assert sum("antibiotic='Y'" in sql for sql, _ in fake_db) == 2


def test_time_zero_prefers_explicit_recognition_even_if_later(monkeypatch):
    """er_regist.sepsis_recognition_datetime/triage_datetime ชัดเจนอยู่แล้ว — ใช้ตามนั้นเสมอ ไม่เทียบกับ SOS"""
    monkeypatch.setattr(queries, "_schema_cache", {("er_regist_vital_sign_list", None): False})
    case = dict(CASE, triage_datetime="2026-10-16 12:00:00")
    tz, src = queries.time_zero_info(case)
    assert (src, tz) == ("triage", datetime(2026, 10, 16, 12, 0))


def test_time_zero_uses_sos_cross_time_when_earlier_than_enter_er(monkeypatch):
    """enter_er_time (08:59) กรอกช้ากว่าสัญญาณชีพจริงที่เข้าเกณฑ์ SOS (08:40) — ต้องใช้เวลา SOS แทน"""
    row = {"d": date(2026, 10, 28), "t": "08:40:00", "temp": 38.8, "bps": 85, "pr": 125, "rr": 28}

    def fetch(sql, params=None):
        return [row] if "er_regist_vital_sign_list" in sql else []

    monkeypatch.setattr(queries, "fetch", fetch)
    monkeypatch.setattr(queries, "_schema_cache", {("er_regist_vital_sign_list", None): True})
    case = dict(CASE, vstdate="2026-10-28", vsttime="08:29:00", enter_er_time="2026-10-28 08:59:00")
    tz, src = queries.time_zero_info(case)
    assert (src, tz) == ("sos_alert", datetime(2026, 10, 28, 8, 40))


def test_time_zero_keeps_enter_er_when_sos_not_earlier(monkeypatch):
    """ไม่พบสัญญาณชีพเข้าเกณฑ์ SOS ก่อน enter_er_time — ยังใช้ enter_er_time เหมือนเดิม"""
    monkeypatch.setattr(queries, "fetch", lambda sql, params=None: [])
    monkeypatch.setattr(queries, "_schema_cache", {("er_regist_vital_sign_list", None): True})
    tz, src = queries.time_zero_info(dict(CASE))
    assert (src, tz) == ("enter_er", datetime(2026, 10, 16, 11, 32))


def test_scheduler_lock_is_exclusive(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(scheduler, "LOCK_PATH", str(tmp_path / "scheduler.lock"))
    first = scheduler._try_lock()
    assert first is not None
    assert scheduler._try_lock() is None          # รอบที่สองต้องข้าม
    first.close()
    again = scheduler._try_lock()
    assert again is not None
    again.close()
