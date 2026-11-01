#Requires -RunAsAdministrator
param([string]$UserName = "employee01")
$ErrorActionPreference = "Stop"

# Domains blocked by default. Tickets can grant some of them later (agent adds them to URLAllowlist).
$BlockedDomains = @(
    "chatgpt.com", "chat.openai.com", "openai.com",      # unapproved external AI  -> DENY
    "claude.ai", "perplexity.ai",                         # unapproved external AI  -> DENY
    "gemini.google.com",                                  # approved AI assistant   -> ALLOW via ticket
    "github.com", "pypi.org",                             # allowlist requests      -> APPROVAL via ticket
    "wetransfer.com", "mega.nz"                           # file sharing            -> DENY
)

$sid = (New-Object System.Security.Principal.NTAccount($UserName)).Translate(
        [System.Security.Principal.SecurityIdentifier]).Value
$hive = "Registry::HKEY_USERS\$sid"
$loadedByScript = $false
if (-not (Test-Path $hive)) {
    $profilePath = (Get-CimInstance Win32_UserProfile | Where-Object SID -eq $sid).LocalPath
    if (-not $profilePath) { throw "No profile for $UserName. Sign in once as $UserName first." }
    reg load "HKU\$sid" "$profilePath\NTUSER.DAT" | Out-Null
    $loadedByScript = $true
}

function Set-ListPolicy([string]$Key, [string[]]$Items) {
    if (Test-Path $Key) { Remove-Item $Key -Recurse -Force }
    New-Item -Path $Key -Force | Out-Null
    for ($i = 0; $i -lt $Items.Count; $i++) {
        New-ItemProperty -Path $Key -Name ($i + 1).ToString() -Value $Items[$i] -PropertyType String -Force | Out-Null
    }
}

try {
    $chrome = "$hive\Software\Policies\Google\Chrome"
    New-Item -Path $chrome -Force | Out-Null
    Set-ListPolicy "$chrome\URLBlocklist" $BlockedDomains
    if (-not (Test-Path "$chrome\URLAllowlist")) { New-Item -Path "$chrome\URLAllowlist" -Force | Out-Null }
    New-ItemProperty -Path $chrome -Name "DownloadRestrictions" -Value 3 -PropertyType DWord -Force | Out-Null
    New-ItemProperty -Path $chrome -Name "IncognitoModeAvailability" -Value 1 -PropertyType DWord -Force | Out-Null

    $edge = "$hive\Software\Policies\Microsoft\Edge"
    New-Item -Path $edge -Force | Out-Null
    Set-ListPolicy "$edge\URLBlocklist" @("*")

    Write-Host "Policies applied for $UserName ($sid)." -ForegroundColor Green
    Get-ItemProperty "$chrome\URLBlocklist" | Select-Object * -ExcludeProperty PS* | Format-List
}
finally {
    if ($loadedByScript) { [gc]::Collect(); Start-Sleep -Seconds 1; reg unload "HKU\$sid" | Out-Null }
}