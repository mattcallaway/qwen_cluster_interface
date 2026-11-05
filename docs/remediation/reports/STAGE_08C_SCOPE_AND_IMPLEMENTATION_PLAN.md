# Stage 8C Scope Reconciliation & Bounded Implementation Plan

**Directive:** Authorized Bounded Stage 8C Preflight Only  
**Program:** Qwen Collective Architecture, Security & Governance Remediation  
**Date:** 2026-10-21  
**Active Branch:** `feature/unified-artifact-deliverables`  
**Git Checkpoint (HEAD):** `c98510b9a1569a113c762f083f5bba4395704531`  
**Current Governance State:**  
- **Stage 8A:** `ACCEPTED` (Point-in-time backup packaging, manifest verification, and isolated disaster recovery)  
- **Stage 8B:** `ACCEPTED` (Operational reliability, fault recovery, lease fencing, health monitoring, and API authorization)  
- **Stage 8C:** `PREFLIGHT COMPLETE — PENDING HUMAN AUTHORIZATION` (Zero implementation changes permitted during preflight)  
- **Stage 9:** `STRICTLY ON HOLD`  
- **Production Deployment:** `STRICTLY FORBIDDEN`  

---

## Executive Summary

Stage 8B has achieved final integration acceptance with **721 tests collected, 720 passed, 0 failed, 1 transparently skipped live-network test, and exit code 0**. All new and aliased API routes, operational health endpoints, lease fencing tokens, worker recovery paths, and CSRF/authorization protections have been verified against adversarial regression suites.

Before implementation of Stage 8C commences, this document reconciles the historical divergence in Stage 8 scope, formally defines the boundaries of Stage 8C, designs a telemetry-driven active-heartbeat infinite-loop detection engine, verifies the fail-closed status of unresolved security obligations (execution containment, browser egress, and transport limits), establishes performance benchmarking and runbook requirements, and provides an actionable, bounded implementation plan broken into 5 independently reviewable tasks.

---

## 1. Stage 8 Scope Reconciliation

### 1.1 Canonical Stage 8 Directive vs. Historical Workstream Divergence

The canonical master remediation directive (`Qwen_Collective_Antigravity_Master_Remediation_Prompt.md`) established:
> **Stage 8 — API Contracts, User Experience, Operations, Backups & Documentation**  
> *Target Findings:* F-16 (API Route Drift) and F-17 (Incomplete Backup Strategy & Missing Artifact Vault), alongside the operational documentation and security enforcement parts of F-12 (Execution Containment), F-14 (Browser Egress / SSRF), and F-15 (Administrative CSRF).

During remediation execution, Stage 8 was partitioned into sub-workstreams. A divergence emerged in reporting:
1. Early roadmaps characterized Stage 8C as *execution security and browser hardening*.
2. The Stage 8B final acceptance report described Stage 8C as *performance benchmarking, operational runbooks, and load testing*.

To maintain architectural integrity without discarding obligations or silently renaming stages, the work is reconciled below:

