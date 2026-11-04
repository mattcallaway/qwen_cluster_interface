"""
API integration tests for Worker Controller Endpoints & Agent Communication.
Verifies:
- Enrollment token lifecycle (creation, validation, expiration, single-use consumption)
- Rejection of unauthenticated or invalid enrollment attempts
- Scoped worker token issuance and authenticated inventory updates
- Worker isolation (prevention of cross-worker execution viewing and lease hijacking)
- Idempotent task leasing, heartbeats, and result submissions
"""

from datetime import datetime, timezone
import pytest
from starlette.testclient import TestClient

from app.main import app
from app.services import worker as worker_svc


@pytest.fixture
def client(db_conn):
    return TestClient(app)


def test_unauthenticated_registration_rejected(client):
    """Worker registration without enrollment token or admin session is rejected (401)."""
    reg_payload = {
        "node_id": "vps-unauth-01",
        "name": "Unauthenticated Worker",
        "role": "vps_worker",
    }
    resp = client.post("/api/v1/worker/register", json=reg_payload)
    assert resp.status_code == 401
    assert "Worker enrollment authorization required" in resp.json()["detail"]


def test_invalid_and_expired_enrollment_tokens_rejected(client, db_conn):
    """Invalid, expired, or node-mismatched enrollment tokens are rejected with 403."""
    reg_payload = {
        "node_id": "vps-invalid-node",
        "name": "Invalid Token Worker",
        "role": "vps_worker",
    }

    # 1. Non-existent / malformed token
    resp = client.post(
        "/api/v1/worker/register",
        json=reg_payload,
        headers={"X-Enrollment-Token": "qcwe_completely_fake_token_value_here"},
    )
    assert resp.status_code == 403
    assert "Enrollment token not found" in resp.json()["detail"]

    # 2. Expired token (expires in -10 seconds)
    token_id, expired_token = worker_svc.create_enrollment_token(expires_in_seconds=-10)
    resp = client.post(
        "/api/v1/worker/register",
        json=reg_payload,
        headers={"X-Enrollment-Token": expired_token},
    )
    assert resp.status_code == 403
    assert "expired" in resp.json()["detail"].lower()

    # 3. Token restricted to node-specific ID
    token_id2, restricted_token = worker_svc.create_enrollment_token(allowed_node_id="vps-authorized-node-only")
    resp = client.post(
        "/api/v1/worker/register",
        json={"node_id": "vps-wrong-node", "name": "Wrong Node"},
        headers={"X-Enrollment-Token": restricted_token},
    )
    assert resp.status_code == 403
    assert "restricted to node" in resp.json()["detail"].lower()


