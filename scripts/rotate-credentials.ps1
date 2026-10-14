<#
.SYNOPSIS
    Rotate production credentials for Qwen Collective Windows Control Server.
.DESCRIPTION
    Generates new cryptographically secure secrets (QC_SECRET_KEY and QC_ADMIN_PASSWORD),
    updates the external protected environment file outside Git, applies restrictive NTFS ACLs,
    synchronizes the SQLite database password hash, and restarts the service.
#>

[CmdletBinding()]
param (
    [string]$EnvFile = "$env:USERPROFILE\.qwen-collective\qwen-collective.env",
    [switch]$NoRestart
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Error "Virtual environment not found at $VenvPython. Run scripts\install-windows.ps1 first."
    exit 1
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " Qwen Collective - Cryptographic Credential Rotation" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Generate new cryptographically secure credentials
Write-Host "`n[1/4] Generating new cryptographic secrets..." -ForegroundColor Yellow
$NewSecretKey = & $VenvPython -c "import secrets; print(secrets.token_urlsafe(36))"
$NewAdminPassword = & $VenvPython -c "import secrets; print(secrets.token_urlsafe(18))"

# 2. Update external environment file
Write-Host "[2/4] Updating protected environment file ($EnvFile)..." -ForegroundColor Yellow
if (-not (Test-Path $EnvFile)) {
    Write-Error "Configuration file not found at $EnvFile."
    exit 1
}

$Lines = Get-Content $EnvFile
$NewLines = @()
$FoundSecret = $false
$FoundPassword = $false

foreach ($line in $Lines) {
    if ($line -match '^\s*QC_SECRET_KEY\s*=') {
        $NewLines += "QC_SECRET_KEY=$NewSecretKey"
        $FoundSecret = $true
    } elseif ($line -match '^\s*QC_ADMIN_PASSWORD\s*=') {
        $NewLines += "QC_ADMIN_PASSWORD=$NewAdminPassword"
        $FoundPassword = $true
    } else {
        $NewLines += $line
    }
}

if (-not $FoundSecret) { $NewLines += "QC_SECRET_KEY=$NewSecretKey" }
if (-not $FoundPassword) { $NewLines += "QC_ADMIN_PASSWORD=$NewAdminPassword" }

Set-Content -Path $EnvFile -Value $NewLines -Encoding UTF8

# 3. Restrict NTFS permissions
Write-Host "[3/4] Enforcing restrictive NTFS permissions on $EnvFile..." -ForegroundColor Yellow
icacls $EnvFile /inheritance:r /grant:r "$($env:USERNAME):(R,W)" "SYSTEM:(R,W)" | Out-Null

# 4. Synchronize database hash
Write-Host "[4/4] Updating database admin credentials and invalidating active sessions..." -ForegroundColor Yellow
$SyncScript = @"
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(r'$EnvFile'))
from app.config import settings
from app.auth import ensure_default_admin
ensure_default_admin()
print('Database password hash synchronized successfully.')
"@

& $VenvPython -c "$SyncScript"

Write-Host "`n==========================================================" -ForegroundColor Green
Write-Host " CREDENTIAL ROTATION COMPLETED SUCCESSFULLY" -ForegroundColor Green
Write-Host " All prior sessions have been invalidated." -ForegroundColor Green
Write-Host " New Admin Username: admin" -ForegroundColor Green
Write-Host " New Admin Password: $NewAdminPassword" -ForegroundColor Green
Write-Host " Protected File:     $EnvFile" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green

if (-not $NoRestart) {
    Write-Host "`nRestarting server to apply rotated credentials..." -ForegroundColor Cyan
    # Check if WinSW service is running
    $ServiceExe = Join-Path $ProjectRoot "service\qwen-collective-service.exe"
    if (Test-Path $ServiceExe) {
        $status = & $ServiceExe status 2>&1
        if ($status -match "Started") {
            & $ServiceExe restart
            Write-Host "WinSW Windows Service restarted." -ForegroundColor Green
        }
    }
}
