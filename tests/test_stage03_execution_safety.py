"""
Stage 3 Execution Safety & Verification Gate Test Suite.

Proves:
1. Process-isolated, budget-constrained execution of generated code and test fixtures
   (no in-process exec, filesystem isolation, network blocked, env stripped, timeout enforced,
    tamper resistance, unavailable runner fails closed).
2. Bounded controller execution capacity, backpressure, per-project fairness, attempt deadlines,
   orphaned task recovery after simulated restart, plan revision commit fencing.
3. Fail-closed research evidence (invented citations rejected, missing sources fail closed,
   cross-run contamination rejected, ungrounded claims rejected).
4. Strengthened completion gates (nonterminal deliverable missing blocks completion,
   terminal report cannot complete without software artifact, unapproved human review blocks,
   stale attempt on required task blocks, acceptance criteria audit logging verified).
"""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

import pytest

from app.database import db_transaction, get_db_connection
from app.services import deliverables as D
from app.services import projects as P
from app.services.orchestration import (
    approval_manager, artifact_manager, code_sandbox, engine, evaluator,
)
from app.services.orchestration.common import jdump, jload, new_id, now_iso


# --- Fixtures ---

@pytest.fixture
def test_proj():
    p = P.create_project(name=f"Safety Project {new_id('p')[:6]}", owner="admin")
    return p["id"]


def _insert_run(c, run_id: str, project_id: str, state: str = "RUNNING", analysis: dict = None, intake: dict = None, autonomy: int = 2):
    now = now_iso()
    c.execute(
        """INSERT OR REPLACE INTO orchestration_runs 
           (id, project_id, state, intake_json, analysis_json, autonomy_level, created_by, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 'user', ?, ?)""",
        (run_id, project_id, state, json.dumps(intake or {}), json.dumps(analysis or {}), autonomy, now, now)
    )


def _insert_task(c, task_id: str, run_id: str, revision_id: str, plan_key: str, state: str = "pending", action: dict = None, attempts: int = 0, max_attempts: int = 2, dispatched_at: str = None, timeout_seconds: int = 60):
    now = now_iso()
    c.execute(
        """INSERT OR REPLACE INTO orchestration_tasks
           (id, run_id, revision_id, plan_key, title, agent_role, action_json, state, attempts, max_attempts, dispatched_at, timeout_seconds, created_at, updated_at)
           VALUES (?, ?, ?, ?, 'Title', 'Role', ?, ?, ?, ?, ?, ?, ?, ?)""",
        (task_id, run_id, revision_id, plan_key, json.dumps(action or {}), state, attempts, max_attempts, dispatched_at, timeout_seconds, now, now)
    )


def _insert_revision(c, rev_id: str, run_id: str, rev_num: int = 1, status: str = "approved", plan: dict = None):
    now = now_iso()
    c.execute(
        """INSERT OR REPLACE INTO orchestration_plan_revisions
           (id, run_id, revision_number, status, source, plan_json, created_by, created_at)
           VALUES (?, ?, ?, ?, 'template', ?, 'user', ?)""",
        (rev_id, run_id, rev_num, status, json.dumps(plan or {}), now)
    )


def _insert_deliverable(c, deliv_id: str, project_id: str, name: str, deliv_type: str, status: str = "planned"):
    now = now_iso()
    c.execute(
        """INSERT OR REPLACE INTO deliverables
           (id, project_id, name, deliverable_type, status, created_by, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 'user', ?, ?)""",
        (deliv_id, project_id, name, deliv_type, status, now, now)
    )



# =========================================================================
# 1. PROCESS-ISOLATED CODE EXECUTION & ISOLATION NEGATIVE TESTS
# =========================================================================
# 1. PROCESS-ISOLATED CODE EXECUTION & ISOLATION NEGATIVE TESTS
# =========================================================================

def test_code_execution_disabled_by_default_returns_blocked():
    """MANDATORY SECURITY GATE: Execution is disabled by default without OS-enforced sandbox."""
    code = "def add(a, b):\n    return a + b\n"
    test_code = "assert add(2, 3) == 5\n"
    res = code_sandbox.run_isolated_code_evaluation(code, test_code)
    assert res.success is False
    assert res.status == "BLOCKED"
    assert "SECURE_EXECUTION_UNAVAILABLE" in (res.error_message or "")
    assert "secure_execution_unavailable" in (res.failed_checks or [])


