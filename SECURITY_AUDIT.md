# Qwen Collective — Comprehensive Security Audit Report (SECURITY_AUDIT.md)

**Audit Date:** October 14, 2026  
**Audited Commit:** `330125c`  
**Remediation Branch:** `fix/security-reliability-audit`  
**Scope:** Full codebase audit covering `app/`, `worker/`, `service/`, `scripts/`, network policies, authentication, session integrity, and external dependencies.

---

## 1. Executive Summary

A comprehensive source-level security audit was conducted against the active repository state. Rather than treating past documentation as verified truth, every finding was tested against actual executable source code.

Five confirmed security and reliability defects were identified, analyzed, and repaired with regression tests added to prevent recurrence. Total automated test coverage expanded from **62 to 85 passing tests** (100% pass rate).

---

## 2. Confirmed Vulnerabilities and Detailed Remediations

### Vulnerability 1: VPS Worker Insecure Default Token & 0.0.0.0 Network Binding
- **Severity:** High / Critical
- **Source Files & Functions:** `worker/vps_worker.py` (`AUTH_TOKEN`, `HOST`, `verify_token`, `execute_task`)
- **Root Cause:**
  1. `worker/vps_worker.py` fell back to a hardcoded string `default-vps-secret-token` when `QC_AUTH_TOKEN` was unset.
  2. The service defaulted to listening on `0.0.0.0:8081`, exposing it to all network interfaces.
  3. Token verification used standard `!=` string equality instead of constant-time comparison, opening a timing side-channel.
  4. Advertised capabilities included phantom features (`page_retrieval_sandbox`, `structured_research_inbox`) not implemented in task handlers.
- **Remediation Performed:**
  - Removed all fallback default tokens. Implemented `validate_auth_token()` requiring a minimum 32-character cryptographically strong token at startup; raises `RuntimeError` on weak/default values.
  - Changed default binding host to loopback `127.0.0.1`. Binding to `0.0.0.0` or `::` now requires explicit opt-in (`QC_ALLOW_PUBLIC_BIND=1`).
  - Switched token validation to `secrets.compare_digest()` for constant-time comparison.
  - Aligned advertised capabilities strictly to implemented handlers: `["health_check", "test_task", "echo_ping", "network_probe"]`.
- **Verification Tests:**
  `tests/test_vps_security.py` (6 tests: token missing, token weak, token valid, host binding enforcement, timing-safe authentication, and unauthorized shell task rejection).

---

### Vulnerability 2: Outbound SSRF, IPv4-Mapped IPv6 Bypasses & Permissive IP Policy
- **Severity:** High
- **Source Files & Functions:** `app/services/network_security.py` (`validate_destination_ip`, `validate_endpoint_url`, `validate_probe_target`), `app/services/inference.py`
- **Root Cause:**
  1. Outbound IP validation permitted any global public IP address without port restrictions (`is_global == True`).
  2. IPv4-mapped IPv6 literals (e.g. `::ffff:169.254.169.254`) could bypass IPv4 string-matching checks for cloud metadata services (`169.254.169.254`) and loopback.
  3. URL parsing accepted user-info credentials (`http://user:pass@host`), a common SSRF vector.
  4. Outbound HTTP requests followed redirects without re-validating intermediary hops, allowing open-redirect SSRF bypasses.
  5. `generate_distributed_launch_command` in `inference.py` output `--host 0.0.0.0` for `llama-server`.
- **Remediation Performed:**
  - Implemented `normalize_ip()` to automatically unmap `IPv6Address.ipv4_mapped` to true `IPv4Address` objects before any validation.
  - Added full CIDR checking for `169.254.0.0/16` and `fe80::/10` link-local ranges, plus cloud metadata hostnames.
  - Strictly banned URL user-info syntax and forbidden characters.
  - Restricted external/global destinations to explicit allowed service ports (`80, 443, 8000, 8080, 8081, 11434`).
  - Implemented `safe_execute_http()` enforcing `follow_redirects=False` with mandatory destination re-validation on every redirect hop.
  - Changed default coordinator host in `generate_distributed_launch_command` from `0.0.0.0` to loopback `127.0.0.1`.
- **Verification Tests:**
  `tests/test_ssrf_advanced.py` (8 tests: IPv4-mapped IPv6 metadata, IPv4-mapped IPv6 loopback, IPv6 link-local, user-info rejection, mixed DNS answers, Tailscale/private routing, and redirect-to-metadata blocking).

---

