"""LINE Flex Message สำหรับแจ้งเตือน Sepsis Six Bundle

สไตล์ยึดตาม thai-infographic-slides: พื้นลาเวนเดอร์ การ์ดขาวมุมมน หัวเป็น gradient
KPI tile แบบ gradient และ pill สีตามสถานะ (เขียว = ผ่าน, แดง = ไม่ผ่าน, เทา = ต้องยืนยันเอง)

ไม่มีชื่อผู้ป่วยในข้อความ — ส่งแค่ AN/HN/หอผู้ป่วย/เตียง ตามหลักความเป็นส่วนตัวของ รพ.
"""

from datetime import datetime, timedelta

from config import HOSPITAL_NAME as HOSPITAL

BG = "#f3f0ff"
INK = "#2b2b55"
MUTED = "#8a84b5"
GRAD = {
    "purple": ("#6d4ee8", "#a47bf7"),
    "red": ("#e23b3b", "#f77f7f"),
    "green": ("#0ea877", "#43d6a3"),
    "orange": ("#f08a0b", "#fbbf46"),
}
PILL = {  # (พื้น, ตัวอักษร)
    True: ("#d1fae5", "#047857"),
    False: ("#fee2e2", "#b91c1c"),
    None: ("#eef0f5", "#64748b"),
}
ICON = {True: "✅", False: "❌", None: "➖"}

SIX_ACTIONS = [
    "ให้ออกซิเจน คง SpO₂ ตามเป้า",
    "เก็บ Hemoculture ก่อนให้ยา",
    "ให้ยาปฏิชีวนะทางหลอดเลือด",
    "ให้สารน้ำ IV (crystalloid)",
    "ตรวจระดับ Lactate",
    "ติดตาม Urine Output",
]


# ------------------------------------------------------------ ชิ้นส่วนพื้นฐาน

def _grad(name):
    a, b = GRAD[name]
    return {"type": "linearGradient", "angle": "135deg", "startColor": a, "endColor": b}


def _text(text, **kw):
    return {"type": "text", "text": str(text) if text not in (None, "") else "-", "wrap": True, **kw}


def _pill(text, bg, fg, size="xs"):
    return {
        "type": "box", "layout": "vertical", "flex": 0,
        "backgroundColor": bg, "cornerRadius": "20px",
        "paddingTop": "2px", "paddingBottom": "2px", "paddingStart": "10px", "paddingEnd": "10px",
        "contents": [_text(text, size=size, color=fg, weight="bold", align="center", wrap=False)],
    }


def _card(contents, **kw):
    return {
        "type": "box", "layout": "vertical", "backgroundColor": "#ffffff",
        "cornerRadius": "16px", "paddingAll": "14px", "spacing": "sm",
        "contents": contents, **kw,
    }


def _card_title(icon, title):
    return _text(f"{icon}  {title}", size="sm", weight="bold", color="#5b3fd6")


def _kv(label, value):
    return {
        "type": "box", "layout": "baseline", "spacing": "md",
        "contents": [
            _text(label, size="xs", color=MUTED, flex=3, wrap=False),
            _text(value, size="sm", color=INK, weight="bold", flex=7),
        ],
    }


def _kpi(grad, label, value, unit=""):
    return {
        "type": "box", "layout": "vertical", "flex": 1,
        "background": _grad(grad), "cornerRadius": "14px", "paddingAll": "10px",
        "contents": [
            _text(label, size="xxs", color="#ffffffcc", wrap=False),
            {"type": "box", "layout": "baseline", "spacing": "xs", "contents": [
                _text(value, size="xl", color="#ffffff", weight="bold", flex=0, wrap=False),
                *([_text(unit, size="xs", color="#ffffffdd", flex=0, wrap=False)] if unit else []),
            ]},
        ],
    }


def _header(grad, title, subtitle, test=False):
    contents = [
        {"type": "box", "layout": "horizontal", "contents": [
            _text(HOSPITAL, size="xxs", color="#ffffffcc", flex=1, wrap=False, gravity="center"),
            *([_pill("ข้อความทดสอบ", "#ffffff", "#e23b3b")] if test else []),
        ]},
        _text(title, size="lg", color="#ffffff", weight="bold", margin="sm"),
        {"type": "box", "layout": "horizontal", "margin": "sm", "contents": [
            _pill(subtitle, "#ffffff33", "#ffffff"),
        ]},
    ]
    return {"type": "box", "layout": "vertical", "background": _grad(grad),
            "paddingAll": "16px", "contents": contents}


