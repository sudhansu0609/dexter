@echo off
setlocal enabledelayedexpansion

REM ===================================================
REM Dexter - one-click launcher, with a preflight check
REM
REM The previous version pointed at ..\MidnightBuzz\backend\voice_env, which no
REM longer exists. It fell back to system python with only a [WARN] - and that
REM interpreter was missing APScheduler and kokoro-onnx, so reminders silently
REM never fired and speech silently fell back to edge-tts. Nothing said so.
REM
REM Now: resolve the interpreter explicitly, then run preflight, which reports
REM exactly what is missing or degraded and refuses to launch on a real failure.
REM ===================================================

REM Ports are not set here any more (GUARDIAN_PLAN.md section 11). Dexter asks
REM for DEXTER_PORT (default 8098), steps forward if something already holds it,
REM and publishes where it landed in %LOCALAPPDATA%\Buzzcaf\ports.json. Set
REM DEXTER_PORT yourself, here or in .env, only if you want a different wish.
set "DEXTER_DIR=%~dp0"
if defined DEXTER_PORT echo Preferred port: %DEXTER_PORT%

echo ===================================================
echo  Dexter AI Super Assistant
echo ===================================================

REM ── Pick the interpreter ────────────────────────────
REM Order: DEXTER_PYTHON override, a project-local venv, then system python.
set "PYTHON_EXE="
if defined DEXTER_PYTHON (
    if exist "%DEXTER_PYTHON%" set "PYTHON_EXE=%DEXTER_PYTHON%"
)
if not defined PYTHON_EXE (
    if exist "%DEXTER_DIR%.venv\Scripts\python.exe" set "PYTHON_EXE=%DEXTER_DIR%.venv\Scripts\python.exe"
)
if not defined PYTHON_EXE (
    set "PYTHON_EXE=python"
)
echo Interpreter: %PYTHON_EXE%
echo.

REM ── Preflight ───────────────────────────────────────
"%PYTHON_EXE%" "%DEXTER_DIR%backend\preflight.py"
if errorlevel 1 (
    echo.
    echo Preflight failed. Fix the items marked [FAIL] above, then run this again.
    echo.
    pause
    exit /b 1
)
echo.

REM ── Ports ───────────────────────────────────────────
REM This used to Stop-Process whatever held 3006 or 8098. It is gone: killing
REM another app to take its port is exactly what section 11 outlaws, and on
REM this machine 8098's neighbour is the Studio. Dexter steps forward instead.

echo Launching Dexter...
"%PYTHON_EXE%" "%DEXTER_DIR%backend\desktop_app.py"

echo.
echo ===================================================
echo  Dexter closed. Log: data\dexter.log
echo ===================================================
echo.
pause
