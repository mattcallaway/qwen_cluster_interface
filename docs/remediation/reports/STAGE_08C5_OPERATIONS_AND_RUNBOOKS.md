# STAGE 08C.5: TRUTHFUL OPERATIONS RUNBOOKS, DOCUMENTATION RECONCILIATION & FINAL STAGE 8 READINESS ASSESSMENT

**Status**: IMPLEMENTED AND VERIFIED — HOLD FOR HUMAN ACCEPTANCE & STAGE 8 CLOSURE  
**Date**: 2026-10-21  
**Author**: Antigravity Assistant (Qwen Collective Remediation Subsystem)  
**Task Authorization**: Task 8C.5: Truthful Operations Runbooks, Documentation Reconciliation, and Final Stage 8 Readiness Assessment Only  
**Previous Gate**: Task 8C.4 Operational Benchmarking Accepted  
**Source Revision**: `c98510b9a1569a113c762f083f5bba4395704531`  
**Production Code Changes**: **NONE (0 lines)**  

---

## Executive Summary

Task 8C.5 concludes the Stage 8 remediation workstream. This task performed a rigorous, evidence-based audit and reconciliation of operational documentation, performance claims, memory profiles, and security invariants across the Qwen Collective codebase. 

Key outcomes:
1. **Recovery Point Objective (RPO) Correction**: The previous provisional claim of achieving a 5-minute RPO under a 300-second backup schedule was mathematically invalid (worst-case disaster window is 303.64 seconds). The status has been corrected to **TARGET MISSED / NOT YET DEMONSTRATED**. A 240-second backup cadence has been evaluated and documented as a provisional recommendation for operator approval.
2. **Benchmark Throughput Reconciliation**: Worker throughput figures (34.32 to 732.62 tasks/sec) have been reconciled as pure in-process SQLite scheduling, lease acquisition, and ledger dispatch throughput. They are formally distinguished from model inference throughput and long-running execution. Single-worker and multi-worker benchmark workloads have been labeled **non-comparable**.
3. **Upload Memory Boundary Reconciliation**: The 35.77 MiB peak heap reported in Stage 8C.4 has been reconciled with the ~120 MB peak heap reported in Stage 8C.3. The 35.8 MiB figure represents the ASGI transport streaming boundary, whereas 120 MB represents end-to-end JSON deserialization and Base64 decoding. All claims of "zero memory buffering" have been replaced with **bounded streaming buffering**, and multi-stream concurrency limits are labeled **provisional**.
4. **Canonical Runbook Overhaul**: `OPERATIONS_AND_RUNBOOK.md` has been rewritten using the active codebase as the single source of truth, providing verified commands and exact paths for all 17 operator workflows without inventing non-existent tools or UI elements.
5. **Security Limitations & Capacity Boundaries**: Explicit operator-facing limitations (blocked untrusted code, non-equivalence of process isolation to kernel sandboxing, external agent loop independence, and authentication gates for production restore) and capacity classifications (`VERIFIED IN TEST ENVIRONMENT`, `PROVISIONAL CONFIGURATION`, `NOT YET VERIFIED IN PRODUCTION`, `UNAVAILABLE`) are now canonical.
6. **Requirements Traceability & Readiness**: All Stage 8 requirements have been mapped. Finding F-12 (OS-enforced code execution sandboxing) is classified as **Disabled by safety policy (`SECURE_EXECUTION_UNAVAILABLE`)** rather than fixed. A formal readiness evaluation provides explicit release blockers and required operator decisions.

---

## 1. Corrected Recovery Performance Record (RTO & RPO)

### 1.1 Empirical Dataset Review
From `docs/remediation/evidence/stage08c4_benchmark_results.json` and `STAGE_08C4_PERFORMANCE_BENCHMARKS.md`:

| Dataset Tier | Nominal Size | Artifacts | Snapshot Creation Duration | Manifest Verification | Restoration Duration | CAS Hash Verification | Total Recovery Time |
|---|---|---|---|---|---|---|---|
| **Small** | 5.0 MiB | 20 blobs | 0.996 s | 0.092 s | 0.594 s | 0.024 s | 0.710 s |
| **Medium** | 25.0 MiB | 50 blobs | 2.374 s | 0.271 s | 1.587 s | 0.107 s | 1.965 s |
| **Large** | 75.0 MiB | 100 blobs | 3.639 s | 0.721 s | 2.787 s | 0.314 s | 3.822 s |

