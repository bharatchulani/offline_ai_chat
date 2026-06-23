@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Start-OfflineAI.ps1" -OpenBrowser
echo.
echo Press any key to close this window.
pause >nul
