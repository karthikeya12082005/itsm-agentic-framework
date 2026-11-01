#Requires -RunAsAdministrator
param([string]$UserName = "employee01")
$ErrorActionPreference = "Stop"

if (Get-LocalUser -Name $UserName -ErrorAction SilentlyContinue) {
    Write-Host "$UserName already exists." -ForegroundColor Yellow
} else {
    $pw = Read-Host -AsSecureString "Set a password for $UserName"
    New-LocalUser -Name $UserName -Password $pw -FullName "Employee 01 (ITSM demo)" `
        -Description "Restricted standard user for the ITSM agent demo" -PasswordNeverExpires | Out-Null
    Add-LocalGroupMember -Group "Users" -Member $UserName
    Write-Host "Created $UserName as a STANDARD user." -ForegroundColor Green
}
$isAdmin = Get-LocalGroupMember -Group "Administrators" | Where-Object { $_.Name -like "*\$UserName" }
if ($isAdmin) { Write-Host "WARNING: $UserName is an administrator!" -ForegroundColor Red }
else { Write-Host "Check OK: $UserName is NOT an administrator." -ForegroundColor Green }