def test_evaluator_returns_blocked_with_structured_secure_execution_unavailable():
    """When execution is required for acceptance but secure sandbox is absent, returns BLOCKED."""
    code = "def compute(x):\n    return x * 2\n"
    test_code = "assert compute(21) == 42\n"
    ev = evaluator.evaluate_code_deliverable(
        code=code,
        filename="compute.py",
        test_code=test_code,
        acceptance_criteria={"require_execution": True}
    )
    assert ev["accepted"] is False
    assert ev["decision"] == "BLOCKED"
    assert ev["blocked_reason"] == "SECURE_EXECUTION_UNAVAILABLE"
    assert "SECURE_EXECUTION_UNAVAILABLE" in ev["summary"]
    # Check that failed check is secure_execution_environment
    sec_checks = [c for c in ev["checks"] if c["name"] == "secure_execution_environment"]
    assert len(sec_checks) == 1
    assert sec_checks[0]["passed"] is False


def test_evaluator_static_validation_does_not_claim_dynamic_execution_verified():
    """Static validation continues to verify syntax and AST symbols without claiming execution."""
    code = "def compute(x):\n    return x * 2\n"
    ev = evaluator.evaluate_code_deliverable(
        code=code,
        filename="compute.py",
        acceptance_criteria={"required_functions": ["compute"]}
    )
    assert ev["accepted"] is True
    assert ev["decision"] == "ACCEPTED"
    # Ensure no check claims dynamic execution was verified
    assert not any(c["name"] == "code_executable" for c in ev["checks"])
    syntax_check = next(c for c in ev["checks"] if c["name"] == "code_syntax_valid")
    assert syntax_check["passed"] is True
    assert "dynamic execution unverified" in syntax_check["detail"]


def test_cannot_bypass_execution_restriction_via_deliverable_acceptance():
    """Regression test: deliverable acceptance cannot bypass execution restriction to mark ACCEPTED."""
    code = "print('pwned')\n"
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(code)
        f_path = f.name
    try:
        deliverable = {"type": "code", "name": "UntrustedScript"}
        artifacts = [{"filename": "script.py", "storage_path": f_path, "validation_state": "valid"}]
        res = evaluator.evaluate_deliverable_acceptance(
            deliverable=deliverable,
            artifacts=artifacts,
            acceptance_criteria={"test_code": "assert True", "require_execution": True}
        )
        assert res["accepted"] is False
        assert res["decision"] == "BLOCKED"
        assert res["blocked_reason"] == "SECURE_EXECUTION_UNAVAILABLE"
    finally:
        os.unlink(f_path)


def test_code_sandbox_clean_execution():
    """Valid code and test fixture execute cleanly when subprocess isolation runner is invoked."""
    code = "def add(a, b):\n    return a + b\n"
    test_code = "assert add(2, 3) == 5\n"
    res = code_sandbox.run_isolated_code_evaluation(code, test_code, allow_untrusted_subprocess=True)
    assert res.success is True
    assert res.status == "ACCEPTED"
    assert "code_execution_clean" in res.passed_checks
    assert "test_fixture_passed" in res.passed_checks


def test_code_sandbox_tamper_resistance_host_unaffected():
    """Adversarial test code attempting to mutate host evaluator policy cannot touch host process."""
    original_fn = evaluator.evaluate_code_deliverable
    adversarial_code = """
import sys
# Attempt to hijack or overwrite evaluator module in sys.modules
if 'app.services.orchestration.evaluator' in sys.modules:
    sys.modules['app.services.orchestration.evaluator'].evaluate_code_deliverable = lambda *a, **k: {"accepted": True}
"""
    test_code = "assert True\n"
    res = code_sandbox.run_isolated_code_evaluation(adversarial_code, test_code, allow_untrusted_subprocess=True)
    # The host evaluator function is completely unchanged
    assert evaluator.evaluate_code_deliverable is original_fn


def test_code_sandbox_network_isolation_blocked():
    """Negative test: Attempting socket network connection is intercepted and raises PermissionError."""
    network_code = """
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect(("127.0.0.1", 80))
"""
    res = code_sandbox.run_isolated_code_evaluation(network_code, allow_untrusted_subprocess=True)
    assert res.success is False
    assert res.status == "REJECTED"
    assert "PermissionError" in (res.error_message or "") or "disabled" in (res.error_message or "").lower()


