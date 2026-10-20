# หน้าต่างเตือน "สงสัย Sepsis" ในหน้าซักประวัติ HOSxP

เมื่อพยาบาลบันทึกชีพจรในหน้าซักประวัติ HOSxP แล้ว **SOS Score > 3 ร่วมกับไข้ ≥ 38 °C หรือตัวเย็น < 36 °C**
(ผู้ป่วยอายุ ≥ 15 ปี) จะขึ้นหน้าต่างเตือนทันที แยกหน้าตาตามจุดที่บันทึก:

| จุดที่บันทึก | หน้าต่าง |
|---|---|
| จุดคัดกรอง / OPD | **แดง** "เตือนสงสัย SEPSIS : ส่ง ER ทันที" — ส่ง ER ไม่ต้องรอคิว · โทรแจ้ง ER · เปลี่ยนห้องส่งตรวจ |
| ER (รหัสแผนกที่ตั้งไว้ หรือมีแถว er_regist แล้ว) | **น้ำเงิน-ส้ม** "SEPSIS ALERT (ER)" + เวลาเป้าหมาย (เวลาบันทึก + 60 นาที) + Hemoculture/Lactate/ยาปฏิชีวนะ/สารน้ำ |
| SOS > 3 แต่ไม่มีไข้ | ข้อความ "ผู้ป่วยมี SOS Score = X" แบบเดิม |

เป็น Pascal script ใน `sys_app_script` ชื่อ `SYS_VN_SCRIPT` ที่ HOSxP รันตอนบันทึก — ไม่ต้องติดตั้งโปรแกรมที่เครื่องพยาบาล
ใช้งานจริงที่ รพ.วังน้ำเขียว ตั้งแต่ 19 ต.ค. 2026 (HOSxP 3.69.9.24) · ข้อมูล 90 วันย้อนหลัง: เตือนเฉลี่ย ~1 ครั้ง/วัน

## ไฟล์

| ไฟล์ | คืออะไร |
|---|---|
| `install_alert.py` | ตัวติดตั้ง: `check` / `install` / `rollback` (สำรองอัตโนมัติ, ถามรหัสแบบซ่อน) |
| `SYS_VN_SCRIPT_sepsis_template.pas` | script ต้นแบบ (ตัวติดตั้งใส่รหัสแผนก ER ให้) |
| `SYS_VN_SCRIPT_sos_only_example.pas` | ตัวอย่าง script SOS เดิมก่อนเพิ่มการเตือน (ไว้เทียบ) |
| `_vendor/pymysql` | ไลบรารีเชื่อม MariaDB/MySQL (MIT License) แนบมาในชุด zip — ถ้าดึงจาก GitHub ให้ `pip install pymysql` |

## ขั้นตอน (ผู้ดูแล HOSxP)

ต้องมี Python 3.9+ บนเครื่องที่ต่อฐาน HOSxP ได้ และบัญชีฐานข้อมูลที่มีสิทธิ์ SELECT/UPDATE บน `sys_app_script`
(INSERT ด้วยถ้ายังไม่มีแถว SYS_VN_SCRIPT) — แนะนำสร้างบัญชีชั่วคราวเฉพาะงานนี้ แล้วลบทิ้งหลังติดตั้ง:

```sql
CREATE USER 'sepsis_deploy'@'<IP เครื่องที่ใช้ติดตั้ง>' IDENTIFIED BY '<รหัสใหม่>';
GRANT SELECT, UPDATE, INSERT ON hos.sys_app_script TO 'sepsis_deploy'@'<IP>';
GRANT SELECT ON hos.opdscreen TO 'sepsis_deploy'@'<IP>';
GRANT SELECT ON hos.er_regist TO 'sepsis_deploy'@'<IP>';
GRANT SELECT ON hos.vn_stat TO 'sepsis_deploy'@'<IP>';
GRANT SELECT ON hos.kskdepartment TO 'sepsis_deploy'@'<IP>';
GRANT SELECT ON hos.depcode_vn_script_allow TO 'sepsis_deploy'@'<IP>';
```

1. **ตรวจ (อ่านอย่างเดียว)** — ดู script เดิม, รหัสแผนก ER ที่แนะนำ, ประมาณจำนวนการเตือนต่อวัน
   ```
   python install_alert.py check --host <IP ฐาน> --user sepsis_deploy
   ```
   เลือก `--er-deps` จากแผนกที่มีจำนวน visit ER มากที่สุด (ปกติคือ ER ของ รพ. เช่น 011)
2. **ติดตั้งในฐานทดสอบก่อนเสมอ** (สำเนาฐาน hos ที่ไม่ใช่ replica) แล้วทดสอบใน HOSxP ตามตารางด้านล่าง
   ```
   python install_alert.py install --host <IP ฐานทดสอบ> --db hos_test --user <user> --er-deps 011
   ```