def _footer(note):
    return {
        "type": "box", "layout": "vertical", "backgroundColor": BG, "paddingAll": "10px",
        "contents": [_text(note, size="xxs", color=MUTED, align="center")],
    }


def _dispo_text(d):
    if not d:
        return "-"
    text = f"{d['icon']} {d['label']}"
    if d["code"] == "refer":
        text += f" → {d.get('detail') or 'ไม่ระบุ รพ.'}"
    if d.get("at"):
        text += f" {d['at'][11:16]} น."
    return text


def _patient_card(an, vn, hn, ward, bed, icd10, time_zero_label, time_zero, dispo=None):
    rows = [
        _card_title("🧾", "ข้อมูลผู้ป่วย"),
        {"type": "separator", "color": "#ece8f8"},
        _kv("AN", an) if an else _kv("VN", vn),
        _kv("HN", hn),
        _kv("หอผู้ป่วย", f"{ward or '-'}" + (f"  ·  เตียง {bed}" if bed and bed != "-" else "")),
        _kv("ICD-10", icd10),
        _kv(time_zero_label, time_zero),
    ]
    if dispo:
        rows.append(_kv("สถานะ", _dispo_text(dispo)))
    return _card(rows)


def _n(x):
    return "–" if x is None else f"{x:g}"


def _vitals_text(v):
    return (f"BP {_n(v.get('sbp'))}/{_n(v.get('dbp'))} · HR {_n(v.get('hr'))} · RR {_n(v.get('rr'))} · "
            f"T {_n(v.get('temp'))} · SpO₂ {_n(v.get('spo2'))}{'%' if v.get('spo2') else ''}")


def _news2_text(n2):
    if not n2:
        return "ไม่มีข้อมูล"
    text = f"{n2['score']}{'+' if n2['partial'] else ''} · {n2['label']}"
    if n2["partial"]:
        text += f" (ขาด {', '.join(n2['missing'])})"
    return text


def _triage_card(t):
    """ประเมินแรกรับตาม SSC 2026: NEWS2, ชีพจร, สัญญาณ shock, เป้าสารน้ำตามน้ำหนัก"""
    v = t["vitals"]
    rows = [_card_title("🫀", "ประเมินแรกรับ (SSC 2026)"), {"type": "separator", "color": "#ece8f8"},
            _kv("NEWS2", _news2_text(t.get("news2"))),
            _kv("ชีพจร", _vitals_text(v))]
    if t.get("shock"):
        rows.append(_kv("Shock", "⚠️ สงสัย (ความดันต่ำ / R57.2) → ยาปฏิชีวนะภายใน 1 ชม."))
    else:
        rows.append(_kv("Shock", "ยังไม่พบความดันต่ำ — ประเมินซ้ำ · วัด Lactate"))
    if t.get("fluid_target_ml"):
        rows.append(_kv("เป้าสารน้ำ", f"30 mL/kg = {t['fluid_target_ml']:,} mL ({_n(v.get('weight'))} kg) "
                                       "ถ้าความดันต่ำหรือ Lactate ≥ 4 · ประเมินซ้ำทุกครั้ง"))
    rows.append(_text("Lactate > 2 → วัดซ้ำภายใน 6 ชม. · ความดันยังต่ำหลังให้สารน้ำ → Norepinephrine (MAP ≥ 65)",
                      size="xxs", color=MUTED))
    return _card(rows)


