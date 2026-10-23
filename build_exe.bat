@echo off
REM Builds a standalone dist\WindowPilot-<version>.exe (run run.bat once first to create the environment).
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run run.bat once first.
  pause
  exit /b 1
)
for /f %%v in ('".venv\Scripts\python.exe" make_version_info.py') do set VER=%%v
echo Building WindowPilot v%VER%...
".venv\Scripts\python.exe" -m pip install pyinstaller
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --onefile --windowed --name WindowPilot-%VER% ^
  --version-file file_version_info.txt --add-data "CHANGELOG.md;." --add-data "LICENSE;." --add-data "THIRD_PARTY_NOTICES.md;." ^
  --collect-all uiautomation --collect-all winotify ^
  --hidden-import pynput.keyboard._win32 --hidden-import pynput.mouse._win32 ^
  main.py
echo.
echo Done: dist\WindowPilot-%VER%.exe
pause
