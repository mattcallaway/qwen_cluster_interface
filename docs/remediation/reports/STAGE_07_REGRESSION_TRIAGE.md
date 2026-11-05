# Qwen Collective — Stage 7 Regression Failure Triage Report

**Date:** 2026-10-20  
**Status:** BOUNDED TRIAGE COMPLETE — TEST-RUNNER CORRECTED — STAGE 7 ON HOLD  
**Active Branch:** `feature/unified-artifact-deliverables`  
**Git Checkpoint (HEAD):** `c98510b9a1569a113c762f083f5bba4395704531`  
**Working Tree Patch Size:** 1,869,956 bytes (including 19 untracked Stage 1–7 sources/tests)  
**Execution Scope:** Forensic analysis, root-cause identification, and test-runner exit code correction only. No autonomous Stage 8 implementation performed.

---

## Executive Summary

The latest full regression suite execution reported:
- **680 tests collected**
- **646 passed**
- **34 failed**
- **0 skipped / 0 errors**
- **1,125.2 seconds execution time**

This triage reconciles all 34 failing test cases, resolves the arithmetic discrepancy in earlier reporting, isolates the common root cause behind the 24 SQLite `FOREIGN KEY constraint failed` errors, diagnoses the 4 legacy security tests and 3 standalone failures, verifies the test-runner exit-code fix on a disposable fixture, and confirms full source-tree preservation.

**Key Finding:** There is **zero functional regression in application business logic**. 
- 27 failures (24 FK + 3 unit assertions) are caused by cross-test session database pollution where `tests/test_infrastructure.py` deletes records from `infrastructure_nodes` without wiping `worker_task_leases`, leaving stranded leases that expire and trigger SQLite foreign-key violations inside `_reconcile_expired_leases_conn`.
- 4 failures are intentional security contract enforcements where legacy tests supplied unauthenticated plain strings (`'admin_architect'`) instead of authenticated `OperatorAuthority` tokens.
- 1 failure is an external network timeout (`net::ERR_TUNNEL_CONNECTION_FAILED`) in a live browser test.
- 1 failure is an overly narrow HTTP status check in `test_stage01_security` expecting `500` or `503` when the proxy contract returns `504 Gateway Timeout`.
- 1 failure is an outdated test assertion in `test_web_routes` expecting legacy prototype wording rather than the updated repository header.

---

## 1. Complete Inventory of All 34 Failing Test IDs

