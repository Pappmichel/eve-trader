@echo off
REM EVE Trader launcher (Windows) - double-click this file.
REM Creates the virtual environment/npm deps on first run, then starts the
REM FastAPI backend + React frontend (dev mode) and opens the browser.
REM No PyCharm / terminal knowledge needed.

cd /d "%~dp0"

REM .venv contains absolute paths to the Python that created it, so a copy
REM synced in via Dropbox from a different PC will not run here. Detect that
REM instead of silently falling back to some other global Python on PATH.
set VENV_OK=0
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "" >nul 2>nul
    if not errorlevel 1 set VENV_OK=1
)
if "%VENV_OK%"=="0" (
    if exist ".venv" (
        echo [Setup] Existing .venv is not usable on this machine ^(e.g. synced from a different PC^) - recreating...
        rmdir /s /q ".venv"
    )
    echo [Setup] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo Error: Python was not found. Please install Python 3.10+ from https://python.org
        echo and check "Add Python to PATH" during setup.
        pause
        exit /b 1
    )
    REM venvs are machine-specific and must be rebuilt per PC - tell Dropbox
    REM to stop syncing this one so this problem cannot recur.
    powershell -NoProfile -Command "Set-Content -Path '.venv' -Stream com.dropbox.ignored -Value 1" >nul 2>nul
)

echo [Setup] Checking/installing Python dependencies...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
".venv\Scripts\python.exe" -m pip install -q -e .

if not exist ".env" (
    echo [Setup] Copying .env.example to .env - please fill in EVE_SSO_CLIENT_ID!
    copy .env.example .env >nul
    notepad .env
)

if not exist "config.yaml" (
    echo [Setup] Copying config.example.yaml to config.yaml - please adjust structure_id / characters!
    copy config.example.yaml config.yaml >nul
    notepad config.yaml
)

REM Same story as .venv: node_modules (native binaries, absolute paths in
REM some binstubs) is not portable between machines either.
set NODE_OK=0
if exist "frontend\node_modules\.bin\vite.cmd" set NODE_OK=1
if "%NODE_OK%"=="0" (
    if exist "frontend\node_modules" (
        echo [Setup] Existing frontend\node_modules is not usable on this machine - reinstalling...
        rmdir /s /q "frontend\node_modules"
    )
    echo [Setup] Installing frontend dependencies ^(one-time, may take a while^)...
    pushd frontend
    call npm install
    popd
    powershell -NoProfile -Command "Set-Content -Path 'frontend\node_modules' -Stream com.dropbox.ignored -Value 1" >nul 2>nul
)

echo.
echo [Start] Backend (FastAPI) starting in its own window...
start "EVE Trader - Backend" cmd /k "cd /d "%~dp0" && .venv\Scripts\python.exe -m uvicorn eve_trader.api.main:app --port 8000"

timeout /t 2 >nul

echo [Start] Frontend starting - browser opens automatically...
pushd frontend
start "" http://localhost:5173
call npm run dev
popd
