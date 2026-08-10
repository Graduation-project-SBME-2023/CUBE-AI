@echo off
:: OHIF-Standalone launcher  ·  Production / GPU
:: Double-click this file to launch
::
:: This wrapper calls the PowerShell script in the same folder.
:: It also enables running unsigned local scripts without changing system policy.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch.ps1"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [!] Something went wrong. See error above.
    pause
)