def _ssc_card(c):
    """สรุปตัวชี้วัด SSC 2026 ในการ์ดผล bundle"""
    rows = [_card_title("🧭", "SSC 2026" + (" · Shock" if c["shock"] else "")),
            {"type": "separator", "color": "#ece8f8"},
            _kv("NEWS2", _news2_text(c.get("news2")))]
    if c["shock"]:
        rows.append(_kv("Shock", " · ".join(c["shock_reasons"])))
    lac = c["lactate"]
    if lac["value"] is not None:
        text = f"{_n(lac['value'])} mmol/L"
        if lac["repeat_needed"]:
            text += (f" · วัดซ้ำ {_n(lac['repeat_value'])} ที่ {lac['repeat_elapsed'] // 60} ชม."
                     if lac["repeat_at"] else " · ❌ ยังไม่วัดซ้ำ")
            if lac["repeat_at"] and not lac["repeat_ok"]:
                text += " (เกิน 6 ชม.)"
        rows.append(_kv("Lactate", text))
    atb = c["atb"]
    rows.append(_kv(f"ยา ≤ {atb['target_min'] // 60} ชม.",
                    ("✅ " if atb["ok"] else "❌ ") + (f"{atb['elapsed_min']} นาที" if atb["elapsed_min"] is not None else "ไม่พบ")))
    f = c["fluid"]
    if f["applies"] and f["target_ml"]:
        given = f"{f['given_ml']:,}" if f["given_ml"] else "ไม่พบ load"
        rows.append(_kv("สารน้ำ 3 ชม.", f"{given} / {f['target_ml']:,} mL" + (f" ({f['pct']}%)" if f["pct"] else "")))
    vp = c.get("vasopressor")
    if vp:
        rows.append(_kv("Vasopressor", f"{vp['agent']}" + (f" · {vp['elapsed_min']} นาที" if vp["elapsed_min"] is not None else "")
                        + (" ⚠️ dopamine ก่อน NE" if vp["ne_first"] is False else "")))
    elif c["shock"]:
        rows.append(_kv("Vasopressor", "ไม่พบการให้"))
    esc = c["escalation"]
    if esc["applies"]:
        rows.append(_kv("Refer/ICU ≤ 6 ชม.", ("✅ " if esc["ok"] else ("❌ " if esc["ok"] is False else "➖ "))
                        + (esc["note"] or "")))
    return _card(rows)


def _bubble(header, body_contents, footer):
    return {
        "type": "bubble", "size": "mega",
        "header": header,
        "body": {"type": "box", "layout": "vertical", "backgroundColor": BG,
                 "paddingAll": "12px", "spacing": "md", "contents": body_contents},
        "footer": footer,
        "styles": {"footer": {"separator": False}},
    }


def _fmt_time(dt_str):
    """'2026-10-16 13:27' -> '16/10 13:27 น.'"""
    try:
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return dt_str or "-"
    return f"{dt:%d/%m} {dt:%H:%M} น."


# -------------------------------------------------------------- 1. เคสใหม่

def new_case(case, time_zero, window_min, test=False, tz_label="เริ่มนับ", now=None, triage=None):
    """case: แถวจาก queries.find_sepsis_cases, time_zero: datetime, triage: queries.triage_info()"""
    deadline = time_zero + timedelta(minutes=window_min)
    tz_str = time_zero.strftime("%Y-%m-%d %H:%M")
    overdue = round(((now or datetime.now()) - deadline).total_seconds() / 60)

    steps = []
    for i, label in enumerate(SIX_ACTIONS, 1):
        steps.append({"type": "box", "layout": "horizontal", "spacing": "md", "contents": [
            {"type": "box", "layout": "vertical", "flex": 0, "width": "22px", "height": "22px",
             "cornerRadius": "11px", "background": _grad("purple"), "justifyContent": "center",
             "contents": [_text(str(i), size="xxs", color="#ffffff", weight="bold", align="center")]},
            _text(label, size="sm", color=INK, gravity="center"),
        ]})

    # ICD มักลงหลังมาถึง ER พักหนึ่ง ระบบจึงอาจเห็นเคสเมื่อเลยเส้นตายแล้ว — บอกตรง ๆ แทนการนับถอยหลังปลอม
    if overdue > 0:
        tiles = [_kpi("red", "เลยกำหนดแล้ว", str(overdue), "นาที"),
                 _kpi("purple", tz_label, f"{time_zero:%H:%M}", "น.")]
        subtitle = "ตรวจพบหลังครบ 1 ชม. — ทำข้อที่ยังขาดทันที"
    else:
        tiles = [_kpi("orange", "ต้องทำครบภายใน", f"{deadline:%H:%M}", "น."),
                 _kpi("purple", "กรอบเวลา", str(window_min), "นาที")]
        subtitle = "เริ่ม Sepsis Six Bundle ทันที"
    body = [
        {"type": "box", "layout": "horizontal", "spacing": "sm", "contents": tiles},
        _patient_card(case.get("an"), case.get("vn"), case.get("hn"),
                      case.get("ward_name") or case.get("ward"), case.get("cur_bedno"),
                      case.get("icd10_list"), tz_label, _fmt_time(tz_str),
                      case.get("disposition")),
        _card([_card_title("⏱️", "Sepsis Six — ทำให้ครบภายใน 1 ชั่วโมง"),
               {"type": "separator", "color": "#ece8f8"}, *steps]),
    ]
    if triage:
        body.insert(2, _triage_card(triage))
    label = "AN" if case.get("an") else "VN"
    when = f"เลยกำหนด {overdue} นาที" if overdue > 0 else f"ทำ Sepsis Six ให้ครบภายใน {deadline:%H:%M} น."
    alt = f"🚨 พบผู้ป่วยสงสัย Sepsis รายใหม่ {label} {case.get('case_key')} — {when}"
    return {
        "type": "flex", "altText": alt[:400],
        "contents": _bubble(
            _header("red", "🚨 สงสัย Sepsis รายใหม่", subtitle, test),
            body,
            _footer("ระบบติดตาม Sepsis Six Bundle · ข้อมูลจาก HOSxP"),
        ),
    }