| # | Test ID | Subsystem | Failure Type / Exception | Category | Reproducible in Isolation? | Classification |
|:---:|---|---|---|---|:---:|---|
| 1 | `tests/test_orchestrator_units.py::test_pause_stops_dispatch_and_resume_continues` | Orchestration | `sqlite3.IntegrityError: FOREIGN KEY constraint failed` at `worker.py:1709` | DB Fixture Pollution | **NO** (Passes 100%) | Test Order Dependency |
| 2 | `tests/test_orchestrator_units.py::test_failure_retries_with_backoff_then_blocks_and_manual_retry` | Orchestration | `sqlite3.IntegrityError: FOREIGN KEY constraint failed` at `worker.py:1709` | DB Fixture Pollution | **NO** (Passes 100%) | Test Order Dependency |
| 3 | `tests/test_orchestrator_units.py::test_oom_reported_by_worker_is_classified` | Orchestration | `sqlite3.IntegrityError: FOREIGN KEY constraint failed` at `worker.py:1709` | DB Fixture Pollution | **NO** (Passes 100%) | Test Order Dependency |
| 4 | `tests/test_orchestrator_units.py::test_model_timeout_classified_and_retried` | Orchestration | `sqlite3.IntegrityError: FOREIGN KEY constraint failed` at `worker.py:1709` | DB Fixture Pollution | **NO** (Passes 100%) | Test Order Dependency |
| 5 | `tests/test_orchestrator_units.py::test_invalid_worker_output_is_not_accepted` | Orchestration | `sqlite3.IntegrityError: FOREIGN KEY constraint failed` at `worker.py:1709` | DB Fixture Pollution | **NO** (Passes 100%) | Test Order Dependency |
| 6 | `tests/test_orchestrator_units.py::test_completion_requires_validated_deliverable_not_just_exit_codes` | Orchestration | `sqlite3.IntegrityError: FOREIGN KEY constraint failed` at `worker.py:1709` | DB Fixture Pollution | **NO** (Passes 100%) | Test Order Dependency |
| 7 | `tests/test_orchestrator_units.py::test_duplicate_result_submission_is_idempotent` | Orchestration | `sqlite3.IntegrityError: FOREIGN KEY constraint failed` at `worker.py:1709` | DB Fixture Pollution | **NO** (Passes 100%) | Test Order Dependency |
| 8 | `tests/test_orchestrator_units.py::test_lease_expiry_then_redelivery` | Orchestration | `sqlite3.IntegrityError: FOREIGN KEY constraint failed` at `worker.py:1709` | DB Fixture Pollution | **NO** (Passes 100%) | Test Order Dependency |
| 9 | `tests/test_orchestrator_units.py::test_controller_restart_recovery_resumes_from_database` | Orchestration | `sqlite3.IntegrityError: FOREIGN KEY constraint failed` at `worker.py:1709` | DB Fixture Pollution | **NO** (Passes 100%) | Test Order Dependency |
| 10 | `tests/test_orchestrator_units.py::test_task_budget_blocks_and_expansion_requires_approval` | Orchestration | `sqlite3.IntegrityError: FOREIGN KEY constraint failed` at `worker.py:1709` | DB Fixture Pollution | **NO** (Passes 100%) | Test Order Dependency |
| 11 | `tests/test_orchestrator_units.py::test_replan_creates_new_revision_and_preserves_accepted_work` | Orchestration | `sqlite3.IntegrityError: FOREIGN KEY constraint failed` at `worker.py:1709` | DB Fixture Pollution | **NO** (Passes 100%) | Test Order Dependency |
| 12 | `tests/test_orchestrator_units.py::test_artifact_download_blocks_traversal_and_tampering` | Orchestration | `sqlite3.IntegrityError: FOREIGN KEY constraint failed` at `worker.py:1709` | DB Fixture Pollution | **NO** (Passes 100%) | Test Order Dependency |
| 13 | `tests/test_orchestrator_units.py::test_artifact_checksum_mismatch_detected` | Orchestration | `sqlite3.IntegrityError: FOREIGN KEY constraint failed` at `worker.py:1709` | DB Fixture Pollution | **NO** (Passes 100%) | Test Order Dependency |
| 14 | `tests/test_orchestrator_units.py::test_report_labels_ai_content_as_unverified_not_telemetry` | Orchestration | `sqlite3.IntegrityError: FOREIGN KEY constraint failed` at `worker.py:1709` | DB Fixture Pollution | **NO** (Passes 100%) | Test Order Dependency |
| 15 | `tests/test_orchestrator_units.py::test_deliverable_acceptance_at_level2_is_audited` | Orchestration | `sqlite3.IntegrityError: FOREIGN KEY constraint failed` at `worker.py:1709` | DB Fixture Pollution | **NO** (Passes 100%) | Test Order Dependency |
| 16 | `tests/test_orchestrator_units.py::test_level1_requires_human_deliverable_acceptance` | Orchestration | `sqlite3.IntegrityError: FOREIGN KEY constraint failed` at `worker.py:1709` | DB Fixture Pollution | **NO** (Passes 100%) | Test Order Dependency |
| 17 | `tests/test_orchestrator_units.py::test_cancel_cancels_inflight_tasks_and_project` | Orchestration | `assert []` (Polluted active run state) | DB State Pollution | **NO** (Passes 100%) | Test Order Dependency |
| 18 | `tests/test_orchestrator_units.py::test_worker_disconnect_before_start_reroutes_with_retry` | Orchestration | `assert 'pending' == 'dispatched'` (Polluted task lease status) | DB State Pollution | **NO** (Passes 100%) | Test Order Dependency |
| 19 | `tests/test_orchestrator_units.py::test_double_tick_never_double_dispatches` | Orchestration | `assert 0 == 1` (Pre-existing dispatched tasks from prior test) | DB State Pollution | **NO** (Passes 100%) | Test Order Dependency |
| 20 | `tests/test_stage03_execution.py::test_worker_idempotent_result_replay` | Worker / Exec | `sqlite3.IntegrityError: FOREIGN KEY constraint failed` at `worker.py:1709` | DB Fixture Pollution | **NO** (Passes 100%) | Test Order Dependency |
| 21 | `tests/test_stage03_execution.py::test_stale_worker_submission_after_lease_expiry_rejected` | Worker / Exec | `sqlite3.IntegrityError: FOREIGN KEY constraint failed` at `worker.py:1709` | DB Fixture Pollution | **NO** (Passes 100%) | Test Order Dependency |
| 22 | `tests/test_stage03_execution.py::test_end_to_end_deliverable_gate_allows_completion_only_on_acceptance` | Worker / Exec | `sqlite3.IntegrityError: FOREIGN KEY constraint failed` at `worker.py:1709` | DB Fixture Pollution | **NO** (Passes 100%) | Test Order Dependency |
| 23 | `tests/test_worker_api.py::test_task_leasing_and_submission_api_flow` | Worker API | `sqlite3.IntegrityError: FOREIGN KEY constraint failed` at `worker.py:1709` | DB Fixture Pollution | **NO** (Passes 100%) | Test Order Dependency |
| 24 | `tests/test_worker_reliability.py::test_independent_lease_heartbeat_thread` | Worker Reliability | `sqlite3.IntegrityError: FOREIGN KEY constraint failed` at `worker.py:1709` | DB Fixture Pollution | **NO** (Passes 100%) | Test Order Dependency |
| 25 | `tests/test_worker_service.py::test_durable_task_leasing_and_concurrency` | Worker Service | `sqlite3.IntegrityError: FOREIGN KEY constraint failed` at `worker.py:1709` | DB Fixture Pollution | **NO** (Passes 100%) | Test Order Dependency |
| 26 | `tests/test_worker_service.py::test_idempotent_task_result_submission` | Worker Service | `sqlite3.IntegrityError: FOREIGN KEY constraint failed` at `worker.py:1709` | DB Fixture Pollution | **NO** (Passes 100%) | Test Order Dependency |
| 27 | `tests/test_worker_service.py::test_lease_expiration_and_recovery` | Worker Service | `sqlite3.IntegrityError: FOREIGN KEY constraint failed` at `worker.py:1709` | DB Fixture Pollution | **NO** (Passes 100%) | Test Order Dependency |
| 28 | `tests/test_continuous_research_knowledge.py::test_promotion_governance_and_workflow` | Knowledge / Security | `PermissionError: Unauthorized: Plain username string 'admin_architect' cannot provide operator authority` | Security Authority Contract | **YES** | Changed Security Contract |
| 29 | `tests/test_distributed_artifact_lifecycle.py::test_distributed_three_node_artifact_lifecycle` | Knowledge / Security | `PermissionError: Unauthorized: Plain username string 'system_architect' cannot provide operator authority` | Security Authority Contract | **YES** | Changed Security Contract |
| 30 | `tests/test_live_multistage_research_integration.py::test_live_multistage_research_lifecycle` | Knowledge / Security | `PermissionError: Unauthorized: Plain username string 'chief_data_architect' cannot provide operator authority` | Security Authority Contract | **YES** | Changed Security Contract |
| 31 | `tests/test_stage05_research_lifecycle.py::test_i_candidate_knowledge_no_automatic_promotion` | Knowledge / Security | `PermissionError: Unauthorized: Plain username string 'lead_security_architect' cannot provide operator authority` | Security Authority Contract | **YES** | Changed Security Contract |
| 32 | `tests/test_live_web_research.py::test_live_web_research_cycle` | Research / Browser | `AssertionError: Headless browser failed: ... net::ERR_TUNNEL_CONNECTION_FAILED` | Network / Proxy Timeout | **YES** (requires internet) | External Dependency Failure |
| 33 | `tests/test_stage01_security.py::test_failed_proxy_causes_browser_retrieval_fail_closed` | Security / Proxy | `assert 504 in (500, 503)` | HTTP Status Contract | **YES** | Test Assertion Narrowness |
| 34 | `tests/test_web_routes.py::test_knowledge_page` | Web UI Routes | `assert 'Persistent Knowledge Repository' in response.text` | Outdated Test Assertion | **YES** | Baseline Legacy Assertion |

