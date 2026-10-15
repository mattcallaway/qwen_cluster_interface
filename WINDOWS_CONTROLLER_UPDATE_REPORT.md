# Qwen Collective — Windows Production Controller Update Report

**Deployment Date**: 2026-10-14  
**Target Repository**: `C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective`  
**Windows Service**: `QwenCollective` (WinSW managed, Automatic start)  
**Status**: **DEPLOYED & FULLY VERIFIED**

---

## 1. Revision & Code State

| Parameter | Value | Verification Method |
| :--- | :--- | :--- |
| **Previous Revision** | `7a7377d66380992c9355b0f029ea406e9d38bf53` | Verified via Git history |
| **Deployed Revision** | `2e569e1b2a58049e375f5ce2d262aa67a21232d3` | `git rev-parse HEAD` |
| **Branch Origin** | `origin/fix/worker-enrollment-readiness` | Commit SHA matches approved tree |
| **Working Tree** | Clean (`HEAD detached at 2e569e1`, 0 uncommitted files) | `git status` |
| **Python Dependencies** | No broken requirements found | `.venv\Scripts\python.exe -m pip check` |
| **Module Imports** | `app.main`, `app.database`, `app.services.worker`, `app.routes.worker` loaded cleanly | Runtime import verification |

---

## 2. Database Integrity & Backup Verification

The production SQLite database was verified and preserved prior to service changes. Schema version 8 was confirmed with no unnecessary migrations or schema rebuilds.

| Item | Details |
| :--- | :--- |
| **Production Database** | `C:\Users\Matt C\.qwen-collective\data\qwen_collective.db` |
| **Schema Version** | **Version 8** (`PRAGMA user_version = 8`) |
| **Integrity Check** | `PRAGMA integrity_check;` returned `ok` |
| **Pre-Upgrade Backup Path** | `C:\Users\Matt C\.qwen-collective\data\qwen_collective.pre-worker-enrollment-update-20261014T225623Z.db` |
| **Backup File Size** | 643,072 bytes |
| **Backup SHA-256** | `f868d48bddfb4b194f23fa2c2d970fdbeba03cf616ffc01d9cdbf1cd07c4e9f4` |
| **Backup Integrity** | Verified via SQLite online backup API; `PRAGMA integrity_check` returned `ok` |
| **Service Config Backup** | `C:\Users\Matt C\.qwen-collective\data\qwen-collective-service.xml.backup-20261014` |
| **Existing Nodes** | Preserved: `controller-local` (online), `node-d35568f5` (offline) |

---

## 3. Service Lifecycle & Connectivity Verification

1. **Stop**: `QwenCollective` was cleanly stopped; process PID and TCP listener on port 8000 exited completely.
2. **Update**: Git checkout moved from `7a7377d` to `2e569e1b2a58049e375f5ce2d262aa67a21232d3`.
3. **Start**: `QwenCollective` service was started via Administrator elevation and entered `Running` state.
4. **Local HTTP Connectivity**:
   - `http://127.0.0.1:8000/auth/login` $\rightarrow$ `HTTP/1.1 200 OK`
   - `http://127.0.0.1:8000/openapi.json` $\rightarrow$ `HTTP/1.1 200 OK`
5. **Tailscale HTTPS Connectivity**:
   - `https://desktop-44h6qvp.tailb5d90b.ts.net/auth/login` $\rightarrow$ `HTTP/1.1 200 OK`
   - End-to-end encrypted proxying through Tailscale Serve confirmed operational.

---

## 4. Live Security & Zero Database Mutation Validation

Ten live HTTP API security tests were executed against the running production service (`http://127.0.0.1:8000`) to confirm strict security boundaries:

| Test ID | Operation | Input / Auth State | Expected | Actual | Result |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **SEC-01** | `POST /api/v1/worker/register` | Unauthenticated (no token / cookie) | HTTP 401 | HTTP 401 | **PASS** |
| **SEC-02** | `POST /api/v1/worker/register` | Invalid `X-Enrollment-Token` header | HTTP 403 | HTTP 403 | **PASS** |
| **SEC-03** | `POST /api/v1/worker/register` | Bogus `Authorization: Bearer` token | HTTP 401 | HTTP 401 | **PASS** |
| **SEC-04** | `POST /api/v1/worker/heartbeat` | Unauthenticated (no token) | HTTP 401 | HTTP 401 | **PASS** |
| **SEC-05** | `POST /api/v1/worker/heartbeat` | Invalid Bearer token | HTTP 401 | HTTP 401 | **PASS** |
| **SEC-06** | `POST /api/v1/worker/tasks/lease` | Unauthenticated (no token) | HTTP 401 | HTTP 401 | **PASS** |
| **SEC-07** | `POST /api/v1/worker/tasks/lease` | Invalid Bearer token | HTTP 401 | HTTP 401 | **PASS** |
| **SEC-08** | `POST /api/v1/worker/tasks/{id}/heartbeat`| Unauthenticated lease heartbeat | HTTP 401 | HTTP 401 | **PASS** |
| **SEC-09** | `DELETE /api/v1/worker/tokens/{id}` | Unauthenticated token revocation | HTTP 401 | HTTP 401 | **PASS** |
| **SEC-10** | `GET /api/v1/worker/enrollment-tokens` | Unauthenticated token listing | HTTP 401 | HTTP 401 | **PASS** |