| Stage 8 Sub-Workstream | Canonical Focus | Requirements Completed | Remaining / Deferred Scope | Reconciled Home |
|---|---|---|---|:---:|
| **Stage 8A** | Backup, Disaster Recovery & Data Integrity (F-17) | • Atomic snapshot bundle (`sqlite3.backup()` + CAS blobs + Markdown vault + evidence)<br>• Versioned SHA-256 manifest (`manifest.json`)<br>• Fail-closed restore verification engine<br>• Isolated recovery dry-run mode<br>• Protection against unauthenticated production overwrites | Long-term backup retention pruning policies; multi-gigabyte CAS benchmark profiling. | **Stage 8A Complete** (Accepted) |
| **Stage 8B** | Operational Reliability & API Contract Hardening (F-16, F-15) | • Backward-compatible route aliases (`/api/v1/orchestrator/*`, `/snapshots/latest`, `/transfer`, `/promote`)<br>• Worker crash recovery & stale-lease 409 conflict fencing<br>• Controller restart rehydration & orphan lease reclamation<br>• Bounded retries terminating runaway worker loops<br>• Non-disruptive `/health` and `/api/v1/system/diagnostics`<br>• Endpoint authorization hardening (CSRF, token segregation, cross-project access checks) | In-memory verification cache eviction; detection of active-heartbeat unproductive agent loops. | **Stage 8B Complete** (Accepted) |
| **Stage 8C (Reconciled)** | Operational Hardening, Loop Telemetry, Security Verification & Runbooks | *Pending Implementation* | **1. Active-Heartbeat Infinite-Loop Detection:** Telemetry mechanism separating Cases A-E.<br>**2. Security Boundary Auditing & Verification:** Verify OS sandbox fail-closed guarantee (`SECURE_EXECUTION_UNAVAILABLE`); verify browser anti-SSRF/DNS-rebinding proxy.<br>**3. ASGI Transport Ingestion Cap:** 25MB upload limit enforced before in-memory buffering (Open Issue #6).<br>**4. Performance Benchmarks:** Latency, queue throughput, backup RTO/RPO, memory constraints.<br>**5. Operational Runbooks & Doc Alignment:** Truthful updates to `OPERATIONS_AND_RUNBOOK.md` and UI copy assertion fix (Open Issue #2). | **Stage 8C Core Scope** |
| **Stage 9** | Final Release Certification & Fleet Adversarial Gates | *Not Started (Strict Hold)* | • Full regression suite clean (0 failures)<br>• 30 black-box scenarios (BB-01..BB-30)<br>• Fleet-wide live worker validation across Tailscale<br>• Production staging container deployment validation | **Stage 9** |

### 1.2 Placement of Specific Architectural Concerns

1. **OS-Enforced Execution Isolation (F-12 / RSK-15):**  
   *Current Reality:* The controller runs on Windows 10 Pro. Subprocess execution with monkey-patching was evaluated and found incapable of providing genuine kernel-level containment. Dynamic untrusted code execution was disabled fail-closed (`is_os_enforced_sandbox_available() -> False`) returning `BLOCKED` with reason `SECURE_EXECUTION_UNAVAILABLE`.  
   *Stage 8C Role:* **Audit and verify that unsafe execution remains strictly BLOCKED**. Do not attempt to introduce unverified, fake container virtualization or unblock untrusted code execution on the Windows host. Formalize container sandbox architecture requirements for Stage 9 container deployment.
2. **Browser Anti-SSRF & DNS-Rebinding Verification (F-14 / RSK-02 / Open Issue #5):**  
   *Current Reality:* `OutboundFilteringProxy` with loopback binding, socket-level IP pinning, redirect revalidation, and fail-closed 503 fallback was implemented in Stage 1 and tested with 7 adversarial unit tests.  
   *Stage 8C Role:* **Verify browser egress controls against live network edge conditions and staging configurations**. Ensure all research providers and Playwright contexts route strictly through verified proxy configurations without bypass vectors.
3. **Performance Benchmarking, Load Testing & Runbooks (F-17 / OPERATIONS_AND_RUNBOOK.md):**  
   *Current Reality:* Runbooks are outdated (referencing direct file copies rather than Stage 8A backup tools; lacking Stage 8B recovery commands; missing triage for active-loop stalls).  
   *Stage 8C Role:* **Execute non-destructive performance benchmarks and draft comprehensive, truthful operational runbooks**.

---

## 2. Requirements Traceability Matrix (Outstanding Stage 8 Scope)

| Requirement ID | Canonical Source | Description | Current Status | Proposed Stage 8C Verification |
|---|---|---|:---:|---|
| **REQ-8C-01** | Prompt Section 2 / Incident Analysis | **Active-Heartbeat Infinite-Loop Detection:** Detect processes making repeated identical tool calls or failing to make progress despite recent heartbeats. | `UNIMPLEMENTED` (Stall monitor currently trusts active heartbeats) | Unit and integration test suite with simulated 50-repetition identical file read loop vs. progressing computation. |
| **REQ-8C-02** | F-12 / RSK-15 | **OS Execution Containment Fail-Closed Verification:** Untrusted code execution must remain blocked (`SECURE_EXECUTION_UNAVAILABLE`). | `ENFORCED_FAIL_CLOSED` | Negative security tests verifying no environment variable or API call can trigger uncontained execution. |
| **REQ-8C-03** | F-14 / RSK-02 / Open Issue #5 | **Browser Network Egress & Anti-SSRF Verification:** Verify `OutboundFilteringProxy` blocks loopback/LAN/metadata, validates redirects, pins DNS, and fails closed. | `PARTIALLY_FIXED` | Adversarial test suite exercising DNS rebinding, internal IP redirection, WebSocket drops, and proxy startup failure 503. |
| **REQ-8C-04** | Open Issue #6 | **ASGI Transport-Layer Upload Limit:** Enforce 25MB artifact limit at ASGI layer before in-memory buffering of JSON/base64 payloads. | `OPEN` | Test sending 30MB payload chunk stream; verify server returns HTTP 413 without allocating 30MB heap buffer. |
| **REQ-8C-05** | Open Issue #2 | **Knowledge Web Route Test Alignment:** Fix assertion mismatch between template header and test expectation in `test_web_routes.py`. | `OPEN` | Update test assertion to match authoritative UI copy; verify HTTP 200 and clean render. |
| **REQ-8C-06** | F-17 / Operations | **Performance & Resource Profiling:** Benchmark worker/controller load, queue throughput, task latency, health-check overhead, and backup size/speed. | `PLANNED` | Benchmark harness recording CPU/RAM, p50/p95 latency, health-check duration (<10ms), and backup creation/verify throughput. |
| **REQ-8C-07** | F-17 / Operations | **RTO and RPO Objective Definitions:** Formally specify and validate Recovery Time Objective (<60s) and Recovery Point Objective (<300s). | `PLANNED` | Empirical restore benchmark measuring exact wall-clock time from cold archive to verified SQLite + CAS state. |
| **REQ-8C-08** | Operations & Runbook | **Truthful Operator Runbooks:** Update `OPERATIONS_AND_RUNBOOK.md` with Stage 8A backup/restore CLI, Stage 8B recovery procedures, and stall triage trees. | `DRAFT_OUTDATED` | Operator documentation review ensuring zero commands fail or point to obsolete scripts. |

---

## 3. Progress & Infinite-Loop Detection Architecture

### 3.1 The Active-Heartbeat Problem Definition

The Stage 8B health monitoring routine (`app/services/operational_health.py:512-524`) evaluated task health using a naive rule:
```python
if duration_s > STALL_DETECTION_THRESHOLD_S:
    has_active_heartbeat = (hb_age_s is not None and hb_age_s <= WORKER_ONLINE_THRESHOLD_S)
    if has_active_heartbeat:
        logger.debug("Task duration exceeds threshold but has active heartbeat...")
    else:
        stalled_tasks.append(...)
```

**Flaw:** An agent or worker stuck in an infinite loop (e.g., repeatedly reading the same file, querying the same model prompt, or failing and retrying in a tight cycle) continues sending heartbeats every 30 seconds. The monitor misclassifies this runaway process as healthy long-running computation, masking resource consumption and stalling the cluster.

### 3.2 Five Distinct Operational Execution Classes

The Stage 8C detection engine differentiates five operational scenarios:

```
                                  +---------------------------------------+
                                  |    Task Duration > Threshold (600s)   |
                                  +---------------------------------------+
                                                      |
                         +----------------------------+----------------------------+
                         |                                                         |
                 [Heartbeat Stale / Dead]                                  [Heartbeat Fresh]
                         |                                                         |
                 +---------------+                                                 |
                 |    Case B     |                                                 |
                 |  Worker Crash |                                                 |
                 | Lost Heartbeat|                                                 |
                 +---------------+                                                 |
                                                                                   |
                         +---------------------------------+-----------------------+-----------------------+
                         |                                 |                                               |
             [Tool Fingerprints Identical]      [Task Attempt Count > 1]                    [Progress Signals Advancing]
             [Zero Output Delta Across N Ops]   [Repeated Failure Category]                                |
                         |                                 |                                               |
                 +---------------+                 +---------------+                       +---------------+---------------+
                 |    Case C     |                 |    Case D     |                       |    Case A     |    Case E     |
                 | Unproductive  |                 | Repeated Loop |                       | Long Compute  | Multi-Step    |
                 | Active Loop   |                 | Failures      |                       | Continuing    | Varied Ops    |
                 +---------------+                 +---------------+                       | Progress      | Progressing   |
                                                                                           +---------------+---------------+
```

1. **Case A: Healthy Long-Running Computation with Continuing Progress**  
   - *Characteristics:* High elapsed duration, active heartbeats, but telemetry demonstrates continuous measurable advancement: monotonic token emissions, intermediate stage completions, progress percentage updates, or shifting subtask identifiers.
   - *Verdict:* `HEALTHY_LONG_RUNNING` (No alert).
2. **Case B: Worker Crash or Lost Heartbeat**  
   - *Characteristics:* `heartbeat_age_seconds > WORKER_STALE_THRESHOLD_S` (300s) or current time exceeds `lease_expires_at`.
   - *Verdict:* `LEASE_EXPIRED_CRASH` (Flagged as stalled task; eligible for Stage 8B lease reconciliation).
3. **Case C: Active Process Repeatedly Making Identical Operations (The Incident Pattern)**  
   - *Characteristics:* Active heartbeats sent within threshold (`< 60s`), but worker executes identical tool calls or controller steps with identical inputs, yielding identical output hashes or zero state delta across sliding window $W \ge 5$.
   - *Verdict:* `UNPRODUCTIVE_TOOL_LOOP` (Diagnostic warning with loop fingerprint and repetition count).
4. **Case D: Repeated Failures and Retries Without Resolution**  
   - *Characteristics:* Task restarts across attempts (`attempt_count > 1`), failing repeatedly with identical error category or message, consuming worker leases without advancing past the failing graph node.
   - *Verdict:* `REPEATED_FAILURE_CYCLE` (Diagnostic warning; halts at `attempt_count >= max_retries`).
5. **Case E: Legitimate Repeated Operations with Measurable Delta**  
   - *Characteristics:* Multiple calls to the same tool (e.g., paginated API fetches, batch file processing, web crawling), but input arguments vary (differing URLs or page tokens) and output hashes change.
   - *Verdict:* `HEALTHY_PROGRESSIVE_ITERATION` (No alert).

### 3.3 Proposed Telemetry & Fingerprinting Mechanism

To detect Case C without heavy overhead, we introduce lightweight in-memory and database-backed telemetry structures:

1. **Tool-Call Fingerprinting:**
   $$\text{Fingerprint} = \text{SHA256}(\text{tool\_name} \parallel \text{canonical\_json}(\text{arguments}))[:16]$$
   - Canonical JSON sorts keys, strips volatile timestamps, and produces deterministic digests.
2. **Result State Hashing:**
   $$\text{ResultHash} = \text{SHA256}(\text{output\_payload} \parallel \text{state\_summary})[:16]$$
3. **Progress Telemetry Fields (Extending `worker_task_leases` and in-flight tracking):**
   - `last_progress_at TEXT`: Timestamp of the last operation that produced a *new* fingerprint or advanced state.
   - `progress_counter INTEGER`: Monotonically increasing counter incremented on valid state advancements.
   - `recent_ops_ring_buffer JSON`: Sliding window of the last $N=10$ operation fingerprints: `[{"op": fingerprint, "res": result_hash, "ts": iso_ts}]`.
4. **Detection Rule (Diagnostic Only):**
   - If `(now - last_progress_at) > STALL_DETECTION_THRESHOLD_S` (600s), **OR**
   - If the last $K=5$ entries in `recent_ops_ring_buffer` share the exact same `op` fingerprint and `res` hash:
     - Emit structured alert in `/api/v1/system/diagnostics`:
       ```json
       {
         "task_id": "task_abc123",
         "anomaly_type": "UNPRODUCTIVE_TOOL_LOOP",
         "tool_name": "view_file",
         "repeated_fingerprint": "a1b2c3d4e5f60718",
         "repetition_count": 50,
         "elapsed_stagnant_seconds": 612.4,
         "heartbeat_age_seconds": 12.1,
         "status": "warning",
         "action_taken": "none_diagnostic_only"
       }
       ```

### 3.4 Strict Non-Destructive Safeguard

In strict accordance with the directive:
- **Detection must initially be diagnostic only.**
- **NO automated killing of worker processes.**
- **NO automated deletion of tasks or runs.**
- **NO automated restarting of workers or controllers.**
- Anomalies are surfaced via:
  1. Diagnostic alerts in `GET /api/v1/system/diagnostics`.
  2. Non-blocking warning logs in controller service output.
  3. Audit event emitted into `orchestration_events` (`event_type: "unproductive_loop_detected"`).

### 3.5 Concrete Incident Test Scenario: Repeated File Read

To prevent recurrence of the historical incident where an agent performed thousands of repeated reads of the same source file:
- **Test File:** `tests/test_stage08c_infinite_loop_detection.py`
- **Scenario:**
  1. A worker acquires a lease for a code analysis task.
  2. The worker repeatedly calls `view_file(path="main.py", start=1, end=50)` 50 consecutive times.
  3. The worker sends legitimate task lease heartbeats every 10 seconds (confirming it is actively computing and alive).
  4. The test executes `get_operational_diagnostics()`.
  5. **Assertions:**
     - The task is flagged with anomaly `UNPRODUCTIVE_TOOL_LOOP`.
     - The report includes `repeated_fingerprint`, `repetition_count=50`, and identifies `view_file`.
     - The task status remains `running` (non-destructive; worker is NOT killed).
     - A companion control task executing 50 calls with differing file paths or page offsets is verified as `normal` (zero false positives).

---

## 4. Reconciliation of Security Obligations

### 4.1 OS-Enforced Sandbox Containment (F-12 / RSK-15)

- **Audit Findings:**
  - True kernel-enforced sandboxing (namespaces, cgroups, seccomp, gVisor, or chroot jails) is not natively provided by Windows Python subprocess execution.
  - In Stage 4, `QC_ENABLE_OS_ENFORCED_SANDBOX` was completely removed.
  - `is_os_enforced_sandbox_available()` returns `False` unconditionally on the host controller.
- **Fail-Closed Policy Confirmation:**
  - Dynamic execution of untrusted generated code must remain **BLOCKED**.
  - Any evaluation check requesting dynamic execution must fail with:
    `status="BLOCKED"`, `error_code="SECURE_EXECUTION_UNAVAILABLE"`.
  - Static AST parsing and syntax checking (`code_syntax_valid`) is permitted for syntax validation, provided deliverables state dynamic testing was skipped due to sandbox unavailability.
  - Under no circumstances will untrusted code execution be enabled during Stage 8C preflight or implementation.

### 4.2 Browser Network Egress & Anti-SSRF Protection (F-14 / RSK-02)

- **Audit Findings:**
  - `OutboundFilteringProxy` in `app/services/research/browser_proxy.py` intercepts browser HTTP and HTTPS CONNECT traffic on `127.0.0.1`.
  - Destination hostnames are resolved and checked against `ResearchSecurityPolicy`.
  - Upstream sockets connect directly to the verified IP address (`socket.create_connection((ip, port))`), preventing time-of-check to time-of-use (TOCTOU) DNS rebinding.
  - Redirection hops in HTTPX transport are re-validated on every hop.
  - WebSockets (`Upgrade: websocket`) and private IP ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `127.0.0.0/8`, `169.254.169.254`, `metadata.google.internal`) are blocked with HTTP 403 Forbidden.
- **Fail-Closed Policy Confirmation:**
  - If proxy binding fails or Playwright is missing, browser retrieval fails closed with HTTP 503 (`BrowserRenderResult(error="Browser automation unavailable...")`). Zero unproxied egress is permitted.

### 4.3 Credential and Data Isolation

- **Diagnostic Redaction:** `GET /api/v1/system/diagnostics` and `/api/v1/observatory/snapshots/latest` must never return raw passwords, secret keys, or authentication tokens.
- **Exclusive CAS Staging:** Artifact writes must continue using exclusive staging (`open(..., 'xb')`) with 64-hex SHA-256 validation to prevent CAS clobbering.
- **Worker Isolation:** Workers cannot lease, inspect, or upload deliverables to tasks assigned to other workers or across distinct project boundaries.

---

## 5. Performance Benchmarking & Operational Runbook Requirements

### 5.1 Performance Profiling Obligations

Stage 8C will implement a dedicated, non-destructive benchmark harness (`scripts/benchmark_stage08c.py` and `tests/test_stage08c_benchmarks.py`) measuring:

1. **Worker and Controller Load:**
   - CPU and memory consumption of the controller under idle conditions vs. active task scheduling.
   - Resource overhead of concurrent background controller steps (bounded by `_CONTROLLER_EXECUTOR = ThreadPoolExecutor(max_workers=4)`).
2. **Queue Throughput and Task Latency:**
   - Latency for task lease acquisition (`POST /api/v1/worker/tasks/lease`).
   - Task completion submission latency (`POST /api/v1/worker/tasks/{id}/submit`).
   - P50, P90, and P95 latency across 100 queued tasks.
3. **Health-Check Overhead:**
   - Wall-clock execution time of `get_operational_diagnostics()`.
   - Verification that in-memory backup verification caching keeps diagnostics execution time under **15 milliseconds** without disk I/O.
4. **Backup Size, Compression & Verification Throughput:**
   - Time required to generate atomic backup archive across varying repository scales (50MB, 250MB).
   - CAS blob deduplication ratio and zip compression ratio.
   - Verification speed of `verify_backup()` across cold disk reads.
5. **Recovery-Time Objective (RTO) & Recovery-Point Objective (RPO):**
   - **RTO Benchmark:** Target $< 60$ seconds to unpack, restore SQLite database via `sqlite3.Connection.backup()`, extract CAS artifacts, restore Knowledge Vault notes, and execute `PRAGMA integrity_check`.
   - **RPO Specification:** Target $< 300$ seconds (or point of last completed task lease commit), guaranteed by SQLite WAL mode and atomic CAS writes.

### 5.2 Operational Runbook Reconciliation

`OPERATIONS_AND_RUNBOOK.md` will be updated to replace obsolete procedures with verified Stage 8 capabilities:

1. **Disaster Recovery Section:**
   - Deprecate raw file copy scripts.
   - Provide exact operator commands for Stage 8A backup creation:
     `python -c "from app.services.backup import create_backup; print(create_backup())"`
   - Provide exact operator commands for dry-run verification:
     `python -c "from app.services.backup import restore_backup; print(restore_backup(..., dry_run=True))"`
2. **Incident Triage Trees:**
   - **Triage Tree 1 (Task Lease Expiry):** Handling abandoned tasks and worker network partitions.
   - **Triage Tree 2 (Unproductive Tool Loop):** Identifying Case C loop alerts in diagnostics, evaluating loop fingerprints, and canceling hung runs via UI.
   - **Triage Tree 3 (Database Lock & WAL Truncation):** Resolving `database is locked` via safe WAL checkpoints.
3. **Degraded Operation Runbook:**
   - Operating cluster when model inference nodes are offline.
   - Operating cluster when internet access is unavailable (offline research fallback).

---

## 6. Bounded Implementation Work Breakdown (Tasks 8C.1 – 8C.5)

To ensure reviewability and safe execution, the remaining Stage 8 work is broken into five discrete, self-contained tasks:

```
[Task 8C.1: Infinite Loop Detector] 
        │
[Task 8C.2: Browser Security Verification]
        │
[Task 8C.3: Transport Ingestion Capping]
        │
[Task 8C.4: Operational Benchmarking]
        │
[Task 8C.5: Runbooks & Copy Reconciliation]
```

---

### Task 8C.1: Telemetry-Based Active-Heartbeat Infinite Loop & Stall Detector

- **Objective:** Extend `app/services/operational_health.py` and task execution tracking to differentiate Cases A, B, C, D, and E. Detect processes making repeated identical tool calls or failing to make progress despite active heartbeats. Diagnostic only.
- **Files Affected:**
  - `app/services/operational_health.py`
  - `app/routes/worker.py` (optional telemetry payload fields in task heartbeat/progress)
  - `tests/test_stage08c_infinite_loop_detection.py` (new test suite)
- **Dependencies:** Stage 8B operational health foundations.
- **Required Safety Controls:**
  - Strictly read-only and diagnostic; no automated worker kills, task cancellations, or node restarts.
  - Zero regression on existing 18 operational reliability tests.
- **Acceptance Tests:**
  - Test simulated 50-repetition identical file read loop flags `UNPRODUCTIVE_TOOL_LOOP` with exact repetition count and fingerprint.
  - Test healthy long-running task with advancing progress counters is NOT flagged.
  - Test expired lease without heartbeat flags `LEASE_EXPIRED_CRASH`.
  - Test repeated failure cycles across retries flags `REPEATED_FAILURE_CYCLE`.
  - Test legitimate multi-step paginated operations with changing arguments are NOT flagged.
- **Expected Deliverables:** Hardened `_detect_stalls_and_unproductive_loops` implementation and dedicated test suite.
- **Stop Conditions:** All 5 operational cases pass unit tests; diagnostics payload matches specification; stop for review.

---

### Task 8C.2: Browser Egress, Anti-SSRF & DNS-Rebinding Hardening & Verification Gate

- **Objective:** Validate that browser research network isolation strictly enforces anti-SSRF, DNS-pinning, redirect revalidation, and fail-closed 503 behavior across all invocation paths.
- **Files Affected:**
  - `app/services/research/browser_proxy.py`
  - `app/services/research/browser.py`
  - `tests/test_stage08c_browser_security.py` (new test suite)
- **Dependencies:** Stage 1 proxy infrastructure (`OutboundFilteringProxy`).
- **Required Safety Controls:**
  - Zero unproxied outbound traffic permitted.
  - Fail-closed fallback: if proxy fails to bind or start, browser calls abort immediately with HTTP 503.
- **Acceptance Tests:**
  - Test connection to `127.0.0.1`, RFC1918, and `169.254.169.254` blocked with HTTP 403 `X-Blocked-By: OutboundFilteringProxy`.
  - Test DNS rebinding simulation (hostname resolving first to public IP, then to private IP) is intercepted and rejected.
  - Test multi-hop redirect to private IP is aborted on the second hop.
  - Test proxy failure results in structured `BrowserRenderResult(error=...)` with zero network egress.
- **Expected Deliverables:** Verified browser security test suite and documentation of staging gate requirements.
- **Stop Conditions:** 100% pass on browser security tests; stop for review.

---

### Task 8C.3: ASGI Transport-Layer Upload Size Enforcement & Ingestion Protection

- **Objective:** Enforce the 25MB artifact upload limit at the ASGI transport layer on `POST /api/v1/worker/tasks/{id}/artifacts` before in-memory buffering of JSON and base64 payloads (resolving Open Issue #6).
- **Files Affected:**
  - `app/routes/worker.py` (or custom ASGI middleware in `app/main.py`)
  - `tests/test_stage08c_transport_security.py` (new test suite)
- **Dependencies:** None.
- **Required Safety Controls:**
  - Abort oversized requests immediately with HTTP 413 Payload Too Large.
  - Ensure streaming termination closes connection cleanly without memory leakage.
- **Acceptance Tests:**
  - Request with `Content-Length > 26214400` rejected immediately with HTTP 413.
  - Chunked streaming upload exceeding 25MB aborted mid-stream with HTTP 413 without loading entire payload into RAM.
  - Legitimate uploads (< 25MB) succeed normally.
- **Expected Deliverables:** ASGI transport guard and negative boundary tests.
- **Stop Conditions:** Transport-layer tests passing; stop for review.

---

### Task 8C.4: Operational Performance Benchmarks (Queue, Latency, RTO/RPO & Memory)

- **Objective:** Implement an automated benchmark harness measuring cluster throughput, task latency, health-check overhead, backup compression/verification speed, and RTO/RPO metrics.
- **Files Affected:**
  - `scripts/benchmark_stage08c.py` (benchmark harness script)
  - `docs/remediation/evidence/stage08c_benchmark_results.json` (benchmark output artifact)
  - `tests/test_stage08c_benchmarks.py` (automated threshold assertions)
- **Dependencies:** Stage 8A backup tools, Stage 8B health services.
- **Required Safety Controls:**
  - Benchmarks must execute in isolated temporary environments using disposable test databases.
  - Never execute load tests against live production databases or active worker nodes.
- **Acceptance Tests:**
  - Health check diagnostic execution time $< 15\text{ ms}$ on cached backups.
  - Task lease & submit latency $< 50\text{ ms}$ per operation under local concurrency.
  - Atomic backup restoration verified within RTO target ($< 60\text{ seconds}$).
  - Benchmark script outputs reproducible JSON metrics report.
- **Expected Deliverables:** Benchmark script, test suite, and evidence JSON file.
- **Stop Conditions:** Benchmark assertions pass within budgeted thresholds; stop for review.

---

### Task 8C.5: Truthful Operations Runbook Reconciliation & Knowledge Web Route Fix

- **Objective:** Update `OPERATIONS_AND_RUNBOOK.md` with complete, verified procedures for Stage 8A backups, Stage 8B recovery, and Stage 8C loop triage. Reconcile the template assertion mismatch in `test_web_routes.py` (resolving Open Issue #2).
- **Files Affected:**
  - `OPERATIONS_AND_RUNBOOK.md`
  - `tests/test_web_routes.py`
  - `docs/remediation/OPEN_ISSUES.md`
  - `docs/remediation/STATUS.md`
- **Dependencies:** Tasks 8C.1 through 8C.4.
- **Required Safety Controls:**
  - All runbook commands must be verified against actual code signatures.
  - No speculative or unverified commands documented.
- **Acceptance Tests:**
  - `test_knowledge_page` passes cleanly.
  - Runbook verification passes static review.
  - Open Issues #2 and #6 marked resolved in tracking documents.
- **Expected Deliverables:** Truthful, updated runbook; clean passing web route tests.
- **Stop Conditions:** Full regression suite passes with 0 failures; stop for Stage 8C final report.

---

## 7. Strict Governance Limits & Immediate Stop

In accordance with explicit instructions:
1. **Preflight Boundary:** This preflight is strictly read-only analysis. No implementation code or tests have been created or modified during this turn.
2. **Zero Autonomous Transitions:** No implementation of Tasks 8C.1 through 8C.5 will begin without separate, explicit human authorization.
3. **Stage 9 Status:** Stage 9 remains strictly on `HOLD`.
4. **Production Deployment:** Production deployment remains strictly `FORBIDDEN`.

**GOVERNANCE STOP:** Preflight completed. Awaiting human review and explicit authorization to proceed with Task 8C.1.