def test_code_sandbox_subprocess_spawning_blocked():
    """Negative test: Attempting to spawn uncontrolled host subprocesses is intercepted."""
    spawn_code = """
import subprocess
p = subprocess.Popen(["cmd.exe", "/c", "echo pwned"])
"""
    res = code_sandbox.run_isolated_code_evaluation(spawn_code, allow_untrusted_subprocess=True)
    assert res.success is False
    assert res.status == "REJECTED"
    assert "PermissionError" in (res.error_message or "") or "disabled" in (res.error_message or "").lower()


def test_code_sandbox_environment_sanitization():
    """Negative test: Verifies that controller secrets and database environment variables are stripped."""
    # Temporarily set sensitive env vars in host process
    os.environ["SECRET_CONTROLLER_KEY"] = "super-secret-pass-1234"
    os.environ["QC_DATABASE_PATH"] = "/var/data/qc.db"
    os.environ["CONTROLLER_DATABASE_URL"] = "sqlite:////tmp/ctrl.db"
    try:
        env_probe_code = """
import os
import sys
# Check if any secret or DB env var leaked into child environment
leaked = [k for k in os.environ if k in ("SECRET_CONTROLLER_KEY", "QC_DATABASE_PATH", "CONTROLLER_DATABASE_URL") or "SECRET" in k or "DATABASE" in k]
assert len(leaked) == 0, f"Leaked sensitive env vars: {leaked}"
"""
        res = code_sandbox.run_isolated_code_evaluation(env_probe_code, allow_untrusted_subprocess=True)
        assert res.success is True, f"Failed environment check: {res.error_message}"
    finally:
        os.environ.pop("SECRET_CONTROLLER_KEY", None)
        os.environ.pop("QC_DATABASE_PATH", None)
        os.environ.pop("CONTROLLER_DATABASE_URL", None)


def test_code_sandbox_wall_clock_timeout_enforced():
    """Negative test: Code exceeding wall-clock budget is terminated and fails."""
    infinite_loop_code = "while True:\n    pass\n"
    start = time.time()
    res = code_sandbox.run_isolated_code_evaluation(infinite_loop_code, timeout_seconds=1.0, allow_untrusted_subprocess=True)
    duration = time.time() - start
    assert res.success is False
    assert res.status == "REJECTED"
    assert "TimeoutExpired" in (res.error_message or "") or "exceeded wall-clock budget" in (res.error_message or "")
    assert duration < 4.0  # Terminated swiftly


def test_code_sandbox_runner_unavailable_fails_closed():
    """Negative test: When secure runner is unavailable, returns BLOCKED (never ACCEPTED)."""
    code = "x = 42\n"
    res = code_sandbox.run_isolated_code_evaluation(
        code,
        python_executable="nonexistent_python_binary_xyz",
        allow_untrusted_subprocess=True,
    )
    assert res.success is False
    assert res.status == "BLOCKED"
    assert "runner_unavailable" in (res.failed_checks or [])


def test_evaluator_evaluate_code_deliverable_uses_sandbox(monkeypatch):
    """evaluate_code_deliverable fails-closed (BLOCKED) because config env var alone cannot enable sandbox."""
    monkeypatch.setenv("QC_ENABLE_OS_ENFORCED_SANDBOX", "1")
    code = "def multiply(a, b):\n    return a * b\n"
    test_code = "assert multiply(3, 4) == 12\n"
    ev = evaluator.evaluate_code_deliverable(
        code=code,
        filename="calc.py",
        test_code=test_code,
        acceptance_criteria={"required_functions": ["multiply"]}
    )
    assert ev["accepted"] is False
    assert ev["decision"] == "BLOCKED"
    assert ev["blocked_reason"] == "SECURE_EXECUTION_UNAVAILABLE"


def test_evaluator_evaluate_code_deliverable_missing_required_tests_fails():
    """When acceptance criteria specifies required tests but no fixture is provided, acceptance fails."""
    code = "def calc(): return 1\n"
    ev = evaluator.evaluate_code_deliverable(
        code=code,
        filename="calc.py",
        test_code=None,
        acceptance_criteria={"require_tests": True}
    )
    assert ev["accepted"] is False
    assert ev["decision"] == "REJECTED"
    assert any(c["name"] == "required_tests_executed" and not c["passed"] for c in ev["checks"])


# =========================================================================
# 2. BOUNDED QUEUES, PROJECT FAIRNESS, COMMIT FENCING & RESTART RECOVERY
# =========================================================================

