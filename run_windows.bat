@echo off
setlocal enabledelayedexpansion

echo ==========================================
echo    SerPilas Virtual Money Server
echo ==========================================
echo.

:: Check for Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    echo Please install Python from https://www.python.org/
    pause
    exit /b
)

:: Create Virtual Environment
if not exist "venv" (
    echo [+] Creating virtual environment...
    python -m venv venv
)

:: Activate Virtual Environment
echo [+] Activating environment...
call venv\Scripts\activate

:: Install/Update Dependencies
echo [+] Checking requirements...
pip install -r requirements.txt

:: Run Application
echo [+] Starting Server...
python main.py

echo.
echo [!] Server has stopped.
pause
