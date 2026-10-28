"""เฝ้าระวังหอผู้ป่วย (ward_watch.py) — SOS > 3 ร่วมกับไข้/ตัวเย็น จาก ipd_nurse_note"""

import json
from datetime import date, datetime, timedelta

import flex
import notify
import queries
import scheduler
import ward_watch

NOW = datetime(2026, 10, 27, 14, 0)


def _row(an="690001234", minutes_ago=30, t=38.6, bps=88, pulse=124, rr=26, **kw):
    at = NOW - timedelta(minutes=minutes_ago)
    return {"an": an, "note_date": at.date(), "note_time": at.strftime("%H:%M:%S"),
            "temperature": t, "pulse": pulse, "rr": rr, "bps": bps, "bpd": 50,
            "spo2": None, "spo2_o2": None, "ward": "01", "ward_name": "หอผู้ป่วยใน",
            "bedno": "5", **kw}


def _fake(monkeypatch, rows):
    calls = []

    def fetch(sql, params=None):
        calls.append((sql, params))
        return rows

    monkeypatch.setattr(queries, "fetch", fetch)
    monkeypatch.setattr(queries, "_schema_cache", {("ipd_nurse_note", None): True})
    monkeypatch.setattr(ward_watch, "sepsis_care", lambda an, until: [])
    return calls


def test_sos_matches_er_script_boundaries():
    """ลำดับเงื่อนไขเหมือน SQL ของหน้าต่างเตือน ER — เช่น ชีพจร 100 ได้ 0, RR 35 ได้ 2"""
    assert ward_watch.sos_parts(37, 120, 80, 18) == {"T": 0, "BP": 0, "HR": 0, "RR": 0}
    assert ward_watch.sos_parts(38.6, 80, 140, 36) == {"T": 2, "BP": 3, "HR": 3, "RR": 3}
    assert ward_watch.sos_parts(38.2, 95, 100, 35)["HR"] == 0
    assert ward_watch.sos_parts(38.2, 95, 100, 35)["RR"] == 2
    assert ward_watch.sos_parts(35.5, 185, 45, 8) == {"T": 1, "BP": 1, "HR": 1, "RR": 3}
    # ค่าว่าง/กรอกผิดได้ 0 ไม่ถูกนับเป็นค่าต่ำสุด
    assert ward_watch.sos_parts(None, 0, "", None) == {"T": 0, "BP": 0, "HR": 0, "RR": 0}


def test_fever_or_hypothermia():
    assert ward_watch.fever(38) and ward_watch.fever(35.9)
    assert not ward_watch.fever(36) and not ward_watch.fever(37.9)
    assert not ward_watch.fever(0) and not ward_watch.fever(None) and not ward_watch.fever(15)


def test_find_alerts_needs_sos_and_fever(monkeypatch):
    calls = _fake(monkeypatch, [
        _row(an="A1"),                              # SOS 2+2+2+2=8, ไข้ → เตือน
        _row(an="A2", t=37.2),                      # SOS สูงแต่ไม่มีไข้
        _row(an="A3", bps=120, pulse=80, rr=18),    # ไข้แต่ SOS 2
        _row(an="A4", minutes_ago=240),             # เก่าเกิน 3 ชม.
        _row(an="A5", minutes_ago=-60),             # เวลาในอนาคต (พิมพ์ผิด)
    ])
    found = ward_watch.find_ward_alerts(NOW)
    assert [a["an"] for a in found] == ["A1"]
    a = found[0]
    assert a["sos"] == 8 and a["key"] == "A1|2026-10-27"
    assert a["vitals"]["temp"] == 38.6 and a["bed"] == "5"
    sql, params = calls[0]
    assert "ipd_nurse_note" in sql and "dchdate is null" in sql
    assert params == {"d1": "2026-10-27", "d2": "2026-10-27", "age": 15}


def test_window_crosses_midnight(monkeypatch):
    calls = _fake(monkeypatch, [])
    ward_watch.find_ward_alerts(datetime(2026, 10, 27, 1, 0))
    assert calls[0][1]["d1"] == "2026-10-26" and calls[0][1]["d2"] == "2026-10-27"


