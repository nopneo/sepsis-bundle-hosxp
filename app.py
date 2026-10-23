"""Sepsis Six Bundle Dashboard (HOSxP)

รันเอง:      python app.py                      → http://127.0.0.1:5500/
production:  gunicorn -b 0.0.0.0:5500 app:app    (แนะนำให้ใช้ในวง intranet เท่านั้น
                                                    ไม่ควรเปิดผ่าน sub-path 443 ออกนอก รพ.
                                                    เพราะเป็นข้อมูลผู้ป่วย)
"""

import hmac
import ipaddress
import os
import secrets
import time
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit

from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

import config
import db
import flex
import login_guard
import moph_notify
import queries


class PrefixMiddleware:
    """รองรับการวางแอปไว้หลัง reverse proxy แบบ sub-path (ถ้าจำเป็นต้องทำในอนาคต)"""

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        prefix = environ.get("HTTP_X_FORWARDED_PREFIX", "")
        if prefix:
            environ["SCRIPT_NAME"] = prefix.rstrip("/")
            path = environ.get("PATH_INFO", "")
            if path.startswith(prefix):
                environ["PATH_INFO"] = path[len(prefix):] or "/"
        proto = environ.get("HTTP_X_FORWARDED_PROTO")
        if proto:
            environ["wsgi.url_scheme"] = proto
        return self.wsgi_app(environ, start_response)


SECRET_KEY_PATH = os.path.join(config.BASE_DIR, "data", "secret_key")


def _secret_key():
    """กุญแจเซ็น cookie — ใครรู้ค่านี้ปลอม cookie "ปลดล็อกแล้ว" ได้เองโดยไม่ต้องรู้รหัส

    ใช้ SEPSIS_SECRET_KEY ถ้าตั้งไว้ (ต้องยาวอย่างน้อย 32 ตัวอักษร) ไม่งั้นสุ่มครั้งแรกแล้วเก็บที่
    data/secret_key (สิทธิ์ 600) — ทุก worker ใช้ค่าเดียวกัน และไม่เปลี่ยนเมื่อรีสตาร์ต
    """
    env = os.getenv("SEPSIS_SECRET_KEY", "").strip()
    if env:
        if len(env) < 32:
            raise RuntimeError("SEPSIS_SECRET_KEY สั้นเกินไป (ต้อง ≥ 32 ตัวอักษร) — "
                               "สุ่มใหม่: python -c \"import secrets; print(secrets.token_hex(32))\" "
                               "หรือลบบรรทัดนี้ออกให้ระบบสุ่มเก็บไว้ใน data/secret_key เอง")
        return env
    os.makedirs(os.path.dirname(SECRET_KEY_PATH), exist_ok=True)
    try:
        fd = os.open(SECRET_KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass                    # worker อื่นสร้างไว้แล้ว
    else:
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32))
    for _ in range(50):         # worker อื่นอาจสร้างไฟล์แล้วแต่ยังเขียนไม่เสร็จ
        key = open(SECRET_KEY_PATH, encoding="ascii").read().strip()
        if key:
            return key
        time.sleep(0.05)
    raise RuntimeError(f"{SECRET_KEY_PATH} ว่าง — ลบไฟล์แล้วรีสตาร์ตแอป")


app = Flask(__name__)
app.wsgi_app = PrefixMiddleware(app.wsgi_app)
app.secret_key = _secret_key()
app.permanent_session_lifetime = timedelta(hours=int(os.getenv("SEPSIS_UNLOCK_HOURS", "8")))
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")


@app.after_request
def _security_headers(resp):
    """ห้ามฝังหน้าในเว็บอื่น (clickjacking), ห้ามเดาชนิดไฟล์, ไม่ส่ง URL ไปเว็บอื่น
    และห้ามเก็บหน้าที่มีข้อมูลผู้ป่วยไว้ใน cache ของเบราว์เซอร์/proxy"""
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if not request.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp

# รหัสดู HN/AN — เก็บเฉพาะ hash (generate_password_hash(pw, method="pbkdf2:sha256")) ไม่เก็บรหัสจริง
# ไม่ตั้งค่า = แสดง HN/AN ตามปกติ (แบบเดิม)
VIEW_PASSWORD_HASH = os.getenv("SEPSIS_VIEW_PASSWORD_HASH", "").strip()
MASK = "xxx"


