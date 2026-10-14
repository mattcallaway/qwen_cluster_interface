# Targeted Security & Reliability Repair Audit Report
**Antigravity — Qwen Collective**  
**Branch:** `fix/vps-production-acceptance`  
**Date:** October 14, 2026  
**Status:** Repairs Complete & Fully Verified (276 Automated Tests Passing)

---

## Executive Summary

This report documents the targeted security, reliability, and data-integrity repairs implemented for Qwen Collective in response to the audit findings. All changes were implemented on the dedicated repair branch `fix/vps-production-acceptance` without altering core application frameworks or introducing new distributed infrastructure.

The test suite expanded from **262 passing tests** to **276 passing tests** (+14 tests) with **zero failures** and **zero regressions**.

| Repair Area | Issue Diagnosed | Correction Implemented | Test Status |
| :--- | :--- | :--- | :--- |
| **Repair A: Class B Execution Isolation** | Arbitrary Python payloads could execute without approval records or checksum validation; PYTHONPATH could allow importing internal agent/controller code. | Enforced cryptographic checksum validation (`script_checksum`), mandatory approval verification (`is_approved`), strict environment variable scrubbing (`QC_*`, `API_KEY`, `TOKEN`, `SECRET`, `PASSWORD`), and restricted `PYTHONPATH` confined exclusively to the isolated scratch working directory (`0o700`). Unapproved scripts fail closed with exit code 126. Class C experimental code fails closed without approved container isolation. | **PASS** (13 tests in `tests/test_vps_agent.py`) |
| **Repair B: Offline Buffer & Conflict Acknowledgment** | Conflict reconciliation returned `reconciled_conflict`, but the worker buffer lacked explicit terminal acknowledgment semantics, risking retry loops or buffer blocking on HTTP 400 errors. | Implemented deterministic acknowledgment state machine in `vps_agent/agent.py` and `vps_agent/buffer.py`: `recorded`, `already_processed`, and `reconciled_conflict` immediately clear delivery from buffer; HTTP 400/422 invalid payloads are quarantined with diagnostic reason and do not block FIFO delivery; HTTP 401/403 suspends retries pending re-registration; HTTP 5xx and network dropouts retain and retry. Ensured already-completed tasks are never overwritten. | **PASS** (7 tests in `tests/test_worker_reliability.py`) |
| **Repair C: Metric Verification & Evidence Integrity** | Worker-submitted telemetry claims could self-declare `verification_status = 'verified'` in SQL without independent validation. | Discarded worker control over verification status; implemented `_evaluate_and_record_metric_observation` in `app/services/worker.py`. Controller independently validates artifact checksums (SHA256), verifies referenced deliverable files on the filesystem, uses trusted controller runtime timestamps, marks unconfirmed claims `unverified`, rejects fraudulent or mismatched checksums (`rejected`), and deduplicates observations. Updated Project Context Packet to visibly distinguish `Actual: ... (verified)` from `Reported: ... (unverified)`. | **PASS** (7 tests in `tests/test_metric_integrity.py` + full E2E loop) |

---

## 1. Baseline Test Execution
- **Pre-Repair Test Count:** 262 passed (41.26s)
- **Post-Repair Test Count:** 276 passed (43.10s)
- **Regressions:** 0
- **Failures:** 0
- **Warnings:** 4 (Starlette deprecation notices in test clients)

---

## 2. Summary of Changes

### Modified Files:
1. `vps_agent/executor.py`:
   - Enforced Class B script approval gate (`is_approved` or `approved_script_id`).
   - Added SHA256 script checksum validation against tamper/drift (`checksum_mismatch`).
   - Restricted working directory permissions (`0o700` on POSIX).
   - Sanitized child environment variables (`QC_*`, `SECRET`, `TOKEN`, `KEY`, `PASSWORD`, `SSH_`, `AWS_`, `DATABASE_`).
   - Pinned `PYTHONPATH` to `work_dir` so child processes cannot import worker/controller internals.
2. `vps_agent/buffer.py`:
   - Added `status` (`pending`, `quarantined`) and `quarantine_reason` columns to local SQLite buffer with automatic schema migration.
   - Added `quarantine_submission()`, `get_quarantined_submissions()`, and `count_quarantined()`.
   - Filtered active FIFO drain queries to `WHERE status = 'pending'`.
3. `vps_agent/agent.py`:
   - Updated `flush_offline_buffer()` to handle terminal acks: `recorded`, `already_processed`, `reconciled_conflict`.
   - Handled HTTP 400/422 by quarantining invalid payloads.
   - Handled HTTP 401/403 by suspending retries.
   - Handled HTTP 5xx/network errors by retaining items for retry.
   - Added dependency injection for `buffer` in `VPSWorkerAgent.__init__` for testing and custom configurations.
4. `app/services/worker.py`:
   - Updated `lease_task_for_worker()` to include `t.approval_required` and automatically generate immutable SHA256 script checksums for approved Class B scripts.
   - Updated `submit_task_result()` so that any submission targeting an already-completed task is reconciled as `reconciled_conflict` without overwriting the completed task.
   - Implemented `_evaluate_and_record_metric_observation()` to strip worker self-verification and enforce authoritative controller validation rules.
5. `app/services/context_packet.py`:
   - Updated Priority 6 Metrics rendering: verified observations render as `Actual: {val} (verified)`, unverified observations render as `Reported: {val} (unverified)`.
6. `app/routes/worker.py`:
   - Made `lease_id: Optional[str] = None` in `TaskResultSubmission` to support direct task result submissions.
7. `tests/test_vps_agent.py`:
   - Added positive and negative Class B approval and checksum validation tests.
8. `tests/test_worker_reliability.py`:
   - Added offline buffer conflict clearance, invalid payload quarantine, and auth suspension tests.
9. `tests/test_metric_integrity.py`:
   - Added 7 comprehensive tests covering self-verification denial, checksum validation, file mismatch rejection, deduplication, context packet distinction, and forecast calculation.

---

## 3. Next Phase

Repairs A, B, and C are completed and verified across the entire automated regression suite. The project is ready for **Phase Two: Controlled Production Acceptance Plan**, starting with read-only environment verification (Phase P0) and backup preparation (Phase P1).
