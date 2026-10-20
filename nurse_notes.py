"""อ่านเวลาให้ยา/สารน้ำ/ออกซิเจน จากบันทึกพยาบาล ER ที่เขียนเป็นข้อความอิสระ (opdscreen.hpi)

รูปแบบที่พบจริง (ต.ค. 2026) — รายการคั่นด้วย " - " และมักมีเวลากำกับ:
    รายงานแพทย์ at 11.58 น. - lactate - H/C*2 - 0.9%NaCl 1000 ml iv drip 60 cc/hr at 12.15 น.
    - Cef -3 2 g iv drip at 12.40 น. ...   /   Clindamycin 600 mg iv drip stat 02.50 น.
    / 15.00 น ให้ยา Cef -3 2 gm v   /   On O2 canular 3 LPM >> SpO2 97 %

นับเป็นเวลาเฉพาะเมื่อมี at / stat / เวลา นำหน้า หรือ น. ต่อท้าย — กันตัวเลขอย่าง
0.9%NaCl, Cr 6.14, INR 2.91 ถูกอ่านเป็นเวลา
"""

import re
from datetime import datetime, timedelta

ANTIBIOTIC = re.compile(
    r"\b(cef\w*|ceftri\w*|clinda\w*|metro\w*|merop\w*|ertap\w*|imipen\w*|tazo\w*|piperacil\w*|"
    r"augm\w*|amox\w*|ampi\w*|unasyn|sulbactam|cloxa\w*|genta\w*|amika\w*|vanco\w*|azithro\w*|"
    r"roxi\w*|clarith\w*|doxy\w*|levo(?!phed)\w*|cipro\w*|oflox\w*|cotrimox\w*|bactrim|penicillin\w*|pgs|lincomycin)",
    re.I)
FLUID = re.compile(r"(0\.9\s*%?\s*(nacl|nss)|\bnacl\b|\bnss\b|\blrs\b|\brls\b|ringer|acetar|sterofundin)", re.I)
IV_ROUTE = re.compile(r"\b(iv|drip|load|bolus|ml\s*/\s*hr|cc\s*/\s*hr|free\s*flow)", re.I)
CULTURE = re.compile(r"(\bh\s*/\s*c\b|hemo\s*c\w*)", re.I)
OXYGEN = re.compile(
    r"(on\s*o2|o2\s*(canu|cannu|mask|nasal)|nasal\s*cann\w*|mask\s*c\s*bag|hfnc|high\s*flow|"
    r"\bet[\s-]*tube|\bett\b|intubat\w*|ventilator|on\s*vent|bag[\s-]*mask)", re.I)
URINE = re.compile(r"(foley|สวนปัสสาวะ|retain(ed)?\s*(foley|cath)|urine\s*output|\bu\s*/\s*o\b|\bi\s*/\s*o\b)", re.I)
# ยากระตุ้นความดัน — norepinephrine ก่อน epinephrine เพราะชื่อซ้อนกัน
VASOPRESSOR = re.compile(r"\b(nor[\s-]?epi\w*|levophed|noradrenalin\w*|dopamine|adrenalin\w*|epinephrine)", re.I)
BALANCED = re.compile(r"(\blrs\b|\brls\b|ringer|acetar|sterofundin|plasma[\s-]*lyte)", re.I)
VOL = r"(\d{1,2},\d{3}|\d{2,4})\s*(?:ml|cc)"
# ปริมาณ load/bolus ที่สั่งให้ เช่น "iv load 1,500 ml", "Load เพิ่ม 500 cc" (ไม่นับ rate เช่น 80 cc/hr)
LOAD_VOL = re.compile(r"(?:load|bolus|free\s*flow)\D{0,25}?" + VOL, re.I)
# ยอดรวม เช่น "total load 1500 cc", "load เพิ่มให้ครบ 1,000 ml"
TOTAL_VOL = re.compile(r"(?:total\s*(?:iv\s*)?load|ให้ครบ)\D{0,10}?" + VOL, re.I)
AFTER = re.compile(r"^\s*หลัง")   # "หลัง Load ครบ 500cc" = รายงานว่าให้ครบแล้ว ไม่ใช่สั่งเพิ่ม
CARRIER = re.compile(r"\+\s*\S*\s*(nss|nacl|5\s*dw|d5w)|\b(50|100)\s*(ml|cc)\b(?!\s*/)", re.I)
BP = re.compile(r"\bBP\s*[:=]?\s*(\d{2,3})\s*/\s*(\d{2,3})", re.I)
SPO2 = re.compile(r"(?:o2\s*sat|spo\s*2)\s*:?\s*(\d{2,3})\s*%", re.I)

TIME = re.compile(
    r"(?P<pre>\b(?:at|stat)\s*\.{0,3}\s*|เวลา\s*\.{0,3}\s*)?"
    r"(?<![\d.,/])(?P<h>[01]?\d|2[0-3])[.:](?P<m>[0-5]\d)(?![\d%/])"
    r"(?P<suf>\s*\.{0,3}\s*น)?", re.I)
