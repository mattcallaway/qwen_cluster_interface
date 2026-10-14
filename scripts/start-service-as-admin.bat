@echo off
title Start Qwen Collective Windows Service
echo ========================================================
echo Starting Qwen Collective Windows Service (QwenCollective)
echo ========================================================
net start QwenCollective
if %ERRORLEVEL% equ 0 (
    echo.
    echo ========================================================
    echo SUCCESS: QwenCollective Windows Service is now RUNNING!
    echo ========================================================
) else (
    echo.
    echo ========================================================
    echo ERROR: Failed to start service. Error code: %ERRORLEVEL%
    echo Please make sure you right-clicked and selected:
    echo "Run as administrator"
    echo ========================================================
)
echo.
pause
