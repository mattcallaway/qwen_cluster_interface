# Security Remediation Report: Qwen Collective

**Date:** October 14, 2026  
**Auditor & Implementation Agent:** Antigravity AI Pair Programmer  
**Branch:** `fix/integration-security-operational-readiness`  
**Test Suite Verification:** 262 passed, 0 failures, 1 warning (42.05s)

---

## 1. Executive Summary

This report documents the security defects discovered, corrected, and verified across the Qwen Collective distributed execution platform. All identified vulnerabilities (unauthenticated worker registration, unrestricted cross-worker inspection, unsafe subprocess execution, false success reporting, and controller secret leakage) have been remediated with zero regressions across the 262-test suite.

---

## 2. Remediated Vulnerabilities Summary

| ID | Title | Severity | Status | Verification Class |
|---|---|---|---|---|
| **SEC-01** | Open Worker Registration Without Admin Enrollment Authorization | Critical | **Resolved** | Unit & Integration Tested |
| **SEC-02** | Unsanitized Execution, Missing Sandboxing & False Success Reporting | Critical | **Resolved** | Unit & Integration Tested |
| **SEC-03** | Worker Route Scope & Cross-Worker Resource Inspection | High | **Resolved** | Unit & Integration Tested |
| **SEC-04** | Controller Secrets Leaked to Child Worker Execution Environments | High | **Resolved** | Unit & Integration Tested |

---

## 3. In-Depth Remediation Breakdown

### Finding SEC-01: Open Worker Registration Without Enrollment Token

#### Original Defect & Reproduction
- **Affected Files**: `app/routes/worker.py:register_worker`, `app/services/worker.py:register_worker_node`
- **Current Behavior**: Unauthenticated HTTP callers calling `POST /api/v1/worker/register` were issued active `Bearer qcw_...` tokens unconditionally without presenting prior administrative authorization.
- **Security Impact**: Any network entity with HTTP access to the controller could register arbitrary rogue worker identities, pull approved project tasks, and submit forged execution results.
- **Reproduction**:
  ```python
  client = TestClient(app)
  resp = client.post("/api/v1/worker/register", json={"node_id": "rogue-01", "name": "Rogue"})
  assert resp.status_code == 200 # Previously succeeded and issued valid token!
  ```

#### Corrected Behavior
1. **Enrollment Token Schema (Migration 8)**:
   Added `worker_enrollment_tokens` table with columns: `id`, `token_hash`, `token_prefix`, `allowed_node_id`, `created_by`, `created_at`, `expires_at`, `used_at`, `used_by_node_id`, `revoked`.
2. **Dedicated Token Service**:
   Implemented `create_enrollment_token(allowed_node_id, created_by, duration_minutes)` generating cryptographically random `qcwe_<hex32>` tokens, hashed via SHA256 before database storage.
3. **Strict Enrollment Verification (`verify_enrollment_token`)**:
   - Requires single-use consumption (`used_at IS NULL`).
   - Verifies expiration timestamp (`expires_at > now`).
   - Enforces revocation status (`revoked == 0`).
   - If `allowed_node_id` is set, rejects any mismatched `node_id`.
   - Atomically records `used_at` and `used_by_node_id` upon successful enrollment.
4. **Registration Gate**:
   `POST /api/v1/worker/register` now strictly requires one of:
   - An active administrator session (cookie),
   - An existing valid `Bearer` worker token matching `node_id` (for re-registration/heartbeats), or
   - A valid `X-Enrollment-Token` header / `enrollment_token` payload field.
   All unauthenticated attempts without an enrollment token return HTTP 401 Unauthorized. Invalid, expired, reused, or revoked enrollment tokens return HTTP 403 Forbidden.

#### Tests Added & Results
- `tests/test_worker_api.py::test_unauthenticated_registration_rejected` (PASSED)
- `tests/test_worker_api.py::test_invalid_and_expired_enrollment_tokens_rejected` (PASSED)
- `tests/test_worker_api.py::test_valid_enrollment_succeeds_and_token_is_single_use` (PASSED)
- `tests/test_migrations.py::test_migration_8_worker_enrollment_and_chat_project` (PASSED)

#### Remaining Limitations
- In isolated local or test environments, enrollment tokens are provisioned via administrative API calls or pre-seeded database scripts. Tailscale authentication provides an additional transport layer boundary, but application-layer enrollment tokens remain strictly enforced.

---

### Finding SEC-02: Unsanitized Execution & Unsupported Operation Reporting

#### Original Defect & Reproduction
- **Affected Files**: `vps_agent/executor.py:execute_task`
- **Current Behavior**:
  - Catch-all fallback returned `{"status": "success", "exit_code": 0}` for any unhandled or unknown task type, falsely reporting successful execution for operations never performed.
  - Python scripts ran without working directory isolation or execution class restrictions.
- **Security Impact**: Silent failures, false assurance of task execution, and potential host filesystem tampering.
- **Reproduction**:
  ```python
  res = execute_task({"task_type": "arbitrary_malicious_or_unknown", "input_data": {}})
  assert res["status"] == "success" # Previously returned success!
  ```

