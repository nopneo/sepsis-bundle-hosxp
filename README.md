# Sepsis Six Bundle — โรงพยาบาลวังน้ำเขียว

ดึงเคส sepsis จาก HOSxP อัตโนมัติ (ทั้งเคสที่ admit และเคสที่จบที่ ER) คำนวณว่าทำ Sepsis Six Bundle
ครบภายใน 1 ชม. นับจากมาถึง ER หรือไม่ และแจ้งเตือนเข้ากลุ่ม LINE ผ่าน MOPH Notify

## เกณฑ์ที่ใช้

- **เคส** = visit ที่มี ICD10 `A40.x`, `A41.x` หรือ `R57.2` (septic shock) ใน `ovstdiag` (ER/OPD)
  หรือ `iptdiag` (หลัง admit) — HOSxP เก็บรหัสแบบไม่มีจุด (`A419`, `R572`)
- **time-zero** (`SEPSIS_TIME_ZERO=er`) = ค่าแรกที่มีของ `er_regist.sepsis_recognition_datetime` →
  `triage_datetime` → `enter_er_time` → เวลาลงทะเบียน visit · ตอนนี้ ER ไม่ได้กรอกสองช่องแรก
  จึงได้ **เวลาเข้า ER** เป็นส่วนใหญ่ — ถ้า ER เริ่มกรอก "เวลารับรู้ sepsis" ระบบจะใช้ค่านั้นเองทันที
- Bundle element ที่ดึงอัตโนมัติ (ภายใน `SEPSIS_BUNDLE_MINUTES` นาที, default 60) — เวลาที่ได้คือ
  **เวลาสั่ง/บันทึกคำสั่ง** และหน้าแดชบอร์ด/การ์ดบอกแหล่งของเวลาทุกข้อ:
  1. **Lactate** — `lab_head.order_time` (lab_items 2240)
  2. **Hemoculture** — รพ. สั่งเป็นรายการค่าบริการ icode `3071625` (ไม่ผ่านระบบ lab) · เช็คลำดับก่อน ATB
  3. **ยาปฏิชีวนะ** — `drugitems.antibiotic='Y'`
  4. **IV fluid** — NSS 1000 ml / LRS เท่านั้น (ไม่นับ NSS 100 ml ที่ใช้ผสมยา)

  เวลายา/รายการค่าบริการ ใช้ค่าที่เร็วที่สุดจาก: เวลาให้ยาที่ ER บันทึก (`er_regist.antibiotics_datetime`) →
  ครั้งแรกที่รายการปรากฏใน `opitemrece_log` ของ visit ER → คำสั่งบนหอ (`opitemrece.item_type='P'`)

- **บันทึกพยาบาล ER (`opdscreen.hpi`) มาก่อนเวลาสั่ง** — `nurse_notes.py` อ่านรายการที่คั่นด้วย " - "
  เช่น `Cef -3 2 g iv drip at 13.40 น.`, `0.9%NaCl iv load 1,000 ml at 11.50 น.` แล้วใช้เป็น "เวลาให้จริง"
  นับเป็นเวลาเฉพาะเมื่อมี `at`/`stat`/`เวลา` นำหน้า หรือ `น.` ต่อท้าย (กันค่า lab อย่าง `Cr 6.14`)
  แดชบอร์ดยกข้อความต้นทางมาให้ตรวจทานทุกครั้ง
- **ออกซิเจน** — บันทึก `On O2 ...`/`O2 mask`/ใส่ ETT = ให้แล้ว · ถ้า SpO2 แรก ≥ 94% และไม่ได้ให้ = ไม่จำเป็น (ผ่าน)
- **Urine output** — บันทึก Foley/สวนปัสสาวะคาสาย/I-O = ติดตามแล้ว (ไม่มีเวลา)
- ออกซิเจน/Urine output ยังไม่นับใน "ครบ 4 ข้อ" (เกณฑ์อัตโนมัติเดิม) แต่แสดงผลในแดชบอร์ดและการ์ด

## ข้อจำกัดสำคัญ — ตรวจสอบกับข้อมูลจริงแล้ว (ต.ค. 2026)

