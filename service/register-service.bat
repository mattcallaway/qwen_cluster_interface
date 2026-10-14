@echo off
:: ============================================================================
:: Qwen Collective - Windows Background Service Installer
:: Automatically requests Administrator privileges to register the service.
:: ============================================================================

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [INFO] Administrator rights required. Requesting elevation...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process cmd.exe -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

echo ============================================================================
echo Installing Qwen Collective Background Service...
echo ============================================================================

cd /d "%~dp0"

if not exist "logs" mkdir "logs"

if not exist "qwen-collective-service.exe" (
    echo [INFO] WinSW binary not found. Downloading WinSW v2.12.0...
    powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; (New-Object Net.WebClient).DownloadFile('https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe', 'qwen-collective-service.exe')"
)

echo Installing service using WinSW...
qwen-collective-service.exe install
if %errorLevel% neq 0 (
    echo [ERROR] Installation failed or service is already installed.
) else (
    echo [OK] Service installed successfully.
)

echo Starting service...
qwen-collective-service.exe start
if %errorLevel% neq 0 (
    echo [ERROR] Failed to start service. Check service\logs\ for details.
) else (
    echo [OK] Service started successfully!
)

echo.
echo Service status:
qwen-collective-service.exe status
echo ============================================================================
pause
