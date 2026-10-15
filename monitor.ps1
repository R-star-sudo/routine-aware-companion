[CmdletBinding()]
param(
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$Inputs
)

$rootDir = $PSScriptRoot
$pyExe = if (Test-Path "$rootDir\backend\.venv\Scripts\python.exe") {
    "$rootDir\backend\.venv\Scripts\python.exe"
} elseif (Test-Path "$env:USERPROFILE\.platformio\penv\Scripts\python.exe") {
    "$env:USERPROFILE\.platformio\penv\Scripts\python.exe"
} else {
    "python"
}

# Stop any orphaned live_monitor.py processes holding the COM port from earlier runs
try {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -eq "python.exe" -and $_.CommandLine -like "*live_monitor.py*" -and $_.ProcessId -ne $PID
    } | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
} catch {}

& $pyExe "$rootDir\scripts\live_monitor.py" @Inputs
