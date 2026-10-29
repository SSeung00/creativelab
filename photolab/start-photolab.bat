@echo off
title PhotoLab
cd /d "%~dp0"

rem Opens as its own window. Edge names the taskbar entry after the host (not the port), so each app
rem uses its own *.localhost host to get its own taskbar icon.
set APP_URL=http://photolab.localhost:8002/

rem  start-photolab.bat           normal: console window + opens the browser
rem  start-photolab.bat --quiet   background (Windows start-up): no browser, output to data\server.log
set QUIET=
if /i "%~1"=="--quiet" set QUIET=1

rem Already running (e.g. started at log-in)? Just open it.
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8002 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if not errorlevel 1 (
  if not defined QUIET start msedge --app=%APP_URL%
  exit /b 0
)

rem First run: create the Python environment.
if not exist backend\.venv\Scripts\python.exe (
  echo Setting up Python packages ^(first run only^)...
  pushd backend
  uv sync
  popd
)

rem Rebuild the web app only when a source file is newer than the last build.
powershell -NoProfile -Command "$d='frontend\dist\index.html'; if(!(Test-Path $d)){exit 1}; $t=(Get-Item $d).LastWriteTime; if(Get-ChildItem frontend\src,frontend\public,frontend\index.html -Recurse -File | Where-Object {$_.LastWriteTime -gt $t} | Select-Object -First 1){exit 1}; exit 0"
if errorlevel 1 (
  echo Building the web app ^(first run or after an update^)...
  pushd frontend
  if not exist node_modules call npm install
  call npm run build
  popd
)

if defined QUIET (
  if not exist data mkdir data
  cd backend
  .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8002 --timeout-graceful-shutdown 2 >> ..\data\server.log 2>&1
  exit /b
)

echo.
echo PhotoLab is running at http://127.0.0.1:8002
echo Phone: open the Tailscale address shown in PhotoLab Settings.
echo Keep this window open. Closing it stops PhotoLab.
echo.

start "" /b cmd /c "timeout /t 4 /nobreak >nul & start msedge --app=%APP_URL%"

cd backend
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8002 --timeout-graceful-shutdown 2
