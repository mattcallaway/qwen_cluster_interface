# Operator Deployment Decision & Release Gate

**Document Version:** 1.0.0-PROD-REC  
**Date:** 2026-10-22 (UTC)  
**Branch:** `remediation/production-recovery`  
**Deployment Target:** Qwen Collective Local Controller (WinSW Service `QwenCollective`)  
**Safety Gate:** **STOP AND VERIFY** — Explicit Operator Approval Required Prior to Any Live Service Action  

---

## 1. Executive Deployment Recommendation

### Recommendation: **APPROVED FOR OPERATOR-SUPERVISED DEPLOYMENT**

Following comprehensive functional remediation of all 14 audit findings (F-01 through F-14) arising from the Sonoma County Water Security real-world project failure, the codebase on branch `remediation/production-recovery` is certified ready for deployment.

### Key Certifications:
1. **Security Integrity (F-04):** All artifact downloads, previews, and metadata endpoints are strictly authenticated and project-scoped (verified 401 unauthenticated, 403 cross-project, 200 owner).
2. **Deterministic Research (F-02, F-05, F-09):** Entity-preserving search queries prevent topic-blind results; anti-bot CAPTCHA pages are filtered; evidence resolution generates numeric citations (`[1]`, `[2]`) with zero `[Uncited]` markers.
3. **Multi-Deliverable Planning (F-01, F-03, F-06, F-14):** Complex intake decomposes into all requested deliverables (5/5 for Sonoma); worker model inference tasks participate in the plan; state machine replanning bugs are eliminated.
4. **Zero Live Intrusion:** Throughout all development, testing, and certification stages, **zero modifications were made to the live running controller process (WinSW PID 19300)** and **zero modifications were made to the live production database (`C:\Users\Matt C\.qwen-collective\data\qwen_collective.db`)**.
5. **Full Backup Protection:** A verified, tamper-evident tarball backup exists (`qwen_collective_backup_snp-20261022_014431-ba1c1834.tar.gz`, 9.8 MB) ready for instantaneous rollback.

---

## 2. Certification & Evidence Matrix

| Gate / Subsystem | Verification Scope | Status | Evidence Document / Artifact |
|---|---|---|---|
| **Sonoma County Water Security Acceptance** | 4-Stage End-to-End Acceptance (Security, Queries, Multi-Deliverable Plan, Synthesis & Acceptance) | **PASSED (100%)** | `docs/remediation/evidence/stage09/sonoma_recovery_acceptance_results.json` |
| **Artifact Security Gate (F-04)** | 401 unauth / 403 cross-user / 200 authorized | **PASSED** | Stage 1 Acceptance & `tests/test_stage06_artifact_subsystem.py` |
| **Research Engine (F-02, F-05)** | Entity preservation, bot barrier filtering, query expansion | **PASSED (7/7)** | `tests/test_research_engine.py` |
| **Orchestrator State Machine (F-06, F-14)** | Transitions, task key constraints, replanning without crash | **PASSED (67/67)** | `tests/test_orchestrator_units.py` |
| **Operational Reliability & Safety** | Backup verification, single-instance worker lock, CAS integrity | **PASSED (41/41)** | `tests/test_stage08a_backup_recovery.py` & `test_stage08b_operational_reliability.py` |
| **Regression Suite** | Stages 1 through 7 test suites | **PASSED (145/145)** | Full test harness run |

---

## 3. Production State Confirmation

Prior to executing any deployment procedure, the operational state of the host must be verified:

1. **Live Service:** WinSW service `QwenCollective` is actively running under PID 19300 listening on `127.0.0.1:8000`.
2. **Production Database:** `C:\Users\Matt C\.qwen-collective\data\qwen_collective.db` has remained untouched (MD5/SHA256 preserved from pre-audit baseline).
3. **Backup Status:**
   - Archive: `qwen_collective_backup_snp-20261022_014431-ba1c1834.tar.gz`
   - Size: 9.8 MB (10,314,624 bytes)
   - Manifest: Validated against database, configuration, and CAS files.

