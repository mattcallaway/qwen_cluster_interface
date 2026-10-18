# Controlled Production Acceptance Plan
**Antigravity — Qwen Collective**  
**Target Environments:** Windows Controller (`100.122.228.19`) & Linux VPS Worker (`100.97.174.81`)  
**Status:** PLAN PREPARED — AWAITING ADMINISTRATOR AUTHORIZATION BEFORE MUTATION  
**Branch:** `fix/vps-production-acceptance`

---

## 1. Overview & Acceptance Objectives

The objective of this plan is to verify that the security, reliability, evidence integrity, and execution isolation repairs function correctly on actual production hardware:
1. Windows Controller running the WinSW service on `127.0.0.1:8000` / `100.122.228.19:8000` over Tailscale.
2. Linux VPS (`hvm-chicago` at `100.97.174.81`) running the `vps_agent` companion under OS-level isolation (`systemd-run` transient unit with `DynamicUser=yes`).
3. Real physical artifact creation on the VPS, transfer to the controller, independent byte verification by the controller, and durable recording into project metrics and knowledge.

---

## 2. Phase P0 — Read-Only Environment Verification

### P0.1 Windows Controller Discovery
- **WinSW Service Name:** `QwenCollective` (Display Name: `Qwen Collective Control Server`)
- **Service Status:** Running
- **Listening Port:** `127.0.0.1:8000` (Local) / `100.122.228.19:8000` (Tailscale)
- **Active Production Path:** `C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective`
- **Development Path:** `C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective-dev` (Branch: `fix/vps-production-acceptance`)
- **Production Database:** `C:\Users\Matt C\.qwen-collective\data\qwen_collective.db`
- **Production Schema Level:** Version 7 (Pending Migration 8: `worker_enrollment_tokens` and `chat_conversations.project_id`)

### P0.2 Linux VPS Discovery
- **Node Identifier:** `hvm-chicago`
- **Tailscale Address:** `100.97.174.81`
- **OS:** Linux (systemd, cgroups v2)
- **Tailscale Reachability:** Verified (`pong from hvm-chicago in ~57ms`)
- **Worker Architecture:** Outbound pull-based leasing model over Tailscale HTTP.

---

## 3. Phase P1 — Backup & Rollback Preparation

Before mutating any production database, configuration, or service:

### P1.1 Production SQLite Database Backup
Execute a consistent online SQLite backup using the SQLite backup API / `VACUUM INTO`:
```powershell
python -c "import sqlite3; conn = sqlite3.connect('C:/Users/Matt C/.qwen-collective/data/qwen_collective.db'); conn.execute('VACUUM INTO ''C:/Users/Matt C/.qwen-collective/data/qwen_collective_pre_acceptance_backup.db'''); conn.close(); print('Backup complete.')"
```

### P1.2 Backup Integrity Validation
Verify the backup database is consistent:
```powershell
python -c "import sqlite3; conn = sqlite3.connect('C:/Users/Matt C/.qwen-collective/data/qwen_collective_pre_acceptance_backup.db'); print('Integrity:', conn.execute('PRAGMA integrity_check').fetchall()); print('Foreign keys:', conn.execute('PRAGMA foreign_key_check').fetchall()); print('Version:', conn.execute('PRAGMA user_version').fetchone()[0])"
```

### P1.3 Deterministic Rollback Procedure
If any deployment or acceptance failure occurs:
1. Stop Windows service: `Stop-Service -Name QwenCollective`
2. Restore verified database:
   `Copy-Item 'C:\Users\Matt C\.qwen-collective\data\qwen_collective_pre_acceptance_backup.db' 'C:\Users\Matt C\.qwen-collective\data\qwen_collective.db' -Force`
3. Restore pre-cutover code files.
4. Restart service: `Start-Service -Name QwenCollective`
5. Verify health endpoint: `http://127.0.0.1:8000/auth/login`.

---

## 4. Phase P2 — Production Cutover Protocol

> [!CAUTION]
> **GATEWAY CHECKPOINT:** Do not execute Phase P2 without explicit administrator authorization. No running service or production database may be modified without prior approval.

### P2.1 Cutover Steps (Upon Explicit Approval):
1. Take verified SQLite backup (P1.1).
2. Sync tested repair code (`app/routes/worker.py`, `app/services/worker.py`, `app/services/metrics.py`, `vps_agent/`) to the production repository or merge branch.
3. Apply Migration 8 to upgrade database schema from Version 7 to Version 8.
4. Restart Windows service: `Restart-Service -Name QwenCollective`.
5. Deploy updated `vps_agent` code to Linux VPS worker node (`hvm-chicago`).
6. Provision/verify worker enrollment token and HMAC secret.
7. Verify node registers and begins heartbeat cycles.

