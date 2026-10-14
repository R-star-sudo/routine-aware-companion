@echo off
setlocal
set ROOT=%~dp0

:: Find Python environment
set PY_EXE=
if exist "%ROOT%backend\.venv\Scripts\python.exe" (
    set "PY_EXE=%ROOT%backend\.venv\Scripts\python.exe"
) else if exist "%USERPROFILE%\.platformio\penv\Scripts\python.exe" (
    set "PY_EXE=%USERPROFILE%\.platformio\penv\Scripts\python.exe"
) else (
    set "PY_EXE=python"
)

"%PY_EXE%" "%ROOT%scripts\live_monitor.py" %*