---

## 2. Corrected Failure-Category Arithmetic

### Reconciliation of the "Missing 34th Test"
Earlier review documentation grouped failures into:
- 4 security-policy failures
- 1 external dependency failure
- 28 "alleged historical failures"  
**Sum = 33 failures (1 test missing from total 34).**

### Exact Breakdown (34 Total)
1. **SQLite Foreign-Key Constraint Failures (24 tests):**  
   Every one of these 24 tests crashes at `app/services/worker.py:1709` (`_reconcile_expired_leases_conn`) with `sqlite3.IntegrityError: FOREIGN KEY constraint failed`.
2. **Orchestrator Unit Assertion Failures (3 tests):**  
   `test_cancel_cancels_inflight_tasks_and_project`, `test_worker_disconnect_before_start_reroutes_with_retry`, and `test_double_tick_never_double_dispatches` fail due to leaked task/project state in the shared session database.
3. **Security Authority Contract Tests (4 tests):**  
   `test_promotion_governance_and_workflow`, `test_distributed_three_node_artifact_lifecycle`, `test_live_multistage_research_lifecycle`, and `test_i_candidate_knowledge_no_automatic_promotion` fail because Stage 7 strictly enforces authenticated `OperatorAuthority` instances over plain string usernames.
4. **External Dependency Failure (1 test):**  
   `test_live_web_research_cycle` encounters `net::ERR_TUNNEL_CONNECTION_FAILED` attempting to reach live search engines without external connectivity.
