"""ตัวติดตั้งหน้าต่างเตือน "สงสัย Sepsis" ในหน้าซักประวัติ HOSxP (SYS_VN_SCRIPT)

    python install_alert.py check    --host <ip> [--db hos] --user <user>
    python install_alert.py install  --host <ip> [--db hos] --user <user> --er-deps 011[,012] [--mews-alert 5] [--news2-alert 5]
    python install_alert.py rollback --host <ip> [--db hos] --user <user> --backup backups/<ไฟล์>.pas
    python install_alert.py wizard   [--wizard-port 8787]

ถามรหัสผ่านแบบซ่อน ไม่เก็บรหัสในไฟล์ · ทดสอบกับฐานทดสอบก่อนฐานจริงเสมอ

check    : script ปัจจุบัน, รายการแผนกที่รัน VN script, รหัสแผนก ER ที่แนะนำ (จาก opdscreen.screen_dep
           ของ visit ER), ประมาณจำนวนการเตือนต่อวันจากข้อมูล 90 วัน — อ่านอย่างเดียว
install  : สำรอง script เดิมลง backups/ อัตโนมัติ แล้วติดตั้ง (เขียนเฉพาะเมื่อ script ไม่เปลี่ยนระหว่างนั้น)
           MEWS แสดงในหน้าต่างเตือน และเตือนแยกเมื่อ MEWS >= --mews-alert (ค่าเริ่มต้น 5, 0 = ไม่เตือนแยก)
           รวม AVPU ในคะแนนเองถ้ามีคอลัมน์ opdscreen.consciousness_level_type_id
           ถ้า script เดิมของ รพ. ไม่ใช่ script SOS แบบที่ระบบนี้รู้จัก จะไม่ทับให้ — ต้องรวมเอง หรือใส่ --replace
rollback : คืน script จากไฟล์สำรอง (เขียนเฉพาะเมื่อ script ปัจจุบันเป็นของระบบนี้)
wizard   : เปิดหน้าเว็บที่ localhost ให้กรอกฐาน + เลือกรหัสแผนก ER/lab จากรายการที่อ่านได้จริง แทนการพิมพ์ CLI flag
           (รันครั้งเดียวตอนติดตั้ง ไม่ใช่ service ค้างถาวร — ทำงานเหมือนคำสั่ง install ทุกอย่าง แค่เปลี่ยนหน้าตา)

สิทธิ์ที่ต้องใช้: SELECT, UPDATE (และ INSERT ถ้ายังไม่มีแถว SYS_VN_SCRIPT) บน hos.sys_app_script
และ SELECT บน opdscreen, ovst, er_regist, vn_stat, kskdepartment, depcode_vn_script_allow (สำหรับ check)
"""

import argparse
import contextlib
import getpass
import hashlib
import html
import io
import os
import re
import sys
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "_vendor"))
try:
    import pymysql
except ImportError:
    sys.exit("ต้องติดตั้ง pymysql ก่อน: pip install pymysql")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TEMPLATE = os.path.join(HERE, "SYS_VN_SCRIPT_sepsis_template.pas")
BACKUPS = os.path.join(HERE, "backups")
MARKER = "SepsisNoticeForm"            # มีใน script ของระบบนี้ทุกฉบับ
SOS_MARKER = "sos_score"               # script SOS เดิมแบบ รพ.วังน้ำเขียว
SCRIPT_NAME = "SYS_VN_SCRIPT"


# Modified Early Warning Score (Subbe 2001) — ช่องว่าง/ค่าผิด (เช่น T 0, BP 0) ได้ 0 ไม่ใช่คะแนนสูงสุด
MEWS_PARTS = [
    "(case when rr is null or rr <= 2 then 0 when rr < 9 then 2 when rr <= 14 then 0 "
    "when rr <= 20 then 1 when rr <= 29 then 2 else 3 end)",
    "(case when pulse is null or pulse <= 20 then 0 when pulse <= 40 then 2 when pulse <= 50 then 1 "
    "when pulse <= 100 then 0 when pulse <= 110 then 1 when pulse < 130 then 2 else 3 end)",
    "(case when bps is null or bps <= 50 then 0 when bps <= 70 then 3 when bps <= 80 then 2 "
    "when bps <= 100 then 1 when bps < 200 then 0 else 2 end)",
    "(case when temperature is null or temperature <= 20 then 0 when temperature <= 35 then 2 "
    "when temperature < 38.5 then 0 else 2 end)",
]
# AVPU จากหน้าซักประวัติ: 1 A=0, 2 V=1, 3 P=2, 4 U=3 · ว่าง = 0
MEWS_AVPU = "(case consciousness_level_type_id when 2 then 1 when 3 then 2 when 4 then 3 else 0 end)"


def mews_sql(avpu, sep=" + "):
    return sep.join(MEWS_PARTS + ([MEWS_AVPU] if avpu else []))


# SpO2: ช่อง o2sat ก่อน ถ้าว่างดึงจากข้อความที่พยาบาลพิมพ์ เช่น "O2 sat : 96 %", "O2 sat RA =97" · นอกช่วง 50-100 = 0
SPO2_TEXT_COLS = ("hpi", "pe", "note")
# ห้ามใช้ ? และ { } — ตัวเชื่อมฐานของ HOSxP ตีความ ? เป็นพารามิเตอร์ / { } เป็นคอมเมนต์ Pascal (script หยุดเงียบ)
SPO2_REGEX = "(o2 *sat|spo *2|sat *o2)( *ra| *room *air)* *[:=]* *[0-9][0-9][0-9]*"


def spo2_sql(cols=None):
    """นิพจน์ SpO2 ต่อ 1 แถว opdscreen — cols = คอลัมน์ที่มีจริง (HOSxP รุ่นเก่าอาจไม่มีบางช่อง = script พังเงียบ)"""
    cols = set(cols) if cols is not None else {"o2sat", *SPO2_TEXT_COLS}
    parts = ["nullif(round(o2sat), 0)"] if "o2sat" in cols else []
    text = [c for c in SPO2_TEXT_COLS if c in cols]
    if text:
        parts.append(f'cast(regexp_substr(regexp_substr(lower(concat_ws(" ", {", ".join(text)})), '
                     f'"{SPO2_REGEX}"), "[0-9]+$") as unsigned)')
    if not parts:
        return "0"
    v = f"coalesce({', '.join(parts)}, 0)"
    return f"if({v} between 50 and 100, {v}, 0)"


SPO2_SQL = spo2_sql()

# qSOFA (Sepsis-3) — แสดงประกอบในหน้าต่าง Sepsis เท่านั้น ไม่ใช้สั่งเตือน (SSC: ไม่ใช้ qSOFA คัดกรองเดี่ยว)
# สติเปลี่ยน = AVPU V/P/U ในหน้าซักประวัติ หรือ GCS < 15 ที่ ER (er_nursing_detail) — นับเฉพาะค่าในช่วงที่ถูกต้อง
_GCS_VALID = ("d.vn = o.vn and d.gcs_e between 1 and 4 and d.gcs_v between 1 and 5 "
              "and d.gcs_m between 1 and 6")


def _gcs_text(text_cols):
    """GCS ที่พยาบาลพิมพ์ใน HPI/PE เช่น "E4V5M6", "E3VTM5" (ต.ค. 2026: พบ ~14% ของ visit มากกว่าช่อง GCS ราว 5 เท่า)"""
    cols = ", ".join(f"o.{c}" for c in SPO2_TEXT_COLS if c in text_cols)
    return f'regexp_substr(lower(concat_ws(" ", {cols})), "e *[1-4] *v *[1-5t] *m *[1-6]")' if cols else ""


# วลีบอกสติเปลี่ยนที่พยาบาลพิมพ์ใน CC/HPI/PE/หมายเหตุ — เฉพาะวลีที่ชัด (ข้อมูล 90 วัน ต.ค. 2026:
# "ซึม" เดี่ยว ๆ ส่วนใหญ่คือ "เลือดซึม/แผลซึม" ~480 visit จึงใช้เฉพาะ "ซึมลง/ซึมมา/ดูซึม" และตัดกรณีของเหลวซึม
# "หมดสติ" มักเป็นลมที่ฟื้นแล้ว ไม่นับ) · ห้ามใช้ ? และ { } ใน regex (script หยุดเงียบใน HOSxP)
MENT_TEXT_COLS = ("cc",) + SPO2_TEXT_COLS
MENT_SUEM = "ซึม *(ลง|มา)|ดูซึม"
MENT_OOZE = "(เลือด|น้ำเหลือง|หนอง|แผล) *ซึม"
MENT_OTHER = "สับสน|(เรียก|ปลุก) *ไม่ *ตื่น|ไม่รู้สึกตัว|drows|confus"
MENT_NEG = "(ไม่มี|ไม่|ปฏิเสธ|no|not|deny|denies|denied|without) *"


