@echo off
title Qwen Collective - Windows Desktop Companion Worker
echo ========================================================
echo Starting Windows Desktop Companion Worker Daemon
echo Worker ID: desktop-worker-01
echo ========================================================
cd /d "%~dp0.."
call .venv\Scripts\activate.bat
python -m vps_agent --config "%USERPROFILE%\.qwen-collective\desktop-worker.env"
if %ERRORLEVEL% neq 0 (
    echo.
    echo Worker exited with error code %ERRORLEVEL%.
    pause
)