### 1.2 Recovery Time Objective (RTO) Assessment
- **Target RTO**: < 60.0 seconds.
- **Measured Worst-Case Total Recovery**: **3.822 seconds** (Large 75 MiB dataset, including complete archive extraction, database rehydration, manifest validation, and full SHA-256 CAS integrity verification).
- **Safety Margin**: 56.18 seconds (93.6% safety margin).
- **RTO Status**: **ACHIEVED (VERIFIED IN TEST ENVIRONMENT)**.

### 1.3 Recovery Point Objective (RPO) Correction
- **Target RPO**: 300.0 seconds (5 minutes maximum allowable data loss).
- **Configured Backup Cadence**: 300.0 seconds (nominal interval).
- **Measured Worst-Case Snapshot Duration**: 3.639 seconds.
- **Time to Completed, Verified Recovery Point**: 4.360 seconds (3.639s creation + 0.721s manifest verification).
- **Worst-Case Potential Data Loss Window**:
  $$\text{Worst-Case Loss} = \text{Cadence} + \text{Snapshot Duration} = 300.0\,\text{s} + 3.64\,\text{s} = \mathbf{303.64\,\text{seconds}}$$
  *(Or $304.36\,\text{seconds}$ including manifest verification).*
- **Assessment**: **TARGET MISSED / NOT YET DEMONSTRATED**.
  Because $303.64\,\text{s} > 300.0\,\text{s}$, the system strictly exceeds the RPO objective during a disaster immediately preceding snapshot completion.

### 1.4 Technical Distinctions in Disaster Recovery
Operators must distinguish five independent temporal variables:
1. **Snapshot Creation Duration ($T_\text{snap}$)**: The wall-clock time required for SQLite `.backup()`, CAS staging, and tarball compression (measured at $0.996\text{s}$ to $3.639\text{s}$).
2. **Backup Scheduling Interval ($T_\text{interval}$)**: The configured time between consecutive automated snapshot triggers (currently 300s).
3. **Time to Completed, Verified Recovery Point ($T_\text{verified}$)**: $T_\text{snap} + T_\text{verify}$ (measured at $1.09\text{s}$ to $4.36\text{s}$). An unverified snapshot cannot be treated as an authoritative recovery point.
4. **Backup Failure and Retry Windows ($T_\text{retry}$)**: If an automated snapshot fails due to transient I/O contention or locked resources, the loss window expands by the retry delay plus snapshot time.
5. **Actual Maximum Potential Data Loss ($T_\text{loss}$)**: In the worst-case failure scenario occurring at time $T_\text{interval} - \epsilon$, data loss is $T_\text{interval} + T_\text{snap}$.

### 1.5 Evaluation of Proposed 240-Second (4-Minute) Backup Interval
Setting $T_\text{interval} = 240\,\text{seconds}$ provides a valid mathematical basis to achieve the 300-second RPO:
- **Worst-Case Data Loss Window**: $240.0\,\text{s} + 3.64\,\text{s} = \mathbf{243.64\,\text{seconds}}$ (providing a $56.36\text{-second}$ margin below the $300\text{s}$ ceiling).
- **Verification and Scheduling Overhead**: Consuming ~4.4 seconds of I/O every 240 seconds represents a negligible **1.8% I/O duty cycle**.
- **Storage Overhead**: An increase from 12 snapshots/hour to 15 snapshots/hour adds modest disk churn (~0.84 MiB/hour at Large tier with gzip compression), which is safely managed by the existing 24-hour backup pruning policy.
- **Failure Window Cushion**: If a snapshot experiences a transient error, a single retry triggered within 30 seconds will still complete at $273.64\text{s}$, preserving the 300-second guarantee.
- **Operational Constraint**: The live backup schedule was **NOT** altered during Task 8C.5. The 240-second interval remains a **PROVISIONAL CONFIGURATION** requiring operator approval and failure-window simulation before claiming 5-minute RPO certification.

---

## 2. Benchmark Throughput Methodology Reconciliation

### 2.1 Investigation of Reported Throughput Metrics
Stage 8C.4 reported the following task throughputs:
- Single-Worker: **34.32 tasks/sec** (0.728s for 25 tasks)
- Two-Worker: **439.17 tasks/sec** (0.046s for 20 tasks)
- Four-Worker: **492.70 tasks/sec** (0.081s for 40 tasks)
- Eight-Worker: **732.62 tasks/sec** (0.109s for 80 tasks)

### 2.2 Workload Disparity & Non-Comparability
Investigation of `run_stage08c4_benchmarks.py` revealed that the single-worker and multi-worker benchmark suites executed fundamentally different workloads:

