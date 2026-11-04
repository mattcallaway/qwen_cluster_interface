"""
Tests for Final Recovery Integration & Invariant Verification:
- F-06: PLANNING/checkpoint concurrency race condition elimination
- F-06: Orphaned inference request cancellation on run cancellation / failure
- F-06: Queue-wait vs execution timeout separation in model_router
- F-06: Invariant that CANCELLED cannot transition to COMPLETED (stale result fencing)
- F-05: Claim verification distinguishing direct support from independent multi-domain corroboration
- F-05: CAPTCHA and anti-bot challenge page rejection
- F-14: Plan validation rejecting schema placeholders, incompatible capabilities, and preventing false approval
"""
import uuid
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from app.database import get_db_connection, db_transaction
from app.services import tasks as T
from app.services import projects as P
from app.services import worker
from app.services.orchestration import (
    approval_manager,
    common,
    engine,
    model_router,
    planner,
    states,
    task_graph,
)
from app.services.research import extraction
from app.services.research.verification import EvidenceLedger


@pytest.fixture
def clean_project():
    proj = P.create_project(
        name=f"Integration Test Project {uuid.uuid4().hex[:8]}",
        description="Verifying F-06, F-05, F-14 integration fixes",
        owner="operator",
    )
    return proj


# =========================================================================
# 1. F-06: CANCELLED invariant & Stale Result Fencing
# =========================================================================

def test_task_cancelled_cannot_transition_to_completed():
    """Verify that a cancelled task cannot be transitioned to running or completed."""
    t = T.create_task(
        title="Inference test task",
        description="Testing terminal cancellation fencing",
        task_type="inference",
        assigned_worker="desktop-worker-01",
    )
    task_id = t["id"]
    assert t["status"] in ("draft", "queued", "pending")

    # Cancel the task
    cancelled = T.cancel_task(task_id)
    assert cancelled is True
    t_after = T.get_task_by_id(task_id)
    assert t_after["status"] == "cancelled"

    # Attempt 1: update_task_progress with 'running'
    T.update_task_progress(task_id, "running", result_data={"info": "should be ignored"})
    t_check1 = T.get_task_by_id(task_id)
    assert t_check1["status"] == "cancelled", "Cancelled task was improperly updated to running"

    # Attempt 2: update_task_progress with 'completed'
    T.update_task_progress(task_id, "completed", result_data={"response": "late response"})
    t_check2 = T.get_task_by_id(task_id)
    assert t_check2["status"] == "cancelled", "Cancelled task was improperly updated to completed"

    # Attempt 3: record_task_execution with 'completed' outcome
    T.record_task_execution(
        task_id=task_id,
        user="operator",
        outcome="completed",
        attempted="run model",
        actual_result="completed inference",
    )
    t_check3 = T.get_task_by_id(task_id)
    assert t_check3["status"] == "cancelled", "record_task_execution improperly overwrote cancelled task to completed"


def test_worker_submit_result_reconciles_conflict_for_terminal_task():
    """Verify that worker.submit_task_result rejects submissions for cancelled tasks with 409 TaskAuthorizationError."""
    t = T.create_task(
        title="Worker inference task",
        description="Late response simulation",
        task_type="inference",
        assigned_worker="desktop-worker-01",
    )
    task_id = t["id"]
    T.cancel_task(task_id)

    # Worker sends result for cancelled task -> must raise TaskAuthorizationError
    with pytest.raises(worker.TaskAuthorizationError) as exc_info:
        worker.submit_task_result(
            task_id=task_id,
            worker_id="desktop-worker-01",
            lease_id=None,
            idempotency_key=f"idem-{uuid.uuid4().hex[:8]}",
            status="success",
            stdout_summary="Delayed model inference stdout",
            result_payload={"response": "Late text output"},
        )
    assert "terminal state 'cancelled'" in str(exc_info.value)
    t_final = T.get_task_by_id(task_id)
    assert t_final["status"] == "cancelled", "Delayed result should not overwrite terminal cancelled task"


# =========================================================================
# 2. F-06: Queue-Wait vs Execution Timeout
# =========================================================================

