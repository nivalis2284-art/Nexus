@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
where python >nul 2>&1
if errorlevel 1 (
  py -3 "静态预览.py"
) else (
  python "静态预览.py"
)
if errorlevel 1 pause
endlocal
