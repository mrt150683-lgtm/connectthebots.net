@echo off
rem Update connectthebots.net from this dedicated clone. Double-click or run DEPLOY.
rem First run: creates an SSH key for this PC and asks for the root password once.
setlocal
set "HOST=connectthebots.net"
set "KEY=%USERPROFILE%\.ssh\ctb_deploy"
cd /d "%~dp0"

echo == Get the latest main
git diff --quiet || (echo This folder has local changes. Use a clean clone for deploying. & goto :fail)
git checkout -q main || goto :fail
git pull -q --ff-only origin main || goto :fail

if not exist "%USERPROFILE%\.ssh" mkdir "%USERPROFILE%\.ssh"
if not exist "%KEY%" (
  echo == Create a deploy key for this PC
  ssh-keygen -q -t ed25519 -N "" -C ctb-deploy -f "%KEY%" || goto :fail
)
findstr /c:"Host %HOST%" "%USERPROFILE%\.ssh\config" >nul 2>&1 || (
  >>"%USERPROFILE%\.ssh\config" echo.
  >>"%USERPROFILE%\.ssh\config" echo Host %HOST%
  >>"%USERPROFILE%\.ssh\config" echo   User root
  >>"%USERPROFILE%\.ssh\config" echo   IdentityFile ~/.ssh/ctb_deploy
)
ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 root@%HOST% true >nul 2>&1
if errorlevel 1 (
  echo == One-time setup: type the server root password so this PC can deploy without it
  type "%KEY%.pub" | ssh root@%HOST% "umask 077; mkdir -p ~/.ssh; cat >> ~/.ssh/authorized_keys" || goto :fail
  ssh -o BatchMode=yes -o ConnectTimeout=15 root@%HOST% true >nul 2>&1 || (echo The key was not accepted. & goto :fail)
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0site\deploy\update-production.ps1" -Server root@%HOST% || goto :fail
pause
exit /b 0

:fail
echo.
echo DEPLOY stopped. Paste this window's text in the Connect the Bots thread.
pause
exit /b 1
