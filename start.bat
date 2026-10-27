@echo off
setlocal EnableExtensions
chcp 936 >nul
cd /d "%~dp0"

if not defined NEXUS_HOME set "NEXUS_HOME=%~dp0.nexus-data"
title Nexus

set "PY=.venv\Scripts\python.exe"
set "BASE_PY="

if exist "%PY%" (
  "%PY%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>nul
  if not errorlevel 1 (
    "%PY%" -m nexus.bootstrap --root .
    if errorlevel 1 goto :failed
    goto :launch
  )
)

call :find_python
if not defined BASE_PY goto :no_python

"%BASE_PY%" -m nexus.bootstrap --root . --base "%BASE_PY%"
if errorlevel 1 goto :failed

:launch
echo [start] Nexus is starting. The browser will open shortly.
echo.
echo   Keep this window open. Close it to stop the agent.
echo.
"%PY%" -m nexus --workspace .
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" exit /b 0
echo.
echo [!] Failed with code %RC%.
pause
exit /b %RC%

:find_python
call :probe_py_launcher
if defined BASE_PY exit /b 0
for /f "delims=" %%P in ('where python3.12 2^>nul') do (
  call :probe_python "%%P"
  if defined BASE_PY exit /b 0
)
call :probe_python "%USERPROFILE%\.local\bin\python3.12.exe"
if defined BASE_PY exit /b 0
call :probe_python "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if defined BASE_PY exit /b 0
call :probe_python "C:\Python312\python.exe"
if defined BASE_PY exit /b 0
call :probe_python "%PROGRAMFILES%\Python312\python.exe"
if defined BASE_PY exit /b 0
for /f "delims=" %%P in ('where python 2^>nul') do (
  call :probe_python "%%P"
  if defined BASE_PY exit /b 0
)
exit /b 0

:probe_py_launcher
set "BASE_PY="
for /f "delims=" %%P in ('py -3.12 -c "import sys; print(sys.executable)" 2^>nul') do (
  call :probe_python "%%P"
  if defined BASE_PY exit /b 0
)
exit /b 0

:probe_python
set "BASE_PY="
"%~1" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>nul
if errorlevel 1 exit /b 1
set "BASE_PY=%~1"
exit /b 0

:no_python
echo [!] Python 3.12 was not found. Install it, then run start.bat again:
echo     https://www.python.org/downloads/
pause
exit /b 1

:failed
echo [!] Environment setup failed. The previous .venv was not deleted.
pause
exit /b 1
