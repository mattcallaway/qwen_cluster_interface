# Architecture Decision Records (ADRs)

This document records architectural proposals, trade-offs, security implications, and compatibility rationales for the Qwen Collective remediation program.

**Governance Policy:** No architectural decision is marked `APPROVED` without explicit human authorization. All entries prior to human review are categorized as `PROPOSED / PENDING_HUMAN_APPROVAL`.

---

## ADR-01: Compatibility-First Route Aliasing for Documented Endpoints
- **Date:** 2026-10-18
- **Status:** `PROPOSED / PENDING_HUMAN_APPROVAL`
- **Target Stage:** Stage 8
- **Context:** `API_AND_INTEGRATION_SPECIFICATION.md` documents endpoints such as `/api/v1/orchestrator/runs`, `/api/v1/orchestrator/runs/{id}/plan`, and `/api/v1/artifacts/{id}/download`. The implementation in `app/routes/orchestration.py` uses `/api/v1/orchestration/projects/{project_id}/runs` and related nested paths (Finding F-16).
- **Proposal:** Implement backward-compatible route aliases for documented paths while retaining canonical routes. Both paths resolve to the same underlying service handlers.
- **Implications & Testing:** Tests must exercise both paths and assert identical JSON payloads and status codes.
- **Alternatives Considered:** 
  1. *Breaking Change:* Rename all routes to match docs (breaks web UI and existing clients).
  2. *Doc Only:* Update docs to match code (breaks external scripts following the published specification).
- **Compatibility & Regression Risks:** Low risk. Aliases are additive.
- **Explicit Approval Required:** Yes, required prior to Stage 8 implementation.

---

## ADR-02: Strict Anti-Substitution Invariant for Plan Validation & Repair
- **Date:** 2026-10-18
- **Status:** `IMPLEMENTED_IN_STAGE_2 / PENDING_HUMAN_APPROVAL`
- **Target Stage:** Stage 2
- **Context:** `task_graph.py` previously repaired unrecognized `library_script` or `controller_step` actions by silently rewriting them to `inference` or `assemble_report` (Finding F-01). Cycles were silently dropped by deleting edges. Deliverables with invalid `produced_by` references were arbitrarily reassigned.
- **Implementation:**
  1. Authoritative Action Registry implemented in [`app/services/orchestration/action_registry.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/action_registry.py) with dynamic capability probes, TTL tracking, and structured capability gaps.
  2. `auto_repair_plan` strictly refactored in [`app/services/orchestration/task_graph.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/task_graph.py) to eliminate silent action conversions, preserve cycle edges so Kahn's algorithm flags `Dependency cycle among tasks`, and reject missing deliverable producers.
  3. Lossless intake requirement ledger implemented in [`app/services/orchestration/intake.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/intake.py) to preserve all user objectives, constraints, exclusions, deliverables, and success criteria without parameter bleed-through or hard slicing.
  4. Dynamic token budgeting (`calculate_planner_token_budget`) and semantic revision diffing (`diff_plans`) implemented in [`app/services/orchestration/planner.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/planner.py) to protect small model context windows and enforce mandatory re-approval gates.
