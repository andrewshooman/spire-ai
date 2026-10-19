@echo off
set PYTHONUTF8=1
cd /d "%~dp0"
"%~dp0sts-bench\.venv\Scripts\python.exe" agent\evolve_tools.py watch %*
pause
