$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectRoot

$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw 'Project environment is missing. Run .\setup.ps1 first.'
}

$PythonVersion = & $VenvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0) {
    throw 'Unable to start the project Python environment.'
}
if ([version]$PythonVersion -lt [version]'3.11') {
    throw "Python 3.11 or newer is required; found $PythonVersion."
}
Write-Host "Python $PythonVersion"

$DatabasePath = if ($env:CREATORSCOPE_DB) {
    [System.IO.Path]::GetFullPath($env:CREATORSCOPE_DB)
} else {
    Join-Path $ProjectRoot 'instance\creatorscope_unified.sqlite3'
}
if (Test-Path -LiteralPath $DatabasePath -PathType Leaf) {
    $DatabaseCheck = @'
import pathlib, sqlite3, sys
path = pathlib.Path(sys.argv[1]).resolve()
with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as database:
    integrity = database.execute("PRAGMA integrity_check").fetchone()[0]
    foreign_key_errors = database.execute("PRAGMA foreign_key_check").fetchall()
if integrity != "ok" or foreign_key_errors:
    raise SystemExit(f"Database check failed: integrity={integrity}, foreign_key_errors={len(foreign_key_errors)}")
print(f"Database check: integrity={integrity}, foreign_key_errors={len(foreign_key_errors)}")
'@
    & $VenvPython -c $DatabaseCheck $DatabasePath
    if ($LASTEXITCODE -ne 0) {
        throw "Database preflight failed: $DatabasePath"
    }
} else {
    Write-Host "Database not found; the app will initialize its schema on startup: $DatabasePath"
}

Write-Host 'CreatorScope will be available at http://127.0.0.1:8765'
Write-Host 'Press Ctrl+C in this window to stop the local app.'
& $VenvPython (Join-Path $ProjectRoot 'app.py')
