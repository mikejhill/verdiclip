@echo off
setlocal
set "launcher=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%launcher%" (
    echo VerdiClip is not installed. Run "uv sync" in this directory first. 1>&2
    exit /b 1
)
start "" /D "%~dp0" "%launcher%" -m verdiclip
exit /b %errorlevel%
