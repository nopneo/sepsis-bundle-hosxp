"""คำนวณ SOS Score (สูตรเดิมของ รพ.วังน้ำเขียว) และเกณฑ์ไข้/ตัวเย็น

สูตรเดียวกับ SQL ของหน้าต่างเตือน ER (ดู hosxp_alert/) ใช้ร่วมกันระหว่าง
  - ward_watch.py  — เฝ้าระวังหอผู้ป่วยจาก ipd_nurse_note (SOS > 3 ร่วมกับไข้/ตัวเย็น)
  - queries.py     — หาเวลา SOS เข้าเกณฑ์ที่ ER จาก er_regist_vital_sign_list สำหรับ time-zero
                      ของ Sepsis Six Bundle (ตรงกับที่หน้าต่างเตือน ER ใช้เริ่มนับ bundle)
แก้สูตรที่นี่ที่เดียว ทั้งสองจุดจะใช้ตรงกันเสมอ
"""


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x > 0 else None


def sos_parts(t, sbp, hr, rr):
    """{T, BP, HR, RR: คะแนน} — ลำดับเงื่อนไขตาม SQL ของหน้าต่างเตือน ER ทุกบรรทัด (ค่าว่างได้ 0)"""
    t, sbp, hr, rr = (_num(x) for x in (t, sbp, hr, rr))
    if t is None or t <= 20 or 36.1 <= t <= 38:
        pt = 0
    elif 35.1 <= t <= 36 or 38.1 <= t <= 38.4:
        pt = 1
    elif t <= 35 or t >= 38.5:
        pt = 2
    else:
        pt = 0
    if sbp is None or sbp <= 50 or 101 <= sbp <= 180:
        pb = 0
    elif 91 <= sbp <= 100 or 181 <= sbp <= 199:
        pb = 1
    elif 81 <= sbp <= 90 or sbp >= 200:
        pb = 2
    elif sbp <= 80:
        pb = 3
    else:
        pb = 0
    if hr is None or hr <= 20 or 51 <= hr <= 100:
        ph = 0
    elif 41 <= hr <= 50 or 100 <= hr <= 120:
        ph = 1
    elif 121 <= hr <= 139:
        ph = 2
    elif hr <= 40 or hr >= 140:
        ph = 3
    else:
        ph = 0
    if rr is None or rr <= 2 or 9 <= rr <= 20:
        pr = 0
    elif 21 <= rr <= 25:
        pr = 1
    elif 26 <= rr <= 35:
        pr = 2
    elif rr >= 35 or rr <= 8:
        pr = 3
    else:
        pr = 0
    return {"T": pt, "BP": pb, "HR": ph, "RR": pr}


def fever(t):
    """ไข้ ≥ 38 หรือตัวเย็น < 36 (ค่า ≤ 20 = กรอกผิด ไม่นับ)"""
    t = _num(t)
    return t is not None and (t >= 38 or 20 < t < 36)
