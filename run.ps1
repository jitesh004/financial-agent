# Starts the API and the UI together.
#   ./run.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

if (-not (Test-Path "$root/.venv")) { Write-Error "Run: python -m venv .venv; .venv/Scripts/pip install -r backend/requirements.txt"; exit 1 }
if (-not (Test-Path "$root/frontend/node_modules")) { npm install --prefix "$root/frontend" }
if (-not (Test-Path "$root/data/samples") -or -not (Get-ChildItem "$root/data/samples" -ErrorAction SilentlyContinue)) {
  Write-Host "Generating sample statements..." -ForegroundColor Cyan
  & "$root/.venv/Scripts/python.exe" "$root/backend/tools/generate_samples.py"
}

Write-Host "API  -> http://127.0.0.1:8078" -ForegroundColor Green
Write-Host "UI   -> http://localhost:5173"  -ForegroundColor Green

$api = Start-Process -PassThru -NoNewWindow "$root/.venv/Scripts/python.exe" `
  @("-m","uvicorn","app.main:app","--port","8078","--app-dir","$root/backend")
try { npm run dev --prefix "$root/frontend" } finally { Stop-Process -Id $api.Id -Force -ErrorAction SilentlyContinue }
