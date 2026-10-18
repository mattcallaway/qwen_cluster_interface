# Qwen Collective — Web Research Engine Deployment & Rollback Runbook

## 1. Pre-Deployment Readiness

Before deploying the Autonomous Web Research & Browsing Engine to production:
- [x] Schema Migration 11 tested on clean databases and upgraded schema targets.
- [x] Zero network changes required for remote worker nodes (`qwen-vps`, `qwen-laptop`). Worker sandboxes remain fully air-gapped from the public internet.
- [x] Python dependencies verified: `beautifulsoup4`, `pypdf`, `ddgs`, `playwright`.
- [x] Playwright Chromium binaries installed on controller host.
- [x] Regression test suite passing with 100% test integrity.
- [x] Live internet search and Playwright headless browsing verified against public web (`python.org`).

---

## 2. Production Deployment Steps

### Step 1: Online Database Backup
Before applying Schema Migration 11:
```powershell
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
Copy-Item "qwen_collective.db" "data/backups/qwen_collective_pre_research_$timestamp.db"
```

### Step 2: Merge Feature Branch
```bash
git checkout main
git merge feature/autonomous-web-research --ff-only
```

### Step 3: Install Required Dependencies
On the Windows Controller machine:
```powershell
.\.venv\Scripts\pip.exe install beautifulsoup4 pypdf ddgs playwright
.\.venv\Scripts\playwright.exe install chromium
```

### Step 4: Run Database Migration
Initialize or restart the FastAPI application. Schema Migration 11 will automatically execute:
```powershell
.\.venv\Scripts\python.exe -c "from app.database import init_db; init_db()"
```
Verify the 11 new tables:
- `research_runs`
- `research_queries`
- `research_search_results`
- `research_sources`
- `research_source_versions`
- `research_extractions`
- `research_claims`
- `research_evidence`
- `research_citations`
- `research_provider_status`
- `research_budgets`

### Step 5: Service Restart
Restart the Windows Controller service:
```powershell
Restart-Service -Name "QwenCollectiveController"
```

### Step 6: Post-Deployment Verification
1. Access the web interface at `https://127.0.0.1:8000` or through the Tailscale HTTPS endpoint.
2. Open any project and verify the **🌐 Research Engine** button appears in the project detail view.
3. Check the Research Dashboard at `/projects/{id}/research/` to ensure metrics, activity, and source library load with HTTP 200.

---

## 3. Rollback Procedure

If unexpected operational issues arise:

### Step 1: Stop Controller Service
```powershell
Stop-Service -Name "QwenCollectiveController"
```

### Step 2: Restore Database Backup
```powershell
Copy-Item "data/backups/qwen_collective_pre_research_<timestamp>.db" "qwen_collective.db" -Force
```

### Step 3: Revert Git Commit
```bash
git checkout main
git reset --hard HEAD~1
```

### Step 4: Restart Service
```powershell
Start-Service -Name "QwenCollectiveController"
```
Verify service health:
```powershell
Get-Service -Name "QwenCollectiveController"
curl http://127.0.0.1:8000/api/v1/health
```
