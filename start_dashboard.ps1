# Routine-Aware Companion - PowerShell Launcher
param(
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$Inputs
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

$collectorArgs = if ($Inputs -and $Inputs.Count -gt 0) {
    $Inputs -join " "
} else {
    "07:00:00 1"
}

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "         ROUTINE-AWARE COMPANION - 1-CLICK LAUNCHER                   " -ForegroundColor White -BackgroundColor DarkBlue
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host " Simulation Clock Parameters: $collectorArgs" -ForegroundColor Yellow
Write-Host ""

# Stop any orphaned processes on ports 8000 and 5173 before launching
foreach ($port in 8000, 5173) {
    try {
        $existingPids = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique
        foreach ($procId in $existingPids) {
            if ($procId -gt 0) {
                Write-Host "Releasing port $port (stopping previous process PID $procId)..." -ForegroundColor Yellow
                Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
            }
        }
    } catch {}
}

# Stop any orphaned live_monitor or collector processes that might be locking COM ports
try {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { 
        $_.Name -eq "python.exe" -and (
            $_.CommandLine -like "*live_monitor.py*" -or 
            $_.CommandLine -like "*app.collector*"
        )
    } | ForEach-Object {
        Write-Host "Releasing serial port (stopping previous process PID $($_.ProcessId))..." -ForegroundColor Yellow
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
} catch {}
Start-Sleep -Milliseconds 500

# 1. Start Backend API Server
Write-Host "[1/3] Starting FastAPI Backend on http://127.0.0.1:8000..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-ExecutionPolicy", "Bypass", "-NoExit", "-Command", "cd '$root\backend'; & '.\.venv\Scripts\python.exe' -m uvicorn app.main:app --reload --port 8000"

Start-Sleep -Seconds 2

# 2. Start Base Station Collector
Write-Host "[2/3] Starting Base Station Collector on COM5 ($collectorArgs)..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-ExecutionPolicy", "Bypass", "-NoExit", "-Command", "cd '$root\backend'; & '.\.venv\Scripts\python.exe' -m app.collector --serial-port COM5 --baudrate 115200 $collectorArgs"

Start-Sleep -Seconds 1

# 3. Start Frontend UI
Write-Host "[3/3] Starting Frontend Dashboard..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-ExecutionPolicy", "Bypass", "-NoExit", "-Command", "cd '$root\frontend'; npm.cmd run dev"

Start-Sleep -Seconds 3

# Open browser
Start-Process "http://localhost:5173"

Write-Host ""
Write-Host "======================================================================" -ForegroundColor Green
Write-Host " All 3 services have been launched in separate terminal windows:" -ForegroundColor Green
Write-Host "   1. Backend API:     http://127.0.0.1:8000" -ForegroundColor White
Write-Host "   2. Serial Collector: Ingesting COM5 into API ($collectorArgs)" -ForegroundColor White
Write-Host "   3. Web Dashboard:   http://localhost:5173" -ForegroundColor White
Write-Host "======================================================================" -ForegroundColor Green
