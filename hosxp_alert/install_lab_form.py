"""
ติดตั้งใบสั่ง Lab "Sepsis work up" ใน HOSxP (lab_form_head + lab_form) — แบบเดียวกับใบ "Covid admit" ที่มีอยู่
ผู้สั่งเลือกใบนี้ในหน้าสั่ง Lab ของ HOSxP ตามปกติ — HOSxP ออกใบสั่ง/คิดเงิน/ส่ง LIS เอง (script ไม่สร้างใบสั่งเอง)

  python install_lab_form.py show                                   ดูรายการและตำแหน่ง (ไม่ต่อฐาน)
  python install_lab_form.py check   --host <IP> --db hos_test --user <user>    ตรวจรหัส lab ในฐานนั้น
  python install_lab_form.py install --host <IP> --db hos_test --user <user>
  python install_lab_form.py update  --host <IP> --db hos_test --user <user>    ปรับรายการในใบที่ติดตั้งแล้ว
  python install_lab_form.py rollback --host <IP> --db hos_test --user <user>

รหัส lab ด้านล่างเป็นของ รพ.วังน้ำเขียว (ตรวจจาก replica ต.ค. 2026) — รพ.อื่นแก้ ITEMS ก่อน แล้วรัน check
Hemoculture = ชุดตรวจ 30 จากใบ Out Lab (รพ.เจาะแล้วส่งตรวจภายนอก) — ผู้ใช้ให้รวมไว้ใบเดียวกัน (23 ต.ค. 2026)
CXR อยู่ระบบ X-ray — ใบนี้มีข้อความเตือนแทน
"""
import argparse
import sys

from install_alert import connect, q

FORM_NAME = "Sepsis work up"
COPY_FROM = "Covid admit"          # คัดค่าตั้งของหัวใบ (owner, คิดเงิน ฯลฯ) จากใบที่ใช้งานได้จริง

# (ชนิด, รหัส, ข้อความ, ติ๊กไว้ก่อน, x, y, กว้าง) · group = ชุดตรวจ (lab_items_sub_group) ติ๊กเดียวสั่งทั้งชุด
ITEMS = [
    ("checkbox", 2240, "Lactate", "Y", 24, 44, 160),
    ("group", 30, "Hemoculture (H/C) x 2 ขวด - ส่ง Out Lab", "Y", 24, 70, 260),
    ("group", 7, "Urine Analysis (UA)", "Y", 24, 96, 185),
    ("group", 3, "CBC", "Y", 24, 122, 155),
    ("checkbox", 2006, "BUN", "Y", 24, 148, 98),
    ("checkbox", 2005, "Creatinine", "Y", 24, 174, 120),
    ("checkbox", 2135, "eGFR", "Y", 24, 200, 98),
    ("group", 11, "Electrolyte", "Y", 24, 226, 140),
    ("checkbox", 509, "Influenza A-B (Rapid test)", "N", 24, 252, 210),
    ("checkbox", 2174, "Covid-19 rapid test (ATK)", "N", 24, 278, 200),
    ("group", 12, "Liver Function Test (LFT)", "N", 300, 44, 210),
    ("checkbox", 283, "Calcium", "N", 300, 70, 98),
    ("checkbox", 284, "Magnesium", "N", 300, 96, 110),
    ("checkbox", 285, "Phosphorus (PO4)", "N", 300, 122, 140),
]
LABELS = [   # (ชื่อ component, ข้อความ, x, y, กว้าง, ขนาดอักษร, สี TColor)
    ("Label1", "Sepsis work up", 24, 12, 200, 12, 16711680),
    ("Label2", "อื่นๆ (เลือกเพิ่ม)", 300, 20, 160, 10, 8388608),
    ("Label3", "CXR : สั่งในระบบ X-ray", 300, 170, 260, 10, 255),
]
LABEL_CODE = 99      # ใบเดิมใช้ 99 กับ label/groupbox


def rows():
    out = []
    for kind, code, cap, chk, x, y, w in ITEMS:
        if kind == "group":
            out.append(dict(component_name=f"LabGroup{code}", component_type="checkbox_group", lab_items_code=code,
                            component_caption=cap, checked=chk, form_position_left=x, form_position_top=y,
                            form_position_width=w, font_name="MS Sans Serif", font_size=10, font_color=8388608))
        else:
            out.append(dict(component_name=f"Lab{code}", component_type="checkbox", lab_items_code=code,
                            component_caption=cap, checked=chk, form_position_left=x, form_position_top=y,
                            form_position_width=w, font_name=None, font_size=None, font_color=None))
    for name, cap, x, y, w, size, color in LABELS:
        out.append(dict(component_name=name, component_type="label", lab_items_code=LABEL_CODE,
                        component_caption=cap, checked="N", form_position_left=x, form_position_top=y,
                        form_position_width=w, font_name="MS Sans Serif", font_size=size, font_color=color))
    for r in out:
        r.update(form_name=FORM_NAME, specimen_code=0)
    return out