def unlocked():
    return not VIEW_PASSWORD_HASH or session.get("unlocked") is True


def masked(results):
    """ซ่อน HN/AN/VN จนกว่าจะกรอกรหัส"""
    if unlocked():
        return results
    return [dict(r, hn=MASK, vn=MASK, case_key=MASK, an=MASK if r.get("an") else r.get("an")) for r in results]


@app.context_processor
def lock_state():
    return {"locked": not unlocked(), "lock_enabled": bool(VIEW_PASSWORD_HASH)}


@app.teardown_appcontext
def _close_db(_exc):
    """คืน connection ของ request นี้ (db.py ใช้ connection เดียวทุกคำสั่งใน request)"""
    db.close()


@app.context_processor
def hospital_name():
    return {"hospital": config.HOSPITAL_NAME}


TH_MONTH = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
            "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


@app.template_filter("thdt")
def thai_datetime(value):
    """'2026-10-16 12:23' หรือ datetime -> '16 ต.ค. 69 · 12:23 น.'"""
    dt = value if isinstance(value, datetime) else datetime.strptime(str(value)[:16], "%Y-%m-%d %H:%M")
    return f"{dt.day} {TH_MONTH[dt.month - 1]} {(dt.year + 543) % 100:02d} · {dt:%H:%M} น."


@app.template_filter("n")
def num_text(value):
    """3.40 -> '3.4', 88.0 -> '88', None -> '–'"""
    return "–" if value is None else f"{value:g}"


@app.template_filter("ml")
def ml_text(value):
    return "–" if value is None else f"{value:,.0f}"


MAX_RANGE_DAYS = 366  # หนึ่งปีงบประมาณ — นานกว่านี้หน้าเว็บช้าเกิน timeout ของ gunicorn


def _th_date(d):
    return f"{d.day} {TH_MONTH[d.month - 1]} {(d.year + 543) % 100:02d}"


def period_label(d1, d2):
    """1–17 ต.ค. 69 / 25 ก.ย. 69 – 17 ต.ค. 69 / 17 ต.ค. 69"""
    if d1 == d2:
        return _th_date(d1)
    if (d1.year, d1.month) == (d2.year, d2.month):
        return f"{d1.day}–{_th_date(d2)}"
    return f"{_th_date(d1)} – {_th_date(d2)}"


def quick_ranges(today):
    first = today.replace(day=1)
    last_month_end = first - timedelta(days=1)
    fiscal_start = date(today.year if today.month >= 10 else today.year - 1, 10, 1)
    return [
        ("วันนี้", today, today),
        ("7 วัน", today - timedelta(days=6), today),
        ("30 วัน", today - timedelta(days=29), today),
        ("เดือนนี้", first, today),
        ("เดือนที่แล้ว", last_month_end.replace(day=1), last_month_end),
        ("ปีงบนี้", fiscal_start, today),
    ]


def _date_range():
    """อ่าน ?d1=YYYY-MM-DD&d2=YYYY-MM-DD (หรือ ?days=N แบบเดิม) — ค่าผิดใช้ 7 วันล่าสุด"""
    today = date.today()
    notice = None
    try:
        d1 = date.fromisoformat(request.args["d1"])
        d2 = date.fromisoformat(request.args["d2"])
    except (KeyError, ValueError):
        if request.args.get("d1") or request.args.get("d2"):
            notice = "รูปแบบวันที่ไม่ถูกต้อง — แสดง 7 วันล่าสุดแทน"
        try:
            days = max(1, min(int(request.args.get("days", 7)), MAX_RANGE_DAYS))
        except ValueError:
            days = 7
        d1, d2 = today - timedelta(days=days - 1), today
    if d1 > d2:
        d1, d2 = d2, d1
    if (d2 - d1).days >= MAX_RANGE_DAYS:
        d1 = d2 - timedelta(days=MAX_RANGE_DAYS - 1)
        notice = f"ช่วงยาวเกิน {MAX_RANGE_DAYS} วัน — ตัดให้เหลือ {period_label(d1, d2)}"
    return d1, d2, notice