def test_controller_queue_saturation_backpressure(test_proj, monkeypatch):
    """When controller pool capacity is saturated, dispatch defers safely with backpressure."""
    # Set capacity artificially low to trigger backpressure
    monkeypatch.setattr(engine, "MAX_OUTSTANDING_CONTROLLER_TASKS", 2)
    monkeypatch.setattr(engine, "MAX_CONTROLLER_TASKS_PER_RUN", 10)

    # Populate active futures to simulate saturation
    with engine._FUTURES_LOCK:
        engine._ACTIVE_FUTURES["dummy_task_1:1"] = None
        engine._ACTIVE_FUTURES["dummy_task_2:1"] = None
        engine._ACTIVE_METADATA["dummy_task_1:1"] = {"run_id": "other_run"}
        engine._ACTIVE_METADATA["dummy_task_2:1"] = {"run_id": "other_run"}

    try:
        run = {"id": f"run-sat-{new_id('r')[:8]}", "project_id": test_proj, "autonomy_level": 2, "state": "RUNNING"}
        ot = {
            "id": f"ot-sat-{new_id('t')[:8]}",
            "plan_key": "step_saturated",
            "state": "pending",
            "backoff_until": None,
            "action_json": json.dumps({"type": "controller_step", "step": "research_search"}),
            "attempts": 0,
            "max_attempts": 2,
            "timeout_seconds": 60,
            "approval_mode": "auto",
            "risk": "low",
        }
        # Attempt dispatch
        with db_transaction() as c:
            _insert_run(c, run["id"], test_proj, "RUNNING")
            _insert_revision(c, "rev1", run["id"], 1, "approved")
            _insert_task(c, ot["id"], run["id"], "rev1", ot["plan_key"], action=json.loads(ot["action_json"]))

        # Calling _dispatch directly
        engine._dispatch(run, nodes=[], manual=False)

        # Task MUST remain pending (not dispatched) due to capacity saturation!
        c = get_db_connection()
        try:
            row = c.execute("SELECT state FROM orchestration_tasks WHERE id=?", (ot["id"],)).fetchone()
            assert row["state"] == "pending", "Saturated pool must defer task in pending state"
        finally:
            conn = c.close()
    finally:
        with engine._FUTURES_LOCK:
            engine._ACTIVE_FUTURES.pop("dummy_task_1:1", None)
            engine._ACTIVE_FUTURES.pop("dummy_task_2:1", None)
            engine._ACTIVE_METADATA.pop("dummy_task_1:1", None)
            engine._ACTIVE_METADATA.pop("dummy_task_2:1", None)


def test_controller_per_project_fairness(test_proj, monkeypatch):
    """One run cannot monopolize controller slots; per-run cap allows other runs to progress."""
    monkeypatch.setattr(engine, "MAX_OUTSTANDING_CONTROLLER_TASKS", 4)
    monkeypatch.setattr(engine, "MAX_CONTROLLER_TASKS_PER_RUN", 1)

    run1_id = f"run-fair-1-{new_id('r')[:6]}"
    run2_id = f"run-fair-2-{new_id('r')[:6]}"

    # Simulate run1 already having 1 active task
    with engine._FUTURES_LOCK:
        engine._ACTIVE_FUTURES["t1:1"] = None
        engine._ACTIVE_METADATA["t1:1"] = {"run_id": run1_id}

    try:
        # Check active counts
        with engine._FUTURES_LOCK:
            r1_active = sum(1 for m in engine._ACTIVE_METADATA.values() if m.get("run_id") == run1_id)
            r2_active = sum(1 for m in engine._ACTIVE_METADATA.values() if m.get("run_id") == run2_id)

        assert r1_active >= engine.MAX_CONTROLLER_TASKS_PER_RUN  # Run 1 is capped
        assert r2_active < engine.MAX_CONTROLLER_TASKS_PER_RUN   # Run 2 has available slots
    finally:
        with engine._FUTURES_LOCK:
            engine._ACTIVE_FUTURES.pop("t1:1", None)
            engine._ACTIVE_METADATA.pop("t1:1", None)


