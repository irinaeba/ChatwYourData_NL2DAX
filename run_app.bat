@echo off
REM ============================================================
REM FastAPI Web App Launcher for Natural Language to DAX
REM ============================================================

echo.
echo ============================================================
echo   Natural Language to DAX Query Generator
echo   FastAPI Web Application
echo ============================================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH
    echo Please install Python 3.8+ from https://www.python.org
    pause
    exit /b 1
)

echo [OK] Python found
echo.

REM Check if virtual environment exists
if not exist "venv\" (
    echo Creating virtual environment...
    python -m venv venv
    echo [OK] Virtual environment created
)

REM Activate virtual environment
echo Activating virtual environment...
call venv\Scripts\activate.bat
echo [OK] Virtual environment activated

echo.

REM Install dependencies
echo Installing/updating dependencies...
pip install -q -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies
    pause
    exit /b 1
)
echo [OK] Dependencies installed

echo.
echo ============================================================
echo Starting FastAPI Server...
echo ============================================================
echo.
echo Web UI will be available at:
echo    http://localhost:8000
echo.
echo API Documentation:
echo    http://localhost:8000/docs
echo.
echo Press Ctrl+C to stop the server
echo.

REM Start the server
uvicorn app:app --reload --host 0.0.0.0 --port 8000

pause