def cmd_show(_):
    print(f"ใบสั่ง Lab: {FORM_NAME}")
    for kind, code, cap, chk, *_ in ITEMS:
        print(f"  [{'x' if chk == 'Y' else ' '}] {cap:<30} {'ชุดตรวจ' if kind == 'group' else 'รายการ'} {code}")
    for _, cap, *_ in LABELS[2:]:      # ข้อความเตือนในใบ
        print(f"  * {cap}")


def check_codes(cur):
    ok = True
    for kind, code, cap, *_ in ITEMS:
        if kind == "group":
            r = q(cur, "select lab_items_sub_group_name, (select count(*) from lab_items_sub_group_list m "
                       "where m.lab_items_sub_group_code = g.lab_items_sub_group_code) "
                       "from lab_items_sub_group g where lab_items_sub_group_code = %s", (code,))
            name = f"{r[0][0]} ({r[0][1]} รายการ)" if r else None
        else:
            r = q(cur, "select lab_items_name, active_status from lab_items where lab_items_code = %s", (code,))
            name = f"{r[0][0]}" + ("" if r[0][1] == "Y" else "  !! active_status ไม่ใช่ Y") if r else None
        print(f"  {cap:<30} {code:>5} -> {name or '!! ไม่พบรหัสนี้'}")
        ok = ok and bool(name)
    return ok


def cmd_check(args):
    c = connect(args)
    cur = c.cursor()
    print(f"ฐาน {args.db} @ {args.host}")
    ok = check_codes(cur)
    exists = q(cur, "select count(*) from lab_form_head where form_name = %s", (FORM_NAME,))[0][0]
    src = q(cur, "select count(*) from lab_form_head where form_name = %s", (COPY_FROM,))[0][0]
    print(f"ใบ '{FORM_NAME}' มีอยู่แล้ว: {'ใช่' if exists else 'ไม่'} · ใบต้นแบบ '{COPY_FROM}': {'มี' if src else 'ไม่มี'}")
    print("พร้อมติดตั้ง" if ok and not exists and src else "!! ยังติดตั้งไม่ได้ — ดูรายการด้านบน")
    c.close()


def confirm(args, text):
    if args.yes:
        return
    if input(f"\n{text} บนฐาน {args.db} @ {args.host} — พิมพ์ YES เพื่อยืนยัน: ").strip() != "YES":
        sys.exit("ยกเลิก")


def cmd_install(args):
    c = connect(args)
    cur = c.cursor()
    if not check_codes(cur):
        sys.exit("!! มีรหัส lab ที่ไม่พบในฐานนี้ — แก้ ITEMS ก่อน")
    if q(cur, "select count(*) from lab_form_head where form_name = %s", (FORM_NAME,))[0][0]:
        sys.exit(f"ใบ '{FORM_NAME}' มีอยู่แล้ว — ใช้คำสั่ง update เพื่อปรับรายการในใบ")
    head_cols = [r[0] for r in q(cur, "select column_name from information_schema.columns where table_schema = "
                                      "database() and table_name = 'lab_form_head' order by ordinal_position")]
    src = q(cur, f"select {', '.join(head_cols)} from lab_form_head where form_name = %s", (COPY_FROM,))
    if not src:
        sys.exit(f"!! ไม่พบใบต้นแบบ '{COPY_FROM}'")
    head = dict(zip(head_cols, src[0]))
    confirm(args, f"สร้างใบสั่ง Lab '{FORM_NAME}' ({len(ITEMS)} รายการ)")
    # form_id ใหม่จากตาราง serial (แบบเดียวกับที่ HOSxP ใช้) — ล็อกแถวกันชนกับเครื่องอื่น
    cur.execute("select serial_no from serial where name = 'lab_form_head_id' for update")
    r = cur.fetchone()
    top = q(cur, "select coalesce(max(form_id), 0) from lab_form_head")[0][0]
    form_id = max(int(r[0]) if r else 0, int(top)) + 1
    if r:
        cur.execute("update serial set serial_no = %s where name = 'lab_form_head_id'", (form_id,))
    else:
        cur.execute("insert into serial (name, serial_no) values ('lab_form_head_id', %s)", (form_id,))
    head.update(form_name=FORM_NAME, form_id=form_id, active_status="Y", hos_guid=None,
                design_dfm=None, design_pas=None, use_design=None, enable_script=None)
    cols = list(head)
    cur.execute(f"insert into lab_form_head ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))})",
                [head[k] for k in cols])
    insert_rows(cur)
    c.commit()
    n = q(cur, "select count(*) from lab_form where form_name = %s", (FORM_NAME,))[0][0]
    print(f"ติดตั้งแล้ว — form_id {form_id}, {n} ช่องในใบ · เปิดหน้าสั่ง Lab ใน HOSxP แล้วเลือกใบ '{FORM_NAME}'")
    c.close()


