@echo off
setlocal
cd /d "%~dp0.."
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m http.server 8777 --bind 127.0.0.1 --directory nexus-preview
) else (
  python -m http.server 8777 --bind 127.0.0.1 --directory nexus-preview
)
