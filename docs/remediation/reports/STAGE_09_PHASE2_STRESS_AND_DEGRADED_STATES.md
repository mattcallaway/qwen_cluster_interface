# Stage 9 Phase 2: Stress & Degraded States Certification Report

> **Stage**: Stage 9 — End-to-End Black-Box Verification & Release Certification  
> **Phase**: Phase 2 — Local Multi-Process Concurrency & Degraded States  
> **Status**: **PASSED (3 / 3 SCENARIOS CERTIFIED)**  
> **Execution Date**: 2026-10-21  
> **Evidence Directory**: [`docs/remediation/evidence/stage09/`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/stage09/)  
> **Structured Evidence File**: [`docs/remediation/evidence/stage09/bb_phase2_results.json`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/stage09/bb_phase2_results.json)  
> **Phase 2 Test Harness**: [`run_stage09_phase2_stress_and_degraded.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/run_stage09_phase2_stress_and_degraded.py)  
> **Governance State**: **HOLD FOR HUMAN REVIEW** (No Phase 3/4 testing executed)

---

## 1. Executive Summary

In accordance with limited Stage 9 Phase 2 authorization, this report documents:
1. **Authenticity Boundary Audit of Stage 9 Phase 1**: Rigorous inspection of the execution boundaries exercised across all 21 Phase 1 scenarios, establishing the technical distinction between in-process ASGI HTTP requests and internal Python service calls.
2. **Reclassification of Phase 1 Claims**: Reclassifying direct service-function evaluations as `FUNCTIONALLY VERIFIED / BLACK-BOX EVIDENCE INCOMPLETE` while preserving functional verification integrity.
3. **Corrected Windows 10 Operating System & ESU Support Assessment**: Reconciling host edition to Windows 10 Home (Build `10.0.19045.7807`), correcting consumer ESU entitlement through **October 12, 2027**, and defining the boundary between developer workstation usage and enterprise production controller hosting.
4. **Execution of Phase 2 Stress & Degraded Scenarios**: Complete empirical certification of **BB-18** (Controller concurrency and non-blocking scheduling under slow work), **BB-20** (Missing telemetry detection and refusal to fabricate data), and **BB-25** (External dependency failure and offline degraded operation).

---

## 2. Phase 1 Test-Boundary Authenticity Audit

An empirical audit of `run_stage09_phase1_blackbox.py` was conducted to determine the exact boundary exercised by each scenario:

### 2.1 Interface Categories
1. **In-Process ASGI HTTP Client (`TestClient`)**: Dispatches real HTTP requests through the FastAPI/Starlette routing pipeline, middleware, authentication session decoding, and CSRF verification. Does not traverse physical TCP network interfaces.
2. **Internal Python Service Function**: Calls Python service functions, database models, or CLI utility engines directly in-process without entering the HTTP ASGI stack.
3. **Real Running HTTP Server**: Binds a listening TCP socket (`0.0.0.0` or `127.0.0.1:8000`) and receives network requests across the loopback adapter. (None in Phase 1).
4. **Real Headless Browser**: Automates Chromium/WebKit to interact with rendered HTML/DOM. (None in Phase 1).
5. **Cross-Component Workflow**: Chains multiple discrete subsystems (e.g. database snapshot + CAS archive + file restoration).

### 2.2 Scenario Boundary Inventory & Reclassification

| Scenario ID | Canonical Title | Exercised Boundary | Functional Verdict | Black-Box Authenticity Classification |
|---|---|---|:---:|:---:|
| **BB-01** | Unknown script produces CAPABILITY_GAP | Internal Service Function (`script_library.validate`) | **PASS** | **FUNCTIONALLY VERIFIED / BLACK-BOX EVIDENCE INCOMPLETE** |
| **BB-02** | Unsupported plan step requires replan | Internal Service Function (`planner.validate_plan`) | **PASS** | **FUNCTIONALLY VERIFIED / BLACK-BOX EVIDENCE INCOMPLETE** |
| **BB-03** | Cyclic task graph is rejected | Internal Service Function (`task_graph.detect_cycle`) | **PASS** | **FUNCTIONALLY VERIFIED / BLACK-BOX EVIDENCE INCOMPLETE** |
| **BB-04** | Deliverables & criteria preserved | In-Process ASGI HTTP (`POST /projects`) | **PASS** | **PASS (IN-PROCESS ASGI)** |
| **BB-05** | Report without citations fails audit | Internal Service Function (`CitationEngine.audit_report`) | **PASS** | **FUNCTIONALLY VERIFIED / BLACK-BOX EVIDENCE INCOMPLETE** |
| **BB-06** | Syndicated sources not independent | Internal Service Function (`PublicSuffixManager`) | **PASS** | **FUNCTIONALLY VERIFIED / BLACK-BOX EVIDENCE INCOMPLETE** |
| **BB-07** | Contradictory evidence surfaced | Internal Service Function (`ResearchManager` NLI) | **PASS** | **FUNCTIONALLY VERIFIED / BLACK-BOX EVIDENCE INCOMPLETE** |
| **BB-08** | Research evidence isolated across runs | Internal Service Function (`EvidenceLedger` DB) | **PASS** | **FUNCTIONALLY VERIFIED / BLACK-BOX EVIDENCE INCOMPLETE** |
| **BB-09** | Cross-worker artifact upload denied | In-Process ASGI HTTP (`POST /api/v1/worker/tasks/{id}/artifacts`) | **PASS** | **PASS (IN-PROCESS ASGI)** |
| **BB-10** | Invalid artifact checksum rejected | In-Process ASGI HTTP (`POST /api/v1/worker/tasks/{id}/artifacts`) | **PASS** | **PASS (IN-PROCESS ASGI)** |
| **BB-11** | Stale worker lease cannot overwrite | Internal Service Function (`worker_svc` transactions) | **PASS** | **FUNCTIONALLY VERIFIED / BLACK-BOX EVIDENCE INCOMPLETE** |
| **BB-14** | Admin mutation without CSRF denied | In-Process ASGI HTTP (`POST /api/v1/orchestration/projects`) | **PASS** | **PASS (IN-PROCESS ASGI)** |
| **BB-15** | Identically named artifacts distinct | Internal Service Function (`artifact_manager` / CAS) | **PASS** | **FUNCTIONALLY VERIFIED / BLACK-BOX EVIDENCE INCOMPLETE** |
| **BB-16** | Cross-project reuse preserves lineage | In-Process ASGI HTTP (`POST /deliverables/{id}/reuse`) | **PASS** | **PASS (IN-PROCESS ASGI)** |
| **BB-17** | Artifact promotion candidate-only | Internal Service Function (`knowledge_store` propose) | **PASS** | **FUNCTIONALLY VERIFIED / BLACK-BOX EVIDENCE INCOMPLETE** |
| **BB-22** | Contract API examples conform | In-Process ASGI HTTP (`GET /api/v1/*`) | **PASS** | **PASS (IN-PROCESS ASGI)** |
| **BB-23** | Full backup, restore & verification | Cross-Component Workflow (`backup.py` CLI engine) | **PASS** | **FUNCTIONALLY VERIFIED / BLACK-BOX EVIDENCE INCOMPLETE** |
| **BB-24** | Historical migrations additive | Internal Service Function (`migrations.apply_all`) | **PASS** | **FUNCTIONALLY VERIFIED / BLACK-BOX EVIDENCE INCOMPLETE** |
| **BB-26** | Prompt injection cannot bypass gates | In-Process ASGI HTTP (`POST /projects`) | **PASS** | **PASS (IN-PROCESS ASGI)** |
| **BB-27** | Malicious preview sandbox enforced | In-Process ASGI HTTP (`GET /artifacts/{id}/download`) | **PASS** | **PASS (IN-PROCESS ASGI)** |
| **BB-30** | Unreviewed vault injection gated | Internal Service Function (`knowledge_store` search) | **PASS** | **FUNCTIONALLY VERIFIED / BLACK-BOX EVIDENCE INCOMPLETE** |

### 2.3 Detailed Analysis of Key Scenarios
- **BB-05 (Citation audit)**: Exercised `CitationEngine.audit_report()` directly in memory. It verified citation extraction and audit logic, but did not exercise the `/api/v1/projects/{id}/orchestration` evaluation controller route.
- **BB-09 (Worker artifact upload authorization)**: Exercised the real HTTP ASGI boundary via `TestClient`. Unauthorized worker authentication headers were validated, resulting in an authentic HTTP 403 Forbidden response.
- **BB-16 (Cross-project artifact reuse)**: Exercised the real HTTP ASGI endpoint `POST /deliverables/{id}/reuse`, verifying zero-copy CAS link creation and producer project attribution across SQLite tables.
- **BB-23 (Complete backup and independent restoration)**: Exercised the comprehensive cross-component workflow via `app.services.backup`. Created an atomic SQLite snapshot, generated a SHA-256 verified `.tar.gz` bundle, simulated dry-run verification, and restored to an isolated directory with 100% integrity check pass.
- **BB-27 (Malicious artifact preview and script containment)**: Exercised the real HTTP ASGI route `GET /artifacts/{id}/download`, verifying `Content-Disposition: attachment` and restrictive `Content-Security-Policy` sandbox response headers.
- **BB-30 (Unreviewed knowledge governance)**: Exercised direct database insertion and knowledge retrieval methods, proving `[UNREVIEWED_CANDIDATE]` tags are enforced, but did not dispatch an automated planner run via HTTP.

---

## 3. Corrected Windows 10 Operating System & Security Support Assessment

A read-only host inspection was conducted using PowerShell and Windows Management Instrumentation:

### 3.1 Host Specification
- **Edition**: Microsoft Windows 10 Home (Reconciled from previous Windows 10 Pro classification)
- **Version & Build**: Version 22H2, Build `10.0.19045.7807` (UBR 7807, 64-bit AMD64)
- **Servicing Channel**: Retail Channel
- **Machine Profile**: Personal Development Workstation (`C:\Users\Matt C\`), hostname `LAPTOP-R84M7M49`
- **Installed Security Updates**:
  - `KB5126657` (Security Update, Installed: 10/16/2026)
  - `KB5127689` (Cumulative Update, Installed: 10/16/2026)
  - `KB5126256` (Security Update, Installed: 09/14/2026)
  - `KB5007401` (Servicing Stack Update)

### 3.2 Corrected Extended Security Updates (ESU) Timeline
- **Correction**: The Stage 9 Phase 1 report erroneously stated that consumer Windows 10 ESU coverage expired on October 13, 2026.
- **Official Status**: Microsoft's official lifecycle documentation specifies that eligible consumer installations receive security updates through **October 12, 2027**.
- **Current Security Status**: The host operating system received and installed active cumulative security updates on **October 16, 2026** and remains fully supported for development and verification purposes.

### 3.3 Production Controller Suitability
- **Development Workstation**: Fully supported for local engineering, automated testing, and development through October 12, 2027.
- **Production Controller Blocker**: Consumer editions (Windows 10 Home) lack mandatory enterprise server security controls (AppLocker, Credential Guard, enterprise telemetry audit logging, headless background service isolation). Production deployments must run on a dedicated enterprise-supported operating system (e.g., Ubuntu 24.04 LTS, Debian 12, or Windows Server / Windows 11 Enterprise).

---

## 4. Phase 2 Certification Execution Results

All 3 Phase 2 scenarios were executed using [`run_stage09_phase2_stress_and_degraded.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/run_stage09_phase2_stress_and_degraded.py).

```
=====================================================================
STAGE 9 PHASE 2 CERTIFICATION SUMMARY
=====================================================================
Total Scenarios Scheduled:   3
Scenarios Passed:            3 (100.0%)
Scenarios Failed:            0 (  0.0%)
Scenarios Blocked:           0 (  0.0%)
Total Execution Duration:    3,425 ms
Status:                      ALL SCENARIOS PASSED
=====================================================================
```

### 4.1 BB-18: Controller Concurrency and Progress Under Slow Work
- **Objective**: Verify that a deliberately slow controller task in Project A does not block independent scheduler ticks, does not starve ready tasks in Project B, maintains attempt fencing, and accurately updates operator diagnostics.
- **Test Actions**:
  1. Created Project A (with a 1.5s background controller step) and Project B (with a fast controller step).
  2. Dispatched scheduler tick 1 via `POST /api/v1/orchestrator/tick`.
  3. Tick 1 returned in **578.42 ms** (non-blocking, well below the 1.5s execution time).
  4. Project B's task completed in its background thread and reached `accepted` state in < 250 ms while Project A's task remained actively in-flight (`dispatched`).
  5. Dispatched scheduler tick 2 via `POST /api/v1/orchestrator/tick` (completed in **208.84 ms**).
  6. Awaited Project A completion; Task A durably committed to `accepted`.
  7. Attempted to commit a stale attempt (attempt 0) for Task A; rejected by fencing check with zero state corruption.
  8. Verified operator diagnostics via `GET /api/v1/projects/{id}/orchestration/status` for both projects (HTTP 200 OK with accurate run states).
- **Result**: **PASS** (Duration: 2,228.2 ms).

### 4.2 BB-20: Missing Telemetry Detection & Refusal to Fabricate Data
- **Objective**: Verify that tasks requiring measured telemetry are rejected or blocked when telemetry is missing, that synthetic or estimated text is rejected, and that genuine measured 0.0 is distinguished from missing (None).
- **Test Actions**:
  1. Inspected physical collector `collect_gpu_telemetry()`: returned `supported=True` with valid metrics, and confirmed unmeasured values evaluate strictly to `None` (never `0.0`).
  2. Evaluated deliverable requiring measured telemetry with no telemetry input: returned `decision="BLOCKED"` with summary `"requires measured telemetry, but no telemetry evidence is available."`
  3. Evaluated deliverable with synthetic model output containing estimated metrics ("Estimated CPU: 0%"): returned `decision="BLOCKED"`.
  4. Evaluated deliverable with genuine verified worker telemetry containing measured `cpu_percent: 0.0`: all telemetry checks passed with `decision="ACCEPTED"`.
- **Result**: **PASS** (Duration: 113.3 ms).

### 4.3 BB-25: External Dependency Failure & Offline Degraded Operation
- **Objective**: Verify graceful failure, transparent fallback, capability gap handling, and independent offline execution when external dependencies fail.
- **Test Actions**:
  1. Search timeout with NO fallback: simulated 5.0s timeout; query failed closed with 0 results fabricated; audit record logged `status="failed"`.
  2. Search timeout with authorized backup: primary failed, transparently recovered via `backup_mock` with 1 valid local result.
  3. Missing LLM capability: dispatched task requiring `nonexistent-cluster-model:70b`; engine detected capability gap, failed task with `failure_class="unsupported_capability"` (`attempts=1`), and transitioned run to `BLOCKED`.
  4. Independent offline task: in an independent project, executed local offline curation step; completed successfully to `accepted` without network dependencies.
- **Result**: **PASS** (Duration: 671.7 ms).

---

## 5. Actual Observed Fault-Recovery Behavior

1. **Non-Blocking Controller Threadpool**: Controller steps are dispatched asynchronously onto `_CONTROLLER_EXECUTOR`. Scheduler ticks never hold SQLite database write locks or wait for I/O completion.
2. **Cryptographic & Attempt Fencing**: Attempts to commit results from superseded plan revisions or stale task attempts are trapped by `_commit_controller_attempt()`:
   ```
   [WARNING] qc.orchestrator.engine: Fencing rejection: controller step ot-a-243e81 attempt 0 superseded (current attempt=1, state=accepted). Result discarded.
   ```
3. **Strict Telemetry Grounding**: The evaluator enforces a strict distinction between absent telemetry (`None`) and measured inactivity (`0.0`). Deliverables requiring hardware telemetry cannot be satisfied by natural-language assertions or heuristic estimates.
4. **Bounded Failure Without Spinloops**: Unsupported model capabilities fail permanently in a single attempt (`attempts=1`), transitioning the orchestration run into `BLOCKED` with an explicit reason rather than spinning in an unproductive retry loop.

---

## 6. Evidence Paths and Logs

- **Structured Phase 2 Results**: [`docs/remediation/evidence/stage09/bb_phase2_results.json`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/stage09/bb_phase2_results.json)
- **Phase 2 Test Harness**: [`run_stage09_phase2_stress_and_degraded.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/run_stage09_phase2_stress_and_degraded.py)
- **Updated Phase 1 Results**: [`docs/remediation/reports/STAGE_09_PHASE1_BLACKBOX_RESULTS.md`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/reports/STAGE_09_PHASE1_BLACKBOX_RESULTS.md)
- **Execution Log**: Disposable test directory `TEMP\qc_stage09_phase2_8zylc0b3` (automatically purged after verification).

---

## 7. Outstanding Phase 1 Evidence Gaps

The Phase 1 authenticity audit identified 13 scenarios evaluated at the internal service function boundary:
- **Scenarios**: BB-01, BB-02, BB-03, BB-05, BB-06, BB-07, BB-08, BB-11, BB-15, BB-17, BB-23, BB-24, BB-30.
- **Gap Description**: While business logic, invariant enforcement, and state persistence are verified, these tests do not exercise the browser UI or live external TCP sockets.
- **Remediation Plan**: During Phase 3 (Mesh Network) and Phase 4 (Packaging), these scenarios will be augmented with live loopback TCP sockets and browser UI previews where applicable.

---

## 8. Remaining Release Blockers

1. **Untrusted Code Execution Isolation (`SECURE_EXECUTION_UNAVAILABLE`)**:
   - `code_sandbox.py` strictly refuses untrusted code execution on host Windows without containerized or hypervisor-enforced isolation.
   - Preserved fail-closed as required.
2. **Production Host Operating System Requirement**:
   - Production controller deployments must use a dedicated enterprise OS (Ubuntu 24.04 LTS, Debian 12, or Windows Server / Windows 11 Enterprise).
   - Windows 10 Home is certified for local personal workstation development only through October 12, 2027.
3. **Phase 3 & Phase 4 Execution Pending**:
   - Live mesh networking, physical worker node registration, and SSRF loopback filtering (Phase 3).
   - End-to-end acceptance and packaging gates (Phase 4).

---

## 9. Exact Source Checkpoint

- **Git Branch**: `feature/unified-artifact-deliverables`
- **Application Code Changes**: **0 lines of production application code in `app/` were modified during Stage 9 Phase 1 or Phase 2**.
- **Working Tree Integrity**: Clean application code; only disposable test harness scripts and remediation reports were added.

---

## 10. GO / HOLD Recommendation for Phase 3

### Formal Recommendation: **HOLD FOR HUMAN REVIEW**

Stage 9 Phase 2 has passed all 3 stress and degraded-state scenarios (BB-18, BB-20, BB-25) with zero failures and zero blocked tasks.

**MANDATORY GOVERNANCE STOP**: In compliance with the authorization terms, execution is **STOPPED**. No Phase 3 (Mesh Network & Worker Nodes) or Phase 4 testing will be initiated without explicit operator sign-off.
