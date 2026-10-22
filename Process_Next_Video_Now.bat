@echo off
title Best Find - Process Next Video Single Run
cd /d "%~dp0"
echo ======================================================================
echo Processing Next Video to Facebook Page 'best find'...
echo ======================================================================
python -c "import pipeline; pipeline.execute_pipeline_cycle()"
pause