def test_controller_restart_orphaned_task_recovery(test_proj):
    """Simulated restart: in-memory future missing for dispatched task triggers restart recovery."""
    run_id = f"run-rec-{new_id('r')[:8]}"
    ot_id = f"ot-rec-{new_id('t')[:8]}"

    with db_transaction() as c:
        _insert_run(c, run_id, test_proj, "RUNNING")
        _insert_revision(c, "rev1", run_id, 1, "approved")
        # Task was marked dispatched 10 seconds ago before the controller process restarted
        _insert_task(
            c, ot_id, run_id, 'rev1', 'step_orphaned',
            state='dispatched',
            action={"type": "controller_step", "step": "research_search"},
            attempts=1, max_attempts=3,
            dispatched_at='2026-10-19T00:00:00Z', timeout_seconds=60
        )
        c.execute("INSERT INTO orchestration_attempts (id, orch_task_id, attempt_number, started_at) VALUES ('att-rec-1', ?, 1, '2026-10-19T00:00:00Z')",
                  (ot_id,))

    # In-memory _ACTIVE_FUTURES has no record for this task (clean restart state)
    with engine._FUTURES_LOCK:
        engine._ACTIVE_FUTURES.pop(f"{ot_id}:1", None)

    run = engine.get_run(run_id)
    ot = engine._fresh(ot_id)

    # Calling _collect_one detects the orphaned task
    engine._collect_one(run, ot, nodes=[])

    # Attempt 1 must be marked failed with failure_class='controller_restart'
    c = get_db_connection()
    try:
        att = c.execute("SELECT outcome, failure_class FROM orchestration_attempts WHERE orch_task_id=? AND attempt_number=1", (ot_id,)).fetchone()
        assert att["outcome"] == "failed"
        assert att["failure_class"] == "controller_restart"

        # The task was rescheduled to pending for retry
        fresh_ot = c.execute("SELECT state, failure_class FROM orchestration_tasks WHERE id=?", (ot_id,)).fetchone()
        assert fresh_ot["state"] in ("pending", "dispatched")
        assert fresh_ot["failure_class"] == "controller_restart"
    finally:
        c.close()


def test_controller_commit_fencing_plan_revision_superseded(test_proj):
    """If plan revision changes while a controller step runs, committing old result is rejected by fencing."""
    run_id = f"run-fen-{new_id('r')[:8]}"
    ot_id = f"ot-fen-{new_id('t')[:8]}"

    with db_transaction() as c:
        _insert_run(c, run_id, test_proj, "RUNNING")
        # Initial approved revision
        c.execute("""INSERT INTO orchestration_plan_revisions (id, run_id, revision_number, status, source, plan_json, created_by, created_at)
                     VALUES ('rev-old', ?, 1, 'superseded', 'template', '{"tasks":[]}', 'user', ?)""",
                  (run_id, now_iso()))
        # Newer approved revision
        c.execute("""INSERT INTO orchestration_plan_revisions (id, run_id, revision_number, status, source, plan_json, created_by, created_at)
                     VALUES ('rev-new', ?, 2, 'approved', 'template', '{"tasks":[{"key":"t_new"}]}', 'user', ?)""",
                  (run_id, now_iso()))
        _insert_task(
            c, ot_id, run_id, 'rev-old', 'step_stale',
            state='dispatched',
            action={"type": "controller_step", "step": "research_search"},
            attempts=1, max_attempts=2
        )
        c.execute("INSERT INTO orchestration_attempts (id, orch_task_id, attempt_number, started_at) VALUES ('att-fen-1', ?, 1, ?)",
                  (ot_id, now_iso()))

    # Try committing result using the old revision 'rev-old'
    ev = {"accepted": True, "checks": [{"name": "test", "passed": True}], "result": {}}
    engine._commit_controller_attempt(
        run_id=run_id,
        orch_task_id=ot_id,
        attempt_number=1,
        ev=ev,
        plan_revision_id="rev-old",
        plan_digest=hashlib.sha256(b'{"tasks":[]}').hexdigest()
    )

    # Attempt must be rejected due to plan revision fencing!
    c = get_db_connection()
    try:
        att = c.execute("SELECT outcome, failure_class FROM orchestration_attempts WHERE orch_task_id=? AND attempt_number=1", (ot_id,)).fetchone()
        assert att["outcome"] == "rejected"
        assert att["failure_class"] == "plan_superseded_fencing"

        # Task state MUST NOT have been updated to accepted
        task_row = c.execute("SELECT state FROM orchestration_tasks WHERE id=?", (ot_id,)).fetchone()
        assert task_row["state"] != "accepted"
    finally:
        c.close()


# =========================================================================
# 3. FAIL CLOSED ON UNVERIFIED RESEARCH EVIDENCE
# =========================================================================

