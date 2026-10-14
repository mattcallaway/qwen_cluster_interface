<#
.SYNOPSIS
    Performs an atomic backup of the Qwen Collective database and Markdown vault on Windows.
.DESCRIPTION
    Creates a consistent SQLite snapshot using the SQLite online backup API,
    bundles the persistent Markdown knowledge vault, and produces a timestamped ZIP archive.
#>

[CmdletBinding()]
param (
    [string]$DataDir = "$env:USERPROFILE\.qwen-collective\data",
    [string]$BackupDir = "$env:USERPROFILE\.qwen-collective\backups",
    [int]$RetentionDays = 14
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "   Qwen Collective - Windows Atomic Backup Job" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Verify Data Directory
$DbPath = Join-Path $DataDir "qwen_collective.db"
$KnowledgePath = Join-Path $DataDir "knowledge"

if (-not (Test-Path $DbPath)) {
    Write-Error "Database not found at: $DbPath"
    exit 1
}

# 2. Ensure Backup Directory
if (-not (Test-Path $BackupDir)) {
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
}

$Timestamp = (Get-Date).ToString("yyyy-MM-dd_HHmmss")
$TempStage = Join-Path $BackupDir "_stage_$Timestamp"
New-Item -ItemType Directory -Path $TempStage -Force | Out-Null

try {
    # 3. Create Atomic Online SQLite Snapshot
    Write-Host "[1/3] Creating atomic online SQLite snapshot..." -ForegroundColor Yellow
    $TempDb = Join-Path $TempStage "qwen_collective.db"

    $BackupScript = @"
import sqlite3
src = sqlite3.connect(r'$DbPath')
dst = sqlite3.connect(r'$TempDb')
src.backup(dst)
src.close()
dst.close()
print('Snapshot complete.')
"@

    & $VenvPython -c $BackupScript
    if (-not (Test-Path $TempDb)) {
        throw "Failed to create database snapshot."
    }

    # 4. Copy Knowledge Markdown Vault
    Write-Host "[2/3] Copying persistent Markdown knowledge vault..." -ForegroundColor Yellow
    if (Test-Path $KnowledgePath) {
        Copy-Item -Path $KnowledgePath -Destination (Join-Path $TempStage "knowledge") -Recurse -Force
    }

    # 5. Compress into ZIP Archive
    Write-Host "[3/3] Compressing backup into archive..." -ForegroundColor Yellow
    $ArchiveFile = Join-Path $BackupDir "qwen_collective_backup_$Timestamp.zip"
    Compress-Archive -Path (Join-Path $TempStage "*") -DestinationPath $ArchiveFile -Force

    $ArchiveSize = (Get-Item $ArchiveFile).Length / 1MB
    Write-Host "Backup created successfully: $ArchiveFile ($([math]::Round($ArchiveSize, 2)) MB)" -ForegroundColor Green

    # 6. Apply Retention Policy
    $CutoffDate = (Get-Date).AddDays(-$RetentionDays)
    Get-ChildItem -Path $BackupDir -Filter "qwen_collective_backup_*.zip" | Where-Object { $_.CreationTime -lt $CutoffDate } | ForEach-Object {
        Write-Host "Pruning old backup: $($_.Name)" -ForegroundColor DarkGray
        Remove-Item $_.FullName -Force
    }

} finally {
    if ($TempStage -and [System.IO.Directory]::Exists($TempStage)) {
        [System.IO.Directory]::Delete($TempStage, $true)
    }
}

Write-Host "==========================================================" -ForegroundColor Green
Write-Host "   Backup Completed Cleanly" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
