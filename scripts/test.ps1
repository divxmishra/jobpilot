# Run all automated tests (backend, safety, UI end-to-end).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
& .\.venv\Scripts\python.exe -m pytest tests -q --ignore=tests/e2e_ui.py --ignore=tests/run_e2e.py
& .\.venv\Scripts\python.exe tests\run_e2e.py test-results\e2e-shots
