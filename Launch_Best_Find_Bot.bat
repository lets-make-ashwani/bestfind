@echo off
title Best Find Automation - 7-Day Autonomous Cadence Engine
cd /d "%~dp0"
echo ======================================================================
echo Launching Best Find Automation (FastAPI + 7-Day Cadence Engine)
echo Access Local Dashboard: http://localhost:10000
echo ======================================================================
python -m uvicorn app:app --host 0.0.0.0 --port 10000 --reload
pause
