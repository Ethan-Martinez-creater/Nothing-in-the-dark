@echo off
setlocal
cd /d "%~dp0..\frontend"
if not exist "node_modules" (
  echo Frontend dependencies not found. Run scripts\setup-frontend.cmd first.
  exit /b 1
)
npm run dev
