$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectRoot

$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw 'Project environment is missing. Run .\setup.ps1 first.'
}

Write-Host 'CreatorScope will be available at http://127.0.0.1:8765'
Write-Host 'Press Ctrl+C in this window to stop the local app.'
& $VenvPython (Join-Path $ProjectRoot 'app.py')
