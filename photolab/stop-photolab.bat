@echo off
rem Stop a PhotoLab server running in the background (whatever is listening on port 8002).
powershell -NoProfile -Command ^
  "$c = Get-NetTCPConnection -LocalPort 8002 -State Listen -ErrorAction SilentlyContinue;" ^
  "if ($c) { $c | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }; 'PhotoLab stopped.' } else { 'PhotoLab is not running.' }"
pause
