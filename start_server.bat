@echo off
title PKP Seat Optimizer Server
cd /d "%~dp0"
echo ========================================================
echo   PKP Seat Optimizer - System Optymalizacji Miejsc
echo   Uruchamianie serwera na http://127.0.0.1:8000 ...
echo ========================================================
set PYTHONPATH=.
.\venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
pause