def insert_rows(cur):
    for r in rows():
        cols = list(r)
        cur.execute(f"insert into lab_form ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))})",
                    [r[k] for k in cols])


def cmd_update(args):
    """แทนช่องในใบเดิมด้วยรายการปัจจุบัน — คงหัวใบ/form_id ไว้ (ใบสั่งเก่าอ้างแค่ชื่อใบ จึงไม่กระทบ)"""
    c = connect(args)
    cur = c.cursor()
    if not q(cur, "select count(*) from lab_form_head where form_name = %s", (FORM_NAME,))[0][0]:
        sys.exit(f"ยังไม่มีใบ '{FORM_NAME}' — ใช้คำสั่ง install")
    if not check_codes(cur):
        sys.exit("!! มีรหัส lab ที่ไม่พบในฐานนี้ — แก้ ITEMS ก่อน")
    old = q(cur, "select count(*) from lab_form where form_name = %s", (FORM_NAME,))[0][0]
    confirm(args, f"ปรับใบ '{FORM_NAME}' ({old} ช่องเดิม -> {len(rows())} ช่องใหม่)")
    cur.execute("delete from lab_form where form_name = %s", (FORM_NAME,))
    insert_rows(cur)
    cur.execute("update lab_form_head set active_status = 'Y' where form_name = %s", (FORM_NAME,))
    c.commit()
    n = q(cur, "select count(*) from lab_form where form_name = %s", (FORM_NAME,))[0][0]
    print(f"ปรับแล้ว — {n} ช่องในใบ · ปิดหน้าสั่ง Lab ใน HOSxP แล้วเปิดใหม่")
    c.close()


def cmd_rollback(args):
    c = connect(args)
    cur = c.cursor()
    if not q(cur, "select count(*) from lab_form_head where form_name = %s", (FORM_NAME,))[0][0]:
        sys.exit(f"ไม่มีใบ '{FORM_NAME}' — ไม่มีอะไรให้ย้อนกลับ")
    used = q(cur, "select count(*) from lab_head where form_name = %s", (FORM_NAME,))[0][0]
    if used:     # มีใบสั่งจริงอ้างชื่อใบแล้ว — ลบจะทำให้ประวัติเปิดไม่ได้ จึงปิดใช้งานแทน
        confirm(args, f"มีใบสั่งที่ใช้ใบนี้แล้ว {used} ใบ — ปิดใช้งาน (active_status = N) แทนการลบ")
        cur.execute("update lab_form_head set active_status = 'N' where form_name = %s", (FORM_NAME,))
        c.commit()
        print("ปิดใช้งานแล้ว")
    else:
        confirm(args, f"ลบใบสั่ง Lab '{FORM_NAME}'")
        cur.execute("delete from lab_form where form_name = %s", (FORM_NAME,))
        cur.execute("delete from lab_form_head where form_name = %s", (FORM_NAME,))
        c.commit()
        print("ลบแล้ว")
    c.close()


def main():
    ap = argparse.ArgumentParser(description=f"ติดตั้งใบสั่ง Lab '{FORM_NAME}' ใน HOSxP")
    ap.add_argument("command", choices=["show", "check", "install", "update", "rollback"])
    ap.add_argument("--host")
    ap.add_argument("--port", type=int, default=3306)
    ap.add_argument("--db", default="hos")
    ap.add_argument("--user")
    ap.add_argument("--yes", action="store_true", help="ไม่ถามยืนยัน")
    args = ap.parse_args()
    if args.command != "show" and not (args.host and args.user):
        ap.error("ต้องระบุ --host และ --user")
    {"show": cmd_show, "check": cmd_check, "install": cmd_install, "update": cmd_update,
     "rollback": cmd_rollback}[args.command](args)


if __name__ == "__main__":
    main()
