# Launch the Macgear 3PL Portal on http://127.0.0.1:8000
# First run: creates the venv, installs deps, creates the SQLite DB and seeds demo data.
# Local dev uses SQLite (no setup). On the droplet set DATABASE_URL to the managed Postgres.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install --upgrade pip
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
}

if (-not (Test-Path "data\app.db")) {
    Write-Host "Seeding database..."
    .\.venv\Scripts\python.exe -m app.seed
}

Write-Host "Starting app at http://127.0.0.1:8000  (Ctrl+C to stop)"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
