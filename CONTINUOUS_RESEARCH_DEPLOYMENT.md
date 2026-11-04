# Continuous Research Deployment & Migration Guide

## 1. Pre-Deployment Verification

Ensure the following pre-conditions are met on `controller-local`:
1. SQLite version >= 3.25 (with FTS5 module enabled).
2. Python virtual environment `.venv` active with required dependencies (`fastapi`, `uvicorn`, `beautifulsoup4`, `jinja2`).
3. Database backup completed before running schema migrations.

---

## 2. Migration Execution (Migration 12)

Schema Migration 12 introduces:
- `knowledge_records` & `knowledge_records_fts`
- `knowledge_impacts`
- `stage_research_checkpoints`
- `knowledge_promotions`
- `knowledge_freshness_policies`

### Migration Step
Run the database initializer using the virtual environment:
```powershell
.venv\Scripts\python.exe -c "from app.database import init_db; init_db(); print('Migration 12 applied successfully.')"
```

The migration runner will automatically:
1. Verify database integrity (`PRAGMA foreign_key_check`).
2. Snapshot the current database: `qwen_collective.pre-migration-v11-to-v12-<timestamp>.db`.
3. Apply Migration 12 inside an atomic SQLite transaction.
4. Seed default freshness policies.

---

## 3. Post-Deployment Verification

Execute the test suite to confirm operational readiness:
```powershell
.venv\Scripts\python.exe -m pytest tests/test_continuous_research_knowledge.py tests/test_live_multistage_research_integration.py -v
```

All 9 tests should output `PASSED`.

Verify the endpoints via curl or browser:
- Knowledge interface: `GET https://<controller-tailscale-ip>:8000/knowledge`
- Research telemetry: `GET https://<controller-tailscale-ip>:8000/api/v1/observatory/research`

---

## 4. Rollback Procedure

If a critical issue occurs:
1. Stop the controller service.
2. Restore the pre-migration snapshot:
   ```powershell
   Copy-Item "C:\Users\Matt C\.qwen-collective\data\qwen_collective.pre-migration-v11-to-v12-*.db" "C:\Users\Matt C\.qwen-collective\data\qwen_collective.db" -Force
   ```
3. Restart the controller service.
