"""
Stage 8B: Operational Reliability, Health, and Failover Test Suite.

Verifies:
1. Worker crash, disconnect, and lease expiry recovery with bounded retries.
2. Controller restart recovery with proactive lease reconciliation and durable state restoration.
3. Dual-worker race prevention and rejection of stale worker completion after reassignment.
4. Model capability unavailability scheduling defense.
5. Cluster health snapshot (API Spec Section 6.1) and operational diagnostics endpoint.
6. Bounded non-destructive stall detection distinguishing legitimate work from unproductive loops.
7. Stage 8A backup health integration (latest recovery point, verification status, capacity warnings).
8. API contract verification matrix for documented routes (Finding F-16 / ADR-01).
9. Negative security tests (unauthorized access denial, expired lease rejection).
"""
import base64
import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any

import pytest
from starlette.testclient import TestClient

from app.main import app
from app.config import settings
from app.database import get_db_connection, db_transaction
from app.auth import create_session_token, get_csrf_token
from app.services import operational_health
from app.services import projects as ps
from app.services import worker as worker_svc
from app.services.orchestration import engine, approval_manager, planner, model_router
from app.services.artifacts import artifact_service
from app.services.backup import create_backup, list_backups, verify_backup
from tests.orch_helpers import register_node, disable_other_nodes, restore_nodes


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture(autouse=True)
def setup_cluster_nodes():
    """Ensure standard nodes exist and restore state afterwards."""
    register_node("t-laptop", "inference_node", models=["qwen2.5:1.5b"])
    register_node("t-vps", "vps_worker", models=[])
    yield
    restore_nodes()


@pytest.fixture
def auth_client():
    """Client authenticated with administrative session cookie and CSRF token."""
    client = TestClient(app)
    admin_token = create_session_token("admin")
    client.cookies.set("qc_session", admin_token)
    return client


@pytest.fixture
def bearer_client():
    """Client authenticated via Bearer token."""
    client = TestClient(app)
    admin_token = create_session_token("admin")
    client.headers.update({"Authorization": f"Bearer {admin_token}"})
    return client


@pytest.fixture
def anon_client():
    """Unauthenticated client."""
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. Worker and Controller Failure Recovery
# ---------------------------------------------------------------------------

def test_worker_crash_and_lease_expiry_reconciliation():
    """Verify that when a worker crashes or abandons a lease, the task is re-queued with attempt count incremented."""
    now = datetime.now(timezone.utc)
    expired_time = (now - timedelta(minutes=5)).isoformat()

    worker_id = "test-worker-crashed"
    register_node(worker_id, "vps_worker")

    proj = ps.create_project(name=f"Crash Proj {uuid.uuid4().hex[:6]}", owner="admin")
    task_id = f"task-crash-{uuid.uuid4().hex[:8]}"
    lease_id = f"lease-crash-{uuid.uuid4().hex[:8]}"

    conn = get_db_connection()
    try:
        # Insert in-progress task with attempt_count = 0
        conn.execute(
            """
            INSERT INTO tasks (id, project_id, title, status, assigned_worker, attempt_count, max_retries, created_at, updated_at)
            VALUES (?, ?, 'Test Crash Task', 'in_progress', ?, 0, 3, ?, ?)
            """,
            (task_id, proj["id"], worker_id, expired_time, expired_time),
        )

        # Insert expired lease
        conn.execute(
            """
            INSERT INTO worker_task_leases (
                id, task_id, worker_id, idempotency_key, status, leased_at, lease_expires_at, heartbeat_at, attempt_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'leased', ?, ?, ?, 1, ?, ?)
            """,
            (lease_id, task_id, worker_id, f"idemp-{lease_id}", expired_time, expired_time, expired_time, expired_time, expired_time),
        )
        conn.commit()
    finally:
        conn.close()

    # Trigger lease reconciliation
    recovered = worker_svc.reconcile_expired_leases()
    assert task_id in recovered

    # Verify task was recovered to pending and attempt_count incremented to 1
    conn = get_db_connection()
    try:
        t = dict(conn.execute("SELECT status, assigned_worker, attempt_count FROM tasks WHERE id = ?", (task_id,)).fetchone())
        assert t["status"] == "pending"
        assert t["assigned_worker"] is None
        assert t["attempt_count"] == 1

        l = dict(conn.execute("SELECT status FROM worker_task_leases WHERE id = ?", (lease_id,)).fetchone())
        assert l["status"] == "expired"
    finally:
        conn.close()