5. **Proxy Fail-Closed Status Code Mismatch (1 test):**  
   `test_failed_proxy_causes_browser_retrieval_fail_closed` expects `500` or `503`, but the proxy server returns standard `504 Gateway Timeout`.
6. **Outdated Template String Assertion (1 test):**  
   `test_knowledge_page` asserts the legacy prototype string `'Persistent Knowledge Repository'`, whereas the updated UI template renders `'Collective Knowledge & Evidence Repository'`.

$$\text{Arithmetic Verification: } 24 + 3 + 4 + 1 + 1 + 1 = 34 \text{ failures.}$$

The previous tally of 28 historical failures collapsed 24 FK failures + 3 unit assertion failures + 1 other failure (either proxy status or template string), inadvertently omitting the other single failure from the 29-test non-security, non-network cluster ($24 + 3 + 1 + 1 = 29$, not 28).

---

## 3. Comparison Against the Previous 671-Test Execution

| Metric | Previous Full Regression (Stage 7 Repair Checkpoint) | Current Full Regression (Stage 7 Acceptance Gate) | Delta | Explanation |
|---|:---:|:---:|:---:|---|
| **Collected Tests** | 671 | 680 | +9 | +5 new Stage 7 security tests (`test_stage07_security_gate_repairs.py`), +4 Stage 6 UI deliverable verification tests |
| **Passed Tests** | 669 | 646 | -23 | Apparent regression driven by test-order DB state pollution across 27 tests |
| **Failed Tests** | 2 | 34 | +32 | Detailed below |
| **Skipped / Errors** | 0 | 0 | 0 | Unchanged |
| **Execution Time** | ~192s | 1,125.2s | +933.2s | Timeout backoffs and lease expirations waiting for orphaned leases |

### Delta Breakdown
In the 671-test execution:
- Only **2 tests failed**:
  1. `tests.test_live_web_research.test_live_web_research_cycle` (external network)
  2. `tests.test_web_routes.test_knowledge_page` (outdated template string)

In the 680-test execution:
- The same **2 baseline tests** failed again.
- **+1 proxy test** failed (`test_failed_proxy_causes_browser_retrieval_fail_closed` asserting 504 not in (500, 503)).
- **+4 legacy security tests** failed because Stage 7 intentional operator authorization hardening replaced string usernames with HMAC-signed `OperatorAuthority` instances.
- **+24 SQLite FK failures + 3 unit assertion failures** surfaced because running the full expanded suite past the 30-second lease expiration threshold allowed orphaned leases from `test_infrastructure.py` to trigger cascading foreign-key violations during subsequent worker leasing calls.

---

## 4. Root Cause Analysis of the SQLite Fixture Failures

