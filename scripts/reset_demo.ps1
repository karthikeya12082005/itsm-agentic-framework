# reset_demo.ps1
# Wrapper for scripts/demo.py (reset after a demo: lab healthy, endpoint grants rolled back, open tickets cancelled). Run in a NORMAL terminal from anywhere:
#   powershell -ExecutionPolicy Bypass -File scripts\reset_demo.ps1 [-Apply] [-CancelOpen] [-Local]   (without flags: dry run)
param([switch]$Apply, [switch]$CancelOpen, [switch]$Local)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
$env:PYTHONIOENCODING = "utf-8"
$a = @("reset")
if ($Local) { $a += @("--source", "local") }
if ($Apply) { $a += "--apply" }
if ($CancelOpen) { $a += "--cancel-open" }
& $py (Join-Path $root "scripts\demo.py") @a
exit $LASTEXITCODE