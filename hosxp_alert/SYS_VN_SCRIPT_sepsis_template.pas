// SYS_VN_SCRIPT - เตือนสงสัย Sepsis (Sepsis Six Bundle) ต้นแบบ - สร้างจริงด้วย install_alert.py
// {{ER_DEPS_SQL}} / {{ER_DEPS_TEXT}} = รหัสแผนก ER ของโรงพยาบาล
var
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
crlf := chr(13) + chr(10);
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
ffever := getsqldata('select if(temperature >= 38 or (temperature > 20 and temperature < 36),1,0) '+
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
      frm.Width := 760;
      frm.Height := 540;
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
      pnRed.Width := 700;
      pnRed.Height := 300;
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

      lbVital := TLabel.Create(frm);
      lbVital.Parent := pnRed;
      lbVital.Left := 30;
      lbVital.Top := 62;
      lbVital.Font.Name := 'Tahoma';
      lbVital.Font.Height := -18;
      lbVital.Font.Color := 4605510;
      lbVital.Caption := fvital;

      lbTodo := TLabel.Create(frm);
      lbTodo.Parent := pnRed;
      lbTodo.Left := 30;
      lbTodo.Top := 104;
      lbTodo.Font.Name := 'Tahoma';
      lbTodo.Font.Height := -21;
      lbTodo.Font.Color := 5908510;
      lbTodo.Caption := ftodo;

      btnOK := TButton.Create(frm);
      btnOK.Parent := frm;
      btnOK.Left := 590;
      btnOK.Top := 440;
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
      frm.Width := 760;
      frm.Height := 540;
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
      pnRed.Width := 700;
      pnRed.Height := 300;
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

      lbVital := TLabel.Create(frm);
      lbVital.Parent := pnRed;
      lbVital.Left := 30;
      lbVital.Top := 62;
      lbVital.Font.Name := 'Tahoma';
      lbVital.Font.Height := -18;
      lbVital.Font.Color := 4605510;
      lbVital.Caption := fvital;

      lbDue := TLabel.Create(frm);
      lbDue.Parent := pnRed;
      lbDue.Left := 30;
      lbDue.Top := 96;
      lbDue.Font.Name := 'Tahoma';
      lbDue.Font.Height := -24;
      lbDue.Font.Color := 20660;
      lbDue.Caption := 'เป้าหมาย: ครบ bundle ภายใน ' + fdue + ' น.';

      lbTodo := TLabel.Create(frm);
      lbTodo.Parent := pnRed;
      lbTodo.Left := 30;
      lbTodo.Top := 140;
      lbTodo.Font.Name := 'Tahoma';
      lbTodo.Font.Height := -21;
      lbTodo.Font.Color := 1973790;
      lbTodo.Caption := ftodo;

      btnOK := TButton.Create(frm);
      btnOK.Parent := frm;
      btnOK.Left := 590;
      btnOK.Top := 440;
      btnOK.Width := 134;
      btnOK.Height := 40;
      btnOK.Font.Name := 'Tahoma';
      btnOK.Font.Height := -18;
      btnOK.Caption := fbtn;
      btnOK.ModalResult := 1;

      frm.ShowModal;
      frm.Free;
    end;
  end
  else
  begin
    showmessage ('ผู้ป่วยมี SOS Score ='+fptname+' ');
  end;
end;


end;
