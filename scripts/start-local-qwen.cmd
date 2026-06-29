@echo off
setlocal

pushd "%~dp0.."
set "ROOT=%CD%"
popd

if exist "%ROOT%\.env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ROOT%\.env") do (
    set "%%A=%%B"
  )
)

set "LOG_DIR=%ROOT%\.runtime\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

if "%LLM_BASE_URL%"=="" set "LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1"
if "%LLM_MODEL%"=="" set "LLM_MODEL=qwen-plus"
set "LLM_API_STYLE=chat"
if "%LLM_TIMEOUT_SECONDS%"=="" set "LLM_TIMEOUT_SECONDS=90"
if "%LLM_TEMPERATURE%"=="" set "LLM_TEMPERATURE=0.1"
if "%PYTHON_BIN%"=="" set "PYTHON_BIN=python"

if "%LLM_API_KEY%"=="" (
  echo Please set LLM_API_KEY in %ROOT%\.env before starting Qwen mode.
  echo Example:
  echo LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
  echo LLM_API_KEY=your-qwen-api-key
  echo LLM_MODEL=qwen-plus
  echo LLM_API_STYLE=chat
  exit /b 1
)

start "fm-backend-qwen" /min cmd /c "cd /d %ROOT%\backend && set LLM_BASE_URL=%LLM_BASE_URL% && set LLM_API_KEY=%LLM_API_KEY% && set LLM_MODEL=%LLM_MODEL% && set LLM_API_STYLE=%LLM_API_STYLE% && set LLM_TIMEOUT_SECONDS=%LLM_TIMEOUT_SECONDS% && set LLM_TEMPERATURE=%LLM_TEMPERATURE% && %PYTHON_BIN% -m uvicorn app.main:app --host 127.0.0.1 --port 8000 > %LOG_DIR%\backend.qwen.log 2>&1"
start "fm-frontend" /min cmd /c "cd /d %ROOT%\frontend && npm.cmd run dev -- --hostname 127.0.0.1 --port 3000 > %LOG_DIR%\frontend.qwen.log 2>&1"

echo Started backend and frontend in Qwen mode.
echo Frontend: http://127.0.0.1:3000
echo Backend: http://127.0.0.1:8000