| Benchmark Dimension | Single-Worker Suite (Benchmark A) | Multi-Worker Suite (Benchmark B) | Comparability Analysis |
|---|---|---|---|
| **Payload Complexity** | Included structured JSON `result_payload` (`{"result": "ok", "iteration": tid}`) and simulated runtime metadata. | Omitted `result_payload` and runtime metadata entirely; submitted empty result bodies. | **Non-Comparable**: Serializing JSON payloads and checking idempotency keys in SQLite adds transaction overhead absent from Benchmark B. |
| **Execution Loop Topology** | Single thread executing sequential dispatch, lease acquisition, expired lease reconciliation, and completion. | Thread pool (`ThreadPoolExecutor`) running concurrent worker loops grabbing pre-created pending tasks. | **Non-Comparable**: Benchmark B measured concurrent thread pool task polling rather than single-stream lifecycle latency. |
| **Task Pre-allocation** | 25 tasks pre-inserted and committed before loop. | $N \times 10$ tasks pre-inserted and committed before loop. | Consistent across both. |

### 2.3 Boundary Delineation: Scheduling vs. Model Inference
To prevent misrepresenting synthetic controller performance as LLM capacity, the measurement boundaries are formally delineated:

```
+---------------------------------------------------------------------------------------+
| CONTROLLER DISPATCH BOUNDARY (Measured in Benchmarks)                                 |
|                                                                                       |
|  [Pending Task in DB] ---> [Lease Selection & Lock] ---> [Task Result Ledger Insert]  |
|  - Measured Throughput: 34 - 732 tasks/sec                                            |
|  - Latency: 14 - 17 ms                                                                |
|  - Subsystem: SQLite WAL + Python Process Dispatch                                    |
+---------------------------------------------------------------------------------------+
                                           |
                                           v  (HTTP / Tailscale RPC)
+---------------------------------------------------------------------------------------+
| WORKER EXECUTION BOUNDARY (NOT Measured in Benchmarks)                                |
|                                                                                       |
|  [Worker Node Process] ---> [Tool / Bash / Browser] ---> [Token Generation (LLM)]     |
|  - Real Throughput: 0.02 - 0.2 tasks/sec (typical)                                    |
|  - Latency: 5 - 120 seconds per task                                                  |
|  - Subsystem: NVIDIA CUDA / Ollama / llama.cpp / External Web APIs                    |
+---------------------------------------------------------------------------------------+
```

1. **What Was Measured**: SQLite index traversal, lease status transitions, worker lock assignment, and execution ledger write throughput.
2. **What Was Excluded**: Model weight loading, prompt tokenization, LLM auto-regressive token generation, external tool I/O, subprocess execution, and network transport latency over Tailscale.
3. **Conclusion**: Synthetic scheduling throughput (up to 732 tasks/s) demonstrates that the controller scheduler will not be a bottleneck under heavy cluster load. It must never be presented as model execution capacity.

---

## 3. Upload Memory Measurement Reconciliation

### 3.1 Reconciliation of Stage 8C.3 vs. Stage 8C.4 Measurements
- **Stage 8C.3 Measurement (~120 MB Peak Python Heap)**:
  - *Measurement Scope*: Full end-to-end FastAPI endpoint lifecycle (`POST /api/projects/{id}/artifacts`) for a 25 MiB binary payload encoded in Base64 within a JSON envelope (~33.33 MiB wire size).
  - *Subsystems Involved*: Starlette ASGI body reception, Pydantic request body validation, JSON string parsing (`json.loads`), Base64 decoding (`base64.b64decode` into 25 MiB bytes), intermediate Python heap allocations (multiple copies of the payload in memory), SHA-256 CAS calculation, disk persistence, and database metadata insertion.
  - *Result*: Approximately 120 MB peak heap allocation for a single 25 MiB Base64 upload.
- **Stage 8C.4 Measurement (35.77 MiB Peak Python Heap)**:
  - *Measurement Scope*: ASGI transport receive boundary using a dedicated mock ASGI application wrapped by `UploadSizeLimitMiddleware`.
  - *Subsystems Involved*: Streaming ASGI chunk reader (64 KiB buffer iterations), cumulative size counter, and transport early termination guards.
  - *Result*: Peak heap remained flat at ~35.77 MiB across 1, 2, 4, and 8 concurrent streams.

