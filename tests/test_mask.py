"""ซ่อน HN/AN — การ์ด LINE เป็น xxx เสมอ, แดชบอร์ดต้องกรอกรหัสก่อน"""

import json

import flask
import pytest
from werkzeug.security import generate_password_hash

import app as app_mod
import flex
import notify

CASE = {"an": "690012345", "vn": "691016194632", "case_key": "690012345", "hn": "0123456"}


@pytest.fixture
def client(fake_db, monkeypatch):
    monkeypatch.setattr(app_mod, "VIEW_PASSWORD_HASH", generate_password_hash("pw-test", method="pbkdf2:sha256"))
    monkeypatch.setattr(app_mod, "SETTINGS_PASSWORD", "")
    monkeypatch.setattr(app_mod, "SETTINGS_PASSWORD_HASH",
                        generate_password_hash("settings-pw", method="pbkdf2:sha256"))
    monkeypatch.setattr(app_mod.queries, "build", lambda d1, d2: [dict(CASE)])
    empty = app_mod.queries.summary([])
    monkeypatch.setattr(app_mod.queries, "summary", lambda r: empty)
    monkeypatch.setattr(app_mod, "render_template",
                        lambda name, **kw: name if name == "dashboard.html" else flask.render_template(name, **kw))
    monkeypatch.setattr(app_mod.time, "sleep", lambda s: None)
    app_mod.app.config["TESTING"] = True
    return app_mod.app.test_client()


def test_api_masked_until_unlocked(client):
    assert client.get("/api/data.json").json[0]["hn"] == "xxx"
    assert client.post("/unlock", data={"password": "ผิด"}).status_code == 200
    assert client.get("/api/data.json").json[0]["hn"] == "xxx"
    assert client.post("/unlock", data={"password": "pw-test", "next": "/"}).status_code == 302
    assert client.get("/api/data.json").json[0] == CASE
    client.post("/lock")
    assert client.get("/api/data.json").json[0]["an"] == "xxx"


def test_unlock_ignores_external_next(client):
    r = client.post("/unlock", data={"password": "pw-test", "next": "//evil.example/"})
    assert r.headers["Location"] == "/"


def test_settings_asks_password_every_visit(client):
    r = client.get("/settings", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 302 and "scope=settings" in r.headers["Location"]
    client.post("/unlock", data={"password": "pw-test"})                 # ปลดดู HN/AN อย่างเดียว
    assert client.get("/settings").status_code == 302                   # ยังเข้าหน้าตั้งค่าไม่ได้
    client.post("/unlock", data={"password": "pw-test", "scope": "settings"})         # รหัสดู HN ใช้ไม่ได้
    assert client.get("/settings").status_code == 302
    client.post("/unlock", data={"password": "settings-pw", "scope": "settings"})
    assert client.get("/settings", environ_base={"REMOTE_ADDR": "10.0.0.5"}).status_code == 200
    client.get("/")                                                     # กลับแดชบอร์ด
    assert client.get("/settings").status_code == 302                   # เข้าใหม่ต้องกรอกอีก


def test_settings_unlock_does_not_reveal_hn(client):
    client.post("/unlock", data={"password": "settings-pw", "scope": "settings"})
    assert client.get("/api/data.json").json[0]["hn"] == "xxx"


def test_line_messages_have_no_real_ids():
    text = json.dumps(flex.sample_messages(60), ensure_ascii=False)
    assert "TEST000001" not in text and "0000000" not in text and '"xxx"' in text
    assert "0123456" not in notify.format_new_case_message(dict(CASE, ward_name="ER", icd10_list="A41.9"))


SUSPECT = {"an": "690012345", "hn": "0123456", "ward_name": "หอผู้ป่วยใน", "bed": "5",
           "first_at": __import__("datetime").datetime(2026, 10, 27, 8, 0), "max_sos": 8, "hits": 2, "days": 1,
           "first": {"sos": 6, "sos_parts": {"T": 2, "BP": 2, "HR": 1, "RR": 1},
                     "vitals": {"temp": 39.0, "sbp": 85, "dbp": 50, "hr": 115, "rr": 24}},
           "dch_at": None, "dch_label": None, "care": [], "sent": True, "pdx": "J18.9", "pdx_name": "Pneumonia"}


def test_suspected_page_masks_and_renders(client, monkeypatch):
    seen = {}

    def fake(d1, d2, sent):
        seen["range"] = (d1, d2)
        return [dict(SUSPECT), dict(SUSPECT, an="690099999", care=["สั่งยาปฏิชีวนะ"], dch_at=SUSPECT["first_at"],
                                    dch_label="กลับบ้าน", sent=False)], 3

    monkeypatch.setattr(app_mod.ward_watch, "suspected_cases", fake)
    html = client.get("/suspected?d1=2026-01-01&d2=2026-10-27").get_data(as_text=True)
    assert (seen["range"][1] - seen["range"][0]).days == app_mod.SUSPECT_MAX_DAYS - 1   # ตัดช่วงยาว
    assert "690012345" not in html and "0123456" not in html and "AN xxx" in html
    assert "ยังไม่พบการดูแล" in html and "สั่งยาปฏิชีวนะ" in html and "ส่งการ์ดหอผู้ป่วยแล้ว" in html
    assert "J18.9" in html and "กลับบ้าน" in html and "ไม่เกิน 92 วัน" in html
    client.post("/unlock", data={"password": "pw-test"})
    assert "AN 690012345" in client.get("/suspected").get_data(as_text=True)
