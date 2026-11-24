"""IP จริงหลัง Apache/Cloudflare (ตัวนับรหัสผิดไม่รวมทุกคนเป็น 127.0.0.1) · หน้าตั้งค่าเฉพาะใน รพ. ·
header จาก proxy เชื่อเฉพาะเครื่องเดียวกัน · cookie Secure ทาง https"""
import pytest
from werkzeug.security import generate_password_hash

import app as app_mod
import login_guard

CF_EDGE = "172.70.1.1"
HOSP = "203.0.113.10"


def _cf(ip, **extra):
    h = {"X-Forwarded-For": f"{ip}, {CF_EDGE}", "CF-Connecting-IP": ip, "X-Forwarded-Proto": "https"}
    h.update(extra)
    return {"headers": h, "environ_base": {"REMOTE_ADDR": "127.0.0.1"}}


LAN = {"headers": {"X-Forwarded-For": "192.168.11.25", "X-Forwarded-Proto": "https"},
       "environ_base": {"REMOTE_ADDR": "127.0.0.1"}}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_mod, "VIEW_PASSWORD_HASH", generate_password_hash("view-pw", method="pbkdf2:sha256:1000"))
    monkeypatch.setattr(app_mod, "SETTINGS_PASSWORD_HASH", generate_password_hash("set-pw", method="pbkdf2:sha256:1000"))
    monkeypatch.setattr(app_mod, "SETTINGS_PASSWORD", "")
    monkeypatch.setattr(app_mod, "LAN_NETS", app_mod._nets(HOSP))
    monkeypatch.delenv("SEPSIS_SETTINGS_LAN_ONLY", raising=False)
    app_mod.app.config["TESTING"] = True
    return app_mod.app.test_client()


def test_client_ip_behind_cloudflare_and_lan():
    with app_mod.app.test_request_context("/", **_cf("1.2.3.4")):
        assert app_mod.client_ip() == "1.2.3.4"
    with app_mod.app.test_request_context("/", **LAN):
        assert app_mod.client_ip() == "192.168.11.25"
    with app_mod.app.test_request_context("/", headers={"X-Forwarded-For": "9.9.9.9", "CF-Connecting-IP": "1.1.1.1"},
                                          environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        assert app_mod.client_ip() == "9.9.9.9"                       # CF header จากเครื่องที่ไม่ใช่ Cloudflare ไม่เชื่อ
    with app_mod.app.test_request_context("/", headers={"X-Forwarded-For": "192.168.1.1"},
                                          environ_base={"REMOTE_ADDR": "8.8.8.8"}):
        assert app_mod.client_ip() == "8.8.8.8"                       # ต่อตรงจากเครื่องอื่น ไม่เชื่อ XFF


def test_one_attacker_does_not_lock_out_everyone(client):
    for _ in range(login_guard.PER_IP):
        client.post("/unlock", data={"password": "wrong"}, **_cf("1.2.3.4"))
    blocked = client.post("/unlock", data={"password": "view-pw"}, **_cf("1.2.3.4"))
    assert "ลองใหม่" in blocked.get_data(as_text=True)
    ok = client.post("/unlock", data={"password": "view-pw"}, **_cf("5.6.7.8"))
    assert ok.status_code == 302                                      # เจ้าหน้าที่อีกคนยังเข้าได้


@pytest.mark.parametrize("path", ["/settings", "/unlock?scope=settings"])
def test_settings_blocked_from_internet(client, path):
    r = client.get(path, **_cf("1.2.3.4"))
    assert r.status_code == 403 and "เฉพาะเครื่องในโรงพยาบาล" in r.get_data(as_text=True)


def test_settings_unlock_post_blocked_from_internet(client):
    r = client.post("/unlock", data={"scope": "settings", "password": "set-pw"}, **_cf("1.2.3.4"))
    assert r.status_code == 403


def test_settings_allowed_from_lan_and_hospital_ip(client):
    assert client.get("/settings", **LAN).status_code == 302               # ไปหน้ากรอกรหัส
    assert client.get("/unlock?scope=settings", **LAN).status_code == 200
    assert client.get("/unlock?scope=settings", **_cf(HOSP)).status_code == 200


def test_view_unlock_still_open_from_internet(client):
    assert client.get("/unlock", **_cf("1.2.3.4")).status_code == 200


def test_settings_opt_out(client, monkeypatch):
    monkeypatch.setenv("SEPSIS_SETTINGS_LAN_ONLY", "0")
    assert client.get("/unlock?scope=settings", **_cf("1.2.3.4")).status_code == 200


def test_server_itself_still_allowed(client):
    assert client.get("/unlock?scope=settings", environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 200


@pytest.mark.parametrize("ip, lan", [("192.168.1.1", True), ("10.0.0.1", True), ("198.18.0.1", False),
                                     ("203.0.113.7", False), ("8.8.8.8", False), ("::ffff:192.168.1.1", True)])
def test_private_ranges_explicit(ip, lan):
    with app_mod.app.test_request_context("/", environ_base={"REMOTE_ADDR": ip}):
        assert app_mod.from_lan() is lan


def test_cookie_secure_over_https(client):
    r = client.post("/unlock", data={"password": "view-pw"}, **_cf("5.6.7.9"))
    assert "Secure" in r.headers.get("Set-Cookie", "")


def test_proto_from_untrusted_peer_ignored(client):
    r = client.post("/unlock", data={"password": "view-pw"}, headers={"X-Forwarded-Proto": "https"},
                    environ_base={"REMOTE_ADDR": "192.168.1.7"})
    assert "Secure" not in r.headers.get("Set-Cookie", "")


def test_prefix_only_from_trusted_proxy(client):
    html = client.get("/unlock", headers={"X-Forwarded-Prefix": "/evil"},
                      environ_base={"REMOTE_ADDR": "192.168.1.5"}).get_data(as_text=True)
    assert "/evil/" not in html
    html = client.get("/unlock", headers={"X-Forwarded-Prefix": "/sepsis"},
                      environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_data(as_text=True)
    assert "/sepsis/" in html
    html = client.get("/unlock", headers={"X-Forwarded-Prefix": "//evil.example"},
                      environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_data(as_text=True)
    assert "evil.example" not in html
