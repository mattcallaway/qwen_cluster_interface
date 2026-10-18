# Qwen Collective — VPS Worker Autonomous Authentication & Enrollment Report

**Target Node:** `qwen-vps` (`100.71.194.68`)  
**Controller:** `https://desktop-44h6qvp.tailb5d90b.ts.net` / `http://127.0.0.1:8000`  
**Date:** October 15, 2026  
**Final Status:** **FULLY OPERATIONAL & VERIFIED ONLINE** (Systemd Active, Authenticated Heartbeats Flowing, Dashboard ONLINE)

---

## Executive Summary

The autonomous repair and enrollment mission for the Qwen Collective VPS execution worker (`qwen-vps`) has concluded successfully. All objectives have been achieved without compromising sandbox defenses, exposing sensitive credentials, or bypassing security controls.

```mermaid
sequenceDiagram
    autonumber
    actor Admin as Authorized Admin
    participant Ctrl as Windows Controller (127.0.0.1:8000)
    participant VPS as Linux Worker Daemon (qwen-vps: 100.71.194.68)
    participant DB as SQLite DB (Schema v8)

    Note over Admin,Ctrl: Phase 1-3: Root Cause & Admin Auth
    Admin->>Ctrl: POST /api/v1/worker/enrollment-tokens (Same-Origin Session)
    Ctrl->>DB: INSERT worker_enrollment_tokens (enr_f609ffa4f829)
    Ctrl-->>Admin: Single-Use Enrollment Token (TTL 1800s, Scoped: qwen-vps)

    Note over Admin,VPS: Phase 4-5: Secure Key Exchange & Single-Use Enrollment
    Admin->>VPS: Deliver token to /etc/qwen-collective/worker.env (0600 qwen-worker)
    VPS->>Ctrl: POST /api/v1/worker/register (Bearer: enr_f609ffa4f829)
    Ctrl->>DB: Mark enr_f609ffa4f829 used; generate tok_855aa0b64e24
    Ctrl-->>VPS: Persistent Bearer Token
    VPS->>VPS: Save /opt/qwen-collective/data/worker_token.json (0600)
    VPS->>VPS: Scrub QC_ENROLLMENT_TOKEN from /etc/qwen-collective/worker.env

    Note over VPS,Ctrl: Phase 6-7: Production Service & Continuous Heartbeat
    VPS->>VPS: systemctl restart qwen-worker.service (Active)
    loop Every 15s (Heartbeat) & 5s (Lease Poll)
        VPS->>Ctrl: POST /api/v1/worker/heartbeat (Bearer: tok_855aa0b64e24)
        Ctrl->>DB: Update last_contact_at on infrastructure_nodes ('online')
        Ctrl-->>VPS: 200 OK
    end
```

---

## 1. Root Cause Diagnosis: HTTP 401 Unauthorized

### The Failure Symptom
Attempts to generate a worker enrollment token via the web dashboard or browser automated scripts resulted in `HTTP 401 Unauthorized`, despite the user being logged in as an administrator.

### Investigation & Root Cause
1. **Origin & Cookie Scope Isolation:**  
   The browser frontend scripts dispatched fetch requests with the default browser fetch option `credentials: "same-origin"`.
2. **Cross-Origin Host Mismatch:**  
   When administrative dashboard pages or automated browser actions were accessed via alternate origins (such as `http://127.0.0.1:8010/` or cross-domain URLs), the browser strictly adhered to standard cross-origin security rules: it withheld the `qc_session` cookie from requests targeted at the primary controller API on `http://127.0.0.1:8000/`.
3. **Session Verification Failure:**  
   The FastAPI dependency `get_current_user()` requires a valid `qc_session` cookie. Because the cookie was withheld by the browser engine during cross-origin calls, the controller received an unauthenticated request and correctly failed closed with `401 Unauthorized`.
4. **Resolution:**  
   Executing the token creation flow within the authenticated, same-origin context of `http://127.0.0.1:8000` allowed the browser to include the valid `qc_session` cookie. The endpoint immediately returned `200 OK` and generated the scoped enrollment token.

---

## 2. Secure Transport & Key Exchange

To ensure secure, non-interactive configuration and avoid password leakage over shell environments:
- **SSH Key Pair Validation:** The operator's dedicated Ed25519 public key (`C:\Users\Matt C\.ssh\id_ed25519.pub`) was verified.
- **VPS Host Authorization:** The public key was securely installed into `/root/.ssh/authorized_keys` on `qwen-vps` (`100.71.194.68`) over the encrypted Tailscale network.
- **Verification:** Verified passwordless SSH access with strict host key checking. All subsequent deployment operations were executed through this encrypted SSH channel.

---

## 3. Single-Use Enrollment Token Lifecycle

1. **Token Generation:**
   - **Token ID:** `enr_f609ffa4f829`
   - **Allowed Node:** `qwen-vps`
   - **TTL:** 1800 seconds
   - **Policy:** Single-use, fail-closed
