@echo off
title Routine-Aware Companion Launcher
echo ======================================================================
echo           ROUTINE-AWARE COMPANION - 1-CLICK LAUNCHER
echo ======================================================================
echo.

set ROOT=%~dp0
cd /d "%ROOT%"

:: Step 1: Start Backend API Server
echo [1/3] Starting FastAPI Backend Server on http://127.0.0.1:8000...
start "1 - Routine Backend API" cmd /k "cd /d "%ROOT%backend" && .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000"

:: Wait 2 seconds for API to spin up
timeout /t 2 /nobreak >nul

:: Step 2: Start Base Station Serial Collector
echo [2/3] Starting Base Station Collector on COM5...
start "2 - Routine Base Collector (COM5)" cmd /k "cd /d "%ROOT%backend" && .\.venv\Scripts\python.exe -m app.collector --serial-port COM5 --baudrate 115200 --simulation-start "2026-10-15T07:00:00+05:30" --simulation-rate 60"

:: Step 3: Check / Start Frontend
echo [3/3] Checking Frontend UI on http://localhost:5173...
start "3 - Routine Frontend UI" cmd /k "cd /d "%ROOT%frontend" && npm run dev"

:: Open Browser
timeout /t 3 /nobreak >nul
start http://localhost:5173

echo.
echo ======================================================================
echo All 3 components are running in separate dedicated windows:
echo   1. Backend API:     http://127.0.0.1:8000
echo   2. Serial Collector: Streaming from COM5 -> API
echo   3. Web Dashboard:   http://localhost:5173
echo.
echo Tip: In the browser, navigate to:
echo      Settings -> Data source -> Select 'Live dashboard API'
echo ======================================================================
echo.
pause
