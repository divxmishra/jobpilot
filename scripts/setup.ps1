# One-time local setup on Windows (PowerShell):  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw "Python 3.11+ is required: https://www.python.org/downloads/" }
if (-not (Test-Path ".venv")) { python -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\pip.exe install -r requirements-local.txt
# Chromium for application prefill (optional, ~150 MB) and UI tests
& .\.venv\Scripts\python.exe -m playwright install chromium
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host "Created .env from .env.example (edit it to add optional keys)." }
Write-Host "Setup complete. Start JobPilot with: powershell -ExecutionPolicy Bypass -File scripts\run.ps1  ->  http://127.0.0.1:8000"