def test_disabled_or_missing_table(monkeypatch):
    calls = _fake(monkeypatch, [_row()])
    monkeypatch.setattr(queries, "_schema_cache", {("ipd_nurse_note", None): False})
    assert ward_watch.find_ward_alerts(NOW) == [] and calls == []
    monkeypatch.setattr(ward_watch, "ENABLED", False)
    assert ward_watch.find_ward_alerts(NOW) == []


def test_card_masks_patient_and_serializes(monkeypatch):
    _fake(monkeypatch, [_row(an="690001234")])
    alert = ward_watch.find_ward_alerts(NOW)[0]
    card = json.dumps(flex.ward_alert(alert), ensure_ascii=False)
    assert "690001234" not in card and "xxx" in card
    assert "หอผู้ป่วยใน" in card and "เตียง 5" in card and "SOS" in card
    text = notify.format_ward_alert_message(alert)
    assert "690001234" not in text and "SOS 8" in text


def test_scheduler_sends_once_per_patient_per_day(monkeypatch, tmp_path):
    rows = [_row(an="A1", minutes_ago=90), _row(an="A1", minutes_ago=20), _row(an="B2")]
    _fake(monkeypatch, rows)
    monkeypatch.setattr(scheduler, "DB_PATH", str(tmp_path / "s.sqlite"))
    sent = []
    monkeypatch.setattr(scheduler, "_dispatch", lambda fm, text: sent.append(text))
    conn = scheduler._conn()
    scheduler._run_ward(conn, NOW)
    scheduler._run_ward(conn, NOW)          # รอบถัดไป — ไม่ส่งซ้ำ
    assert len(sent) == 2
    keys = {r[0] for r in conn.execute("select an from notify_log where notify_type='ward_sos'")}
    assert keys == {"A1|2026-10-27", "B2|2026-10-27"}


def test_skips_patients_already_managed_for_sepsis(monkeypatch):
    _fake(monkeypatch, [_row(an="A1"), _row(an="A1", minutes_ago=10), _row(an="B2")])
    asked = []

    def care(an, until):
        asked.append(an)
        return ["สั่งยาปฏิชีวนะ"] if an == "A1" else []

    monkeypatch.setattr(ward_watch, "sepsis_care", care)
    assert [a["an"] for a in ward_watch.find_ward_alerts(NOW)] == ["B2"]
    assert asked == ["A1", "B2"]          # ถามครั้งเดียวต่อ AN
    monkeypatch.setattr(ward_watch, "SKIP_MANAGED", False)
    assert len(ward_watch.find_ward_alerts(NOW)) == 3


def _fake_care_db(monkeypatch, dx=None, atb=None, hc=None, lab_hc=None, er_text="", kips=()):
    def fetch_one(sql, params=None):
        if "from ipt i" in sql:
            return {"vn": "V1", "hn": "H1", "vstdate": date(2026, 10, 26), "vsttime": "10:00:00",
                    "regdate": date(2026, 10, 26), "regtime": "12:00:00"}
        if "er_dx" in sql:
            return {"er_dx": dx, "ipd_dx": None}
        if "opitemrece" in sql:
            return {"atb": atb, "hc": hc}
        if "lab_head" in sql:
            return {"ordered_at": lab_hc}
        if "opdscreen" in sql:
            return {"t": er_text}
        raise AssertionError(sql)

    monkeypatch.setattr(queries, "fetch_one", fetch_one)
    monkeypatch.setattr(queries, "fetch", lambda sql, params=None: [
        {"an": "A1", "record_date": d, "record_time": t, "txt": x} for d, t, x in kips])
    monkeypatch.setattr(queries, "_schema_cache", {("kips_ipd_nurse_focus_detail", None): True})


def test_sepsis_care_none(monkeypatch):
    _fake_care_db(monkeypatch, er_text="0.9%NaCl 1000 ml iv drip 80 cc/hr", kips=[
        (date(2026, 10, 27), "08:00:00", "IV 0.9% NaCl 1000 ml rate 80 cc/hr")])
    assert ward_watch.sepsis_care("A1", NOW) == []


