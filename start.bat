@echo off
chcp 936 >nul
cd /d "%~dp0"
title Nexus

set "PY=.venv\Scripts\python.exe"

if not exist "%PY%" goto :prepare
goto :launch

:prepare
echo [prepare] First run: creating a virtual environment...
where python >nul 2>nul
if errorlevel 1 goto :no_python
python -m venv .venv
if errorlevel 1 goto :failed
"%PY%" -m pip install --quiet --disable-pip-version-check httpx pydantic
if errorlevel 1 goto :failed

:launch
echo [start] Nexus is starting. The browser will open shortly.
echo.
echo   Keep this window open. Close it to stop the agent.
echo.
"%PY%" -m nexus --workspace "%~dp0"
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" exit /b 0
echo.
echo [!] Failed with code %RC%.
pause
exit /b %RC%

:no_python
echo [!] Python 3.12 is required: https://www.python.org/downloads/
pause
exit /b 1

:failed
echo [!] Environment setup failed.
pause
exit /b 1
