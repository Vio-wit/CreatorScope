$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectRoot

$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $VenvPython)) {
    Write-Host 'Creating isolated Python 3.11 environment...'
    & py -3.11 -m venv (Join-Path $ProjectRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the project virtual environment with Python 3.11.' }
}

Write-Host 'Installing the pinned project dependencies into .venv...'
& $VenvPython -m pip install -r (Join-Path $ProjectRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }

$BrowserExecutable = & $VenvPython -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); print(p.chromium.executable_path); p.stop()"
if (-not (Test-Path -LiteralPath $BrowserExecutable)) {
    Write-Host 'The Playwright Chromium binary is missing; installing the matching browser...'
    & $VenvPython -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw 'Playwright Chromium installation failed.' }
} else {
    Write-Host "Playwright Chromium is available: $BrowserExecutable"
}

Write-Host 'Setup complete. Run .\run.ps1 to start CreatorScope.'
