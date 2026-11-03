@echo off
setlocal
set PYTHONUTF8=1
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call install.cmd
if errorlevel 1 exit /b 1
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "scripts\manage.py" serve %*
) else (
  py -3.13 "scripts\manage.py" serve %*
)
if errorlevel 1 (
  echo See docs\SETUP.md. Python 3.13 and Docker Desktop Linux containers are required.
  pause
  exit /b 1
)
