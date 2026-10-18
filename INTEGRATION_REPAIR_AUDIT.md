# Qwen Collective: Integration & Security Repair Audit

**Audit Date:** October 14, 2026  
**Audited Commit:** `3ce27be` (main / fix/security-reliability-audit / feature/vps-execution-and-awareness)  
**Dedicated Repair Branch:** `fix/integration-security-operational-readiness`  
**Test Suite Baseline:** 248 passed, 0 failed, 1 warning (39.42s)  
**Auditor:** Antigravity AI Pair Programmer

---

## 1. Executive Summary & Verification Methodology

A thorough source-level audit was conducted across the Qwen Collective codebase covering:
- Worker registration and Bearer token issuance (`app/routes/worker.py`, `app/services/worker.py`)
- VPS execution sandboxing, classes, and unsupported operations (`vps_agent/executor.py`)
- Qwen Chat route inference and Project Context Packet construction (`app/routes/chat.py`, `app/services/context_packet.py`)
- Task dependency data interface alignment (`app/services/tasks.py`, `app/services/context_packet.py`)
- VPS worker leasing concurrency, heartbeat, and long-running execution (`vps_agent/agent.py`, `app/services/worker.py`)
- Offline result buffering, drain verification, and foreign key safety (`vps_agent/buffer.py`, `app/services/worker.py`)
- End-to-end Project → Path → Stage → Task → Execution → Evidence → Knowledge → Metrics feedback loop

Every defect identified in the development prompt was investigated against the active source tree and classified into:
- **Confirmed Defect**: Reproducible gap between specification and active implementation.
- **Partially Mitigated**: Initial mechanism present but vulnerable to edge cases or lacking required security boundaries.
- **Already Fixed**: Behavior already verified by active code and tests.

---

## 2. Detailed Findings Matrix

| Finding ID | Component | Severity | Classification | Summary |
|---|---|---|---|---|
| **SEC-01** | `app/routes/worker.py` | Critical | **Confirmed** | Unauthenticated worker registration permits open token provisioning without admin enrollment token. |
| **SEC-02** | `vps_agent/executor.py` | Critical | **Confirmed** | Subprocess execution lacks Execution Classes (A/B/C), working dir sandbox, and returns `success` for unknown operations. |
| **SEC-03** | `app/routes/worker.py` | High | **Confirmed** | Worker API endpoints allow cross-worker inspection and spoofed result submissions. |
| **INT-01** | `app/routes/chat.py` | High | **Confirmed** | Ordinary chat route does not invoke Project Context Packets or provide context diagnostic inspection. |
| **INT-02** | `app/services/context_packet.py` | High | **Confirmed** | Field mismatch in task dependencies (`d['depends_on_task_id']` vs `d['id']`) causes crash in context generation. |
| **REL-01** | `vps_agent/agent.py` | High | **Confirmed** | Synchronous task execution blocks agent main loop, causing lease expiration and heartbeat loss during long jobs. |
| **INT-03** | `app/services/worker.py` | Medium | **Confirmed** | Execution-to-knowledge feedback loop is disconnected; completed VPS jobs do not stage deliverables or metrics. |

---

## 3. In-Depth Defect Breakdown & Proposed Corrections

### Finding SEC-01: Open Worker Registration Without Admin Enrollment Authorization
- **Affected File & Function**: `app/routes/worker.py:register_worker`, `app/services/worker.py:register_worker_node`
- **Current Behavior**: If a caller calls `POST /api/v1/worker/register` without an `Authorization` header, lines 151-155 unconditionally invoke `worker_svc.create_worker_token(req.node_id)` and return a valid Bearer token (`qcw_...`) with status `provisioned`.
- **Expected Behavior**: Worker registration must require an administrator-issued, single-use, time-limited enrollment token (or active administrator session). Unauthenticated callers without valid enrollment credentials must be strictly rejected with HTTP 401/403.
- **Security Impact**: Any network entity with HTTP access to the controller can register arbitrary rogue workers, claim approved tasks, and forge execution results.
- **Reproduction**:
  ```python
  client.post("/api/v1/worker/register", json={"node_id": "rogue-01", "name": "Rogue"})
  # Returns 200 OK with newly minted token
  ```
- **Proposed Correction**:
  1. Add `worker_enrollment_tokens` table in database schema (token hash, created_by, expires_at, used_at, single_use=1).
  2. Implement `create_enrollment_token()` for administrators.
  3. Require `X-Enrollment-Token` or valid pre-existing worker Bearer token during `POST /api/v1/worker/register`.
  4. Reject unauthenticated registration without valid, unexpired, unused enrollment token.

