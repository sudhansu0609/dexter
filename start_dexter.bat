@echo off
setlocal enabledelayedexpansion

REM ===================================================
REM Dexter Personal AI Super Assistant - One-Click Launcher
REM ===================================================

set FRONTEND_PORT=3006
set BACKEND_PORT=8098

echo ===================================================
echo Starting Dexter AI Super Assistant Engine...
echo ===================================================

REM Stop processes using the configured ports if they exist
echo Clearing ports %FRONTEND_PORT% and %BACKEND_PORT%...
powershell -NoProfile -Command "Get-Process -Id (Get-NetTCPConnection -LocalPort %FRONTEND_PORT%,%BACKEND_PORT% -ErrorAction SilentlyContinue).OwningProcess -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue" >nul 2>&1

REM Get current directory of the batch file
set "DEXTER_DIR=%~dp0"
set "BACKEND_DIR=%DEXTER_DIR%backend"

REM Find python executable or virtual environment
set "PYTHON_EXE=%DEXTER_DIR%..\MidnightBuzz\backend\voice_env\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo [WARN] Virtual environment python not found at "%PYTHON_EXE%". Falling back to system python.
    set "PYTHON_EXE=python"
)

echo Starting Dexter Native Desktop App Window...
start "Dexter Desktop Window" cmd /k "cd /d "%DEXTER_DIR%" && ".\node_modules\electron\dist\electron.exe" ."

echo Starting Dexter FastAPI Backend Engine on port %BACKEND_PORT%...
start "Dexter Backend Engine" cmd /k "cd /d "%DEXTER_DIR%" && set PYTHONIOENCODING=utf-8 && "%PYTHON_EXE%" -X utf8 -m uvicorn backend.main:app --host 0.0.0.0 --port %BACKEND_PORT% --reload"

echo.
echo ===================================================
echo Dexter Desktop Super Assistant Started Successfully!
echo.
echo Native Desktop App    : Electron Window Active
echo FastAPI Backend Engine: http://localhost:%BACKEND_PORT%
echo ===================================================
echo.
pause
