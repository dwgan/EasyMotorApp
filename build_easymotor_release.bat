@echo off
setlocal

if /i "%~1"=="/?" goto :help
if /i "%~1"=="-?" goto :help

if "%~1"=="" (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_easymotor_release.ps1" -Clean
) else (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_easymotor_release.ps1" %*
)
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [ERROR] EasyMotor release build failed with exit code %EXIT_CODE%.
    pause
)

endlocal
exit /b %EXIT_CODE%

:help
echo EasyMotor one-file Windows release builder
echo.
echo Usage:
echo   build_easymotor_release.bat [-Clean] [-Version 1.2.3] [-WriteChecksum]
echo.
echo Default output: release\EasyMotor_v1.0.0_win-x64.exe
endlocal
exit /b 0
