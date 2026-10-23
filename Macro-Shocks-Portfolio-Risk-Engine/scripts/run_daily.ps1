# Runs the daily market report and appends its output to reports\daily_task.log.
# Used by the Windows scheduled task described in docs/RUTINA_DIARIA.md.

$project = Split-Path -Parent $PSScriptRoot
Set-Location $project
New-Item -ItemType Directory -Force -Path reports | Out-Null

# Python writes redirected output in the Windows code page by default, which cannot
# encode the report's box-drawing characters; force UTF-8 so the task does not fail.
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

"==== $(Get-Date -Format 'yyyy-MM-dd HH:mm') ====" | Out-File -Append -Encoding utf8 reports\daily_task.log
# the private environment from scripts\install.ps1 if there is one, else the system Python
$py = if (Test-Path .venv\Scripts\python.exe) { ".venv\Scripts\python.exe" } else { "python" }
& $py -m macro_shock daily 2>&1 | Out-File -Append -Encoding utf8 reports\daily_task.log
