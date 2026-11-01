#Requires -RunAsAdministrator
param([string]$UserName = "employee01")
$sid = (New-Object System.Security.Principal.NTAccount($UserName)).Translate(
        [System.Security.Principal.SecurityIdentifier]).Value
$hive = "Registry::HKEY_USERS\$sid"; $loaded = $false
if (-not (Test-Path $hive)) {
    $p = (Get-CimInstance Win32_UserProfile | Where-Object SID -eq $sid).LocalPath
    reg load "HKU\$sid" "$p\NTUSER.DAT" | Out-Null; $loaded = $true
}
try {
    Remove-Item "$hive\Software\Policies\Google\Chrome" -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item "$hive\Software\Policies\Microsoft\Edge" -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "All browser restrictions removed for $UserName." -ForegroundColor Yellow
} finally {
    if ($loaded) { [gc]::Collect(); Start-Sleep 1; reg unload "HKU\$sid" | Out-Null }
}