// SYS_VN_SCRIPT - เตือนสงสัย Sepsis (Sepsis Six Bundle) ต้นแบบ - สร้างจริงด้วย install_alert.py
// {{ER_DEPS_SQL}} / {{ER_DEPS_TEXT}} = รหัสแผนก ER ของโรงพยาบาล
// MEWS_SQL = นิพจน์ MEWS, MEWS_GT = เตือน MEWS แยกเมื่อคะแนนมากกว่าค่านี้ (ตัวติดตั้งแทนค่าให้)
// SPO2_SQL = นิพจน์ SpO2, SPO2_LT = เตือน SpO2 แยกเมื่อต่ำกว่าค่านี้
// QSOFA_SQL / QSOFA_KNOWN_SQL = นิพจน์ qSOFA (แสดงประกอบในหน้าต่าง Sepsis)
// SIRS_SQL / SIRS_WBC_SQL = นิพจน์ SIRS 0-4 และมีผล WBC แล้วหรือไม่ (แสดงประกอบในหน้าต่าง Sepsis)
var
fmews : string;
fnews : string;
fews : string;
fsepsis : string;
fspo2 : string;
fshown : string;
fqsofa : string;
fqsofak : string;
fqtext : string;
fqnote : string;
fsirs : string;
fsirsk : string;
fform : string;
fmap : string;
fmaptext : string;
fvn : string;
fhn : string;
fptname : string;
fage : string;
ffever : string;
ftemp : string;
fwhere : string;
crlf : string;
ftitle : string;
fbps : string;
fbpd : string;
fpulse : string;
frr : string;
fhead : string;
ftodo : string;
fbtn : string;
fvital : string;
frm : TForm;
pnTop : TPanel;
pnRed : TPanel;
pnBar : TPanel;
lbDue : TLabel;
fdue : string;
lbSub : TLabel;
lbScore : TLabel;
lbScore2 : TLabel;
lbVital : TLabel;
lbHead : TLabel;
lbTodo : TLabel;
btnOK : TButton;

begin

fvn := GetGlobalValue('VN');
fhn := getsqldata('select hn from opdscreen where vn="'+fvn+'" ');
fage := getsqldata('select age_y from vn_stat where vn="'+fvn+'" ');
fptname := '';
ffever := '0';
fmews := '0';
fnews := '0';
fsepsis := '0';
fspo2 := '0';
fqsofa := '0';
fqtext := '';
fqnote := '';
fsirs := '0';
fmap := '0';
fmaptext := '';
crlf := chr(13) + chr(10);
// --- เพิ่ม: ไม่เตือนเมื่อ script ถูกเรียกจากหน้าที่ไม่ใช่การบันทึกซักประวัติ ---
// HOSxP เรียก script นี้ทุกครั้งที่บันทึก visit รวมหน้าสั่ง Lab (ทดสอบ 23 ต.ค. 2026: บันทึกซักประวัติ = DoctorDeskForm,
// หน้าสั่ง Lab = frmLabOrder ขึ้น 2 รอบ) - ระบุหน้าที่ไม่เตือน แทนการระบุหน้าที่เตือน กันพลาดที่จุดคัดกรองที่ใช้หน้าอื่น
fform := Screen.ActiveForm.Name;
if {{SKIP_FORMS_COND}} then
begin
  fage := '0';