def _ment_text(text_cols):
    cols = ", ".join(f"o.{c}" for c in MENT_TEXT_COLS if c in text_cols)
    return f'lower(concat_ws(" ", {cols}))' if cols else ""


def _ment_words(text_cols, altered):
    t = _ment_text(text_cols)
    if not t:
        return ""
    if not altered:     # มีการเขียนถึงสติ (รวมเชิงปฏิเสธ เช่น "ไม่สับสน") = ประเมินแล้ว
        return f'({t} regexp "{MENT_OTHER}" or ({t} regexp "{MENT_SUEM}" and {t} not regexp "{MENT_OOZE}"))'
    return (f'(({t} regexp "{MENT_OTHER}" and {t} not regexp "{MENT_NEG}({MENT_OTHER})") or '
            f'({t} regexp "{MENT_SUEM}" and {t} not regexp "{MENT_OOZE}" and {t} not regexp "{MENT_NEG}({MENT_SUEM})"))')


def _mentation(avpu, er_gcs, altered, text_cols=()):
    conds = []
    if avpu:
        conds.append(f"o.consciousness_level_type_id in ({'2, 3, 4' if altered else '1, 2, 3, 4'})")
    if er_gcs:
        extra = " and d.gcs_e + d.gcs_v + d.gcs_m < 15" if altered else ""
        conds.append(f"exists(select 1 from er_nursing_detail d where {_GCS_VALID}{extra})")
    g = _gcs_text(text_cols)
    if g:   # ค่าแรกที่พิมพ์ ไม่ใช่ E4V5M6 = GCS < 15 (รวม VT ใส่ท่อช่วยหายใจ)
        conds.append(f'({g} <> "" and {g} not regexp "e *4 *v *5 *m *6")' if altered else f'{g} <> ""')
    w = _ment_words(text_cols, altered)
    if w:
        conds.append(w)
    return " or ".join(conds)


def qsofa_sql(avpu, er_gcs, text_cols=()):
    """คะแนน qSOFA 0-3 ต่อ 1 แถว opdscreen (alias o): RR >= 22, SBP <= 100, สติเปลี่ยน"""
    parts = ["(case when o.rr >= 22 then 1 else 0 end)",
             "(case when o.bps > 50 and o.bps <= 100 then 1 else 0 end)"]
    m = _mentation(avpu, er_gcs, True, text_cols)
    if m:
        parts.append(f"(case when {m} then 1 else 0 end)")
    return " + ".join(parts)


def qsofa_known_sql(avpu, er_gcs, text_cols=()):
    """1 = มีข้อมูลสติให้ประเมินข้อ 3 ของ qSOFA · 0 = ไม่มี (คะแนนอาจต่ำกว่าจริง)"""
    m = _mentation(avpu, er_gcs, False, text_cols)
    return f"(case when {m} then 1 else 0 end)" if m else "0"


# SIRS (Bone 1992): T > 38 หรือ < 36, HR > 90, RR > 20, WBC > 12,000 หรือ < 4,000 หรือ Band > 10%
# WBC/Band จากผล lab ของ visit นี้ (รหัส lab_items_code แต่ละ รพ. ไม่เหมือนกัน — ส่งผ่าน --wbc-items / --band-items)
# ผลเก็บเป็นข้อความ: นับเฉพาะตัวเลขล้วน · หน่วย x10^3 (เช่น 9.6) หรือ cell (เช่น 9600) — >= 100 หาร 1000
_LAB_NUM = '^[0-9]+[.]*[0-9]*$'


def _codes(items):
    codes = [c.strip() for c in (items or "").split(",") if c.strip()]
    if not all(c.isdigit() for c in codes):
        sys.exit("--wbc-items / --band-items ต้องเป็นรหัส lab_items_code ตัวเลข คั่นด้วยจุลภาค เช่น 35")
    return ", ".join(codes)


def _lab_exists(codes, cond):
    return (f"exists(select 1 from lab_head h join lab_order lo on lo.lab_order_number = h.lab_order_number "
            f'where h.vn = o.vn and lo.lab_items_code in ({codes}) and lo.lab_order_result regexp "{_LAB_NUM}"'
            f"{cond})")


def _wbc_abnormal(wbc, band):
    conds = []
    if wbc:
        v = "(lo.lab_order_result + 0)"
        conds.append(_lab_exists(wbc, f" and (if({v} >= 100, {v} / 1000, {v}) > 12 "
                                      f"or if({v} >= 100, {v} / 1000, {v}) < 4)"))
    if band:
        conds.append(_lab_exists(band, " and (lo.lab_order_result + 0) > 10"))
    return " or ".join(conds)


def sirs_sql(wbc="", band=""):
    """คะแนน SIRS 0-4 ต่อ 1 แถว opdscreen (alias o) — ช่องว่าง/ค่าผิด (T <= 20, P/RR 0) ไม่นับ"""
    parts = ["(case when o.temperature > 38 or (o.temperature > 20 and o.temperature < 36) then 1 else 0 end)",
             "(case when o.pulse > 90 then 1 else 0 end)",
             "(case when o.rr > 20 then 1 else 0 end)"]
    w = _wbc_abnormal(_codes(wbc), _codes(band))
    if w:
        parts.append(f"(case when {w} then 1 else 0 end)")
    return " + ".join(parts)


def sirs_wbc_known_sql(wbc=""):
    """1 = มีผล WBC ของ visit นี้แล้ว · 0 = ยังไม่มี (SIRS คิดจากสัญญาณชีพ 3 ข้อ)"""
    codes = _codes(wbc)
    return f"(case when {_lab_exists(codes, '')} then 1 else 0 end)" if codes else "0"


# หน้าต่าง HOSxP ที่เรียก script แต่ไม่ใช่การบันทึกซักประวัติ — ไม่เตือน (ทดสอบด้วย --probe 23 ต.ค. 2026)
# frmLabOrder = หน้าสั่ง LAB (เรียก 2 รอบ) · ERDetailEntryForm = ลงรายละเอียด ER ขั้นสุดท้ายก่อนส่งต่อ
# บันทึกซักประวัติ = DoctorDeskForm (ต้องเตือนทุกครั้ง)
SKIP_FORMS = "frmLabOrder,ERDetailEntryForm"

# ข้อความทดสอบ: บอกหน้าต่างที่เปิดอยู่ตอน script ทำงาน — ใช้หาวิธีแยก "บันทึกซักประวัติ" ออกจาก "เปิดหน้าสั่ง Lab"
PROBE_LINE = ("showmessage('PROBE ' + Screen.ActiveForm.Name + ' / ' + Screen.ActiveForm.Caption + "
              "' / ' + Screen.ActiveForm.ClassName);")


# NEWS2 (Royal College of Physicians 2017) — SpO2 scale 1 · ให้ O2 = +2 · สติเปลี่ยน (CVPU) = 3
# ใช้คอลัมน์ sp (SpO2 จาก spo2_sql) ของตารางย่อย o · ช่องว่าง/ค่าผิดได้ 0
NEWS2_PARTS = [
    "(case when o.rr is null or o.rr <= 2 then 0 when o.rr <= 8 then 3 when o.rr <= 11 then 1 "
    "when o.rr <= 20 then 0 when o.rr <= 24 then 2 else 3 end)",
    "(case when o.sp is null or o.sp = 0 then 0 when o.sp <= 91 then 3 when o.sp <= 93 then 2 "
    "when o.sp <= 95 then 1 else 0 end)",
    "(case when o.bps is null or o.bps <= 50 then 0 when o.bps <= 90 then 3 when o.bps <= 100 then 2 "
    "when o.bps <= 110 then 1 when o.bps < 220 then 0 else 3 end)",
    "(case when o.pulse is null or o.pulse <= 20 then 0 when o.pulse <= 40 then 3 when o.pulse <= 50 then 1 "
    "when o.pulse <= 90 then 0 when o.pulse <= 110 then 1 when o.pulse <= 130 then 2 else 3 end)",
    "(case when o.temperature is null or o.temperature <= 20 then 0 when o.temperature <= 35 then 3 "
    "when o.temperature <= 36 then 1 when o.temperature <= 38 then 0 when o.temperature <= 39 then 1 else 2 end)",
]
# ให้ออกซิเจนตามข้อความพยาบาล เช่น "On O2 cannula 3 LPM", "O2 mask c bag" (ห้ามใช้ ? และ { })
O2_REGEX = "(on *o2|o2 *(canu|cannu|mask|nasal)|nasal *cann|mask *c *bag|hfnc|high *flow)"