@app.route("/")
def dashboard():
    session.pop("settings_until", None)  # ออกจากหน้าตั้งค่าแล้ว — เข้าใหม่ต้องกรอกรหัสอีกครั้ง
    d1, d2, notice = _date_range()
    results = queries.build(d1=d1, d2=d2)
    today = date.today()
    return render_template("dashboard.html", results=masked(results), s=queries.summary(results),
                           d1=d1, d2=d2, today=today, period=period_label(d1, d2),
                           quick=quick_ranges(today), notice=notice, updated=datetime.now(),
                           lac_h=queries.LACTATE_REPEAT_HOURS)


@app.route("/api/data.json")
def api_data():
    d1, d2, _ = _date_range()
    return jsonify(masked(queries.build(d1=d1, d2=d2)))


def _safe_next():
    nxt = request.values.get("next", "")
    return nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("dashboard")


#: หน้าตั้งค่าถามรหัสทุกครั้งที่เข้า — ปลดล็อกแล้วใช้ได้ช่วงสั้น ๆ พอกรอก/ทดสอบส่ง (แยกจากการปลดดู HN/AN)
SETTINGS_UNLOCK_SECONDS = 10 * 60


def settings_unlocked():
    return session.get("settings_until", 0) > time.time()


#: รหัสหน้าตั้งค่าแจ้งเตือน — แยกจากรหัสดู HN/AN ที่แจกให้เจ้าหน้าที่หลายคน
#: (ใครเข้าหน้านี้ได้ก็เปลี่ยน key ของ MOPH Notify จนการแจ้งเตือนเคส sepsis หยุดหรือไปผิดกลุ่มได้)
#: SEPSIS_SETTINGS_PASSWORD_HASH (แนะนำ) หรือ SEPSIS_SETTINGS_PASSWORD (รหัสตรง ๆ แบบเดิม)
#: ไม่ตั้งทั้งคู่ = เปิดหน้าตั้งค่าได้เฉพาะจากเครื่องเซิร์ฟเวอร์เอง
SETTINGS_PASSWORD_HASH = os.getenv("SEPSIS_SETTINGS_PASSWORD_HASH", "").strip()
SETTINGS_PASSWORD = os.getenv("SEPSIS_SETTINGS_PASSWORD", "")


def settings_password_set():
    return bool(SETTINGS_PASSWORD_HASH or SETTINGS_PASSWORD)


def _password_ok(scope, password):
    if not password:
        return False
    if scope == "settings":
        if SETTINGS_PASSWORD_HASH:
            return check_password_hash(SETTINGS_PASSWORD_HASH, password)
        return bool(SETTINGS_PASSWORD) and hmac.compare_digest(password.encode(), SETTINGS_PASSWORD.encode())
    return bool(VIEW_PASSWORD_HASH) and check_password_hash(VIEW_PASSWORD_HASH, password)


@app.route("/unlock", methods=["GET", "POST"])
def unlock():
    scope = "settings" if request.values.get("scope") == "settings" else "view"
    error = None
    ip = request.remote_addr or "?"
    if request.method == "POST":
        wait = login_guard.wait_seconds(ip)
        if wait:
            error = f"กรอกรหัสผิดหลายครั้ง — ลองใหม่ในอีก {wait // 60 + 1} นาที"
        elif _password_ok(scope, request.form.get("password", "")):
            login_guard.reset(ip)
            session.permanent = True
            if scope == "settings":
                session["settings_until"] = time.time() + SETTINGS_UNLOCK_SECONDS
            else:
                session["unlocked"] = True
            return redirect(_safe_next())
        else:
            login_guard.record_failure(ip)
            app.logger.warning("unlock ผิด scope=%s ip=%s", scope, ip)
            error = "รหัสไม่ถูกต้อง"
    return render_template("unlock.html", error=error, next=request.values.get("next", ""), scope=scope)


@app.route("/lock", methods=["POST"])
def lock():
    session.pop("unlocked", None)
    session.pop("settings_until", None)
    return redirect(url_for("dashboard"))


def _from_server_itself():
    """เปิดจากเครื่องที่รันแอปเอง (127.0.0.1) และไม่ได้ผ่าน reverse proxy"""
    if request.headers.get("X-Forwarded-For") or request.headers.get("X-Forwarded-Prefix"):
        return False
    try:
        return ipaddress.ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        return False