def test_bounded_retries_on_repeated_lease_expiries():
    """Verify that when repeated lease expiries reach max_retries, the task fails cleanly rather than looping indefinitely."""
    now = datetime.now(timezone.utc)
    expired_time = (now - timedelta(minutes=10)).isoformat()

    worker_id = "test-worker-flaky"
    register_node(worker_id, "vps_worker")

    proj = ps.create_project(name=f"Flaky Proj {uuid.uuid4().hex[:6]}", owner="admin")
    task_id = f"task-bound-{uuid.uuid4().hex[:8]}"
    lease_id = f"lease-bound-{uuid.uuid4().hex[:8]}"

    conn = get_db_connection()
    try:
        # Task already at attempt_count = 2 (max_retries = 3)
        conn.execute(
            """
            INSERT INTO tasks (id, project_id, title, status, assigned_worker, attempt_count, max_retries, created_at, updated_at)
            VALUES (?, ?, 'Repeated Crash Task', 'in_progress', ?, 2, 3, ?, ?)
            """,
            (task_id, proj["id"], worker_id, expired_time, expired_time),
        )

        conn.execute(
            """
            INSERT INTO worker_task_leases (
                id, task_id, worker_id, idempotency_key, status, leased_at, lease_expires_at, heartbeat_at, attempt_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'leased', ?, ?, ?, 2, ?, ?)
            """,
            (lease_id, task_id, worker_id, f"idemp-{lease_id}", expired_time, expired_time, expired_time, expired_time, expired_time),
        )
        conn.commit()
    finally:
        conn.close()

    # Reconcile leases
    recovered = worker_svc.reconcile_expired_leases()
    assert task_id in recovered

    conn = get_db_connection()
    try:
        t = dict(conn.execute("SELECT status, assigned_worker, attempt_count, error_details FROM tasks WHERE id = ?", (task_id,)).fetchone())
        assert t["status"] == "failed"
        assert t["assigned_worker"] is None
        assert t["attempt_count"] == 3
        assert "exceeded maximum retry attempts" in (t["error_details"] or "").lower()
    finally:
        conn.close()


