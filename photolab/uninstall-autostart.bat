@echo off
rem Stop starting PhotoLab at log-in (removes the shortcut made by install-autostart.bat).
powershell -NoProfile -Command "Remove-Item -ErrorAction SilentlyContinue ([Environment]::GetFolderPath('Startup') + '\PhotoLab.lnk')"
echo PhotoLab will no longer start at log-in. (A copy running right now keeps running: use stop-photolab.bat.)
pause
