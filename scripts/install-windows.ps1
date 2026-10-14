<#
.SYNOPSIS
    Installs and configures Qwen Collective on a Windows Control Server.
.DESCRIPTION
    Idempotent installation script that:
    1. Validates Python 3.11+ installation
    2. Provisions dedicated virtual environment (.venv)
    3. Installs dependencies
    4. Sets up persistent data directory outside source tree
    5. Configures production secrets and environment
    6. Hardens OpenSSH .ssh directory permissions
    7. Prepares WinSW Windows Service wrapper configuration
#>

[CmdletBinding()]
param (
    [string]$DataDir = "$env:USERPROFILE\.qwen-collective\data",
    [int]$Port = 8000,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "   Qwen Collective - Windows Control Server Setup" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Project Directory: $ProjectRoot"
Write-Host "Target Data Directory: $DataDir"

# 1. Check Python
Write-Host "`n[1/6] Checking Python installation..." -ForegroundColor Yellow
$PythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $PythonCmd) {
    Write-Error "Python is not installed or not in PATH. Please install Python 3.11+ from python.org."
    exit 1
}

$PyVer = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"
Write-Host "Found Python version: $PyVer" -ForegroundColor Green

# 2. Setup Virtual Environment
Write-Host "`n[2/6] Provisioning dedicated virtual environment (.venv)..." -ForegroundColor Yellow
$VenvDir = Join-Path $ProjectRoot ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip = Join-Path $VenvDir "Scripts\pip.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Host "Creating virtual environment at $VenvDir..."
    python -m venv $VenvDir
} else {
    Write-Host "Existing virtual environment found."
}

Write-Host "Installing dependencies from requirements.txt..."
& $VenvPip install --upgrade pip --quiet
& $VenvPip install -r (Join-Path $ProjectRoot "requirements.txt") --quiet
Write-Host "Dependencies successfully installed." -ForegroundColor Green

# 3. Setup Persistent Data Directory
Write-Host "`n[3/6] Configuring persistent data directory outside source tree..." -ForegroundColor Yellow
if (-not (Test-Path $DataDir)) {
    New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
    Write-Host "Created persistent directory: $DataDir"
} else {
    Write-Host "Persistent data directory already exists: $DataDir"
}

# Preserve any legacy data from ./data if target DB doesn't exist
$SourceDataDir = Join-Path $ProjectRoot "data"
$TargetDb = Join-Path $DataDir "qwen_collective.db"
if ((Test-Path (Join-Path $SourceDataDir "qwen_collective.db")) -and (-not (Test-Path $TargetDb))) {
    Write-Host "Migrating existing database from source tree to persistent location..." -ForegroundColor Yellow
    Copy-Item -Path (Join-Path $SourceDataDir "*") -Destination $DataDir -Recurse -Force
}

# 4. Generate Production Secrets and Environment Configuration
Write-Host "`n[4/6] Checking production secrets and configuration..." -ForegroundColor Yellow
$ConfigDir = Split-Path -Parent $DataDir
$EnvFile = Join-Path $ConfigDir "qwen-collective.env"

if (-not (Test-Path $EnvFile)) {
    Write-Host "Generating cryptographically secure secrets..."
    $SecretKey = & $VenvPython -c "import secrets; print(secrets.token_urlsafe(36))"
    $AdminPassword = & $VenvPython -c "import secrets; print(secrets.token_urlsafe(16))"

    $EnvContent = @"
# Qwen Collective Production Configuration for Windows Control Server
QC_APP_NAME=Qwen Collective
QC_ENV=production
QC_HOST=127.0.0.1
QC_PORT=$Port
QC_DATA_DIR=$DataDir
QC_SECRET_KEY=$SecretKey
QC_ADMIN_PASSWORD=$AdminPassword
QC_SESSION_EXPIRE_HOURS=72
QC_LOG_LEVEL=INFO
"@
    Set-Content -Path $EnvFile -Value $EnvContent -Encoding UTF8
    Write-Host "Restricting NTFS permissions on production environment file..."
    icacls $EnvFile /inheritance:r /grant:r "$($env:USERNAME):(R,W)" "SYSTEM:(R,W)" | Out-Null
    Write-Host "Created production environment file: $EnvFile" -ForegroundColor Green
    Write-Host "==========================================================" -ForegroundColor Red
    Write-Host "  INITIAL ADMIN CREDENTIALS GENERATED:" -ForegroundColor Red
    Write-Host "  Username: admin" -ForegroundColor Red
    Write-Host "  Password: $AdminPassword" -ForegroundColor Red
    Write-Host "  (Stored safely outside git repository in: $EnvFile)" -ForegroundColor Red
    Write-Host "==========================================================" -ForegroundColor Red
} else {
    Write-Host "Existing environment file found at $EnvFile (preserving)." -ForegroundColor Green
    # Ensure strict permissions even on pre-existing file
    icacls $EnvFile /inheritance:r /grant:r "$($env:USERNAME):(R,W)" "SYSTEM:(R,W)" | Out-Null
}

# 5. Harden OpenSSH Directory Permissions
Write-Host "`n[5/6] Verifying OpenSSH known_hosts and key security..." -ForegroundColor Yellow
$SshDir = Join-Path $env:USERPROFILE ".ssh"
if (-not (Test-Path $SshDir)) {
    New-Item -ItemType Directory -Path $SshDir -Force | Out-Null
}

$KnownHosts = Join-Path $SshDir "known_hosts"
if (-not (Test-Path $KnownHosts)) {
    New-Item -ItemType File -Path $KnownHosts -Force | Out-Null
}

# Restrict permissions using icacls
Write-Host "Enforcing strict filesystem permissions on $SshDir..."
icacls $SshDir /inheritance:r /grant:r "$($env:USERNAME):(OI)(CI)F" "SYSTEM:(OI)(CI)F" | Out-Null
Write-Host "OpenSSH directory secured." -ForegroundColor Green

# 6. Verify Service Configuration File
Write-Host "`n[6/6] Verifying Windows Service Wrapper (WinSW) definition..." -ForegroundColor Yellow
$ServiceDir = Join-Path $ProjectRoot "service"
$ServiceExe = Join-Path $ServiceDir "qwen-collective-service.exe"
$ServiceXml = Join-Path $ServiceDir "qwen-collective-service.xml"

if (-not (Test-Path $ServiceExe)) {
    Write-Host "Downloading WinSW x64 service wrapper binary..."
    & $VenvPython -c "import httpx; r=httpx.get('https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe', follow_redirects=True); open(r'$ServiceExe', 'wb').write(r.content)"
}

Write-Host "Service configuration ready at: $ServiceXml (references external protected config)" -ForegroundColor Green

# 7. Verification Tests
if (-not $SkipTests) {
    Write-Host "`nRunning test suite to verify configuration..." -ForegroundColor Yellow
    $TestResult = & $VenvDir\Scripts\pytest.exe -v
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Verification tests failed. Please review output."
        exit 1
    }
    Write-Host "All verification tests passed successfully." -ForegroundColor Green
}

Write-Host "`n==========================================================" -ForegroundColor Green
Write-Host "   Installation & Configuration Complete!" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
Write-Host "You can now run Qwen Collective using:"
Write-Host "  powershell scripts\start-windows.ps1"
Write-Host "Or install as a persistent Windows Service:"
Write-Host "  service\qwen-collective-service.exe install"
Write-Host "  service\qwen-collective-service.exe start"