### The Mechanism
1. **Shared Database Scope:** `tests/conftest.py` declares `@pytest.fixture(scope="session", autouse=True) def setup_test_environment()`, creating a single shared SQLite database for all 680 tests.
2. **Cascading Node Wipe:** In [`tests/test_infrastructure.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_infrastructure.py#L14-L26):
   ```python
   conn.execute("PRAGMA foreign_keys = OFF")
   conn.execute("DELETE FROM worker_executions")
   conn.execute("DELETE FROM infrastructure_verifications")
   conn.execute("DELETE FROM infrastructure_reconciliations")
   conn.execute("DELETE FROM infrastructure_capabilities")
   conn.execute("DELETE FROM infrastructure_nodes")
   conn.commit()
   conn.execute("PRAGMA foreign_keys = ON")
   ```
3. **Orphaned Leases in `worker_task_leases`:**  
   Notice that `worker_task_leases` was **not deleted**. Earlier tests inserted leases with `worker_id` pointing to nodes (e.g. `t-ram-big`, `test_worker_1`). When `infrastructure_nodes` was wiped with foreign keys disabled, all rows in `worker_task_leases` remained in the table, now referencing non-existent `node_id`s.
4. **Lease Expiration Threshold:**  
   Task leases are granted with a 30-second lifetime (`lease_expires_at`). In a short test run (<30s), these leases do not expire. In the full 1,125s regression run, these leases become expired early in the session.
5. **Trigger at `app/services/worker.py:1709`:**  
   Any test in subsequent modules (`test_orchestrator_units.py`, `test_stage03_execution.py`, `test_worker_service.py`, `test_worker_api.py`, `test_worker_reliability.py`) that calls `lease_task_for_worker()` triggers automatic expired lease reclamation:
   ```python
   def _reconcile_expired_leases_conn(conn) -> List[str]:
       # Finds expired leases from worker_task_leases
       for lease in expired_leases:
           recon_id = f"recon_{uuid4().hex[:12]}"
           conn.execute("""
               INSERT INTO infrastructure_reconciliations (
                   id, node_id, discrepancy_type, description, severity, status, detected_at
               ) VALUES (?, ?, 'verification_failure', ?, 'warning', 'detected', ?)
           """, (recon_id, lease["worker_id"], ...))
   ```
6. **The Constraint Violation:**  
   In `app/migrations.py:764`:
   ```sql
   CREATE TABLE infrastructure_reconciliations (
       id TEXT PRIMARY KEY,
       node_id TEXT REFERENCES infrastructure_nodes(id) ON DELETE SET NULL,
       ...
   );
   ```
   Because `foreign_keys = ON` is enabled on the active database connection, SQLite verifies that `node_id` exists in `infrastructure_nodes`. Because `test_infrastructure.py` wiped `infrastructure_nodes`, SQLite immediately raises:
   `sqlite3.IntegrityError: FOREIGN KEY constraint failed`.

### Confirmation
When run in isolation (with clean test databases), all 24 failing worker/orchestrator tests pass with 100% success (e.g., `tests/test_orchestrator_units.py::test_pause_stops_dispatch_and_resume_continues` confirmed passing in 28.6s).

---

## 5. Reconciling the Four Legacy Security Tests

The four tests failing with `PermissionError: Unauthorized: Plain username string ... cannot provide operator authority`:
1. `tests/test_continuous_research_knowledge.py::test_promotion_governance_and_workflow`
2. `tests/test_distributed_artifact_lifecycle.py::test_distributed_three_node_artifact_lifecycle`
3. `tests/test_live_multistage_research_integration.py::test_live_multistage_research_lifecycle`
4. `tests/test_stage05_research_lifecycle.py::test_i_candidate_knowledge_no_automatic_promotion`

### Architectural Verdict
- **The behavioral change is intentional and required.** Stage 7 explicitly eliminated self-declared operator privilege escalation where callers could bypass review boundaries by passing bare username strings (`'admin_architect'`).
- The application code is **correctly enforcing security invariants**. Allowing plain strings to succeed would re-introduce the critical vulnerability identified during the Stage 7 security review.

### Recommended Test Modernization (Stage 8)
- Update these 4 positive workflow tests to create an authenticated `OperatorAuthority` instance using the authorized test helper:
  ```python
  from app.services.knowledge_store import OperatorAuthority
  auth = OperatorAuthority(
      operator_id="admin_architect",
      is_operator=True,
      session_verified=True,
      can_force_transition=True,
      auth_signature=generate_operator_hmac("admin_architect", ...)
  )
  ```
- **Preserve explicit negative tests:** Ensure existing negative tests (e.g. in `test_stage07_security_gate_repairs.py`) continue to prove that unauthenticated strings, forgeable authority objects, and invalid HMAC signatures are rejected with `PermissionError`.

---

## 6. Investigation of Remaining Failures

### 1. `test_live_web_research_cycle` (Research Lifecycle)
- **Traceback:** `AssertionError: Headless browser failed: Browser navigation error: Page.goto: net::ERR_TUNNEL_CONNECTION_FAILED`.
- **Classification:** External dependency / network environment failure. The test attempts to connect to upstream live search engines (`duckduckgo.com`, `searxng`) through a headless Chromium instance in an environment where outbound tunnel routing is unavailable.
- **Contract:** Not a code regression. Business logic correctly fails closed when internet is unreachable.

### 2. `test_failed_proxy_causes_browser_retrieval_fail_closed` (Stage 1 Security)
- **Assertion:** `assert 504 in (500, 503)`.
- **Contractual Status:** RFC 9110 and standard HTTP proxy gateways specify **`504 Gateway Timeout`** (or `502 Bad Gateway`) when an upstream proxy fails to connect. The browser proxy returned `504`.
- **Contract:** `504` is a valid, correct fail-closed status. The test assertion `in (500, 503)` is overly narrow and should include `504`.

### 3. `test_knowledge_page` (Web UI Routes)
- **Assertion:** `assert 'Persistent Knowledge Repository' in response.text`.
- **Contractual Status:** `app/templates/knowledge/index.html` was updated in Stage 5/6 to render:
  `Collective Knowledge & Evidence Repository`.
- **Contract:** The HTML template is current; the test assertion in `test_web_routes.py` is an outdated string check from the initial prototype.

---

## 7. Test-Runner Exit Code Correction and Proof

### The Defect
`run_evidence_suite.py` executed `pytest` via `subprocess.run()`, captured `exec_proc.returncode`, and stored it in `pytest_execution_meta.json`. However, the script lacked an exit code invocation at the bottom, falling off the end with implicit exit code 0 regardless of test suite success or failure.

### The Correction
Applied to [`run_evidence_suite.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/run_evidence_suite.py):
1. Enabled passing target test arguments (`extra_args = sys.argv[1:]`) so verification fixtures can be executed without running the full suite.
2. Added pre-parse validation for `junit_report.xml` to distinguish evidence generation failures from test suite execution failures.
3. Added terminal exit code propagation:
   ```python
   # 7. Final Test Runner Exit Code Signaling
   # Preserves all evidence files, but returns non-zero when tests fail/error, zero when all pass.
   if exec_proc.returncode != 0:
       print(f"\n[TEST SUITE FAILURE] Pytest completed with failure exit code {exec_proc.returncode}. All evidence files preserved.")
       sys.exit(exec_proc.returncode)

   print("\n[TEST SUITE SUCCESS] All tests passed with exit code 0. All evidence files preserved.")
   sys.exit(0)
   ```

