# Qwen Collective: Operations, Maintenance & Incident Runbook

> **Target Audience**: Human DevOps engineers, cluster operators, and automated maintenance subagents.  
> **Purpose**: This runbook provides exact, step-by-step procedures for operating, provisioning, backing up, troubleshooting, and recovering services across the Qwen Collective cluster.

---

## 1. Service Management & Process Control

### 1.1 Windows Central Controller (Host)
The controller runs under **WinSW (Windows Service Wrapper)** ensuring automatic boot launch, process supervision, and rolling logs.

| Action | Command (PowerShell as Administrator) |
|---|---|
| **Check Service Status** | `Get-Service -Name QwenCollective` |
| **Start Controller Service** | `Start-Service -Name QwenCollective` |
| **Stop Controller Service** | `Stop-Service -Name QwenCollective` |
| **Restart Controller Service**| `Restart-Service -Name QwenCollective` |
| **Direct Interactive Run** | `cd "C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective"; .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload` |
| **Inspect Service Logs** | `Get-Content service\qwen_service.err.log -Tail 100 -Wait` |

---

### 1.2 Remote Operational VPS Worker (`qwen-vps`)
Managed via systemd under the dedicated, unprivileged user `qwen-worker`.

| Action | Command (SSH to VPS) |
|---|---|
| **Check Service Status** | `sudo systemctl status qwen-vps.service` |
| **Restart Worker Agent** | `sudo systemctl restart qwen-vps.service` |
| **Inspect Live Logs** | `sudo journalctl -u qwen-vps.service -f` |
| **Inspect Local Buffer** | `sqlite3 /home/qwen-worker/vps_agent.db "SELECT * FROM buffered_results;"` |

---

### 1.3 Local Inference Cluster (`qwen-laptop` / `desktop-worker-01`)
Inference nodes run `llama-server` or `Ollama` exposing local loopback RPC endpoints multiplexed over Tailscale.

| Action | Command (Linux / Windows Worker) |
|---|---|
| **Ollama Service Status** | `systemctl status ollama` (Linux) or check Task Manager (Windows) |
| **List Installed Models** | `ollama list` |
| **Verify Port Binding** | `curl http://127.0.0.1:11434/api/tags` |
| **llama-server Launch** | `llama-server -m models/qwen2.5-7b-instruct-q4_k_m.gguf -c 4096 --port 8080 --host 0.0.0.0` |

---

## 2. Tailscale WireGuard Mesh & Network Diagnostics

All inter-node traffic travels across an authenticated, encrypted Tailscale overlay network.

### 2.1 Cluster Connectivity Verification
From the Windows Controller, test connectivity to all member nodes:

```powershell
# 1. Verify Tailscale Status & IP Allocations
tailscale status

# 2. Ping VPS Operational Companion
tailscale ping 100.64.0.5

# 3. Ping Local Linux Inference Laptop
tailscale ping 100.64.0.6

# 4. Test Controller Inbound Access via Tailscale Serve
curl -k https://100.64.0.1:8000/api/v1/health
```

### 2.2 Key Rotation & Re-Authentication
Node authentication tokens expire after 90 days. To re-authenticate an expired node:
1. Generate an auth key in the Tailscale Admin Console.
2. Run on the affected node:
   ```bash
   sudo tailscale up --authkey=tskey-auth-xxxxxxxxxxxx --reset
   ```

---

## 3. Database Maintenance & Backup Procedures

The authoritative database resides at `qwen_collective.db` using **SQLite 3 in WAL (Write-Ahead Logging) mode**.

### 3.1 Live Checkpointing & Truncation
When the WAL file (`qwen_collective.db-wal`) exceeds 100MB, execute a safe truncate checkpoint:

```powershell
.venv\Scripts\python.exe -c "
import sqlite3
conn = sqlite3.connect('qwen_collective.db', timeout=60.0)
conn.execute('PRAGMA wal_checkpoint(TRUNCATE);')
conn.close()
print('WAL checkpoint complete.')
"
```

