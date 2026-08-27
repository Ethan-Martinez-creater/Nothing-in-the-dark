@echo off
setlocal
cd /d "%~dp0..\backend"
if not defined COIFESP_PYTHON set "COIFESP_PYTHON=E:\miniconda3\envs\bettafish\python.exe"
if not exist "%COIFESP_PYTHON%" (
  echo Python environment not found: %COIFESP_PYTHON%
  echo Set COIFESP_PYTHON to the intended Python 3.11-3.13 executable.
  exit /b 1
)
"%COIFESP_PYTHON%" -m pip install --upgrade pip
"%COIFESP_PYTHON%" -m pip install -e ".[dev]"