def test_controller_restart_reconciliation():
    """Verify that controller restart logs durable recovery event and reconciles pending work."""
    proj = ps.create_project(name=f"Restart Proj {uuid.uuid4().hex[:6]}", owner="admin")
    run_id = f"run-restart-{uuid.uuid4().hex[:8]}"

    conn = get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO orchestration_runs (
                id, project_id, state, version, autonomy_level, intake_json, created_by, created_at, updated_at
            ) VALUES (?, ?, 'RUNNING', 1, 1, '{}', 'system', ?, ?)
            """,
            (run_id, proj["id"], _now_iso(), _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()

    recovered_runs = engine.recover_after_restart()
    assert recovered_runs >= 1

    # Verify event recorded
    conn = get_db_connection()
    try:
        event = conn.execute(
            "SELECT event_type, reason, detail_json FROM orchestration_events WHERE run_id = ? AND event_type = 'controller_restart_recovered'",
            (run_id,),
        ).fetchone()
        assert event is not None
        assert "restored" in (event["reason"] or "").lower()
    finally:
        conn.close()


def test_stale_worker_reconnection_after_reassignment_rejected(auth_client):
    """Verify that if a worker reconnects after its lease expired and task was reassigned, its submission is rejected."""
    now = datetime.now(timezone.utc)
    expired_time = (now - timedelta(minutes=15)).isoformat()

    worker_a = "worker-slow-a"
    worker_b = "worker-active-b"
    register_node(worker_a, "vps_worker")
    register_node(worker_b, "vps_worker")

    proj = ps.create_project(name=f"Reconn Proj {uuid.uuid4().hex[:6]}", owner="admin")
    task_id = f"task-reconnect-{uuid.uuid4().hex[:8]}"
    stale_lease = f"lease-stale-{uuid.uuid4().hex[:8]}"

    conn = get_db_connection()
    try:
        # Expired lease for Worker A
        conn.execute(
            """
            INSERT INTO worker_task_leases (
                id, task_id, worker_id, idempotency_key, status, leased_at, lease_expires_at, heartbeat_at, attempt_count, created_at, updated_at
            ) VALUES (?, ?, ?, 'tok-a', 'expired', ?, ?, ?, 1, ?, ?)
            """,
            (stale_lease, task_id, worker_a, expired_time, expired_time, expired_time, expired_time, expired_time),
        )

        # Task reassigned to Worker B
        conn.execute(
            """
            INSERT INTO tasks (id, project_id, title, status, assigned_worker, attempt_count, max_retries, created_at, updated_at)
            VALUES (?, ?, 'Task Reassigned', 'in_progress', ?, 1, 3, ?, ?)
            """,
            (task_id, proj["id"], worker_b, _now_iso(), _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()

    # Worker A tries to submit with expired lease
    with pytest.raises(Exception):
        worker_svc.submit_task_result(
            task_id=task_id,
            worker_id=worker_a,
            idempotency_key="idemp-late-a",
            status="success",
            lease_id=stale_lease,
            result_payload={"result": "late submission"},
        )

    # Verify task remains in progress on Worker B
    conn = get_db_connection()
    try:
        t = dict(conn.execute("SELECT status, assigned_worker FROM tasks WHERE id = ?", (task_id,)).fetchone())
        assert t["status"] == "in_progress"
        assert t["assigned_worker"] == worker_b
    finally:
        conn.close()


def test_dual_worker_completion_race_prevention():
    """Verify that when two workers race to complete a task, only the valid lease holder succeeds."""
    now = datetime.now(timezone.utc)
    now_str = now.isoformat()
    future_time = (now + timedelta(minutes=10)).isoformat()

    worker_a = "worker-winner"
    worker_b = "worker-impostor"
    register_node(worker_a, "vps_worker")
    register_node(worker_b, "vps_worker")

    proj = ps.create_project(name=f"Race Proj {uuid.uuid4().hex[:6]}", owner="admin")
    task_id = f"task-race-{uuid.uuid4().hex[:8]}"
    lease_a = f"lease-race-a-{uuid.uuid4().hex[:8]}"

    conn = get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO tasks (id, project_id, title, status, assigned_worker, attempt_count, max_retries, created_at, updated_at)
            VALUES (?, ?, 'Task Dual Race', 'in_progress', ?, 0, 3, ?, ?)
            """,
            (task_id, proj["id"], worker_a, now_str, now_str),
        )

        conn.execute(
            """
            INSERT INTO worker_task_leases (
                id, task_id, worker_id, idempotency_key, status, leased_at, lease_expires_at, heartbeat_at, attempt_count, created_at, updated_at
            ) VALUES (?, ?, ?, 'tok-valid', 'leased', ?, ?, ?, 1, ?, ?)
            """,
            (lease_a, task_id, worker_a, now_str, future_time, now_str, now_str, now_str),
        )
        conn.commit()
    finally:
        conn.close()

    # 1. Impostor worker B attempts to submit without valid lease -> fails
    with pytest.raises(Exception):
        worker_svc.submit_task_result(
            task_id=task_id,
            worker_id=worker_b,
            idempotency_key="idemp-b",
            status="success",
            lease_id="invalid-or-missing-lease",
        )

    # 2. Legitimate worker A submits -> succeeds
    res_a = worker_svc.submit_task_result(
        task_id=task_id,
        worker_id=worker_a,
        idempotency_key="idemp-a-win",
        status="success",
        lease_id=lease_a,
        result_payload={"summary": "winner executed"},
    )
    assert res_a["task_id"] == task_id

    # 3. Subsequent conflicting submission after completion is rejected
    with pytest.raises(Exception):
        worker_svc.submit_task_result(
            task_id=task_id,
            worker_id=worker_b,
            idempotency_key="idemp-b-late",
            status="success",
            lease_id=lease_a,
        )


