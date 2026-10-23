import json
import os
import sys

import pytest

from werkzeug.security import generate_password_hash

import app as app_mod
import db
import login_guard
import moph_notify


@pytest.fixture
def client(fake_db, monkeypatch):
    monkeypatch.setattr(app_mod, "SETTINGS_PASSWORD", "")
    monkeypatch.setattr(app_mod, "SETTINGS_PASSWORD_HASH", "")
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


@pytest.mark.parametrize("attr, value", [
    ("SETTINGS_PASSWORD", "s3cret"),                                                  # แบบเดิม (รหัสตรง ๆ)
    ("SETTINGS_PASSWORD_HASH", generate_password_hash("s3cret", method="pbkdf2:sha256")),
])
def test_settings_password(client, monkeypatch, attr, value):
    monkeypatch.setattr(app_mod, attr, value)
    r = _get(client, "/settings", ip="10.0.0.5")
    assert r.status_code == 302 and "scope=settings" in r.headers["Location"]
    _post(client, "/unlock", {"password": "wrong", "scope": "settings"}, ip="10.0.0.5")
    assert _get(client, "/settings", ip="10.0.0.5").status_code == 302
    _post(client, "/unlock", {"password": "s3cret", "scope": "settings"}, ip="10.0.0.5")
    assert _get(client, "/settings", ip="10.0.0.5").status_code == 200
    # ตั้งรหัสแล้ว เครื่องเซิร์ฟเวอร์เองก็ต้องกรอกรหัส
    assert app_mod.app.test_client().get("/settings").status_code == 302


def test_view_password_does_not_open_settings(client, monkeypatch):
    """รหัสดู HN/AN แจกให้เจ้าหน้าที่หลายคน — ห้ามใช้เปิดหน้าตั้งค่าแจ้งเตือน"""
    monkeypatch.setattr(app_mod, "VIEW_PASSWORD_HASH", generate_password_hash("view", method="pbkdf2:sha256"))
    monkeypatch.setattr(app_mod, "SETTINGS_PASSWORD_HASH", generate_password_hash("admin", method="pbkdf2:sha256"))
    _post(client, "/unlock", {"password": "view", "scope": "settings"}, ip="10.0.0.5")
    assert _get(client, "/settings", ip="10.0.0.5").status_code == 302
    # ตั้งรหัสดู HN/AN อย่างเดียว ไม่ตั้งรหัสหน้าตั้งค่า = เปิดได้เฉพาะจากเครื่องเซิร์ฟเวอร์
    monkeypatch.setattr(app_mod, "SETTINGS_PASSWORD_HASH", "")
    assert _get(client, "/settings", ip="10.0.0.5").status_code == 403


def test_unlock_locks_out_after_repeated_failures(client, monkeypatch):
    monkeypatch.setattr(app_mod, "VIEW_PASSWORD_HASH", generate_password_hash("right", method="pbkdf2:sha256"))
    for _ in range(login_guard.PER_IP):
        _post(client, "/unlock", {"password": "wrong"}, ip="10.0.0.9")
    r = _post(client, "/unlock", {"password": "right"}, ip="10.0.0.9")     # รหัสถูกก็ยังต้องรอ
    assert r.status_code == 200 and "ลองใหม่" in r.get_data(as_text=True)
    # เครื่องอื่นยังใช้ได้
    assert _post(client, "/unlock", {"password": "right", "next": "/"}, ip="10.0.0.10").status_code == 302


def test_login_guard_window_and_global_limit(monkeypatch):
    now = 1_000_000.0
    for i in range(login_guard.PER_IP):
        login_guard.record_failure("a", now=now + i)
    assert login_guard.wait_seconds("a", now=now + 10) > 0
    assert login_guard.wait_seconds("b", now=now + 10) == 0
    assert login_guard.wait_seconds("a", now=now + login_guard.WINDOW + 10) == 0     # ครบเวลาแล้วกรอกได้
    monkeypatch.setattr(login_guard, "GLOBAL", 8)
    for i in range(8):
        login_guard.record_failure(f"ip{i}", now=now + 100)
    assert login_guard.wait_seconds("someone-else", now=now + 101) > 0              # เดาจากหลายเครื่อง
    login_guard.reset("a")


def test_security_headers(client):
    r = _get(client, "/")
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Cache-Control"] == "no-store"


def test_secret_key_persisted_and_shared(tmp_path, monkeypatch):
    monkeypatch.delenv("SEPSIS_SECRET_KEY")
    monkeypatch.setattr(app_mod, "SECRET_KEY_PATH", str(tmp_path / "data" / "secret_key"))
    first, second = app_mod._secret_key(), app_mod._secret_key()
    assert first == second and len(first) == 64
    if sys.platform != "win32":
        assert os.stat(app_mod.SECRET_KEY_PATH).st_mode & 0o777 == 0o600


def test_short_secret_key_rejected(monkeypatch):
    monkeypatch.setenv("SEPSIS_SECRET_KEY", "1234")
    with pytest.raises(RuntimeError, match="32"):
        app_mod._secret_key()


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
