@echo off
:: OHIF-Standalone launcher  .  Backend only
:: Double-click this file to launch
::
:: This wrapper calls launch.backend.ps1 in the same folder.
:: It also enables running unsigned local scripts without changing system policy.
::
:: Backend only - no frontend. Use launcher.windows.bat for both.
:: No image updates - runs whatever images are already on this machine.
:: Only the containers whose config changed (storage mounts / storage env
:: vars) are recreated.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch.backend.ps1"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [!] Something went wrong. See error above.
    pause
)
