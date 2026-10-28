@echo off
cd /d "%~dp0"
echo Nexus frontend preview: http://127.0.0.1:8767/
echo Local demo only. Press Ctrl+C to stop.
"..\.venv\Scripts\python.exe" -m http.server 8767 --bind 127.0.0.1 --directory .