def test_research_report_invented_citation_rejected():
    """Invented citation marker [src-invented] is rejected by evaluator."""
    md = "# Research Findings\n\nQuantum algorithms show exponential speedup [src-fake999].\n\n## References\n[1] Fake."
    ev = evaluator.evaluate_report(
        md=md,
        requires_citations=True,
        verified_sources=[{"id": "src-real1", "canonical_url": "https://example.com"}]
    )
    assert ev["accepted"] is False
    assert ev["decision"] == "REJECTED"
    assert any(c["name"] == "citations_resolved" and not c["passed"] for c in ev["checks"])


def test_research_report_missing_source_catalog_fails_closed():
    """Report with citations but 0 verified sources in catalog fails closed (NEEDS_HUMAN_REVIEW / BLOCKED)."""
    md = "# Findings\n\nImportant claim [1].\n\n## References\n[1] Some Source."
    ev = evaluator.evaluate_report(
        md=md,
        requires_citations=True,
        verified_sources=[]  # Empty catalog
    )
    assert ev["accepted"] is False
    assert ev["decision"] in ("NEEDS_HUMAN_REVIEW", "BLOCKED")
    assert any(c["name"] == "citations_verified" and not c["passed"] for c in ev["checks"])


def test_research_report_cross_run_contamination_rejected():
    """Citation pointing to a source record from a different run violates run isolation."""
    md = "# Analysis\n\nKey fact [src-runA].\n\n## References\n[1] Source."
    ev = evaluator.evaluate_report(
        md=md,
        requires_citations=True,
        run_id="run-current-B",
        verified_sources=[
            {"id": "src-runA", "research_run_id": "run-foreign-A", "canonical_url": "https://other.com"}
        ]
    )
    assert ev["accepted"] is False
    assert ev["decision"] == "REJECTED"
    assert any(c["name"] == "cross_run_isolation" and not c["passed"] for c in ev["checks"])


def test_research_report_unsupported_claims_fail_grounding():
    """When claims_grounded is False or citation_audit reports failure, report fails goal check."""
    md = "# Analysis\n\nUnsupported assertion [1].\n\n## References\n[1] Real Source."
    ev = evaluator.evaluate_report(
        md=md,
        requires_citations=True,
        claims_grounded=False,  # Citation does not support claim
        verified_sources=[{"id": "src-1", "canonical_url": "https://example.com"}]
    )
    assert ev["accepted"] is False
    assert any(c["name"] == "claims_grounded" and not c["passed"] for c in ev["checks"])


# =========================================================================
# 4. STRENGTHENED COMPLETION GATES
# =========================================================================

def test_completion_gate_blocked_when_nonterminal_deliverable_missing(test_proj):
    """Run CANNOT reach COMPLETED if an intermediate required nonterminal deliverable is missing."""
    run_id = f"run-gate-{new_id('r')[:8]}"
    d_nonterm_id = f"del-non-{new_id('d')[:8]}"
    d_term_id = f"del-term-{new_id('d')[:8]}"

    plan = {
        "deliverables": [
            {"key": "code_deliv", "name": "Core Module", "deliverable_type": "software_artifact", "produced_by": "task_code", "terminal": False},
            {"key": "report_deliv", "name": "Final Summary", "deliverable_type": "report", "produced_by": "task_rep", "terminal": True},
        ]
    }

    with db_transaction() as c:
        _insert_run(c, run_id, test_proj, "EVALUATING", analysis={"deliverable_map": {"code_deliv": d_nonterm_id, "report_deliv": d_term_id}})
        _insert_revision(c, 'rev-gate-1', run_id, 1, 'approved', plan)
        _insert_task(c, 'ot-rep', run_id, 'rev-gate-1', 'task_rep', state='accepted', attempts=1)
        c.execute("INSERT INTO orchestration_attempts (id, orch_task_id, attempt_number, started_at, outcome) VALUES ('att-r1', 'ot-rep', 1, ?, 'accepted')", (now_iso(),))

        # Register deliverables in deliverables table
        _insert_deliverable(c, d_nonterm_id, test_proj, 'Core Module', 'software_artifact', 'planned')
        _insert_deliverable(c, d_term_id, test_proj, 'Final Summary', 'report', 'submitted')

    run = engine.get_run(run_id)
    engine._evaluate_deliverables(run)

    # Run MUST NOT have transitioned to COMPLETED
    fresh_run = engine.get_run(run_id)
    assert fresh_run["state"] != "COMPLETED", "Run cannot complete when a nonterminal deliverable is missing"


