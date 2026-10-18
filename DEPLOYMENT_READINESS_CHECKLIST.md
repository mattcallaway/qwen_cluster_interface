# Qwen Collective — Deployment Readiness Checklist

This document specifies the mandatory deployment gates required prior to live worker enrollment and continuous autonomous task execution.

---

## Deployment Gates Summary

| Gate ID | Verification Item | Target Status | Audit Status | Evidence / Notes |
| :--- | :--- | :--- | :--- | :--- |
| **GATE-01** | Controller Database Schema Version | **Schema 8** | **PASS** | Verified SQLite database schema version is 8 on `desktop-44h6qvp`. |
| **GATE-02** | Controller Service Health | **Running (WinSW)** | **PASS** | `QwenCollective` Windows service running, healthy on `127.0.0.1:8000`. |
| **GATE-03** | Tailnet Reverse Proxy & TLS | **Encrypted HTTPS** | **PASS** | Tailscale Serve active at `https://desktop-44h6qvp.tailb5d90b.ts.net`. |
| **GATE-04** | Controller Network Isolation | **Loopback Bound** | **PASS** | Port 8000 strictly bound to `127.0.0.1`; 0 public ports exposed. |
| **GATE-05** | Linux VPS Tailnet Reachability | **Direct Ping < 20ms** | **PASS** | Tailscale ping to `100.71.194.68` returned pong in 13ms. |
| **GATE-06** | Configuration Parser & Precedence | **Zero-Dependency** | **PASS** | `parse_env_file()` implemented, tested, and validated. |
| **GATE-07** | Configuration Directory Protection | **Safe Fallback** | **PASS** | Rejects data directories in source checkout; defaults to `/opt/qwen-collective/data`. |
| **GATE-08** | Pre-Flight Validation Command | **Exit Code 0** | **PASS** | `--validate` supports pending credentials; rejects invalid settings. |
| **GATE-09** | Systemd Service Template | **Zero Syntax Warnings**| **PASS** | `StartLimitIntervalSec` moved to `[Unit]`. |
| **GATE-10** | Execution Sandbox Launcher | **Scoped Runner Validated**| **PASS** | `/usr/local/bin/qwen-sandbox-runner` verified with `DynamicUser=yes`. |
| **GATE-11** | Restricted Sudoers Elevation | **Narrow Permitted Rule**| **PASS** | `/etc/sudoers.d/qwen-worker` verified with `visudo -c`. |
| **GATE-12** | Automated Regression Suite | **100% Passing** | **PASS** | 314 passed, 0 failures across full repository test suite. |
| **GATE-13** | SSH Administrative Hardening | **Key Auth Transition** | **PENDING USER ACTION** | Action plan established (SEC-01). Awaiting user authorization. |
| **GATE-14** | Live Worker Enrollment Token | **Authorized Issuance** | **HOLD** | Token ready to generate upon explicit user approval. |

---

## Detailed Gate Verification

### GATE-01: Controller Database Schema Version
- **Verification**: SQLite `PRAGMA user_version;` returns `8`.
- **Status**: **PASS**. Database migration from schema 7 to 8 completed and verified.

### GATE-02: Controller Service Health
- **Verification**: `Get-Service -Name QwenCollective` reports status `Running`. Local queries to `http://127.0.0.1:8000` succeed.
- **Status**: **PASS**.

### GATE-03: Tailnet Reverse Proxy & TLS
- **Verification**: `tailscale serve status` displays `https://desktop-44h6qvp.tailb5d90b.ts.net (tailnet only) -> http://127.0.0.1:8000`. HTTPS probe validates TLS certificate.
- **Status**: **PASS**.

### GATE-04: Controller Network Isolation
- **Verification**: `Get-NetTCPConnection` confirms port 8000 is listening on `127.0.0.1` only. Port 443 listens only on Tailscale interfaces.
- **Status**: **PASS**.

### GATE-05: Linux VPS Reachability
- **Verification**: `tailscale ping 100.71.194.68` succeeds over direct WireGuard tunnel.
- **Status**: **PASS**.

### GATE-06 to GATE-08: Worker Daemon Configuration & Validation
- **Verification**:
  - `python -m vps_agent --config /etc/qwen-collective/worker.env --validate` parses without external dependency.
  - Runtime directories decoupled from configuration loading.
  - Source directory write attempts fail closed.
- **Status**: **PASS**.

### GATE-09: Systemd Service Template
- **Verification**: `StartLimitIntervalSec=300s` and `StartLimitBurst=5` correctly placed under `[Unit]`.
- **Status**: **PASS**.

### GATE-10 & GATE-11: Scoped Sandbox & Sudoers Rule
- **Verification**: Real Linux sandbox test passed on Ubuntu 24.04 with UID isolation, filesystem read-only enforcement, network probe blocking, and clean teardown.
- **Status**: **PASS**.

### GATE-12: Full Automated Regression Suite
- **Verification**: 314 automated tests run via `pytest` with 314 passed, 0 failures.
- **Status**: **PASS**.

### GATE-13: SSH Hardening Action Plan
- **Verification**: SEC-01 documented in `SECURITY_FINDINGS.md`. Requires creating an admin user, installing an Ed25519 key, verifying login, and disabling root password login.
- **Status**: **PENDING EXPLICIT USER ACTION** (Safety constraint: Do not modify SSH until authorized).

### GATE-14: Live Enrollment Token Issuance
- **Verification**: Enrollment flow ready to issue a single-use token via `/api/v1/worker/register`.
- **Status**: **HOLD** (Awaiting user authorization to start live enrollment).