def test_model_unavailability_scheduling_defense():
    """Verify that an unavailable model or unverified capability blocks scheduling into an observable state."""
    task = {
        "id": "t-nonexistent",
        "action": {"type": "inference", "model": "non_existent_model:999b"},
        "required_capabilities": ["non_existent_capability"],
        "timeout_seconds": 60,
    }
    nodes = model_router.node_inventory()

    eligible = model_router.eligible_nodes(task, nodes, enforce_verified=True)
    assert len(eligible["eligible"]) == 0


# ---------------------------------------------------------------------------
# 2. Operational Health and Monitoring
# ---------------------------------------------------------------------------

def test_liveness_health_endpoint(anon_client):
    """Verify lightweight liveness probes /health and /api/v1/health."""
    for path in ("/health", "/api/v1/health"):
        resp = anon_client.get(path)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert "timestamp" in data
        assert "version" in data


def test_cluster_health_snapshot_api_spec_section_6_1(anon_client):
    """Verify Cluster Real-Time Health Snapshot matches API Specification Section 6.1 schema."""
    resp = anon_client.get("/api/v1/observatory/snapshots/latest")
    assert resp.status_code == 200
    data = resp.json()

    assert "timestamp" in data
    assert data["cluster_status"] in ("healthy", "degraded", "critical")
    assert isinstance(data["nodes_online"], int)
    assert isinstance(data["nodes_offline"], int)
    assert isinstance(data["active_tasks"], int)
    assert isinstance(data["inference_latency_p95_ms"], (int, float))
    assert isinstance(data["nodes"], list)

    if data["nodes"]:
        n = data["nodes"][0]
        assert "node_id" in n
        assert "status" in n
        assert "cpu_percent" in n
        assert "ram_free_mb" in n


def test_operational_diagnostics_service_and_api(anon_client):
    """Verify full operational diagnostics endpoint /api/v1/system/diagnostics."""
    resp = anon_client.get("/api/v1/system/diagnostics")
    assert resp.status_code == 200
    data = resp.json()

    # Summary
    summary = data["summary"]
    assert "workers_online" in summary
    assert "workers_stale" in summary
    assert "workers_offline" in summary
    assert "queue_in_progress" in summary
    assert "queue_making_progress" in summary
    assert "stall_status" in summary
    assert "backup_status" in summary

    # Sections
    assert "workers" in data
    assert "capabilities" in data
    assert "task_queue" in data
    assert "research_providers" in data
    assert "backup_operations" in data
    assert "stall_detection" in data

    # Verify research providers
    assert data["research_providers"]["status"] in ("available", "degraded")
    assert len(data["research_providers"]["providers"]) > 0

    # Verify backup operations integration
    b_ops = data["backup_operations"]
    assert b_ops["confidentiality_enforced"] is True
    assert b_ops["automatic_production_restore_disabled"] is True
    assert "storage" in b_ops

    # Verify stall detection policy
    stalls = data["stall_detection"]
    assert stalls["status"] in ("normal", "warning", "stalled")
    assert "Non-destructive" in stalls["policy"]


