@echo off
title Install SPCM
echo Installing SPCM. This can take a few minutes the first time.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\setup.ps1"
if errorlevel 1 (
  echo.
  echo Something went wrong - see the red text above.
  echo Take a photo or copy it and send it to whoever helps you with SPCM.
)
echo.
pause