---

## 5. Phase P3 — Real Artifact Production Acceptance Test

Execute an approved diagnostic task that produces a **real physical file artifact** on the VPS disk, transfers it to the controller, and triggers independent byte verification.

### P3.1 Test Project & Deliverable Specification
- **Project:** `VPS Production Acceptance`
- **Path:** `Authorized VPS Execution`
- **Stage:** `Execution and Evidence Verification`
- **Task:** `Produce and transfer a diagnostic artifact`
- **Deliverable:** `Verified Diagnostic Execution Artifact`
- **Metric:** `Validated VPS Diagnostic Executions` (Target: 1)

### P3.2 Real Diagnostic Script Specification
The Python script executed on the VPS does **NOT** rely on in-memory strings. It writes a physical file to its authorized isolated workspace, flushes to disk, and computes the hash directly from disk bytes:

```python
import hashlib
import json
import os
import time

timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
artifact_filename = "vps_diagnostic_artifact.json"

payload_data = {
    "test_name": "VPS Production Acceptance Diagnostic",
    "timestamp": timestamp,
    "system_platform": os.name,
    "nonce": os.urandom(16).hex(),
    "verification_target": "Validated VPS Diagnostic Executions"
}

# 1. Write physical file to isolated workspace
with open(artifact_filename, "w", encoding="utf-8") as f:
    json.dump(payload_data, f, indent=2)
    f.flush()
    os.fsync(f.fileno())

# 2. Read back from disk bytes and compute SHA256 independently
with open(artifact_filename, "rb") as f:
    disk_bytes = f.read()
    file_sha256 = hashlib.sha256(disk_bytes).hexdigest()
    file_size = len(disk_bytes)

print(f"DIAGNOSTIC_EXECUTION_SUCCESS: {file_sha256}")

# 3. Output structured result referencing physical artifact
result = {
    "artifact_filename": artifact_filename,
    "file_size": file_size,
    "metric_observation": {
        "value": 1.0,
        "artifact_checksum": file_sha256,
        "evidence_reference": artifact_filename,
        "notes": f"Real artifact generated on VPS hardware at {timestamp}"
    }
}
print(json.dumps(result))
```

### P3.3 Artifact Transfer & Independent Controller Verification Workflow
1. The VPS agent executor detects the physical file `vps_diagnostic_artifact.json` created in the workspace.
2. The agent reads the bytes, base64 encodes them, and attaches them to `TaskResultSubmission.artifacts`.
3. The controller receives the payload at `POST /api/v1/worker/tasks/{task_id}/result`.
4. The controller stores the artifact at:
   `data/evidence/task_{task_id}/vps_diagnostic_artifact.json`
5. The controller reads the stored physical file in chunks and computes `hashlib.sha256()`.
6. The controller verifies that:
   `controller_computed_sha256 == worker_claimed_sha256`
7. Only after the cryptographic byte match succeeds:
   - The metric observation is recorded with `verification_status = 'verified'`.
   - The deliverable `Verified Diagnostic Execution Artifact` is marked completed and linked to the stored file.
   - The task is marked `completed`.
8. The Project Context Packet displays:
   `Actual: 1.0 (verified)`

---

## 6. Phase P4 — Recovery & Edge Case Verification
1. **Idempotency Check:** Re-submit identical task payload; verify controller returns `already_processed` and does not duplicate records.
2. **Buffer Reconciliation:** Verify worker buffer is empty (`count_pending() == 0`).
3. **Shortcut Attempt Rejection:** Verify that submitting a checksum without uploading the physical artifact produces `unverified` status.
4. **Heartbeat Liveness:** Verify worker node status remains `online` in the infrastructure dashboard.

---

## 7. Phase P5 — Production Acceptance Report
Upon completion of Phase P3, `PRODUCTION_ACCEPTANCE_RESULTS.md` will be updated with actual hardware telemetry:
- Worker identity (`hvm-chicago` / `100.97.174.81`)
- Controller version and commit SHA
- VPS agent version
- Task ID and Execution ID
- Artifact identifier (`vps_diagnostic_artifact.json`)
- Original VPS checksum
- Independently calculated controller checksum
- Verified file size in bytes
- Execution timestamp and Controller receipt timestamp
- Validation result (`PASS`)
- Metric observation ID
- Associated deliverable and project references
