@echo off
title Spire AI
cd /d "%~dp0"
if not exist settings.bat copy settings.example.bat settings.bat >nul
call settings.bat
rem defaults for settings added after your settings.bat was made
if "%GAME_SPEED%"=="" set GAME_SPEED=5
if "%LLM_CARDS%"=="" set LLM_CARDS=on
if "%LLM_SHOP%"=="" set LLM_SHOP=on
if "%BOT_SLOT%"=="" set BOT_SLOT=0
set PYTHONUTF8=1
set PY=%~dp0sts-bench\.venv\Scripts\python.exe
set FLAGS=--character %CHARACTER% --model %MODEL% --coach-every %COACH_EVERY%
if not "%EVENT_MODEL%"=="" set FLAGS=%FLAGS% --event-model %EVENT_MODEL%
if /i "%LLM_CARDS%"=="off" set FLAGS=%FLAGS% --no-llm-cards
if /i "%LLM_SHOP%"=="off" set FLAGS=%FLAGS% --no-llm-shop
if /i "%LLM_EVENTS%"=="off" set FLAGS=%FLAGS% --no-llm-events
if /i "%COACH_THINK%"=="off" set FLAGS=%FLAGS% --no-coach-think
if /i "%AUTO_LAUNCH_GAME%"=="yes" set FLAGS=%FLAGS% --relaunch-game
set NEED_LLM=yes
if /i "%LLM_EVENTS%"=="off" if "%COACH_EVERY%"=="0" set NEED_LLM=no
if /i "%NEED_LLM%"=="no" goto run
echo Starting Ollama on the GPU with Vulkan...
taskkill /f /im "ollama app.exe" >nul 2>&1
taskkill /f /im ollama.exe >nul 2>&1
set OLLAMA_VULKAN=1
set OLLAMA_CONTEXT_LENGTH=%CONTEXT%
rem keep the model loaded between runs; flash attention + 8-bit KV cache: less VRAM, same speed
set OLLAMA_KEEP_ALIVE=-1
set OLLAMA_FLASH_ATTENTION=1
set OLLAMA_KV_CACHE_TYPE=q8_0
rem one model at a time: no stray model steals VRAM (NUM_PARALLEL=1 caused hangs; keep Ollama's default)
set OLLAMA_MAX_LOADED_MODELS=1
start "Ollama GPU - leave this open" /min ollama serve
timeout /t 5 /nobreak >nul
:run
if /i not "%OPEN_LIVE_VIEW%"=="no" start "" /min cmd /c "timeout /t 8 /nobreak >nul & start http://127.0.0.1:8765/"
if /i "%AUTO_LAUNCH_GAME%"=="yes" start "" /min powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch-game.ps1" -DelaySeconds 6
rem exit code 75 = "Restart the bot" from the web page: the bot closed the game after a run; relaunch it and
rem start the bot again on the current code
:bot
"%PY%" agent\evolve.py %FLAGS% %*
if %errorlevel%==75 (
  echo Restarting the bot with the current code...
  start "" /min powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch-game.ps1" -DelaySeconds 6
  goto bot
)
pause