### Verification Proof on Disposable Fixture
Tested using a dedicated disposable fixture `tests/test_disposable_runner_check.py`:
- **Passing Case (`test_disposable_pass`):**
  - Output: `Pytest exited with code 0 in 2.1s`
  - Result: `[TEST SUITE SUCCESS] All tests passed with exit code 0. All evidence files preserved.`
  - Shell exit code: **`0`**
- **Failing Case (`test_disposable_fail`):**
  - Output: `Pytest exited with code 1 in 2.3s`
  - Result: `[TEST SUITE FAILURE] Pytest completed with failure exit code 1. All evidence files preserved.`
  - Shell exit code: **`1`**
  - Evidence files (`junit_report.xml`, `pytest_stdout.txt`, `pytest_execution_meta.json`, `test_matrix.json`, `test_ids_inventory.json`) were completely written and preserved.
- **Baseline Evidence Restoration:** Original 680-test full regression evidence files were verified intact after testing.

---

## 8. Source Checkpoint Preservation Status

- **Git HEAD:** `c98510b9a1569a113c762f083f5bba4395704531`
- **Working Tree Patch:** [`docs/remediation/evidence/working_tree_patch.diff`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/working_tree_patch.diff)
  - Updated to **1,869,956 bytes** (previously 940 KB, and originally 252 KB).
  - Includes **all 39 modified tracked files** and **all 19 untracked Stage 1–7 implementation source and test files**:
    - `app/services/orchestration/action_registry.py`
    - `app/services/orchestration/code_sandbox.py`
    - `app/services/orchestration/intake.py`
    - `app/services/research/browser_proxy.py`
    - `app/services/research/entailment.py`
    - `app/services/research/public_suffix.py`
    - `run_evidence_suite.py`
    - `run_stage06_browser_acceptance.py`
    - `tests/test_stage01_security.py`
    - `tests/test_stage02_planning.py`
    - `tests/test_stage03_execution.py`
    - `tests/test_stage03_execution_safety.py`
    - `tests/test_stage04_research_integrity.py`
    - `tests/test_stage04_review_corrections.py`
    - `tests/test_stage05_research_lifecycle.py`
    - `tests/test_stage06_artifact_subsystem.py`
    - `tests/test_stage06_unified_artifacts.py`
    - `tests/test_stage07_knowledge_governance.py`
    - `tests/test_stage07_security_gate_repairs.py`
