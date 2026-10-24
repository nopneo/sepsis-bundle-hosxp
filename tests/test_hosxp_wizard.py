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


class FakeCur:
    """cursor ปลอมสำหรับหน้าวิซาร์ด — ตอบตามคำใน SQL · บันทึกคำสั่งที่ execute ไว้ตรวจ"""

    def __init__(self, deps, allow=None, script="// SepsisNoticeForm"):
        self.deps, self.allow, self.script, self.log, self._rows = deps, allow, script, [], []

    def execute(self, sql, params=None):
        self.log.append((sql, params))
        s = sql.lower()
        if "from er_regist" in s:
            self._rows = self.deps
        elif "information_schema" in s and "depcode_vn_script_allow" in str(params) + s:
            self._rows = [(1,)] if self.allow is not None else []
        elif "from depcode_vn_script_allow" in s:
            self._rows = [(d,) for d in (self.allow or [])]
        elif "from sys_app_script" in s:
            self._rows = [(1, self.script, "md5")]
        elif s.startswith("insert"):
            self._rows = []
            return 1
        else:
            self._rows = []
        return len(self._rows)

    def fetchall(self):
        return self._rows


DEPS = [("011", "ER", 1666), ("035", "Exit Care", 49), ("046", "หัตถการ OPD", 16),
        ("010", "OPD จุดซักประวัติ", 13), ("013", "ซักประวัติ ARI", 11)]


def test_recommend_er_deps_picks_er_only():
    assert install_alert.recommend_er_deps(DEPS) == {"011"}
    # ไม่มีชื่อ ER แต่แผนกแรกเป็นส่วนใหญ่ = แนะนำแผนกแรก
    assert install_alert.recommend_er_deps([("099", "แผนก X", 90), ("010", "OPD", 10)]) == {"099"}
    assert install_alert.recommend_er_deps([("099", "แผนก X", 40), ("098", "แผนก Y", 60)]) == {"098"}


def test_select_page_explains_two_windows_and_prechecks_er():
    page = install_alert._select_page(FakeCur(DEPS, allow=["002", "011"]),
                                      {"user": "u", "host": "h", "db": "hos_test"})
    assert "SEPSIS ALERT (ER)" in page and "ส่ง ER ทันที" in page          # อธิบายหน้าต่าง 2 แบบ
    assert 'value="011" checked' in page and 'value="010" checked' not in page
    assert "แนะนำ" in page


def test_select_page_warns_er_not_in_allow_list():
    page = install_alert._select_page(FakeCur(DEPS, allow=["002", "003"]),
                                      {"user": "u", "host": "h", "db": "hos"})
    assert "depcode_vn_script_allow" in page and 'name="add_allow" value="1" checked' in page


def test_target_banner_marks_live_db():
    live = install_alert.target_banner("10.0.0.2", "hos")
    test = install_alert.target_banner("10.0.0.3", "hos_test")
    assert "10.0.0.2" in live and "hos" in live and 'class="err' in live and "ฐานจริง" in live
    assert 'class="warn' in test and "ฐานทดสอบ" in test


def test_er_dep_warnings_flag_screening_points():
    names = {d: n for d, n, _ in DEPS}
    w = install_alert.er_dep_warnings(["011", "010"], names)
    assert any("010" in x for x in w)
    assert install_alert.er_dep_warnings(["011"], names) == []


def test_add_allow_deps_inserts_only_missing():
    cur = FakeCur(DEPS, allow=["002", "011"])
    added = install_alert.add_allow_deps(cur, ["011", "012"])
    assert added == ["012"]
    inserts = [p for s, p in cur.log if s.lower().startswith("insert")]
    assert inserts == [("012",)]


def test_preview_page_shows_target_and_window_split():
    args = types.SimpleNamespace(host="10.0.0.2", db="hos", er_deps="011,010", wbc_items="", band_items="",
                                 mews_alert=5, spo2_alert=92, news2_alert=5, skip_forms="", replace=False,
                                 add_allow=True)
    page = install_alert._preview_page(args, ["x"], "ของระบบนี้", False, False, {"est": 90, "ge5": 90},
                                       {d: n for d, n, _ in DEPS})
    assert "ฐานจริง" in page and "10.0.0.2 / hos" in page                 # บอกฐานที่จะเขียนก่อนกดยืนยัน
    assert "011 ER" in page and "แผนกอื่นทั้งหมด" in page
    assert "010 OPD จุดซักประวัติ ดูเหมือนจุดซักประวัติ" in page            # ติ๊กจุดซักประวัติผิด = เตือน
    assert 'name="add_allow" value="1"' in page


def test_install_adds_allow_even_when_script_unchanged(monkeypatch):
    """ฐานที่ติดตั้ง script ล่าสุดแล้วแต่ขาดแผนก ER ใน allow list — ต้องเพิ่มได้โดยไม่ต้องเปลี่ยน script"""
    script = install_alert.build_script("011", avpu=False, er_gcs=False, spo2_cols=set())
    cur = FakeCur(DEPS, allow=["002"], script=script)
    conn = types.SimpleNamespace(cursor=lambda: cur, commit=lambda: None, rollback=lambda: None, close=lambda: None)
    monkeypatch.setattr(install_alert, "connect", lambda a: conn)
    monkeypatch.setattr(install_alert, "opdscreen_cols", lambda c: set())
    monkeypatch.setattr(install_alert, "table_cols", lambda c, t: set())
    args = types.SimpleNamespace(er_deps="011", mews_alert=5, spo2_alert=92, news2_alert=5, wbc_items="",
                                 band_items="", probe=False, skip_forms=None, add_allow=True, yes=True,
                                 replace=False, host="h", db="hos_test", user="u")
    with pytest.raises(SystemExit, match="เพิ่มแผนก 011"):
        install_alert.cmd_install(args)
    assert [p for s, p in cur.log if s.lower().startswith("insert")] == [("011",)]