def test_sepsis_care_each_reason(monkeypatch):
    _fake_care_db(monkeypatch, dx="A419")
    assert ward_watch.sepsis_care("A1", NOW) == ["วินิจฉัย A41.9"]
    _fake_care_db(monkeypatch, atb=1)
    assert ward_watch.sepsis_care("A1", NOW) == ["สั่งยาปฏิชีวนะ"]
    _fake_care_db(monkeypatch, hc=1)
    assert ward_watch.sepsis_care("A1", NOW) == ["สั่ง Hemoculture"]
    monkeypatch.setattr(queries, "HEMOCULTURE_LAB_ITEMS", ["1986"])
    _fake_care_db(monkeypatch, lab_hc=datetime(2026, 10, 27, 9, 0))
    assert ward_watch.sepsis_care("A1", NOW) == ["สั่ง Hemoculture"]
    _fake_care_db(monkeypatch, er_text="NSS 1000 ml iv load 500 cc in 30 min")
    assert ward_watch.sepsis_care("A1", NOW) == ["load สารน้ำ"]
    _fake_care_db(monkeypatch, kips=[(date(2026, 10, 27), "09:00:00", "Load NSS 1,000 ml")])
    assert ward_watch.sepsis_care("A1", NOW) == ["load สารน้ำ"]


def test_sepsis_care_ignores_kips_notes_after_alert(monkeypatch):
    # KIPS บางแถวเก็บปี พ.ศ.
    _fake_care_db(monkeypatch, kips=[(date(2569, 10, 27), "16:00:00", "Load NSS 500 ml")])
    assert ward_watch.sepsis_care("A1", NOW) == []


def test_failed_send_retries_next_round(monkeypatch, tmp_path):
    _fake(monkeypatch, [_row(an="A1")])
    monkeypatch.setattr(scheduler, "DB_PATH", str(tmp_path / "s.sqlite"))

    def boom(fm, text):
        raise RuntimeError("network")

    monkeypatch.setattr(scheduler, "_dispatch", boom)
    conn = scheduler._conn()
    scheduler._run_ward(conn, NOW)
    assert conn.execute("select count(*) from notify_log").fetchone()[0] == 0


def test_suspected_cases_groups_by_an_and_drops_diagnosed(monkeypatch):
    rows = [_row(an="A1", minutes_ago=600, hn="H1", dchdate=None, dchtime=None, dchtype=None),
            _row(an="A1", minutes_ago=60, t=39.5, hn="H1", dchdate=None, dchtime=None, dchtype=None),
            _row(an="A1", minutes_ago=30, t=37.0, hn="H1", dchdate=None, dchtime=None, dchtype=None),  # ไม่เข้าเกณฑ์
            _row(an="B2", hn="H2", dchdate=date(2026, 10, 27), dchtime="13:00:00", dchtype="01"),
            _row(an="C3", hn="H3", dchdate=None, dchtime=None, dchtype=None)]

    def fetch(sql, params=None):
        if "iptdiag" in sql:
            return [{"an": "B2", "icd10": "J189", "name": "Pneumonia, unspecified"}]
        return rows if "ipd_nurse_note" in sql else []

    monkeypatch.setattr(queries, "fetch", fetch)
    monkeypatch.setattr(queries, "_schema_cache", {})
    monkeypatch.setattr(ward_watch, "sepsis_care", lambda an, until, kips=None:
                        {"A1": [], "B2": ["สั่งยาปฏิชีวนะ"], "C3": ["วินิจฉัย A41.9"]}[an])
    cases, diagnosed = ward_watch.suspected_cases(date(2026, 10, 27), date(2026, 10, 27), ["A1|2026-10-27"], NOW)
    assert diagnosed == 1 and [c["an"] for c in cases] == ["B2", "A1"]
    b, a = cases
    assert a["hits"] == 2 and a["days"] == 1 and a["first"]["vitals"]["temp"] == 38.6 and a["sent"]
    assert a["max_sos"] >= a["first"]["sos"] and a["dch_at"] is None and a["care"] == []
    assert b["dch_label"] == "กลับบ้าน" and b["pdx"] == "J18.9" and not b["sent"]
