@echo off
setlocal
title Routine-Aware Companion Launcher
echo ======================================================================
echo           ROUTINE-AWARE COMPANION - 1-CLICK LAUNCHER
echo ======================================================================
echo.

set "ROOT=%~dp0"
pushd "%ROOT%"

:: Forward optional simulation clock arguments to the collector.
set "COLLECTOR_ARGS=%*"
if "%COLLECTOR_ARGS%"=="" set "COLLECTOR_ARGS=07:00:00 1"

:: Reuse a healthy API if it is already running. Otherwise try 8000, then 8001.
set "API_PORT=8000"
set "API_URL=http://127.0.0.1:%API_PORT%"
call :start_or_reuse_api
if not defined API_READY (
    echo Port 8000 did not start. Retrying the backend on port 8001...
    set "API_PORT=8001"
    set "API_URL=http://127.0.0.1:8001"
    call :start_or_reuse_api
)
if not defined API_READY goto :backend_failed

echo.
echo [2/3] Starting Base Station Collector on COM5 with time: %COLLECTOR_ARGS%...
:: Release COM5 if an older serial reader from this same project is still running.
powershell.exe -NoProfile -Command "$root = '%ROOT%'.TrimEnd([char]92); Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.Name -in @('python.exe','pythonw.exe') -and $_.CommandLine -match 'app\.collector|live_monitor\.py' -and $_.CommandLine.Contains($root) } | ForEach-Object { Write-Host ('Stopping previous project serial reader PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
start "2 - Routine Base Collector (COM5)" /D "%ROOT%backend" cmd /k ""%ROOT%backend\.venv\Scripts\python.exe" -m app.collector --serial-port COM5 --baudrate 115200 --api-url %API_URL%/api/v1/telemetry %COLLECTOR_ARGS%"

:: Avoid opening an older Vite window by choosing the first unused dashboard port.
set "FRONTEND_PORT="
for /L %%P in (5173,1,5180) do (
    if not defined FRONTEND_PORT (
        netstat -ano | findstr /R /C:":%%P .*LISTENING" >nul
        if errorlevel 1 set "FRONTEND_PORT=%%P"
    )
)
if not defined FRONTEND_PORT set "FRONTEND_PORT=5173"

echo [3/3] Starting Frontend UI on http://127.0.0.1:%FRONTEND_PORT%...
set "VITE_API_BASE_URL=%API_URL%"
start "3 - Routine Frontend UI" /D "%ROOT%frontend" cmd /k "call npm.cmd run dev -- --host 127.0.0.1 --port %FRONTEND_PORT% --strictPort"

set "FRONTEND_READY="
for /L %%I in (1,1,20) do (
    if not defined FRONTEND_READY (
        curl.exe --silent --fail http://127.0.0.1:%FRONTEND_PORT%/ >nul 2>nul && set "FRONTEND_READY=1"
        if not defined FRONTEND_READY timeout /t 1 /nobreak >nul
    )
)
if not defined FRONTEND_READY echo Frontend did not become ready. Check the frontend window for errors.
if defined FRONTEND_READY start "" http://127.0.0.1:%FRONTEND_PORT%

echo.
echo ======================================================================
echo Backend API:     %API_URL%
echo Serial Collector: COM5 -^> API (%COLLECTOR_ARGS%)
echo Web Dashboard:   http://127.0.0.1:%FRONTEND_PORT%
echo.
echo Leave the three service windows open while using the dashboard.
echo The collector window must show new packets being posted for live data.
echo ======================================================================
popd
pause
exit /b 0

:start_or_reuse_api
set "API_READY="
curl.exe --silent --fail "%API_URL%/health" >nul 2>nul && set "API_READY=1"
if defined API_READY (
    echo Reusing healthy API at %API_URL%.
    exit /b 0
)
echo [1/3] Starting FastAPI Backend on %API_URL%...
:: Reload is omitted for stable one-click startup on Windows.
start "1 - Routine Backend API (%API_PORT%)" /D "%ROOT%backend" cmd /k ""%ROOT%backend\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port %API_PORT%"
for /L %%I in (1,1,15) do (
    if not defined API_READY (
        curl.exe --silent --fail "%API_URL%/health" >nul 2>nul && set "API_READY=1"
        if not defined API_READY timeout /t 1 /nobreak >nul
    )
)
if defined API_READY echo Backend API is ready at %API_URL%.
exit /b 0

:backend_failed
echo.
echo ERROR: FastAPI did not become healthy on ports 8000 or 8001.
echo Check the backend window for the startup error, then run this launcher again.
popd
pause
exit /b 1
