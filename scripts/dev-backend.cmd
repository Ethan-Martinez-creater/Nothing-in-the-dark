@echo off
setlocal
cd /d "%~dp0..\backend"
if not defined COIFESP_PYTHON set "COIFESP_PYTHON=E:\miniconda3\envs\bettafish\python.exe"
if not exist "%COIFESP_PYTHON%" (
  echo Python environment not found: %COIFESP_PYTHON%
  echo Run scripts\setup-backend.cmd or set COIFESP_PYTHON.
  exit /b 1
)
rem uvicorn >= 0.51 forces ProactorEventLoop on Windows, which breaks the
rem psycopg-based LangGraph checkpointer; app.main runs the Server on a
rem SelectorEventLoop itself. No --reload support in this mode.
"%COIFESP_PYTHON%" -m app.main
