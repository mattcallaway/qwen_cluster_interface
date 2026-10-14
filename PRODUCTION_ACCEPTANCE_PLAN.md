# Controlled Production Acceptance Plan
**Antigravity — Qwen Collective**  
**Target Environments:** Windows Controller (`100.122.228.19`) & Linux VPS Worker (`100.97.174.81`)  
**Status:** PLAN PREPARED — AWAITING ADMINISTRATOR AUTHORIZATION BEFORE MUTATION

---

## 1. Overview & Acceptance Objectives
The objective of this plan is to verify that the security, reliability, and data-integrity repairs (Repairs A, B, and C) function correctly on actual production hardware:
1. Windows Controller running the WinSW service on `127.0.0.1:8000` over Tailscale.
2. Linux VPS (`hvm-chicago` at `100.97.174.81`) running the `vps_agent` companion.

---

## 2. Phase P0 — Read-Only Environment Verification

### P0.1 Windows Controller Discovery
- **WinSW Service Name:** `QwenCollective` (Display Name: `Qwen Collective Control Server`)
- **Service Status:** Running
- **Process ID:** Wrapper PID `31488`, Python Worker PID `22588`
- **Listening Port:** `127.0.0.1:8000`
- **Tailscale Address:** `100.122.228.19` (`desktop-44h6qvp`)
- **Production Workspace Path:** `C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective`
- **Dev/Repair Workspace Path:** `C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective-dev` (Branch: `fix/vps-production-acceptance`)
- **Active Database Path:** `qwen_collective.db`
- **Database Schema Level:** Milestone 5 (16 tables + FTS5 tables + migrations table)

### P0.2 Linux VPS Discovery
- **Node Identifier:** `hvm-chicago`
- **Tailscale Address:** `100.97.174.81`
- **OS:** Linux
- **Tailscale Reachability:** Verified (`pong from hvm-chicago (100.97.174.81) in 57ms`)
- **Worker Architecture:** Outbound pull-based worker leasing model over Tailscale HTTP. The worker initiates all requests to `http://100.122.228.19:8000`.

---

## 3. Phase P1 — Backup & Rollback Preparation

Before mutating any production database, configuration, or service:

### P1.1 Production SQLite Database Backup
Execute a consistent online SQLite backup using `VACUUM INTO`:
```powershell
python -c "import sqlite3; conn = sqlite3.connect('C:/Users/Matt C/.gemini/antigravity-ide/scratch/qwen-collective/qwen_collective.db'); conn.execute('VACUUM INTO ''C:/Users/Matt C/.gemini/antigravity-ide/scratch/qwen_collective_backup_pre_acceptance.db'''); conn.close(); print('Backup complete.')"
```

### P1.2 Application Configuration & State Preservation
1. Preserve production `.env` and `config.json`.
2. Record currently deployed Git commit SHA on production path.
3. Verify backup file exists and is readable by running integrity check:
   ```powershell
   python -c "import sqlite3; c = sqlite3.connect('C:/Users/Matt C/.gemini/antigravity-ide/scratch/qwen_collective_backup_pre_acceptance.db'); print(c.execute('PRAGMA integrity_check').fetchall())"
   ```

### P1.3 Deterministic Rollback Procedure
If any deployment or acceptance failure occurs:
1. Stop Windows service: `Stop-Service -Name QwenCollective`
2. Restore verified database:
   `Copy-Item 'C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen_collective_backup_pre_acceptance.db' 'C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective\qwen_collective.db' -Force`
3. Restore pre-cutover code files.
4. Restart service: `Start-Service -Name QwenCollective`
5. Verify health on `http://127.0.0.1:8000/auth/login`.

---

## 4. Phase P2 — Production Cutover Protocol

> [!CAUTION]
> **STOP CONDITION:** Do not proceed to Phase P2 without explicit administrator authorization. No running service or production database may be modified without prior approval.

### P2.1 Cutover Steps (Executed only upon explicit approval):
1. Sync tested repair code (`app/routes/worker.py`, `app/services/worker.py`, `app/services/context_packet.py`, `vps_agent/`) to the production repository or merge branch.
2. Verify production database schema compatibility.
3. Restart Windows service: `Restart-Service -Name QwenCollective`.
4. Deploy updated `vps_agent` code to Linux VPS worker node.
5. Provision/verify worker enrollment token.
6. Verify node registers and begins heartbeat cycles.

---

## 5. Phase P3 — Controlled Production Acceptance Test

Execute a harmless, non-destructive test using a dedicated test project.

### P3.1 Test Project Specification
- **Project Name:** `VPS Production Acceptance`
- **Objective:** Verify secure distributed Class B execution, checksum validation, and durable result capture on actual hardware.
- **Path:** `Authorized VPS Execution`
- **Stage:** `Execution and Evidence Verification`
- **Deliverable:** `Diagnostic Execution Artifact`
- **Metric:** `Accepted Diagnostic Proofs` (target: 1.0)

### P3.2 Diagnostic Script Content
```python
import hashlib, json, time

timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
content = f"Qwen Collective Production Acceptance Diagnostic - {timestamp}"
sha = hashlib.sha256(content.encode("utf-8")).hexdigest()

print(f"DIAGNOSTIC_EXECUTION_SUCCESS: {sha}")
result = {
    "metric_observation": {
        "value": 1.0,
        "artifact_checksum": sha,
        "evidence_reference": "diagnostic_proof",
        "notes": f"Verified on hardware at {timestamp}"
    }
}
print(json.dumps(result))
```

### P3.3 Verification Checklist
- [ ] Task created with approval gate.
- [ ] Admin approves task (moving it to `queued`).
- [ ] Controller leases task to VPS worker (`100.97.174.81`), signing it with `approved: True` and SHA256 `script_checksum`.
- [ ] VPS agent validates checksum, isolates environment (`PYTHONPATH` scrubbed), and executes script.
- [ ] Worker submits result with deliverable evidence and SHA256 checksum.
- [ ] Controller validates checksum, updates task to `completed`, records immutable ledger entry, and marks metric observation `verified`.
- [ ] Project Context Packet renders `Actual: 1.0 (verified)`.
- [ ] Zero secret leaks in logs.

---

## 6. Phase P4 — Recovery Verification
1. **Idempotency Check:** Re-submit identical task payload; verify controller returns `already_processed` and does not duplicate records.
2. **Buffer Flush:** Verify worker buffer is empty (`count_pending() == 0`).
3. **Node Heartbeat:** Verify node status remains `online` in infrastructure dashboard.

---

## 7. Phase P5 — Reporting
Generate `PRODUCTION_ACCEPTANCE_RESULTS.md` with:
- Exact controller commit and worker version.
- Real hardware timestamps and task IDs.
- Observed status codes and deliverable checksums.
- Explicit confirmation that test was executed on real hardware.