def test_valid_enrollment_succeeds_and_token_is_single_use(client, db_conn):
    """Valid enrollment token succeeds, provisions worker token, and cannot be reused."""
    node_id = "vps-valid-node-01"
    token_id, raw_enrollment_token = worker_svc.create_enrollment_token(
        allowed_node_id=node_id,
        expires_in_seconds=3600,
    )

    reg_payload = {
        "node_id": node_id,
        "name": "Valid Worker Node",
        "role": "vps_worker",
        "cpu_count": 2,
        "ram_total_mb": 2048,
        "ram_available_mb": 1024,
        "disk_total_gb": 40.0,
        "disk_free_gb": 20.0,
        "declared_capabilities": ["python_execution", "test_task"],
    }

    # 1. First enrollment succeeds
    resp = client.post(
        "/api/v1/worker/register",
        json=reg_payload,
        headers={"X-Enrollment-Token": raw_enrollment_token},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "registered"
    assert data["token_status"] == "provisioned"
    assert "worker_token" in data
    worker_token = data["worker_token"]
    assert worker_token.startswith("qcw_")

    # 2. Immediate reuse of the same enrollment token is rejected (single-use)
    reuse_resp = client.post(
        "/api/v1/worker/register",
        json=reg_payload,
        headers={"X-Enrollment-Token": raw_enrollment_token},
    )
    assert reuse_resp.status_code == 403
    assert "already consumed" in reuse_resp.json()["detail"].lower()

    # 3. Subsequent inventory updates use the authenticated worker Bearer token
    update_payload = dict(reg_payload, ram_available_mb=1200)
    auth_resp = client.post(
        "/api/v1/worker/register",
        json=update_payload,
        headers={"Authorization": f"Bearer {worker_token}"},
    )
    assert auth_resp.status_code == 200
    assert auth_resp.json()["token_status"] == "authenticated"


def test_worker_cannot_impersonate_or_access_other_nodes(client, db_conn):
    """A worker cannot access another node's private executions or claim another node's tasks."""
    # Enroll Worker A
    _, enr_a = worker_svc.create_enrollment_token(allowed_node_id="worker-node-a")
    resp_a = client.post(
        "/api/v1/worker/register",
        json={"node_id": "worker-node-a", "name": "Worker A"},
        headers={"X-Enrollment-Token": enr_a},
    )
    token_a = resp_a.json()["worker_token"]

    # Enroll Worker B
    _, enr_b = worker_svc.create_enrollment_token(allowed_node_id="worker-node-b")
    resp_b = client.post(
        "/api/v1/worker/register",
        json={"node_id": "worker-node-b", "name": "Worker B"},
        headers={"X-Enrollment-Token": enr_b},
    )
    token_b = resp_b.json()["worker_token"]

    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # Worker A cannot claim tasks using Worker B's worker_id
    lease_resp = client.post(
        "/api/v1/worker/tasks/lease",
        json={"worker_id": "worker-node-b", "supported_categories": ["test_task"]},
        headers=headers_a,
    )
    assert lease_resp.status_code == 403

    # Worker A cannot send heartbeat for Worker B
    hb_resp = client.post(
        "/api/v1/worker/heartbeat",
        json={"node_id": "worker-node-b"},
        headers=headers_a,
    )
    assert hb_resp.status_code == 403

    # Worker A cannot inspect Worker B's execution history
    exec_resp = client.get(
        "/api/v1/worker/executions?worker_id=worker-node-b",
        headers=headers_a,
    )
    assert exec_resp.status_code == 403


def test_task_leasing_and_submission_api_flow(client, db_conn):
    """Complete lifecycle: enrollment, lease claim, heartbeat renewal, execution submission."""
    # 1. Enroll worker with enrollment token
    _, enr_tok = worker_svc.create_enrollment_token(allowed_node_id="vps-task-worker-01")
    reg_resp = client.post(
        "/api/v1/worker/register",
        json={
            "node_id": "vps-task-worker-01",
            "name": "Task Test Worker",
            "declared_capabilities": ["test_task"],
        },
        headers={"X-Enrollment-Token": enr_tok},
    )
    assert reg_resp.status_code == 200
    token = reg_resp.json()["worker_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Ensure no pre-existing tasks are pending before starting the test
    db_conn.execute("UPDATE tasks SET status = 'completed' WHERE status IN ('pending', 'approved', 'queued')")
    db_conn.commit()

    # 2. No work available initially
    lease_resp = client.post(
        "/api/v1/worker/tasks/lease",
        json={"worker_id": "vps-task-worker-01", "supported_categories": ["test_task"]},
        headers=headers,
    )
    assert lease_resp.status_code == 200
    assert lease_resp.json()["status"] == "no_work"

    # 3. Create approved task in database
    now = datetime.now(timezone.utc).isoformat()
    task_id = "task-api-001"
    db_conn.execute(
        """
        INSERT INTO tasks (
            id, title, description, task_type, status, approval_required,
            input_data_json, created_at, updated_at
        ) VALUES (?, 'Milestone 2 Acceptance Task', 'Harmless verification probe', 'test_task', 'approved', 1, '{"check": true}', ?, ?)
        """,
        (task_id, now, now),
    )
    db_conn.commit()

    # 4. Worker pulls and claims lease
    lease_resp2 = client.post(
        "/api/v1/worker/tasks/lease",
        json={"worker_id": "vps-task-worker-01", "supported_categories": ["test_task"]},
        headers=headers,
    )
    assert lease_resp2.status_code == 200
    data = lease_resp2.json()
    assert data["status"] == "leased"
    task_payload = data["task"]
    assert task_payload["task_id"] == task_id
    lease_id = task_payload["lease_id"]
    idempotency_key = task_payload["idempotency_key"]

    # 5. Extend lease heartbeat
    renew_resp = client.post(f"/api/v1/worker/tasks/{lease_id}/heartbeat", headers=headers)
    assert renew_resp.status_code == 200
    assert renew_resp.json()["status"] == "renewed"

    # 6. Worker B cannot submit result for Worker A's lease
    _, enr_tok_b = worker_svc.create_enrollment_token(allowed_node_id="vps-worker-hijacker")
    reg_b = client.post(
        "/api/v1/worker/register",
        json={"node_id": "vps-worker-hijacker", "name": "Hijacker"},
        headers={"X-Enrollment-Token": enr_tok_b},
    )
    headers_b = {"Authorization": f"Bearer {reg_b.json()['worker_token']}"}

    sub_payload = {
        "task_id": task_id,
        "lease_id": lease_id,
        "idempotency_key": idempotency_key,
        "status": "success",
        "exit_code": 0,
        "runtime_seconds": 0.08,
        "stdout_summary": "Acceptance probe completed successfully.",
        "result_payload": {"success": True},
    }
    hijack_resp = client.post(f"/api/v1/worker/tasks/{task_id}/submit", json=sub_payload, headers=headers_b)
    assert hijack_resp.status_code == 403

    # 7. Authorized worker submits task result
    submit_resp = client.post(f"/api/v1/worker/tasks/{task_id}/submit", json=sub_payload, headers=headers)
    assert submit_resp.status_code == 200
    assert submit_resp.json()["status"] == "recorded"

    # 8. Check execution history
    hist_resp = client.get("/api/v1/worker/executions", headers=headers)
    assert hist_resp.status_code == 200
    executions = hist_resp.json()
    assert len(executions) >= 1
    assert executions[0]["task_id"] == task_id
    assert executions[0]["status"] == "success"


def test_unauthorized_registration_has_no_database_side_effects(client, db_conn):
    """
    Verify that unauthenticated registration attempts and invalid/expired enrollment tokens
    have ZERO side effects on the database: no rows created in infrastructure_nodes or worker_auth_tokens.
    """
    # Count rows before
    nodes_before = db_conn.execute("SELECT COUNT(*) FROM infrastructure_nodes").fetchone()[0]
    tokens_before = db_conn.execute("SELECT COUNT(*) FROM worker_auth_tokens").fetchone()[0]

    # 1. Unauthenticated request (no token)
    resp1 = client.post(
        "/api/v1/worker/register",
        json={"node_id": "rogue-node-1", "name": "Rogue 1", "role": "vps_worker"}
    )
    assert resp1.status_code == 401

    # 2. Invalid enrollment token
    resp2 = client.post(
        "/api/v1/worker/register",
        json={"node_id": "rogue-node-2", "name": "Rogue 2", "role": "vps_worker"},
        headers={"X-Enrollment-Token": "qcwe_completely_invalid_token"}
    )
    assert resp2.status_code == 403

    # 3. Expired enrollment token
    _, expired_tok = worker_svc.create_enrollment_token(expires_in_seconds=-60)
    resp3 = client.post(
        "/api/v1/worker/register",
        json={"node_id": "rogue-node-3", "name": "Rogue 3", "role": "vps_worker"},
        headers={"X-Enrollment-Token": expired_tok}
    )
    assert resp3.status_code == 403

    # Verify database counts are completely untouched
    nodes_after = db_conn.execute("SELECT COUNT(*) FROM infrastructure_nodes").fetchone()[0]
    tokens_after = db_conn.execute("SELECT COUNT(*) FROM worker_auth_tokens").fetchone()[0]
    assert nodes_after == nodes_before
    assert tokens_after == tokens_before

    # Verify no rogue node rows exist
    rogue_nodes = db_conn.execute("SELECT id FROM infrastructure_nodes WHERE id LIKE 'rogue-node%'").fetchall()
    assert len(rogue_nodes) == 0


def test_worker_token_revocation_invalidates_all_operations(client, db_conn):
    """
    Revoking an active worker Bearer token immediately invalidates:
    - Heartbeat ping
    - Task leasing
    - Re-registration / inventory update
    """
    node_id = "vps-revocation-test-node"
    _, enr_token = worker_svc.create_enrollment_token(allowed_node_id=node_id)
    reg_resp = client.post(
        "/api/v1/worker/register",
        json={"node_id": node_id, "name": "Revocation Test Node"},
        headers={"X-Enrollment-Token": enr_token}
    )
    assert reg_resp.status_code == 200
    token_id = reg_resp.json()["token_id"]
    raw_token = reg_resp.json()["worker_token"]
    headers = {"Authorization": f"Bearer {raw_token}"}

    # Heartbeat works before revocation
    hb_resp = client.post("/api/v1/worker/heartbeat", json={"node_id": node_id}, headers=headers)
    assert hb_resp.status_code == 200

    # Revoke the token
    revoked = worker_svc.revoke_worker_token(token_id)
    assert revoked is True

    # 1. Heartbeat immediately rejected
    hb_revoked = client.post("/api/v1/worker/heartbeat", json={"node_id": node_id}, headers=headers)
    assert hb_revoked.status_code == 401
    assert "invalid or revoked" in hb_revoked.json()["detail"].lower()

    # 2. Task lease immediately rejected
    lease_revoked = client.post(
        "/api/v1/worker/tasks/lease",
        json={"worker_id": node_id, "supported_categories": ["python_execution"]},
        headers=headers
    )
    assert lease_revoked.status_code == 401

    # 3. Registration with revoked token rejected
    reg_revoked = client.post(
        "/api/v1/worker/register",
        json={"node_id": node_id, "name": "Revocation Test Node Update"},
        headers=headers
    )
    assert reg_revoked.status_code == 401


def test_invalid_and_mismatched_worker_credentials(client, db_conn):
    """Verify that node mismatches, malformed tokens, and unknown tokens are rejected."""
    # Enroll node X
    _, enr_x = worker_svc.create_enrollment_token(allowed_node_id="node-x")
    reg_x = client.post(
        "/api/v1/worker/register",
        json={"node_id": "node-x", "name": "Node X"},
        headers={"X-Enrollment-Token": enr_x}
    )
    token_x = reg_x.json()["worker_token"]

    # 1. Node mismatch: token for node-x sent with node_id=node-y
    mismatch_resp = client.post(
        "/api/v1/worker/register",
        json={"node_id": "node-y", "name": "Node Y"},
        headers={"Authorization": f"Bearer {token_x}"}
    )
    assert mismatch_resp.status_code == 403
    assert "mismatch" in mismatch_resp.json()["detail"].lower()

    # 2. Malformed token prefix
    bad_prefix_resp = client.post(
        "/api/v1/worker/heartbeat",
        json={"node_id": "node-x"},
        headers={"Authorization": "Bearer bad_prefix_token_12345"}
    )
    assert bad_prefix_resp.status_code == 401

    # 3. Non-existent token with valid prefix
    fake_token_resp = client.post(
        "/api/v1/worker/heartbeat",
        json={"node_id": "node-x"},
        headers={"Authorization": "Bearer qcw_nonexistent_token_string_12345"}
    )
    assert fake_token_resp.status_code == 401


def test_vps_agent_persists_token_and_authenticates_on_restart(client, tmp_path, monkeypatch):
    """
    Test the complete VPS agent token lifecycle:
    1. Agent enrolls with single-use enrollment token.
    2. Agent securely persists returned worker_token to disk (mode 0600 on Linux).
    3. Agent restarts (simulated with fresh VPSWorkerAgent instance reading persisted file).
    4. Restarted agent authenticates and updates inventory via Bearer token without needing enrollment token.
    """
    from vps_agent.config import AgentConfig, load_config
    from vps_agent.agent import VPSWorkerAgent

    node_id = "vps-restart-test-node"
    _, enr_token = worker_svc.create_enrollment_token(allowed_node_id=node_id)

    # Configure agent with temporary data dir
    data_dir = tmp_path / "worker_data"
    cfg = AgentConfig(
        worker_id=node_id,
        worker_name="Restart Test Node",
        enrollment_token=enr_token,
        data_dir=data_dir,
    )

    # Use test client for HTTP requests
    agent1 = VPSWorkerAgent(config=cfg)
    agent1.session = client

    # 1. Initial registration consumes enrollment token
    success1 = agent1.register()
    assert success1 is True
    assert agent1.is_registered is True
    assert agent1.token.startswith("qcw_")

    # 2. Verify token file was written to disk
    token_file = cfg.token_file_path
    assert token_file.exists()
    token_content = token_file.read_text(encoding="utf-8")
    assert agent1.token in token_content

    # Verify POSIX permissions if on Linux/POSIX
    import os
    if os.name != "nt":
        stat_mode = token_file.stat().st_mode & 0o777
        assert stat_mode == 0o600
        parent_mode = token_file.parent.stat().st_mode & 0o777
        assert parent_mode == 0o700

    # 3. Simulate agent restart: new config WITHOUT enrollment token, pointing to same data_dir
    monkeypatch.setenv("QC_WORKER_ID", node_id)
    monkeypatch.setenv("QC_WORKER_DATA_DIR", str(data_dir))
    monkeypatch.delenv("QC_WORKER_TOKEN", raising=False)
    monkeypatch.delenv("QC_ENROLLMENT_TOKEN", raising=False)

    cfg2 = load_config()
    # Confirm worker_token was loaded from disk
    assert cfg2.worker_token == agent1.token

    # 4. Restarted agent registers using the loaded token
    agent2 = VPSWorkerAgent(config=cfg2)
    agent2.session = client

    success2 = agent2.register()
    assert success2 is True
    assert agent2.is_registered is True
    assert agent2.token == agent1.token

    # 5. Heartbeat succeeds on restarted agent
    hb_resp = client.post(
        "/api/v1/worker/heartbeat",
        json={"node_id": node_id},
        headers=agent2._headers()
    )
    assert hb_resp.status_code == 200


def test_admin_can_revoke_worker_token_via_api(client, db_conn):
    """Administrator can revoke a worker token via DELETE /api/v1/worker/tokens/{token_id}."""
    from app.routes.auth import create_session_token

    # Enroll worker
    node_id = "vps-admin-revocation-node"
    _, enr_tok = worker_svc.create_enrollment_token(allowed_node_id=node_id)
    reg_resp = client.post(
        "/api/v1/worker/register",
        json={"node_id": node_id, "name": "Admin Revocation Test"},
        headers={"X-Enrollment-Token": enr_tok}
    )
    token_id = reg_resp.json()["token_id"]
    worker_token = reg_resp.json()["worker_token"]

    # Non-admin cannot revoke
    resp_unauth = client.delete(f"/api/v1/worker/tokens/{token_id}")
    assert resp_unauth.status_code == 401

    # Admin revokes token (with CSRF protection F-15)
    import hashlib
    import hmac
    from app.config import settings
    admin_session = create_session_token("admin")
    client.cookies.set("qc_session", admin_session)
    csrf_tok = hmac.new(settings.secret_key.encode("utf-8"), f"csrf:{admin_session}".encode("utf-8"), hashlib.sha256).hexdigest()[:32]

    resp_revoke = client.delete(f"/api/v1/worker/tokens/{token_id}", headers={"X-CSRF-Token": csrf_tok})
    assert resp_revoke.status_code == 200
    assert resp_revoke.json()["status"] == "revoked"

    # Worker can no longer authenticate
    client.cookies.clear()
    hb_resp = client.post(
        "/api/v1/worker/heartbeat",
        json={"node_id": node_id},
        headers={"Authorization": f"Bearer {worker_token}"}
    )
    assert hb_resp.status_code == 401

