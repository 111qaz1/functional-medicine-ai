@echo off
for /f "tokens=2 delims==" %%P in ('wmic process where "CommandLine like '%%uvicorn app.main:app%%' or CommandLine like '%%next dev%%' or CommandLine like '%%npm.cmd run dev%%'" get ProcessId /value ^| findstr "="') do (
  taskkill /PID %%P /F >nul 2>nul
)

echo Requested stop for local backend/frontend processes.
