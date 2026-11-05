"""
Increment R2 regression suite: Provenance, Acceptance, and Truthful Status
(AUD-04, AUD-07, AUD-10c).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.config import settings
from app.database import get_db_connection, init_db
from app.services import deliverables as deliverables_service
from app.services import metrics as metrics_service
from app.services import projects as projects_service
from app.services import tasks as tasks_service
from app.services import worker as worker_service
from app.services.artifacts.service import ReuseId, ReuseStatus
from app.services.orchestration import (
    deliberate_loop,
    engine,
    intake as intake_mod,
    protected_repo_verifier,
    working_memory as wm,
)
from app.services.orchestration.model_router import WorkerOllamaProvider
from app.services.projects import ValidationError


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    init_db()
    wm.GLOBAL_WORKING_SET_CACHE = wm.WorkingSetCache()
    protected_repo_verifier.configure_protected_repo_verifier(None)
    yield tmp_path
    protected_repo_verifier.configure_protected_repo_verifier(None)


def test_aud04_no_synthetic_direct_lease_or_expired_submit_or_unleased_inference_poll(isolated_db: Path):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    past = "2020-01-01T00:00:00Z"

    proj = projects_service.create_project(
        name="R2 Test Project",
        owner="operator",
        objective="Test R2 worker provenance",
        status="active",
        actor="operator",
    )
    project_id = proj["id"]

    conn = get_db_connection()
    conn.execute(
        """INSERT INTO infrastructure_nodes (
               id, name, role, hostname, ip_address, tailscale_status,
               health_status, is_enabled, created_at, updated_at
           ) VALUES ('node-w1', 'node-w1', 'vps_worker', 'node-w1', '127.0.0.1', 'connected', 'online', 1, ?, ?)""",
        (now, now),
    )
    conn.execute(
        """INSERT INTO worker_auth_tokens (id, node_id, token_hash, token_prefix, created_at, revoked)
           VALUES ('tok-w1', 'node-w1', 'hash-w1', 'qcw_w1', ?, 0)""",
        (now,),
    )
    conn.execute(
        """INSERT INTO tasks (
               id, project_id, title, description, task_type, status,
               assigned_worker, created_at, updated_at
           ) VALUES ('task-unleased', ?, 'Unleased Task', 'No lease row', 'inference', 'running', 'node-w1', ?, ?)""",
        (project_id, now, now),
    )
    conn.commit()

    # 1a. verify_worker_task_authorization must reject when no worker_task_leases row exists
    with pytest.raises(worker_service.TaskAuthorizationError) as exc_info:
        worker_service.verify_worker_task_authorization(
            "node-w1", "task-unleased", operation="submit", conn=conn
        )
    assert exc_info.value.status_code in (403, 409)

    # 1b. Expired lease must be rejected on submit (409) even if no other worker reacquired it
    conn.execute(
        """INSERT INTO tasks (
               id, project_id, title, description, task_type, status,
               assigned_worker, created_at, updated_at
           ) VALUES ('task-expired', ?, 'Expired Lease Task', 'Expired', 'inference', 'running', 'node-w1', ?, ?)""",
        (project_id, now, now),
    )
    conn.execute(
        """INSERT INTO worker_task_leases (
               id, task_id, worker_id, idempotency_key, status,
               leased_at, lease_expires_at, heartbeat_at, created_at, updated_at
           ) VALUES ('lease-exp-1', 'task-expired', 'node-w1', 'idem-exp-1', 'active', ?, ?, ?, ?, ?)""",
        (past, past, past, past, past),
    )
    conn.commit()
    with pytest.raises(worker_service.TaskAuthorizationError) as exc_exp:
        worker_service.verify_worker_task_authorization(
            "node-w1", "task-expired", operation="submit", lease_id="lease-exp-1", conn=conn
        )
    assert exc_exp.value.status_code == 409
    conn.close()

    # 1c. Manual record_task_execution cannot mark a worker/inference task 'completed' without a verified lease
    with pytest.raises(ValidationError):
        tasks_service.record_task_execution(
            task_id="task-unleased",
            outcome="completed",
            result_summary="Manually marked completed without worker lease",
            actor="operator",
        )

    # 1d. WorkerOllamaProvider.poll must reject a task whose SQLite status was forced to 'completed' without lease/execution
    provider = WorkerOllamaProvider()
    req_id = provider.submit(
        run_id=None,
        orch_task_id=None,
        purpose="task",
        node_id="node-w1",
        model="qwen2.5:3b",
        prompt="Test prompt",
    )
    conn = get_db_connection()
    req_row = conn.execute("SELECT task_id FROM model_inference_requests WHERE id = ?", (req_id,)).fetchone()
    conn.execute(
        """UPDATE tasks SET status = 'completed', result_data_json = '{"response": "forged"}'
           WHERE id = ?""",
        (req_row["task_id"],),
    )
    conn.commit()
    conn.close()

    resp = provider.poll(req_id)
    assert resp is not None
    assert resp["status"] == "failed"
    assert resp["error_class"] == "unverified_worker_provenance"

    # 1e. Checkpointed work boundary and worker authorization must reject:
    # - synthetic `lease-direct-*` lease IDs,
    # - revoked/cancelled leases,
    # - mismatched idempotency keys / worker IDs, and
    # - cross-project lease replay (valid lease belonging to another project).
    wm.register_task_contract(
        project_id,
        run_id=None,
        task_key="t_r2_lease_fence",
        purpose="Test authentic lease enforcement at work boundary",
        expected_output="out.json",
    )
    with pytest.raises(wm.StaleWorkerResultError, match="lease-direct-synthetic-bypass"):
        wm.execute_checkpointed_work_boundary(
            project_id,
            "t_r2_lease_fence",
            lambda _pkt: {
                "statement": "Synthetic direct lease attempt",
                "lease_id": "lease-direct-synthetic-bypass",
            },
        )

    other_proj = projects_service.create_project(
        name="Other R2 Project",
        owner="operator",
        objective="Cross-project replay test",
        status="active",
        actor="operator",
    )
    conn = get_db_connection()
    conn.execute("UPDATE project_task_contracts SET status = 'pending', stale_reason = '' WHERE project_id = ? AND task_key = 't_r2_lease_fence'", (project_id,))
    conn.execute(
        """INSERT INTO tasks (
               id, project_id, title, description, task_type, status,
               assigned_worker, idempotency_key, created_at, updated_at
           ) VALUES ('task-other-proj', ?, 'Other Task', 'Valid in other proj', 'inference', 'running', 'node-w1', 'idem-other-1', ?, ?)""",
        (other_proj["id"], now, now),
    )
    conn.execute(
        """INSERT INTO worker_task_leases (
               id, task_id, worker_id, idempotency_key, status,
               leased_at, lease_expires_at, heartbeat_at, created_at, updated_at
           ) VALUES ('lease-other-proj', 'task-other-proj', 'node-w1', 'idem-other-1', 'active', ?, '2099-01-01T00:00:00Z', ?, ?, ?)""",
        (now, now, now, now),
    )
    conn.commit()

    # Mismatched worker_id and mismatched idempotency key must be rejected
    with pytest.raises(worker_service.TaskAuthorizationError) as exc_worker:
        worker_service.verify_worker_task_authorization(
            "node-wrong-worker",
            "task-other-proj",
            operation="submit",
            lease_id="lease-other-proj",
            conn=conn,
        )
    assert exc_worker.value.status_code in (401, 403, 409)
    conn.close()

    with pytest.raises(worker_service.TaskAuthorizationError) as exc_idem:
        worker_service.submit_task_result(
            worker_id="node-w1",
            task_id="task-other-proj",
            lease_id="lease-other-proj",
            idempotency_key="idem-wrong-key",
            status="success",
            exit_code=0,
            runtime_seconds=0.5,
            stdout_summary="mismatched idem",
            stderr_summary="",
        )
    assert exc_idem.value.status_code == 409

    # Replaying `lease-other-proj` (which belongs to `other_proj`) at `project_id`'s work boundary must fail closed!
    with pytest.raises(wm.StaleWorkerResultError, match="belongs to project"):
        wm.execute_checkpointed_work_boundary(
            project_id,
            "t_r2_lease_fence",
            lambda _pkt: {
                "statement": "Cross-project lease replay attempt",
                "lease_id": "lease-other-proj",
            },
        )


def test_aud04_no_unverified_deliverable_metric_project_or_knowledge_acceptance(isolated_db: Path):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    proj = projects_service.create_project(
        name="R2 Acceptance Project",
        owner="operator",
        objective="Test acceptance invariants",
        status="active",
        actor="operator",
    )
    project_id = proj["id"]
    deliv = deliverables_service.create_deliverable(
        project_id=project_id,
        name="Unverified Deliverable",
        description="No valid artifact",
        deliverable_type="report",
        acceptance_criteria="Must be verified",
        created_by="operator",
    )
    deliverables_service.submit_deliverable(
        deliv["id"],
        user="operator",
        artifact_references="Submitted without verified artifact",
        verification_results="Self-reported pass",
    )

    # 2a. deliverables.review_deliverable(..., "accepted", ...) must reject unverified deliverable
    with pytest.raises(ValidationError):
        deliverables_service.review_deliverable(
            deliv["id"],
            "accepted",
            reviewer="user",
            rationale="Manually accepting without artifact or verification",
        )

    # 2b. projects.change_project_status(..., "completed", ...) must reject project without verified deliverables/contracts
    projects_service.change_project_status(project_id, "active", actor="user")
    with pytest.raises(ValidationError):
        projects_service.change_project_status(project_id, "completed", actor="user")

    # 2c. metrics.record_observation(..., verification_status="verified") without verified evidence must not store "verified"
    metric = metrics_service.define_metric(
        project_id=project_id,
        name="test_r2_metric",
        unit="score",
        direction_of_improvement="higher_is_better",
    )
    obs = metrics_service.record_observation(
        metric_id=metric["id"],
        value=99.5,
        project_id=project_id,
        evidence_reference="execution:nonexistent-exec-id",
        verification_status="verified",
    )
    assert obs["verification_status"] == "unverified"

    # 2d. worker._evaluate_and_record_metric_observation with fake execution ref must record 'unverified'
    conn = get_db_connection()
    conn.execute(
        """INSERT INTO infrastructure_nodes (
               id, name, role, hostname, ip_address, tailscale_status,
               health_status, is_enabled, created_at, updated_at
           ) VALUES ('node-w2', 'node-w2', 'vps_worker', 'node-w2', '127.0.0.1', 'connected', 'online', 1, ?, ?)""",
        (now, now),
    )
    conn.execute(
        """INSERT INTO tasks (
               id, project_id, title, description, task_type, status,
               assigned_worker, created_at, updated_at
           ) VALUES ('task-metric-fake', ?, 'Metric Task', 'Fake metric ref', 'inference', 'running', 'node-w2', ?, ?)""",
        (project_id, now, now),
    )
    task_row = conn.execute("SELECT * FROM tasks WHERE id = 'task-metric-fake'").fetchone()
    obs_id = worker_service._evaluate_and_record_metric_observation(
        conn,
        worker_id="node-w2",
        actual_task_id="task-metric-fake",
        task_row=task_row,
        idempotency_key="idem-metric-fake",
        metric_obs={
            "metric_id": metric["id"],
            "value": 88.0,
            "evidence_reference": "execution:fake-exec-999",
        },
        status="success",
        exit_code=0,
        runtime_seconds=1.5,
        now=now,
    )
    conn.commit()
    row = conn.execute(
        "SELECT verification_status FROM metric_observations WHERE id = ?",
        (obs_id,),
    ).fetchone()
    conn.close()
    assert row["verification_status"] == "unverified"


def test_aud07_repo_change_report_truthful_controller_provenance_and_public_only_scope(
    isolated_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    ws_root = tmp_path / "allowed_ws"
    ws_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "allowed_workspace_roots", str(ws_root.resolve()))

    # Create a tiny repository with a failing public test (unittest) and NO holdout_test_dir
    repo_dir = ws_root / "sample_repo"
    (repo_dir / "src").mkdir(parents=True, exist_ok=True)
    (repo_dir / "tests").mkdir(parents=True, exist_ok=True)
    (repo_dir / "src" / "__init__.py").write_text("", encoding="utf-8")
    (repo_dir / "src" / "calc.py").write_text(
        "def add(a: int, b: int) -> int:\n    return a - b\n",
        encoding="utf-8",
    )
    (repo_dir / "tests" / "test_calc.py").write_text(
        "import unittest\nfrom src.calc import add\n\nclass TestCalc(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n",
        encoding="utf-8",
    )

    local_double = protected_repo_verifier.make_local_blackbox_isolated_transport()
    protected_repo_verifier.configure_protected_repo_verifier(
        local_double,
        backend_kind="local_blackbox_process_isolated_oracle",
        nonproduction=True,
    )

    def _mock_worker_inf(**kwargs):
        now_w = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        c_w = get_db_connection()
        c_w.execute(
            """INSERT OR IGNORE INTO infrastructure_nodes (
                   id, name, role, hostname, ip_address, tailscale_status,
                   health_status, is_enabled, created_at, updated_at
               ) VALUES ('node-patch-w1', 'node-patch-w1', 'vps_worker', 'node-patch-w1', '127.0.0.1', 'connected', 'online', 1, ?, ?)""",
            (now_w, now_w),
        )
        c_w.execute(
            """INSERT OR IGNORE INTO tasks (
                   id, project_id, title, description, task_type, status,
                   assigned_worker, created_at, updated_at
               ) VALUES ('task-patch-t2', ?, 'Patch Task', 'Patch inference', 'inference', 'completed', 'node-patch-w1', ?, ?)""",
            (kwargs.get("project_id"), now_w, now_w),
        )
        c_w.execute(
            """INSERT OR IGNORE INTO worker_task_leases (
                   id, task_id, worker_id, idempotency_key, status,
                   leased_at, lease_expires_at, heartbeat_at, created_at, updated_at
               ) VALUES ('lease-worker-patch-t2', 'task-patch-t2', 'node-patch-w1', 'idem-worker-patch-t2', 'completed', ?, ?, ?, ?, ?)""",
            (now_w, "2099-01-01T00:00:00Z", now_w, now_w, now_w),
        )
        c_w.execute(
            """INSERT OR IGNORE INTO worker_executions (
                   id, task_id, worker_id, lease_id, idempotency_key, status,
                   exit_code, started_at, completed_at, runtime_seconds, created_at
               ) VALUES ('exec-worker-patch-t2', 'task-patch-t2', 'node-patch-w1', 'lease-worker-patch-t2', 'idem-worker-patch-t2', 'success', 0, ?, ?, 0.5, ?)""",
            (now_w, now_w, now_w),
        )
        c_w.commit()
        c_w.close()
        return {
            "status": "completed",
            "response": "```python\ndef add(a: int, b: int) -> int:\n    return a + b\n```\n",
            "lease_id": "lease-worker-patch-t2",
            "execution_id": "exec-worker-patch-t2",
            "idempotency_key": "idem-worker-patch-t2",
        }

    monkeypatch.setattr(deliberate_loop, "execute_native_worker_inference", _mock_worker_inf)

    intake_payload = {
        "objective": "Fix repository bug in src/calc.py so unit tests pass.",
        "constraints": ["Target file: src/calc.py", "Public tests: tests/test_calc.py"],
        "deliverables": [
            "repo_inspection_and_reproduction.json",
            "verified_bugfix_patch.diff",
            "repo_change_verification_report.md",
        ],
    }
    created = engine.create_project_with_run(
        name="Repo Bugfix Public Only",
        actor="operator",
        intake=intake_payload,
        autonomy_level=2,
    )
    project_id = created["project"]["id"]
    run_id = created["run"]["id"]
    ledger = intake_mod.parse_intake_requirements(intake_payload)
    intake_mod.persist_requirement_ledger(project_id, ledger, run_id=run_id, actor="operator")

    contracts = deliberate_loop.ensure_deliberate_task_contracts(project_id, run_id=run_id)
    by_key = {c["task_key"]: c for c in contracts}
    assert "t3_publish_change_report" in by_key
    t3_contract = by_key["t3_publish_change_report"]
    # AUD-07: t3_publish_change_report must be typed as artifact publication, not protected_repo_test_verifier
    assert t3_contract["selected_tool"] == "create_or_revise_artifact"
    assert t3_contract["authority_scope"] == "artifact_publication"

    # Advance through all 3 steps with public-only tests (holdout_test_dir=None)
    result = deliberate_loop.advance_deliberate_project(
        project_id,
        workspace_path=str(repo_dir),
        holdout_test_dir=None,
        max_steps=3,
        use_live_worker=True,
    )
    steps_by_key = {s["task_key"]: s for s in result["steps_executed"]}
    assert "t3_publish_change_report" in steps_by_key
    t3_step = steps_by_key["t3_publish_change_report"]
    # AUD-07: Controller-synthesized summary must NOT claim a worker lease_id as its own execution lease
    assert t3_step["lease_id"] is None
    assert t3_step.get("producing_actor") == "deterministic_controller"
    assert t3_step.get("referenced_patch_lease_id") == steps_by_key["t2_propose_and_verify_patch"]["lease_id"]

    # AUD-07: Public-only repository work must NOT promote public_diagnostics_passed into protected acceptance
    assert result.get("verification_scope") == "public_diagnostics_passed"
    assert result.get("establishes_protected_acceptance") is False
    assert result.get("final_cycle_phase") != "completed"


def test_aud10c_reuse_status_and_reuse_id_truthful_strings():
    status = ReuseStatus("queued")
    assert status == "queued"
    assert status != "reused"
    assert status != "active"

    rid = ReuseId("reuse-123456789abc")
    assert rid.startswith("reuse-") is True
    assert rid.startswith("ar-") is False
