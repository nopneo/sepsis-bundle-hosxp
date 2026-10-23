"""วิซาร์ดตั้งค่า hosxp_alert (install_alert.py wizard) — ฟังก์ชันล้วนและ smoke test ของ http.server

ไม่ทดสอบ flow ที่ต้องต่อฐาน HOSxP จริง (connect/select/preview/install) — ต้องทดสอบด้วยมือกับฐานทดสอบ
ตามตารางใน hosxp_alert/README.md เหมือนคำสั่ง install เดิม
"""

import http.client
import os
import sys
import threading
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hosxp_alert"))
import install_alert  # noqa: E402


def test_pymysql_connect_rejects_thai_password():
    with pytest.raises(ValueError, match="อักษรไทย"):
        install_alert.pymysql_connect("127.0.0.1", 3306, "u", "รหัสผ่าน", "hos")


def test_install_summary_lines_mentions_lab_codes_when_given():
    args = types.SimpleNamespace(wbc_items="35", band_items="2015", spo2_alert=92, mews_alert=5)
    lines = install_alert.install_summary_lines(args, cols={"o2sat"}, avpu=True, er_gcs=False)
    joined = "\n".join(lines)
    assert "lab 35" in joined
    assert "lab 2015" in joined
    assert "AVPU" in joined
    assert "SpO2 < 92%" in joined
    assert "MEWS >= 5" in joined


def test_install_summary_lines_notes_missing_wbc_items():
    args = types.SimpleNamespace(wbc_items="", band_items="", spo2_alert=0, mews_alert=0)
    lines = install_alert.install_summary_lines(args, cols=set(), avpu=False, er_gcs=False)
    joined = "\n".join(lines)
    assert "ไม่ได้ระบุ --wbc-items" in joined
    assert "ไม่เตือนแยก" in joined


def test_build_wizard_args_joins_checkbox_lists():
    install_alert._WIZ.clear()
    install_alert._WIZ.update(host="10.0.0.1", port=3306, db="hos_test", user="sepsis_deploy")
    form = {"er_deps": ["011", "012"], "wbc_items": ["35"], "band_items": ["2015"],
            "mews_alert": ["7"], "spo2_alert": ["90"], "skip_forms": ["frmLabOrder"], "replace": ["1"]}
    args = install_alert._build_wizard_args(form)
    assert args.host == "10.0.0.1" and args.db == "hos_test"
    assert args.er_deps == "011,012"
    assert args.wbc_items == "35" and args.band_items == "2015"
    assert args.mews_alert == 7 and args.spo2_alert == 90
    assert args.skip_forms == "frmLabOrder"
    assert args.replace is True
    assert args.yes is True
    install_alert._WIZ.clear()


def test_build_wizard_args_defaults_when_nothing_checked():
    install_alert._WIZ.clear()
    install_alert._WIZ.update(host="10.0.0.1", port=3306, db="hos_test", user="sepsis_deploy")
    args = install_alert._build_wizard_args({})
    assert args.er_deps == "" and args.wbc_items == "" and args.band_items == ""
    assert args.mews_alert == 5 and args.spo2_alert == 92
    assert args.skip_forms == install_alert.SKIP_FORMS
    assert args.replace is False
    install_alert._WIZ.clear()


@pytest.fixture
def wizard_server():
    server = install_alert.HTTPServer(("127.0.0.1", 0), install_alert.WizardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        install_alert._WIZ.clear()


def test_wizard_serves_connect_form(wizard_server):
    conn = http.client.HTTPConnection("127.0.0.1", wizard_server.server_port, timeout=5)
    conn.request("GET", "/")
    resp = conn.getresponse()
    body = resp.read().decode("utf-8")
    assert resp.status == 200
    assert 'action="/connect"' in body
    assert 'name="password"' in body
    conn.close()


def test_wizard_unknown_path_is_404(wizard_server):
    conn = http.client.HTTPConnection("127.0.0.1", wizard_server.server_port, timeout=5)
    conn.request("GET", "/not-a-page")
    resp = conn.getresponse()
    resp.read()
    assert resp.status == 404
    conn.close()


def test_wizard_connect_failure_shows_friendly_error_not_crash(wizard_server):
    # พอร์ต 1 ไม่มีอะไรฟัง — ถูกปฏิเสธทันที (เร็วกว่ารอ connect_timeout กับ host ที่ไม่มีจริง)
    body = "host=127.0.0.1&port=1&db=hos&user=u&password=p"
    conn = http.client.HTTPConnection("127.0.0.1", wizard_server.server_port, timeout=10)
    conn.request("POST", "/connect", body=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"})
    resp = conn.getresponse()
    text = resp.read().decode("utf-8")
    assert resp.status == 200
    assert "เชื่อมต่อไม่ได้" in text
    conn.close()