end;
//showmessage(fptname);
if fage > 14 then
begin
fptname := getsqldata('select concat(concat(if(temperature<=20,0,if(temperature BETWEEN 36.1 and 38,0,if(temperature BETWEEN 35.1 and 36,1,if(temperature BETWEEN 38.1 and 38.4,1,if((temperature <=35 or temperature>=38.5 ),2,0 )))))) + '+
'concat(if(bps<=50,0,if(bps BETWEEN 101 and 180,0,if(bps BETWEEN 91 and 100,1,if(bps BETWEEN 181 and 199,1,if(bps BETWEEN 81 and 90,2,if(bps >=200,2,if(bps <=80,3,0  )))))))) + concat(if(pulse<=20,0,if(pulse BETWEEN 51 and 100,0,if(pulse '+
'BETWEEN 41 and 50,1, if(pulse BETWEEN 100 and 120,1,if(pulse BETWEEN 121 and 139,2,if(pulse <=40,3,if(pulse >=140,3,0  ))))))))+ concat(if(rr<=2,0,if(rr BETWEEN 9 and 20,0,if(rr BETWEEN 21 and 25,1, '+
'if(rr BETWEEN 26 and 35,2,if((rr>=35 or rr<=8),3,0 ))))))) as sos_score '+
'from opdscreen o '+
'LEFT JOIN vn_stat v on v.vn=o.vn '+
'where o.vn ="'+fvn+'" and v.age_y >= 15  and concat(concat(if(temperature<=20,0,if(temperature BETWEEN 36.1 and 38,0,if(temperature BETWEEN 35.1 and 36,1,if(temperature BETWEEN 38.1 and 38.4,1,if((temperature <=35 or temperature>=38.5 ),2,0 )))))) + '+
'concat(if(bps<=50,0,if(bps BETWEEN 101 and 180,0,if(bps BETWEEN 91 and 100,1, if(bps BETWEEN 181 and 199,1,if(bps BETWEEN 81 and 90,2,if(bps >=200,2,if(bps <=80,3,0  )))))))) + concat(if(pulse<=20,0,if(pulse BETWEEN 51 and 100,0,if(pulse '+
'BETWEEN 41 and 50,1, if(pulse BETWEEN 100 and 120,1,if(pulse BETWEEN 121 and 139,2, if(pulse <=40,3,if(pulse >=140,3,0  ))))))))+ concat(if(rr<=2,0,if(rr BETWEEN 9 and 20,0,if(rr BETWEEN 21 and 25,1, '+
'if(rr BETWEEN 26 and 35,2,if((rr>=35 or rr<=8),3,0 ))))))) ');
// --- เพิ่ม: สัญญาณติดเชื้อ = ไข้ >= 38 หรือ ตัวเย็น < 36 (เกณฑ์ SIRS, ไม่นับช่องว่าง/ค่าผิด <= 20) ---
// คืนเป็นข้อความ "1"/"0" แบบเดียวกับ fwhere - จำนวนเต็มเทียบกับ '1' อาจไม่ตรงใน script engine
ffever := getsqldata('select if(temperature >= 38 or (temperature > 20 and temperature < 36),"1","0") '+
'from opdscreen where vn="'+fvn+'" ');
ftemp := getsqldata('select round(temperature,1) from opdscreen where vn="'+fvn+'" ');
// ดึงทีละค่าแบบเดียวกับ ftemp (getsqldata ที่คืนผล concat(...) ทำให้ script หยุดเงียบ ๆ ใน HOSxP 3.69.9.24)
fbps := getsqldata('select round(bps) from opdscreen where vn="'+fvn+'" ');
fbpd := getsqldata('select round(bpd) from opdscreen where vn="'+fvn+'" ');
fpulse := getsqldata('select round(pulse) from opdscreen where vn="'+fvn+'" ');
frr := getsqldata('select round(rr) from opdscreen where vn="'+fvn+'" ');
// ต่อทีละช่วงแบบที่ทดสอบผ่าน - ห้ามต่อ fage (age_y เป็นจำนวนเต็ม ต่อข้อความแล้ว script หยุดเงียบ ๆ)
fvital := 'BP ' + fbps;
fvital := fvital + '/' + fbpd;
fvital := fvital + '   P ' + fpulse + '   RR ' + frr;
// getsqldata ที่คืนจำนวนเต็ม (เช่น age_y, ผลบวก case) แล้วนำไปต่อข้อความ = script หยุดเงียบ
// จึงห่อเป็น concat(...) + 0 ให้ได้ทศนิยมแบบเดียวกับ SOS Score (fptname) ที่ใช้ได้จริง
// --- เพิ่ม: MEWS (Modified Early Warning Score) คืนเป็นตัวเลขเดียว ช่องว่าง/ค่าผิดได้ 0 ---
fmews := getsqldata('select concat({{MEWS_SQL}}) + 0 as mews '+
'from opdscreen where vn="'+fvn+'" ');
// --- เพิ่ม: SpO2 จากช่อง o2sat หรือข้อความที่พยาบาลพิมพ์ เช่น "O2 sat : 96 %" (0 = ไม่พบ) ---
fspo2 := getsqldata('select concat({{SPO2_SQL}}) + 0 as spo2 '+
'from opdscreen where vn="'+fvn+'" ');
if fspo2 > 0 then
begin
  fvital := fvital + '   SpO2 ' + fspo2 + '%';