def news2_sql(avpu, er_gcs, text_cols=()):
    parts = list(NEWS2_PARTS)
    cols = ", ".join(f"o.{c}" for c in SPO2_TEXT_COLS if c in text_cols)
    if cols:
        parts.append(f'(case when lower(concat_ws(" ", {cols})) regexp "{O2_REGEX}" then 2 else 0 end)')
    m = _mentation(avpu, er_gcs, True, text_cols)
    if m:
        parts.append(f"(case when {m} then 3 else 0 end)")
    return " + ".join(parts)


def build_script(er_deps, avpu=False, mews_alert=5, spo2_alert=92, spo2_cols=None, er_gcs=False,
                 wbc_items="", band_items="", probe=False, skip_forms=None, news2_alert=5):
    codes = [c.strip() for c in er_deps.split(",") if c.strip()]
    if not codes or not all(re.fullmatch(r"[0-9A-Za-z]{1,6}", c) for c in codes):
        sys.exit("--er-deps ต้องเป็นรหัสแผนก คั่นด้วยจุลภาค เช่น 011 หรือ 011,012")
    if not 0 <= mews_alert <= 14:
        sys.exit("--mews-alert ต้องเป็น 0-14 (0 = ไม่เตือน MEWS แยก)")
    text = open(TEMPLATE, encoding="utf-8").read()
    text = text.replace("{{ER_DEPS_SQL}}", ",".join(f'"{c}"' for c in codes))
    text = text.replace("{{ER_DEPS_TEXT}}", "/".join(codes))
    # นิพจน์อยู่ในสตริง Pascal '...' — ต่อสตริงบรรทัดละส่วน
    text = text.replace("{{MEWS_SQL}}", mews_sql(avpu, sep=" + '+\n'"))
    # เทียบแบบ fptname > 3 ที่ทดสอบผ่านใน HOSxP แล้ว (MEWS >= N เท่ากับ > N-1) · 0 = ไม่มีวันถึง
    text = text.replace("{{MEWS_GT}}", str(mews_alert - 1 if mews_alert else 98))
    if not 0 <= spo2_alert <= 100:
        sys.exit("--spo2-alert ต้องเป็น 0-100 (0 = ไม่เตือน SpO2 แยก)")
    text = text.replace("{{SPO2_SQL}}", spo2_sql(spo2_cols))
    text = text.replace("{{SPO2_LT}}", str(spo2_alert))
    text_cols = set(spo2_cols) if spo2_cols is not None else set(MENT_TEXT_COLS)
    text = text.replace("{{QSOFA_SQL}}", qsofa_sql(avpu, er_gcs, text_cols))
    text = text.replace("{{QSOFA_KNOWN_SQL}}", qsofa_known_sql(avpu, er_gcs, text_cols))
    if not 0 <= news2_alert <= 20:
        sys.exit("--news2-alert ต้องเป็น 0-20 (0 = ไม่เตือน NEWS2 แยก)")
    text = text.replace("{{NEWS2_SQL}}", news2_sql(avpu, er_gcs, text_cols))
    text = text.replace("{{NEWS2_GT}}", str(news2_alert - 1 if news2_alert else 98))
    text = text.replace("{{SIRS_SQL}}", sirs_sql(wbc_items, band_items))
    text = text.replace("{{SIRS_WBC_SQL}}", sirs_wbc_known_sql(wbc_items))
    forms = [f.strip() for f in (SKIP_FORMS if skip_forms is None else skip_forms).split(",") if f.strip()]
    if not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,60}", f) for f in forms):
        sys.exit("--skip-forms ต้องเป็นชื่อหน้าต่าง HOSxP คั่นด้วยจุลภาค เช่น frmLabOrder")
    text = text.replace("{{SKIP_FORMS_COND}}", " or ".join(f"(fform = '{f}')" for f in forms) or "(fform = '-')")
    if probe:
        anchor = "fvn := GetGlobalValue('VN');\n"
        assert anchor in text
        text = text.replace(anchor, anchor + PROBE_LINE + "\n", 1)
    text = split_long_literals(text)
    text.encode("tis-620")          # ฐาน HOSxP ส่วนใหญ่เก็บ tis620 — อักษรนอกชุดนี้บันทึกไม่ได้
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


# script engine ของ HOSxP รับ literal '...' ได้ไม่เกิน 255 ตัวแบบ Delphi — ยาวกว่านี้ script หยุดเงียบ
# (23 ต.ค. 2026: SQL SpO2/qSOFA ยาว 335-599 ตัวทำให้ hos_test ไม่ขึ้นอะไรเลย · script เดิมที่ใช้ได้ยาวสุด 238)
LITERAL_MAX = 200
_LITERAL = re.compile(r"'(?:[^'\n]|'')*'")


def _split_sql(s, limit=LITERAL_MAX):
    """ตัดข้อความเป็นท่อน <= limit ที่ช่องว่างนอก "..." (ไม่ตัดกลาง regex/ค่าคงที่) — ต่อกลับแล้วได้ข้อความเดิม"""
    cuts, inq = [], False
    for i, ch in enumerate(s):
        if ch == '"':
            inq = not inq
        elif ch == " " and not inq:
            cuts.append(i)
    parts, start = [], 0
    while len(s) - start > limit:
        ok = [c for c in cuts if start < c <= start + limit]
        end = ok[-1] if ok else next((c for c in cuts if c > start), len(s))
        parts.append(s[start:end])
        start = end
    parts.append(s[start:])
    return parts


def split_long_literals(text, limit=LITERAL_MAX):
    def fix(m):
        inner = m.group(0)[1:-1]
        if len(inner) <= limit:
            return m.group(0)
        return "'+\n'".join(_split_sql(inner, limit)).join("''")
    out = []
    for ln in text.split("\n"):
        out.append(ln if ln.lstrip().startswith("//") else _LITERAL.sub(fix, ln))
    return "\n".join(out)


def pymysql_connect(host, port, user, password, db):
    """เชื่อมต่อฐาน HOSxP ตรง ๆ — แยกจาก connect() เพื่อให้วิซาร์ด (cmd_wizard) เรียกด้วยรหัสผ่านจากฟอร์มเว็บได้"""
    if any(ord(ch) > 255 for ch in password):
        raise ValueError("รหัสผ่านมีอักษรไทย — แป้นพิมพ์อาจเป็นภาษาไทยอยู่ สลับเป็นภาษาอังกฤษ (ปุ่ม ~ หรือ Alt+Shift) แล้วรันใหม่")
    return pymysql.connect(host=host, port=port, user=user, password=password, database=db,
                           charset="tis620", connect_timeout=10, autocommit=False)


def connect(args):
    # ALERT_DB_PASS ใช้กับงานอัตโนมัติได้ (ตั้งเฉพาะในหน้าต่างคำสั่งนั้น ห้ามเขียนลงไฟล์) ไม่งั้นถามแบบซ่อน
    pw = os.environ.get("ALERT_DB_PASS") or getpass.getpass(f"รหัสผ่าน {args.user}@{args.host} (พิมพ์แล้วไม่แสดง): ")
    try:
        c = pymysql_connect(args.host, args.port, args.user, pw, args.db)
    except ValueError as exc:
        sys.exit(f"!! {exc}")
    finally:
        pw = None
    return c


def q(cur, sql, params=None):
    cur.execute(sql, params)
    return cur.fetchall()


def current(cur):
    rows = q(cur, "select app_script_id, script_text, md5(script_text) from sys_app_script "
                  "where script_name=%s order by app_script_id", (SCRIPT_NAME,))
    if len(rows) > 1:
        sys.exit(f"!! มีแถว {SCRIPT_NAME} มากกว่า 1 แถว ({[r[0] for r in rows]}) — ตรวจด้วยตัวเองก่อน")
    return rows[0] if rows else None


