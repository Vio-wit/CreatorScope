param(
    [ValidateSet('Live', 'Demo')]
    [string]$Mode = 'Live'
)

$ErrorActionPreference = 'Stop'
$PreviousCreatorscopeDb = $env:CREATORSCOPE_DB
$PreviousCreatorscopeDemoMode = $env:CREATORSCOPE_DEMO_MODE
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

$AppPort = if ($env:CREATORSCOPE_PORT) { $env:CREATORSCOPE_PORT } else { '8765' }
if ($Mode -eq 'Demo') {
    $DatabasePath = Join-Path $ProjectRoot 'data\demo\creatorscope_demo.sqlite3'
    if (-not (Test-Path -LiteralPath $DatabasePath -PathType Leaf)) {
        throw "Offline demo dataset is missing: $DatabasePath"
    }
} else {
    $DatabasePath = if ($env:CREATORSCOPE_DB) {
        [System.IO.Path]::GetFullPath($env:CREATORSCOPE_DB)
    } else {
        Join-Path $ProjectRoot 'instance\creatorscope_unified.sqlite3'
    }
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

if ($Mode -eq 'Demo') {
    Write-Host 'CreatorScope is starting in read-only offline demo mode with the real local history dataset.'
} else {
    Write-Host 'CreatorScope is starting in live collection mode.'
}
Write-Host "CreatorScope will be available at http://127.0.0.1:$AppPort"
Write-Host 'Press Ctrl+C in this window to stop the local app.'
$env:CREATORSCOPE_DB = $DatabasePath
$env:CREATORSCOPE_DEMO_MODE = if ($Mode -eq 'Demo') { '1' } else { '0' }
try {
    & $VenvPython (Join-Path $ProjectRoot 'app.py')
} finally {
    if ($null -eq $PreviousCreatorscopeDb) { Remove-Item Env:CREATORSCOPE_DB -ErrorAction SilentlyContinue }
    else { $env:CREATORSCOPE_DB = $PreviousCreatorscopeDb }
    if ($null -eq $PreviousCreatorscopeDemoMode) { Remove-Item Env:CREATORSCOPE_DEMO_MODE -ErrorAction SilentlyContinue }
    else { $env:CREATORSCOPE_DEMO_MODE = $PreviousCreatorscopeDemoMode }
}
