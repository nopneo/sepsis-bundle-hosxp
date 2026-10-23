"""ใบสั่ง Lab "Sepsis work up" (hosxp_alert/install_lab_form.py) — ตรวจโครงสร้างโดยไม่ต่อฐาน"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hosxp_alert"))
import install_lab_form as f  # noqa: E402

LAB_FORM_COLS = set("form_name,lab_items_code,form_position_left,form_position_top,tab_order,form_position_width,"
                    "component_name,parent_name,component_type,component_caption,checked,form_position_height,"
                    "font_name,font_size,font_color,specimen_code,hos_guid,hos_guid_ext".split(","))


def test_rows_fit_lab_form_table():
    rs = f.rows()
    names = [r["component_name"] for r in rs]
    assert len(names) == len(set(names))                 # PRIMARY KEY (component_name, form_name)
    assert all(set(r) <= LAB_FORM_COLS for r in rs)
    for r in rs:
        r["component_caption"].encode("tis-620")          # ฐาน HOSxP เก็บ tis620


def test_core_items_prechecked():
    checked = {cap for _, _, cap, chk, *_ in f.ITEMS if chk == "Y"}
    assert {"Lactate", "Hemoculture (H/C) x 2 ขวด - ส่ง Out Lab", "CBC", "BUN", "Creatinine", "eGFR", "Electrolyte",
            "Urine Analysis (UA)"} == checked


def test_layout_no_overlap():
    boxes = [(x, y) for *_, x, y, _w in f.ITEMS] + [(x, y) for _, _, x, y, *_ in f.LABELS]
    assert len(boxes) == len(set(boxes))
