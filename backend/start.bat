@echo off
setlocal

rem Start the trading-dashboard backend on http://localhost:8000
rem (the Plotly Dash analytics app is served by this same process at /analytics/).
rem The first run creates the venv and installs deps; later runs go straight to uvicorn.

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv || exit /b 1
    echo Installing dependencies...
    ".venv\Scripts\python.exe" -m pip install -e ".[dev]" || exit /b 1
)

if not exist ".env" (
    echo WARNING: backend\.env not found. The app will still start, but the universe
    echo stays empty and scanners show no rows. Copy .env.example to .env and fill in
    echo ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY.
    echo.
)

rem Calling the venv's python directly rather than activate.bat -- same interpreter,
rem no shell state to leak, and it works when the script is double-clicked.
".venv\Scripts\python.exe" -m uvicorn app.main:app --reload --port 8000
