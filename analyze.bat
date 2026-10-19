@echo off
rem Summary of the live runs: throughput, depth, killers, risk levels, shops, LLM use. Options: --since N, --last N
set PYTHONUTF8=1
cd /d "%~dp0"
"%~dp0sts-bench\.venv\Scripts\python.exe" agentnalyze.py %*
pause