---

### Finding SEC-02: Missing Execution Classes & False Success on Unsupported Operations
- **Affected File & Function**: `vps_agent/executor.py:execute_task`
- **Current Behavior**:
  1. Lines 122-132 contain a catch-all fallback:
     ```python
     # 3. Default fallback for general approved tasks
     return {"status": "success", "exit_code": 0, "result_payload": {"processed": True}}
     ```
     Any unsupported or unknown task type reports `status: "success"` even though no operation was executed.
  2. `python_execution` runs `subprocess.run([sys.executable, "-c", script])` directly on the host without setting a designated restricted working directory, without environment variable sanitization, and without execution class isolation (Class A: built-in, Class B: approved scripts, Class C: experimental).
- **Expected Behavior**:
  1. Unknown or unsupported task types must explicitly fail with error `unsupported_operation`.
  2. Separate execution classes (Class A: built-in safe diagnostics; Class B: approved versioned scripts; Class C: experimental scripts requiring sandbox isolation or fail closed).
  3. Restrict working directory to dedicated agent sandbox directory (`worker_data/sandbox/`).
  4. Strip sensitive environment variables (`QC_SECRET_KEY`, `QC_ADMIN_PASSWORD`, etc.) from child process environment.
- **Security Impact**: Rogue or malformed tasks can read parent environment secrets, tamper with host filesystem, or report false task success.
- **Proposed Correction**: Refactor `vps_agent/executor.py` to enforce Class A/B/C execution models, strip parent environment secrets, enforce dedicated sandbox paths, and return `unsupported_operation` for unknown tasks.

---

### Finding SEC-03: Cross-Worker Inspection & Spoofed Result Submissions
- **Affected File & Function**: `app/routes/worker.py:list_executions`, `app/routes/worker.py:submit_task_result`
- **Current Behavior**:
  1. `list_executions` takes an optional `worker_id` query parameter. If Worker A passes `worker_id="worker-B"`, it returns Worker B's execution records without verifying that the requesting token owns `worker-B`.
  2. `submit_task_result` accepts results for any `task_id` without verifying that the task is currently assigned to or leased by `worker["node_id"]`.
- **Expected Behavior**:
  1. A worker can only inspect its own execution records (`worker_id == worker["node_id"]`).
  2. A worker can only submit results for tasks leased to that specific worker identity.
- **Security Impact**: Worker impersonation, task result tampering, and unauthorized data leakage across distributed nodes.
- **Proposed Correction**: Enforce strict node ID equality between authenticated token and target resources in `app/routes/worker.py` and `app/services/worker.py`.

---

### Finding INT-01: Qwen Chat Disconnected from Persistent Project Intelligence
- **Affected File & Function**: `app/routes/chat.py:chat_send`, `app/routes/chat.py:build_context_messages`
- **Current Behavior**: `chat_send` only pulls past conversation messages from `chat_messages`. It has no parameter or database column for `project_id`. It never invokes `build_project_context_packet()` or `build_architecture_context_packet()`. Qwen operates in complete ignorance of projects, active paths, tasks, and deliverables.
- **Expected Behavior**:
  1. Support explicit `project_id` association on `chat_conversations` and in the chat UI.
  2. When a conversation has an active project, dynamically generate and inject a token-budgeted Project Context Packet into the system prompt.
  3. Provide a read-only diagnostic inspector showing the assembled context sections, source references, trust classifications, token budgets, and omission notices.
- **Operational Impact**: Qwen cannot answer questions about active projects, strategic paths, deliverables, or infrastructure capabilities during user conversations.
- **Proposed Correction**:
  1. Add `project_id` to `chat_conversations`.
  2. In `app/routes/chat.py`, integrate `build_project_context_packet()` and `build_architecture_context_packet()` when `project_id` is set.
  3. Add context preview route `GET /chat/context-preview` and UI inspector modal.

---

### Finding INT-02: Task Dependency Key Mismatch in Context Packet Generator
- **Affected File & Function**: `app/services/context_packet.py:176-177`, `app/services/tasks.py:get_task_dependencies`
- **Current Behavior**:
  Line 177 of `app/services/context_packet.py`:
  ```python
  deps = task_svc.get_task_dependencies(t["id"])
  dep_str = f" (Depends on: {', '.join([d['depends_on_task_id'] for d in deps])})" if deps else ""
  ```
  However, `get_task_dependencies` executes:
  ```python
  SELECT t.* FROM tasks t JOIN task_dependencies td ON td.depends_on_task_id = t.id WHERE td.task_id = ?
  ```
  The returned dictionaries have keys matching `tasks` columns (`id`, `title`, etc.) and do NOT contain `depends_on_task_id`. Accessing `d['depends_on_task_id']` raises `KeyError: 'depends_on_task_id'`.