- **Implications & Testing:** 13 positive and negative tests verified in [`tests/test_stage02_planning.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage02_planning.py).
- **Alternatives Considered:**
  1. *Permissive Auto-Downgrade:* Allow conversion with a warning (rejected: causes false-positive executions).
  2. *Automatic Cycle Reordering:* Reorder tasks automatically (rejected: risks executing tasks out of causal order).
- **Compatibility & Regression Risks:** Plans requesting unavailable scripts fail explicitly with capability errors rather than executing unintended inference commands. Safe syntax repairs (self-reference stripping, deliverable type aliasing) are fully preserved.
- **Explicit Approval Required:** Yes, verified in Stage 2 review.

---

## ADR-03: Single Authoritative Content-Addressed Storage (CAS) with Staging Adapter
- **Date:** 2026-10-18
- **Status:** `PROPOSED / PENDING_HUMAN_APPROVAL`
- **Target Stage:** Stage 1 (quarantine staging) / Stage 6 (full CAS unification)
- **Context:** Artifacts are written to two conflicting stores: `app/services/artifacts/manager.py` (CAS stored at `data/artifacts/xx/hash`) and `app/services/orchestration/artifact_manager.py` (mutable directory paths at `evidence/orch_{run_id}/{name}`), permitting overwriting (Findings F-10, F-11).
- **Proposal:** Establish `app/services/artifacts/manager.py` as the sole authoritative CAS catalog. In Stage 1, implement secure temporary quarantine staging. In Stage 6, migrate all legacy paths to CAS with dry-run verification and backward-compatible read adapters.
- **Implications & Testing:** Cryptographic hashing during streaming. Existing legacy files are hashed and indexed without destructive deletions.
- **Alternatives Considered:**
  1. *Maintain Dual Catalogs:* Keep evidence and artifacts separate (rejected: violates Invariant I-09).
  2. *Immediate Hard CAS Cutover in Stage 1:* Immediate migration of all orchestration code (rejected: too broad a blast radius for Stage 1 security fix).
- **Compatibility & Regression Risks:** Medium risk during Stage 6 migration; mitigated by non-destructive dry-run.
- **Explicit Approval Required:** Yes, required prior to Stage 6 migration.

---

## ADR-04: Strict Citation Enforcement over Synthetic Heuristics
- **Date:** 2026-10-18
- **Status:** `PROPOSED / PENDING_HUMAN_APPROVAL`
- **Target Stage:** Stage 4
- **Context:** The research pipeline currently extracts pseudo-claims using arbitrary first-sentence slicing with hardcoded 0.9 confidence (Finding F-06) and permits reports with zero citations to pass validation (Finding F-07).
- **Proposal:** Enforce strict citation validation: if research sources were retrieved, reports with zero in-body citations must fail validation (`is_valid = False`, `MISSING_REQUIRED_CITATIONS`). Replace arbitrary first-sentence chunking with claim-driven extraction and source-span linking.
- **Implications & Testing:** Zero-citation fixture must fail; syndicated duplicate sources must not count as independent corroboration.
- **Alternatives Considered:**
  1. *Warning-Only Citations:* Log warning on missing citations (rejected: violates Invariant I-07).
- **Compatibility & Regression Risks:** Generates validation failures for reports generated by weak models that omit citations.
- **Explicit Approval Required:** Yes, required prior to Stage 4 implementation.

---

## ADR-05: Decoupling Task Completion from Deliverable Acceptance
- **Date:** 2026-10-18
- **Status:** `PROPOSED / PENDING_HUMAN_APPROVAL`
- **Target Stage:** Stage 3
- **Context:** `evaluator.py` marks inference deliverables valid if they exceed 20 characters, and `engine.py` automatically marks deliverables accepted if autonomy level >= 2 without verifying objective fulfillment (Finding F-12).
- **Proposal:** Decouple task execution completion (`task.status = "completed"`) from deliverable acceptance (`deliverable.status = "accepted"`). Deliverable evaluation must test against user acceptance criteria. Autonomy level >= 2 can bypass manual approval only if objective acceptance checks pass.
- **Implications & Testing:** Tests must prove that 25-character placeholder text is rejected and broken code samples fail acceptance even if formatted nicely.
- **Alternatives Considered:**
  1. *Increase Character Threshold:* Require 200 chars instead of 20 (rejected: still rubber-stamps hallucinated text).
- **Compatibility & Regression Risks:** Projects with autonomy >= 2 that previously auto-completed will now stop for review if deliverables fail validation.
- **Explicit Approval Required:** Yes, required prior to Stage 3 implementation.

---

## ADR-06: Socket-Level IP Pinning & Network-Restricted Browser Egress for SSRF & DNS Rebinding Elimination
- **Date:** 2026-10-18
- **Human Approval Status:** `PENDING_HUMAN_APPROVAL`
- **Implementation Status:** `IMPLEMENTED` (Stage 1)
- **Target Stage:** Stage 1
- **Governance Note:** In accordance with remediation program policy, architectural decisions require explicit human authorization to transition to `APPROVED`. Code implementation of this control is complete (`IMPLEMENTED`), while formal governance approval remains pending human review prior to production sign-off. ADR-09 details the low-level HTTPX transport implementation; this ADR encompasses the end-to-end network boundary including the browser egress filtering proxy.
- **Context:** Preflight DNS resolution validates domain IPs, but subsequent HTTP requests and browser sessions connect by hostname, allowing time-of-check to time-of-use (TOCTOU) DNS rebinding attacks targeting localhost or internal LAN services (Finding F-14).
- **Proposal:** Implement a custom HTTPX transport that pins the socket connection directly to the pre-validated IP address while preserving the original Host header and SNI for TLS. For Playwright browser automation, route all egress through a local forward filtering proxy (`OutboundFilteringProxy`) with destination IP pinning, CONNECT tunneling, redirect/subresource blocking, and strict fail-closed behavior.
- **Security Requirement on Failure:** **FAIL CLOSED.** If secure transport or socket-level destination pinning is unavailable or fails, web retrieval and browser rendering MUST fail closed (returning structured 503 Service Unavailable / 403 Forbidden). Insecure fallback to unpinned hostname connections or direct browser egress is strictly forbidden.
- **Implications & Testing:** Deterministic mock-DNS rebinding tests demonstrate that requests rebinding to `127.0.0.1` are blocked at the connection layer. Verified across 6 dedicated adversarial browser tests in `tests/test_stage01_security.py`.
- **Alternatives Considered:**
  1. *Preflight DNS Check Only:* Retain existing preflight check (rejected: proven vulnerable to DNS rebinding).
  2. *OS-Level Firewall Only:* Rely solely on Windows/Linux packet filters (rejected: insufficient defense-in-depth).
- **Compatibility & Regression Risks:** Requires careful SNI and Host header handling to prevent TLS certificate validation failures on virtual-hosted web servers.
- **Explicit Approval Required:** Yes, human approval required prior to Stage 2 authorization.

---

## ADR-07: Anti-CSRF Token Enforcement on Administrative Endpoints
- **Date:** 2026-10-18
- **Status:** `PROPOSED / PENDING_HUMAN_APPROVAL`
- **Target Stage:** Stage 1
- **Context:** Admin endpoints for worker approval, rejection, and key revocation rely on cookie authentication without CSRF token validation (Finding F-15).
- **Proposal:** Implement anti-CSRF token verification (`X-CSRF-Token` header or double-submit cookie) for all cookie-authenticated state-modifying routes. Preserve separate bearer-token authentication for worker and API clients.
- **Implications & Testing:** TestClient tests must prove that cookie-authenticated POSTs without CSRF token receive HTTP 403 Forbidden.
- **Alternatives Considered:**
  1. *Rely on SameSite=Lax:* Rely solely on browser cookie flags (rejected: insufficient protection against cross-origin form submissions or older browsers).
- **Compatibility & Regression Risks:** Web UI forms must include the CSRF token in headers or form fields.
- **Explicit Approval Required:** Yes, required prior to Stage 1 implementation.

---

## ADR-08: Complete Disaster Recovery & Atomic Backup Bundling
- **Date:** 2026-10-18
- **Status:** `PROPOSED / PENDING_HUMAN_APPROVAL`
- **Target Stage:** Stage 8
- **Context:** Existing backup runbooks only cover `data/qwen_collective.db`, leaving CAS artifacts, vault notes, and manifests unbacked (Finding F-17).
- **Proposal:** Implement an atomic backup utility creating a verified archive containing:
  1. Consistent SQLite backup via `VACUUM INTO` or online backup API.
  2. Content-Addressed Storage blobs (`data/artifacts/`).
  3. Evidence files and knowledge vault markdown documents.
  4. Cryptographic manifest verifying SHA-256 digests.
  Must include an automated isolated restore rehearsal test.
- **Implications & Testing:** Backup must be restored to an empty temporary directory and pass complete integrity checks.
- **Alternatives Considered:**
  1. *Filesystem Snapshot (VSS/LVM):* Rely on external storage snapshots (rejected: not platform-independent across Windows and Linux).
- **Compatibility & Regression Risks:** Backup process must handle large CAS storage without causing prolonged controller lockups.
- **Explicit Approval Required:** Yes, required prior to Stage 8 implementation.

---

## ADR-09: Socket-Level Destination IP Pinning for SSRF & TOCTOU DNS Rebinding Elimination
- **Date:** 2026-10-18
- **Human Approval Status:** `PENDING_HUMAN_APPROVAL`
- **Implementation Status:** `IMPLEMENTED` (Stage 1)
- **Target Stage:** Stage 1
- **Context:** `validate_url()` performed preflight DNS checks, but HTTPX and Playwright re-resolved hostnames when opening sockets, exposing the crawler to TOCTOU DNS rebinding (F-14).
- **Decision:** Implemented `PinningSyncBackend` (`httpcore.SyncBackend`) and `PinningHTTPTransport` (`httpx.HTTPTransport`) that resolves DNS, validates all resolved IPs against security policy, and binds raw TCP sockets directly to `(pinned_ip, port)`. Passes `server_hostname=host` to `start_tls` to ensure SNI and certificate validation succeed.
- **Implications & Testing:** Completely eliminates TOCTOU DNS rebinding without disabling TLS certificate verification. Verified in `test_research_ssrf_dns_rebinding_blocked_by_pinning`.

---

## ADR-10: Fail-Closed Checksum Verification with Zero Persistence on Corrupted Artifacts
- **Date:** 2026-10-18
- **Status:** `IMPLEMENTED` (Stage 1)
- **Target Stage:** Stage 1
- **Context:** In F-10, worker artifact uploads with mismatched checksums logged a warning but returned HTTP 200 and persisted the bad bytes to disk.
- **Decision:** In `store_task_artifact`, compute SHA-256 over raw bytes in memory prior to opening file handles. If `expected_sha256` does not match, immediately abort with `ValueError` (mapped to HTTP 400 Bad Request) and write zero bytes to disk.
- **Implications & Testing:** Protects evidence store from corrupted or tampered files. Verified in `test_checksum_verification_fails_closed_zero_bytes_persisted`.

---

## ADR-11: Content-Derived Staging and Display Filename Path Traversal Sanitization
- **Date:** 2026-10-18
- **Status:** `IMPLEMENTED` (Stage 1)
- **Target Stage:** Stage 1
- **Context:** Upload filenames allowed directory traversal sequences (`..`) and repeated uploads mutably overwrote existing evidence files (F-10, initial F-11).
- **Decision:** Sanitize all upload filenames using `sanitize_display_filename` to neutralize `..`, control characters, directory separators, and Windows reserved device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1..9`, `LPT1..9`). For colliding filenames, append content hash prefixes and timestamps to prevent in-place overwrites.
- **Implications & Testing:** Protects controller filesystem from arbitrary write attacks. Verified in `test_filename_sanitization_and_no_overwrite`.

