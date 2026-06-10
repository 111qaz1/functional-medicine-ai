@echo off
setlocal

pushd "%~dp0.."
set "ROOT=%CD%"
popd

set "LOG_DIR=%ROOT%\.runtime\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

set "LLM_BASE_URL=https://ark.cn-beijing.volces.com/api/v3"
if "%LLM_API_KEY%"=="" (
  echo Please set LLM_API_KEY in your local shell or .env before starting Doubao mode.
  exit /b 1
)
set "LLM_MODEL=doubao-seed-2-0-lite-260215"
set "LLM_API_STYLE=responses"
set "LLM_TIMEOUT_SECONDS=45"
set "LLM_TEMPERATURE=0.1"
if "%PYTHON_BIN%"=="" set "PYTHON_BIN=python"

start "fm-backend-doubao" /min cmd /c "cd /d %ROOT%\backend && set LLM_BASE_URL=%LLM_BASE_URL% && set LLM_API_KEY=%LLM_API_KEY% && set LLM_MODEL=%LLM_MODEL% && set LLM_API_STYLE=%LLM_API_STYLE% && set LLM_TIMEOUT_SECONDS=%LLM_TIMEOUT_SECONDS% && set LLM_TEMPERATURE=%LLM_TEMPERATURE% && %PYTHON_BIN% -m uvicorn app.main:app --host 127.0.0.1 --port 8000 > %LOG_DIR%\backend.stable.log 2>&1"
start "fm-frontend" /min cmd /c "cd /d %ROOT%\frontend && npm.cmd run dev -- --hostname 127.0.0.1 --port 3000 > %LOG_DIR%\frontend.stable.log 2>&1"

echo Started backend and frontend.
echo Frontend: http://127.0.0.1:3000
echo Backend: http://127.0.0.1:8000