- **Expected Behavior**: Context packet generator should access `d['id']` or `d['title']`, and `get_task_dependencies` should return both prerequisite task metadata and dependency references cleanly.
- **Operational Impact**: Whenever any project task has dependencies, calling `build_project_context_packet()` crashes with an unhandled 500 error.
- **Proposed Correction**: Update `app/services/context_packet.py` to use `d.get('title') or d['id']`, and add comprehensive regression tests covering tasks with single, multiple, and chained dependencies.

---

### Finding REL-01: Synchronous Execution Blocks Worker Heartbeats & Lease Renewals
- **Affected File & Function**: `vps_agent/agent.py:poll_and_execute_task`, `vps_agent/agent.py:run_cycle`
- **Current Behavior**: `execute_task` runs synchronously on the main thread during `poll_and_execute_task`. If a task runs for 300 seconds, the main loop is blocked. The agent cannot send its 15-second health heartbeat, nor can it call `POST /tasks/{lease_id}/heartbeat`. The controller assumes the worker is dead and expires the lease.
- **Expected Behavior**:
  1. The agent must run long tasks with an active lease heartbeat thread or interval timer that extends `/tasks/{lease_id}/heartbeat` every N seconds (e.g. 30s) while execution is in progress.
  2. An independent heartbeat keeps node status `online`.
  3. If cancellation is received during lease renewal, the agent aborts execution gracefully.
- **Operational Impact**: Valid long-running tasks are prematurely reclaimed and reassigned, causing duplicate executions and race conditions.
- **Proposed Correction**: Enhance `VPSWorkerAgent` with a background lease-heartbeat manager that periodically touches `/tasks/{lease_id}/heartbeat` during execution and checks for cancellation.

---

### Finding INT-03: Execution-to-Knowledge Loop Disconnected
- **Affected File & Function**: `app/services/worker.py:submit_task_result`, `app/services/deliverables.py`
- **Current Behavior**: When a worker finishes a task and submits output data (`result_payload`), `submit_task_result` marks the task `completed` and logs to `worker_executions`, but does not create or link deliverables, record evidence in `task_execution_events`, stage findings into the project knowledge vault, or log metric observations.
- **Expected Behavior**: When a project task completes with deliverables or findings:
  1. Create or link deliverable records with checksums and provenance.
  2. Record a structured execution event in `task_execution_events`.
  3. If structured research/content is returned, stage a Markdown note in `knowledge/projects/{project_slug}/inbox/`.
  4. If numerical metrics are reported, record actual observations in `metric_observations`.
- **Operational Impact**: The system executes work in a silo without enriching the project's persistent knowledge base or metric trackers.
- **Proposed Correction**: Add automated evidence capture and knowledge staging hooks into `submit_task_result` for project-linked tasks.

---

## 4. Implementation Plan & Work Order

We will address these findings in the exact sequence mandated by the prompt:
1. **Phase B — Critical Security Repairs**:
   - Worker Enrollment Tokens & Registration Authorization (SEC-01, SEC-03).
   - VPS Execution Sandboxing & Unsupported Operations (SEC-02).
2. **Phase C — Knowledge & Context Integration**:
   - Connect Qwen Chat to Project Context & Architecture Knowledge (INT-01).
   - Fix Task Dependency Data Mismatch in Context Packet (INT-02).
   - Add Context Visibility Diagnostic Inspector.
3. **Phase D — Worker Reliability & Lease Management**:
   - Independent Lease Heartbeat & Cancellation Handling (REL-01).
   - Offline Result Buffering & Reconciliation.
4. **Phase E — End-to-End Integration**:
   - Execution-to-Knowledge Feedback Loop (INT-03).
   - Full Acceptance Scenario Test.
5. **Phase F — Verification & Final Reports**:
   - Full regression and security test suites.
   - Comprehensive documentation (`SECURITY_REMEDIATION_REPORT.md`, `WORKER_PROTOCOL_VALIDATION.md`, `CONTEXT_INTEGRATION_REPORT.md`, `END_TO_END_ACCEPTANCE.md`, `REMAINING_GAPS.md`).
