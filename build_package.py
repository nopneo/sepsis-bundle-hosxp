"""สร้างชุดติดตั้งสำหรับแจก รพ.อื่น -> dist/sepsis_bundle_v<VERSION>.zip

    python build_package.py

- ใส่เฉพาะตัวโปรแกรม + สคริปต์ติดตั้ง + คู่มือ PDF (render จาก package/manual.html ด้วย Playwright)
- ไม่ใส่ .env, data/ (key MOPH Notify, ประวัติแจ้งเตือน), สคริปต์ deploy/SSH ของ รพ.วังน้ำเขียว, โลโก้ รพ.
- ตรวจซ้ำก่อน zip ว่าไม่มีรหัส/key/IP ภายในหลุดไป ถ้าพบจะหยุด
"""

import json
import os
import shutil
import tempfile
import zipfile
from datetime import date

VERSION = "1.3.0"
TH_MONTH = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
            "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]

ROOT = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(ROOT, "package")
NAME = f"sepsis_bundle_v{VERSION}"
# สร้างในโฟลเดอร์ชั่วคราว — ไม่ติดไฟล์ใน dist/ ที่อาจถูกเปิดค้าง (เช่นคู่มือ PDF) แล้วค่อยคัดลอกออก
STAGE = os.path.join(tempfile.mkdtemp(prefix="sepsis_pkg_"), NAME)
DIST_COPY = os.path.join(ROOT, "dist", NAME)
MANUAL_PDF = "คู่มือ-Sepsis-Six-Bundle.pdf"

APP_FILES = ["app.py", "config.py", "db.py", "queries.py", "nurse_notes.py", "flex.py", "moph_notify.py",
             "notify.py", "scheduler.py", "setup_check.py", "requirements.txt"]
PKG_FILES = {".env.example": ".env.example", "install.sh": "install.sh",
             "install_windows.ps1": "install_windows.ps1", "install_windows.bat": "install_windows.bat"}
# ห้ามมีในชุดติดตั้ง — ข้อมูลภายในของ รพ.วังน้ำเขียว
# คำที่ห้ามหลุดไปในชุดติดตั้ง (IP/ชื่อเครื่องภายใน) — อ่านจาก .publish_forbidden (ไม่ขึ้น git) บรรทัดละคำ
_FORBID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".publish_forbidden")
FORBIDDEN = ([ln.strip() for ln in open(_FORBID_FILE, encoding="utf-8") if ln.strip() and not ln.startswith("#")]
             if os.path.exists(_FORBID_FILE) else [])


def subst(text):
    today = date.today()
    return (text.replace("{{VERSION}}", VERSION)
                .replace("{{DATE}}", f"{TH_MONTH[today.month - 1]} {today.year + 543}"))


def write_text(path, text, newline, bom=False):
    with open(path, "w", encoding="utf-8-sig" if bom else "utf-8", newline=newline) as fh:
        fh.write(text)


def stage():
    os.makedirs(STAGE)
    for f in APP_FILES + ["LICENSE", "NOTICE"]:
        shutil.copy2(os.path.join(ROOT, f), STAGE)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(os.path.join(ROOT, "templates"), os.path.join(STAGE, "templates"), ignore=ignore)
    shutil.copytree(os.path.join(ROOT, "static"), os.path.join(STAGE, "static"), ignore=ignore)
    # ตัวติดตั้งหน้าต่างเตือนสงสัย Sepsis ใน HOSxP (ไม่รวมไฟล์สำรองของ รพ.วังน้ำเขียว)
    shutil.copytree(os.path.join(ROOT, "hosxp_alert"), os.path.join(STAGE, "hosxp_alert"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "backups"))
    for f in ("logo.png", "favicon.png"):  # โลโก้กลางแทนโลโก้ รพ.วังน้ำเขียว
        shutil.copy2(os.path.join(PKG, f), os.path.join(STAGE, "static", f))

    for src, dst in PKG_FILES.items():
        text = open(os.path.join(PKG, src), encoding="utf-8-sig").read()
        if src.endswith((".ps1", ".bat")):
            # PowerShell 5.1 อ่านภาษาไทยถูกเฉพาะไฟล์ UTF-8 ที่มี BOM
            write_text(os.path.join(STAGE, dst), text, "\r\n", bom=src.endswith(".ps1"))
        else:
            write_text(os.path.join(STAGE, dst), text, "\n")
    write_text(os.path.join(STAGE, "README.md"), subst(open(os.path.join(PKG, "README.md"), encoding="utf-8").read()), "\n")