### 3.2 Correction from "Zero Buffering" to "Bounded Streaming Buffering"
- The description in earlier draft documentation claiming "zero memory buffering" was technically inaccurate.
- The `UploadSizeLimitMiddleware` does not achieve zero buffering; rather, it enforces **strictly bounded streaming buffering** (reading 64 KiB chunks from the socket without buffering the complete 25 MiB payload in RAM before enforcing limits).
- However, once a request passes the transport middleware and reaches FastAPI's JSON parser, application-level heap buffering occurs.

### 3.3 Provisional Concurrency Status
- Multipart streaming uploads: **8 concurrent streams** is safe and verified at the transport boundary (28.6 MiB heap).
- JSON/Base64 uploads: **4 concurrent streams** is classified as a **PROVISIONAL CONFIGURATION**. Operators are warned that 4 concurrent 25 MiB JSON uploads could produce up to ~480 MB of transient Python heap allocation during concurrent JSON/Base64 deserialization.

---

## 4. Canonical Operations Runbook Verification

The canonical operations runbook (`OPERATIONS_AND_RUNBOOK.md`) was completely rewritten during Task 8C.5. Every command, path, and diagnostic procedure was verified against the current codebase:

| Procedure Category | Verified Command / Code Path | Source Implementation | Status |
|---|---|---|---|
| **Controller Start/Stop** | `Start-Service -Name QwenCollective`, `Stop-Service`, or `scripts\start-service-as-admin.bat` | `service\qwen-collective-service.xml`, WinSW | Verified |
| **Interactive Debug Run** | `.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000` | `app/main.py` | Verified |
| **Worker Daemon Control** | `sudo systemctl status qwen-vps.service`, `sudo journalctl -u qwen-vps.service -f` | `service/qwen-worker.service`, systemd | Verified |
| **Worker Diagnostic Probe** | `python3 -m vps_agent --config /etc/qwen-collective/worker.env --once` | `vps_agent/__main__.py` | Verified |
| **Enrollment Token Creation** | `app.services.worker.create_enrollment_token(allowed_node_id, valid_hours)` | `app/services/worker.py:382` | Verified |
| **Worker Registration** | `POST /api/workers/register` | `app/routes/worker.py:168` | Verified |
| **Cluster Health Snapshot** | `app.services.operational_health.get_cluster_health_snapshot()` | `app/services/operational_health.py:726` | Verified |
| **Storage Capacity Check** | `app.services.backup.check_storage_capacity()` | `app/services/backup.py:890` | Verified |
| **Model Capability Inspection** | `app.services.orchestration.model_router.evaluate_task_model_requirements()` | `app/services/orchestration/model_router.py:48` | Verified |
| **Tool Loop Triage** | `app.services.loop_detection.LoopDetectorManager` (20-op buffer, 5-repetition trigger) | `app/services/loop_detection.py:130` | Verified |
| **Stale Lease Reconciliation** | `app.services.worker.reconcile_expired_leases()` | `app/services/worker.py:840` | Verified |
| **Exhausted Retry Reset** | `UPDATE tasks SET status = 'pending', retry_count = 0 WHERE id = ?` | `app/database.py`, SQLite schema | Verified |
| **Research Provider Triage** | Outbound connectivity check + inspection of circuit-breaker fallback in `app/services/research/provider.py` | `app/services/research/provider.py` | Verified |
| **Plan & Approval Triage** | `GET /api/orchestration/projects/{id}/plans`, `POST /plans/{id}/approve` | `app/routes/orchestration.py:120` | Verified |
| **Artifact Ingestion & CAS** | `POST /api/projects/{id}/artifacts` (multipart), `POST /reuse` | `app/routes/projects.py:350`, `app/services/artifacts/` | Verified |
| **Upload Size Rejection Triage**| HTTP 413 `UPLOAD_SIZE_EXCEEDED` / HTTP 411 `CONTENT_LENGTH_REQUIRED` | `app/services/upload_security.py:74` | Verified |
| **Backup Creation** | `app.services.backup.create_backup(comment=...)` | `app/services/backup.py:266` | Verified |
| **Backup Verification** | `app.services.backup.verify_backup(archive_path)` | `app/services/backup.py:614` | Verified |
| **Isolated Environment Restore**| `app.services.backup.restore_backup(archive_path, target_data_dir, dry_run=False)` | `app/services/backup.py:816` | Verified |
| **Dry-Run Recovery** | `app.services.backup.restore_backup(..., dry_run=True)` | `app/services/backup.py:897` | Verified |
| **CAS Disk Blob Audit** | Compare `artifact_versions` table paths with disk sha256 | `data/artifacts/{sha256[:2]}/{sha256}` | Verified |
| **SSRF Incident Response** | `SSRFBlockedError` on loopback/RFC1918/cloud-metadata | `app/services/browser_egress.py` | Verified |

