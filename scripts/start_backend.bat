@echo off
setlocal enabledelayedexpansion

REM ==============================================================================
REM SIH-2026: Backend Bootstrap & Startup Script (Windows)
REM ==============================================================================

cd /d "%~dp0.."
echo [SIH-2026] Working Directory: %CD%

if not exist ".venv\Scripts\activate.bat" (
    echo [SIH-2026] Virtual environment not found. Creating .venv...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment. Ensure Python 3.11+ is installed.
        exit /b 1
    )
)

echo [SIH-2026] Activating virtual environment...
call .venv\Scripts\activate.bat

echo [SIH-2026] Verifying dependencies...
python -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies from requirements.txt.
    exit /b 1
)

echo [SIH-2026] Launching backend server on port 8000...
python -m uvicorn app.main:app --app-dir backend/backend --port 8000