def kind(text):
    if text is None:
        return "ไม่มี"
    if MARKER in text:
        return "ของระบบนี้ (เตือนสงสัย Sepsis)"
    if SOS_MARKER in text:
        return "script SOS เดิม (แบบที่ระบบนี้รู้จัก)"
    if not text.strip():
        return "ว่าง"
    return "script อื่นของ รพ. (ต้องรวมเอง)"


def fetch_er_dep_candidates(cur):
    """แผนกที่ซักประวัติ visit ER (30 วัน) เรียงตามจำนวนมากไปน้อย — ใช้เลือก --er-deps"""
    return q(cur, """
            select s.screen_dep, k.department, count(*) n from er_regist e
            join opdscreen s on s.vn = e.vn left join kskdepartment k on k.depcode = s.screen_dep
            where e.vstdate >= curdate() - interval 30 day and s.bps > 0
            group by s.screen_dep, k.department order by n desc limit 6""")


ER_NAME = re.compile(r"\ber\b|ฉุกเฉิน|อุบัติเหตุ|emergency", re.I)
SCREEN_NAME = re.compile(r"ซักประวัติ|คัดกรอง|opd|ari|ผู้ป่วยนอก|คลินิก|หัตถการ", re.I)


def recommend_er_deps(rows):
    """แผนก ER ที่ควรติ๊ก: ชื่อเป็นห้องฉุกเฉิน ถ้าไม่มีใช้แผนกที่มี visit ER มากที่สุด
    (แผนกจุดซักประวัติ OPD/ARI ที่มี visit ER ด้วย = ผู้ป่วยคัดกรองที่ OPD แล้วส่ง ER — ต้องได้หน้าต่างแดง ไม่ใช่ ER)"""
    by_name = {dep for dep, name, _ in rows if dep and ER_NAME.search(name or "") and not SCREEN_NAME.search(name or "")}
    if by_name:
        return by_name
    top = max(rows, key=lambda r: r[2], default=None)
    return {top[0]} if top and top[0] else set()


def er_dep_warnings(er_deps, names):
    """เตือนเมื่อติ๊กแผนกที่ชื่อเหมือนจุดซักประวัติ — จุดนั้นจะได้หน้าต่าง ER แทนหน้าต่างแดง "ส่ง ER ทันที\""""
    return [f"{d} {names.get(d, '')} ดูเหมือนจุดซักประวัติ/OPD — ถ้าติ๊ก จุดนี้จะได้หน้าต่าง ER แทนหน้าต่างแดง \"ส่ง ER ทันที\""
            for d in er_deps if SCREEN_NAME.search(names.get(d, "") or "") and not ER_NAME.search(names.get(d, "") or "")]


def fetch_allow_deps(cur):
    """รายการแผนกที่ HOSxP ยอมรัน VN script (depcode_vn_script_allow) — None = ไม่มีตารางนี้"""
    if not q(cur, "select 1 from information_schema.tables where table_schema = database() "
                  "and table_name = %s", ("depcode_vn_script_allow",)):
        return None
    return {r[0] for r in q(cur, "select depcode from depcode_vn_script_allow") if r[0]}


def add_allow_deps(cur, deps):
    """เพิ่มแผนก ER ที่ยังไม่อยู่ใน depcode_vn_script_allow (เพิ่มอย่างเดียว ไม่ลบของเดิม) — คืนรายการที่เพิ่ม"""
    have = fetch_allow_deps(cur)
    if have is None:
        return []
    added = [d for d in deps if d and d not in have]
    for d in added:
        cur.execute("insert into depcode_vn_script_allow (depcode) values (%s)", (d,))
    return added


def target_banner(host, db):
    """กล่องบอกฐานที่จะเขียน — ฐานที่ชื่อไม่มีคำว่า test ถือเป็นฐานจริง (HOSxP ทุกเครื่องใช้ทันทีหลังเปิดใหม่)"""
    where = f"{_esc(host)} / {_esc(db)}"
    if re.search(r"test|tst|dev|demo", db or "", re.I):
        return f'<div class="warn"><b>ฐานทดสอบ: {where}</b></div>'
    return (f'<div class="err"><b>ฐานจริง: {where}</b> — HOSxP ทุกเครื่องที่ต่อฐานนี้จะใช้ script ใหม่ทันทีหลังเปิดโปรแกรมใหม่ '
            "ติดตั้งช่วงคนน้อย และลองผู้ป่วยจริง 1 รายทันทีหลังติดตั้ง</div>")


def fetch_lab_item_candidates(cur):
    """รหัส lab ที่ชื่อเข้าเกณฑ์ WBC/Band — ใช้เลือก --wbc-items / --band-items"""
    return q(cur, """select lab_items_code, lab_items_name, lab_items_unit from lab_items
                       where lab_items_name regexp "(^| )wbc count|white blood|^band" order by 1""")


def estimate_alerts(cur, avpu):
    """ประมาณจำนวนการเตือนจากข้อมูล 90 วันย้อนหลัง (อ่านอย่างเดียว)"""
    est = q(cur, """
        select count(*) from (
          select s.temperature t,
           if(s.temperature<=20,0,if(s.temperature between 36.1 and 38,0,if(s.temperature between 35.1 and 36,1,
             if(s.temperature between 38.1 and 38.4,1,if(s.temperature<=35 or s.temperature>=38.5,2,0)))))
          + if(s.bps<=50,0,if(s.bps between 101 and 180,0,if(s.bps between 91 and 100,1,if(s.bps between 181 and 199,1,
             if(s.bps between 81 and 90,2,if(s.bps>=200,2,if(s.bps<=80,3,0)))))))
          + if(s.pulse<=20,0,if(s.pulse between 51 and 100,0,if(s.pulse between 41 and 50,1,if(s.pulse between 100 and 120,1,
             if(s.pulse between 121 and 139,2,if(s.pulse<=40,3,if(s.pulse>=140,3,0)))))))
          + if(s.rr<=2,0,if(s.rr between 9 and 20,0,if(s.rr between 21 and 25,1,if(s.rr between 26 and 35,2,
             if(s.rr>=35 or s.rr<=8,3,0))))) sos
          from opdscreen s join vn_stat v on v.vn = s.vn
          where s.vstdate >= curdate() - interval 90 day and v.age_y >= 15
            and s.temperature > 0 and s.bps > 0 and s.pulse > 0 and s.rr > 0) x
        where sos > 3 and (t >= 38 or (t > 20 and t < 36))""")[0][0]
    row = q(cur, f"""
        select sum(m >= 3), sum(m >= 5), sum(m >= 7), count(*), sum(a) from (
          select {mews_sql(avpu)} m, {"consciousness_level_type_id is not null" if avpu else "0"} a
          from (select s.* from opdscreen s join vn_stat v on v.vn = s.vn
                where s.vstdate >= curdate() - interval 90 day and v.age_y >= 15) s) x""")[0]
    ge3, ge5, ge7, n, filled = (int(x or 0) for x in row)
    return {"est": est, "ge3": ge3, "ge5": ge5, "ge7": ge7, "n": n, "filled": filled}


def cmd_check(args):
    c = connect(args)
    cur = c.cursor()
    print(f"\n=== ฐาน {args.db} @ {args.host} ===")
    print("MariaDB/MySQL", q(cur, "select version()")[0][0])
    row = current(cur)
    if row:
        print(f"\nSYS_VN_SCRIPT id {row[0]} · ยาว {len(row[1] or '')} ตัวอักษร · md5 {row[2]} · {kind(row[1])}")
        print("  10 บรรทัดแรก:")
        for ln in (row[1] or "").splitlines()[:10]:
            print("   |", ln[:110])
    else:
        print("\nยังไม่มีแถว SYS_VN_SCRIPT (ติดตั้งจะเพิ่มแถวใหม่ ต้องมีสิทธิ์ INSERT)")
    try:
        allow = [r[0] for r in q(cur, "select depcode from depcode_vn_script_allow order by depcode")]
        print("\nแผนกใน depcode_vn_script_allow:", ", ".join(a for a in allow if a) or "(ว่าง)")
    except pymysql.MySQLError as exc:
        print("\nอ่าน depcode_vn_script_allow ไม่ได้:", exc.args[-1])

    print("\nแผนกที่ซักประวัติ visit ER (30 วัน) — ใช้เลือก --er-deps:")
    for dep, name, n in fetch_er_dep_candidates(cur):
        print(f"   {dep or '(ว่าง)':>6}  {n:>5} visit  {name or ''}")

    avpu = has_avpu(cur)
    stats = estimate_alerts(cur, avpu)
    print(f"\nประมาณการ: จะเตือนสงสัย Sepsis {stats['est']} ครั้งใน 90 วัน (~{stats['est'] / 90:.1f} ครั้ง/วัน)")
    print("\nช่อง AVPU (opdscreen.consciousness_level_type_id):", "มี" if avpu else "ไม่มี — MEWS คิด 4 ค่า")
    print(f"MEWS ผู้ใหญ่ {stats['n']} visit (กรอก AVPU {stats['filled']}) — >= 3: {stats['ge3']} · >= 5: {stats['ge5']}"
          f" (~{stats['ge5'] / 90:.1f} ครั้ง/วัน) · >= 7: {stats['ge7']}")
    print("\nรหัส lab สำหรับ SIRS (ใส่ใน --wbc-items / --band-items ตอนติดตั้ง):")
    for code, name, unit in fetch_lab_item_candidates(cur):
        print(f"  {code:>6}  {name}  [{unit or '-'}]")
    c.close()