2. **Secure Delivery:**
   - The token was injected directly into `/etc/qwen-collective/worker.env` via encrypted SFTP/SSH.
   - File permissions: `0600` (`-rw-------`).
   - Ownership: `qwen-worker:qwen-worker`.
3. **Controlled Single Registration Cycle:**
   - Executed a single registration pass as unprivileged user `qwen-worker`:
     ```bash
     sudo -u qwen-worker /opt/qwen-collective/.venv/bin/python -m vps_agent --once
     ```
   - **Output:** Exit code 0, node `qwen-vps` registered.
4. **Token Cleanup & Scrubbing:**
   - The single-use enrollment token was automatically scrubbed from `/etc/qwen-collective/worker.env`.
   - The controller database updated `worker_enrollment_tokens`:
     - `used_at`: `2026-10-15T08:32:48.317957+00:00`
     - `used_by_node_id`: `qwen-vps`
   - The enrollment token cannot be replayed or reused.

---

## 4. Persistent Worker Authentication & Systemd Daemon

1. **Persistent Bearer Token:**
   - Location: `/opt/qwen-collective/data/worker_token.json`
   - Permissions: `0600` (`-rw-------`)
   - Ownership: `qwen-worker:qwen-worker` (UID 999)
   - Database Record: `worker_auth_tokens` ID `tok_855aa0b64e24` (node `qwen-vps`, `revoked: 0`)
2. **Systemd Service Unit:**
   - Unit File: `/etc/systemd/system/qwen-worker.service`
   - User: `qwen-worker` (UID 999)
   - Working Directory: `/opt/qwen-collective/source`
   - Environment File: `/etc/qwen-collective/worker.env`
   - Rate Limiting: `StartLimitIntervalSec=300s`, `StartLimitBurst=5` (under `[Unit]`)
3. **Service Status Verification:**
   ```
   ● qwen-worker.service - Qwen Collective VPS Execution Worker Daemon
        Loaded: loaded (/etc/systemd/system/qwen-worker.service; enabled; preset: enabled)
        Active: active (running) since Thu 2026-10-15 08:32:50 UTC
      Main PID: 20113 (python)
         Tasks: 1 (limit: 1055)
        Memory: 30.0M (peak: 30.1M)
        CGroup: /system.slice/qwen-worker.service
                └─20113 /opt/qwen-collective/.venv/bin/python -m vps_agent
   ```

---

## 5. Live Verification & Telemetry

### Controller SQLite Database State
Query of `C:\Users\Matt C\.qwen-collective\data\qwen_collective.db`:

| Table | Record ID / Node | Key Attributes | Live Status |
| :--- | :--- | :--- | :--- |
| `infrastructure_nodes` | `qwen-vps` | Role: `vps_worker`<br>`is_enabled: 1` | **`health_status: 'online'`**<br>`last_contact_at: Live (<15s)` |
| `worker_auth_tokens` | `tok_855aa0b64e24` | Scoped Node: `qwen-vps`<br>`revoked: 0` | **`last_used_at: Live (<15s)`** |
| `worker_enrollment_tokens` | `enr_f609ffa4f829` | Allowed Node: `qwen-vps` | **`used_at: 2026-10-15T08:32:48Z`**<br>`used_by_node_id: 'qwen-vps'` |

### Continuous Inbound Heartbeats
The controller access logs confirm steady, authenticated HTTPS traffic over Tailscale from `100.71.194.68`:
- `POST /api/v1/worker/heartbeat` $\rightarrow$ `HTTP 200 OK` (every 15s)
- `POST /api/v1/worker/tasks/lease` $\rightarrow$ `HTTP 200 OK` (every 5s)

### Web Dashboard Confirmation
Visual verification performed via browser automation at `http://127.0.0.1:8000/infrastructure?tab=workers`:
- Node Name: **Qwen VPS** (`qwen-vps`)
- Role: **Vps Worker**
- Badge: **ONLINE** (green)
- Host OS: Linux 6.8.0-147-generic (x86_64)
- Verified Capabilities: `python-3.12`, `sh`, `bash`, `curl`, `git`, `systemctl`, `cron`

---

## 6. Security Guarantees Maintained

1. **Zero Credential Exposure:**
   - At no point were raw enrollment tokens, persistent bearer tokens, session secrets, or system passwords logged, committed to git, or exposed in shell arguments.
2. **Hardened Execution Sandbox Untouched:**
   - The scoped root launcher at `/usr/local/bin/qwen-sandbox-runner` and `/etc/sudoers.d/qwen-worker` retain strict isolation:
     - `DynamicUser=yes` (ephemeral unprivileged UID per task execution)
     - `IPAddressDeny=any` (complete network isolation inside sandbox)
     - `ProtectSystem=strict` (read-only system tree)
3. **No Unapproved Workloads:**
   - No execution tasks or test commands were dispatched to the worker queue during enrollment. The worker daemon is currently polling and ready for controlled task scheduling.
