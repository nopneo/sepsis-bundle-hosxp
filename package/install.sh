#!/usr/bin/env bash
# ติดตั้ง/อัปเดต Sepsis Six Bundle บน Linux (AlmaLinux / Rocky / RHEL 8-9, Ubuntu 20.04+)
#
#   sudo bash install.sh            (รันในโฟลเดอร์ที่แตกไฟล์ zip)
#   sudo PORT=5900 bash install.sh  (เปลี่ยนพอร์ต — ค่าเริ่มต้น 5800)
#
# รันซ้ำได้ปลอดภัย: ไม่ทับ .env, data/ (ค่า MOPH Notify, ประวัติการแจ้งเตือน) และโลโก้ที่เปลี่ยนไว้
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/sepsis_bundle}"
PORT="${PORT:-5800}"
SERVICE="sepsis-bundle"
APP_USER="sepsis"
SRC="$(cd "$(dirname "$0")" && pwd)"

say()  { printf '\n\033[1;35m==> %s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31m!! %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "ต้องรันด้วยสิทธิ์ root: sudo bash install.sh"
[ -f "$SRC/app.py" ] || die "ไม่พบ app.py — ต้องรันสคริปต์จากโฟลเดอร์ที่แตก zip"

# ------------------------------------------------------------------ 1. Python
say "1/8 ตรวจ Python (ต้อง 3.9 ขึ้นไป)"
PY=""
for p in python3.12 python3.11 python3.10 python3.9 python3; do
  if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
    PY="$(command -v "$p")"; break
  fi
done
if [ -z "$PY" ]; then
  echo "ไม่พบ Python 3.9+ ติดตั้งก่อนแล้วรันใหม่:"
  echo "  AlmaLinux/Rocky 8-9 : dnf install -y python3.11"
  echo "  Ubuntu              : apt install -y python3 python3-venv"
  exit 1
fi
echo "ใช้ $PY ($("$PY" -V))"
"$PY" -c 'import ensurepip' 2>/dev/null || die "Python นี้สร้าง venv ไม่ได้ — Ubuntu ให้รัน: apt install -y python3-venv"

TZ_NOW="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
if [ -n "$TZ_NOW" ] && [ "$TZ_NOW" != "Asia/Bangkok" ]; then
  echo "คำเตือน: time zone ของเครื่องคือ $TZ_NOW — ควรเป็น Asia/Bangkok (timedatectl set-timezone Asia/Bangkok)"
fi

# ------------------------------------------------------------- 2. คัดลอกไฟล์
say "2/8 คัดลอกไฟล์ไปที่ $APP_DIR"
id "$APP_USER" >/dev/null 2>&1 || useradd --system --no-create-home --shell "$(command -v nologin || echo /bin/false)" "$APP_USER"
mkdir -p "$APP_DIR/data" "$APP_DIR/static" "$APP_DIR/templates"
cp -f "$SRC"/*.py "$SRC/requirements.txt" "$SRC/.env.example" "$APP_DIR/"
cp -rf "$SRC/templates/." "$APP_DIR/templates/"
# โลโก้/ไอคอน: ไม่ทับถ้า รพ. เปลี่ยนเป็นของตัวเองไว้แล้ว
for f in "$SRC"/static/*; do
  name="$(basename "$f")"
  case "$name" in
    logo.png|favicon.png) [ -e "$APP_DIR/static/$name" ] || cp "$f" "$APP_DIR/static/$name" ;;
    *) cp -rf "$f" "$APP_DIR/static/" ;;
  esac
done

# ------------------------------------------------------------------ 3. .env
say "3/8 ไฟล์ตั้งค่า .env"
ENV="$APP_DIR/.env"
if [ -f "$ENV" ]; then
  echo "มี .env อยู่แล้ว — ไม่เขียนทับ (แก้เองได้ที่ $ENV)"
else
  echo "กรอกข้อมูลการเชื่อมต่อฐานข้อมูล HOSxP (แนะนำ user ที่มีสิทธิ์ SELECT อย่างเดียว)"
  read -rp "  ชื่อโรงพยาบาล (แสดงบนหน้าเว็บ/การ์ด LINE): " H_NAME
  read -rp "  DB host [127.0.0.1]: " DB_HOST; DB_HOST="${DB_HOST:-127.0.0.1}"
  read -rp "  DB port [3306]: " DB_PORT; DB_PORT="${DB_PORT:-3306}"
  read -rp "  ชื่อฐานข้อมูล [hos]: " DB_NAME; DB_NAME="${DB_NAME:-hos}"
  read -rp "  DB user: " DB_USER
  read -rsp "  DB password (พิมพ์แล้วไม่แสดงบนจอ): " DB_PASS; echo
  umask 077
  # เขียนด้วย python — ค่า (เช่นรหัสผ่าน) อาจมีอักขระพิเศษที่ sed ตีความผิด
  SEPSIS_HOSPITAL_NAME="${H_NAME:-โรงพยาบาล}" DB_HOST="$DB_HOST" DB_PORT="$DB_PORT" DB_NAME="$DB_NAME" \
  DB_USER="$DB_USER" DB_PASS="$DB_PASS" PORT="$PORT" \
  "$PY" - "$SRC/.env.example" "$ENV" <<'PYEOF'
import os, sys
keys = ("SEPSIS_HOSPITAL_NAME", "DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASS", "PORT")
out = []
for line in open(sys.argv[1], encoding="utf-8").read().splitlines():
    key = line.split("=", 1)[0]
    out.append(f"{key}={os.environ[key]}" if key in keys and "=" in line else line)
open(sys.argv[2], "w", encoding="utf-8").write("\n".join(out) + "\n")
PYEOF
  unset DB_PASS
  umask 022
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 600 "$ENV"
chmod 700 "$APP_DIR/data"

# ------------------------------------------------------- 4. venv + ไลบรารี
say "4/8 สร้าง virtualenv และติดตั้งไลบรารี (ต้องออกอินเทอร์เน็ตไป pypi.org ได้)"
[ -x "$APP_DIR/.venv/bin/python" ] || "$PY" -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/python" -m pip install -q --upgrade pip || true
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

# ----------------------------------------------------- 5. ตรวจ DB + หารหัส
say "5/8 ตรวจการเชื่อมต่อ HOSxP และค้นหารหัส lab/ยาของโรงพยาบาล"
if ! (cd "$APP_DIR" && runuser -u "$APP_USER" -- .venv/bin/python setup_check.py --write); then
  die "ตรวจไม่ผ่าน — แก้ค่าใน $ENV แล้วรัน sudo bash install.sh ใหม่"
fi

# ------------------------------------------------------------- 6. systemd
say "6/8 ตั้งค่า service $SERVICE (พอร์ต $PORT)"
cat > "/etc/systemd/system/$SERVICE.service" <<EOF
[Unit]
Description=Sepsis Six Bundle Dashboard (HOSxP)
After=network.target

[Service]
User=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/gunicorn -w 2 -t 120 -b 0.0.0.0:$PORT app:app
Restart=always
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now "$SERVICE" >/dev/null
systemctl restart "$SERVICE"

# ---------------------------------------------------------- 7. cron + firewall
say "7/8 ตั้งงานแจ้งเตือนทุก 15 นาที และเปิดพอร์ต $PORT ในวง LAN"
if ! command -v crontab >/dev/null 2>&1; then
  if command -v dnf >/dev/null 2>&1; then dnf install -y -q cronie && systemctl enable --now crond
  elif command -v apt-get >/dev/null 2>&1; then apt-get install -y -q cron && systemctl enable --now cron
  fi
fi
cat > /etc/cron.d/sepsis-bundle <<EOF
# Sepsis Six Bundle — ดึงเคสใหม่และแจ้งเตือนทุก 15 นาที
*/15 * * * * $APP_USER cd $APP_DIR && .venv/bin/python scheduler.py >> data/scheduler.log 2>&1
EOF
chmod 644 /etc/cron.d/sepsis-bundle
if systemctl is-active --quiet firewalld 2>/dev/null; then
  firewall-cmd --permanent --add-port="$PORT/tcp" >/dev/null && firewall-cmd --reload >/dev/null
  echo "เปิดพอร์ต $PORT ใน firewalld แล้ว"
elif command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow "$PORT/tcp" >/dev/null && echo "เปิดพอร์ต $PORT ใน ufw แล้ว"
fi
echo "สำคัญ: อย่าเปิดพอร์ตนี้ออกอินเทอร์เน็ต — แดชบอร์ดมีข้อมูลผู้ป่วยและไม่มีระบบล็อกอิน"

# ---------------------------------------------------------------- 8. ตรวจ
say "8/8 ตรวจสอบการทำงาน"
sleep 3
systemctl is-active "$SERVICE" >/dev/null || { journalctl -u "$SERVICE" -n 30 --no-pager; die "service ไม่ทำงาน"; }
curl -fsS -o /dev/null "http://127.0.0.1:$PORT/healthz" && echo "หน้าเว็บตอบสนองปกติ"
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
cat <<EOF

ติดตั้งเสร็จแล้ว
  แดชบอร์ด     : http://${IP:-<ip-เครื่องนี้>}:$PORT/
  ตั้งค่าแจ้งเตือน : http://${IP:-<ip-เครื่องนี้>}:$PORT/settings  (กรอก Client Key / Secret Key ของ MOPH Notify)
  ไฟล์ตั้งค่า     : $ENV
  log แจ้งเตือน   : $APP_DIR/data/scheduler.log
  ตรวจซ้ำ       : cd $APP_DIR && sudo -u $APP_USER .venv/bin/python setup_check.py
EOF
