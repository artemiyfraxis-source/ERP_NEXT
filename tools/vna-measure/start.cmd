@echo off
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0vna-measure.ps1" %*
if errorlevel 1 echo VNA tool stopped with an error.
pause
