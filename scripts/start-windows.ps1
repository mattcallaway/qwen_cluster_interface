<#
.SYNOPSIS
    Starts the Qwen Collective Control Server locally in the console.
.DESCRIPTION
    Loads the persistent production or development environment variables and
    launches the Uvicorn application server bound to 127.0.0.1.
#>

[CmdletBinding()]
param (
    [string]$EnvPath = "$env:USERPROFILE\.qwen-collective\qwen-collective.env"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

# Check environment file
if (Test-Path $EnvPath) {
    Write-Host "Loading configuration from: $EnvPath" -ForegroundColor Cyan
    Get-Content $EnvPath | ForEach-Object {
        $line = $_.Trim()
        if ($line -and (-not $line.StartsWith("#")) -and ($line -match '^\s*([^=]+)\s*=\s*(.*)$')) {
            $name = $matches[1].Trim()
            $val = $matches[2].Trim().Trim('"').Trim("'")
            [System.Environment]::SetEnvironmentVariable($name, $val, [System.EnvironmentVariableTarget]::Process)
        }
    }
} elseif (Test-Path (Join-Path $ProjectRoot ".env")) {
    $LocalEnv = Join-Path $ProjectRoot ".env"
    Write-Host "Loading configuration from local .env: $LocalEnv" -ForegroundColor Cyan
    Get-Content $LocalEnv | ForEach-Object {
        $line = $_.Trim()
        if ($line -and (-not $line.StartsWith("#")) -and ($line -match '^\s*([^=]+)\s*=\s*(.*)$')) {
            $name = $matches[1].Trim()
            $val = $matches[2].Trim().Trim('"').Trim("'")
            [System.Environment]::SetEnvironmentVariable($name, $val, [System.EnvironmentVariableTarget]::Process)
        }
    }
} else {
    Write-Host "No custom .env file found. Using default environment configuration." -ForegroundColor Yellow
}

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Error "Virtual environment not found at $VenvPython. Run scripts\install-windows.ps1 first."
    exit 1
}

$HostAddr = if ($env:QC_HOST) { $env:QC_HOST } else { "127.0.0.1" }
$PortNum = if ($env:QC_PORT) { $env:QC_PORT } else { 8000 }

Write-Host "==========================================================" -ForegroundColor Green
Write-Host " Starting Qwen Collective Control Server on http://${HostAddr}:${PortNum}" -ForegroundColor Green
Write-Host " Data Directory: $env:QC_DATA_DIR" -ForegroundColor Green
Write-Host " Press Ctrl+C to gracefully stop." -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green

Set-Location $ProjectRoot
& $VenvPython -m uvicorn app.main:app --host $HostAddr --port $PortNum --log-level info
