#Requires -RunAsAdministrator
# Install the local "IT Help" portal for one Windows user (TODO 6.4). Run in an ADMINISTRATOR terminal:
#   powershell -ExecutionPolicy Bypass -File scripts\portal\install_it_help.ps1 [-UserName employee01]
# 1. sign-in token in the user's own profile (only that user, Administrators and SYSTEM can read it)
# 2. launcher C:\ProgramData\ITSM-Portal\open_it_help.ps1 (users may read/run it, not change it)
# 3. desktop shortcut "IT Help" for that user
# The portal itself runs in the project owner's normal terminal:  python -m src.portal
param([string]$UserName = "employee01")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
if (-not (Get-LocalUser -Name $UserName -ErrorAction SilentlyContinue)) { throw "Windows user $UserName does not exist" }

Push-Location $root
try { & $py -m src.portal register $UserName; if ($LASTEXITCODE -ne 0) { throw "token registration failed" } }
finally { Pop-Location }

$dir = "C:\ProgramData\ITSM-Portal"
New-Item -ItemType Directory -Force $dir | Out-Null
$launcher = @'
# IT Help launcher (runs as the signed-in employee): reads the personal sign-in token and opens the local portal.
Add-Type -AssemblyName PresentationFramework
$f = Join-Path $env:LOCALAPPDATA "ITSM-Portal\token.txt"
if (-not (Test-Path $f)) {
    [System.Windows.MessageBox]::Show("IT Help is not set up for your account. Please contact IT.", "IT Help") | Out-Null
    exit 1
}
try { Invoke-WebRequest "http://127.0.0.1:8810/health" -UseBasicParsing -TimeoutSec 3 | Out-Null }
catch {
    [System.Windows.MessageBox]::Show("The IT Help service is not running right now. Please try again later or contact IT.",
                                      "IT Help") | Out-Null
    exit 1
}
$t = (Get-Content $f -Raw).Trim()
Start-Process "http://127.0.0.1:8810/login?t=$t"
'@
Set-Content -Path "$dir\open_it_help.ps1" -Value $launcher -Encoding UTF8
# Administrators + SYSTEM full control, Users read & execute only (nobody else can change the launcher)
icacls $dir /inheritance:r /grant:r "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-545:(OI)(CI)RX" | Out-Null

$desk = "C:\Users\$UserName\Desktop"
New-Item -ItemType Directory -Force $desk | Out-Null
$sh = (New-Object -ComObject WScript.Shell).CreateShortcut("$desk\IT Help.lnk")
$sh.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$sh.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$dir\open_it_help.ps1`""
$sh.IconLocation = "$env:SystemRoot\System32\shell32.dll,23"
$sh.Description = "Ask the AI Service Desk for access or report an IT problem"
$sh.Save()
Write-Host "IT Help installed for $UserName : desktop shortcut '$desk\IT Help.lnk'" -ForegroundColor Green
Write-Host "Start the portal in the project owner's normal terminal:  python -m src.portal" -ForegroundColor Yellow
