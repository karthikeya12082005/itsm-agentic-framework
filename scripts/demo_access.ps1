# demo_access.ps1
# Wrapper for scripts/demo.py (access-request demo). Run in a NORMAL terminal from anywhere:
#   powershell -ExecutionPolicy Bypass -File scripts\demo_access.ps1 [-AutoApprove] [-Local] [-RulesOnly]
param([switch]$AutoApprove, [switch]$Local, [switch]$RulesOnly)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
$env:PYTHONIOENCODING = "utf-8"
$a = @("access")
if ($Local) { $a += @("--source", "local") }
if ($AutoApprove) { $a += "--auto-approve" }
if ($RulesOnly) { $a += "--rules-only" }
& $py (Join-Path $root "scripts\demo.py") @a
exit $LASTEXITCODE