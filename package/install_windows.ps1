# ติดตั้ง/อัปเดต Sepsis Six Bundle บน Windows 10/11 หรือ Windows Server 2016+
# ดับเบิลคลิก install_windows.bat (จะขอสิทธิ์ Administrator ให้เอง)
# รันซ้ำได้ปลอดภัย: ไม่ทับ .env, data\ และโลโก้ที่เปลี่ยนไว้
param(
    [string]$AppDir = "C:\sepsis_bundle",
    [int]$Port = 5800
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$Src = Split-Path -Parent $MyInvocation.MyCommand.Path
$Utf8NoBom = New-Object System.Text.UTF8Encoding $false

function Say($msg) { Write-Host "`n==> $msg" -ForegroundColor Magenta }
function Die($msg) { Write-Host "`n!! $msg" -ForegroundColor Red; Read-Host "กด Enter เพื่อปิด"; exit 1 }

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`" -AppDir `"$AppDir`" -Port $Port"
    exit
}
if (-not (Test-Path (Join-Path $Src "app.py"))) { Die "ไม่พบ app.py — ต้องรันจากโฟลเดอร์ที่แตก zip" }

# ------------------------------------------------------------------ 1. Python
Say "1/7 ตรวจ Python (ต้อง 3.9 ขึ้นไป)"
$Py = $null
foreach ($cand in @(@("py", "-3"), @("python"))) {
    try {
        $exe = $cand[0]; $pre = @($cand | Select-Object -Skip 1)
        & $exe @pre -c "import sys; sys.exit(sys.version_info < (3, 9))" 2>$null
        if ($LASTEXITCODE -eq 0) { $Py = $cand; break }
    } catch { }
}
if (-not $Py) { Die "ไม่พบ Python 3.9+ — ติดตั้งจาก https://www.python.org/downloads/ (ติ๊ก 'Add python.exe to PATH') แล้วรันใหม่" }
$PyExe = $Py[0]; $PyPre = @($Py | Select-Object -Skip 1)
& $PyExe @PyPre -V

# ------------------------------------------------------------- 2. คัดลอกไฟล์
Say "2/7 คัดลอกไฟล์ไปที่ $AppDir"
New-Item -ItemType Directory -Force -Path "$AppDir\data", "$AppDir\static", "$AppDir\templates" | Out-Null
Copy-Item "$Src\*.py", "$Src\requirements.txt", "$Src\.env.example" -Destination $AppDir -Force
Copy-Item "$Src\templates\*" -Destination "$AppDir\templates" -Recurse -Force
Get-ChildItem "$Src\static" | ForEach-Object {
    $dest = Join-Path "$AppDir\static" $_.Name
    if (($_.Name -in @("logo.png", "favicon.png")) -and (Test-Path $dest)) { return }  # ไม่ทับโลโก้ของ รพ.
    Copy-Item $_.FullName -Destination "$AppDir\static" -Recurse -Force
}

# ------------------------------------------------------------------ 3. .env
Say "3/7 ไฟล์ตั้งค่า .env"
$EnvPath = "$AppDir\.env"
if (Test-Path $EnvPath) {
    Write-Host "มี .env อยู่แล้ว — ไม่เขียนทับ (แก้เองได้ที่ $EnvPath)"
} else {
    Write-Host "กรอกข้อมูลการเชื่อมต่อฐานข้อมูล HOSxP (แนะนำ user ที่มีสิทธิ์ SELECT อย่างเดียว)"
    $vals = [ordered]@{}
    $vals["SEPSIS_HOSPITAL_NAME"] = Read-Host "  ชื่อโรงพยาบาล (แสดงบนหน้าเว็บ/การ์ด LINE)"
    $h = Read-Host "  DB host [127.0.0.1]"; $vals["DB_HOST"] = $(if ($h) { $h } else { "127.0.0.1" })
    $p = Read-Host "  DB port [3306]"; $vals["DB_PORT"] = $(if ($p) { $p } else { "3306" })
    $n = Read-Host "  ชื่อฐานข้อมูล [hos]"; $vals["DB_NAME"] = $(if ($n) { $n } else { "hos" })
    $vals["DB_USER"] = Read-Host "  DB user"
    $sec = Read-Host "  DB password (พิมพ์แล้วไม่แสดงบนจอ)" -AsSecureString
    $vals["DB_PASS"] = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
    $vals["PORT"] = "$Port"
    if (-not $vals["SEPSIS_HOSPITAL_NAME"]) { $vals["SEPSIS_HOSPITAL_NAME"] = "โรงพยาบาล" }
    $lines = [IO.File]::ReadAllLines("$Src\.env.example", $Utf8NoBom) | ForEach-Object {
        $key = ($_ -split "=", 2)[0]
        if ($vals.Contains($key) -and $_.Contains("=")) { "$key=$($vals[$key])" } else { $_ }
    }
    [IO.File]::WriteAllLines($EnvPath, $lines, $Utf8NoBom)   # python-dotenv อ่าน BOM ไม่ได้
    $vals["DB_PASS"] = $null
}
# .env อ่านได้เฉพาะ Administrators และ SYSTEM
icacls $EnvPath /inheritance:r /grant:r "*S-1-5-32-544:F" "*S-1-5-18:F" | Out-Null

