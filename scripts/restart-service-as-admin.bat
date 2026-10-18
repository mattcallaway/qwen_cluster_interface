@echo off
:: Check for administrative rights
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Administrative permissions required. Requesting elevation...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

title Restart Qwen Collective Windows Service
echo ========================================================
echo Restarting Qwen Collective Windows Service (QwenCollective)
echo ========================================================
net stop QwenCollective
timeout /t 3 /nobreak >nul
net start QwenCollective
if %ERRORLEVEL% equ 0 (
    echo.
    echo ========================================================
    echo SUCCESS: QwenCollective Windows Service is now RUNNING!
    echo ========================================================
) else (
    echo.
    echo ========================================================
    echo ERROR: Failed to restart service. Error code: %ERRORLEVEL%
    echo ========================================================
)
echo.
pause
