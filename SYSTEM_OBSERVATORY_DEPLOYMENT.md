# Qwen Collective — System Observatory Deployment Guide
**Production Deployment, Database Migration, Service Coordination & Rollback Plan**

## 1. Pre-Deployment Checklist

Before deploying the System Observatory to production, verify:
- [x] Code is committed on feature branch `feature/system-observatory`.
- [x] Schema Migration 10 is tested and verified on both fresh and v9 databases.
- [x] All automated unit and regression tests pass (`pytest tests/test_observatory.py`).
- [x] Backward compatibility maintained: existing worker heartbeats without extended telemetry continue functioning normally.
- [x] Database backup procedure is documented and validated.

---

## 2. Step-by-Step Deployment Procedure

### Step 1: Database Backup
Before applying migration 10 on the live controller database, perform a verified backup:
```powershell
# From C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective
$stamp = Get-Date -Format "yyyyMMddTHHmmssZ"
Copy-Item "C:\Users\Matt C\.qwen_collective\qwen_collective.db" "C:\Users\Matt C\.qwen_collective\qwen_collective.pre-v10-$stamp.db"
```
The application's `app/database.py` also automatically performs an online SQLite backup snapshot via `backup_before_migration` prior to applying schema migrations.

### Step 2: Merge Feature Branch
```powershell
git checkout main  # or current target integration branch
git merge --no-ff feature/system-observatory -m "feat(observatory): deploy System Observatory (Milestone 10)"
```

### Step 3: Run Database Migrations
Start the application or trigger `init_db()`:
```powershell
.venv\Scripts\python -c "from app.database import init_db; init_db()"
```
Output must show:
```text
Applied schema migration 10: system observatory telemetry, rollups, inference accounting, and alerting
Database initialized successfully.
```

### Step 4: Restart Controller Windows Service
Restart the background WinSW service as Administrator:
```cmd
net stop QwenCollective
net start QwenCollective
```
Or execute:
```cmd
scripts\restart-service-as-admin.bat
```

### Step 5: Verify Controller Health
1. Access the Observatory dashboard:
   `http://127.0.0.1:8000/observatory`
2. Confirm the 6 tabs load properly: Overview, Machines, Models & Tokens, Tasks & Projects, Errors & Alerts, History & Analytics.
3. Confirm `controller-local` telemetry appears in real-time.

### Step 6: Update Worker Daemons (VPS & Laptop)
On `qwen-vps` and `qwen-laptop`:
1. Pull the updated repository branch or update `vps_agent/capabilities.py` and `vps_agent/agent.py`.
2. Restart the worker service:
   ```bash
   sudo systemctl restart qwen-worker
   ```
3. Within 15 seconds, confirm that telemetry samples from `qwen-vps` and `qwen-laptop` begin populating on the Observatory dashboard.

---

## 3. Rollback Plan

If any critical issue arises during deployment:

### Step 1: Stop Windows Service
```cmd
net stop QwenCollective
```

### Step 2: Restore Database Backup
Restore the snapshot taken prior to migration:
```powershell
Copy-Item "C:\Users\Matt C\.qwen_collective\qwen_collective.pre-v10-*.db" "C:\Users\Matt C\.qwen_collective\qwen_collective.db" -Force
```

### Step 3: Roll Back Git Commits
```powershell
git revert -m 1 HEAD  # Or checkout prior integration commit
```

### Step 4: Restart Service
```cmd
net start QwenCollective
```
Verify controller responds normally at `http://127.0.0.1:8000/`.