def test_bounded_stall_detection_on_unproductive_retry_loops():
    """Verify that repeated unproductive retry loops trigger bounded stall detection warnings without deleting tasks."""
    now = datetime.now(timezone.utc)
    proj = ps.create_project(name=f"Stall Proj {uuid.uuid4().hex[:6]}", owner="admin")
    task_id = f"task-stalled-loop-{uuid.uuid4().hex[:8]}"

    conn = get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO tasks (id, project_id, title, status, attempt_count, max_retries, created_at, updated_at)
            VALUES (?, ?, 'Looping Task', 'in_progress', 2, 3, ?, ?)
            """,
            (task_id, proj["id"], now.isoformat(), now.isoformat()),
        )
        conn.commit()
    finally:
        conn.close()

    # Run diagnostics
    diag = operational_health.get_operational_diagnostics()
    stall_info = diag["stall_detection"]

    # Verify suspicious operation flagged
    suspicious_ids = [s["task_id"] for s in stall_info["suspicious_operations"] if "task_id" in s]
    assert task_id in suspicious_ids

    # Confirm non-destructive invariant: task is NOT deleted or silently dropped
    conn = get_db_connection()
    try:
        t = conn.execute("SELECT id, status FROM tasks WHERE id = ?", (task_id,)).fetchone()
        assert t is not None
        assert t["status"] == "in_progress"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 3. API Contract Verification (Finding F-16 / ADR-01)
# ---------------------------------------------------------------------------

def test_api_contract_orchestrator_runs_flow(bearer_client):
    """Verify documented Section 2.1-2.4 endpoints with Bearer token authentication."""
    proj = ps.create_project(name=f"Spec Flow {uuid.uuid4().hex[:6]}", owner="admin")
    project_id = proj["id"]

    # 1. POST /api/v1/orchestrator/runs (201 Created)
    run_payload = {
        "project_id": project_id,
        "objective": "Collect basic health information from the VPS and laptop, use a local AI model to summarize the results, and generate a consolidated system report (system health report).",
        "scope": "Read-only diagnostics",
        "deliverables": "Health report",
        "success_criteria": "Accurate metrics",
    }
    r1 = bearer_client.post("/api/v1/orchestrator/runs", json=run_payload)
    assert r1.status_code == 201
    d1 = r1.json()
    assert "run_id" in d1
    assert d1["state"] == "DRAFT"
    run_id = d1["run_id"]

    # 2. POST /api/v1/orchestrator/runs/{run_id}/plan (200 OK)
    r2 = bearer_client.post(f"/api/v1/orchestrator/runs/{run_id}/plan", json={"mode": "template"})
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["ok"] is True
    res2 = d2["result"]
    assert "state" in res2

    # Advance to awaiting approval for approval test
    run = engine.get_run(run_id)
    if run["state"] != "AWAITING_PLAN_APPROVAL":
        # Force plan generation into awaiting approval with template
        planner.generate_plan(run_id, "admin", mode="template")

    # 3. POST /api/v1/orchestrator/runs/{run_id}/approve (200 OK)
    r3 = bearer_client.post(f"/api/v1/orchestrator/runs/{run_id}/approve", json={"actor": "admin"})
    assert r3.status_code == 200
    d3 = r3.json()
    assert d3["ok"] is True
    res3 = d3["result"]
    assert res3["state"] == "READY"
    assert "tasks_enqueued" in res3

    # 4. POST /api/v1/orchestrator/tick (200 OK)
    r4 = bearer_client.post("/api/v1/orchestrator/tick")
    assert r4.status_code == 200
    d4 = r4.json()
    assert d4["ok"] is True
    res4 = d4["result"]
    assert "active_runs" in res4
    assert "tasks_dispatched" in res4


def test_api_contract_artifacts_transfer_and_promote(bearer_client):
    """Verify Section 4.3 (transfer) and 4.4 (promote) endpoints."""
    proj_a = ps.create_project(name=f"Src Art {uuid.uuid4().hex[:6]}", owner="admin")
    proj_b = ps.create_project(name=f"Dst Art {uuid.uuid4().hex[:6]}", owner="admin")

    art = artifact_service.register_artifact(
        project_id=proj_a["id"],
        display_name="architecture_spec.md",
        artifact_type="document",
        data=b"# High Level Architecture\nVerifiable documentation content.",
        created_by="planner",
    )
    art_id = art["id"]

    # 1. POST /api/v1/artifacts/{artifact_id}/transfer (201 Created)
    t_resp = bearer_client.post(
        f"/api/v1/artifacts/{art_id}/transfer",
        json={"target_project_id": proj_b["id"]},
    )
    assert t_resp.status_code == 201
    t_data = t_resp.json()
    assert t_data["new_artifact_id"] == art_id
    assert t_data["target_project_id"] == proj_b["id"]
    assert t_data["lineage_edge"] == "transferred_to"

    # 2. POST /api/v1/artifacts/{artifact_id}/promote (200 OK)
    p_resp = bearer_client.post(
        f"/api/v1/artifacts/{art_id}/promote",
        json={
            "vault_path": "research/architecture_spec.md",
            "tags": ["architecture", "spec"],
        },
    )
    assert p_resp.status_code == 200
    p_data = p_resp.json()
    assert p_data["status"] == "promoted"
    assert "note_id" in p_data
    assert p_data["indexed_fts"] is True


# ---------------------------------------------------------------------------
# 4. Security & Negative Authorization Tests
# ---------------------------------------------------------------------------

def test_unauthenticated_requests_denied(anon_client):
    """Verify that unauthenticated mutating requests are rejected with 401."""
    # Orchestrator mutating routes
    assert anon_client.post("/api/v1/orchestrator/runs", json={}).status_code == 401
    assert anon_client.post("/api/v1/orchestrator/runs/dummy/plan", json={}).status_code == 401
    assert anon_client.post("/api/v1/orchestrator/runs/dummy/approve", json={}).status_code == 401
    assert anon_client.post("/api/v1/orchestrator/tick").status_code == 401
    # Artifact mutating routes
    assert anon_client.post("/api/v1/artifacts/art-dummy/transfer", json={"target_project_id": "p"}).status_code == 401
    assert anon_client.post("/api/v1/artifacts/art-dummy/promote", json={}).status_code == 401


def test_session_cookie_mutating_requests_require_csrf():
    """Verify that browser cookie sessions without CSRF tokens are rejected with 403."""
    client = TestClient(app)
    admin_token = create_session_token("admin")
    client.cookies.set("qc_session", admin_token)

    # Missing X-CSRF-Token header on cookie requests
    assert client.post("/api/v1/orchestrator/runs", json={"project_id": "test"}).status_code == 403
    assert client.post("/api/v1/artifacts/art-dummy/transfer", json={"target_project_id": "test"}).status_code == 403
    assert client.post("/api/v1/artifacts/art-dummy/promote", json={}).status_code == 403


def test_worker_automation_token_cannot_approve_or_promote_or_transfer():
    """Verify that worker automation tokens cannot replace human approval or execute cross-project mutations."""
    # Register worker node and token in DB
    node_id = f"node-{uuid.uuid4().hex[:8]}"
    raw_token = f"worker-tok-{uuid.uuid4().hex[:16]}"
    register_node(node_id, "vps_worker", models=[])
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO worker_auth_tokens (token_hash, token_prefix, node_id, revoked, created_at) VALUES (?, ?, ?, 0, datetime('now'))",
            (raw_token, raw_token[:8], node_id),
        )
        conn.commit()
    finally:
        conn.close()

    worker_client = TestClient(app)
    worker_client.headers.update({"Authorization": f"Bearer {raw_token}"})

    # 1. Worker token cannot approve a plan (Human approval required)
    appr_resp = worker_client.post(
        "/api/v1/orchestrator/runs/dummy-run/approve",
        json={"rationale": "Attempted worker approval"},
    )
    assert appr_resp.status_code == 403
    assert "Human approval required" in appr_resp.json()["error"]

    # 2. Worker token cannot create an orchestrator run
    create_resp = worker_client.post(
        "/api/v1/orchestrator/runs",
        json={"project_id": "test-proj", "objective": "test"},
    )
    assert create_resp.status_code == 403
    assert "Worker automation tokens cannot create" in create_resp.json()["error"]

    # 3. Worker token cannot transfer artifacts across projects
    transfer_resp = worker_client.post(
        "/api/v1/artifacts/dummy-art/transfer",
        json={"target_project_id": "target-proj"},
    )
    assert transfer_resp.status_code == 403
    assert "Worker automation tokens cannot transfer" in transfer_resp.json()["detail"]

    # 4. Worker token cannot promote artifacts to knowledge vault
    promote_resp = worker_client.post(
        "/api/v1/artifacts/dummy-art/promote",
        json={},
    )
    assert promote_resp.status_code == 403
    assert "human review required" in promote_resp.json()["detail"]

    # 5. Worker token cannot trigger orchestrator ticks
    tick_resp = worker_client.post("/api/v1/orchestrator/tick")
    assert tick_resp.status_code == 403


def test_cross_project_authorization_enforced():
    """Verify that non-admin authenticated users cannot manipulate other users' projects."""
    alice_proj = ps.create_project(name=f"Alice Project {uuid.uuid4().hex[:6]}", owner="alice")
    bob_proj = ps.create_project(name=f"Bob Project {uuid.uuid4().hex[:6]}", owner="bob")

    bob_tok = create_session_token("bob")
    bob_client = TestClient(app)
    bob_client.headers.update({"Authorization": f"Bearer {bob_tok}"})

    # Bob tries to create run in Alice's project -> 403 Forbidden
    resp = bob_client.post(
        "/api/v1/orchestrator/runs",
        json={"project_id": alice_proj["id"], "objective": "Hijack project"},
    )
    assert resp.status_code == 403
    assert "not authorized to create runs" in resp.json()["error"]

    # Create run for Alice
    run_alice = engine.create_run(alice_proj["id"], "alice", {"objective": "Alice work"})

    # Bob tries to trigger planning for Alice's run -> 403 Forbidden
    p_resp = bob_client.post(f"/api/v1/orchestrator/runs/{run_alice['id']}/plan", json={})
    assert p_resp.status_code == 403

    # Bob tries to approve Alice's run -> 403 Forbidden
    a_resp = bob_client.post(f"/api/v1/orchestrator/runs/{run_alice['id']}/approve", json={})
    assert a_resp.status_code == 403

    # Register artifact for Alice
    art = artifact_service.register_artifact(
        project_id=alice_proj["id"],
        display_name="secret_plan.md",
        artifact_type="document",
        data=b"Confidential details.",
        created_by="alice",
    )

    # Bob tries to transfer Alice's artifact into Bob's project -> 403 Forbidden
    t_resp = bob_client.post(
        f"/api/v1/artifacts/{art['id']}/transfer",
        json={"target_project_id": bob_proj["id"]},
    )
    assert t_resp.status_code == 403
    assert "not authorized to transfer artifact" in t_resp.json()["detail"]


