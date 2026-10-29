@echo off
rem Start PhotoLab in the background whenever you log in to Windows.
rem Adds a shortcut to your own Start-up folder (no admin rights, nothing system-wide).
rem Undo with uninstall-autostart.bat.
cd /d "%~dp0"
powershell -NoProfile -Command ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup') + '\PhotoLab.lnk');" ^
  "$s.TargetPath = 'wscript.exe';" ^
  "$s.Arguments = '\"' + (Resolve-Path 'photolab-hidden.vbs').Path + '\"';" ^
  "$s.WorkingDirectory = (Get-Location).Path;" ^
  "$s.Description = 'PhotoLab (background)';" ^
  "$s.Save()"
if errorlevel 1 (
  echo Could not create the start-up shortcut.
  pause
  exit /b 1
)
echo PhotoLab will now start in the background when you log in.
echo Starting it now as well...
wscript.exe "%~dp0photolab-hidden.vbs"
echo.
echo Tip: so the phone can reach it, keep the PC from sleeping while plugged in
echo   (Windows Settings ^> System ^> Power ^> Sleep: Never when plugged in).
pause
