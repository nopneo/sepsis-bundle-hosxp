var
fvn : string;
fhn : string;
fptname : string;
fage : string;

begin

fvn := GetGlobalValue('VN');
fhn := getsqldata('select hn from opdscreen where vn="'+fvn+'" ');
fage := getsqldata('select age_y from vn_stat where vn="'+fvn+'" ');
fptname := '';
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
end;

if fptname > 3 then
begin
showmessage ('ผู้ป่วยมี SOS Score ='+fptname+' ');
end;


end;