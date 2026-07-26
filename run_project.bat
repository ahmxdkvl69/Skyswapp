@echo off
setlocal

cd /d "%~dp0"

set "PYTHON_CMD="

where python >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=python"

if "%PYTHON_CMD%"=="" (
    where py >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=py -3"
)

if "%PYTHON_CMD%"=="" (
    echo Python is not installed or not available on PATH.
    echo Install Python 3.11+ from https://www.python.org/downloads/
    pause
    exit /b 1
)

rem A venv copied from another machine still has python.exe but cannot run,
rem because it points at a base install that isn't here. Test it, don't trust it.
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" --version >nul 2>nul
    if errorlevel 1 (
        echo Existing virtual environment is broken, rebuilding...
        rmdir /s /q venv
    )
)

if not exist "venv\Scripts\python.exe" (
    echo Creating virtual environment...
    %PYTHON_CMD% -m venv venv
    if errorlevel 1 exit /b %errorlevel%
)

echo Installing dependencies...
"venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 exit /b %errorlevel%

findstr /b /c:"DATABASE_URL=postgres" .env >nul 2>nul
if errorlevel 1 (
    echo.
    echo   DATABASE_URL is not set in .env
    echo   Paste your Neon connection string there, then run: python init_db.py
    echo.
    pause
    exit /b 1
)

echo Starting SkySwap at http://127.0.0.1:5000
"venv\Scripts\python.exe" app.py
exit /b %errorlevel%
