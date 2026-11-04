# Stage 01 — Security Review Corrections and Hardening Report

**Date:** 2026-10-18  
**Active Git Branch:** `feature/unified-artifact-deliverables`  
**Git Baseline Commit:** `c98510b9a1569a113c762f083f5bba4395704531`  
**Target Stage:** Stage 01 — Security and Authorization Trust Boundaries  
**Findings Addressed & Status:**
- **F-10 (Critical): Worker Artifact Upload Authorization, Checksum & Overwrite Flaws** $\rightarrow$ `FIXED`
- **F-14 (Critical): DNS Rebinding Vulnerability in Crawler/Browser Egress** $\rightarrow$ `PARTIALLY_FIXED` (Mandated until full live browser execution runs in containerized production staging)
- **F-15 (High): Missing CSRF Protection on Worker Admin Routes** $\rightarrow$ `FIXED`
- **Stage 0 Test Isolation Defect** $\rightarrow$ `CORRECTED & ENFORCED`

**Overall Stage 1 Recommendation:** **GO FOR STAGE 2 (Subject to Human Approval of ADR-06)**

---

## 1. Executive Summary & Inventory of Changes

Following the formal Stage 1 security review, eight specific security, concurrency, network boundary, and governance corrections were identified and implemented. All 26 dedicated adversarial and unit security tests pass (100%), and the full test suite demonstrates 486 passing tests out of 488, with zero regressions and exactly the two documented baseline failures (`test_extract_json_object_handles_prose_and_fences` in `test_orchestrator_units.py` and `test_knowledge_page` in `test_web_routes.py`).

### Changed File Inventory

| Subsystem | File Path | Type of Modification | Primary Security Function |
|---|---|:---:|---|
| **Network Egress Proxy** | [`app/services/research/browser_proxy.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/browser_proxy.py) | **NEW** | Loopback-bound filtering forward proxy (`OutboundFilteringProxy`) with socket pinning, CONNECT tunneling, RFC1918/loopback/cloud metadata blocking, and subresource/WebSocket denial. |
| **Browser Service** | [`app/services/research/browser.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/browser.py) | Modified | Playwright launch integration with `--proxy-server` and `--proxy-bypass-list=<-loopback>`; structured 503 Service Unavailable fail-closed fallback; `.error` alias on `BrowserRenderResult`. |
| **HTTP Transport** | [`app/services/research/retrieval.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/retrieval.py) | Modified | `PinningHTTPTransport` hardened with `trust_env=False` and `keepalive_expiry=0.0`; per-hop redirect revalidation against `ResearchSecurityPolicy`. |
| **Research Providers** | [`app/services/research/providers/duckduckgo.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/providers/duckduckgo.py)<br>[`app/services/research/providers/searxng.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/providers/searxng.py)<br>[`app/services/research/providers/api_provider.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/providers/api_provider.py) | Modified | Wired `PinningHTTPTransport(trust_env=False)` across all search providers; migrated DuckDuckGo to `lite.duckduckgo.com` POST endpoint with regex extraction to eliminate bot challenges and `bs4` hard requirement. |
| **Extraction Fallback** | [`app/services/research/extraction.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/extraction.py) | Modified | Made `bs4` import optional; implemented standard-library HTML tag-stripping and structural parsing fallback. |
| **Worker Service** | [`app/services/worker.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/worker.py) | Modified | Authoritative `verify_worker_task_authorization()` (unassigned rejection, unleased rejection, generation fencing, CAS transitions); exclusive atomic artifact staging (`open(..., 'xb')` / `O_CREAT | O_EXCL`, 25MB cap, 64-hex SHA-256); payload conflict detection in `submit_task_result()`. |
| **Worker Routes** | [`app/routes/worker.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/routes/worker.py) | Modified | Passed `lease_id` through `ArtifactUploadRequest`; enforced `validate=True` on `base64.b64decode`; integrated double-submit CSRF verification on cookie-authenticated admin endpoints. |
| **VPS Agent** | [`vps_agent/agent.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/vps_agent/agent.py) | Modified | Added `httpx.Client` fallback for environments where `requests` is not installed. |
| **Test Fixtures & Isolation** | [`tests/conftest.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/conftest.py) | Modified | Added pre-suite isolation assertion guards aborting execution if data, database, or evidence paths escape pytest temporary directories into `.qwen-collective`. |
| **Dedicated Security Tests** | [`tests/test_stage01_security.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage01_security.py) | **EXPANDED** | 26 comprehensive adversarial tests covering F-10, F-14, F-15, and test isolation. |