1. **เวลาให้จริงมีเฉพาะที่พยาบาล ER เขียนไว้ใน HPI** — `ipd_mar` คือตารางคำสั่งยา ไม่ใช่บันทึกการให้ยา
   เคสที่ไม่มีเวลาในบันทึก (หรือให้บนหอ) จะใช้เวลาสั่งแทน ซึ่งอาจเร็วกว่าเวลาให้จริง
   ถ้าพยาบาลเปลี่ยนวิธีเขียน (เช่นไม่ใส่ "at"/"น.") ตัวอ่านอาจหาเวลาไม่เจอ — ดูข้อความต้นทางในแดชบอร์ด
2. **แถวยาที่โอนจาก ER ไป AN ใช้เวลาไม่ได้** — `opitemrece.vsttime/rxtime` ติดเป็นเวลาลงทะเบียน visit
   (บางแถวเป็นเวลาก่อนมาถึงด้วย) จึงต้องอ่านเวลาจาก `opitemrece_log` แทน
3. **ออกซิเจน/Urine Output ไม่มีในช่องโครงสร้าง** (`er_regist_vital_sign_list.o2sat` ว่าง) — อ่านจาก
   บันทึกพยาบาลแทน ถ้าไม่พบในบันทึกจะขึ้น "ยืนยันเอง"
4. **ระบบเห็นเคสเมื่อมีการลง ICD sepsis** — ถ้าแพทย์ลงรหัสช้า การ์ด "เคสใหม่" จะมาหลังครบ 1 ชม.
   การ์ดจะบอกว่า "เลยกำหนดแล้ว X นาที" แทนการนับถอยหลัง
5. **lab_head ไม่มีคอลัมน์ `an`** — จับคู่ lab ด้วย `hn` + ช่วงเวลา (`SEPSIS_WINDOW_HOURS`)
6. **การ์ดผล bundle ใน LINE ปิดไว้ก่อน** (`SEPSIS_SEND_BUNDLE_RESULT=0`) — ดูผลในแดชบอร์ดได้ตามปกติ
   เปิดด้วย `SEPSIS_SEND_BUNDLE_RESULT=1` ใน `/opt/sepsis_bundle/.env` หลังทีมทวนตัวเลขแล้ว
7. **LINE Notify (ตัวเดิม) ปิดบริการแล้วตั้งแต่ปลายมีนาคม 2025** — ใช้ LINE Messaging API แทนได้
   (ต้องสร้าง LINE Official Account + Channel access token เอง) หรือใช้ MOPH Notify แทนก็ได้ (ดูหัวข้อถัดไป)
8. **ความเป็นส่วนตัว** — ข้อความแจ้งเตือนส่งแค่ HN/AN/ward/เตียง **ไม่ส่งชื่อผู้ป่วย** ตามหลักของ รพ.
   และแนะนำให้รันแอปนี้เฉพาะในวง intranet เท่านั้น ไม่ต้องเปิด sub-path ผ่าน 443 ออกนอก รพ.

## ตั้งค่าแจ้งเตือนผ่าน MOPH Notify (หมอพร้อม)

MOPH Notify ส่งข้อความเข้า **LINE group ของหน่วยบริการ** ที่มีบอทหมอพร้อมอยู่ (อ้างอิงเอกสารในโฟลเดอร์
`คู่มือการใช้งานสำหรับ MOPH Notify/`) เตรียมกลุ่มครั้งเดียว:

1. สร้าง LINE group ตั้งชื่อลงท้าย `HC` + รหัสหน่วยบริการ 5 หลัก (ต้องตั้งชื่อ **ก่อน** เชิญหมอพร้อม)
2. เชิญ LINE "หมอพร้อม" เข้ากลุ่ม
3. Admin หน่วยบริการอนุมัติกลุ่มใน CMS MOPH Notify
4. ดู Client_ID / Secret ของกลุ่มในเมนูกลุ่มบอท

แล้วเปิดหน้า `/settings` ในแอป (ลิงก์ "⚙️ ตั้งค่าแจ้งเตือน" บนแดชบอร์ด) กรอก Client Key / Secret Key
กด "บันทึก + ทดสอบส่ง" ระบบยิง `POST {URL}/api/notify/send` (header `client-key`/`secret-key`)
URL ค่าเริ่มต้น `https://morpromt2f.moph.go.th` (รับเฉพาะ `https://*.moph.go.th`) ค่าที่บันทึกเก็บที่ `data/moph_settings.json`
(สิทธิ์ 600 ตั้งแต่สร้างไฟล์ · เขียนแบบ atomic) ไม่ต้องแก้ `.env` + restart แอป

