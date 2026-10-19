@echo off
title Spire AI
cd /d "%~dp0"
if not exist settings.bat copy settings.example.bat settings.bat >nul
call settings.bat
set PYTHONUTF8=1
set PY=%~dp0sts-bench\.venv\Scripts\python.exe
set FLAGS=--character %CHARACTER% --model %MODEL% --coach-every %COACH_EVERY%
if /i "%LLM_EVENTS%"=="off" set FLAGS=%FLAGS% --no-llm-events
if /i "%COACH_THINK%"=="off" set FLAGS=%FLAGS% --no-coach-think
set NEED_LLM=yes
if /i "%LLM_EVENTS%"=="off" if "%COACH_EVERY%"=="0" set NEED_LLM=no
if /i "%NEED_LLM%"=="no" goto run
echo Starting Ollama on the GPU with Vulkan...
taskkill /f /im "ollama app.exe" >nul 2>&1
taskkill /f /im ollama.exe >nul 2>&1
set OLLAMA_VULKAN=1
set OLLAMA_CONTEXT_LENGTH=%CONTEXT%
start "Ollama GPU - leave this open" /min ollama serve
timeout /t 5 /nobreak >nul
:run
if /i "%AUTO_LAUNCH_GAME%"=="yes" start "" /min powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch-game.ps1" -DelaySeconds 6
"%PY%" agent\evolve.py %FLAGS% %*
pause
