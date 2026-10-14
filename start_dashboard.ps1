# Routine-Aware Companion - PowerShell Launcher
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "         ROUTINE-AWARE COMPANION - 1-CLICK LAUNCHER                   " -ForegroundColor White -BackgroundColor DarkBlue
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Start Backend API Server
Write-Host "[1/3] Starting FastAPI Backend on http://127.0.0.1:8000..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\backend'; & '.\.venv\Scripts\python.exe' -m uvicorn app.main:app --reload --port 8000"

Start-Sleep -Seconds 2

# 2. Start Base Station Collector
Write-Host "[2/3] Starting Base Station Collector on COM5..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\backend'; & '.\.venv\Scripts\python.exe' -m app.collector --serial-port COM5 --baudrate 115200 --simulation-start '2026-10-14T07:00:00+05:30' --simulation-rate 60"

Start-Sleep -Seconds 1

# 3. Start Frontend UI
Write-Host "[3/3] Starting Frontend Dashboard..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\frontend'; npm run dev"

Start-Sleep -Seconds 3

# Open browser
Start-Process "http://localhost:5173"

Write-Host ""
Write-Host "======================================================================" -ForegroundColor Green
Write-Host " All 3 services have been launched in separate terminal windows:" -ForegroundColor Green
Write-Host "   1. Backend API:     http://127.0.0.1:8000" -ForegroundColor White
Write-Host "   2. Serial Collector: Ingesting COM5 into API" -ForegroundColor White
Write-Host "   3. Web Dashboard:   http://localhost:5173" -ForegroundColor White
Write-Host ""
Write-Host " In the dashboard browser:" -ForegroundColor Cyan
Write-Host "   Click Settings -> Data source -> Select 'Live dashboard API'" -ForegroundColor Yellow
Write-Host "======================================================================" -ForegroundColor Green