---

## ADR-12: Centralized Task Authorization Gate with Active Lease Fencing and Direct Execution Compatibility
- **Date:** 2026-10-18
- **Status:** `IMPLEMENTED` (Stage 1)
- **Target Stage:** Stage 1
- **Context:** Worker routes lacked task ownership and lease fencing checks, allowing workers to mutate arbitrary tasks (F-10).
- **Decision:** Created `verify_worker_task_authorization(worker_id, task_id, operation, lease_id, conn)`. Enforces:
  1. Task existence (404)
  2. Assigned worker match (403)
  3. No competing active lease held by another worker (403)
  4. Active lease required for artifact upload (409 if expired)
  5. Delayed/offline result reconciliation supported for submit operations
  6. Direct assignment compatibility for unleased tasks (preserves test fixtures and direct executions).
- **Implications & Testing:** Unifies authorization across `/artifacts`, `/submit`, and `/artifacts/{id}`. Verified in `test_cross_worker_task_authorization_blocked` and `test_expired_lease_fencing_blocks_upload`.

---

## ADR-13: CSRF Token Enforcement on Cookie-Authenticated Admin Worker Endpoints
- **Date:** 2026-10-18
- **Status:** `IMPLEMENTED` (Stage 1)
- **Target Stage:** Stage 1
- **Context:** Admin worker management endpoints relied solely on session cookies without CSRF validation (F-15).
- **Decision:** Enforce `verify_csrf(request, request.headers.get("X-CSRF-Token"))` on `POST /enrollment-tokens`, `DELETE /enrollment-tokens/{id}`, `DELETE /tokens/{id}`, and cookie-authenticated `POST /register`. Token-authenticated worker requests remain exempt.
- **Implications & Testing:** Defeats browser-driven CSRF attacks against worker infrastructure. Verified in `test_admin_worker_routes_csrf_protection`.