def render_manual():
    from playwright.sync_api import sync_playwright

    html = subst(open(os.path.join(PKG, "manual.html"), encoding="utf-8").read())
    tmp = os.path.join(PKG, "_manual_render.html")  # อยู่ใน package/ ให้ path รูป/ฟอนต์ relative ถูก
    write_text(tmp, html, "\n")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto("file:///" + tmp.replace("\\", "/"))
            page.wait_for_timeout(800)
            page.pdf(path=os.path.join(STAGE, MANUAL_PDF), format="A4", print_background=True,
                     display_header_footer=True, header_template="<span></span>",
                     footer_template='<div style="width:100%;font-size:8px;color:#8a84b5;text-align:center;">'
                                     f'Sepsis Six Bundle v{VERSION} · หน้า <span class="pageNumber"></span>'
                                     ' / <span class="totalPages"></span></div>',
                     margin={"top": "14mm", "bottom": "16mm", "left": "14mm", "right": "14mm"})
            browser.close()
    finally:
        os.remove(tmp)


def safety_check():
    secrets = []
    moph = os.path.join(ROOT, "data", "moph_settings.json")
    if os.path.exists(moph):
        data = json.load(open(moph, encoding="utf-8"))
        secrets += [v for k, v in data.items() if k != "base_url" and v and len(v) >= 8]
    bad = []
    for rootd, _dirs, files in os.walk(STAGE):
        for f in files:
            path = os.path.join(rootd, f)
            rel = os.path.relpath(path, STAGE)
            if f == ".env" or rel.startswith("data"):
                bad.append(f"{rel}: ไฟล์ต้องห้าม")
            if not f.endswith((".py", ".html", ".css", ".txt", ".md", ".sh", ".ps1", ".bat", ".example")):
                continue
            text = open(path, encoding="utf-8-sig").read()
            for s in FORBIDDEN + secrets:
                if s in text:
                    bad.append(f"{rel}: พบ {'key MOPH Notify' if s in secrets else s!r}")
    if bad:
        raise SystemExit("!! หยุดสร้างชุดติดตั้ง:\n  " + "\n  ".join(bad))
    print("  ตรวจแล้ว: ไม่มี .env / data / key / IP ภายใน")


def make_zip():
    out = os.path.join(ROOT, "dist", f"{NAME}.zip")
    if os.path.exists(out):
        os.remove(out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for rootd, _dirs, files in os.walk(STAGE):
            for f in sorted(files):
                path = os.path.join(rootd, f)
                arc = os.path.join(NAME, os.path.relpath(path, STAGE)).replace("\\", "/")
                info = zipfile.ZipInfo.from_file(path, arc)
                if f.endswith(".sh"):
                    info.external_attr = 0o100755 << 16
                with open(path, "rb") as fh:
                    z.writestr(info, fh.read(), zipfile.ZIP_DEFLATED)
    return out


if __name__ == "__main__":
    print(f"สร้างชุดติดตั้ง {NAME}")
    stage()
    render_manual()
    safety_check()
    out = make_zip()
    try:   # สำเนาแบบแตกไฟล์ไว้ดูใน dist/ (ข้ามได้ถ้ามีไฟล์เดิมเปิดค้าง)
        shutil.rmtree(DIST_COPY)
    except OSError:
        pass
    try:
        shutil.copytree(STAGE, DIST_COPY, dirs_exist_ok=True)
    except OSError as exc:
        print("  (ไม่ได้อัปเดตโฟลเดอร์ dist/ แบบแตกไฟล์: มีไฟล์เปิดค้าง —", type(exc).__name__, ")")
    shutil.rmtree(os.path.dirname(STAGE), ignore_errors=True)
    print(f"  เสร็จ: {out} ({os.path.getsize(out) / 1024:.0f} KB)")