end;
// --- เพิ่ม: NEWS2 (RCP 2017, SpO2 scale 1, ให้ O2 +2 จากข้อความ, สติเปลี่ยน 3) คำนวณจาก SpO2 ชุดเดียวกัน ---
fnews := getsqldata('select concat({{NEWS2_SQL}}) + 0 as news2 '+
'from (select o.*, {{SPO2_SQL}} sp from opdscreen o where o.vn="'+fvn+'") o ');
fvital := fvital + '   NEWS2 ' + fnews;
// --- เพิ่ม: qSOFA = RR >= 22, SBP <= 100, สติเปลี่ยน (AVPU/GCS ถ้ามี) - ข้อมูลประกอบในหน้าต่าง Sepsis ไม่ใช้สั่งเตือน ---
fqsofa := getsqldata('select concat({{QSOFA_SQL}}) + 0 as q '+
'from opdscreen o where o.vn="'+fvn+'" ');
fqsofak := getsqldata('select concat({{QSOFA_KNOWN_SQL}}) + 0 as k '+
'from opdscreen o where o.vn="'+fvn+'" ');
fqtext := 'qSOFA = ' + fqsofa + '/3';
if fqsofa > 1 then
begin
  fqtext := fqtext + ' เสี่ยงสูง';
end;
if fqsofak < 1 then
begin
  fqnote := '(qSOFA ไม่มีข้อมูลสติ)';
end;
// --- เพิ่ม: SIRS = T > 38 หรือ < 36, HR > 90, RR > 20, WBC > 12,000 หรือ < 4,000 / Band > 10% (ผล lab ของ visit นี้) ---
fsirs := getsqldata('select concat({{SIRS_SQL}}) + 0 as s '+
'from opdscreen o where o.vn="'+fvn+'" ');
fsirsk := getsqldata('select concat({{SIRS_WBC_SQL}}) + 0 as w '+
'from opdscreen o where o.vn="'+fvn+'" ');
if fsirsk < 1 then
begin
  fqnote := fqnote + '   (SIRS ยังไม่มีผล WBC)';
end;
// --- เพิ่ม: MAP = (SBP + 2 x DBP) / 3 - เป้าหมาย >= 65 (SSC), ไม่ได้วัด BP = missing ---
fmap := getsqldata('select concat(if(bps > 50 and bpd > 0, round((bps + 2 * bpd) / 3), 0)) + 0 as m '+
'from opdscreen where vn="'+fvn+'" ');
if fmap > 0 then
begin
  fmaptext := 'MAP = ' + fmap;
  if fmap < 65 then
  begin
    fmaptext := fmaptext + ' ต่ำ';
  end;
end
else
begin
  fmaptext := 'MAP = missing';
end;
// เวลาเป้าหมายฝั่ง ER = เวลาบันทึก + 60 นาที (date_format คืนข้อความ HH:MM)
fdue := getsqldata('select date_format(now() + interval 60 minute, "%H:%i")');
// --- เพิ่ม: บันทึกจาก ER หรือไม่ = แผนกที่ซักประวัติ (opdscreen.screen_dep) เป็นรหัส ER ({{ER_DEPS_TEXT}}) หรือมีแถว er_regist แล้ว ---
// ER = แจ้งแพทย์ + เริ่ม bundle / จุดคัดกรองอื่น = ส่ง ER ทันที (ถ้า ER มีหลายรหัส ให้เพิ่มใน in (...))
fwhere := getsqldata('select if(o.screen_dep in ({{ER_DEPS_SQL}}) or exists(select 1 from er_regist e where e.vn=o.vn),"ER","SCREEN") '+
'from opdscreen o where o.vn="'+fvn+'" ');
end;

