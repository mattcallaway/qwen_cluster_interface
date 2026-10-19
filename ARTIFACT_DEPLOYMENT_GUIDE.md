# Qwen Collective — Unified Artifacts Deployment Guide

## 1. Prerequisites & Target Nodes

The unified deliverables & artifact management system operates across the 3 nodes of the Qwen Collective cluster:
1. **Windows Controller**:
   - Manages canonical SQLite metadata (`qwen_collective.db`), durable artifact storage (`data/artifacts/`), full-text search index, web interface, and REST API.
2. **Linux VPS (`qwen-vps`)**:
   - Sandboxed execution node for dataset generation, code builds, and batch tasks. Uploads outputs over HTTPS with bearer token authentication.
3. **Linux Laptop (`qwen-laptop`)**:
   - Local GPU inference node running Ollama and code analysis. Leases tasks, downloads input artifacts, and uploads derivative reports.

---

## 2. Windows Controller Deployment Steps

### Step 1: Verify Git Branch
Ensure working copy is on the feature branch:
```powershell
git status
# Branch: feature/unified-artifact-deliverables
```

### Step 2: Database Migration Check
Migrations are applied automatically upon application startup via `init_db()`.
To run migrations explicitly or verify version:
```powershell
.venv\Scripts\python -c "from app.database import init_db; init_db()"
```
Verify schema version is 13:
```powershell
.venv\Scripts\python -c "import sqlite3; conn = sqlite3.connect('data/qwen_collective.db'); print('Schema version:', conn.execute('PRAGMA user_version').fetchone()[0])"
```

### Step 3: Verify Artifact Storage Permissions
Ensure the artifact storage directory exists and is writable:
```powershell
New-Item -ItemType Directory -Force -Path "data\artifacts"
```

### Step 4: Run Health & Telemetry Check
Start controller or run the test suite:
```powershell
.venv\Scripts\python -m pytest tests/test_unified_artifact_deliverables.py tests/test_distributed_artifact_lifecycle.py -v
```

---

## 3. Remote Worker Configuration (Linux VPS & Laptop)

Workers require no new database connections or local storage dependencies. They communicate with the controller via standard authenticated REST endpoints.

### Environment Configuration:
Ensure worker environments define:
```bash
export CONTROLLER_URL="https://controller.tailscale.net" # or local IP
export WORKER_TOKEN="<secret_bearer_token>"
```

### Worker Artifact Ingestion Protocol:
When a worker finishes producing a file:
1. Compute local SHA-256: `sha256sum <output_file>`
2. Upload via `POST /api/v1/worker/tasks/<task_id>/artifacts` with multipart form data.
3. Confirm response contains `checksum_matches: true` and a valid `artifact_id`.

---

## 4. Rollback Plan

If unexpected regressions occur:
1. Stop the controller service.
2. Restore database from pre-migration backup:
   ```powershell
   Copy-Item "data\backups\qwen_collective_pre_m13_*.db" "data\qwen_collective.db"
   ```
3. Revert code to previous commit:
   ```powershell
   git checkout main
   ```
4. Restart controller service. Existing files in `data/artifacts/` will remain safe and intact.