3. **ติดตั้งฐานจริง** ช่วงคนน้อย แล้วเปิด HOSxP ใหม่ที่เครื่องจุดคัดกรองและ ER
   ```
   python install_alert.py install --host <IP ฐานหลัก> --user sepsis_deploy --er-deps 011
   ```
   ตัวติดตั้งสำรอง script เดิมไว้ใน `backups/` และพิมพ์คำสั่งย้อนกลับให้ · **เช็คผู้ป่วยจริง 1 รายทันที**
   ว่าข้อความ SOS / หน้าต่างเตือนยังขึ้น — ถ้าไม่ขึ้นอะไรเลยให้ย้อนกลับทันที:
   ```
   python install_alert.py rollback --host <IP ฐานหลัก> --user sepsis_deploy --backup backups/<ไฟล์>.pas
   ```

**ห้ามติดตั้งบนฐาน replica / ห้ามต่อ HOSxP เครื่องทดสอบเข้า replica** — การบันทึกจะทำให้ replication พัง
และเครื่อง HOSxP จริงอ่าน script จากฐานหลักอยู่แล้ว

## ตารางทดสอบ (ในฐานทดสอบ)

| # | ล็อกอินแผนก | T | P | RR | BP | อายุ | ผลที่ต้องเห็น |
|---|---|---|---|---|---|---|---|
| 1 | จุดคัดกรอง | 36.8 | 80 | 18 | 120/80 | ผู้ใหญ่ | ไม่มีข้อความ |
| 2 | จุดคัดกรอง | 37.0 | 130 | 28 | 85/50 | ผู้ใหญ่ | ข้อความ SOS เดิม |
| 3 | จุดคัดกรอง | 39.0 | 125 | 26 | 88/50 | ผู้ใหญ่ | หน้าต่างแดง "ส่ง ER ทันที" |
| 4 | ER | 39.0 | 125 | 26 | 88/50 | ผู้ใหญ่ | หน้าต่างน้ำเงิน "SEPSIS ALERT (ER)" + เวลาเป้าหมาย |
| 5 | จุดคัดกรอง | 39.0 | 125 | 26 | 88/50 | < 15 ปี | ไม่มีข้อความ |

ถ้าที่ ER ไม่ขึ้นแม้ข้อความ SOS ให้เพิ่มรหัส ER ใน `depcode_vn_script_allow` ผ่านหน้าตั้งค่าของ HOSxP

## ถ้า รพ. มี SYS_VN_SCRIPT ของตัวเองอยู่แล้ว

ตัวติดตั้งจะ**ไม่ทับ** script ที่ไม่ใช่แบบ SOS ที่รู้จัก (ป้องกันการทำงานเดิมหาย) ให้เลือก:
- รวมเอง: นำส่วนตั้งแต่ `// --- เพิ่ม: สัญญาณติดเชื้อ` และบล็อกหน้าต่างใน `if fptname > 3` จาก script ที่ตัวติดตั้งสร้าง
  ไปใส่ใน script ของ รพ. (ต้องมีตัวแปร SOS ชื่อ `fptname` หรือแก้ให้ตรง) แล้วทดสอบในฐานทดสอบ
- หรือใส่ `--replace` ถ้าตั้งใจแทนที่ทั้งหมด (script เดิมยังอยู่ใน `backups/`)

## ข้อจำกัดของ script engine ใน HOSxP (สำหรับผู้ที่จะแก้ต่อ)

HOSxP **ไม่แสดง error ของ script** — ถ้าแปลไม่ผ่านจะไม่ขึ้นอะไรเลย (รวมข้อความ SOS) ทดสอบในฐานทดสอบทุกครั้ง

| ห้ามใช้ | ใช้แทน |
|---|---|
| ชื่อค่าคงที่ `clRed`, `bsDialog`, `fsBold`, `mrOk`, `poScreenCenter` | ตัวเลข: สี TColor (BGR), BorderStyle 3, Position 4, Align 1/3, ModalResult 1, BevelOuter 0 |
| `procedure`, `try ... except/finally`, `Beep`, `fsStayOnTop` | เขียนโค้ดตรง ๆ ในตัว script |
| ใช้ `getsqldata(...)` ในนิพจน์ `+` ตรง ๆ, ต่อข้อความกับค่าจำนวนเต็ม (เช่น `age_y`) | เก็บผลลงตัวแปรก่อน, ดึงตัวเลขด้วย `round(...)` |
| อักษรนอก TIS-620 (· ° ≥ — emoji) | อักษรไทย/อังกฤษธรรมดา |
| ใส่ `;` หน้า `else` | ตามไวยากรณ์ Pascal |

ตรวจด้วยข้อความ `showmessage('D1 ...')` ไล่ทีละช่วงว่าหยุดตรงไหน

พัฒนาโดย โรงพยาบาลวังน้ำเขียว จ.นครราชสีมา · ติดต่อ: นายเผด็จชัย สิงหล โรงพยาบาลวังน้ำเขียว โทร 086-257-4817
