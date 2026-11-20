# Start JobPilot locally on Windows: http://127.0.0.1:8000
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $root "backend")
& (Join-Path $root ".venv\Scripts\python.exe") -m uvicorn app.main:app --host 127.0.0.1 --port 8000
