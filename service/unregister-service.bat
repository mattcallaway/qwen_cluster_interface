@echo off
:: ============================================================================
:: Qwen Collective - Windows Background Service Uninstaller
:: Automatically requests Administrator privileges to stop and uninstall.
:: ============================================================================

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [INFO] Administrator rights required. Requesting elevation...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process cmd.exe -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

echo ============================================================================
echo Uninstalling Qwen Collective Background Service...
echo ============================================================================

cd /d "%~dp0"

echo Stopping service...
qwen-collective-service.exe stop

echo Uninstalling service...
qwen-collective-service.exe uninstall
if %errorLevel% neq 0 (
    echo [ERROR] Service uninstallation failed or service was not installed.
) else (
    echo [OK] Service uninstalled successfully.
)

echo ============================================================================
pause