def table_cols(cur, table):
    return {r[0].lower() for r in q(cur, "select column_name from information_schema.columns "
                                         "where table_schema = database() and table_name = %s", (table,))}


def opdscreen_cols(cur):
    return table_cols(cur, "opdscreen")


def has_avpu(cur):
    return "consciousness_level_type_id" in opdscreen_cols(cur)


def backup(row, args):
    os.makedirs(BACKUPS, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(BACKUPS, f"SYS_VN_SCRIPT_{args.db}_{args.host}_{stamp}.pas")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(row[1] if row else "")
    if not row:
        open(path + ".none", "w").close()       # บอก rollback ว่าเดิมไม่มีแถว
    return path


def confirm(args, action):
    if args.yes:
        return
    ans = input(f"\n{action} บนฐาน {args.db} @ {args.host} — พิมพ์ YES เพื่อยืนยัน: ")
    if ans.strip() != "YES":
        sys.exit("ยกเลิก")


def install_summary_lines(args, cols, avpu, er_gcs):
    """สรุปแหล่งข้อมูล SIRS/qSOFA/SpO2/MEWS ที่จะใช้จริงตามคอลัมน์ที่ฐานนี้มี — cmd_install พิมพ์ และวิซาร์ดใช้แสดง preview"""
    lines = []
    lines.append("SIRS: T/HR/RR" + (f" + WBC (lab {args.wbc_items})" if args.wbc_items else
                                    " (ไม่ได้ระบุ --wbc-items ข้อ WBC ไม่นับ)")
                  + (f" + Band (lab {args.band_items})" if args.band_items else ""))
    lines.append("qSOFA: แสดงในหน้าต่าง Sepsis · ข้อสติใช้ "
          + (" + ".join(n for n, ok in (("AVPU", avpu), ("GCS ที่ ER", er_gcs),
                                        ("GCS ในข้อความ HPI/PE", bool(_gcs_text(cols))),
                                        ("วลีใน CC/HPI เช่น ซึมลง/สับสน", bool(_ment_text(cols)))) if ok)
             or "(ไม่มีข้อมูล นับ 2 ข้อ)"))
    lines.append(f"SpO2: อ่านจาก {', '.join(c for c in ('o2sat', *SPO2_TEXT_COLS) if c in cols) or '(ไม่มีช่อง)'} · "
          + (f"เตือนแยกเมื่อ SpO2 < {args.spo2_alert}%" if args.spo2_alert else "ไม่เตือนแยก"))
    lines.append(f"MEWS: {'รวม AVPU' if avpu else 'ไม่มีช่อง AVPU คิด 4 ค่า'} · "
          + (f"เตือนแยกเมื่อ MEWS >= {args.mews_alert}" if args.mews_alert
             else "ไม่เตือนแยก (แสดงในหน้าต่าง Sepsis เท่านั้น)"))
    news2_alert = getattr(args, "news2_alert", 5)
    lines.append("NEWS2: SpO2 scale 1 + ให้ O2 จากข้อความ · "
          + (f"เตือนแยกเมื่อ NEWS2 >= {news2_alert}" if news2_alert
             else "ไม่เตือนแยก (แสดงในหน้าต่าง Sepsis เท่านั้น)"))
    return lines


def cmd_install(args):
    c = connect(args)
    cur = c.cursor()
    cols = opdscreen_cols(cur)
    avpu = "consciousness_level_type_id" in cols
    er_gcs = {"gcs_e", "gcs_v", "gcs_m"} <= table_cols(cur, "er_nursing_detail")
    new = build_script(args.er_deps, avpu=avpu, mews_alert=args.mews_alert,
                       spo2_alert=args.spo2_alert, spo2_cols=cols, er_gcs=er_gcs,
                       wbc_items=args.wbc_items, band_items=args.band_items, probe=args.probe,
                       skip_forms=args.skip_forms, news2_alert=args.news2_alert)
    if args.probe:
        print("!! ฉบับทดสอบ (--probe): ขึ้นข้อความ PROBE ทุกครั้งที่ script ทำงาน")
    for line in install_summary_lines(args, cols, avpu, er_gcs):
        print(line)
    row = current(cur)
    k = kind(row[1] if row else None)
    print(f"script ปัจจุบัน: {k}")
    if row and row[1].replace("\r\n", "\n") == new.replace("\r\n", "\n"):
        sys.exit("ฉบับนี้ติดตั้งอยู่แล้ว — ไม่ต้องทำอะไร")
    if k.startswith("script อื่น") and not args.replace:
        sys.exit("!! script เดิมของ รพ. ไม่ใช่แบบที่ระบบนี้รู้จัก — ติดตั้งจะลบการทำงานเดิมทั้งหมด\n"
                 "   ให้รวมเอง (ดูคู่มือ) หรือใส่ --replace ถ้าตั้งใจแทนที่ทั้งหมด")
    path = backup(row, args)
    print("สำรอง script เดิมไว้ที่", path)
    confirm(args, f"ติดตั้งหน้าต่างเตือน Sepsis (รหัส ER {args.er_deps})")
    if row:
        n = cur.execute("update sys_app_script set script_text=%s, update_date_time=now() "
                        "where app_script_id=%s and md5(script_text)=%s", (new, row[0], row[2]))
    else:
        new_id = q(cur, "select coalesce(max(app_script_id), 0) + 1 from sys_app_script")[0][0]
        n = cur.execute("insert into sys_app_script (app_script_id, script_name, script_text, update_date_time) "
                        "values (%s, %s, %s, now())", (new_id, SCRIPT_NAME, new))
    if n != 1:
        c.rollback()
        sys.exit(f"!! เขียนได้ {n} แถว (ต้องเป็น 1) — script อาจถูกแก้ระหว่างนี้ ยกเลิก")
    er_deps = [d for d in args.er_deps.split(",") if d]
    added = add_allow_deps(cur, er_deps) if getattr(args, "add_allow", False) else []
    c.commit()
    if added:
        print(f"เพิ่มแผนก {', '.join(added)} ใน depcode_vn_script_allow แล้ว · ย้อนกลับ: "
              f"delete from depcode_vn_script_allow where depcode in ({', '.join(repr(d) for d in added)})")
    after = current(cur)
    ok = after and after[1].replace("\r\n", "\n") == new.replace("\r\n", "\n")
    print("ติดตั้งแล้ว — อ่านกลับตรงกับต้นฉบับ" if ok else "!! อ่านกลับไม่ตรง — ให้ rollback ทันที")
    print(f"\nเปิด HOSxP ใหม่แล้วทดสอบ · ย้อนกลับ: python install_alert.py rollback --host {args.host} "
          f"--db {args.db} --user {args.user} --backup \"{path}\"")
    c.close()


def cmd_rollback(args):
    text = open(args.backup, encoding="utf-8", newline="").read()   # คง CRLF ตามที่ HOSxP เก็บ
    was_none = os.path.exists(args.backup + ".none")
    c = connect(args)
    cur = c.cursor()
    row = current(cur)
    if not row:
        sys.exit("ไม่มีแถว SYS_VN_SCRIPT — ไม่มีอะไรให้ย้อนกลับ")
    if MARKER not in (row[1] or "") and not args.force:
        sys.exit("!! script ปัจจุบันไม่ใช่ของระบบนี้ (อาจมีคนแก้ไปแล้ว) — ไม่ทับ ใส่ --force ถ้าตั้งใจ")
    confirm(args, "ย้อนกลับ SYS_VN_SCRIPT เป็นฉบับก่อนติดตั้ง" + (" (ลบแถวที่ระบบนี้เพิ่ม)" if was_none else ""))
    if was_none:
        n = cur.execute("delete from sys_app_script where app_script_id=%s and md5(script_text)=%s", (row[0], row[2]))
    else:
        n = cur.execute("update sys_app_script set script_text=%s, update_date_time=now() "
                        "where app_script_id=%s and md5(script_text)=%s", (text, row[0], row[2]))
    if n != 1:
        c.rollback()
        sys.exit(f"!! เขียนได้ {n} แถว — ยกเลิก")
    c.commit()
    print("ย้อนกลับแล้ว — เปิด HOSxP ใหม่เพื่อใช้ script เดิม")
    c.close()


# ---------------------------------------------------------------------------
# วิซาร์ดตั้งค่า: หน้าเว็บเดียว รันครั้งเดียวตอนติดตั้ง (ไม่ใช่ service ค้างถาวร) ใช้ http.server ของ Python เอง
# ไม่เพิ่ม dependency ให้ รพ. อื่น — เรียก fetch_er_dep_candidates/estimate_alerts/build_script/cmd_install
# ตัวเดิมซ้ำ ไม่เขียน logic ติดตั้งคู่กับ CLI
# ---------------------------------------------------------------------------

_WIZ = {}   # เก็บ connection + ข้อมูลเชื่อมต่อระหว่างขั้นตอน — วิซาร์ดใช้คนเดียวต่อครั้ง ไม่มีการแข่งกันเขียน

WIZARD_CSS = """
<style>
body{font-family:"Segoe UI",Tahoma,sans-serif;max-width:800px;margin:24px auto 60px;padding:0 16px;
     color:#1f2430;background:#f6f6fb}
h1{font-size:21px;margin-bottom:4px} h2{font-size:16px;margin-top:26px}
p.hint{color:#5a6072;font-size:13.5px}
label{display:block;margin-top:14px;font-weight:600;font-size:14.5px}
input[type=text],input[type=password],input[type=number]{width:100%;padding:8px 10px;margin-top:4px;
     border:1px solid #c6c9d6;border-radius:8px;font-size:15px;box-sizing:border-box}
table{border-collapse:collapse;width:100%;margin-top:10px;font-size:14px}
td,th{border:1px solid #dcdfe8;padding:6px 10px;text-align:left}
th{background:#eceefa}
button{margin-top:22px;padding:10px 20px;border:none;border-radius:8px;background:#4b3fd1;color:#fff;
     font-size:15px;cursor:pointer}
pre{background:#11141c;color:#d8dbe8;padding:14px;border-radius:8px;overflow-x:auto;font-size:13px;
     white-space:pre-wrap}
.err{background:#fde2e2;border:1px solid #f3a0a0;color:#8a1f1f;padding:10px 14px;border-radius:8px;margin-top:14px}
.warn{background:#fff3cd;border:1px solid #f0d38a;color:#7a5b00;padding:10px 14px;border-radius:8px;margin-top:14px}
details{border:1px solid #dcdfe8;border-radius:8px;margin-top:18px;padding:8px 14px}
summary{cursor:pointer;font-weight:600}
</style>
"""


def _esc(v):
    return html.escape(str(v if v is not None else ""))


def _wiz_page(title, body):
    return (f"<!doctype html><html lang='th'><head><meta charset='utf-8'>"
            f"<title>{_esc(title)}</title>{WIZARD_CSS}</head><body>{body}</body></html>")


def _form(handler):
    length = int(handler.headers.get("Content-Length", 0) or 0)
    body = handler.rfile.read(length).decode("utf-8") if length else ""
    return parse_qs(body, keep_blank_values=True)


def _first(form, key, default=""):
    v = form.get(key)
    return v[0] if v else default


def _int(form, key, default):
    try:
        return int(_first(form, key, str(default)))
    except ValueError:
        return default


def _connect_page(error=None, host="", port=3306, db="hos", user=""):
    err_html = f'<div class="err">{_esc(error)}</div>' if error else ""
    body = f"""
<h1>วิซาร์ดติดตั้งหน้าต่างเตือน Sepsis ใน HOSxP</h1>
<p class="hint">กรอกข้อมูลเชื่อมต่อฐาน HOSxP (MariaDB/MySQL) — รหัสผ่านไม่ถูกบันทึกลงไฟล์ใด ๆ
ทดสอบกับฐานทดสอบก่อนฐานจริงเสมอ</p>
{err_html}
<form method="post" action="/connect">
  <label>Host<input type="text" name="host" value="{_esc(host)}" required></label>
  <label>Port<input type="number" name="port" value="{_esc(port)}"></label>
  <label>ชื่อฐานข้อมูล (database)<input type="text" name="db" value="{_esc(db)}"></label>
  <label>User<input type="text" name="user" value="{_esc(user)}" required></label>
  <label>Password<input type="password" name="password" required></label>
  <button type="submit">เชื่อมต่อ</button>
</form>"""
    return _wiz_page("วิซาร์ดติดตั้ง Sepsis Alert", body)


def _select_page(cur, conn_info, error=None, picked=None):
    picked = picked or {}
    deps = fetch_er_dep_candidates(cur)
    recommended = recommend_er_deps(deps)
    er_sel = set(picked["er_deps"]) if "er_deps" in picked else recommended
    wbc_sel = set(picked.get("wbc_items", []))
    band_sel = set(picked.get("band_items", []))
    allow = fetch_allow_deps(cur)

    row = current(cur)
    k = kind(row[1] if row else None)

    def role(dep, name):
        if dep in recommended:
            return "<b>ห้องฉุกเฉิน — แนะนำให้ติ๊ก</b>"
        if SCREEN_NAME.search(name or ""):
            return "จุดซักประวัติ/OPD — ไม่ต้องติ๊ก (ได้หน้าต่างแดง)"
        return "ไม่ต้องติ๊ก ถ้าไม่ใช่ห้องฉุกเฉิน"

    def allowed(dep):
        if allow is None or not allow:
            return "-"
        return "รันได้" if dep in allow else '<span style="color:#b42318">ไม่อยู่ในรายการ</span>'

    dep_rows = "".join(
        f'<tr><td><input type="checkbox" name="er_deps" value="{_esc(dep)}"'
        f'{" checked" if dep in er_sel else ""}></td>'
        f'<td>{_esc(dep or "(ว่าง)")}</td><td>{_esc(name or "")}</td><td>{n}</td>'
        f'<td>{role(dep, name)}</td><td>{allowed(dep)}</td></tr>'
        for dep, name, n in deps)
    missing = sorted(d for d in recommended if allow and d not in allow)
    allow_html = ""
    if missing:
        allow_html = (
            f'<div class="warn">แผนก ER ({_esc(", ".join(missing))}) ยังไม่อยู่ในตาราง <code>depcode_vn_script_allow</code> '
            "— HOSxP อาจไม่รัน script เมื่อบันทึกจากแผนกนี้ (ไม่ขึ้นแม้ข้อความ SOS)"
            f'<label><input type="checkbox" name="add_allow" value="1"{"" if picked.get("add_allow") is False else " checked"}>'
            " เพิ่มแผนก ER ที่ติ๊กเข้ารายการนี้ตอนติดตั้ง (เพิ่มอย่างเดียว ไม่ลบแผนกเดิม)</label></div>")
    if not dep_rows:
        dep_rows = ('<tr><td colspan="4">ไม่พบ visit ER ใน 30 วันหลัง — ใส่รหัสแผนกเองไม่ได้ในหน้านี้ '
                   "ใช้ CLI --er-deps แทน</td></tr>")

    lab_rows = "".join(
        f'<tr><td>{_esc(code)}</td><td>{_esc(name)}</td><td>{_esc(unit or "-")}</td>'
        f'<td><input type="checkbox" name="wbc_items" value="{_esc(code)}"'
        f'{" checked" if code in wbc_sel else ""}></td>'
        f'<td><input type="checkbox" name="band_items" value="{_esc(code)}"'
        f'{" checked" if code in band_sel else ""}></td></tr>'
        for code, name, unit in fetch_lab_item_candidates(cur))
    if not lab_rows:
        lab_rows = ('<tr><td colspan="5">ไม่พบรหัส lab ที่ชื่อเข้าเกณฑ์ WBC/Band — ข้ามได้ '
                   "(SIRS จะไม่นับข้อ WBC/Band)</td></tr>")

    err_html = f'<div class="err">{_esc(error)}</div>' if error else ""
    body = f"""
<h1>เลือกค่าตั้งค่า</h1>
<p class="hint">เชื่อมต่อ {_esc(conn_info['user'])}@{_esc(conn_info['host'])}/{_esc(conn_info['db'])} สำเร็จ ·
script ปัจจุบัน: {_esc(k)}</p>
{err_html}
<form method="post" action="/preview">
<h2>แผนกห้องฉุกเฉิน (ER) — ปกติติ๊กแผนกเดียว</h2>
<div class="warn" style="background:#eef4ff;border-color:#b8cdf5;color:#1f3a6b">
หน้าต่างเตือนมี <b>2 แบบ</b> แยกตาม<b>แผนกที่ผู้ใช้ล็อกอินตอนบันทึกซักประวัติ</b>:
<ul style="margin:8px 0 0 18px;padding:0">
<li><b>แผนกที่ติ๊ก (ห้องฉุกเฉิน)</b> → หน้าต่างน้ำเงิน "SEPSIS ALERT (ER) : เริ่ม Bundle ทันที" + เวลาเป้าหมาย 1 ชม.</li>
<li><b>แผนกอื่นทั้งหมด</b> (จุดซักประวัติ OPD, ARI, คลินิก) → หน้าต่างแดง "เตือนสงสัย SEPSIS : ส่ง ER ทันที"</li>
</ul>
ติ๊กเฉพาะแผนกห้องฉุกเฉินจริงที่พยาบาล ER ล็อกอิน — <b>อย่าติ๊กจุดซักประวัติ OPD</b> แม้จะมี visit ER ในตาราง
(คือผู้ป่วยที่คัดกรองที่ OPD แล้วส่งไป ER ซึ่งจุดนั้นต้องได้หน้าต่างแดง)
</div>
<table><tr><th></th><th>รหัสแผนก</th><th>ชื่อแผนก</th><th>visit ER 30 วัน</th><th>คำแนะนำ</th><th>HOSxP รัน script</th></tr>
{dep_rows}
</table>
{allow_html}

<h2>รหัส lab สำหรับ SIRS (WBC / Band %) — ไม่บังคับเลือก</h2>
<table><tr><th>รหัส</th><th>ชื่อ lab</th><th>หน่วย</th><th>WBC</th><th>Band</th></tr>
{lab_rows}
</table>

<details><summary>ตั้งค่าขั้นสูง (ปรับได้ ไม่บังคับ)</summary>
  <label>เตือนแยกเมื่อ MEWS >= <input type="number" name="mews_alert" value="{_esc(picked.get('mews_alert', 5))}"></label>
  <label>เตือนแยกเมื่อ NEWS2 >= <input type="number" name="news2_alert" value="{_esc(picked.get('news2_alert', 5))}"></label>
  <label>เตือนแยกเมื่อ SpO2 ต่ำกว่า (%) <input type="number" name="spo2_alert" value="{_esc(picked.get('spo2_alert', 92))}"></label>
  <label>ชื่อหน้าต่าง HOSxP ที่ไม่ต้องเตือน (คั่นด้วยจุลภาค)
    <input type="text" name="skip_forms" value="{_esc(picked.get('skip_forms', SKIP_FORMS))}"></label>
  <label><input type="checkbox" name="replace" value="1"{" checked" if picked.get("replace") else ""}>
    แทนที่ script เดิมทั้งหมด (ใช้เมื่อ script เดิมของ รพ. ไม่ใช่แบบที่ระบบนี้รู้จัก)</label>
</details>

<button type="submit">ดูตัวอย่างก่อนติดตั้งจริง</button>
</form>
<p><a href="/">← เชื่อมต่อฐานอื่น</a></p>"""
    return _wiz_page("เลือกค่าตั้งค่า", body)


def _preview_page(args, lines, k, already_same, warn_replace, stats, dep_names=None):
    dep_names = dep_names or {}
    er_deps = [d for d in args.er_deps.split(",") if d]
    er_list = ", ".join(f"{d} {dep_names.get(d, '')}".strip() for d in er_deps)
    dep_warn = "".join(f'<div class="warn">{_esc(w)}</div>' for w in er_dep_warnings(er_deps, dep_names))
    allow_line = ('<p class="hint">จะเพิ่มแผนก ER ที่ยังไม่อยู่ใน depcode_vn_script_allow ให้ด้วย</p>'
                  if getattr(args, "add_allow", False) else "")
    lines_html = "\n".join(_esc(ln) for ln in lines)
    stat_line = (f"ประมาณการจากข้อมูล 90 วัน: สงสัย Sepsis ~{stats['est'] / 90:.1f} ครั้ง/วัน · "
                f"MEWS >= 5 ~{stats['ge5'] / 90:.1f} ครั้ง/วัน")
    warn_html = ""
    if already_same:
        warn_html = '<div class="warn">script ฉบับนี้ติดตั้งอยู่แล้ว — กดยืนยันจะไม่มีอะไรเปลี่ยน</div>'
    elif warn_replace:
        warn_html = ('<div class="warn">script เดิมของ รพ. ไม่ใช่แบบที่ระบบนี้รู้จัก — ถ้าตั้งใจแทนที่ทั้งหมด '
                     'ย้อนไปติ๊ก "แทนที่ script เดิมทั้งหมด" ก่อน ไม่งั้นกดยืนยันจะไม่ผ่าน</div>')

    def hid(name, value):
        return f'<input type="hidden" name="{name}" value="{_esc(value)}">'

    hidden = "".join([
        "".join(hid("er_deps", c) for c in args.er_deps.split(",") if c),
        "".join(hid("wbc_items", c) for c in args.wbc_items.split(",") if c),
        "".join(hid("band_items", c) for c in args.band_items.split(",") if c),
        hid("mews_alert", args.mews_alert), hid("spo2_alert", args.spo2_alert),
        hid("news2_alert", args.news2_alert),
        hid("skip_forms", args.skip_forms), hid("replace", "1" if args.replace else ""),
        hid("add_allow", "1" if getattr(args, "add_allow", False) else ""),
    ])
    body = f"""
<h1>ตรวจสอบก่อนติดตั้งจริง</h1>
{target_banner(args.host, args.db)}
<p><b>หน้าต่าง ER</b> (น้ำเงิน เริ่ม Bundle): แผนก {_esc(er_list or "-")} ·
<b>หน้าต่างแดง</b> (ส่ง ER ทันที): แผนกอื่นทั้งหมด</p>
{dep_warn}{allow_line}
<p class="hint">script ปัจจุบัน: {_esc(k)} · {_esc(stat_line)}</p>
<pre>{lines_html}</pre>
{warn_html}
<form method="post" action="/install">
{hidden}
<button type="submit">ยืนยันเขียนจริง</button>
</form>
<p><a href="javascript:history.back()">← กลับไปแก้ค่า</a></p>"""
    return _wiz_page("ตรวจสอบก่อนติดตั้ง", body)


def _result_page(output_text):
    body = f"""
<h1>ผลการติดตั้ง</h1>
<pre>{_esc(output_text)}</pre>
<p class="hint">ปิดแท็บนี้ได้แล้วกด Ctrl+C ที่ terminal เพื่อหยุดวิซาร์ด</p>"""
    return _wiz_page("ผลการติดตั้ง", body)


def _build_wizard_args(form):
    w = _WIZ
    return SimpleNamespace(
        host=w["host"], port=w["port"], db=w["db"], user=w["user"],
        er_deps=",".join(form.get("er_deps", [])),
        mews_alert=_int(form, "mews_alert", 5),
        news2_alert=_int(form, "news2_alert", 5),
        spo2_alert=_int(form, "spo2_alert", 92),
        wbc_items=",".join(form.get("wbc_items", [])),
        band_items=",".join(form.get("band_items", [])),
        backup=None, replace=bool(_first(form, "replace")), force=False,
        yes=True, skip_forms=_first(form, "skip_forms", SKIP_FORMS) or SKIP_FORMS,
        probe=False, add_allow=bool(_first(form, "add_allow")))


class WizardHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        pass   # เงียบ ไม่ปนกับข้อความบน terminal

    def _send(self, body, status=200):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            self._send(_connect_page())
        else:
            self._send(_wiz_page("ไม่พบหน้านี้", "<p>ไม่พบหน้านี้ — <a href='/'>กลับหน้าแรก</a></p>"), 404)

    def do_POST(self):
        form = _form(self)
        try:
            {"/connect": self._handle_connect, "/preview": self._handle_preview,
             "/install": self._handle_install}.get(
                self.path, lambda f: self._send(_wiz_page("ไม่พบหน้านี้", "<p>ไม่พบหน้านี้</p>"), 404))(form)
        except Exception as exc:   # กันวิซาร์ดล้มทั้งตัวจาก error ที่ไม่คาดคิด (เช่น หลุดการเชื่อมต่อฐาน)
            self._send(_wiz_page("ผิดพลาด", f"<div class='err'>{_esc(exc)}</div><p><a href='/'>กลับหน้าแรก</a></p>"), 500)

    def _handle_connect(self, form):
        host = _first(form, "host")
        port = _int(form, "port", 3306)
        db = _first(form, "db", "hos") or "hos"
        user = _first(form, "user")
        password = _first(form, "password")
        try:
            conn = pymysql_connect(host, port, user, password, db)
        except ValueError as exc:
            self._send(_connect_page(error=str(exc), host=host, port=port, db=db, user=user))
            return
        except Exception as exc:
            self._send(_connect_page(error=f"เชื่อมต่อไม่ได้: {exc}", host=host, port=port, db=db, user=user))
            return
        old = _WIZ.get("conn")
        if old:
            try:
                old.close()
            except Exception:
                pass
        _WIZ.clear()
        _WIZ.update(host=host, port=port, db=db, user=user, password=password, conn=conn)
        self._send(_select_page(conn.cursor(), _WIZ))

    def _handle_preview(self, form):
        conn = _WIZ.get("conn")
        if not conn:
            self._send(_connect_page(error="การเชื่อมต่อหมดอายุ กรุณาเชื่อมต่อใหม่"))
            return
        cur = conn.cursor()
        args = _build_wizard_args(form)
        picked = dict(er_deps=form.get("er_deps", []), wbc_items=form.get("wbc_items", []),
                     band_items=form.get("band_items", []), mews_alert=args.mews_alert,
                     news2_alert=args.news2_alert, spo2_alert=args.spo2_alert, skip_forms=args.skip_forms,
                     replace=args.replace, add_allow=args.add_allow)
        cols = opdscreen_cols(cur)
        avpu = "consciousness_level_type_id" in cols
        er_gcs = {"gcs_e", "gcs_v", "gcs_m"} <= table_cols(cur, "er_nursing_detail")
        try:
            new_script = build_script(args.er_deps, avpu=avpu, mews_alert=args.mews_alert,
                                      spo2_alert=args.spo2_alert, spo2_cols=cols, er_gcs=er_gcs,
                                      wbc_items=args.wbc_items, band_items=args.band_items,
                                      skip_forms=args.skip_forms, news2_alert=args.news2_alert)
        except SystemExit as exc:
            self._send(_select_page(cur, _WIZ, error=str(exc.code), picked=picked))
            return
        row = current(cur)
        k = kind(row[1] if row else None)
        already_same = bool(row and row[1].replace("\r\n", "\n") == new_script.replace("\r\n", "\n"))
        warn_replace = k.startswith("script อื่น") and not args.replace
        lines = install_summary_lines(args, cols, avpu, er_gcs)
        stats = estimate_alerts(cur, avpu)
        names = {d: n or "" for d, n, _ in fetch_er_dep_candidates(cur)}
        self._send(_preview_page(args, lines, k, already_same, warn_replace, stats, names))

    def _handle_install(self, form):
        if not _WIZ.get("conn"):
            self._send(_connect_page(error="การเชื่อมต่อหมดอายุ กรุณาเชื่อมต่อใหม่"))
            return
        args = _build_wizard_args(form)
        buf = io.StringIO()
        os.environ["ALERT_DB_PASS"] = _WIZ["password"]
        try:
            with contextlib.redirect_stdout(buf):
                try:
                    cmd_install(args)
                except SystemExit as exc:
                    print(exc.code or "")
        finally:
            os.environ.pop("ALERT_DB_PASS", None)
            conn = _WIZ.pop("conn", None)
            _WIZ.clear()
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
        self._send(_result_page(buf.getvalue()))


def cmd_wizard(args):
    try:
        server = HTTPServer(("127.0.0.1", args.wizard_port), WizardHandler)
    except OSError as exc:
        sys.exit(f"!! เปิดพอร์ต {args.wizard_port} ไม่ได้ ({exc}) — ลองใส่ --wizard-port พอร์ตอื่น")
    url = f"http://127.0.0.1:{args.wizard_port}/"
    print(f"วิซาร์ดเปิดที่ {url} (ถ้าเบราว์เซอร์ไม่เปิดเอง ให้เปิดลิงก์นี้เอง)")
    print("กด Ctrl+C ที่นี่เพื่อหยุดวิซาร์ด")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        conn = _WIZ.pop("conn", None)
        if conn:
            try:
                conn.close()
            except Exception:
                pass
        server.server_close()
        print("\nปิดวิซาร์ดแล้ว")


def main():
    ap = argparse.ArgumentParser(description="ติดตั้งหน้าต่างเตือนสงสัย Sepsis ใน HOSxP (SYS_VN_SCRIPT)")
    ap.add_argument("command", choices=["check", "install", "rollback", "wizard"])
    ap.add_argument("--host")
    ap.add_argument("--port", type=int, default=3306)
    ap.add_argument("--db", default="hos")
    ap.add_argument("--user")
    ap.add_argument("--wizard-port", type=int, default=8787, help="พอร์ตสำหรับเปิดวิซาร์ดในเบราว์เซอร์ (ค่าเริ่มต้น 8787)")
    ap.add_argument("--er-deps", help="รหัสแผนก ER (opdscreen.screen_dep) เช่น 011 หรือ 011,012")
    ap.add_argument("--mews-alert", type=int, default=5, help="เตือนแยกเมื่อ MEWS >= ค่านี้ (0 = ไม่เตือน)")
    ap.add_argument("--news2-alert", type=int, default=5, help="เตือนแยกเมื่อ NEWS2 >= ค่านี้ (0 = ไม่เตือน)")
    ap.add_argument("--spo2-alert", type=int, default=92, help="เตือนแยกเมื่อ SpO2 ต่ำกว่าค่านี้ (0 = ไม่เตือน)")
    ap.add_argument("--wbc-items", default="", help="รหัส lab_items_code ของ WBC สำหรับ SIRS เช่น 35 (ดูจากคำสั่ง check)")
    ap.add_argument("--band-items", default="", help="รหัส lab_items_code ของ Band %% สำหรับ SIRS เช่น 2015")
    ap.add_argument("--add-allow", action="store_true",
                    help="เพิ่มแผนก ER ที่ยังไม่อยู่ใน depcode_vn_script_allow (ไม่งั้น HOSxP ไม่รัน script ที่แผนกนั้น)")
    ap.add_argument("--backup", help="ไฟล์สำรองสำหรับ rollback")
    ap.add_argument("--replace", action="store_true", help="แทนที่ script อื่นของ รพ. ทั้งหมด")
    ap.add_argument("--force", action="store_true", help="rollback แม้ script ปัจจุบันไม่ใช่ของระบบนี้")
    ap.add_argument("--yes", action="store_true", help="ไม่ถามยืนยัน")
    ap.add_argument("--skip-forms", default=SKIP_FORMS,
                    help=f"ชื่อหน้าต่าง HOSxP ที่ไม่ต้องเตือน คั่นด้วยจุลภาค (ดูชื่อด้วย --probe) ค่าเริ่มต้น {SKIP_FORMS}")
    ap.add_argument("--probe", action="store_true", help="ฉบับทดสอบ: ขึ้นชื่อหน้าต่างที่เรียก script (ฐานทดสอบเท่านั้น)")
    args = ap.parse_args()
    if args.command != "wizard" and (not args.host or not args.user):
        ap.error("ต้องระบุ --host และ --user")
    if args.probe and (args.command != "install" or args.db == "hos"):
        ap.error("--probe ใช้กับ install บนฐานทดสอบเท่านั้น (เช่น --db hos_test)")
    if args.command == "install" and not args.er_deps:
        ap.error("install ต้องระบุ --er-deps (ดูคำแนะนำจากคำสั่ง check)")
    if args.command == "rollback" and not args.backup:
        ap.error("rollback ต้องระบุ --backup")
    {"check": cmd_check, "install": cmd_install, "rollback": cmd_rollback,
     "wizard": cmd_wizard}[args.command](args)


if __name__ == "__main__":
    main()
