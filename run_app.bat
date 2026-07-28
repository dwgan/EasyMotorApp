@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE="

if exist "%~dp0.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
)

if not defined PYTHON_EXE if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
    set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python312\python.exe"
)

if not defined PYTHON_EXE (
    where py.exe >nul 2>nul
    if not errorlevel 1 set "PYTHON_EXE=py.exe -3"
)

if not defined PYTHON_EXE (
    echo [ERROR] Python 3.10 or newer was not found.
    echo Install Python, then run this file again.
    pause
    exit /b 1
)

%PYTHON_EXE% "%~dp0robot_joint_app.py"
if errorlevel 1 (
    echo.
    echo [ERROR] RobotJointApp failed to start.
    pause
    exit /b 1
)