# ------------------------------------------------------- 4. venv + ไลบรารี
Say "4/7 สร้าง virtualenv และติดตั้งไลบรารี (ต้องออกอินเทอร์เน็ตไป pypi.org ได้)"
$VPy = "$AppDir\.venv\Scripts\python.exe"
if (-not (Test-Path $VPy)) { & $PyExe @PyPre -m venv "$AppDir\.venv" }
& $VPy -m pip install -q --upgrade pip
& $VPy -m pip install -q -r "$AppDir\requirements.txt"
if ($LASTEXITCODE -ne 0) { Die "ติดตั้งไลบรารีไม่สำเร็จ" }

# ----------------------------------------------------- 5. ตรวจ DB + หารหัส
Say "5/7 ตรวจการเชื่อมต่อ HOSxP และค้นหารหัส lab/ยาของโรงพยาบาล"
Push-Location $AppDir
& $VPy setup_check.py --write
$rc = $LASTEXITCODE
Pop-Location
if ($rc -ne 0) { Die "ตรวจไม่ผ่าน — แก้ค่าใน $EnvPath แล้วรัน install_windows.bat ใหม่" }

# ---------------------------------------------------- 6. Task Scheduler + firewall
Say "6/7 ตั้งให้เว็บเปิดเองตอนเปิดเครื่อง + งานแจ้งเตือนทุก 15 นาที"
$set = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 `
        -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$sys = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

Stop-ScheduledTask -TaskName "SepsisBundle-Web" -ErrorAction SilentlyContinue
$webAction = New-ScheduledTaskAction -Execute "$AppDir\.venv\Scripts\waitress-serve.exe" `
        -Argument "--listen=0.0.0.0:$Port --threads=8 app:app" -WorkingDirectory $AppDir
Register-ScheduledTask -TaskName "SepsisBundle-Web" -Action $webAction -Trigger (New-ScheduledTaskTrigger -AtStartup) `
        -Settings $set -Principal $sys -Description "Sepsis Six Bundle dashboard" -Force | Out-Null

$jobAction = New-ScheduledTaskAction -Execute "cmd.exe" `
        -Argument "/c .venv\Scripts\python.exe scheduler.py >> data\scheduler.log 2>&1" -WorkingDirectory $AppDir
$jobTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 15)
$jobSet = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -StartWhenAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "SepsisBundle-Scheduler" -Action $jobAction -Trigger $jobTrigger `
        -Settings $jobSet -Principal $sys -Description "Sepsis Six Bundle alerts every 15 minutes" -Force | Out-Null

$rule = "Sepsis Bundle $Port"
if (-not (Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $rule -Direction Inbound -Protocol TCP -LocalPort $Port `
        -Profile Domain, Private -Action Allow | Out-Null
}
Write-Host "เปิดพอร์ต $Port เฉพาะเครือข่าย Domain/Private — อย่าเปิดออกอินเทอร์เน็ต (มีข้อมูลผู้ป่วย ไม่มีระบบล็อกอิน)"

Start-ScheduledTask -TaskName "SepsisBundle-Web"

# ---------------------------------------------------------------- 7. ตรวจ
Say "7/7 ตรวจสอบการทำงาน"
Start-Sleep -Seconds 5
try {
    Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Port/healthz" -TimeoutSec 15 | Out-Null
    Write-Host "หน้าเว็บตอบสนองปกติ" -ForegroundColor Green
} catch {
    Write-Host "หน้าเว็บยังไม่ตอบ — ดูสถานะใน Task Scheduler > SepsisBundle-Web" -ForegroundColor Yellow
}
$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
       Select-Object -First 1).IPAddress
Write-Host @"

ติดตั้งเสร็จแล้ว
  แดชบอร์ด     : http://${ip}:$Port/
  ตั้งค่าแจ้งเตือน : http://${ip}:$Port/settings  (กรอก Client Key / Secret Key ของ MOPH Notify)
  ไฟล์ตั้งค่า     : $EnvPath
  log แจ้งเตือน   : $AppDir\data\scheduler.log
"@
Read-Host "กด Enter เพื่อปิด"
