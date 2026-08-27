@echo off
setlocal
cd /d "%~dp0..\vendor\MediaCrawler"
git apply --check "..\mediacrawler-local.patch"
if errorlevel 1 (
  echo MediaCrawler patch does not apply cleanly. Verify the pinned commit first.
  exit /b 1
)
git apply "..\mediacrawler-local.patch"
echo MediaCrawler compatibility patches applied.
