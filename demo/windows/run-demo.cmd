@echo off
setlocal

if "%~1"=="" (
  echo Usage: drag a PNG onto run-demo.cmd
  echo        run-demo.cmd INPUT.png [OUTPUT_DIRECTORY]
  exit /b 2
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-demo.ps1" %*
exit /b %ERRORLEVEL%