### Vulnerability 3: In-Memory Rate Limiting & Account-Based Brute-Force Vulnerability
- **Severity:** Medium / High
- **Source Files & Functions:** `app/routes/auth.py` (`check_login_rate_limit`, `record_failed_login`, `clear_failed_logins`)
- **Root Cause:**
  1. Failed logins were stored exclusively in an in-memory dictionary keyed by `client_ip`. An attacker rotating proxy headers or IPs could execute unbounded password guessing against target accounts (e.g. `admin`).
  2. Restarting the application cleared all in-memory rate-limiting counters.
  3. Stored IP dictionary was unbounded in memory, presenting a memory exhaustion risk under spoofed IP flooding.
- **Remediation Performed:**
  - Implemented a bounded SQLite-backed `login_attempts` table. Rate limits persist across server restarts.
  - Dual-keyed rate limiting: limits are tracked and enforced against BOTH client IP address AND account identity (`username`). Rotating client IPs no longer bypasses account protection.
  - Enforced automatic pruning: records older than `LOCKOUT_WINDOW_SECONDS` (300s) are pruned on every check, and total records are capped at 1,000 entries.
  - Strict proxy header trust: `X-Forwarded-For` is only honored from `TRUSTED_PROXIES` (`127.0.0.1`, `::1`).
- **Verification Tests:**
  `tests/test_auth_ratelimit.py` (5 tests: IP rate limiting, account rate limiting across rotating IPs, clear on login, bounded table size, and spoofed proxy header rejection).

---

### Vulnerability 4: Chat Context Freezing & Global Session Collisions
- **Severity:** Medium (Functional / Context Security)
- **Source Files & Functions:** `app/routes/chat.py` (`SESSION_ID`, `chat_view`, `chat_send`, `chat_clear`), `app/templates/chat.html`
- **Root Cause:**
  1. `app/routes/chat.py` hardcoded `SESSION_ID = "default-session"`, forcing all users and sessions to share a single global conversation history.
  2. The SQL query fetched context using `ORDER BY created_at ASC LIMIT 20`. Once a conversation reached 20 messages, the first 20 messages were perpetually sent to the model, and all newer messages were permanently excluded from model context.
- **Remediation Performed:**
  - Introduced `chat_conversations` table in SQLite linking conversations to `user_id`. Each conversation receives a persistent unique ID (`conv-<uuid>`).
  - Added ownership validation: users cannot view or send prompts to conversations owned by another user (returns HTTP 403).
  - Implemented `build_context_messages()` using a sliding context budget (default 10,000 characters). Queries newest turns first (`ORDER BY id DESC LIMIT 50`), selects messages fitting within the budget, and reverses them into strictly ascending chronological order for the model.
  - Preserved backward compatibility: legacy `default-session` messages are automatically registered to the active admin user upon first visit.
  - Updated `app/templates/chat.html` to support conversation switching, creation of new conversations, and persistent `session_id` tracking.
- **Verification Tests:**
  `tests/test_chat_isolation.py` (4 tests: conversation isolation and cross-user rejection, legacy session preservation, context budgeting with newest messages, and authenticated UI rendering).

---

### Vulnerability 5: Uncontrolled Uvicorn Development Reloading
- **Severity:** Low / Operational
- **Source Files & Functions:** `app/main.py`
- **Root Cause:**
  `app/main.py` evaluated `reload = (settings.environment == "development")` without an explicit flag, risking unintended file watcher threads in test or non-production deployments.
- **Remediation Performed:**
  Enforced `should_reload = False` by default; requires explicit `QC_DEV_RELOAD=1` in development mode. Production service configurations (`service/qwen-collective-service.xml` and `scripts/start-windows.ps1`) continue to invoke Uvicorn without reload.

---

## 3. Dependency Security and Reproducibility

- Audited installed packages using Python 3.12 compatibility inspection.
- Generated pinned, reproducible production lockfile: `requirements-lock.txt` (40 packages pinned to exact tested versions).
- Verified with `pip check`: `No broken requirements found.`

---

## 4. Remaining Residual Risks & Architecture Recommendations

1. **Windows 10 Operating System Lifecycle**:
   - Host is running Windows 10 Home (Build 19045). Standard mainstream support ended in October 2025. Recommend future migration to Windows 11 Pro or dedicated Linux host (Debian 12/Ubuntu 24.04).
2. **WinSW Service Account**:
   - WinSW is currently configured for `LocalSystem`. To achieve least-privilege, a dedicated Windows unprivileged service account should be provisioned with tailored read permissions on `%USERPROFILE%\.qwen-collective`.
3. **External Remote Device Sign-Off**:
   - Local reverse-proxying over Tailscale Serve (`https://desktop-44h6qvp.tailb5d90b.ts.net/`) was verified via DNS, ACME TLS, and authenticated HTTP requests. Manual end-to-end verification from an external phone or laptop connected to the tailnet remains recommended.