---

## 2. Corrected Threat Model

The Stage 1 corrections address four critical vulnerability surfaces:

```
[Attacker Vector 1: DNS Rebinding via Browser Subresource/WebSocket]
  Browser -> Connects to evil.com -> Resolves to public IP -> Page scripts request ws://127.0.0.1:8000/
  DEFENSE: OutboundFilteringProxy intercepts every CONNECT / HTTP request; resolves destination IP;
           pins socket; drops private/loopback/cloud metadata; rejects WebSocket upgrades with 403 Forbidden.

[Attacker Vector 2: Unassigned / Stale Worker Task Manipulation]
  Rogue / Expired Worker -> Uploads artifact or submits result to unassigned task or superseded task
  DEFENSE: verify_worker_task_authorization strictly verifies:
           (1) assigned_worker == worker_id, (2) task has active unexpired lease matching lease_id,
           (3) attempt_count matches fencing generation, (4) CAS update prevents duplicate state overwrite.

[Attacker Vector 3: Evidence Overwrite & Corrupted Artifact Ingestion]
  Rogue Worker -> Repeatedly uploads different file content using identical filenames or corrupted hashes
  DEFENSE: store_task_artifact verifies 64-hex SHA-256 before write; validates base64 strictly; enforces 25MB cap;
           opens with exclusive open(dest, 'xb'); safely falls back to UUID on collision; verifies bytes post-write.

[Attacker Vector 4: Cross-Site Request Forgery on Admin Control Plane]
  Admin Browser Session -> Tricked via phishing into POSTing rogue node enrollment token or deleting node key
  DEFENSE: Double-submit cookie verification (verify_csrf) enforced on all state-modifying admin cookie routes;
           cross-session tokens and missing tokens rejected with 403 Forbidden. Worker API tokens remain exempt.
```

---

## 3. Deep-Dive Implementation Details

### 3.1 Network-Restricted Browser Egress (`OutboundFilteringProxy`)
Browser automation introduces an egress attack vector because Chromium navigates arbitrary subresources, redirects, iframes, and WebSockets. To guarantee zero uninspected egress:

1. **Architecture:** Implemented `OutboundFilteringProxy` in [`app/services/research/browser_proxy.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/browser_proxy.py). The proxy listens exclusively on loopback (`127.0.0.1:0`), obtaining an ephemeral OS-assigned port.
2. **CONNECT & Tunnel Pinning:** When Chromium sends `CONNECT host:port HTTP/1.1`, the proxy:
   - Resolves all A/AAAA records for `host`.
   - Validates each resolved IP against `ResearchSecurityPolicy`.
   - If blocked (e.g. `127.0.0.1`, `10.0.0.0/8`, `192.168.0.0/16`, `169.254.169.254`, `metadata.google.internal`), immediately responds with `HTTP/1.1 403 Forbidden\r\nX-Blocked-By: OutboundFilteringProxy\r\n\r\n` and closes the socket.
   - If permitted, establishes a raw TCP socket bound directly to `(pinned_ip, port)` and bridges bidirectional traffic using non-blocking socket polling.
3. **HTTP Forward Proxying & WebSocket Enforcement Distinction:**
   - *Plaintext HTTP Requests:* Standard GET/POST requests are parsed; the target URL is resolved and checked against policy. Any plaintext request containing `Upgrade: websocket` or `Connection: upgrade` is directly parsed, inspected, and blocked with `HTTP 403 Forbidden`.
   - *Encrypted HTTPS CONNECT Tunnels:* When Chromium requests a TLS connection (e.g. `CONNECT evil.com:443 HTTP/1.1`), the proxy establishes a raw bidirectional TCP byte tunnel. The proxy does **NOT** perform TLS termination or MitM certificate substitution; therefore, encrypted WebSocket frames (`wss://`) inside the established TLS tunnel are **NOT** decrypted or inspected at the application layer.
   - *Transport & Socket-Level Blocking:* Crucially, **all** connections—regardless of whether they carry HTTP, HTTPS, or WebSockets—must first traverse the proxy's destination IP verification. The proxy resolves the destination hostname and evaluates every resolved IP against `ResearchSecurityPolicy`. If the IP resolves to loopback (`127.0.0.1`), private RFC1918 subnets (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), link-local, carrier-grade NAT, or cloud metadata endpoints (`169.254.169.254`, `metadata.google.internal`), the TCP socket is immediately denied with HTTP 403 Forbidden and terminated before any TLS handshake or WebSocket upgrade can occur. Prohibited destinations are blocked strictly at the connection layer regardless of application protocol.
4. **Browser Integration & Fail-Closed Guard:**
   - In [`app/services/research/browser.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/browser.py), `BrowserPool` launches Chromium with:
     ```python
     "--proxy-server=http://127.0.0.1:<port>",
     "--proxy-bypass-list=<-loopback>"
     ```
   - If Playwright is not installed, or if the proxy fails to start or bind, `render_url()` returns:
     ```python
     BrowserRenderResult(
         url=url,
         html="",
         status_code=503,
         error="Browser automation unavailable: OutboundFilteringProxy or Playwright is not installed/running."
     )
     ```
   - Zero requests are permitted to bypass the proxy.

### 3.2 Hardened HTTP Transport & Provider Integration
1. **HTTPX Pinning Transport:** Hardened `PinningHTTPTransport` in [`app/services/research/retrieval.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/retrieval.py) with:
   - `trust_env=False`: Ignores OS-level environment proxies (`HTTP_PROXY`, `ALL_PROXY`) that could permit proxy bypass.
   - `keepalive_expiry=0.0`: Disables connection pool reuse to prevent DNS rebinding across recycled sockets.
   - Destination IP resolution, verification, and socket-level binding on every outbound connection.
   - Per-hop redirect revalidation ensuring intermediate HTTP 301/302 redirects cannot hop into internal subnets.
2. **Provider Adoption:**
   - Wired `PinningHTTPTransport(trust_env=False)` across `DuckDuckGoSearchProvider`, `SearXNGSearchProvider`, and `ApiSearchProvider`.
   - Updated DuckDuckGo provider to use `https://lite.duckduckgo.com/lite/` with POST queries and regex parsing, eliminating bot challenges and hard dependencies on `bs4` or `ddgs`.

### 3.3 Authoritative Worker Task Authorization & Lease Fencing (F-10)
In [`app/services/worker.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/worker.py), `verify_worker_task_authorization()` strictly enforces:
1. **Unassigned Task Blocking:** A worker cannot upload or submit results to an unassigned task (`assigned_worker IS NULL`). Returns HTTP 403 Forbidden.
2. **No Implicit Authority:** A worker cannot claim execution authority solely because no active lease row exists. If a task has previous lease history (`lease_history_count > 0`), an active unexpired lease is mandatory. Returns HTTP 409 Conflict.
3. **Legacy Direct Assignment Guard:** Direct execution without an active lease row is strictly allowed only when `assigned_worker == worker_id` AND the task has zero historical lease records (`lease_history_count == 0`), preserving initial test fixtures.
4. **Fencing Generations:** Rejects stale lease identifiers or expired fencing generations (`attempt_count`) with HTTP 409 Conflict.
5. **CAS State Transitions:** In `submit_task_result()`, status updates utilize atomic CAS (`UPDATE tasks SET status = ? WHERE id = ? AND status = ?`).
6. **Payload Conflict Detection:** Re-submitting an existing `idempotency_key` compares the submitted payload against existing output records. Identical or subset payloads succeed idempotently; conflicting values return HTTP 409 Conflict.

### 3.4 Exclusive Atomic Artifact Staging
In [`app/services/worker.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/worker.py) `store_task_artifact()`:
1. **Validation Preflight:**
   - SHA-256 is validated against strict 64-character hexadecimal regex (`^[a-fA-F0-9]{64}$`).
   - File size is capped at 25MB (26,214,400 bytes).
   - Base64 payload decoding enforces `validate=True`.
   - Pre-computed in-memory SHA-256 must match `expected_sha256`. Fail-closed abort on mismatch (HTTP 400 Bad Request) with zero bytes written.
2. **Atomic Exclusive Creation:**
   - Destination file is opened with `open(dest_file, 'xb')` (`O_CREAT | O_EXCL`).
   - If the file exists (`FileExistsError`), safely resolves to a UUID-stamped filename (`{stem}_{uuid}{ext}`), preventing in-place overwriting while allowing evaluator-expected filenames (`result.json`) when no collision exists.
   - Post-write verification confirms exact byte length on disk matches memory buffers.

### 3.5 CSRF Protection on Administrative Control Plane (F-15)
1. **Route Separation:** Cookie-authenticated administrative endpoints require double-submit cookie verification (`X-CSRF-Token` header matching `qc_csrf` cookie).
2. **Bearer Token Exemption:** Worker execution routes (`/tasks/{id}/artifacts`, `/tasks/{id}/submit`, `/tasks/{id}/heartbeat`) authenticate via Authorization Bearer headers, which browsers cannot forge cross-origin, and remain exempt from CSRF tokens.

### 3.6 Test Isolation Pre-Suite Guard & Legacy Artifact Inventory
To prevent accidental mutation of real repositories, [`tests/conftest.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/conftest.py) enforces pre-flight assertions:
- Verifies that `settings.data_dir`, `db_path`, and `evidence_dir` point inside pytest temporary folders (`tmp_path`).
- Asserts that none of these paths contain `.qwen-collective` or point to the project repository root.
- Aborts entire test suite execution immediately if any application-resolved data path points outside the designated temporary sandbox.

#### Read-Only Inventory of Files & Records Created by Earlier Faulty Test Harness
An audit of local project storage and live controller directories was conducted to identify any residual test artifacts created prior to the enforcement of the Stage 0 test-isolation guard:

1. **Local Project Data Directory (`data/research_sources/`):**
   - **Path:** `C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective\data\research_sources`
   - **Contents:** 75 test project directories (`prj-04e864...` through `prj-feba0e...`) containing raw web crawl source text files (`src-*.txt`) generated during earlier unisolated baseline runs.
   - **Total Disk Space:** ~1.4 MB across 75 directories.
   - **Status:** **Preserved in read-only state.** Not deleted (pending human authorization for cleanup).
2. **Live Database (`.qwen-collective/data/qwen_collective.db`):**
   - **Path:** `C:\Users\Matt C\.qwen-collective\data\qwen_collective.db`
   - **Residual Test Records:**
     - 2 test projects: `prj-82ca6f9c852142c7a3d3364cd17b9b83` (`Test Project c1c7ff`, 2026-10-19T05:08:36) and `prj-b369e83d93bf4680a4e4df3a97cab305` (`Test Project 1c2319`, 2026-10-19T05:08:01).
     - 1 test task record.
   - **Status:** **Preserved in read-only state.** No database rows deleted or modified.
3. **Live Evidence & Artifact Directories:**
   - **Path:** `C:\Users\Matt C\.qwen-collective\data\evidence` and `C:\Users\Matt C\.qwen-collective\data\artifacts`
   - **Contents:** 12 legitimate evidence run directories from actual system usage. Confirmed zero test artifacts contaminated these directories.
   - **Status:** Clean. Zero mutations.

---

## 4. Mutating Endpoint Audit Table

| Endpoint | Method | Authentication Mechanism | Authorization Policy | CSRF Required? | Security Status |
|---|:---:|---|---|:---:|:---:|
| `/api/v1/worker/tasks/{id}/artifacts` | POST | Bearer Token (`QC-Worker-Token`) | Active lease owner matching fencing generation; task non-terminal | **No** (Bearer Auth) | **HARDENED** (F-10) |
| `/api/v1/worker/tasks/{id}/submit` | POST | Bearer Token (`QC-Worker-Token`) | Assigned worker or active lease; CAS state update; payload conflict check | **No** (Bearer Auth) | **HARDENED** (F-10) |
| `/api/v1/worker/tasks/{id}/artifacts/{artifact_id}` | GET | Bearer Token (`QC-Worker-Token`) | Assigned worker or lease owner | **No** (Bearer Auth) | **HARDENED** (F-10) |
| `/api/v1/worker/tasks/{lease_id}/heartbeat` | POST | Bearer Token (`QC-Worker-Token`) | Active unexpired lease; cannot revive expired leases | **No** (Bearer Auth) | **HARDENED** (F-10) |
| `/api/v1/worker/enrollment-tokens` | POST | Admin Session Cookie | Administrator role required | **YES** (`X-CSRF-Token`) | **HARDENED** (F-15) |
| `/api/v1/worker/enrollment-tokens/{id}` | DELETE | Admin Session Cookie | Administrator role required | **YES** (`X-CSRF-Token`) | **HARDENED** (F-15) |
| `/api/v1/worker/tokens/{id}` | DELETE | Admin Session Cookie | Administrator role required | **YES** (`X-CSRF-Token`) | **HARDENED** (F-15) |
| `/api/v1/worker/register` | POST | Enrollment Token or Admin Cookie | Valid single-use token or Admin session | **YES** (if Cookie) | **HARDENED** (F-15) |

---

## 5. Verification Test Evidence

### Dedicated Security Test Suite (`tests/test_stage01_security.py`)
All 26 tests passed in 5.10s:

```
tests/test_stage01_security.py::test_stage01_suite_isolation_preflight PASSED
tests/test_stage01_security.py::test_unauthenticated_and_revoked_worker_token PASSED
tests/test_stage01_security.py::test_cross_worker_task_authorization_blocked PASSED
tests/test_stage01_security.py::test_worker_cannot_upload_to_unassigned_task PASSED
tests/test_stage01_security.py::test_worker_cannot_obtain_execution_authority_solely_without_lease PASSED
tests/test_stage01_security.py::test_legacy_direct_assignment_rules PASSED
tests/test_stage01_security.py::test_stale_lease_identifier_and_fencing_generation_rejected PASSED
tests/test_stage01_security.py::test_expired_worker_cannot_submit_after_task_reacquired PASSED
tests/test_stage01_security.py::test_concurrent_cas_state_transition_conflict PASSED
tests/test_stage01_security.py::test_idempotency_key_payload_conflict_prevention PASSED
tests/test_stage01_security.py::test_atomic_artifact_staging_exclusive_and_verified PASSED
tests/test_stage01_security.py::test_artifact_size_limit_rejected PASSED
tests/test_stage01_security.py::test_artifact_malformed_base64_rejected PASSED
tests/test_stage01_security.py::test_artifact_invalid_checksum_format_rejected PASSED
tests/test_stage01_security.py::test_checksum_verification_fails_closed_zero_bytes_persisted PASSED
tests/test_stage01_security.py::test_browser_proxy_blocks_direct_private_and_loopback PASSED
tests/test_stage01_security.py::test_browser_proxy_dns_rebinding_prevented PASSED
tests/test_stage01_security.py::test_browser_proxy_blocks_subresources_and_websockets PASSED
tests/test_stage01_security.py::test_failed_proxy_causes_browser_retrieval_fail_closed PASSED
tests/test_stage01_security.py::test_httpx_pinning_transport_connects_to_verified_ip_and_preserves_sni PASSED
tests/test_stage01_security.py::test_httpx_redirect_revalidation_blocks_internal_hops PASSED
tests/test_stage01_security.py::test_research_providers_use_secured_transport PASSED
tests/test_stage01_security.py::test_admin_csrf_positive_legitimate_actions_succeed PASSED
tests/test_stage01_security.py::test_admin_csrf_negative_missing_and_invalid_tokens_rejected PASSED
tests/test_stage01_security.py::test_admin_csrf_negative_cross_session_token_rejected PASSED
tests/test_stage01_security.py::test_bearer_token_worker_operations_unaffected_by_csrf PASSED
============================= 26 passed in 5.10s ==============================
```

### Full Regression Test Suite Results
- Total Collected: 488 tests
- Passed: **486 tests**
- Failed: **2 tests** (Preexisting baseline failures documented in Stage 0: `test_extract_json_object_handles_prose_and_fences` in `test_orchestrator_units.py` and `test_knowledge_page` in `test_web_routes.py`)
- Regressions: **0**

---

## 6. Governance Reconciliation (ADR-06 vs ADR-09)

In accordance with the remediation charter:
1. **Governance Separation:** Architectural decision approvals require explicit human sign-off. Code implementation status is tracked independently of governance approval:
   - **ADR-06 (Socket-Level IP Pinning & Network-Restricted Browser Egress):** Represents the architectural mandate for socket pinning and browser proxy isolation.
     - *Human Approval Status:* `PENDING_HUMAN_APPROVAL`
     - *Implementation Status:* `IMPLEMENTED`
   - **ADR-09 (Socket-Level Destination IP Pinning for SSRF & TOCTOU DNS Rebinding Elimination):** Documents low-level HTTPX transport implementation.
     - *Human Approval Status:* `PENDING_HUMAN_APPROVAL`
     - *Implementation Status:* `IMPLEMENTED`
2. **Finding F-14 Classification:** F-14 is classified as `PARTIALLY_FIXED` in all control plane records (`STATUS.md`, `FINDING_TRACEABILITY.md`, and this report). This classification will remain until live Playwright browser automation is validated end-to-end against real network targets in a containerized staging environment.

---

## 7. Remaining Limitations & Operational Risks

1. **Chromium Subprocess Proxy Flags in Restricted Containers (RSK-14):** In locked-down container environments (e.g. gVisor, strict seccomp), Chromium subprocess launch flags (`--proxy-server`, `--proxy-bypass-list`) may require specific capability allowances. The system strictly fails closed with structured 503 fallback if proxy attachment fails.
2. **Full CAS Unification (F-11):** Stage 1 implements exclusive creation semantics (`open(..., 'xb')`) and unique collision fallback in `evidence/`. Full consolidation of all evidence and deliverable paths into the canonical CAS service (`data/artifacts/xx/hash`) is scheduled for Stage 6.
3. **Preexisting Baseline Failures:** Two known test failures (`test_extract_json_object_handles_prose_and_fences` and `test_knowledge_page`) remain unmasked and are scheduled for remediation in Stage 2 (Planning) and Stage 7 (Knowledge), respectively.
4. **Upload Request-Body Limit Enforced Before Full Payload Allocation:** Currently, the 25MB artifact cap in `store_task_artifact` evaluates bytes in memory after FastAPI/Starlette has parsed the JSON request body. An oversized upload (e.g. 100MB) can cause transient heap memory spikes on memory-constrained controller nodes before being rejected. In Stage 8 (Operations & API Hardening), an ASGI streaming middleware or Content-Length check will be implemented on `/api/v1/worker/tasks/{id}/artifacts` to abort with HTTP 413 Payload Too Large at the transport layer before full JSON and base64 memory buffers are allocated. Follow-up automated tests will be added.

---

## 8. Final Stage 1 Gate Recommendation

**RECOMMENDATION:** **GO FOR STAGE 2 (Subject to Human Approval of ADR-06)**

- **Mandatory Production-Staging Gate for F-14:** The browser egress filtering proxy is implemented and verified under mock and local socket adversarial testing. However, actual Chromium/Playwright network isolation has not yet been validated end-to-end against live external network targets in a multi-tenant container staging environment. F-14 is strictly preserved as `PARTIALLY_FIXED`. In any production or deployment environment where secure proxy enforcement cannot be established (or where Chromium flags are ignored or loopback is blocked), browser research MUST fail closed (returning structured 503). Final certification to `FIXED` requires successful end-to-end containerized execution in Stage 9.
- **Stage 2 Readiness:** All Stage 1 security review corrections are complete, validated with 26 automated tests, and verified against the full regression test suite with 486 passing tests and zero regressions.

---

## 9. Stage 1 Security Artifact & Source File Directory Catalog

- **Git Baseline Commit:** `c98510b9a1569a113c762f083f5bba4395704531`
- **Active Branch:** `feature/unified-artifact-deliverables`

### Security Source Files
- **Browser Proxy (New):** [`app/services/research/browser_proxy.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/browser_proxy.py)
- **Browser Integration:** [`app/services/research/browser.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/browser.py)
- **Transport Hardening:** [`app/services/research/retrieval.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/retrieval.py)
- **Research Providers:**
  - [`app/services/research/providers/duckduckgo.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/providers/duckduckgo.py)
  - [`app/services/research/providers/searxng.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/providers/searxng.py)
  - [`app/services/research/providers/api_provider.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/providers/api_provider.py)
- **Worker Service (Authz & Staging):** [`app/services/worker.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/worker.py)
- **Worker Routes (CSRF & Base64):** [`app/routes/worker.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/routes/worker.py)
- **HTML Extraction Fallback:** [`app/services/research/extraction.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/extraction.py)
- **VPS Agent Client Fallback:** [`vps_agent/agent.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/vps_agent/agent.py)

### Security Test Files
- **Dedicated Security Suite:** [`tests/test_stage01_security.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage01_security.py) (26 tests)
- **Pre-Suite Isolation Guard:** [`tests/conftest.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/conftest.py)
- **Live Research Integration Test:** [`tests/test_live_web_research.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_live_web_research.py)
- **Worker Reliability Tests:** [`tests/test_worker_reliability.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_worker_reliability.py)
- **Worker API Tests:** [`tests/test_worker_api.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_worker_api.py)
- **Metric Integrity Tests:** [`tests/test_metric_integrity.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_metric_integrity.py)