if fptname > 3 then
begin
  if ffever = '1' then
  begin
    // --- เพิ่ม: SOS > 3 ร่วมกับไข้/ตัวเย็น = สงสัย Sepsis (ข้อความใช้อักษร TIS-620 เท่านั้น) ---
    fsepsis := '1';
    if fwhere = 'SCREEN' then
    begin
      ftitle := '  เตือนสงสัย SEPSIS : ส่ง ER ทันที';
      fhead := 'ส่งผู้ป่วยไปห้องฉุกเฉินทันที - ไม่ต้องรอคิวตรวจ';
      ftodo := '1. โทรแจ้ง ER ล่วงหน้าว่า "สงสัย Sepsis"' + crlf +
               '    พร้อม SOS Score' + crlf +
               '2. เปลี่ยนห้องส่งตรวจเป็น ER ({{ER_DEPS_TEXT}})' + crlf +
               '3. พาผู้ป่วยไป ER ด้วยรถนั่ง/เปลตามอาการ';
      fbtn := 'รับทราบ';
      frm := TForm.Create(nil);
      frm.Caption := 'SepsisNoticeForm';
      frm.BorderStyle := 3;
      frm.Position := 4;
      frm.Width := 900;
      frm.Height := 582;
      frm.Color := 16447736;
      frm.Font.Name := 'Tahoma';

      pnTop := TPanel.Create(frm);
      pnTop.Parent := frm;
      pnTop.Align := 1;
      pnTop.Height := 56;
      pnTop.BevelOuter := 0;
      pnTop.ParentBackground := False;
      pnTop.Color := 1973930;
      pnTop.Font.Name := 'Tahoma';
      pnTop.Font.Color := 16777215;
      pnTop.Font.Height := -26;
      pnTop.Caption := ftitle;

      lbSub := TLabel.Create(frm);
      lbSub.Parent := frm;
      lbSub.Left := 24;
      lbSub.Top := 72;
      lbSub.Font.Name := 'Tahoma';
      lbSub.Font.Height := -24;
      lbSub.Font.Color := 1315980;
      lbSub.Caption := fhead;

      pnRed := TPanel.Create(frm);
      pnRed.Parent := frm;
      pnRed.Left := 24;
      pnRed.Top := 116;
      pnRed.Width := 840;
      pnRed.Height := 342;
      pnRed.BevelOuter := 0;
      pnRed.ParentBackground := False;
      pnRed.Color := 15527165;
      pnRed.Caption := '';

      pnBar := TPanel.Create(frm);
      pnBar.Parent := pnRed;
      pnBar.Align := 3;
      pnBar.Width := 10;
      pnBar.BevelOuter := 0;
      pnBar.ParentBackground := False;
      pnBar.Color := 1973930;
      pnBar.Caption := '';

      lbScore := TLabel.Create(frm);
      lbScore.Parent := pnRed;
      lbScore.Left := 30;
      lbScore.Top := 14;
      lbScore.Font.Name := 'Tahoma';
      lbScore.Font.Height := -32;
      lbScore.Font.Color := 1315980;
      lbScore.Caption := 'SOS Score = ' + fptname + '     T ' + ftemp + ' C';

      lbScore2 := TLabel.Create(frm);
      lbScore2.Parent := pnRed;
      lbScore2.Left := 30;
      lbScore2.Top := 56;
      lbScore2.Font.Name := 'Tahoma';
      lbScore2.Font.Height := -28;
      lbScore2.Font.Color := 1315980;
      lbScore2.Caption := 'MEWS = ' + fmews + '   ' + fqtext + '   SIRS = ' + fsirs + '/4   ' + fmaptext;

      lbVital := TLabel.Create(frm);
      lbVital.Parent := pnRed;
      lbVital.Left := 30;
      lbVital.Top := 104;
      lbVital.Font.Name := 'Tahoma';
      lbVital.Font.Height := -18;
      lbVital.Font.Color := 4605510;
      lbVital.Caption := fvital + '   ' + fqnote;

      lbTodo := TLabel.Create(frm);
      lbTodo.Parent := pnRed;
      lbTodo.Left := 30;
      lbTodo.Top := 146;
      lbTodo.Font.Name := 'Tahoma';
      lbTodo.Font.Height := -21;
      lbTodo.Font.Color := 5908510;
      lbTodo.Caption := ftodo;

      btnOK := TButton.Create(frm);
      btnOK.Parent := frm;
      btnOK.Left := 730;
      btnOK.Top := 482;
      btnOK.Width := 134;
      btnOK.Height := 40;
      btnOK.Font.Name := 'Tahoma';
      btnOK.Font.Height := -18;
      btnOK.Caption := fbtn;
      btnOK.ModalResult := 1;

      frm.ShowModal;
      frm.Free;
    end
    else
    begin
      ftitle := '  SEPSIS ALERT (ER) : เริ่ม Bundle ทันที';
      fhead := 'แจ้งแพทย์ทันที - ทำให้ครบภายใน 1 ชั่วโมง';
      ftodo := '1. เจาะ Hemoculture 2 ขวด + Lactate ก่อนให้ยา' + crlf +
               '2. ให้ยาปฏิชีวนะภายใน 1 ชั่วโมง' + crlf +
               '3. ความดันต่ำ / Lactate >= 4 : crystalloid' + crlf +
               '    30 mL/kg แล้วประเมินซ้ำ' + crlf +
               '4. บันทึกเวลาให้ยา/สารน้ำใน HPI "at 13.40 น."';
      fbtn := 'รับทราบ';
      frm := TForm.Create(nil);
      frm.Caption := 'SepsisNoticeForm - ER';
      frm.BorderStyle := 3;
      frm.Position := 4;
      frm.Width := 900;
      frm.Height := 582;
      frm.Color := 16447736;
      frm.Font.Name := 'Tahoma';

      pnTop := TPanel.Create(frm);
      pnTop.Parent := frm;
      pnTop.Align := 1;
      pnTop.Height := 56;
      pnTop.BevelOuter := 0;
      pnTop.ParentBackground := False;
      pnTop.Color := 7221780;
      pnTop.Font.Name := 'Tahoma';
      pnTop.Font.Color := 16777215;
      pnTop.Font.Height := -26;
      pnTop.Caption := ftitle;

      lbSub := TLabel.Create(frm);
      lbSub.Parent := frm;
      lbSub.Left := 24;
      lbSub.Top := 72;
      lbSub.Font.Name := 'Tahoma';
      lbSub.Font.Height := -24;
      lbSub.Font.Color := 7221780;
      lbSub.Caption := fhead;

      pnRed := TPanel.Create(frm);
      pnRed.Parent := frm;
      pnRed.Left := 24;
      pnRed.Top := 116;
      pnRed.Width := 840;
      pnRed.Height := 342;
      pnRed.BevelOuter := 0;
      pnRed.ParentBackground := False;
      pnRed.Color := 16577000;
      pnRed.Caption := '';

      pnBar := TPanel.Create(frm);
      pnBar.Parent := pnRed;
      pnBar.Align := 3;
      pnBar.Width := 10;
      pnBar.BevelOuter := 0;
      pnBar.ParentBackground := False;
      pnBar.Color := 30950;
      pnBar.Caption := '';

      lbScore := TLabel.Create(frm);
      lbScore.Parent := pnRed;
      lbScore.Left := 30;
      lbScore.Top := 14;
      lbScore.Font.Name := 'Tahoma';
      lbScore.Font.Height := -32;
      lbScore.Font.Color := 7221780;
      lbScore.Caption := 'SOS Score = ' + fptname + '     T ' + ftemp + ' C';

      lbScore2 := TLabel.Create(frm);
      lbScore2.Parent := pnRed;
      lbScore2.Left := 30;
      lbScore2.Top := 56;
      lbScore2.Font.Name := 'Tahoma';
      lbScore2.Font.Height := -28;
      lbScore2.Font.Color := 1315980;
      lbScore2.Caption := 'MEWS = ' + fmews + '   ' + fqtext + '   SIRS = ' + fsirs + '/4   ' + fmaptext;

      lbVital := TLabel.Create(frm);
      lbVital.Parent := pnRed;
      lbVital.Left := 30;
      lbVital.Top := 104;
      lbVital.Font.Name := 'Tahoma';
      lbVital.Font.Height := -18;
      lbVital.Font.Color := 4605510;
      lbVital.Caption := fvital + '   ' + fqnote;

      lbDue := TLabel.Create(frm);
      lbDue.Parent := pnRed;
      lbDue.Left := 30;
      lbDue.Top := 138;
      lbDue.Font.Name := 'Tahoma';
      lbDue.Font.Height := -24;
      lbDue.Font.Color := 20660;
      lbDue.Caption := 'เป้าหมาย: ครบ bundle ภายใน ' + fdue + ' น.';

      lbTodo := TLabel.Create(frm);
      lbTodo.Parent := pnRed;
      lbTodo.Left := 30;
      lbTodo.Top := 182;
      lbTodo.Font.Name := 'Tahoma';
      lbTodo.Font.Height := -21;
      lbTodo.Font.Color := 1973790;
      lbTodo.Caption := ftodo;

      btnOK := TButton.Create(frm);
      btnOK.Parent := frm;
      btnOK.Left := 730;
      btnOK.Top := 482;
      btnOK.Width := 134;
      btnOK.Height := 40;
      btnOK.Font.Name := 'Tahoma';
      btnOK.Font.Height := -18;
      btnOK.Caption := fbtn;
      btnOK.ModalResult := 1;

      frm.ShowModal;
      frm.Free;
    end;
  end;
