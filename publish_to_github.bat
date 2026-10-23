@echo off
REM Publishes this folder to GitHub. Run it the first time to create the repository, and again after each
REM update: it commits the changes, tags the version (e.g. v1.2.0) and pushes. GitHub then builds the .exe.
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Publish WindowPilot to GitHub

where git >nul 2>nul
if errorlevel 1 (
  echo Git is required. Install it from https://git-scm.com/download/win and run this again.
  pause
  exit /b 1
)

set "PY=py -3"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
set "VER="
for /f "usebackq delims=" %%v in (`%PY% -c "from version import __version__;print(__version__)"`) do set "VER=%%v"
if not defined VER (
  echo Could not read version.py. Is Python installed?
  pause
  exit /b 1
)
echo Publishing WindowPilot v%VER%
echo.

if not exist ".git" git init -q -b main

REM ---- who the commits are from (asked once, saved only in this project)
git config user.email >nul 2>nul
if errorlevel 1 (
  echo Git needs a name and email to label your commits.
  set /p "GNAME=Your name: "
  set /p "GEMAIL=Your GitHub email, or the private one from GitHub Settings - Emails: "
  git config user.name "!GNAME!"
  git config user.email "!GEMAIL!"
)

REM ---- commit and tag this version
git add -A
git diff --cached --quiet
if errorlevel 1 (
  git commit -q -m "WindowPilot v%VER%"
  if errorlevel 1 goto :failed
  echo Saved changes as "WindowPilot v%VER%".
) else (
  echo No file changes since the last upload.
)
git rev-parse -q --verify "refs/tags/v%VER%" >nul
if errorlevel 1 (
  git tag -a "v%VER%" -m "WindowPilot v%VER%"
  echo Tagged v%VER%.
)

git remote get-url origin >nul 2>nul
if not errorlevel 1 goto :push

REM ---- first time: create the GitHub repository
echo.
set "REPO=WindowPilot"
set /p "REPO=Name for the new GitHub repository [WindowPilot]: "
set "VIS=private"
set "ANS=n"
set /p "ANS=Make it public, so anyone can see the code? y/N: "
if /i "%ANS%"=="y" set "VIS=public"

where gh >nul 2>nul
if errorlevel 1 goto :manual_remote

REM GitHub CLI is installed: create the repository directly.
gh auth status >nul 2>nul
if errorlevel 1 gh auth login --web --git-protocol https --scopes workflow
gh auth status 2>&1 | findstr /i "workflow" >nul
if errorlevel 1 gh auth refresh -h github.com -s workflow
gh auth setup-git
gh repo create "%REPO%" --%VIS% --source . --remote origin --description "AI assistant that works inside a Windows app window you choose (MIT)"
if errorlevel 1 goto :failed
goto :push

:manual_remote
echo.
echo Your browser will open GitHub's "Create a new repository" page.
echo   1. Name it %REPO% and choose %VIS%.
echo   2. Leave "Add a README", .gitignore and license turned OFF.
echo   3. Click "Create repository", then copy the HTTPS address it shows,
echo      for example https://github.com/your-name/%REPO%.git
start "" "https://github.com/new?name=%REPO%&visibility=%VIS%"
echo.
set "URL="
set /p "URL=Paste the repository address here: "
if not defined URL (
  echo No address entered.
  pause
  exit /b 1
)
git remote add origin "%URL%"

:push
echo.
echo Uploading... If a GitHub sign-in window appears, log in there and allow access.
git push -u origin main
if errorlevel 1 goto :failed
git push origin "v%VER%"
if errorlevel 1 goto :failed

for /f "usebackq delims=" %%u in (`git remote get-url origin`) do set "URL=%%u"
set "WEB=!URL:.git=!"
echo.
echo Done! WindowPilot v%VER% is on GitHub: !WEB!
echo GitHub is now building WindowPilot-%VER%.exe. It appears under "Releases" in a few minutes.
if /i "!WEB:~0,8!"=="https://" start "" "!WEB!"
pause
exit /b 0

:failed
echo.
echo Something went wrong - see the message above. Nothing was deleted; you can fix it and run this again.
pause
exit /b 1