# ตัดที่ " - " หรือ " -คำ" แต่ไม่ตัด " -3" ในชื่อยา "Cef -3"
SEGMENT_SPLIT = re.compile(r"\s+-(?=\s)|\s+-(?=[^\d\s])")


def _times(segment):
    """[(ตำแหน่ง, ชั่วโมง, นาที)] เฉพาะที่มีคำนำหน้า/น. ยืนยันว่าเป็นเวลา"""
    return [(m.start("h"), int(m.group("h")), int(m.group("m")))
            for m in TIME.finditer(segment) if m.group("pre") or m.group("suf")]


def _time_for(segment, kw_start):
    """เวลาแรกหลังคำค้นในรายการเดียวกัน หรือเวลาที่เขียนนำหน้าคำค้นไม่เกิน 30 ตัวอักษร"""
    times = _times(segment)
    after = [t for t in times if t[0] > kw_start]
    if after:
        return after[0][1:]
    before = [t for t in times if kw_start - 30 <= t[0] < kw_start]
    return before[-1][1:] if before else None


def _to_dt(hm, tz):
    dt = datetime.combine(tz.date(), datetime.min.time()).replace(hour=hm[0], minute=hm[1])
    if dt < tz - timedelta(hours=3):   # ข้ามเที่ยงคืน เช่นมาถึง 23:50 ให้ยา 00:30
        dt += timedelta(days=1)
    return dt


CLIP_STOP = re.compile(r"\s(?:V/S|แพทย์|อธิบาย|>>)")


def _clip(segment, start, width=80):
    """ข้อความต้นทางให้คนตรวจทาน: เริ่มที่คำค้น หยุดก่อน V/S / แพทย์ / >> ถัดไป"""
    text = segment[start:]
    stop = CLIP_STOP.search(text, 1)
    if stop:
        text = text[:stop.start()]
    text = text.strip(" -")
    return text if len(text) <= width else text[:width].rstrip() + "…"


def _ml(s):
    return int(s.replace(",", ""))


def _vasopressor_agent(word):
    w = word.lower()
    if w.startswith(("nor", "levophed")):
        return "Norepinephrine"
    if w.startswith("dopa"):
        return "Dopamine"
    return "Adrenaline"


def parse(text, tz, fluid_hours=3):
    """คืน dict ของสิ่งที่พบในบันทึก: แต่ละรายการมี dt (หรือ None) + snippet ข้อความต้นทาง

    เพิ่มเติม: vasopressor (+agent), fluid_bolus_ml (load ภายใน fluid_hours ชม.), fluid_types
    ({"saline","balanced"}), min_sbp (BP ต่ำสุดที่บันทึก), first_spo2
    """
    text = re.sub(r"\s+", " ", text or "")
    found = {}

    def record(key, segment, m, need_iv=False):
        if need_iv and not IV_ROUTE.search(segment):
            return
        hm = _time_for(segment, m.start())
        dt = _to_dt(hm, tz) if hm else None
        cur = found.get(key)
        # เก็บเวลาที่เร็วที่สุด; รายการมีเวลาชนะรายการไม่มีเวลา
        if cur is None or (dt and (cur["dt"] is None or dt < cur["dt"])):
            found[key] = {"dt": dt, "snippet": _clip(segment, m.start())}

    bolus, total, types = 0, 0, set()
    fluid_end = tz + timedelta(hours=fluid_hours)
    for segment in SEGMENT_SPLIT.split(text):
        for key, rx, need_iv in (("antibiotic", ANTIBIOTIC, False), ("iv_fluid", FLUID, True),
                                 ("culture", CULTURE, False), ("oxygen", OXYGEN, False),
                                 ("urine_output", URINE, False), ("vasopressor", VASOPRESSOR, False)):
            m = rx.search(segment)
            if m:
                record(key, segment, m, need_iv)
                if key == "vasopressor" and found.get(key) and "agent" not in found[key]:
                    found[key]["agent"] = _vasopressor_agent(m.group(1))

        if VASOPRESSOR.search(segment) or AFTER.search(segment):
            continue
        fm = FLUID.search(segment)
        if fm and IV_ROUTE.search(segment) and not CARRIER.search(segment):
            types.add("balanced" if BALANCED.search(segment) else "saline")
        lm = LOAD_VOL.search(segment)
        if lm:
            hm = _time_for(segment, lm.start())
            if hm is None or _to_dt(hm, tz) <= fluid_end:
                tm = TOTAL_VOL.search(segment)
                if tm:
                    total = max(total, _ml(tm.group(1)))
                    if tm.start() >= lm.end():   # "load เพิ่ม 500 cc. total load 1500 cc."
                        bolus += _ml(lm.group(1))
                else:
                    bolus += _ml(lm.group(1))
    if bolus or total:
        found["fluid_bolus_ml"] = max(bolus, total)
    if types:
        found["fluid_types"] = types
    sbps = [int(m.group(1)) for m in BP.finditer(text) if 40 <= int(m.group(1)) <= 260]
    if sbps:
        found["min_sbp"] = min(sbps)

    spo2 = SPO2.search(text)
    if spo2:
        found["first_spo2"] = int(spo2.group(1))
    return found
