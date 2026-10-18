# Offline Buffer & Conflict Reconciliation Validation
**Antigravity — Qwen Collective**  
**Component:** `vps_agent/buffer.py`, `vps_agent/agent.py`, `app/services/worker.py`  
**Status:** VALIDATED & PASSING

---

## 1. Problem Statement
The worker agent buffers task execution outcomes locally in SQLite when network dropouts prevent immediate delivery to the controller. During prolonged disconnections:
1. A leased task may expire on the controller.
2. Another worker or operator may reclaim and complete the task.
3. When the original worker reconnects, the controller's conflict reconciliation returns `reconciled_conflict`.
4. Without explicit terminal acknowledgment semantics, the worker might keep retrying `reconciled_conflict` indefinitely.
5. In addition, malformed submissions (HTTP 400/422) could block the FIFO buffer queue permanently, preventing valid subsequent submissions from draining.

---

## 2. Acknowledgment State Machine Specification

The buffer synchronization loop now enforces deterministic state transitions:

| Controller Response / Network State | Meaning | Local Buffer Action | Next Queue Action |
| :--- | :--- | :--- | :--- |
| **HTTP 200: `recorded`** | Task result accepted and committed to ledger | `remove_submission(id)` | Proceed to next item in FIFO queue |
| **HTTP 200: `already_processed`** | Duplicate submission already processed | `remove_submission(id)` | Proceed to next item in FIFO queue |
| **HTTP 200: `reconciled_conflict`** | Conflicting result durably recorded in ledger | `remove_submission(id)` | Proceed to next item in FIFO queue |
| **HTTP 400 / 422: Invalid Payload** | Result format rejected by schema validation | `quarantine_submission(id, reason)` | Quarantined; does not block remaining items |
| **HTTP 401 / 403: Auth Rejected** | Worker token revoked or invalid | Suspend retries (`is_registered = False`) | Halt drain loop pending re-registration |
| **HTTP 5xx: Controller Error** | Controller temporarily unavailable | Retain in buffer (`attempts += 1`) | Halt drain loop; retry on next cycle |
| **Network Timeout / Dropout** | Connection dropped | Retain in buffer (`attempts += 1`) | Halt drain loop; retry on next cycle |
| **Unknown 200 Status** | Outcome unexpected | Retain in buffer for operator review | Halt drain loop to avoid data loss |

---

## 3. Data Integrity & Non-Overwriting Invariant

In `app/services/worker.py:submit_task_result`, the conflict check guarantees:
```python
if task_already_done:
    # Another worker or process already completed this task!
    # Preserve execution record as reconciled conflict without overwriting the completed task
    execution_id = f"exec_{uuid4().hex[:12]}"
    conn.execute(
        """
        INSERT INTO worker_executions (
            id, task_id, worker_id, lease_id, idempotency_key, status,
            exit_code, started_at, completed_at, runtime_seconds,
            stdout_summary, stderr_summary, error_category,
            deliverable_ids_json, result_payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, 'cancelled', ?, ?, ?, ?, ?, ?, 'reconciled_conflict', ?, ?, ?)
        """,
        ...
    )
    return {
        "status": "reconciled_conflict",
        "execution_id": execution_id,
        "task_id": task_id,
        "task_status": task_row["status"],
        "completed_at": now,
        "message": "Task was already completed by another execution; result preserved as conflicting evidence.",
    }
```
- **Invariant:** A delayed result submitted for an already-completed task **never** overwrites the completed task.
- **Auditability:** Both executions are permanently recorded in `worker_executions`: the winning execution is recorded as `completed`/`success`, while the conflicting execution is preserved as `cancelled` with `error_category = 'reconciled_conflict'`.

---

## 4. Automated Test Evidence

Verified in `tests/test_worker_reliability.py`:

| Test Name | Scenario Tested | Outcome |
| :--- | :--- | :--- |
| `test_offline_buffer_drain_lifecycle` | Local SQLite buffer persistence and removal across agent instances | **PASS** |
| `test_expired_lease_reconciliation_open_task` | Expired lease with open task reconciles to `completed` upon reconnect | **PASS** |
| `test_expired_lease_conflict_preservation` | Expired lease with completed task records `reconciled_conflict` without overwrite | **PASS** |
| `test_offline_buffer_flush_reconciled_conflict_clears_queue` | Worker flushes offline result, receives `reconciled_conflict`, and clears queue | **PASS** |
| `test_offline_buffer_quarantines_invalid_payload_without_blocking_queue` | HTTP 400/422 moves item to `status='quarantined'` and processes remaining FIFO items | **PASS** |
| `test_offline_buffer_auth_rejection_suspends_retries` | HTTP 401 unsets `is_registered` and suspends retries without discarding data | **PASS** |
| `test_independent_lease_heartbeat_thread` | Background heartbeat thread maintains lease validity during long execution | **PASS** |

---

## 5. Summary
No execution result is silently discarded. Every terminal acknowledgment produces deterministic queue behavior, and conflicts preserve all historical evidence for administrative review.