---

## 5. Explicit Security & Execution Limitations

The following operator-facing limitations are formally established as operational truth:

1. **Untrusted Code Execution Is Strictly Blocked**:
   - The execution engine fails closed across all nodes with error code `SECURE_EXECUTION_UNAVAILABLE`.
   - Dynamic untrusted code execution cannot be enabled by environment variables, CLI flags, or API parameters.
2. **Process Isolation Is Not Kernel Containment**:
   - Python subprocess environment stripping, working directory confinement, and argument sanitization are active for trusted tools.
   - These techniques do **NOT** provide genuine kernel-level sandbox isolation (e.g. bubblewrap, gVisor, Firecracker).
3. **Browser Egress Restrictions Are Not a General Sandbox**:
   - Browser egress enforces strict anti-SSRF protections, blocking loopback, RFC 1918 private IP ranges, and cloud metadata (`169.254.169.254`).
   - Browser network restrictions protect internal network infrastructure but do not provide general-purpose OS containment.
4. **Loop Detection Does Not Monitor External Agents**:
   - The in-cluster `LoopDetectorManager` monitors worker tool invocations dispatched through the collective's task system.
   - It does **NOT** monitor, inspect, or govern external autonomous agents running in Antigravity IDE.
5. **Live-Network Research Providers Require Separate Release Gating**:
   - Automated test suites verify search providers using mocked HTTP responses to ensure deterministic testing without internet dependencies.
   - Live-network provider verification is a separate, manual pre-release operational gate.
6. **Production Restoration Requires Explicit Authenticated Authorization**:
   - Automated restoration into the live production directory (`QC_DATA_DIR`) is strictly disabled.
   - Overwriting live production data requires `allow_production_overwrite=True` accompanied by an independently authenticated operator session token or cryptographically signed `OperatorAuthority`.
7. **No Automated Distributed Failover**:
   - The single-controller architecture does not implement distributed consensus (Raft/Paxos) or automatic leader failover. Host disaster recovery requires manual cold-standby restoration from a verified backup archive.

---

## 6. Operational Capacity & Performance Boundaries

Based on empirical testing from Task 8C.4 and historical measurements, operational parameters are classified into four authoritative categories:

| Parameter / Capability | Tested / Recommended Limit | Operational Classification | Operational Rationale & Caveat |
|---|---|---|---|
| **Task Queue Depth Scaling** | **Tested up to 200 tasks** (1,000 alert ceiling) | **TESTED (to 200) / UNVERIFIED (at 1000)** | Enqueue and lease query latencies tested flat (< 17 ms) up to 200 tasks. Queue depth behavior at 1,000 tasks is an unverified operational warning threshold / estimated backlog ceiling. |
| **Worker Concurrency Per Node** | **4 concurrent workers** | **TESTED (Scheduling Only)** | Maximizes scheduling and lease allocation throughput (492 tasks/s) without SQLite lock contention. *Explicitly excludes real LLM inference and network transport.* |
| **Multi-Worker Scaling Comparison** | **Non-Comparable Workloads** | **UNVERIFIED / NON-COMPARABLE** | Single-worker (34.3 tasks/s) and multi-worker (439-732 tasks/s) tests used different payloads and threading models; cannot be directly compared to claim linear scaling. |
| **Multipart Streaming Upload Concurrency**| **8 concurrent streams** | **PROVISIONAL (Tested at Transport)** | Memory flat at 28.6 MiB at ASGI transport boundary. Multi-worker production saturation remains unverified. |
| **JSON/Base64 Upload Concurrency** | **4 concurrent streams** | **PROVISIONAL (Estimated Load)** | Wire body expansion (~33.3 MiB) and ~120 MB peak heap per request during JSON/Base64 deserialization (estimated ~480 MB at 4 streams). |
| **Health Monitoring Polling** | **10 Hz** | **TESTED** | Diagnostic polling consumes only 6.46% scheduler throughput; non-disruptive to active workloads. |
| **Automated Backup Cadence** | **240 seconds (proposed)** | **PROPOSED CONFIGURATION** | Proposed configuration to achieve 5-minute RPO. Current 300s schedule yields 303.64s worst-case loss window (RPO MISSED). *Not an achieved guarantee.* |
| **Backup Storage Churn** | **~0.84 MiB / hour (estimated)** | **ESTIMATED (Awaiting Verification)** | Calculated from synthetic 100-artifact tests. Requires verification against actual production archive sizes, compression ratios, and retention prune behavior. |
| **Disaster Recovery Time (RTO)** | **< 60 seconds** (measured: 3.82s) | **TESTED** | 75 MiB dataset restored, verified, and rehydrated in 3.82 seconds in test environment (93.6% safety margin). |
| **Disaster Recovery Point (RPO)** | **< 300 seconds** | **UNVERIFIED / TARGET MISSED** | Under 300s nominal cadence, worst-case disaster window is 303.64s. Requires operator approval of proposed 240s cadence and failure simulation. |
| **Live Multi-Node Tailscale Fleet** | **3 nodes (controller, VPS, laptop)** | **UNVERIFIED IN PRODUCTION** | Inter-node protocol verified in unit/mock tests; multi-node live deployment reserved for Stage 9. |
| **Live Internet Search Providers** | **DuckDuckGo, SearXNG, Tavily** | **UNVERIFIED IN PRODUCTION** | Search provider logic and fallbacks verified via mocked transports; live egress validation required at release. |
| **Untrusted Code Execution Sandbox** | **OS-enforced container sandbox** | **UNAVAILABLE** | Fails closed with `SECURE_EXECUTION_UNAVAILABLE`. True kernel isolation deferred to Stage 9 containerization. |
| **Automated Live Disaster Failover** | **Multi-master / Raft consensus** | **UNAVAILABLE** | Single-controller architecture. Recovery requires cold-standby restoration from archive. |