# ------------------------------------------------- ตัวอย่างสำหรับปุ่มทดสอบส่ง

def sample_messages(window_min=60):
    """การ์ดตัวอย่าง 2 ใบ (เคสใหม่ + ผล bundle) ข้อมูลสมมติทั้งหมด ติดป้าย "ข้อความทดสอบ\""""
    tz = datetime.now().replace(second=0, microsecond=0) - timedelta(minutes=75)
    tz_str = tz.strftime("%Y-%m-%d %H:%M")
    at = lambda m: (tz + timedelta(minutes=m)).strftime("%Y-%m-%d %H:%M")
    case = {"an": "TEST000001", "vn": "TEST0000000001", "case_key": "TEST000001", "hn": "0000000",
            "ward_name": "ตัวอย่าง (ไม่ใช่ผู้ป่วยจริง)", "cur_bedno": "-", "icd10_list": "A41.9"}

    def el(label, minutes, ok, source):
        return {"label": label, "at": at(minutes), "elapsed_min": minutes, "compliant": ok,
                "source": source, "note": None}

    result = {
        "an": case["an"], "vn": case["vn"], "case_key": case["case_key"], "hn": case["hn"],
        "ward_name": case["ward_name"], "bed": "-",
        "icd10": "A41.9", "time_zero": tz_str, "time_zero_mode": "เวลาเข้า ER",
        "bundle_window_min": window_min,
        "elements": {
            "lactate": el("วัดระดับ Lactate", 25, True, "สั่ง Lab"),
            "culture": el("เพาะเชื้อก่อนให้ยา (Hemoculture)", 40, True, "สั่ง Lab"),
            "antibiotic": el("ยาปฏิชีวนะ (Antibiotic)", 72, False, "สั่ง/จ่ายยาที่ ER"),
            "iv_fluid": el("สารน้ำ IV Fluid", 9, True, "สั่ง/จ่ายยาที่ ER"),
            "oxygen": {"label": "ออกซิเจน (SpO2 target)", "at": None, "elapsed_min": None,
                       "compliant": None, "note": None},
            "urine_output": {"label": "ติดตาม Urine Output", "at": None, "elapsed_min": None,
                             "compliant": None, "note": None},
        },
        "culture_before_antibiotic": True,
        "auto_compliant": False,
    }
    vit = {"sbp": 86, "dbp": 50, "hr": 124, "rr": 26, "temp": 38.9, "spo2": 93, "weight": 60}
    n2 = {"score": 11, "label": "สูง", "partial": True, "missing": ["ความรู้สึกตัว"]}
    result["ssc"] = {
        "news2": n2, "shock": True, "shock_reasons": ["SBP แรก 86", "Lactate 4.6"],
        "lactate": {"value": 4.6, "repeat_needed": True, "repeat_at": at(150), "repeat_value": 2.8,
                    "repeat_elapsed": 150, "repeat_ok": True},
        "atb": {"target_min": 60, "elapsed_min": 72, "ok": False},
        "fluid": {"applies": True, "target_ml": 1800, "given_ml": 1500, "pct": 83},
        "vasopressor": {"agent": "Norepinephrine", "elapsed_min": 95, "ne_first": True},
        "escalation": {"applies": True, "ok": True, "note": "Refer → รพ.ตัวอย่าง"},
    }
    triage = {"vitals": vit, "news2": n2, "shock": True, "fluid_target_ml": 1800}
    return [new_case(case, tz, window_min, test=True, tz_label="เวลาเข้า ER",
                     now=tz + timedelta(minutes=20), triage=triage),
            bundle_result(result, test=True)]


