@echo off
setlocal
chcp 65001 > nul

REM Switch to the folder of this script
pushd "%~dp0"

REM Detect Python launcher (py) or python
where py >nul 2>&1
if %errorlevel%==0 (
  set "PY=py"
) else (
  where python >nul 2>&1
  if %errorlevel%==0 (
    set "PY=python"
  ) else (
    echo [ERROR] Python not found. Install from https://www.python.org/downloads/ and ensure it's on PATH.
    pause
    exit /b 1
  )
)

REM Create venv if missing
if not exist ".venv\Scripts\python.exe" (
  echo [INFO] Creating virtual environment .venv ...
  %PY% -m venv .venv
  if %errorlevel% neq 0 (
    echo [ERROR] Failed to create virtual environment.
    pause
    exit /b 1
  )
)

set "VENV_PY=.venv\Scripts\python.exe"

echo [INFO] Upgrading pip ...
%VENV_PY% -m pip install --upgrade pip

echo [INFO] Installing requirements ...
%VENV_PY% -m pip install -r requirements.txt

echo [INFO] Running AI Intensity Analyzer ...
%VENV_PY% -u main.py
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
  echo [OK] Completed.
) else (
  echo [WARN] Exit code: %RC%
)

popd
pause
endlocal