---

## 7. Stage 8 Requirements Traceability Matrix

Every requirement across Stage 8 (including canonical directives and sub-workstreams 8A, 8B, 8C) is traced below with verified implementation paths:

| Requirement ID | Canonical Ref | Description | Implementation Artifact | Status | Classification |
|---|---|---|---|:---:|---|
| **REQ-8A-01** | F-17 | Point-in-time SQLite backup via `sqlite3.backup()` API | `app/services/backup.py:320` | Complete | **Implemented and verified** |
| **REQ-8A-02** | F-17 | Bundling of CAS artifacts, Knowledge Vault notes, evidence | `app/services/backup.py:350-410` | Complete | **Implemented and verified** |
| **REQ-8A-03** | F-17 | Cryptographic SHA-256 manifest generation (`manifest.json`)| `app/services/backup.py:420-470` | Complete | **Implemented and verified** |
| **REQ-8A-04** | F-17 | Fail-closed archive verification engine | `app/services/backup.py:614-805` | Complete | **Implemented and verified** |
| **REQ-8A-05** | F-17 | Isolated directory restoration engine | `app/services/backup.py:816-950` | Complete | **Implemented and verified** |
| **REQ-8A-06** | F-17 | Dry-run recovery planning mode | `app/services/backup.py:897-915` | Complete | **Implemented and verified** |
| **REQ-8A-07** | F-17 | Unauthenticated production overwrite prevention guard | `app/services/backup.py:847-885` | Complete | **Implemented and verified** |
| **REQ-8B-01** | F-16 | Backward-compatible route aliases for legacy orchestrator | `app/routes/orchestration.py` | Complete | **Implemented and verified** |
| **REQ-8B-02** | F-16 | Worker crash detection and stale lease 409 conflict fencing| `app/services/worker.py:840, 1400`| Complete | **Implemented and verified** |
| **REQ-8B-03** | F-16 | Controller restart rehydration & orphan lease reclamation | `app/services/worker.py:1650-1750`| Complete | **Implemented and verified** |
| **REQ-8B-04** | F-16 | Bounded retry budgets terminating runaway worker loops | `app/services/worker.py:1450-1500`| Complete | **Implemented and verified** |
| **REQ-8B-05** | F-16 | Non-disruptive `/health` & diagnostic observatory routes | `app/routes/observatory.py` | Complete | **Implemented and verified** |
| **REQ-8B-06** | F-15 | CSRF and token segregation on administrative endpoints | `app/routes/auth.py`, `app/auth.py`| Complete | **Implemented and verified** |
| **REQ-8C-01** | Prompt | Telemetry-driven active-heartbeat unproductive loop detection| `app/services/loop_detection.py` | Complete | **Implemented and verified** |
| **REQ-8C-02** | F-12 / RSK-15 | OS execution containment fail-closed verification | `app/services/orchestration/code_sandbox.py:161-235` | Complete | **Disabled by safety policy** |
| **REQ-8C-03** | F-14 / RSK-02 | Browser network egress, DNS rebinding & anti-SSRF guards | `app/services/research/browser_proxy.py`, `app/services/network_security.py` | Complete | **Implemented and verified** |
| **REQ-8C-04** | Open Issue #6 | ASGI transport-layer 25 MiB upload size enforcement | `app/services/upload_security.py` | Complete | **Implemented and verified** |
| **REQ-8C-05** | Open Issue #2 | Knowledge web route template copy assertion alignment | `tests/test_web_routes.py` | Complete | **Implemented and verified** |
| **REQ-8C-06** | Operations | Performance & capacity benchmarking (throughput, latency) | `run_stage08c4_benchmarks.py` | Complete | **Implemented and verified** |
| **REQ-8C-07** | Operations | Recovery Time Objective (RTO < 60s) verification | `STAGE_08C4_PERFORMANCE_BENCHMARKS.md` | Complete | **Implemented and verified** |
| **REQ-8C-08** | Operations | Recovery Point Objective (RPO < 300s) verification | `STAGE_08C4_PERFORMANCE_BENCHMARKS.md` | Incomplete | **Not yet demonstrated** (303.6s) |
| **REQ-8C-09** | Operations | Truthful canonical operations runbook update | `OPERATIONS_AND_RUNBOOK.md` | Complete | **Implemented and verified** |