**หน้า `/settings` ต้องใส่รหัสผ่าน** `SEPSIS_SETTINGS_PASSWORD` ใน `.env` (ชื่อผู้ใช้อะไรก็ได้) — ถ้าไม่ตั้ง
เปิดได้เฉพาะจากเครื่องเซิร์ฟเวอร์เอง (`http://127.0.0.1:<port>/settings`) เพราะใครแก้หน้านี้ได้ก็เปลี่ยนปลายทางการแจ้งเตือนได้

**ซ่อน HN/AN** — การ์ด LINE แสดง HN/AN/VN เป็น `xxx` เสมอ (ทีมดูหอผู้ป่วย/เตียงแทน) ส่วนแดชบอร์ดซ่อนไว้จนกว่าจะกด
"🔒 ดู HN/AN" แล้วกรอกรหัส (ปลดล็อก `SEPSIS_UNLOCK_HOURS` ชม. ต่อเบราว์เซอร์) ตั้งใน `.env` เป็น hash เท่านั้น:
`SEPSIS_VIEW_PASSWORD_HASH` (สร้างด้วย `generate_password_hash(รหัส, method="pbkdf2:sha256")`) และ `SEPSIS_SECRET_KEY`
— เมื่อตั้งแล้ว หน้า `/settings` จะถามรหัสเดียวกันนี้ทุกครั้งที่เข้า (แทน `SEPSIS_SETTINGS_PASSWORD`) · ไม่ตั้ง = แสดง HN/AN ตามปกติ

ถ้ายังไม่ได้ตั้งค่า MOPH Notify, `scheduler.py` จะ fallback ไปใช้ LINE Messaging API (`notify.py`
+ `.env`) แทนโดยอัตโนมัติ — ตั้งอย่างใดอย่างหนึ่งก็พอ

## ตัวชี้วัดตาม SSC 2026 (แสดงเพิ่ม ไม่เปลี่ยนเกณฑ์ "ครบ 4 ข้อ")

`queries.ssc_status()` ต่อเคส + `ssc_summary()` / `monthly()` / `completeness()` บนแดชบอร์ด:

- **shock/hypoperfusion** = R57.2 · SBP แรก < 90 · MAP < 65 · BP ต่ำสุดในบันทึก ER < 90 · Lactate แรก ≥ 4 · ได้ vasopressor
- **ยาปฏิชีวนะตามเป้า** shock ≤ 60 นาที / ไม่ shock ≤ 180 นาที (Rec 16–18)
- **Lactate** ค่าผล (`lab_order.lab_order_result`) + วัดซ้ำ ≤ `SEPSIS_LACTATE_REPEAT_HOURS` (6) เมื่อค่าแรก > 2
  — ข้อมูลจริง ปีงบ 69: วัดซ้ำทันเวลา 4/113 ราย (ส่วนใหญ่วัดซ้ำบนหอ 7–20 ชม.)
- **สารน้ำ 30 mL/kg** (เคส shock) = ผลรวม load/bolus ที่พยาบาลเขียนใน ER ภายใน 3 ชม. (`nurse_notes.parse` → `fluid_bolus_ml`)
  เทียบ 30 × `opdscreen.bw` · ชนิด balanced/NSS จากบันทึก — เป็นข้อมูลประกอบ (conditional)
- **vasopressor** ตัวแรกจากบันทึก (Levophed ฯลฯ) + คำสั่งตาม `SEPSIS_NOREPINEPHRINE_ICODES` / `SEPSIS_DOPAMINE_ICODES`
- **refer/ICU ≤ 6 ชม.** (เคส shock) — ICU ตาม `SEPSIS_ICU_WARDS` (วังน้ำเขียวไม่มี ICU)
- **NEWS2** ใช้ `er_regist/opdscreen.news2_score` ถ้า ER กรอก ไม่งั้นคำนวณจาก opdscreen + SpO₂/O₂ ในบันทึก
  (ความรู้สึกตัว `consciousness_level_type_id` 1 = A) · ตอนนี้ ER ยังไม่กรอก NEWS2/AVPU/SpO₂/triage เลย (0%)
- การ์ด LINE "เคสใหม่" มีกล่องประเมินแรกรับ (`queries.triage_info`) · การ์ดผลมีกล่อง SSC