def test_model_router_queue_timeout_vs_execution_timeout(clean_project):
    """Verify that queue wait uses queue_timeout_seconds and running tasks use timeout_seconds."""
    prov = model_router.WorkerOllamaProvider()
    run = engine.create_run(
        project_id=clean_project["id"],
        actor="operator",
        intake={"objective": "Timeout test run"},
    )
    run_id = run["id"]

    # Case A: Task queued / pending exceeds queue_timeout
    rid_queue = common.new_id("inf")
    t_queue = T.create_task(title="Queued task", description="Waiting in queue", task_type="inference")
    old_time = (datetime.now(timezone.utc) - timedelta(seconds=150)).isoformat()
    with db_transaction() as c:
        c.execute(
            """INSERT INTO model_inference_requests (id, run_id, orch_task_id, purpose, node_id, model,
                         task_id, status, prompt_chars, created_at) VALUES (?,?,?,?,?,?,?,'submitted',?,?)""",
            (rid_queue, run_id, None, "planning", "desktop-worker-01", "qwen2.5:1.5b", t_queue["id"], 50, old_time),
        )

    # Poll with queue_timeout=120, timeout=300
    res_queue = prov.poll(rid_queue, timeout_seconds=300, queue_timeout_seconds=120)
    assert res_queue["status"] == "timeout"
    assert res_queue.get("timeout_type") == "queue_timeout"
    assert res_queue.get("error_class") in ("queue_timeout", "model_timeout")

    # Case B: Task running exceeds execution timeout
    rid_exec = common.new_id("inf")
    t_exec = T.create_task(title="Running task", description="Actively executing", task_type="inference")
    T.update_task_progress(t_exec["id"], "running")
    # Set updated_at to 350 seconds ago
    old_running_time = (datetime.now(timezone.utc) - timedelta(seconds=350)).isoformat()
    with db_transaction() as c:
        c.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (old_running_time, t_exec["id"]))
        c.execute(
            """INSERT INTO model_inference_requests (id, run_id, orch_task_id, purpose, node_id, model,
                         task_id, status, prompt_chars, created_at) VALUES (?,?,?,?,?,?,?,'submitted',?,?)""",
            (rid_exec, run_id, None, "planning", "desktop-worker-01", "qwen2.5:1.5b", t_exec["id"], 50, old_running_time),
        )

    res_exec = prov.poll(rid_exec, timeout_seconds=300, queue_timeout_seconds=600)
    assert res_exec["status"] == "timeout"
    assert res_exec["error_class"] == "model_timeout"


# =========================================================================
# 3. F-06: Orphaned Inference Request Cancellation
# =========================================================================

def test_cancel_run_cancels_in_flight_inference_requests(clean_project):
    """Verify that cancel_run cancels all active model_inference_requests and their tasks."""
    run = engine.create_run(
        project_id=clean_project["id"],
        actor="operator",
        intake={"objective": "Cancel run test"},
    )
    run_id = run["id"]

    prov = model_router.WorkerOllamaProvider()
    rid = prov.submit(
        run_id=run_id,
        orch_task_id=None,
        purpose="planning",
        node_id="desktop-worker-01",
        model="qwen2.5:1.5b",
        prompt="hello world",
    )

    # Confirm submitted
    conn = get_db_connection()
    row = conn.execute("SELECT task_id, status FROM model_inference_requests WHERE id = ?", (rid,)).fetchone()
    conn.close()
    assert row["status"] == "submitted"
    task_id = row["task_id"]

    # Cancel the run
    engine.cancel_run(run_id, actor="operator", reason="User aborted")

    # Verify task cancelled
    t_after = T.get_task_by_id(task_id)
    assert t_after["status"] == "cancelled"

    # Verify model_inference_request marked cancelled
    conn = get_db_connection()
    row_after = conn.execute("SELECT status FROM model_inference_requests WHERE id = ?", (rid,)).fetchone()
    conn.close()
    assert row_after["status"] == "cancelled"


# =========================================================================
# 4. F-05: Claim Verification - Direct Support vs Corroboration
# =========================================================================

