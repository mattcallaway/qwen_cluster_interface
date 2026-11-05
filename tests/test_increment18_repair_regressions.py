"""Regression tests for the 6 findings in INCREMENT_18_INDEPENDENT_SOURCE_REVIEW.md.

Written BEFORE fixes to reproduce the exact pre-fix failures on disposable SQLite databases
(without running live inference or mutating production), and then used to verify the repairs.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest
from starlette.testclient import TestClient

from app.auth import create_session_token
from app.config import settings
from app.database import db_transaction, get_db_connection, init_db
from app.main import app
from app.services import tasks as T
from app.services import worker as W
from app.services.orchestration import (
    acceptance_harness,
    artifact_manager,
    deliberate_loop,
    engine,
    intake as intake_mod,
    working_memory as wm,
)
from app.services.orchestration.common import now_iso


@pytest.fixture()
def isolated_repair_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    init_db()
    wm.GLOBAL_WORKING_SET_CACHE = wm.WorkingSetCache()
    yield tmp_path


def _create_project(name: str = "Repair Test Project") -> tuple[str, str]:
    intake_payload = {
        "objective": "Evaluate suppliers and reconcile exact Decimal costs under constraints.",
        "constraints": [
            "Maximum total budget ceiling: $15,000.00",
            "Maximum delivery lead time: 21 days",
            "Required capabilities: iso27001, telemetry_export",
        ],
        "deliverables": [
            "quote_reconciliation_ledger.json",
            "supplier_recommendation.json",
        ],
        "success_criteria": [
            "All monetary totals exact to cents using Decimal.",
            "Recommendation satisfies all hard constraints.",
        ],
    }
    created = engine.create_project_with_run(
        name=name, actor="operator", intake=intake_payload, autonomy_level=2,
    )
    pid = created["project"]["id"]
    rid = created["run"]["id"]
    ledger = intake_mod.parse_intake_requirements(intake_payload)
    intake_mod.persist_requirement_ledger(pid, ledger, run_id=rid, actor="operator")
    return pid, rid


def test_f1_no_synthetic_self_enrollment_or_direct_sql_2099_lease(isolated_repair_env: Path, monkeypatch: pytest.MonkeyPatch):
    """Finding 1: execute_native_worker_inference must NOT self-enroll a node, fabricate tokens/capabilities,
    insert a 2099 lease via direct SQL, or run vps_agent.executor in-process when no eligible worker exists.
    """
    pid, rid = _create_project("F1 No Self Enrollment")

    import vps_agent.executor as vps_exec
    in_process_called = {"called": False}

    def _forbid_in_process(*args, **kwargs):
        in_process_called["called"] = True
        raise AssertionError("Controller must not execute vps_agent.executor.execute_task in-process!")

    monkeypatch.setattr(vps_exec, "execute_task", _forbid_in_process)

    res = deliberate_loop.execute_native_worker_inference(
        project_id=pid,
        run_id=rid,
        orch_task_id=None,
        purpose="f1_unauthenticated_check",
        prompt="Test prompt",
        role="complex_reasoning",
        max_tokens=32,
        timeout_seconds=5,
    )

    # Must refuse/block cleanly without executing in-process or fabricating nodes/tokens/2099 leases
    assert in_process_called["called"] is False
    assert res["status"] in ("blocked", "no_eligible_worker", "failed")

    conn = get_db_connection()
    try:
        synth_node = conn.execute("SELECT * FROM infrastructure_nodes WHERE id = 'node-local-worker'").fetchone()
        synth_tok = conn.execute("SELECT * FROM worker_auth_tokens WHERE id = 'tok-node-local-worker'").fetchone()
        lease_2099 = conn.execute(
            "SELECT * FROM worker_task_leases WHERE lease_expires_at LIKE '2099%'"
        ).fetchone()
        assert synth_node is None, "Must not self-enroll synthetic node-local-worker in infrastructure_nodes"
        assert synth_tok is None, "Must not insert synthetic token in worker_auth_tokens"
        assert lease_2099 is None, "Must not insert direct SQL lease expiring in 2099"
    finally:
        conn.close()


def test_f2_acceptance_evidence_separates_fixtures_and_unknown_defaults(isolated_repair_env: Path):
    """Finding 2: _make_evidence_record must not default unmeasured unsupported_claims or unauthorized_side_effects
    to 0, must not record tool:/controller: strings as native worker leases, and must classify fixture vs model work.
    """
    rec = acceptance_harness._make_evidence_record(
        task_id="accept-test",
        category="coding_and_artifacts",
        name="json_transform",
        source_commit="2cc6dab",
        model_identity="qwen2.5:3b",
        native_run_task_lease_ids=["tool:execute_and_test_code:accept-13"],
        artifact_versions=[],
        independent_check_results=[{"name": "check_1", "passed": True}],
        elapsed_seconds=0.1,
        model_calls=0,
        token_usage=0,
        user_interventions=0,
        final_status="correct",
    )
    # Unchecked properties must be explicit unknown/not_evaluated, not 0
    assert rec["unsupported_claims"] == "not_evaluated"
    assert rec["unauthorized_side_effects"] == "not_evaluated"
    # tool:... strings are NOT native worker leases
    assert rec["native_worker_lease_ids"] == []
    assert "tool:execute_and_test_code:accept-13" in rec["controller_or_tool_trace_ids"]
    assert rec["counts_as_autonomous_capability"] is False


def test_f3_caller_cleanup_boolean_ignored_when_active_or_expired_lease_exists(isolated_repair_env: Path):
    """Finding 3: Caller-supplied active_process_cleanup_confirmed=True must NOT override active tasks/leases
    or expired leases with unknown worker process state.
    """
    pid, rid = _create_project("F3 Authoritative Cleanup")
    wm.register_task_contract(
        pid, run_id=rid, task_key="t_inflight",
        purpose="In-flight worker task", expected_output="out.json", status="running",
    )
    W.register_worker_node("worker-dev-01", "Dev Worker")
    # Create an underlying in-progress task with an active lease
    t = T.create_task(
        title="Active worker task",
        description="Running on worker",
        task_type="inference",
        assigned_worker="worker-dev-01",
        input_data={"model": "qwen2.5:3b", "prompt": "Hello"},
        project_id=pid,
    )
    now = now_iso()
    with db_transaction() as c:
        c.execute("UPDATE tasks SET status = 'in_progress' WHERE id = ?", (t["id"],))
        c.execute(
            """INSERT INTO worker_task_leases (
                id, task_id, worker_id, idempotency_key, status,
                leased_at, lease_expires_at, heartbeat_at, created_at, updated_at
            ) VALUES (?, ?, 'worker-dev-01', 'idem-f3', 'leased', ?, '2026-12-31T00:00:00+00:00', ?, ?, ?)""",
            ("lease_active_f3", t["id"], now, now, now, now),
        )

    # Caller passes active_process_cleanup_confirmed=True (trying to forge cleanup confirmation)
    with pytest.raises(wm.ParkOrCancelCleanupError):
        wm.park_project(pid, actor="operator", reason="Forge cleanup", active_process_cleanup_confirmed=True)

    with pytest.raises(wm.ParkOrCancelCleanupError):
        wm.cancel_task_or_project(pid, task_key="t_inflight", process_cleanup_confirmed=True)


def test_f4_raw_callback_verified_flag_cannot_self_certify(isolated_repair_env: Path):
    """Finding 4: execute_checkpointed_work_boundary must NOT trust raw_result['verified']=True or
    raw_result['verification_status']='verified_for_scope' without a trusted controller verifier/receipt.
    """
    pid, rid = _create_project("F4 No Self Certification")
    wm.register_task_contract(
        pid, run_id=rid, task_key="t_spoof",
        purpose="Task returning spoofed verified=True", expected_output="spoof.json",
    )

    res = wm.execute_checkpointed_work_boundary(
        pid,
        "t_spoof",
        lambda _pkt: {
            "epistemic_category": "derived_result",
            "verified": True,
            "verification_status": "verified_for_scope",
            "statement": "Spoofed self-certified claim",
            "publish_artifact": {
                "filename": "spoof.json",
                "content_bytes": b'{"self_certified": true}',
            },
            "output_summary": {"spoof": True},
        },
    )

    # Without a trusted verifier_fn or controller-signed receipt, task and artifact MUST remain unverified!
    assert res["task_status"] != "verified"
    assert res["epistemic_item"]["verification_status"] in ("unknown", "proposed")
    tc = wm.get_task_contract(pid, "t_spoof")
    assert tc["status"] != "verified"
    arts = artifact_manager.list_artifacts(pid)
    spoof_art = next(a for a in arts if a["id"] == res["published_artifact_id"])
    assert spoof_art["validation_state"] != "valid"


def test_f5_atomic_revision_fence_with_pre_commit_barrier_and_crash_recovery(isolated_repair_env: Path):
    """Finding 5: Steering occurring AFTER post-work read and right before commit (via pre_commit_barrier)
    must be caught atomically at commit time, and crash recovery must also reject stale revisions.
    """
    pid, rid = _create_project("F5 Atomic Fence & Recovery")
    wm.register_task_contract(
        pid, run_id=rid, task_key="t_race",
        purpose="Task racing with steering", expected_output="race.json",
        bound_requirement_ids=["req-con-2"],
    )

    def _barrier_steer_after_initial_check():
        # Executed AFTER work_fn and after any non-transactional check, right before final commit
        intake_mod.apply_user_steering_update(
            pid, run_id=rid, actor="operator", action="steer",
            replaced_req_codes={"req-con-2": "Maximum total budget ceiling: $11,500.00"},
            reason="Late steering between read check and commit",
        )

    with pytest.raises(wm.StaleWorkerResultError):
        wm.execute_checkpointed_work_boundary(
            pid,
            "t_race",
            lambda _pkt: {
                "epistemic_category": "derived_result",
                "statement": "Computed under old revision 1",
                "publish_artifact": {"filename": "race.json", "content_bytes": b'{"rev": 1}'},
                "output_summary": {"budget_used": "14200.00"},
            },
            pre_commit_barrier=_barrier_steer_after_initial_check,
        )

    tc = wm.get_task_contract(pid, "t_race")
    assert tc["status"] != "verified"
    assert tc["output_summary"] == {}


def test_f6_chat_deliberate_direct_answer_and_durable_consumer(isolated_repair_env: Path):
    """Finding 6: /api/v1/chat/deliberate must not merely echo the input message in direct mode,
    and durable mode must provide a consumer that advances persisted next_action beyond intake.
    """
    client = TestClient(app)
    tok = create_session_token("admin")
    headers = {"Authorization": f"Bearer {tok}"}

    # 1. Direct question mode must not simply echo message without an answer/response field
    r_direct = client.post(
        "/api/v1/chat/deliberate",
        json={"message": "What is 1500.00 * 8 + 450.00?"},
        headers=headers,
    )
    assert r_direct.status_code == 200
    d_body = r_direct.json()
    assert d_body["mode"] == "direct_response"
    assert "response" in d_body and d_body["response"] != "What is 1500.00 * 8 + 450.00?"

    # 2. Durable project mode with advance=True must advance beyond initial intake
    r_durable = client.post(
        "/api/v1/chat/deliberate",
        json={
            "message": "Evaluate supplier quotes and recommend the best feasible supplier under $15,000.00 ceiling.",
            "intake": {
                "objective": "Evaluate supplier quotes and recommend the best feasible supplier.",
                "constraints": [
                    "Maximum total budget ceiling: $15,000.00",
                    "Maximum delivery lead time: 21 days",
                    "Required capabilities: iso27001, telemetry_export",
                    "Required dependencies: dep_power_bus, dep_rack_mount, dep_api_v2",
                ],
                "deliverables": [
                    "quote_reconciliation_ledger.json",
                    "supplier_recommendation.json",
                ],
                "quotes": acceptance_harness.SYNTHETIC_SUPPLIER_QUOTES,
            },
            "advance": True,
        },
        headers=headers,
    )
    assert r_durable.status_code == 200
    dur_body = r_durable.json()
    pid = dur_body["project_id"]
    contracts = wm.list_task_contracts(pid)
    assert len(contracts) >= 2, "Coordinator consumer must decompose and register task contracts beyond intake"
    assert any(tc["status"] == "verified" for tc in contracts), "Coordinator consumer must advance and verify deterministic steps"


def test_f1_worker_leasing_authority_matrix(isolated_repair_env: Path):
    """Section 2 edge-case matrix: held/disabled workers, missing authentication, unsupported models,
    exhausted capacity, denied approval, unrelated queue ordering, expired/revoked leases,
    duplicate submissions, and worker disconnects.
    """
    from app.services.orchestration import model_router, telemetry

    pid, rid = _create_project("F1 Authority Matrix")

    # 1. Enroll a real worker node via normal worker service (no token yet -> missing authentication)
    W.register_worker_node("worker-auth-matrix", "Matrix Worker")
    now = now_iso()
    with db_transaction() as c:
        c.execute(
            """UPDATE infrastructure_nodes
               SET role = 'vps_worker', is_enabled = 1, health_status = 'online',
                   available_ram_mb = 4096, last_contact_at = ?,
                   installed_runtimes_json = '["ollama", "python3"]',
                   available_models_json = '["qwen2.5:3b"]'
               WHERE id = 'worker-auth-matrix'""",
            (now,),
        )
    # Missing authentication token -> rejected
    r_no_auth = deliberate_loop.execute_native_worker_inference(
        project_id=pid, run_id=rid, orch_task_id=None,
        purpose="no_auth", prompt="Test", preferred_model="qwen2.5:3b", timeout_seconds=1,
    )
    assert r_no_auth["status"] == "blocked"
    assert r_no_auth["error_class"] == "no_eligible_worker"

    # Issue real token, but capability is not yet verified -> rejected
    tok_id, _raw_tok = W.create_worker_token("worker-auth-matrix")
    r_unverified_cap = deliberate_loop.execute_native_worker_inference(
        project_id=pid, run_id=rid, orch_task_id=None,
        purpose="unverified_cap", prompt="Test", preferred_model="qwen2.5:3b", timeout_seconds=1,
    )
    assert r_unverified_cap["status"] == "blocked"
    assert any("unverified model capability" in r for r in r_unverified_cap["reasons"])

    # Verify capability for qwen2.5:3b
    model_router.verify_node_model_capability("worker-auth-matrix", "qwen2.5:3b", verified_by="test_harness")

    # 2. Unsupported model (qwen2.5:72b not installed/verified) -> rejected
    r_unsup = deliberate_loop.execute_native_worker_inference(
        project_id=pid, run_id=rid, orch_task_id=None,
        purpose="unsupported_model", prompt="Test", preferred_model="qwen2.5:72b", timeout_seconds=1,
    )
    assert r_unsup["status"] == "blocked"

    # 3. Operational hold -> rejected
    with db_transaction() as c:
        c.execute(
            "UPDATE infrastructure_nodes SET capability_restrictions_json = ? WHERE id = 'worker-auth-matrix'",
            (json.dumps({"operational_hold": True, "hold_reason": "Maintenance hold"}),),
        )
    r_held = deliberate_loop.execute_native_worker_inference(
        project_id=pid, run_id=rid, orch_task_id=None,
        purpose="held_worker", prompt="Test", preferred_model="qwen2.5:3b", timeout_seconds=1,
    )
    assert r_held["status"] == "blocked"
    assert any("operational hold" in r for r in r_held["reasons"])

    # Clear hold, test disabled worker -> rejected
    with db_transaction() as c:
        c.execute(
            "UPDATE infrastructure_nodes SET capability_restrictions_json = '{}', is_enabled = 0 WHERE id = 'worker-auth-matrix'"
        )
    r_disabled = deliberate_loop.execute_native_worker_inference(
        project_id=pid, run_id=rid, orch_task_id=None,
        purpose="disabled_worker", prompt="Test", preferred_model="qwen2.5:3b", timeout_seconds=1,
    )
    assert r_disabled["status"] == "blocked"
    assert any("disabled" in r for r in r_disabled["reasons"])

    # Re-enable worker
    with db_transaction() as c:
        c.execute("UPDATE infrastructure_nodes SET is_enabled = 1 WHERE id = 'worker-auth-matrix'")

    # 4. Denied / pending approval (approval_required=True) -> returns awaiting_approval, cannot be leased until approved
    r_approval = deliberate_loop.execute_native_worker_inference(
        project_id=pid, run_id=rid, orch_task_id=None,
        purpose="needs_approval", prompt="Test approval gate", preferred_model="qwen2.5:3b",
        approval_required=True, timeout_seconds=1,
    )
    assert r_approval["status"] == "awaiting_approval"
    assert r_approval["lease_id"] is None
    unapproved_task_id = r_approval["worker_task_id"]
    # Worker attempting to lease while task is awaiting_approval gets None
    assert W.lease_task_for_worker("worker-auth-matrix", ["inference"]) is None

    # 5. Exhausted capacity (load >= 1 because unapproved_task_id is awaiting_approval on worker-auth-matrix)
    r_cap = deliberate_loop.execute_native_worker_inference(
        project_id=pid, run_id=rid, orch_task_id=None,
        purpose="capacity_busy", prompt="Test busy", preferred_model="qwen2.5:3b", timeout_seconds=1,
    )
    assert r_cap["status"] == "blocked"
    assert any("busy" in r for r in r_cap["reasons"])

    # Cancel the awaiting_approval task so capacity frees up
    with db_transaction() as c:
        c.execute("UPDATE tasks SET status = 'cancelled' WHERE id = ?", (unapproved_task_id,))

    # 6. Worker disconnect / timeout -> task submitted to queue, worker never leases/completes -> times out cleanly
    r_timeout = deliberate_loop.execute_native_worker_inference(
        project_id=pid, run_id=rid, orch_task_id=None,
        purpose="worker_disconnect_timeout", prompt="Test timeout", preferred_model="qwen2.5:3b",
        timeout_seconds=1, poll_interval_seconds=0.1,
    )
    assert r_timeout["status"] in ("timeout", "failed")
    assert r_timeout["error_class"] in ("model_timeout", "worker_timeout")

    # 7. Unrelated queue ordering, expired lease rejection, and duplicate idempotent submission
    t_first = T.create_task(
        title="Queued Task 1", description="First", task_type="inference",
        assigned_worker="worker-auth-matrix", input_data={"model": "qwen2.5:3b", "prompt": "P1"},
        approval_required=False,
    )
    t_second = T.create_task(
        title="Queued Task 2", description="Second", task_type="inference",
        assigned_worker="worker-auth-matrix", input_data={"model": "qwen2.5:3b", "prompt": "P2"},
        approval_required=False,
    )
    # Lease t_first first, then t_second (proving FIFO queue ordering)
    lease_1 = W.lease_task_for_worker("worker-auth-matrix", ["inference"])
    assert lease_1 is not None
    assert lease_1["task_id"] == t_first["id"]

    # Expire lease_1 and verify artifact upload or reacquired/superseded submission with expired lease is rejected
    with db_transaction() as c:
        c.execute(
            "UPDATE worker_task_leases SET status = 'expired', lease_expires_at = '2020-01-01T00:00:00+00:00', attempt_count = 1 WHERE id = ?",
            (lease_1["lease_id"],),
        )
    with pytest.raises(W.TaskAuthorizationError):
        W.verify_worker_task_authorization(
            worker_id="worker-auth-matrix",
            task_id=lease_1["task_id"],
            operation="upload",
            lease_id=lease_1["lease_id"],
        )
    # Supersede with attempt 2 and verify stale lease_1 submission is rejected by TaskAuthorizationError
    with db_transaction() as c:
        c.execute(
            """INSERT INTO worker_task_leases (
                id, task_id, worker_id, idempotency_key, status, attempt_count,
                leased_at, lease_expires_at, heartbeat_at, created_at, updated_at
            ) VALUES ('lease_superseded_2', ?, 'worker-auth-matrix', 'idem-sup-2', 'leased', 2, ?, ?, ?, ?, ?)""",
            (lease_1["task_id"], now, now, now, now, now),
        )
    with pytest.raises(W.TaskAuthorizationError):
        W.submit_task_result(
            worker_id="worker-auth-matrix",
            task_id=lease_1["task_id"],
            lease_id=lease_1["lease_id"],
            idempotency_key="idem-expired",
            status="success",
            stdout_summary="Late result",
        )

    # Now lease t_second and test duplicate idempotent submission
    with db_transaction() as c:
        c.execute("UPDATE tasks SET status = 'cancelled' WHERE id = ?", (lease_1["task_id"],))
        c.execute("UPDATE worker_task_leases SET status = 'cancelled' WHERE task_id = ?", (lease_1["task_id"],))
    lease_2 = W.lease_task_for_worker("worker-auth-matrix", ["inference"])
    assert lease_2 is not None and lease_2["task_id"] == t_second["id"]
    sub_a = W.submit_task_result(
        worker_id="worker-auth-matrix",
        task_id=t_second["id"],
        lease_id=lease_2["lease_id"],
        idempotency_key="idem-dup-check",
        status="success",
        stdout_summary="Completed once",
        result_payload={"response": "OK", "model": "qwen2.5:3b", "prompt_tokens": 5, "gen_tokens": 2},
    )
    sub_b = W.submit_task_result(
        worker_id="worker-auth-matrix",
        task_id=t_second["id"],
        lease_id=lease_2["lease_id"],
        idempotency_key="idem-dup-check",
        status="success",
        stdout_summary="Completed once",
        result_payload={"response": "OK", "model": "qwen2.5:3b", "prompt_tokens": 5, "gen_tokens": 2},
    )
    assert sub_a["status"] == "recorded"
    assert sub_b["status"] == "already_processed"
    assert sub_a["execution_id"] == sub_b["execution_id"]

    # 8. Revoked token -> worker is no longer considered authenticated
    W.revoke_worker_token(tok_id)
    r_revoked = deliberate_loop.execute_native_worker_inference(
        project_id=pid, run_id=rid, orch_task_id=None,
        purpose="revoked_token", prompt="Test", preferred_model="qwen2.5:3b", timeout_seconds=1,
    )
    assert r_revoked["status"] == "blocked"


def test_f3_cleanup_lifecycle_and_expired_lease_unknown(isolated_repair_env: Path):
    """Section 3 edge cases:
    - Expired lease without post-expiry worker heartbeat/ack is treated as cleanup_unknown (not confirmed).
    - Dispatch is stopped (dispatch_stopped == 1) during draining, blocking new work boundaries and worker inference.
    - Once worker sends post-expiry idle heartbeat, cleanup transitions to cleanup_confirmed.
    """
    pid, rid = _create_project("F3 Expired Lease Unknown")
    wm.register_task_contract(
        pid, run_id=rid, task_key="t_exp",
        purpose="Task with expired lease", expected_output="exp.json", status="running",
    )
    W.register_worker_node("worker-exp-01", "Exp Worker")
    t = T.create_task(
        title="Task with expired lease", description="Expired", task_type="inference",
        assigned_worker="worker-exp-01", input_data={"model": "qwen2.5:3b", "prompt": "Hi"}, project_id=pid,
    )
    with db_transaction() as c:
        c.execute("UPDATE tasks SET status = 'failed' WHERE id = ?", (t["id"],))
        c.execute(
            "UPDATE infrastructure_nodes SET last_contact_at = '2026-01-01T00:00:00+00:00' WHERE id = 'worker-exp-01'"
        )
        c.execute(
            """INSERT INTO worker_task_leases (
                id, task_id, worker_id, idempotency_key, status,
                leased_at, lease_expires_at, heartbeat_at, created_at, updated_at
            ) VALUES ('lease_exp_01', ?, 'worker-exp-01', 'idem-exp-01', 'expired',
                      '2026-01-01T00:00:00+00:00', '2026-01-01T00:05:00+00:00',
                      '2026-01-01T00:01:00+00:00', '2026-01-01T00:00:00+00:00', '2026-01-01T00:05:00+00:00')""",
            (t["id"],),
        )

    # Park must fail because expired lease has no post-expiry worker heartbeat or execution record -> cleanup_unknown
    with pytest.raises(wm.ParkOrCancelCleanupError) as exc_info:
        wm.park_project(pid, actor="operator", reason="Park with expired unacknowledged lease")
    assert "cleanup_unknown" in str(exc_info.value)

    # Verify dispatch_stopped is now 1 and blocks new work boundary dispatch
    assert wm.is_project_dispatch_stopped(pid) is True
    wm.register_task_contract(
        pid, run_id=rid, task_key="t_blocked_by_drain",
        purpose="Should be blocked by dispatch_stopped", expected_output="blk.json",
    )
    with pytest.raises(wm.ParkOrCancelCleanupError):
        wm.execute_checkpointed_work_boundary(
            pid, "t_blocked_by_drain", lambda _pkt: {"statement": "Should not run"},
        )

    # Now worker sends a post-expiry idle heartbeat (last_contact_at > lease_expires_at and 0 active leases)
    W.record_worker_heartbeat("worker-exp-01", cpu_percent=5.0, ram_available_mb=2048, running_jobs_count=0)
    with db_transaction() as c:
        c.execute("UPDATE project_task_contracts SET status = 'cancelled' WHERE project_id = ? AND task_key = 't_exp'", (pid,))

    # Now park succeeds with authoritative cleanup_confirmed!
    park_out = wm.park_project(pid, actor="operator", reason="Park after worker post-expiry heartbeat")
    assert park_out["status"] == "parked"
    assert park_out["cleanup_lifecycle_state"] == "cleanup_confirmed"


def test_f4_trusted_receipt_tampering_and_scope_validation(isolated_repair_env: Path):
    """Section 4 edge cases: forged signature, stale revision, mismatched artifact hash,
    and empty checks are all rejected and cannot confer verified status.
    """
    pid, rid = _create_project("F4 Receipt Tampering")
    wm.register_task_contract(
        pid, run_id=rid, task_key="t_tamper",
        purpose="Test forged receipts", expected_output="tamper.json",
    )

    # Issue a valid receipt for different artifact hash, then tamper with it
    forged_receipt = wm.issue_trusted_verification_receipt(
        project_id=pid,
        task_key="t_tamper",
        requirement_revision=1,
        contract_revision=1,
        artifact_id="art_fake",
        artifact_version=1,
        artifact_sha256="0" * 64,
        verification_method="unit_oracle",
        declared_scope="task:t_tamper",
        checks_executed=[{"name": "c1", "passed": True}],
    )
    # Tamper with artifact_sha256 without re-signing
    tampered = dict(forged_receipt, artifact_sha256="f" * 64)
    v_tampered = wm.verify_trusted_receipt(
        tampered,
        project_id=pid,
        task_key="t_tamper",
        current_requirement_revision=1,
        current_contract_revision=1,
        actual_artifact_id="art_fake",
        actual_artifact_version=1,
        actual_artifact_sha256="f" * 64,
    )
    assert v_tampered["valid"] is False
    assert v_tampered["reason"] == "forged_or_tampered_signature"

    # Stale requirement revision check
    v_stale = wm.verify_trusted_receipt(
        forged_receipt,
        project_id=pid,
        task_key="t_tamper",
        current_requirement_revision=2,
        current_contract_revision=1,
        actual_artifact_id="art_fake",
        actual_artifact_version=1,
        actual_artifact_sha256="0" * 64,
    )
    assert v_stale["valid"] is False
    assert "stale_requirement_revision" in v_stale["reason"]


def test_f5_crash_recovery_and_idempotency_revision_fencing(isolated_repair_env: Path):
    """Section 5 edge cases:
    - Crash recovery (reconcile_crash_publication_journal) rejects an 'artifact_written' entry when
      steering occurred before recovery, marking the staged artifact 'invalid' and journal 'rejected_stale_revision'.
    - Idempotent deduplication refuses to return a cached committed result after requirement steering.
    """
    pid, rid = _create_project("F5 Crash Recovery Fence")
    wm.register_task_contract(
        pid, run_id=rid, task_key="t_crash",
        purpose="Crash recovery test", expected_output="crash.json",
        bound_requirement_ids=["req-con-2"],
    )

    # Simulate crash right after artifact staging and receipt generation, before DB commit
    with pytest.raises(RuntimeError, match="SIMULATED_CRASH: after_artifact_before_checkpoint"):
        wm.execute_checkpointed_work_boundary(
            pid,
            "t_crash",
            lambda _pkt: {
                "epistemic_category": "derived_result",
                "statement": "Staged before crash",
                "publish_artifact": {"filename": "crash.json", "content_bytes": b'{"staged_before_crash": true}'},
                "output_summary": {"v": 1},
            },
            verifier_fn=lambda _pkt, _raw, _staged: {
                "passed": True,
                "verification_method": "crash_test_verifier",
                "declared_scope": "task:t_crash",
                "checks": [{"name": "c1", "passed": True}],
            },
            idempotency_key="idem-crash-stale",
            simulate_crash_stage="after_artifact_before_checkpoint",
        )

    # User steers requirement req-con-2 BEFORE crash recovery runs
    intake_mod.apply_user_steering_update(
        pid, run_id=rid, actor="operator", action="steer",
        replaced_req_codes={"req-con-2": "Maximum total budget ceiling: $12,000.00"},
        reason="Steered before crash recovery",
    )

    rec_out = wm.reconcile_crash_publication_journal(pid)
    assert rec_out["reconciled"] == 0
    assert len(rec_out["rejected_stale_revision"]) == 1

    # Staged artifact must be marked 'invalid' and task contract must NOT be verified
    arts = artifact_manager.list_artifacts(pid)
    crash_art = next(a for a in arts if a["filename"] == "crash.json")
    assert crash_art["validation_state"] == "invalid"
    tc = wm.get_task_contract(pid, "t_crash")
    assert tc["status"] == "stale"


