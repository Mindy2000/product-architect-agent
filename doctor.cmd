@echo off
setlocal
set PYTHONUTF8=1
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "scripts\manage.py" doctor %*
) else (
  py -3.13 "scripts\manage.py" doctor %*
)
if errorlevel 1 (
  echo See docs\SETUP.md. Python 3.13 and Docker Desktop Linux containers are required.
  pause
  exit /b 1
)