def test_claim_verification_requires_independent_sources(clean_project):
    """Verify that a claim backed by 1 source is SUPPORTED, and 2 distinct domains is CORROBORATED."""
    verifier = EvidenceLedger()
    pid = clean_project["id"]

    claim = verifier.record_claim(
        project_id=pid,
        claim_text="The 2026 Sonoma County budget allocated $2.4 billion across municipal services.",
        category="fact",
    )
    claim_id = claim["id"]
    assert claim["verification_status"] == "unverified"

    # Add source 1: sonomacounty.ca.gov
    conn = get_db_connection()
    src1_id = f"src-{uuid.uuid4().hex[:8]}"
    src2_id = f"src-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO research_sources (id, project_id, canonical_url, original_url, title, publisher, quality_score, content_hash, retrieved_at, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (src1_id, pid, "https://sonomacounty.ca.gov/budget/2026", "https://sonomacounty.ca.gov/budget/2026", "Sonoma Budget Summary", "County of Sonoma", 0.95, f"hash-{uuid.uuid4().hex[:8]}", now, now, now),
    )
    conn.execute(
        """INSERT INTO research_sources (id, project_id, canonical_url, original_url, title, publisher, quality_score, content_hash, retrieved_at, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (src2_id, pid, "https://www.pressdemocrat.com/news/county-budget-adopted", "https://www.pressdemocrat.com/news/county-budget-adopted", "Press Democrat Report", "The Press Democrat", 0.88, f"hash-{uuid.uuid4().hex[:8]}", now, now, now),
    )
    conn.commit()
    conn.close()

    # Attach evidence from source 1
    verifier.attach_evidence(
        project_id=pid,
        claim_id=claim_id,
        source_id=src1_id,
        supporting_excerpt="The approved 2026 budget totals $2.4 billion for Sonoma County operations.",
        exact_quote="approved 2026 budget totals $2.4 billion",
    )

    clm_after_src1 = verifier.get_claim(pid, claim_id)
    assert clm_after_src1["verification_status"].lower() == "supported", (
        f"Expected 'supported' for single source, got {clm_after_src1['verification_status']}"
    )

    # Attach evidence from independent source 2 (different domain and publisher)
    verifier.attach_evidence(
        project_id=pid,
        claim_id=claim_id,
        source_id=src2_id,
        supporting_excerpt="Supervisors yesterday ratified the $2.4 billion county budget.",
        exact_quote="$2.4 billion county budget",
    )

    clm_after_src2 = verifier.get_claim(pid, claim_id)
    assert clm_after_src2["verification_status"].lower() == "corroborated", (
        f"Expected 'corroborated' for 2 independent domains, got {clm_after_src2['verification_status']}"
    )


def test_captcha_and_interstitial_rejection():
    """Verify that extraction rejects CAPTCHA and anti-bot challenge pages."""
    captcha_html = """
    <html>
      <head><title>Attention Required! | Cloudflare</title></head>
      <body>
        <h1>Please complete the security check</h1>
        <div class="cf-challenge">
          <p>Please click here if you are not a robot</p>
        </div>
      </body>
    </html>
    """
    reason = extraction.detect_challenge_page(title="Attention Required!", raw_html=captcha_html)
    assert reason is not None, "Failed to detect Cloudflare CAPTCHA challenge"
    assert "captcha" in reason.lower() or "challenge" in reason.lower() or "interstitial" in reason.lower() or "security" in reason.lower()


# =========================================================================
# 5. F-14: Plan Validation & Prevention of False Approval
# =========================================================================

def test_plan_validation_rejects_schema_placeholders():
    """Verify that validate_plan rejects prompt schema example placeholders."""
    plan = {
        "schema": "qc.plan.v1",
        "overview": {
            "objective": "high-level goal description",
            "context": "test",
            "constraints": [],
            "assumptions": [],
        },
        "paths": [{
            "name": "Main Path",
            "rationale": "Direct execution",
            "stages": [{"key": "stage_init", "name": "stage 1 title", "objective": "Initialize"}]
        }],
        "tasks": [{
            "key": "task_1",
            "title": "task 1 name",
            "description": "placeholder",
            "stage": "stage_init",
            "agent_role": "analysis",
            "action": {"type": "controller_step", "step": "research_search"},
            "acceptance": {"kind": "nonempty_text"},
            "depends_on": [],
            "required_capabilities": [],
            "preferred_models": [],
            "allowed_nodes": [],
            "priority": 1,
            "max_attempts": 2,
            "timeout_seconds": 300,
            "expected_outputs": [],
            "approval": "auto",
            "risk": "low"
        }],
        "deliverables": [{
            "key": "deliv_1",
            "name": "deliverable 1 name",
            "type": "report",
            "description": "test deliverable",
            "acceptance_criteria": "Deterministic acceptance criteria.",
            "produced_by": "task_1",
            "stage": "stage_init",
            "terminal": True
        }],
    }
    v = task_graph.validate_plan(plan)
    assert not v["valid"], "Plan with schema placeholders must be rejected"
    assert any("placeholder" in err.lower() for err in v["errors"])


def test_approve_plan_rejects_falsely_marked_valid_plan(clean_project):
    """Verify that approval_manager.approve_plan re-validates and rejects an invalid plan."""
    run = engine.create_run(
        project_id=clean_project["id"],
        actor="operator",
        intake={"objective": "Approve plan validation test"},
    )
    run_id = run["id"]

    # Candidate plan with no compatible node for an inference task
    bad_plan = {
        "schema": "qc.plan.v1",
        "overview": {
            "objective": "Comprehensive economic study of Sonoma County",
            "context": "Context",
            "constraints": [],
            "assumptions": [],
        },
        "paths": [{
            "name": "Main Path",
            "rationale": "Direct execution",
            "stages": [{"key": "stage_init", "name": "Initialization", "objective": "Initialize"}]
        }],
        "tasks": [{
            "key": "task_inf",
            "title": "Perform heavy inference on unavailable model",
            "description": "Inference task",
            "stage": "stage_init",
            "agent_role": "analysis",
            "action": {"type": "inference", "instruction": "Analyze data"},
            "acceptance": {"kind": "nonempty_text"},
            "depends_on": [],
            "required_capabilities": ["nonexistent_capability_xyz"],
            "preferred_models": ["nonexistent_model:999b"],
            "allowed_nodes": ["nonexistent_node"],
            "priority": 1,
            "max_attempts": 2,
            "timeout_seconds": 300,
            "expected_outputs": [],
            "approval": "auto",
            "risk": "low"
        }],
        "deliverables": [{
            "key": "deliv_1",
            "name": "Final Report",
            "type": "report",
            "description": "Output report",
            "acceptance_criteria": "Accepted report criteria.",
            "produced_by": "task_inf",
            "stage": "stage_init",
            "terminal": True
        }],
    }

    # Store revision directly
    rev = planner.store_revision(run_id, bad_plan, "model", "test-agent")
    states.transition(run_id, "ANALYZING", "analyzing_started", "test")
    states.transition(run_id, "PLANNING", "planning_started", "test")
    states.transition(run_id, "AWAITING_PLAN_APPROVAL", "plan_proposed", "test", "awaiting approval")

    # Now attempt approval with an empty or non-matching fleet
    with patch("app.services.orchestration.model_router.node_inventory", return_value=[]):
        with pytest.raises(common.OrchestrationError) as exc_info:
            approval_manager.approve_plan(run_id, actor="operator", revision_id=rev["id"])
        assert "cannot run on the current fleet" in str(exc_info.value) or "Plan is not valid" in str(exc_info.value)


def test_planning_checkpoint_concurrency_race_resolved(clean_project):
    """Verify that concurrent scheduler ticks during planning intake/submission do not trigger a planning race failure."""
    import threading
    import time

    run = engine.create_run(
        project_id=clean_project["id"],
        actor="operator",
        intake={"objective": "Develop comprehensive software service for telemetry monitoring"},
    )
    run_id = run["id"]

    stop_ticker = False
    tick_errors = []

    def scheduler_loop():
        while not stop_ticker:
            try:
                # Concurrent engine tick querying runs
                current_run = common.get_run(run_id)
                planner.advance_planning(current_run)
            except Exception as e:
                tick_errors.append(str(e))
            time.sleep(0.01)

    mock_nodes = [{
        "id": "desktop-worker-01",
        "is_worker": True,
        "enabled": True,
        "authenticated": True,
        "health": "online",
        "status": "online",
        "capabilities": ["inference", "python_execution"],
        "models": [{"name": "qwen2.5:1.5b", "status": "verified"}],
    }]
    with patch("app.services.orchestration.model_router.node_inventory", return_value=mock_nodes), \
         patch("app.services.orchestration.planner._is_model_probed", return_value=True), \
         patch("app.services.orchestration.research_checkpoint.execute_research_checkpoint", return_value={"id": "chk-1"}), \
         patch("app.services.orchestration.model_router.get_provider") as mock_prov_get:
            mock_prov = MagicMock()
            mock_prov.submit.return_value = "inf-test-race-req"
            mock_prov.poll.return_value = {"status": "submitted", "task_status": "running"}
            mock_prov_get.return_value = mock_prov

            t_thread = threading.Thread(target=scheduler_loop)
            t_thread.daemon = True
            t_thread.start()

            try:
                res = planner.generate_plan(
                    run_id=run_id,
                    actor="operator",
                    mode="model",
                    planner_model="qwen2.5:1.5b",
                )
                assert res["state"] == "PLANNING"
                assert res["request_id"]
                time.sleep(0.05)
            finally:
                stop_ticker = True
                t_thread.join(timeout=2.0)

    assert not tick_errors, f"Scheduler tick raised errors during concurrent planning: {tick_errors}"
    final_run = common.get_run(run_id)
    # The run must remain in PLANNING (not prematurely dropped into DRAFT or BLOCKED or failed)
    assert final_run["state"] == "PLANNING", f"Run fell into state '{final_run['state']}', expected 'PLANNING'"
    ck = planner.latest_checkpoint(run_id, "planning")
    assert ck is not None, "Checkpoint was not found for planning run"
    assert ck["request_id"] == res["request_id"]