# --------------------------------------------------------- 2. ผล bundle

def bundle_result(result, test=False):
    """result: dict จาก queries.bundle_status"""
    els = result["elements"]
    auto_keys = ["lactate", "culture", "antibiotic", "iv_fluid"]
    passed = sum(1 for k in auto_keys if els[k]["compliant"])
    ok = result["auto_compliant"]
    score_grad = "green" if ok else ("orange" if passed >= 2 else "red")

    rows = []
    for el in els.values():
        state = el["compliant"]
        if el["elapsed_min"] is not None:
            pill_txt = f"{el['elapsed_min']} นาที"
        elif state is None:
            pill_txt = "ยืนยันเอง"
        elif state:
            pill_txt = "ผ่าน"
        else:
            pill_txt = "ไม่พบข้อมูล"
        bg, fg = PILL[state]
        label_box = [_text(el["label"], size="sm", color=INK)]
        if el.get("source") and el.get("at"):
            label_box.append(_text(f"{el['source']} {el['at'][11:16]} น.", size="xxs", color=MUTED))
        elif state is not None and el.get("note"):
            label_box.append(_text(el["note"], size="xxs", color=MUTED))
        rows.append({"type": "box", "layout": "horizontal", "spacing": "sm", "paddingTop": "4px",
                     "paddingBottom": "4px", "alignItems": "center", "contents": [
                         _text(ICON[state], size="sm", flex=0, gravity="center", wrap=False),
                         {"type": "box", "layout": "vertical", "flex": 1, "contents": label_box},
                         _pill(pill_txt, bg, fg),
                     ]})

    body = [
        {"type": "box", "layout": "horizontal", "spacing": "sm", "contents": [
            _kpi(score_grad, "ผ่านเกณฑ์ (อัตโนมัติ)", f"{passed}/4", "ข้อ"),
            _kpi("purple", "เป้าหมาย", str(result["bundle_window_min"]), "นาที"),
        ]},
        _patient_card(result.get("an"), result.get("vn"), result["hn"],
                      result.get("ward_name") or result.get("ward"), result.get("bed"),
                      result.get("icd10"), result["time_zero_mode"], _fmt_time(result["time_zero"]),
                      result.get("disposition")),
        _card([_card_title("📋", "รายการ Bundle"), {"type": "separator", "color": "#ece8f8"}, *rows]),
    ]
    if result.get("ssc"):
        body.append(_ssc_card(result["ssc"]))

    if result.get("culture_before_antibiotic") is False:
        body.append({"type": "box", "layout": "vertical", "backgroundColor": "#ffe4e6",
                     "cornerRadius": "12px", "paddingAll": "10px", "borderWidth": "1px",
                     "borderColor": "#fca5a5",
                     "contents": [_text("🚨 ให้ยาปฏิชีวนะก่อนเก็บ Hemoculture", size="sm",
                                        color="#991b1b", weight="bold")]})
    if not ok:
        body.append({"type": "box", "layout": "vertical", "backgroundColor": "#fff7d6",
                     "cornerRadius": "12px", "paddingAll": "10px", "borderWidth": "1px",
                     "borderColor": "#fcd34d",
                     "contents": [_text("⚠️ ไม่ผ่านเกณฑ์ — ควรทบทวนเคสนี้", size="sm",
                                        color="#78520a", weight="bold")]})

    verdict = "ผ่านเกณฑ์" if ok else "ไม่ผ่านเกณฑ์"
    label = "AN" if result.get("an") else "VN"
    alt = f"🔔 ผล Sepsis Six Bundle {label} {result['case_key']}: {verdict} ({passed}/4 ข้อที่ตรวจอัตโนมัติ)"
    return {
        "type": "flex", "altText": alt[:400],
        "contents": _bubble(
            _header("green" if ok else "purple", "🩺 ผล Sepsis Six Bundle",
                    f"{verdict} · {passed}/4 ข้อ", test),
            body,
            _footer("เวลาจากบันทึกพยาบาล ER ก่อน ไม่มีจึงใช้เวลาสั่ง · ➖ = ต้องยืนยันเอง"),
        ),
    }