def _settings_guard():
    """กันหน้า /settings — ใครแก้ได้ก็เปลี่ยนปลายทางแจ้งเตือน (และส่ง key ของกลุ่ม LINE ออกไป) ได้

    คืน Response เมื่อไม่อนุญาต, None เมื่อผ่าน
    """
    if settings_password_set():
        pass                    # กรอกรหัสหน้าตั้งค่าแล้ว (ตรวจใน settings())
    elif not _from_server_itself():
        return Response("หน้าตั้งค่าเปิดได้เฉพาะจากเครื่องเซิร์ฟเวอร์ (http://127.0.0.1:<พอร์ต>/settings)\n"
                        "ถ้าต้องการเปิดจากเครื่องอื่น ให้ตั้ง SEPSIS_SETTINGS_PASSWORD_HASH ใน .env แล้วรีสตาร์ตแอป\n"
                        "(ใช้รหัสคนละตัวกับรหัสดู HN/AN)",
                        403, mimetype="text/plain")
    # กันเว็บอื่นแอบส่งฟอร์มมาแทนผู้ใช้ (เบราว์เซอร์แนบรหัสผ่านที่จำไว้ให้เอง)
    if request.method == "POST":
        origin = request.headers.get("Origin") or request.headers.get("Referer")
        if origin and urlsplit(origin).netloc != request.host:
            return Response("ส่งฟอร์มจากหน้าอื่นไม่ได้", 403, mimetype="text/plain")
    return None


@app.route("/settings", methods=["GET", "POST"])
def settings():
    if settings_password_set() and not settings_unlocked():  # ถามรหัสหน้าตั้งค่าทุกครั้งที่เข้า
        return redirect(url_for("unlock", scope="settings", next=url_for("settings")))
    denied = _settings_guard()
    if denied:
        return denied
    message = error = None
    if request.method == "POST":
        base_url = request.form.get("base_url", "").strip() or moph_notify.DEFAULT_BASE_URL
        if not moph_notify.allowed_base_url(base_url):
            data = moph_notify.load_settings()
            return render_template("settings.html", s=data, message=None,
                                   error=f"URL ต้องเป็น https:// ของโดเมน moph.go.th เท่านั้น (ได้รับ {base_url})",
                                   configured=moph_notify.is_configured(data))
        values = {
            "base_url": base_url,
            "client_key": request.form.get("client_key", "").strip(),
        }
        secret_key = request.form.get("secret_key", "").strip()
        if secret_key:
            values["secret_key"] = secret_key  # เว้นว่างในฟอร์ม = ไม่เปลี่ยนค่าเดิม
        saved = moph_notify.save_settings(values)

        if request.form.get("action") == "test":
            try:
                moph_notify.send_messages(
                    flex.sample_messages(queries.BUNDLE_WINDOW_MIN), settings=saved
                )
                message = ("ส่งการ์ดทดสอบ 2 ใบสำเร็จ (เคสใหม่ + ผล bundle ข้อมูลสมมติ) "
                           "— ตรวจสอบในกลุ่ม LINE ที่ผูกกับ client-key นี้")
            except Exception as exc:
                error = f"ส่งข้อความทดสอบไม่สำเร็จ: {exc}"
        else:
            message = "บันทึกการตั้งค่าแล้ว"

    data = moph_notify.load_settings()
    return render_template(
        "settings.html",
        s=data,
        message=message,
        error=error,
        configured=moph_notify.is_configured(data),
    )


@app.route("/healthz")
def healthz():
    return {"status": "ok"}


if __name__ == "__main__":
    # debug ปิดเป็นค่าเริ่มต้น — ตัว debugger ของ Werkzeug สั่งรันโค้ดบนเครื่องได้ และแอปนี้มีข้อมูลผู้ป่วย
    # เปิดด้วย FLASK_DEBUG=1 เฉพาะตอนพัฒนา และจะฟังแค่ 127.0.0.1 (เครื่องอื่นใน LAN เข้าไม่ได้)
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="127.0.0.1" if debug else os.getenv("SEPSIS_HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "5500")), debug=debug)
