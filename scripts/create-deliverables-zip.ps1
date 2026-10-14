$ErrorActionPreference = "Stop"

$projectRoot = "C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective"
$zipPath = Join-Path $projectRoot "qwen_collective_audit_deliverables.zip"

if (Test-Path $zipPath) {
    Remove-Item $zipPath -Force
}

$stagingDir = Join-Path $projectRoot "audit_bundle_staging"
if (Test-Path $stagingDir) {
    Remove-Item -Recurse -Force $stagingDir
}

New-Item -ItemType Directory -Path $stagingDir | Out-Null
New-Item -ItemType Directory -Path (Join-Path $stagingDir "reports") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $stagingDir "logs") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $stagingDir "tests") | Out-Null

# Copy Reports
$reports = @(
    "SECURITY_AUDIT.md",
    "CLAIM_RECONCILIATION.md",
    "REMEDIATION_CHANGELOG.md",
    "DEPLOYMENT_READINESS.md",
    "AUDIT_BASELINE.md",
    "STATUS.md",
    "TAILSCALE_DIAGNOSTIC_REPORT.md",
    "DEPLOYMENT_REPORT.md",
    "WINDOWS_DEPLOYMENT.md",
    "requirements-lock.txt"
)

foreach ($r in $reports) {
    $src = Join-Path $projectRoot $r
    if (Test-Path $src) {
        Copy-Item -Path $src -Destination (Join-Path $stagingDir "reports\$r")
    }
}

$testLog = Join-Path $projectRoot "pytest_test_run.log"
if (Test-Path $testLog) {
    Copy-Item -Path $testLog -Destination (Join-Path $stagingDir "logs\pytest_test_run.log")
}

$auditLog = Join-Path $projectRoot "pip_audit_scan.log"
if (Test-Path $auditLog) {
    Copy-Item -Path $auditLog -Destination (Join-Path $stagingDir "logs\pip_audit_scan.log")
}

$serviceLogDir = Join-Path $projectRoot "service\logs"
if (Test-Path $serviceLogDir) {
    Get-ChildItem -Path $serviceLogDir | ForEach-Object {
        Copy-Item -Path $_.FullName -Destination (Join-Path $stagingDir "logs\$($_.Name)")
    }
}

# Copy Tests
$testDir = Join-Path $projectRoot "tests"
if (Test-Path $testDir) {
    Get-ChildItem -Path $testDir -Filter "*.py" | ForEach-Object {
        Copy-Item -Path $_.FullName -Destination (Join-Path $stagingDir "tests\$($_.Name)")
    }
}

# Create Zip
Compress-Archive -Path "$stagingDir\*" -DestinationPath $zipPath -Force

# Clean staging
Remove-Item -Recurse -Force $stagingDir

# Print details
Get-Item $zipPath | Select-Object Name, Length, LastWriteTime | Format-List
