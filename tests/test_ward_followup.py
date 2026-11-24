"""การ์ดตามผลหลังแจ้งเตือน SOS บนหอ (ward_watch.followup_result + scheduler._run_ward_followup)
และกติกาที่ใช้กับเคสบนหอ: นับเฉพาะ "โหลดน้ำ" · ถือว่าเจาะ H/C ก่อนให้ยา · time-zero = เวลาเตือน

ข้อมูลจำลองทั้งหมด ไม่ต่อฐาน HOSxP ไม่ส่ง LINE"""

import json
from datetime import datetime, timedelta

import pytest

import app as app_mod
import flex
import notify
import nurse_notes
import queries
import scheduler
import ward_watch

TZ = datetime(2026, 10, 27, 13, 0)                 # เวลาสัญญาณชีพเข้าเกณฑ์
DUE = timedelta(minutes=scheduler.BUNDLE_WINDOW_MIN + scheduler.GRACE_MIN)
AN = "690001234"


def _el(label, minutes=None):
    if minutes is None:
        return {"label": label, "at": None, "elapsed_min": None, "compliant": False,
                "source": None, "detail": None, "note": "ไม่มีข้อมูล"}
    return {"label": label, "at": (TZ + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M"),
            "elapsed_min": minutes, "compliant": minutes <= queries.BUNDLE_WINDOW_MIN,
            "source": "สั่ง", "detail": None, "note": None}


def _bundle(case, lactate=20, culture=25, antibiotic=40, iv_fluid=None):
    elements = {
        "lactate": _el("Lactate", lactate), "culture": _el("Hemoculture", culture),
        "antibiotic": _el("ยาปฏิชีวนะ", antibiotic), "iv_fluid": _el("IV Fluid", iv_fluid),
        "oxygen": _el("ออกซิเจน"), "urine": _el("Urine Output"),
    }
    auto = [elements[k] for k in ward_watch.AUTO_ELEMENTS]
    return {"disposition": None, "case_key": AN, "an": case.get("an"), "vn": "V1", "hn": "HN9",
            "ward": "01", "ward_name": "หอผู้ป่วยใน", "bed": "5", "icd10": None,
            "time_zero": case["tz_override"].strftime("%Y-%m-%d %H:%M"),
            "time_zero_mode": queries.TIME_ZERO_LABEL["ward_alert"],
            "bundle_window_min": queries.BUNDLE_WINDOW_MIN, "elements": elements,
            "culture_before_antibiotic": True, "auto_compliant": all(e["compliant"] for e in auto),
            "ssc": {"score": 3}, "data": {}}


@pytest.fixture
def fake_hosxp(monkeypatch):
    """แทน queries.find_sepsis_cases / bundle_status ที่ ward_watch เรียก — จดว่าส่งเคสอะไรไป"""
    seen = {"cases": [], "bundle": []}
    state = {"found": True, "kw": {}}

    def find(lookback_days=7, d1=None, d2=None, an=None):
        seen["cases"].append(an)
        return [{"an": an, "vn": "V1", "hn": "HN9", "case_key": an}] if state["found"] else []

    def bundle(case):
        seen["bundle"].append(case)
        return _bundle(case, **state["kw"])

    monkeypatch.setattr(queries, "find_sepsis_cases", find)
    monkeypatch.setattr(queries, "bundle_status", bundle)
    return seen, state


# ------------------------------------------------------------------ followup_result

def test_followup_result_counts_from_alert_time_and_keeps_four_auto_elements(fake_hosxp):
    seen, _ = fake_hosxp
    r = ward_watch.followup_result(AN, TZ)
    assert seen["cases"] == [AN]                                   # ดึงรายนี้ตรง ๆ ไม่ต้องมี ICD10
    case = seen["bundle"][0]
    assert case["tz_override"] == TZ and case["ward_onset"] is True
    assert list(r["elements"]) == list(ward_watch.AUTO_ELEMENTS)   # ตัดออกซิเจน/Urine ออก
    assert r["ssc"] is None
    assert r["icd10"] == "ยังไม่มีวินิจฉัย Sepsis"
    fluid = r["elements"]["iv_fluid"]
    assert fluid["label"] == "โหลดสารน้ำ (IV Fluid load)"
    assert fluid["note"] == "ไม่พบคำสั่งหรือบันทึกการโหลดน้ำหลังเวลาเตือน"
    assert r["elements"]["lactate"]["note"] is None                 # พบแล้ว ไม่เขียนทับ
    assert r["card_verdicts"] == ("ทำครบ 4 ข้อ", "ยังไม่ครบ 4 ข้อ")
    assert r["auto_compliant"] is False


def test_followup_result_none_when_patient_not_found(fake_hosxp):
    _, state = fake_hosxp
    state["found"] = False
    assert ward_watch.followup_result(AN, TZ) is None


def test_followup_card_uses_ward_wording_and_masks_patient(fake_hosxp):
    r = ward_watch.followup_result(AN, TZ)
    card = flex.bundle_result(r)
    text = json.dumps(card, ensure_ascii=False)
    assert "ผลตอบสนองหลังแจ้งเตือนหอผู้ป่วย" in text and "ยังไม่ครบ 4 ข้อ" in text
    assert "เวลาจากบันทึกพยาบาล KIPS" in text and "ยังไม่พบการดูแลครบภายใน 1 ชม." in text
    assert "ไม่พบคำสั่ง" in text
    assert "Sepsis Six Bundle" not in card["altText"] and "3/4" in card["altText"]
    assert AN not in text and "HN9" not in text
    msg = notify.format_bundle_message(r)
    assert msg.startswith("🔔 ผลตอบสนองหลังแจ้งเตือนหอผู้ป่วย")
    assert "ยังไม่ครบ 4 ข้อ" in msg and AN not in msg


def test_followup_card_all_done(fake_hosxp):
    _, state = fake_hosxp
    state["kw"] = {"iv_fluid": 30}
    r = ward_watch.followup_result(AN, TZ)
    assert r["auto_compliant"] is True
    assert "ทำครบ 4 ข้อ" in json.dumps(flex.bundle_result(r), ensure_ascii=False)


def test_er_card_wording_unchanged():
    """การ์ด ER ปกติ (ไม่มี card_*) ยังใช้ถ้อยคำเดิม"""
    r = dict(_bundle({"an": AN, "tz_override": TZ}, iv_fluid=30), ssc=None)
    text = json.dumps(flex.bundle_result(r), ensure_ascii=False)
    assert "ผล Sepsis Six Bundle" in text and "ผ่านเกณฑ์" in text
    assert "เวลาจากบันทึกพยาบาล ER ก่อน" in text
    assert notify.format_bundle_message(r).startswith("🔔 ผล Sepsis Six Bundle")


def test_time_zero_override_for_ward_case():
    assert queries.time_zero_info({"tz_override": TZ}) == (TZ, "ward_alert")
    assert "ward_alert" in queries.TIME_ZERO_LABEL


# ------------------------------------------------------------------ scheduler

@pytest.fixture
def sched(monkeypatch, tmp_path, fake_hosxp):
    monkeypatch.setattr(scheduler, "DB_PATH", str(tmp_path / "s.sqlite"))
    sent = []
    monkeypatch.setattr(scheduler, "_dispatch", lambda fm, text: sent.append((fm, text)))
    conn = scheduler._conn()
    conn.execute("insert into ward_followup (key, an, tz, bed) values (?,?,?,?)",
                 (f"{AN}|2026-10-27", AN, TZ.isoformat(), "7"))
    conn.commit()
    return conn, sent


def _done(conn):
    return conn.execute("select done, sent_at from ward_followup").fetchone()


def test_followup_waits_until_due_then_sends_once(sched):
    conn, sent = sched
    scheduler._run_ward_followup(conn, TZ + DUE - timedelta(minutes=1))
    assert sent == [] and _done(conn)[0] == 0
    now = TZ + DUE
    scheduler._run_ward_followup(conn, now)
    scheduler._run_ward_followup(conn, now + timedelta(minutes=5))      # รอบถัดไป ไม่ส่งซ้ำ
    assert len(sent) == 1
    assert _done(conn) == (1, now.isoformat())
    fm, text = sent[0]
    assert "เตียง: 7" in text                                           # เตียงตอนเตือน
    assert AN not in text and AN not in json.dumps(fm, ensure_ascii=False)


def test_followup_skips_stale_alerts(sched):
    conn, sent = sched
    scheduler._run_ward_followup(conn, TZ + ward_watch.FOLLOWUP_MAX_AGE + timedelta(minutes=1))
    assert sent == [] and _done(conn)[0] == 2


def test_followup_marks_missing_patient(sched, fake_hosxp):
    conn, sent = sched
    fake_hosxp[1]["found"] = False
    scheduler._run_ward_followup(conn, TZ + DUE)
    assert sent == [] and _done(conn)[0] == 3


def test_followup_retries_after_send_failure(sched, monkeypatch):
    conn, sent = sched

    def boom(fm, text):
        raise RuntimeError("network")

    monkeypatch.setattr(scheduler, "_dispatch", boom)
    scheduler._run_ward_followup(conn, TZ + DUE)
    assert _done(conn)[0] == 0
    monkeypatch.setattr(scheduler, "_dispatch", lambda fm, text: sent.append(text))
    scheduler._run_ward_followup(conn, TZ + DUE + timedelta(minutes=5))
    assert len(sent) == 1 and _done(conn)[0] == 1


def test_followup_disabled(sched, monkeypatch):
    conn, sent = sched
    monkeypatch.setattr(ward_watch, "FOLLOWUP", False)
    scheduler._run_ward_followup(conn, TZ + DUE)
    assert sent == [] and _done(conn)[0] == 0


def test_ward_alert_queues_followup(monkeypatch, tmp_path):
    monkeypatch.setattr(scheduler, "DB_PATH", str(tmp_path / "s.sqlite"))
    alert = {"key": f"{AN}|2026-10-27", "an": AN, "at": TZ, "bed": "5", "sos": 8}
    monkeypatch.setattr(ward_watch, "find_ward_alerts", lambda now: [alert])
    monkeypatch.setattr(flex, "ward_alert", lambda a: {})
    monkeypatch.setattr(notify, "format_ward_alert_message", lambda a: "")
    monkeypatch.setattr(scheduler, "_dispatch", lambda fm, text: None)
    conn = scheduler._conn()
    scheduler._run_ward(conn, TZ)
    scheduler._run_ward(conn, TZ + timedelta(minutes=5))
    rows = conn.execute("select key, an, tz, bed, done from ward_followup").fetchall()
    assert rows == [(f"{AN}|2026-10-27", AN, TZ.isoformat(), "5", 0)]


def test_failed_ward_alert_does_not_queue_followup(monkeypatch, tmp_path):
    monkeypatch.setattr(scheduler, "DB_PATH", str(tmp_path / "s.sqlite"))
    alert = {"key": f"{AN}|2026-10-27", "an": AN, "at": TZ, "bed": "5", "sos": 8}
    monkeypatch.setattr(ward_watch, "find_ward_alerts", lambda now: [alert])
    monkeypatch.setattr(flex, "ward_alert", lambda a: {})
    monkeypatch.setattr(notify, "format_ward_alert_message", lambda a: "")

    def boom(fm, text):
        raise RuntimeError("network")

    monkeypatch.setattr(scheduler, "_dispatch", boom)
    conn = scheduler._conn()
    scheduler._run_ward(conn, TZ)
    assert conn.execute("select count(*) from ward_followup").fetchone()[0] == 0


# ------------------------------------------------------------------ โหลดน้ำบนหอ

@pytest.mark.parametrize("text, hit", [
    ("NSS 500 ml iv load", True), ("0.9% NaCl bolus 500 cc", True), ("free flow 300 ml", True),
    ("โหลด NSS 500 ml", True), ("loading dose", True),
    ("volume overload", False), ("fluid overloaded", False), ("NSS rate 80 ml/hr", False),
])
def test_load_word(text, hit):
    assert bool(nurse_notes.LOAD_WORD.search(text)) is hit


def test_ward_fluid_counts_only_load():
    daily = "0.9%NaCl 1000 ml iv drip 80 cc/hr at 13.20 น."
    assert nurse_notes.parse(daily, TZ)["iv_fluid"]["dt"] == datetime(2026, 10, 27, 13, 20)
    assert "iv_fluid" not in nurse_notes.parse(daily, TZ, fluid_load_only=True)
    load = "0.9%NaCl iv load 500 ml at 13.25 น."
    assert nurse_notes.parse(load, TZ, fluid_load_only=True)["iv_fluid"]["dt"] == datetime(2026, 10, 27, 13, 25)


def test_ward_fluid_order_needs_load_usage(monkeypatch):
    rows = [{"t": datetime(2026, 10, 27, 13, 10), "usage_text": "IV drip 80 cc/hr"},
            {"t": datetime(2026, 10, 27, 13, 30), "usage_text": "IV load 500 ml"}]
    calls = []

    def fetch(sql, params=None):
        calls.append(params)
        return rows

    monkeypatch.setattr(queries, "fetch", fetch)
    got = queries._ward_fluid_load_first(AN, {"F1"}, TZ, TZ + timedelta(hours=3))
    assert got == datetime(2026, 10, 27, 13, 30)
    assert calls[0]["an"] == AN and calls[0]["icodes"] == ["F1"]
    assert queries._ward_fluid_load_first(None, {"F1"}, TZ, TZ) is None
    monkeypatch.setattr(queries, "fetch", lambda sql, params=None: rows[:1])
    assert queries._ward_fluid_load_first(AN, {"F1"}, TZ, TZ + timedelta(hours=3)) is None


# ------------------------------------------------------------------ ถือว่าเจาะ H/C ก่อนให้ยา

ATB = (datetime(2026, 10, 27, 13, 30), "สั่ง", None)


def test_culture_ordered_after_atb_is_assumed_before(monkeypatch):
    monkeypatch.setattr(queries, "ASSUME_CULTURE_BEFORE_ATB", True)
    got = queries.assume_culture_first((datetime(2026, 10, 27, 13, 45), "สั่ง", None), ATB)
    assert got[0] == ATB[0] and got[1].startswith(queries.ASSUMED_CULTURE_SOURCE) and "13:45" in got[1]


def test_culture_with_nurse_time_is_kept(monkeypatch):
    monkeypatch.setattr(queries, "ASSUME_CULTURE_BEFORE_ATB", True)
    written = (datetime(2026, 10, 27, 13, 45), "บันทึกพยาบาล", "H/C 13.45 น.")
    assert queries.assume_culture_first(written, ATB) == written


def test_culture_untimed_note_uses_atb_time(monkeypatch):
    monkeypatch.setattr(queries, "ASSUME_CULTURE_BEFORE_ATB", True)
    none = (None, None, None)
    assert queries.assume_culture_first(none, ATB) == none
    got = queries.assume_culture_first(none, ATB, noted_untimed=True)
    assert got[0] == ATB[0] and "ไม่ระบุเวลา" in got[1]


def test_culture_rule_off_or_no_atb(monkeypatch):
    late = (datetime(2026, 10, 27, 13, 45), "สั่ง", None)
    monkeypatch.setattr(queries, "ASSUME_CULTURE_BEFORE_ATB", False)
    assert queries.assume_culture_first(late, ATB) == late
    monkeypatch.setattr(queries, "ASSUME_CULTURE_BEFORE_ATB", True)
    assert queries.assume_culture_first(late, (None, None, None)) == late
    early = (datetime(2026, 10, 27, 13, 10), "สั่ง", None)
    assert queries.assume_culture_first(early, ATB) == early


# ------------------------------------------------------------------ next หลัง login ใต้ /sepsis

@pytest.mark.parametrize("nxt, want", [
    ("/cases", "/sepsis/cases"), ("/sepsis/cases", "/sepsis/cases"), ("/sepsis", "/sepsis"),
    ("/sepsis?x=1", "/sepsis?x=1"), ("//evil.example", None), ("https://evil.example", None),
])
def test_safe_next_adds_prefix_but_blocks_offsite(nxt, want):
    with app_mod.app.test_request_context("/login", query_string={"next": nxt},
                                          environ_overrides={"SCRIPT_NAME": "/sepsis"}):
        got = app_mod._safe_next()
        assert got == (want or app_mod.url_for("dashboard"))
        assert got.startswith("/sepsis") and not got.startswith("//")
