@echo off
REM ============================================================
REM   HKC Knowledge Planet - one-click launcher (Windows)
REM   Starts backend + frontend, opens the browser.
REM   Close the two popup windows (HKC-API / HKC-UI) to stop.
REM
REM   NOTE: ASCII-only on purpose. cmd.exe on a Chinese (GBK)
REM   console mis-parses UTF-8 Chinese in .bat files and breaks.
REM ============================================================
setlocal

cd /d "%~dp0"

set API_PORT=8000
set UI_PORT=8080
if "%HKC_DATA_DIR%"=="" set HKC_DATA_DIR=.\hkc_data
if "%HKC_EMBEDDING%"=="" set HKC_EMBEDDING=local

echo.
echo   HKC Knowledge Planet - starting
echo   ---------------------------------

REM Pick Python: prefer the project's own .venv so it always loads
REM THIS folder's source (never some other copy installed elsewhere).
REM Absolute path (%~dp0 = this script's dir) so it survives a later cd.
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  where python >nul 2>&1
  if errorlevel 1 (
    echo [x] No .venv and no global Python found. Install Python 3.11+ and create .venv.
    pause
    exit /b 1
  )
  set "PY=python"
  echo [!] .venv not found, falling back to global python ^(may load a copy from elsewhere^).
)
echo [i] Python: %PY%

REM Check / install backend deps
"%PY%" -c "import hkc_api" >nul 2>&1
if errorlevel 1 (
  echo [..] First run - installing backend deps...
  "%PY%" -m pip install -e ".[all]"
)

REM LLM key hint
if "%HKC_LLM_PROVIDER%"=="" set HKC_LLM_PROVIDER=anthropic
if "%HKC_LLM_API_KEY%%DEEPSEEK_API_KEY%%OPENAI_API_KEY%%ANTHROPIC_API_KEY%"=="" (
  echo [!] No LLM API key detected - needed to ingest knowledge.
  echo     DeepSeek: set HKC_LLM_PROVIDER=deepseek ^&^& set DEEPSEEK_API_KEY=sk-...
  echo     Claude:   set ANTHROPIC_API_KEY=sk-ant-...
  echo     You can still browse the UI without a key, just cannot ingest.
)

REM Start backend (new window)
echo [..] Starting backend hkc-api on port %API_PORT% ...
start "HKC-API" cmd /c ""%PY%" -m uvicorn hkc_api.main:app --host 127.0.0.1 --port %API_PORT% --log-level warning"

REM Give the backend a few seconds
timeout /t 6 /nobreak >nul

REM Start frontend (new window)
echo [..] Starting frontend hkc-ui on port %UI_PORT% ...
start "HKC-UI" cmd /c "cd /d "%~dp0hkc-ui" && "%PY%" -m http.server %UI_PORT% --bind 127.0.0.1"

timeout /t 2 /nobreak >nul

REM Open browser
start "" "http://localhost:%UI_PORT%/index.html"

echo.
echo   ---------------------------------
echo   [OK] HKC started!
echo   Frontend: http://localhost:%UI_PORT%/index.html
echo   Backend:  http://localhost:%API_PORT%
echo.
echo   In the UI, open "connection settings" and connect to http://localhost:%API_PORT%
echo   Close the popup HKC-API / HKC-UI windows to stop.
echo   ---------------------------------
echo.
pause
endlocal
