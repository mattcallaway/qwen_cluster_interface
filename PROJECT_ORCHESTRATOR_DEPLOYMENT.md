# Project Orchestrator Deployment and Rollback Guide

## 1. Pre-Deployment Checklist

Before deploying the Project Orchestrator to production, verify:
- [x] All 410 regression, unit, and API tests pass in development.
- [x] Feature branch `feature/autonomous-project-orchestrator` is pushed to GitHub.
- [x] Production database is currently at schema version 8 (`PRAGMA user_version = 8`).
- [x] All live worker nodes (VPS `100.71.194.68` and Laptop `100.101.187.115`) are online and reachable via Tailscale.
- [x] Operator authorization has been granted for service restart and database migration.

---

## 2. Production Deployment Procedure

### Step 1: Create an Atomic Pre-Migration Database Backup
Before applying migration v9, create an atomic online backup of the production SQLite database:

```powershell
# Open elevated PowerShell or standard terminal as Matt C
$dbPath = "C:\Users\Matt C\.qwen-collective\data\qwen_collective.db"
$backupPath = "C:\Users\Matt C\.qwen-collective\data\qwen_collective_v8_pre_v9_backup.db"

# Execute SQLite online backup to avoid write lock contention
sqlite3 $dbPath ".backup '$backupPath'"

# Verify backup integrity
sqlite3 $backupPath "PRAGMA integrity_check;"
sqlite3 $backupPath "PRAGMA user_version;" # Must output 8
```

### Step 2: Deploy Updated Code to the Production Controller Repository
The production repository is located at `C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective`.

```powershell
cd "C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective"

# Fetch latest branch
git fetch origin feature/autonomous-project-orchestrator

# Fast-forward production checkout or switch branch
git checkout feature/autonomous-project-orchestrator
git status
```

### Step 3: Run Database Migration v9
Execute `init_db()` using the production virtual environment:

```powershell
& "C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective\.venv\Scripts\python.exe" -c "
from app.database import init_db, get_db_connection
init_db()
conn = get_db_connection()
v = conn.execute('PRAGMA user_version').fetchone()[0]
print(f'Database successfully migrated to version: {v}')
assert v == 9
"
```

### Step 4: Enable Orchestrator Background Engine in Production Environment
Edit `C:\Users\Matt C\.qwen-collective\qwen-collective.env` to add:

```ini
QC_ORCHESTRATOR_ENABLED=1
```

*(Note: If `QC_ORCHESTRATOR_ENABLED` is missing or `0`, the API and UI routes function, but the background scheduler loop remains inactive.)*

### Step 5: Update Worker Agent Executor on Remote Nodes
The updated `vps_agent/executor.py` raises `MAX_NUM_PREDICT` to 1024 and adds `temperature` support.

#### On Linux Laptop (`100.101.187.115`, user `emceemcee`):
```bash
# Push updated executor via SSH or SCP
scp vps_agent/executor.py emceemcee@100.101.187.115:/home/emceemcee/qwen_cluster_interface/vps_agent/executor.py
ssh emceemcee@100.101.187.115 "sudo systemctl restart qwen-vps-agent.service"
```

#### On Linux VPS (`100.71.194.68`, user `root`):
```bash
scp vps_agent/executor.py root@100.71.194.68:/opt/qwen-collective/vps_agent/executor.py
ssh root@100.71.194.68 "systemctl restart qwen-vps-agent.service"
```

### Step 6: Restart Windows Production Service
In an elevated PowerShell prompt:

```powershell
Restart-Service QwenCollective
Get-Service QwenCollective
```

### Step 7: Post-Deployment Smoke Verification
1. Access the web dashboard at `http://127.0.0.1:8000/`.
2. Verify the **"Orchestrate with AI"** button appears on `/projects`.
3. Open `/projects/orchestrate/new` and verify the page loads with CSRF token and model options.
4. Verify in the application logs that `engine.background_loop` initialized cleanly.

---

## 3. Rollback Procedure

If any issue arises during or after deployment, follow these exact rollback steps:

### Step 1: Stop the Production Service
In an elevated PowerShell prompt:
```powershell
Stop-Service QwenCollective
```

### Step 2: Restore the Schema v8 Database
```powershell
$dbPath = "C:\Users\Matt C\.qwen-collective\data\qwen_collective.db"
$backupPath = "C:\Users\Matt C\.qwen-collective\data\qwen_collective_v8_pre_v9_backup.db"

# Restore database from backup
Copy-Item -Path $backupPath -Destination $dbPath -Force

# Verify restored version
sqlite3 $dbPath "PRAGMA user_version;" # Must output 8
```

### Step 3: Revert Code to Previous Commit
```powershell
cd "C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective"
git checkout b57c5c7
```

### Step 4: Disable Orchestrator Engine in Environment
In `C:\Users\Matt C\.qwen-collective\qwen-collective.env`, set:
```ini
QC_ORCHESTRATOR_ENABLED=0
```

### Step 5: Restart Service and Verify
```powershell
Start-Service QwenCollective
Get-Service QwenCollective
```
Verify that existing projects and task polling continue without disruption.
