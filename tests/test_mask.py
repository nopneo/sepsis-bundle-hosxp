"""ซ่อน HN/AN — การ์ด LINE เป็น xxx เสมอ, แดชบอร์ดต้องกรอกรหัสก่อน"""

import json

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
    monkeypatch.setattr(app_mod.queries, "build", lambda d1, d2: [dict(CASE)])
    monkeypatch.setattr(app_mod.queries, "summary", lambda r: None)
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


def test_settings_needs_unlock_even_from_server(client):
    r = client.get("/settings", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 302 and "/unlock" in r.headers["Location"]
    client.post("/unlock", data={"password": "pw-test"})
    assert client.get("/settings", environ_base={"REMOTE_ADDR": "10.0.0.5"}).status_code == 200


def test_line_messages_have_no_real_ids():
    text = json.dumps(flex.sample_messages(60), ensure_ascii=False)
    assert "TEST000001" not in text and "0000000" not in text and '"xxx"' in text
    assert "0123456" not in notify.format_new_case_message(dict(CASE, ward_name="ER", icd10_list="A41.9"))