end;

// --- เพิ่ม: ไม่เข้าเกณฑ์สงสัย Sepsis แต่ NEWS2/MEWS สูง หรือ SpO2 ต่ำ = เสี่ยงทรุดลง (แสดงแทนข้อความ SOS เดิม) ---
if fsepsis = '0' then
begin
  fshown := '0';
  fews := '0';
  if fmews > {{MEWS_GT}} then
  begin
    fews := '1';
  end;
  if fnews > {{NEWS2_GT}} then
  begin
    fews := '1';
  end;
  if fews = '1' then
  begin
    showmessage ('NEWS2 = ' + fnews + '   MEWS = ' + fmews + ' : เสี่ยงสูง (High risk)' + crlf +
                 'แจ้งแพทย์ประเมิน / พิจารณาแจ้งทีมช่วยเหลือ' + crlf +
                 'วัดสัญญาณชีพถี่ขึ้น' + crlf + crlf +
                 'SOS Score = ' + fptname + '   ' + fvital);
    fshown := '1';
  end;
  if fshown = '0' then
  begin
    if fspo2 > 0 then
    begin
      if fspo2 < {{SPO2_LT}} then
      begin
        showmessage ('SpO2 = ' + fspo2 + '% : ต่ำ' + crlf +
                     'ให้ออกซิเจนตามแนวทาง / แจ้งแพทย์ประเมิน' + crlf + crlf +
                     'NEWS2 = ' + fnews + '   MEWS = ' + fmews + '   SOS Score = ' + fptname + crlf + fvital);
        fshown := '1';
      end;
    end;
  end;
  if fshown = '0' then
  begin
    if fptname > 3 then
    begin
      showmessage ('ผู้ป่วยมี SOS Score ='+fptname+' ');
    end;
  end;
end;


end;