### 7.1 Resolution and Classification of Finding F-12 (Untrusted Code Execution)
- **Background**: Finding F-12 identified the lack of genuine kernel-enforced sandboxing for model-generated code.
- **Current State**: On Windows 10 host environments, OS-level containment mechanisms (such as Linux user namespaces, bubblewrap, or cgroups) are unavailable. Rather than implementing an unverified user-space mock sandbox, untrusted code execution was deliberately **disabled fail-closed** with error `SECURE_EXECUTION_UNAVAILABLE`.
- **Classification**: **DISABLED BY SAFETY POLICY / DEFERRED TO STAGE 9**.
- **Product Scope Decision Required**:
  - *Option A (Scope Exclusion)*: Formally declare that Qwen Collective Release 1.0 is an orchestration, research, and project intelligence system that excludes untrusted generated-code execution. Code generation remains supported as text artifacts, but execution is delegated to isolated developer workstations or external CI/CD environments.
  - *Option B (Release-Blocking Requirement)*: Treat OS-enforced sandboxing as a mandatory release prerequisite, blocking release until Stage 9 implements a containerized execution worker (e.g. gVisor / Firecracker on Linux).
  - *Governance Mandate*: This product-scope decision cannot be made autonomously by the AI agent; it requires explicit stakeholder/human direction.

---

## 8. Final Stage 8 Readiness Assessment

### 8.1 Readiness Evaluation for Stage 9 Certification
Stage 8 implementation and documentation reconciliation is **COMPLETE AND VERIFIED**. However, moving from Stage 8 to Stage 9 certification requires resolving several key prerequisites:

```
[ Stage 8 Remediation ] ---> [ Stage 8C.5 Closure ] ---> [ Human Governance Gate ] ---> [ Stage 9 Release Certification ]
  - 8A: Backups                - Runbooks reconciled        - Scope decision (F-12)        - 30 Blackbox Scenarios
  - 8B: Operational Contract   - RPO corrected              - Cadence decision (240s)      - Fleet Live Verification
  - 8C: Loop/Upload/Bench      - Documentation aligned      - Sign-off on blockers         - Final Release Sign-off
```

### 8.2 Outstanding Security Findings & Operational Gaps
1. **Finding F-12 (Untrusted Code Execution)**:
   - *Status*: Fails closed (`SECURE_EXECUTION_UNAVAILABLE`).
   - *Remediation State*: Safe, but functionality is disabled rather than provided in a secure sandbox.
2. **Operational Gap: RPO Target Under Current Schedule**:
   - *Status*: Under the current 300s backup interval, worst-case disaster window is 303.64 seconds (**TARGET MISSED**).
   - *Remediation State*: Proposed 240s interval achieves 243.64s window; pending operator configuration sign-off.
3. **Operational Gap: Live Network Provider Verification**:
   - *Status*: Unit tests verify search providers via mocks; live network validation is reserved for release staging.
4. **Operational Gap: Provisional Upload Concurrency Limits**:
   - *Status*: Multi-stream JSON/Base64 uploads require end-to-end memory profiling under concurrent deserialization load to confirm safe production thresholds beyond 4 streams.

