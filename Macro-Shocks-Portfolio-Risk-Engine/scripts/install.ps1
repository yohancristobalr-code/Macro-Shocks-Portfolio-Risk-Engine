# Installs the Macro Shock Risk Engine on a Windows PC, from the project folder:
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1
# Creates a private Python environment (.venv), installs the engine, asks for the
# FRED key once (stored only in .env, never uploaded), runs the tests and puts an
# "Informe Macro" shortcut on the desktop. Safe to run again: it reuses what exists.
# (ASCII only: Windows PowerShell 5.1 misreads accents in files without a BOM.)

$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $PSScriptRoot
Set-Location $project
Write-Host "== Macro Shock Risk Engine: installation in $project ==" -ForegroundColor Cyan

# 1. Python 3.11 or newer
$python = $null
foreach ($cmd in @("python", "py")) {
    try {
        $v = & $cmd -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($v -and [version]$v -ge [version]"3.11") { $python = $cmd; break }
    } catch {}
}
if (-not $python) {
    Write-Host "Python 3.11+ not found. Install it from https://www.python.org/downloads/" -ForegroundColor Red
    Write-Host "(tick 'Add python.exe to PATH'), then run this script again."
    exit 1
}
Write-Host "1/5 Python $v found ($python)"

# 2. Private environment
if (-not (Test-Path .venv\Scripts\python.exe)) {
    & $python -m venv .venv
}
$py = Join-Path $project ".venv\Scripts\python.exe"
Write-Host "2/5 Environment .venv ready"

# 3. Engine and data libraries
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -e . fredapi pandas-market-calendars pytest
Write-Host "3/5 Engine installed"

# 4. FRED key
if (Test-Path .env) {
    Write-Host "4/5 .env already exists: keeping your FRED key"
} else {
    Write-Host "4/5 Paste your free FRED key (https://fred.stlouisfed.org/docs/api/api_key.html)."
    $secure = Read-Host "FRED key (hidden)" -AsSecureString
    $key = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)).Trim()
    if ($key -notmatch '^[a-z0-9]{32}$') {
        Write-Host "   Warning: a FRED key is usually 32 lowercase letters and digits. Check it if commands fail." -ForegroundColor Yellow
    }
    Set-Content -Path .env -Value "FRED_API_KEY=$key" -Encoding ascii
    Write-Host "   Saved in .env (listed in .gitignore: it is never uploaded)."
}

# 5. Tests and desktop shortcut
& $py -m pytest python/tests/unit -q -p no:cacheprovider | Select-Object -Last 1
$desktop = [Environment]::GetFolderPath("Desktop")
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut((Join-Path $desktop "Informe Macro.lnk"))
$lnk.TargetPath = "powershell.exe"
$lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$project\scripts\report.ps1`""
$lnk.WorkingDirectory = $project
$lnk.Save()
Write-Host "5/5 Tests run; shortcut 'Informe Macro' created on the desktop"

Write-Host ""
Write-Host "Done. Double-click 'Informe Macro' on the desktop, or run:" -ForegroundColor Green
Write-Host "   .\.venv\Scripts\Activate.ps1"
Write-Host "   python -m macro_shock report --open"
