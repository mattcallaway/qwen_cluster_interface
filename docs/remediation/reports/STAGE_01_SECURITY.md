# Stage 01 — Security and Authorization Trust Boundaries

**Date:** 2026-10-18  
**Active Branch:** `feature/unified-artifact-deliverables`  
**Target Stage:** Stage 01 — Security and Authorization Trust Boundaries  
**Covered Findings:** **F-10**, **F-14**, **F-15** (with initial hardening for **F-11**, **F-13**)  
**Status:** **READY_FOR_REVIEW**  

---

## 1. Scope and Covered Findings

Stage 1 establishes zero-trust authorization boundaries, fail-closed artifact intake, socket-level egress isolation, and CSRF protection across the controller and worker planes:

- **F-10 (Critical): Worker Artifact Upload Authorization, Checksum & Overwrite Flaws:**
  - Enforced authoritative task ownership and lease fencing on all worker task mutations.
  - Implemented fail-closed checksum verification: computed SHA-256 in memory before writing to disk; invalid or mismatched hashes abort immediately with HTTP 400 Bad Request and zero bytes persisted.
  - Sanitized display filenames against directory traversal (`..`), path separators, control characters, and Windows reserved device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1..9`, `LPT1..9`).
  - Enforced immutable, non-overwriting staging using unique content-derived filenames (`{stem}_{hash[:10]}_{timestamp}{ext}`).
- **F-14 (Critical): DNS Rebinding & SSRF Vulnerabilities in Crawler/Browser Egress:**
  - Implemented socket-level destination IP pinning in `PinningSyncBackend` (`httpcore.SyncBackend`) and `PinningHTTPTransport` (`httpx.HTTPTransport`), closing TOCTOU DNS rebinding windows.
  - Expanded SSRF IP decoders in `app/services/research/policy.py` to detect and reject numeric (integer), hex (`0x...`), and octal (`0...`) representations of loopback, private, link-local, and cloud metadata addresses (`169.254.169.254`, `169.254.169.250`, `169.254.170.2`, `100.100.100.200`, `metadata.google.internal`).
  - Hardened Playwright route interception in `BrowserPool` with destination IP resolution checks and disabled file downloads (`accept_downloads=False`).
- **F-15 (High): Missing CSRF Protection on Worker Admin Routes:**
  - Enforced `verify_csrf` token verification on cookie-authenticated admin worker endpoints (`POST /enrollment-tokens`, `DELETE /enrollment-tokens/{id}`, `DELETE /tokens/{id}`, and cookie-authenticated `POST /register`).
  - Maintained exemptions for token-authenticated worker node API operations.
- **Initial Hardening for F-11 & F-13:**
  - F-11: Artifact collision avoidance implemented via immutable hash-stamped files in evidence storage.
  - F-13: Unlocked non-blocking worker lease queries and heartbeat renewal validation.

---

## 2. Threat Model and Attack Surface Analysis

Prior to Stage 1, the following high-severity attack vectors were verified:

1. **Cross-Worker Artifact Injection (F-10):** A worker possessing a valid API token could upload arbitrary files to any existing `task_id` assigned to another worker node, corrupting pipeline deliverables.
2. **Expired Lease State Desynchronization (F-10):** Workers whose leases expired or were superseded by another worker could still upload new files to the task evidence directory.
3. **Poisoned Artifact Ingestion (F-10):** When an artifact upload included an `expected_sha256` that mismatched the uploaded bytes, the controller logged a warning but returned HTTP 200 and wrote the bad bytes to disk.
4. **Time-of-Check to Time-of-Use (TOCTOU) DNS Rebinding (F-14):** A malicious actor could host a domain whose initial DNS A-record pointed to a public IP to pass URL validation, but whose subsequent DNS resolution pointed to `127.0.0.1` or `169.254.169.254` when HTTPX or Playwright opened a socket.
5. **Cross-Site Request Forgery (F-15):** A logged-in administrator visiting a malicious web page could be tricked into executing browser-submitted `DELETE` requests against worker authentication tokens or `POST` requests generating rogue enrollment tokens.

---

## 3. Implementation Details

### Workstream 1A: Authoritative Worker Task Authorization & Lease Fencing (F-10, F-11)
- **Centralized Authorization Gate:** Created [`verify_worker_task_authorization()`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/worker.py#L90) in `app/services/worker.py`. Enforces:
  - Task existence (HTTP 404 if missing).
  - Assigned worker identity (HTTP 403 if assigned to another worker).
  - Active lease ownership (HTTP 403 if actively leased to another worker).
  - Lease expiration fencing (HTTP 409 Conflict if lease has expired when uploading artifacts).
  - Task state eligibility (HTTP 409 Conflict if task is in terminal `completed`, `cancelled`, or `failed` state).
  - Direct assignment compatibility: preserves legacy and test fixture executions where tasks are directly assigned without separate lease rows.
- **Fail-Closed Artifact Ingestion:** Updated [`store_task_artifact()`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/worker.py#L740):
  - Pre-computes SHA-256 over raw `content_bytes` in memory.
  - Aborts immediately with `ValueError` (mapped to HTTP 400 Bad Request) on hash mismatch. Zero bytes are written to disk.
  - Sanitizes filenames via [`sanitize_display_filename()`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/worker.py#L70): strips `..`, directory separators, control chars, and Windows reserved filenames.
  - Stems colliding filenames with unique hash prefixes and timestamps to prevent mutable overwrites.
- **Idempotency Conflict Guard:** Updated [`submit_task_result()`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/worker.py#L1250): resubmitting the same `idempotency_key` with a conflicting completion status returns HTTP 409 Conflict.

### Workstream 1B: Socket-Level IP Pinning & SSRF Hardening (F-14)
- **Socket-Level Destination Pinning:** Created [`PinningSyncBackend`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/retrieval.py#L65) (`httpcore.SyncBackend`) and [`PinningHTTPTransport`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/retrieval.py#L125) (`httpx.HTTPTransport`):
  - Performs preflight DNS resolution, parses all resolved IP addresses, and checks each IP against `ResearchSecurityPolicy`.
  - Connects the underlying TCP socket directly to `(pinned_ip, port)`.
  - Passes original hostname via `server_hostname=host` during TLS handshake (`start_tls`), ensuring SNI, certificate validation, and hostname verification succeed without TOCTOU DNS vulnerability.
- **Enhanced SSRF Policy:** Updated [`resolve_and_verify_destination()`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/policy.py#L160):
  - Added numeric, octal (`0177.0.0.1`), and hexadecimal (`0x7f000001`) IP decoders via `socket.inet_aton`.
  - Added blocking for cloud metadata IPs (`169.254.169.254`, `169.254.169.250`, `169.254.170.2`, `100.100.100.200`) and hostnames (`metadata.google.internal`).
- **Browser Route Interception:** Enforced destination IP resolution filtering in `BrowserPool._route_filter()` and set `accept_downloads=False`.

### Workstream 1C: CSRF Protection on Worker Admin Endpoints (F-15)
- **CSRF Token Enforcement:** Added `verify_csrf(request, request.headers.get("X-CSRF-Token"))` checks in `app/routes/worker.py` on:
  - `POST /api/v1/worker/enrollment-tokens`
  - `DELETE /api/v1/worker/enrollment-tokens/{token_id}`
  - `DELETE /api/v1/worker/tokens/{token_id}`
  - `POST /api/v1/worker/register` (when authenticated via administrator session cookie)
- **API Caller Exemption:** Direct Bearer-token worker requests continue without CSRF overhead since browsers do not attach custom Authorization headers automatically.

---

## 4. Mutating Endpoint Audit Table

| Endpoint | Method | Authentication | Authorization Policy | CSRF Required? | Status |
|---|:---:|---|---|:---:|:---:|
| `/api/v1/worker/tasks/{task_id}/artifacts` | POST | Worker Bearer Token | Active task lease owner; task must be non-terminal | No (Bearer Auth) | **HARDENED** (F-10) |
| `/api/v1/worker/tasks/{task_id}/submit` | POST | Worker Bearer Token | Assigned worker or lease owner; idempotency conflict check | No (Bearer Auth) | **HARDENED** (F-10) |
| `/api/v1/worker/tasks/{task_id}/artifacts/{artifact_id}` | GET | Worker Bearer Token | Assigned worker or lease owner; project artifact access | No (Bearer Auth) | **HARDENED** (F-10) |
| `/api/v1/worker/tasks/{lease_id}/heartbeat` | POST | Worker Bearer Token | Active unexpired lease; cannot revive expired lease | No (Bearer Auth) | **HARDENED** (F-10) |
| `/api/v1/worker/enrollment-tokens` | POST | Admin Session Cookie | Administrator role required | **YES** (`X-CSRF-Token`) | **HARDENED** (F-15) |
| `/api/v1/worker/enrollment-tokens/{token_id}` | DELETE | Admin Session Cookie | Administrator role required | **YES** (`X-CSRF-Token`) | **HARDENED** (F-15) |
| `/api/v1/worker/tokens/{token_id}` | DELETE | Admin Session Cookie | Administrator role required | **YES** (`X-CSRF-Token`) | **HARDENED** (F-15) |
| `/api/v1/worker/register` | POST | Enrollment Token / Admin Cookie | Valid single-use token or Admin session | **YES** (if Cookie) | **HARDENED** (F-15) |

---

## 5. Verification Test Results

### Dedicated Security Test Suite (`tests/test_stage01_security.py`)
All 10 adversarial security tests passed:

1. `test_unauthenticated_and_revoked_worker_token`: Rejected with HTTP 401.
2. `test_cross_worker_task_authorization_blocked`: Cross-worker upload, submit, and download rejected with HTTP 403 Forbidden.
3. `test_worker_upload_and_submit_to_nonexistent_task`: Nonexistent task access rejected with HTTP 404 Not Found.
4. `test_expired_lease_fencing_blocks_upload`: Expired lease uploads rejected with HTTP 409 Conflict; heartbeat renewal rejected.
5. `test_checksum_verification_fails_closed_zero_bytes_persisted`: Corrupted hash upload rejected with HTTP 400 Bad Request; 0 bytes written to disk.
6. `test_filename_sanitization_and_no_overwrite`: Path traversal neutralized (`..` stripped); repeated uploads stamped with unique content hashes.
7. `test_submit_task_result_idempotency_and_payload_conflict`: Matching submission returns existing execution; conflicting status returns HTTP 409 Conflict.
8. `test_research_ssrf_dns_rebinding_blocked_by_pinning`: Rebinding domain resolved and pinned to IP; blocked with `DestinationBlockedError`.
9. `test_research_ssrf_expanded_ip_encodings_blocked`: Octal, hex, numeric, and cloud metadata IPs blocked.
10. `test_admin_worker_routes_csrf_protection`: Missing/invalid CSRF on cookie-authenticated admin worker routes returns HTTP 403 Forbidden.

### Regression Test Suite Results
- Baseline passing tests preserved: 460
- New Stage 1 security tests: 10
- Total passing tests: 470
- Preexisting baseline failures (documented in Stage 0): 2 (`test_orchestrator_units.py`, `test_web_routes.py`)
- Regressions: **0**

---

## 6. Operational Procedures & Rollback Plan

- **Rollback Procedure:** If worker connectivity or upload issues occur, revert Stage 1 commits on branch `feature/unified-artifact-deliverables`. The database schema was unchanged; no table migrations are required to roll back.
- **Fencing Observability:** Expired lease rejection and cross-worker access attempts log warning-level security events tagged with worker node ID, task ID, and IP address.