### 3.2 Online Live Database Backup
Create an ACID-compliant live snapshot without pausing or taking down the controller:

```powershell
.venv\Scripts\python.exe -c "
import sqlite3, datetime
src = sqlite3.connect('qwen_collective.db')
ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
dst = sqlite3.connect(f'data/backups/qwen_collective_{ts}.db')
with dst:
    src.backup(dst, pages=100)
dst.close()
src.close()
print(f'Backup saved: data/backups/qwen_collective_{ts}.db')
"
```

### 3.3 Rebuilding FTS5 Search Indexes
If search results appear stale or corrupted:

```powershell
.venv\Scripts\python.exe -c "
import sqlite3
conn = sqlite3.connect('qwen_collective.db')
c = conn.cursor()
c.execute(\"INSERT INTO artifact_search_fts(artifact_search_fts) VALUES('rebuild');\")
c.execute(\"INSERT INTO knowledge_fts(knowledge_fts) VALUES('rebuild');\")
conn.commit()
conn.close()
print('FTS5 indexes rebuilt successfully.')
"
```

---

## 4. Content-Addressable Storage (CAS) Integrity Audits

Artifact binaries are stored under `data/artifacts/{sha256[:2]}/{sha256}`.

### 4.1 Running the CAS Integrity Verification Script
Verifies that every disk blob matches its recorded SHA-256 hash in `artifact_versions`:

```powershell
.venv\Scripts\python.exe -c "
import sqlite3, hashlib, os
conn = sqlite3.connect('qwen_collective.db')
c = conn.cursor()
c.execute('SELECT id, sha256, storage_path FROM artifact_versions')
errors = 0
for vid, expected_hash, path in c.fetchall():
    if not os.path.exists(path):
        print(f'[MISSING FILE] Version {vid}: {path}')
        errors += 1
        continue
    with open(path, 'rb') as f:
        actual_hash = hashlib.sha256(f.read()).hexdigest()
    if actual_hash != expected_hash:
        print(f'[CORRUPT HASH] Version {vid}: expected {expected_hash}, got {actual_hash}')
        errors += 1
if errors == 0:
    print('All CAS artifacts verified 100% intact.')
else:
    print(f'Integrity audit finished with {errors} errors.')
"
```

---

## 5. Incident Triage Trees & Troubleshooting

### Scenario A: Project Plan Rejected with `overview.objective is required`
* **Root Cause**: The model generated a valid plan, but hit the token ceiling, truncating before the closing `}`. The fallback JSON extractor incorrectly grabbed a nested sub-object (`overview`).
* **Resolution**:
  1. Verify `PLANNER_MAX_TOKENS = 2048` in `app/services/orchestration/planner.py`.
  2. Verify `try_repair_truncated_json` is active.
  3. Re-run plan generation; the bounded repair FSM will automatically salvage the output.

---

### Scenario B: Task Lease Timeout (`lease_expired_reclaimed`)
* **Root Cause**: The remote VPS worker failed to send heartbeats every 30 seconds (network drop, CPU lock, or script hung).
* **Resolution**:
  1. SSH to VPS and check status: `sudo systemctl status qwen-vps.service`.
  2. If the task hung on an external HTTP request, check VPS egress: `curl -I https://duckduckgo.com`.
  3. Restart worker: `sudo systemctl restart qwen-vps.service`.
  4. In the Web UI, click **Retry** on the reclaimed task.

---

### Scenario C: Web Research Task Fails with `SSRFBlockedError`
* **Root Cause**: An external search result redirected to an internal loopback (`127.0.0.1`), private RFC1918 IP, or metadata IP (`169.254.169.254`).
* **Resolution**:
  - This is expected zero-trust defensive behavior. The fetcher automatically drops private IP destinations to protect internal cluster services. No remediation needed.
