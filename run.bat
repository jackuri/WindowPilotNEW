@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python 3.10 or newer is required. Download it from https://www.python.org/downloads/
  echo During setup, tick "Add python.exe to PATH".
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  py -3 -m venv .venv || (pause & exit /b 1)
)
if not exist ".venv\installed.flag" (
  echo Installing dependencies, this only happens once...
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || (pause & exit /b 1)
  echo ok> ".venv\installed.flag"
)
start "" ".venv\Scripts\pythonw.exe" main.py
