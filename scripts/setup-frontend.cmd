@echo off
setlocal
cd /d "%~dp0..\frontend"
set "npm_config_cache=%~dp0..\.npm-cache"
npm install