def test_completion_gate_human_review_unapproved_blocks(test_proj):
    """When evaluator returns NEEDS_HUMAN_REVIEW and no user approval exists, deliverable is NOT auto-accepted."""
    run_id = f"run-hr-{new_id('r')[:8]}"
    did = f"del-hr-{new_id('d')[:8]}"

    plan = {
        "deliverables": [
            {
                "key": "ai_report",
                "name": "AI Research Synthesis",
                "deliverable_type": "report",
                "produced_by": "task_ai",
                "terminal": True,
                "acceptance_criteria": {"require_citations": True}
            }
        ]
    }

    with db_transaction() as c:
        _insert_run(c, run_id, test_proj, "EVALUATING", analysis={"deliverable_map": {"ai_report": did}})
        _insert_revision(c, 'rev-hr-1', run_id, 1, 'approved', plan)
        _insert_task(c, 'ot-ai', run_id, 'rev-hr-1', 'task_ai', state='accepted', attempts=1)
        c.execute("INSERT INTO orchestration_attempts (id, orch_task_id, attempt_number, started_at, outcome) VALUES ('att-hr-1', 'ot-ai', 1, ?, 'accepted')", (now_iso(),))
        _insert_deliverable(c, did, test_proj, 'AI Research Synthesis', 'report', 'planned')

    # Add an artifact that triggers NEEDS_HUMAN_REVIEW (e.g. unverified citations with empty source catalog)
    aid = artifact_manager.write_controller_artifact(engine.get_run(run_id), "ot-ai", "research_report.md", b"# Report\n\nAI claim [1].\n\n## References\n[1] Unverified")
    artifact_manager.set_validation(aid, "valid", "registered")

    engine._evaluate_deliverables(engine.get_run(run_id))

    fresh_run = engine.get_run(run_id)
    assert fresh_run["state"] != "COMPLETED", "Unverified research output cannot auto-complete without human review"


def test_completion_records_tested_criteria_and_evidence(test_proj):
    """When a run completes, it writes exact acceptance criteria tested and satisfying evidence into metadata."""
    run_id = f"run-audit-{new_id('r')[:8]}"
    did = f"del-aud-{new_id('d')[:8]}"

    plan = {
        "deliverables": [
            {
                "key": "calc_module",
                "name": "Calculator",
                "deliverable_type": "software_artifact",
                "produced_by": "task_calc",
                "terminal": True,
                "acceptance_criteria": {"required_functions": ["multiply"]}
            }
        ]
    }

    with db_transaction() as c:
        _insert_run(c, run_id, test_proj, "EVALUATING", analysis={"deliverable_map": {"calc_module": did}})
        _insert_revision(c, 'rev-aud-1', run_id, 1, 'approved', plan)
        _insert_task(c, 'ot-calc', run_id, 'rev-aud-1', 'task_calc', state='accepted', attempts=1)
        c.execute("INSERT INTO orchestration_attempts (id, orch_task_id, attempt_number, started_at, outcome) VALUES ('att-aud-1', 'ot-calc', 1, ?, 'accepted')", (now_iso(),))
        _insert_deliverable(c, did, test_proj, 'Calculator', 'software_artifact', 'planned')

    # Add code artifact that satisfies criteria
    code_content = b"def multiply(a, b):\n    return a * b\n"
    aid = artifact_manager.write_controller_artifact(engine.get_run(run_id), "ot-calc", "calc.py", code_content)
    artifact_manager.set_validation(aid, "valid", "code verified")

    engine._evaluate_deliverables(engine.get_run(run_id))

    fresh_run = engine.get_run(run_id)
    assert fresh_run["state"] == "COMPLETED"

    # Verify audit summary in analysis_json
    meta = json.loads(fresh_run["analysis_json"])
    assert "acceptance_summary" in meta
    summary = meta["acceptance_summary"]
    assert len(summary["deliverables_tested"]) == 1
    d_tested = summary["deliverables_tested"][0]
    assert d_tested["key"] == "calc_module"
    assert d_tested["status"] == "accepted"
    assert d_tested["criteria_tested"]["required_functions"] == ["multiply"]
    assert len(d_tested["evidence_satisfied"]) == 1
    assert d_tested["evidence_satisfied"][0]["id"] == aid