#### Corrected Behavior
1. **Explicit Execution Classes**:
   - **Class A (Built-in Safe Operations)**: `test_task`, `health_check`, `diagnostic`, `capability_probe`, `ping`. Verified safe in-process diagnostics.
   - **Class B (Approved Versioned Scripts / Controlled Python Execution)**: `python_execution`, `approved_script`. Runs under a dedicated isolated temporary directory (`tempfile.mkdtemp(prefix="qc_worker_exec_")`), with execution timeouts, output truncation to `MAX_OUTPUT_CHARS` (50,000 chars), and strict working directory bounds.
   - **Class C (Experimental / AI-Generated Programs)**: `experimental_execution`, `docker_execution`. Fails closed (`status: "error", error_category: "isolation_unavailable"`) unless an approved OS-level sandbox or container runtime is confirmed.
2. **Explicit Unsupported Operation Failure**:
   Unknown task types return:
   ```json
   {
     "status": "error",
     "exit_code": 126,
     "error_category": "unsupported_operation",
     "stderr_summary": "Task type '...' is not supported by executor."
   }
   ```
   No false success is ever reported.

#### Tests Added & Results
- `tests/test_vps_agent.py::test_executor_unsupported_operation_fails_explicitly` (PASSED)
- `tests/test_vps_agent.py::test_executor_class_a_built_in` (PASSED)
- `tests/test_vps_agent.py::test_executor_class_b_python_sandbox` (PASSED)
- `tests/test_vps_agent.py::test_executor_class_c_fails_closed_without_container` (PASSED)

---

### Finding SEC-03: Cross-Worker Resource Inspection & Lease Spoofing

#### Original Defect & Reproduction
- **Affected Files**: `app/routes/worker.py`, `app/services/worker.py`
- **Current Behavior**:
  - `GET /api/v1/worker/executions?worker_id=worker-B` allowed Worker A to view Worker B's private execution logs and output data.
  - `POST /api/v1/worker/tasks/{task_id}/submit` did not enforce that the lease or task belonged to the caller's node identity.
- **Security Impact**: Worker impersonation, unauthorized cross-worker execution inspection, and malicious result tampering.

#### Corrected Behavior
1. **Ownership Enforcement on Execution Logs**:
   In `app/routes/worker.py:list_executions`:
   If the requester is authenticated as a worker (rather than an admin session), they can only query their own `node_id`. If `worker_id` query param differs from `worker["node_id"]`, HTTP 403 Forbidden is returned.
2. **Ownership Enforcement on Task Leasing & Submission**:
   - `lease_task_for_worker` requires `worker_id` to match the authenticated worker token.
   - `submit_task_result` verifies:
     - The lease exists and `lease["worker_id"] == worker_id`.
     - The task is not leased to a different active worker.
     - Any mismatched submission raises `PermissionError` (HTTP 403).

#### Tests Added & Results
- `tests/test_worker_api.py::test_worker_cannot_impersonate_or_access_other_nodes` (PASSED)
- `tests/test_worker_reliability.py::test_cross_worker_submission_rejected` (PASSED)

---

### Finding SEC-04: Controller Secrets Scrubbing in Child Subprocesses

#### Original Defect & Reproduction
- **Affected Files**: `vps_agent/executor.py:_build_sanitized_environment`
- **Current Behavior**: `subprocess.run()` inherited `os.environ` completely, exposing `QC_SECRET_KEY`, `QC_ADMIN_PASSWORD`, and other sensitive controller secrets to task scripts.

#### Corrected Behavior
Implemented `_build_sanitized_environment()`:
- Whitelists safe operational variables (`PATH`, `SYSTEMROOT`, `TEMP`, `USER`, `HOME`, `PYTHONPATH`, `LANG`, etc.).
- Explicitly scrubs all variables matching `QC_*`, `*SECRET*`, `*TOKEN*`, `*KEY*`, or `*PASS*`.
- Injects sandboxed execution metadata (`QC_EXECUTION_SANDBOX="1"`).

#### Tests Added & Results
- `tests/test_vps_agent.py::test_executor_environment_sanitization_strips_secrets` (PASSED)

---

## 4. Verification Classification Table

| Component | Test Method | Classification | Status |
|---|---|---|---|
| Migration 8 Schema | Automated Pytest Migration Engine | Integration tested | **Verified** |
| Worker Enrollment Token Lifecycle | Pytest API & Service Tests | Integration tested | **Verified** |
| Cross-Worker Authorization & ACLs | Negative Security Tests | Unit & Integration tested | **Verified** |
| Class A/B/C Sandboxing & Secret Scrubbing | Isolated Executor Subprocess Tests | Tested against isolated worker | **Verified** |
| End-to-End Loop with Sandbox Execution | Full 12-Step Scenario Test | Tested against isolated worker | **Verified** |

> [!NOTE]
> All security repairs were verified through comprehensive unit and integration test suites against an isolated mock/test worker environment without altering production service databases or mutating production VPS infrastructure.