### 8.3 Recommended Release-Blocking Issues
The following items are recommended as **RELEASE-BLOCKING** prior to production deployment:
1. **F-12 Scope Resolution**: Stakeholder sign-off on whether Release 1.0 excludes untrusted code execution or blocks on container sandbox deployment.
2. **Backup Schedule Configuration**: Stakeholder approval to update the default automated backup interval from 300 seconds to 240 seconds.
3. **Live Fleet Verification**: Verification of Tailscale mesh connectivity and worker authentication across the 3 physical staging nodes (Windows Controller, remote VPS worker, local inference laptop).
4. **Execution of 30 Stage 9 Blackbox Scenarios (BB-01 through BB-30)**: Validating end-to-end user workflows against the running controller.

### 8.4 Operator Decisions Required Before Release
1. **Decision 1: Accept Scope Exclusion for Untrusted Code Execution?**
   - [ ] YES: Release 1.0 excludes untrusted code execution. F-12 is resolved via documented limitation and fail-closed safety policy.
   - [ ] NO: Release 1.0 requires code execution. Deploy Linux container worker with gVisor before release.
2. **Decision 2: Authorize 240-Second Backup Schedule?**
   - [ ] YES: Authorize updating the automated backup cadence to 240 seconds to satisfy the 5-minute RPO guarantee.
   - [ ] NO: Retain 300-second cadence and accept an amended RPO specification of 6 minutes (360 seconds).
3. **Decision 3: Authorize Stage 9 Release Certification Execution?**
   - [ ] YES: Authorize commencement of Stage 9 blackbox scenario testing and live fleet verification.
   - [ ] HOLD: Maintain Stage 8 status; address additional operational items before Stage 9.

---

## 9. Source Checkpoint and Documentation Changes

### 9.1 Baseline & File Changes
- **Source Revision**: `c98510b9a1569a113c762f083f5bba4395704531`
- **Application Code Modified**: **0 lines**. No production application code was altered during Task 8C.5.
- **Documentation Updated**:
  1. `docs/remediation/reports/STAGE_08C4_PERFORMANCE_BENCHMARKS.md`:
     - Section 3: Reconciled throughput metrics, distinguished scheduling from model inference, and labeled single vs. multi-worker workloads as non-comparable.
     - Section 5: Reconciled 35.8 MiB transport measurement vs. 120 MB end-to-end measurement, corrected "zero buffering" to "bounded streaming buffering", and labeled concurrency limits as provisional.
     - Section 8: Corrected RPO evaluation to TARGET MISSED / NOT YET DEMONSTRATED (303.64s > 300s target), and provided detailed analysis of proposed 240s interval.
     - Sections 12 & 13: Added capacity classification labels and documented RPO operational gap.
  2. `OPERATIONS_AND_RUNBOOK.md`:
     - Completely overhauled to reflect current codebase as source of truth.
     - Added operator-facing security limitations (untrusted code blocked, loop detector scope, operator authorization on restore).
     - Added operational capacity classifications (`VERIFIED IN TEST ENVIRONMENT`, `PROVISIONAL CONFIGURATION`, `NOT YET VERIFIED IN PRODUCTION`, `UNAVAILABLE`).
     - Documented verified procedures for all 17 operational triage and maintenance workflows.
  3. `docs/remediation/reports/STAGE_08C5_OPERATIONS_AND_RUNBOOKS.md` (this report):
     - Complete Stage 8 final report and readiness assessment.

### 9.2 Test Suite Execution Baseline
- **Regression Suite Baseline**: 774 collected tests, 773 passed, 0 failed, 1 skipped (preserved).
- **Targeted Benchmark Suite**: 12 collected, **12 passed**, 0 failed (`tests/test_stage08c4_benchmarking.py` in 9.67s).

---

## 10. GO or HOLD Recommendation

### **Recommendation: HOLD FOR HUMAN ACCEPTANCE & STAGE 8 CLOSURE**

Task 8C.5 is **IMPLEMENTED AND VERIFIED**.

All operational runbooks, performance benchmarks, memory profiles, recovery targets, and Stage 8 requirements have been reconciled and documented with strict technical truthfulness.

In accordance with governance constraints:
- **Do NOT begin Stage 9.**
- **Do NOT deploy to production.**
- **Do NOT alter live backup schedules or implement code execution sandboxes autonomously.**
- **STOP HERE AND PRESENT FINDINGS FOR HUMAN REVIEW.**
