@echo off
rem Show connectthebots.net numbers: visitors, film plays, sign-ups, agents, referrals.
rem Read-only. Uses the SSH key DEPLOY set up. Optional: STATS 30  (days, up to 30)
setlocal
set "DAYS=%~1"
if "%DAYS%"=="" set "DAYS=14"
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0site\deploy\stats.ps1" -Server root@connectthebots.net -Days %DAYS% -Csv "%~dp0ctb-numbers.csv"
if errorlevel 1 echo. & echo STATS stopped. Paste this window's text in the Connect the Bots thread.
pause
