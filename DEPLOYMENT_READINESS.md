# Deployment Readiness Assessment (DEPLOYMENT_READINESS.md)

**Audit Date:** October 14, 2026  
**Audited Commit:** `330125c`  
**Remediation Branch:** `fix/security-reliability-audit`  

---

## 1. Readiness Classification Summary

| Tier | Deployment Scope | Classification | Summary & Assessment |
| :--- | :--- | :--- | :--- |
| **Tier 1** | **Localhost Use** | **READY** | All core local server functions, SQLite storage, OpenSSH defensive probes, and web UI are fully operational and verified. |
| **Tier 2** | **Tailscale-Only Remote Access** | **READY** | Tailscale Serve reverse proxy is configured and operational on private wireguard mesh with ACME TLS certificates and secure cookies. Public Funnel is disabled. |
| **Tier 3** | **VPS Integration** | **CONDITIONAL READY** | Worker security hardened (mandatory 32-char token, constant-time compare, loopback binding, genuine capabilities). Requires remote VPS deployment and token synchronization. |
| **Tier 4** | **Externally Consequential Actions** | **BLOCKED (BY DESIGN)** | All tasks modifying machines or executing external network probes require explicit human approval via the web UI. Automated autonomous execution is prohibited. |

---

## 2. Detailed Classification & Evidence

### Tier 1: Ready for Localhost Use
- **Status:** **READY**
- **Evidence:**
  1. FastAPI application launches cleanly in Python 3.12 dedicated virtual environment.
  2. Persistent data directory `%USERPROFILE%\.qwen-collective\data` maintains database, knowledge files, and backups outside the git tree.
  3. Production secrets (`QC_SECRET_KEY`, `QC_ADMIN_PASSWORD`) stored outside git with restricted NTFS permissions.
  4. Local HTTP health check `http://127.0.0.1:8000/auth/login` returns HTTP 200 OK.
  5. 85 automated unit and integration tests passing (100% pass rate).
- **Remaining Blockers:** None.

---

### Tier 2: Ready for Tailscale-Only Remote Access
- **Status:** **READY**
- **Evidence:**
  1. Tailscale Serve configured in background (`tailscale serve --bg 8000`).
  2. Let's Encrypt TLS certificate active for `desktop-44h6qvp.tailb5d90b.ts.net`; verified via native Windows Schannel with `ssl_verify_result: 0`.
  3. Reverse proxy redirects use relative paths (`/`), preventing leakage of `127.0.0.1`.
  4. Session cookies enforce `HttpOnly`, `SameSite=lax`, and `Secure` attributes over HTTPS.
  5. Tailscale Funnel confirmed disabled (`(tailnet only)`).
  6. Login rate limiting hardened: dual-keyed against both client IP and target account, persisted in SQLite to survive server restarts, strictly bounded to 1,000 records.
- **Recommended User Step:** Complete one end-to-end browser login from an external phone or laptop on the tailnet for manual visual confirmation.

---

### Tier 3: Ready for VPS Integration
- **Status:** **CONDITIONAL READY**
- **Evidence:**
  1. `worker/vps_worker.py` hardened against unauthorized access:
     - Mandatory minimum 32-character authentication token (`QC_AUTH_TOKEN`).
     - Startup failure if token is missing or insecure.
     - Timing-safe authentication via `secrets.compare_digest()`.
     - Default loopback binding (`127.0.0.1`). Public binding requires explicit `QC_ALLOW_PUBLIC_BIND=1`.
     - Capabilities strictly reflect implemented tasks (`health_check`, `test_task`, `echo_ping`, `network_probe`).
     - Arbitrary shell execution strictly prohibited.
  2. Outbound SSRF policy in `app/services/network_security.py` enforces IPv4-mapped IPv6 unmapping, link-local CIDR rejection, and redirect revalidation.
- **Remaining Operational Steps Before Live Use:**
  1. Deploy `worker/vps_worker.py` to target cloud VPS.
  2. Generate a 256-bit token (`python -c "import secrets; print(secrets.token_urlsafe(32))"`).
  3. Set `QC_AUTH_TOKEN` in the VPS systemd service environment.
  4. Register the node in Qwen Collective UI under **Machines -> VPS Companion**.

---

### Tier 4: Ready for Externally Consequential Actions
- **Status:** **BLOCKED (BY DESIGN)**
- **Evidence & Philosophy:**
  1. Qwen Collective enforces strict approval gates (`approval_required = 1` in `tasks` table).
  2. Tasks cannot trigger external network probes, write to persistent knowledge, or modify remote compute nodes without an explicit human administrator clicking "Approve" in the dashboard.
  3. The system rejects autonomous self-executing agent patterns to prevent unintended side effects on external networks or sensitive hardware.
- **Blockers:** None — this barrier is permanent and intentional.