## ชุดติดตั้งสำหรับโรงพยาบาลอื่น

`python build_package.py` → `dist/sepsis_bundle_v<VERSION>.zip` (โปรแกรม + `install.sh` / `install_windows.bat` +
`setup_check.py` + คู่มือ PDF) ต้นฉบับสคริปต์ติดตั้ง/คู่มืออยู่ใน `package/` · ตัว build ตัด `.env`, `data/`, สคริปต์ deploy/SSH
และโลโก้ รพ. ออก แล้วตรวจซ้ำว่าไม่มี key MOPH Notify หรือ IP ภายในหลุดไป · แก้ `VERSION` ใน `build_package.py` ทุกครั้งที่ออกรุ่นใหม่

ค่าเฉพาะ รพ. (ชื่อ, รหัส lab/ยา) ไม่มีค่าเริ่มต้นในโค้ดแล้ว — เซิร์ฟเวอร์วังน้ำเขียวตั้งไว้ใน `.env` (`deploy_ssh.py` เติมให้ถ้ายังไม่มี)

## โครงสร้างไฟล์

- `db.py` — ชั้นต่อ HOSxP (เหมือน `ipd_dashboard/db.py` รองรับทั้งโหมด direct/ssh)
- `queries.py` — หาเคส sepsis + คำนวณ bundle compliance ต่อเคส
- `notify.py` — แจ้งเตือนผ่าน LINE Messaging API
- `moph_notify.py` — แจ้งเตือนเข้า LINE group ผ่าน MOPH Notify (หมอพร้อม) + เก็บ/โหลดค่าตั้งค่าใน `data/moph_settings.json`
- `scheduler.py` — งาน cron ที่ดึงเคสใหม่ + แจ้งเตือน (MOPH Notify ก่อน ไม่งั้น fallback LINE, กันยิงซ้ำด้วย SQLite ใน `data/`)
  รอบก่อนยังไม่จบ = ข้ามรอบนี้ (`data/scheduler.lock`) · log ที่ `data/scheduler.log` หมุนไฟล์เอง (1 MB × 5)
- `tests/` — ชุดทดสอบ (ตัวอ่านบันทึกพยาบาล, หน้า `/settings`, การส่งพารามิเตอร์ SQL, แคช, ล็อก scheduler)
- `app.py` + `templates/dashboard.html` — เว็บแดชบอร์ดดูสถานะ bundle รายเคส
- `templates/settings.html` — หน้าตั้งค่า MOPH Notify (`/settings`)

## รันตอน dev

```bash
cp .env.example .env   # ใส่ DB_USER/DB_PASS หรือปล่อยว่างแล้วตั้ง SEPSIS_SSH_HOST ให้ใช้โหมด ssh
pip install -r requirements.txt
python app.py           # http://127.0.0.1:5500/   (debug ปิด · เปิดด้วย FLASK_DEBUG=1 จะฟังแค่ 127.0.0.1)
```

ทดสอบ (ไม่ต้องต่อฐานข้อมูล):

```bash
pip install -r requirements-dev.txt
python -m pytest
```

ทดสอบ scheduler/แจ้งเตือนเฉย ๆ (ไม่รัน web):

```bash
python scheduler.py
```

## Deploy ขึ้น production

ใช้สกิล `wnkhos-deploy` ตามมาตรฐานของ รพ. (systemd + gunicorn + .env chmod600) แต่ **ไม่แนะนำให้เปิด
sub-path ผ่าน 443 ออกอินเทอร์เน็ต** เหมือนแอปอื่น เพราะมีข้อมูลผู้ป่วย — ให้บินด้วย gunicorn bind
`0.0.0.0:<port>` แล้วเข้าเฉพาะใน LAN รพ. พอ, และตั้ง cron เรียก `scheduler.py` แยกต่างหากทุก 15 นาที

## สัญญาอนุญาต

MIT License — ใช้ แก้ไข และแจกต่อได้ โดยคงข้อความลิขสิทธิ์ไว้ (ดูไฟล์ `LICENSE` และ `NOTICE`)
ฟอนต์ Sarabun/Kanit ใช้ SIL Open Font License · ระบบนี้ใช้เพื่อพัฒนาคุณภาพ ไม่ใช่เครื่องมือแพทย์และไม่แทนการตัดสินทางคลินิก
