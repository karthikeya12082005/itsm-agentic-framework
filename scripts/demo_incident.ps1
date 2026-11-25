# demo_incident.ps1
# Wrapper for scripts/demo.py (incident demo on the simulated services). Run in a NORMAL terminal from anywhere:
#   powershell -ExecutionPolicy Bypass -File scripts\demo_incident.ps1 [-Fault bad_config|service_down|memory_leak|high_latency|disk_full|cert_expired|db_pool_exhausted|dependency_down] [-AutoApprove] [-Local] [-RulesOnly]
param([string]$Fault = "bad_config", [switch]$AutoApprove, [switch]$Local, [switch]$RulesOnly)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
$env:PYTHONIOENCODING = "utf-8"
$a = @("incident", "--fault", $Fault)
if ($Local) { $a += @("--source", "local") }
if ($AutoApprove) { $a += "--auto-approve" }
if ($RulesOnly) { $a += "--rules-only" }
& $py (Join-Path $root "scripts\demo.py") @a
exit $LASTEXITCODE