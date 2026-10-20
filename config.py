"""ค่าตั้งค่าประจำโรงพยาบาล — โหลด .env ครั้งเดียวก่อนโมดูลอื่นอ่าน os.getenv

ทุกโมดูลที่อ่านค่าจาก .env ตอน import ต้อง `import config` ก่อน
(เดิมโหลดใน db.py ทำให้ notify.py ที่ import ก่อนอ่านค่าไม่เจอ)
"""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass

HOSPITAL_NAME = os.getenv("SEPSIS_HOSPITAL_NAME", "โรงพยาบาล").strip() or "โรงพยาบาล"