- Zero credentials, secret keys, or persistent database binaries are committed to the patch.
- Zero source files have been reset, cleaned, or deleted.

---

## 9. Recommended Fixes Separated by Urgency and Subsystem

### Priority 1: High Urgency (Fixture Isolation & Test Pollution Elimination)
- **Subsystem:** Test Infrastructure / Harness (`tests/test_infrastructure.py`, `tests/conftest.py`)
- **Action:**  
  1. Update `tests/test_infrastructure.py` fixture cleanup so that if `infrastructure_nodes` is purged, `worker_task_leases` is also purged (`DELETE FROM worker_task_leases`).
  2. Implement database transaction rollback fixtures or isolated temporary database files for state-mutating test modules so long-running suites cannot leak expired leases across module boundaries.
- **Estimated Impact:** Instantly eliminates **27 out of 34 failures** (24 FK + 3 unit assertions).

### Priority 2: High Urgency (Legacy Security Test Modernization)
- **Subsystem:** Knowledge / Security Governance (`tests/test_continuous_research_knowledge.py`, `tests/test_distributed_artifact_lifecycle.py`, `tests/test_live_multistage_research_integration.py`, `tests/test_stage05_research_lifecycle.py`)
- **Action:** Update the 4 test cases to construct valid, authenticated `OperatorAuthority` test fixtures to verify legitimate promotion, while retaining explicit negative assertions verifying unauthenticated plain strings fail.
- **Estimated Impact:** Eliminates **4 out of 34 failures**.

### Priority 3: Medium Urgency (Proxy Assertion Widening)
- **Subsystem:** Stage 1 Security (`tests/test_stage01_security.py`)
- **Action:** Update `test_failed_proxy_causes_browser_retrieval_fail_closed` to assert `status_code in (500, 502, 503, 504)`.
- **Estimated Impact:** Eliminates **1 out of 34 failures**.

### Priority 4: Low Urgency (Web Route Legacy Assertion)
- **Subsystem:** Web UI (`tests/test_web_routes.py`)
- **Action:** Update `test_knowledge_page` assertion to check for `"Collective Knowledge & Evidence Repository"`.
- **Estimated Impact:** Eliminates **1 out of 34 failures**.

### Priority 5: Low Urgency (External Dependency Marking)
- **Subsystem:** Research Lifecycle (`tests/test_live_web_research.py`)
- **Action:** Annotate `test_live_web_research_cycle` with `@pytest.mark.network` or skip gracefully when live external egress is disabled.
- **Estimated Impact:** Eliminates **1 out of 34 failures** in offline CI environments.

---

## 10. Explicit Governance Recommendation

- **STAGE 6:** Remains **FUNCTIONALLY ACCEPTED, RELEASE VALIDATION PENDING**.
- **STAGE 7:** Remains on **HOLD**. (Acceptance gate must not be granted until the test-order pollution and legacy authority fixture updates are executed and validated under authorized Stage 8 instructions).
- **STAGE 8:** Remains **NOT AUTHORIZED**.

**EXECUTION HALTED PER STRICT BOUNDARY DIRECTIVE.**
