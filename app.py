"""Sepsis Six Bundle Dashboard (HOSxP)

รันเอง:      python app.py                      → http://127.0.0.1:5500/
production:  gunicorn -b 0.0.0.0:5500 app:app    (แนะนำให้ใช้ในวง intranet เท่านั้น
                                                    ไม่ควรเปิดผ่าน sub-path 443 ออกนอก รพ.
                                                    เพราะเป็นข้อมูลผู้ป่วย)
"""

import hmac
import ipaddress
import os
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit

from flask import Flask, Response, jsonify, render_template, request

import config
import db
import flex
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


app = Flask(__name__)
app.wsgi_app = PrefixMiddleware(app.wsgi_app)


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
    d1, d2, notice = _date_range()
    results = queries.build(d1=d1, d2=d2)
    today = date.today()
    return render_template("dashboard.html", results=results, s=queries.summary(results),
                           d1=d1, d2=d2, today=today, period=period_label(d1, d2),
                           quick=quick_ranges(today), notice=notice, updated=datetime.now(),
                           lac_h=queries.LACTATE_REPEAT_HOURS)


@app.route("/api/data.json")
def api_data():
    d1, d2, _ = _date_range()
    return jsonify(queries.build(d1=d1, d2=d2))


#: รหัสผ่านหน้า /settings (ชื่อผู้ใช้อะไรก็ได้) — ไม่ตั้ง = เปิดได้เฉพาะจากเครื่องเซิร์ฟเวอร์เอง
SETTINGS_PASSWORD = os.getenv("SEPSIS_SETTINGS_PASSWORD", "")


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
    if SETTINGS_PASSWORD:
        auth = request.authorization
        if not (auth and auth.password
                and hmac.compare_digest(auth.password.encode(), SETTINGS_PASSWORD.encode())):
            return Response("ต้องใส่รหัสผ่านหน้าตั้งค่า (SEPSIS_SETTINGS_PASSWORD ใน .env)", 401,
                            {"WWW-Authenticate": 'Basic realm="Sepsis Bundle settings", charset="UTF-8"'},
                            mimetype="text/plain")
    elif not _from_server_itself():
        return Response("หน้าตั้งค่าเปิดได้เฉพาะจากเครื่องเซิร์ฟเวอร์ (http://127.0.0.1:<พอร์ต>/settings)\n"
                        "ถ้าต้องการเปิดจากเครื่องอื่น ให้ตั้ง SEPSIS_SETTINGS_PASSWORD ใน .env แล้วรีสตาร์ตแอป",
                        403, mimetype="text/plain")
    # กันเว็บอื่นแอบส่งฟอร์มมาแทนผู้ใช้ (เบราว์เซอร์แนบรหัสผ่านที่จำไว้ให้เอง)
    if request.method == "POST":
        origin = request.headers.get("Origin") or request.headers.get("Referer")
        if origin and urlsplit(origin).netloc != request.host:
            return Response("ส่งฟอร์มจากหน้าอื่นไม่ได้", 403, mimetype="text/plain")
    return None


@app.route("/settings", methods=["GET", "POST"])
def settings():
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