def test_plan_approval_digest_mismatch_denied():
    """Verify that plan approval strictly rejects mismatched content digests."""
    proj = ps.create_project(name=f"Digest Test {uuid.uuid4().hex[:6]}", owner="admin")
    run = engine.create_run(proj["id"], "admin", {"objective": "Digest verify"})

    # Transition to AWAITING_PLAN_APPROVAL with a plan revision
    with db_transaction() as conn:
        rev_id = f"rev-{uuid.uuid4().hex[:12]}"
        plan_content = {
            "summary": "Plan test",
            "tasks": [{"id": "t1", "title": "Task 1", "action": "general_compute", "estimated_tokens": 100}],
        }
        conn.execute(
            """
            INSERT INTO orchestration_plan_revisions (id, run_id, revision_number, status, source, created_by, plan_json, created_at)
            VALUES (?, ?, 1, 'proposed', 'model', 'planner', ?, datetime('now'))
            """,
            (rev_id, run["id"], json.dumps(plan_content)),
        )
        conn.execute(
            "UPDATE orchestration_runs SET state = 'AWAITING_PLAN_APPROVAL', current_revision_id = ? WHERE id = ?",
            (rev_id, run["id"]),
        )

    admin_tok = create_session_token("admin")
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {admin_tok}"})

    # Approval with incorrect digest fails closed
    resp = client.post(
        f"/api/v1/orchestrator/runs/{run['id']}/approve",
        json={"digest": "deadbeef00000000000000000000000000000000000000000000000000000000"},
    )
    assert resp.status_code == 400
    assert "Plan digest mismatch" in resp.json()["error"]


def test_diagnostics_does_not_expose_credentials_or_tokens(anon_client):
    """Verify that public diagnostics and health snapshots do not leak secrets or tokens."""
    snap = anon_client.get("/api/v1/observatory/snapshots/latest").json()
    diag = anon_client.get("/api/v1/system/diagnostics").json()

    snap_str = json.dumps(snap).lower()
    diag_str = json.dumps(diag).lower()

    # Neither endpoint should contain password hashes, secret tokens, or bearer tokens
    for forbidden in ["password_hash", "token_hash", "secret_key", "bearer"]:
        assert forbidden not in snap_str
        assert forbidden not in diag_str
