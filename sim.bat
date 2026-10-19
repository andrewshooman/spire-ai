@echo off
rem Offline combat simulator: bench, train, compare, calibrate, apply (see agent\sim_train.py)
set PYTHONUTF8=1
cd /d "%~dp0"
if "%~1"=="" (set ARGS=bench) else (set ARGS=%*)
"%~dp0sts-bench\.venv\Scripts\python.exe" agent\sim_train.py %ARGS%
pause
