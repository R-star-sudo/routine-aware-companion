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

& $pyExe "$rootDir\scripts\live_monitor.py" @Inputs