### Zero Database Mutation Verification
- **Pre-Test Table Counts**: `nodes: 2`, `worker_auth_tokens: 0`, `worker_enrollment_tokens: 0`, `worker_task_leases: 0`
- **Post-Test Table Counts**: `nodes: 2`, `worker_auth_tokens: 0`, `worker_enrollment_tokens: 0`, `worker_task_leases: 0`
- **Rogue Entity Check**: `0` rogue nodes in `infrastructure_nodes`, `0` rogue tokens in `worker_auth_tokens`.
- **Post-Test Database Integrity**: `PRAGMA integrity_check` $\rightarrow$ `ok`.

---

## 5. Full Regression Test Suite

Execution Command: `.\.venv\Scripts\python.exe -m pytest -v`

```text
============================== test session starts ===============================
platform win32 -- Python 3.12.7, pytest-8.3.4, pluggy-1.5.0
cachedir: .pytest_cache
rootdir: C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective
configfile: pyproject.toml
collected 298 items

tests/test_audit_security.py::... PASSED
tests/test_auth.py::... PASSED
tests/test_database.py::... PASSED
tests/test_hardware_probe.py::... PASSED
tests/test_infrastructure.py::... PASSED
tests/test_infrastructure_routes.py::... PASSED
tests/test_knowledge.py::... PASSED
tests/test_knowledge_routes.py::... PASSED
tests/test_machine_inventory.py::... PASSED
tests/test_machine_routes.py::... PASSED
tests/test_models.py::... PASSED
tests/test_persistence_and_backup_restore.py::... PASSED
tests/test_projects.py::... PASSED
tests/test_scheduler.py::... PASSED
tests/test_security.py::... PASSED
tests/test_ssrf_advanced.py::... PASSED
tests/test_tasks.py::... PASSED
tests/test_ui_states.py::... PASSED
tests/test_vps.py::... PASSED
tests/test_vps_agent.py::... PASSED
tests/test_vps_security.py::... PASSED
tests/test_vps_worker.py::... PASSED
tests/test_web_routes.py::... PASSED
tests/test_worker_api.py::... PASSED
tests/test_worker_reliability.py::... PASSED
tests/test_worker_service.py::... PASSED

====================== 298 passed, 5 warnings in 44.92s =======================
```

- **Total Tests**: 298
- **Passed**: 298 (100%)
- **Failed**: 0
- **Execution Time**: 44.92s

---

## 6. Preserved Rollback Assets & Instructions

In the event a rollback is required, all assets and steps are preserved:

### Rollback Assets
1. **Target Commit**: `7a7377d66380992c9355b0f029ea406e9d38bf53`
2. **Database Snapshot**: `C:\Users\Matt C\.qwen-collective\data\qwen_collective.pre-worker-enrollment-update-20261014T225623Z.db`
3. **Service Config Backup**: `C:\Users\Matt C\.qwen-collective\data\qwen-collective-service.xml.backup-20261014`

### Rollback Procedure
1. Stop the Windows service in an elevated Administrator PowerShell prompt:
   ```powershell
   Stop-Service -Name QwenCollective
   ```
2. Restore the database file:
   ```powershell
   Copy-Item 'C:\Users\Matt C\.qwen-collective\data\qwen_collective.pre-worker-enrollment-update-20261014T225623Z.db' 'C:\Users\Matt C\.qwen-collective\data\qwen_collective.db' -Force
   ```
3. Check out the prior revision in the repository:
   ```powershell
   cd 'C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective'
   git checkout 7a7377d66380992c9355b0f029ea406e9d38bf53
   ```
4. Restart the Windows service:
   ```powershell
   Start-Service -Name QwenCollective
   ```
5. Verify health:
   ```powershell
   curl.exe -s -i http://127.0.0.1:8000/auth/login
   ```

---

## 7. Safety Boundary Compliance

- **No Enrollment Tokens Created**: Verified count = 0 in `worker_enrollment_tokens`.
- **No Autonomous Tasks Initiated**: Verified zero task dispatches.
- **VPS Credentials & Services**: Unchanged and untouched on `qwen-vps`.
- **Public Firewall Settings**: Completely unmodified; traffic remains confined to `127.0.0.1:8000` and Tailscale Serve.
- **Branch Isolation**: Unrelated monitoring work (`fix/probe-policy-blocked-status`) was not merged into production.

---

## 8. Readiness Assessment

The Windows production controller is fully upgraded to revision `2e569e1b2a58049e375f5ce2d262aa67a21232d3`, running under schema version 8, with verified zero-mutation authentication gates, active local HTTP and Tailscale HTTPS listeners, and 298 passing regression tests.

The environment is **READY** for the next worker-enrollment phase.
