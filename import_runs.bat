@echo off
rem Import runs you played: from your own profile (automatic) and from a folder of .run files,
rem e.g. the Steam Deck's SlayTheSpire\runs folder copied to this PC:   import_runs.bat D:\deck-runs
set PYTHONUTF8=1
if exist "%~dp0settings.bat" call "%~dp0settings.bat"
cd /d "%~dp0"
"%~dp0sts-bench\.venv\Scripts\python.exe" agent\human.py %*
pause
