# Generates the executive HTML report and opens it in the browser.
# Used by the desktop shortcut that scripts\install.ps1 creates.
# Extra arguments are passed through, e.g.: scripts\report.ps1 --quick

$project = Split-Path -Parent $PSScriptRoot
Set-Location $project
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$py = if (Test-Path .venv\Scripts\python.exe) { ".venv\Scripts\python.exe" } else { "python" }
& $py -m macro_shock report --open @args
if ($LASTEXITCODE -ne 0) { Read-Host "The report failed (see above). Press Enter to close" }
