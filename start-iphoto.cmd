@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Please run scripts\setup.ps1 first.
  pause
  exit /b 1
)
start "iPhoto" ".venv\Scripts\pythonw.exe" "%~dp0run.py"