---

## 4. Step-by-Step Deployment Runbook

> [!CAUTION]
> **OPERATOR APPROVAL GATE:** Do NOT execute the following steps until the human operator has reviewed this document and given explicit consent to proceed with restarting the live service.

### Step 1: Pre-Deployment Health & Fleet Check
Verify that no orchestration runs are actively in state `PLANNING` or `RUNNING` on the live controller:
```powershell
# Check current active runs (should be 0 active or all COMPLETED/PAUSED)
curl -s http://127.0.0.1:8000/api/v1/health
```

### Step 2: Stop WinSW Service
```powershell
# Stop the live controller service cleanly
Stop-Service -Name "QwenCollective" -Force
Get-Process -Id 19300 -ErrorAction SilentlyContinue
# Verify PID 19300 has terminated
```

### Step 3: Fast Pre-Deploy Backup Snapshot
Create an immediate snapshot before applying codebase changes:
```powershell
$TIMESTAMP = Get-Date -Format "yyyyMMdd_HHmmss"
Copy-Item "C:\Users\Matt C\.qwen-collective\data\qwen_collective.db" "C:\Users\Matt C\.qwen-collective\data\qwen_collective_predeploy_$TIMESTAMP.db"
```

### Step 4: Merge & Sync Codebase to Service Root
Synchronize the remediation branch `remediation/production-recovery` into the service execution directory:
```powershell
git checkout remediation/production-recovery
git status
# Ensure clean working tree
```

### Step 5: Execute Pending Database Migrations (if applicable)
```powershell
.venv\Scripts\python.exe -c "from app.database import init_db; init_db()"
```

### Step 6: Start WinSW Service
```powershell
Start-Service -Name "QwenCollective"
Start-Sleep -Seconds 3
Get-Service -Name "QwenCollective"
```

### Step 7: Post-Deployment Smoke Verification
Verify new `/health` endpoint reports the correct Git SHA and startup timestamp:
```powershell
curl -s http://127.0.0.1:8000/health
# Expected: {"status":"healthy","version":"1.0.0-rc.2","git_revision":"<SHA>","uptime_seconds":...}

# Verify unauthenticated access returns 401
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/api/v1/artifacts
# Expected: 401
```

---

## 5. Rollback Procedures

If any post-deployment smoke check fails or unexpected errors occur, execute the rollback procedure immediately:

### Immediate Rollback (Under 60 Seconds):
1. **Stop Service:**
   ```powershell
   Stop-Service -Name "QwenCollective" -Force
   ```
2. **Restore Pre-Deployment Database:**
   ```powershell
   Copy-Item "C:\Users\Matt C\.qwen-collective\data\qwen_collective_predeploy_*.db" "C:\Users\Matt C\.qwen-collective\data\qwen_collective.db" -Force
   ```
3. **Revert Git Working Tree:**
   ```powershell
   git checkout main
   ```
4. **Restart Service:**
   ```powershell
   Start-Service -Name "QwenCollective"
   ```
5. **Verify Baseline Recovery:**
   Ensure `http://127.0.0.1:8000/` responds.

---

## 6. Formal Sign-Off Gate

```
================================================================================
                    OPERATOR DEPLOYMENT APPROVAL GATE
================================================================================
Target:                Production Controller (PID 19300 -> New Service PID)
Code Branch:           remediation/production-recovery
Acceptance Status:     100% Passed (Sonoma 5/5 Deliverables Accepted)
Rollback Prepared:     YES (qwen_collective_backup_snp-20261022_014431-ba1c1834.tar.gz)
Live Safety Kept:      YES (0 live process or DB modifications made)

Decision:
[ ] APPROVED: Proceed with Step-by-Step Deployment Runbook
[ ] HOLD: Keep running on current process until scheduled maintenance window
================================================================================
```
