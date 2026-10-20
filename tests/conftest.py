"""ตั้งค่าร่วมของ test — ไม่ต่อฐานข้อมูลจริง (แทน fetch ด้วยตัวปลอม) และไม่ยิงแจ้งเตือนจริง"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402
import moph_notify  # noqa: E402
import queries  # noqa: E402


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    """ไฟล์ตั้งค่า MOPH Notify ไปอยู่ในโฟลเดอร์ชั่วคราว · ห้ามยิง HTTP ออกจริง"""
    monkeypatch.setattr(moph_notify, "SETTINGS_PATH", str(tmp_path / "data" / "moph_settings.json"))

    def no_network(*a, **kw):
        raise AssertionError("test ห้ามยิง HTTP ออกจริง")

    monkeypatch.setattr(moph_notify.requests, "post", no_network)


@pytest.fixture
def fake_db(monkeypatch):
    """แทน fetch: ประกอบ SQL ด้วย db._bind (จับพารามิเตอร์หาย / % ที่ไม่ได้ escape) แล้วคืนผลว่าง

    คืน list ของ (sql, params) ให้ test ตรวจ
    """
    calls = []

    def fetch(sql, params=None):
        db._bind(sql, params)
        calls.append((sql, params))
        return []

    def fetch_one(sql, params=None):
        fetch(sql, params)
        return {}

    monkeypatch.setattr(queries, "fetch", fetch)
    monkeypatch.setattr(queries, "fetch_one", fetch_one)
    monkeypatch.setattr(queries, "_schema_cache", {})
    monkeypatch.setattr(queries, "_antibiotic_icodes", None)
    queries._case_cache.clear()
    queries._er_logs.cache_clear()
    return calls
