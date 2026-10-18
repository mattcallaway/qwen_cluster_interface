# Worker Protocol Validation Report: Leasing, Heartbeats & Offline Resilience

**Date:** October 14, 2026  
**Auditor & Implementation Agent:** Antigravity AI Pair Programmer  
**Branch:** `fix/integration-security-operational-readiness`  
**Test Suite Verification:** 262 passed, 0 failures, 1 warning (42.05s)

---

## 1. Executive Summary

This report validates the reliable distributed execution protocol connecting the Qwen Collective controller with remote VPS worker nodes. The earlier defects—where synchronous execution blocked lease renewal and heartbeats, and offline buffers lacked conflict handling—have been resolved with independent heartbeat concurrency, non-blocking polling loops, and conflict reconciliation states.

---

## 2. Protocol Components & Defect Verification Matrix

| Protocol Component | Defect Addressed | Expected Behavior | Verification Class | Status |
|---|---|---|---|---|
| **Independent Heartbeat** | REL-01: Heartbeat blocked by task execution | Node heartbeat and lease heartbeat run concurrently during execution | Tested against isolated worker | **Validated** |
| **Durable Task Leasing** | Task lease expiration during long-running tasks | Leases extended via `/tasks/{lease_id}/heartbeat` thread every 15s | Integration tested | **Validated** |
| **Capability Matching** | Worker pulled tasks outside its capability set | Lease query filters strictly by `supported_categories` matching `task_type` | Unit & Integration tested | **Validated** |
| **Offline Buffering** | Unreachable controller drops results | SQLite buffer stores results and drains automatically upon reconnection | Unit & Integration tested | **Validated** |
| **Expired Lease Reconciliation** | Double execution or lost result when lease expires | Completed task preserves delayed result as `reconciled_conflict` without overwriting | Integration tested | **Validated** |
| **Idempotency Deduplication** | Duplicate result submission causes duplicate ledger rows | Second submission recognized by `idempotency_key`, returns `already_processed` | Integration tested | **Validated** |

---

## 3. Protocol Implementation Details

### A. Non-Blocking Concurrent Lease & Node Heartbeats (`vps_agent/agent.py`)
- **Problem**: In previous iterations, `poll_and_execute_task` ran synchronously on the main agent loop. If a task took 60 seconds to finish, the worker failed to send its 15-second node health heartbeat, causing the controller to mark the node `degraded` or `offline`. More critically, the task's 60-second execution lease expired, triggering controller lease reclamation.
- **Solution**:
  - Implemented `_run_lease_heartbeats(agent, lease_id, stop_event, interval_seconds=15)`.
  - When a task lease is claimed, a daemon thread is spawned that periodically touches `/api/v1/worker/tasks/{lease_id}/heartbeat` and `/api/v1/worker/heartbeat`.
  - When task execution terminates (success, error, or timeout), `stop_event.set()` is triggered and the thread joins cleanly before result submission.

### B. Capability Matching During Task Lease Dispatch (`app/services/worker.py`)
- When a worker calls `/api/v1/worker/tasks/lease`, it passes `supported_categories: List[str]`.
- The leasing query now dynamically filters:
  ```sql
  WHERE t.status IN ('queued', 'pending', 'approved')
    AND (t.approval_required = 0 OR t.status IN ('queued', 'approved'))
    AND (t.assigned_worker IS NULL OR t.assigned_worker = ?)
    AND t.task_type IN (?, ?, ...)
  ```
- Workers are only leased tasks that they are capable of executing, preventing Class A workers from attempting unsupported Class C or complex foreign task types.

### C. Offline SQLite Buffering & Recovery (`vps_agent/buffer.py`)
- When the controller is unreachable (network timeout or connection refused):
  - Completed execution outcomes are buffered into `agent_buffer.db` SQLite database with `task_id`, `lease_id`, `idempotency_key`, and full submission JSON payload.
  - On subsequent cycles, `flush_offline_buffer()` drains pending records in FIFO order.
  - Confirmed delivery occurs only when the controller returns HTTP 200 with `status: "recorded"` or `status: "already_processed"`.

### D. Expired Lease & Delayed Result Reconciliation (`app/services/worker.py`)
- **Scenario**: A worker's lease expired due to an extended network partition, and the controller re-queued or another worker re-executed the task to completion.
- **Handling**:
  - When the original worker reconnects and submits its delayed result:
  - If the task is already in `status = 'completed'` by another worker:
    - The controller does **not** corrupt or overwrite the active completed task state.
    - Instead, it logs an immutable record in `worker_executions` with `status = 'cancelled'`, `error_category = 'reconciled_conflict'`.
    - It logs a reconciliation resolution note in `infrastructure_reconciliations`.
    - Returns `status: "reconciled_conflict"` to the worker so the worker can safely clear its buffer without error loops.

---

## 4. Test Evidence & Results

### Unit & Reliability Tests (`tests/test_worker_reliability.py`)
1. `test_concurrent_lease_heartbeat_keeps_lease_active`:
   Spawns a mock long-running execution (0.6s) with lease heartbeat interval (0.15s). Verified that `heartbeat_at` was refreshed multiple times during execution.
2. `test_delayed_submission_reconciliation_conflict_preserved`:
   Simulated expired lease where task was completed by Worker B before Worker A submitted. Verified Worker A's result was preserved with `status: "reconciled_conflict"` in `worker_executions` and task status remained untouched.
3. `test_offline_buffer_survives_controller_downtime`:
   Simulated network failure on submission, verified record buffered in SQLite, simulated reconnect, and verified successful drain.

### Milestone 2 Full Acceptance Flow (`tests/test_milestone_2_acceptance.py`)
- Complete cycle: provision enrollment token -> register node -> send health heartbeat -> lease task -> sandboxed execution -> submit result -> offline buffering during network drop -> buffer flush.
- Result: **PASSED (100%)**

### Full Worker API Suite (`tests/test_worker_api.py`, `tests/test_worker_service.py`)
- All 11 tests passed with zero failures.

---

## 5. Verification Classification Summary

- **Source Reviewed**: Validated query structures, thread safety, exception trapping, and trigger compatibility.
- **Unit Tested**: Local database operations, token hashing, buffer insertion/drain.
- **Integration Tested**: Complete Starlette TestClient API interactions across controller and agent interfaces.
- **Tested Against Isolated Worker**: Executed against isolated `VPSWorkerAgent` running with temporary data directories and sandboxed subprocesses.
- **Verified on Production Hardware**: Production Windows service and production remote VPS agent left untouched in accordance with operational safety directives; all changes validated in isolated staging workspace.
