import json
import os
import sys

import pytest

import app as app_mod
import db
import moph_notify


@pytest.fixture
def client(fake_db, monkeypatch):
    monkeypatch.setattr(app_mod, "SETTINGS_PASSWORD", "")
    monkeypatch.setattr(app_mod, "VIEW_PASSWORD_HASH", "")
    app_mod.app.config["TESTING"] = True
    return app_mod.app.test_client()


def _get(client, url, ip="127.0.0.1", **kw):
    return client.get(url, environ_base={"REMOTE_ADDR": ip}, **kw)


def _post(client, url, data, ip="127.0.0.1", **kw):
    return client.post(url, data=data, environ_base={"REMOTE_ADDR": ip}, **kw)


@pytest.mark.parametrize("url", ["/", "/?d1=2026-10-01&d2=2026-10-15", "/?days=30", "/api/data.json", "/healthz"])
def test_pages_render_with_empty_db(client, url):
    assert _get(client, url, ip="10.0.0.5").status_code == 200


# ------------------------------------------------------------ หน้า /settings

def test_settings_open_from_server_itself(client):
    assert _get(client, "/settings").status_code == 200


def test_settings_blocked_from_lan_without_password(client):
    assert _get(client, "/settings", ip="10.0.0.5").status_code == 403


def test_settings_blocked_through_proxy(client):
    r = _get(client, "/settings", headers={"X-Forwarded-For": "1.2.3.4"})
    assert r.status_code == 403


def test_settings_password(client, monkeypatch):
    monkeypatch.setattr(app_mod, "SETTINGS_PASSWORD", "s3cret")
    assert _get(client, "/settings", ip="10.0.0.5").status_code == 401
    assert _get(client, "/settings", ip="10.0.0.5", auth=("x", "wrong")).status_code == 401
    assert _get(client, "/settings", ip="10.0.0.5", auth=("admin", "s3cret")).status_code == 200
    # ตั้งรหัสแล้ว เครื่องเซิร์ฟเวอร์เองก็ต้องใส่รหัส
    assert _get(client, "/settings").status_code == 401


def test_settings_rejects_cross_site_post(client):
    r = _post(client, "/settings", {"client_key": "k", "secret_key": "s"},
              headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert not os.path.exists(moph_notify.SETTINGS_PATH)


def test_settings_rejects_foreign_base_url(client):
    r = _post(client, "/settings", {"base_url": "https://evil.example", "client_key": "k", "secret_key": "s"},
              headers={"Origin": "http://localhost"})
    assert r.status_code == 200 and "moph.go.th" in r.get_data(as_text=True)
    assert not os.path.exists(moph_notify.SETTINGS_PATH)


def test_settings_save_keeps_secret_when_blank(client):
    _post(client, "/settings", {"client_key": "k1", "secret_key": "s1"})
    _post(client, "/settings", {"client_key": "k2", "secret_key": ""})
    saved = moph_notify.load_settings()
    assert saved["client_key"] == "k2" and saved["secret_key"] == "s1"
    assert saved["base_url"] == moph_notify.DEFAULT_BASE_URL


# --------------------------------------------------------- moph_notify

@pytest.mark.parametrize("url, ok", [
    ("https://morpromt2f.moph.go.th", True),
    ("https://moph.go.th/x", True),
    ("http://morpromt2f.moph.go.th", False),          # ต้องเป็น https
    ("https://evil.example", False),
    ("https://moph.go.th.evil.example", False),
    ("https://evilmoph.go.th", False),
    ("https://user@evil.example/?x=.moph.go.th", False),
    ("not a url", False),
])
def test_allowed_base_url(url, ok):
    assert moph_notify.allowed_base_url(url) is ok


def test_corrupt_settings_file_falls_back_to_defaults():
    os.makedirs(os.path.dirname(moph_notify.SETTINGS_PATH), exist_ok=True)
    with open(moph_notify.SETTINGS_PATH, "w", encoding="utf-8") as f:
        f.write('{"client_key": "abc", "secr')         # เขียนค้างครึ่งไฟล์
    assert moph_notify.load_settings() == moph_notify.DEFAULT_SETTINGS


def test_save_settings_is_atomic_and_private():
    moph_notify.save_settings({"client_key": "k", "secret_key": "s"})
    folder = os.path.dirname(moph_notify.SETTINGS_PATH)
    assert os.listdir(folder) == ["moph_settings.json"]          # ไม่มีไฟล์ชั่วคราวค้าง
    assert json.load(open(moph_notify.SETTINGS_PATH, encoding="utf-8"))["secret_key"] == "s"
    if sys.platform != "win32":
        assert os.stat(moph_notify.SETTINGS_PATH).st_mode & 0o777 == 0o600


def test_send_refuses_foreign_url():
    with pytest.raises(RuntimeError, match="moph.go.th"):
        moph_notify.send_messages([], settings={"base_url": "https://evil.example",
                                                "client_key": "k", "secret_key": "s"})


# ------------------------------------------------------------------ db

def test_bind_escapes_values():
    sql = db._bind("select 1 where a = %(a)s and b in %(b)s and c like %(c)s",
                   {"a": "x' or '1'='1", "b": ["1", "2"], "c": "A40%"})
    assert sql == r"select 1 where a = 'x\' or \'1\'=\'1' and b in ('1','2') and c like 'A40%'"
    assert db._bind("select 'A40%'", None) == "select 'A40%'"
