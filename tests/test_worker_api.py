"""
API integration tests for Worker Controller Endpoints & Agent Communication (Milestone 2).
"""

from datetime import datetime, timezone
import pytest
from starlette.testclient import TestClient

from app.main import app
from app.services import worker as worker_svc
from vps_agent.agent import VPSWorkerAgent
from vps_agent.config import AgentConfig


@pytest.fixture
def client(db_conn):
    return TestClient(app)


def test_worker_registration_and_auth_flow(client):
    # 1. Register worker
    reg_payload = {
        "node_id": "vps-api-node-01",
        "name": "API Test Worker",
        "role": "vps_worker",
        "cpu_count": 2,
        "ram_total_mb": 2048,
        "ram_available_mb": 1024,
        "disk_total_gb": 40.0,
        "disk_free_gb": 20.0,
        "declared_capabilities": ["python_execution", "test_task"],
    }
    resp = client.post("/api/v1/worker/register", json=reg_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "registered"
    assert "worker_token" in data
    token = data["worker_token"]
    assert token.startswith("qcw_")

    # 2. Unauthenticated heartbeat rejected
    hb_payload = {
        "node_id": "vps-api-node-01",
        "ram_available_mb": 950,
        "disk_free_gb": 19.5,
    }
    unauth_resp = client.post("/api/v1/worker/heartbeat", json=hb_payload)
    assert unauth_resp.status_code == 401

    # 3. Authenticated heartbeat accepted
    auth_headers = {"Authorization": f"Bearer {token}"}
    auth_resp = client.post("/api/v1/worker/heartbeat", json=hb_payload, headers=auth_headers)
    assert auth_resp.status_code == 200
    hb_data = auth_resp.json()
    assert hb_data["status"] == "ok"
    assert hb_data["node_id"] == "vps-api-node-01"


def test_task_leasing_and_submission_api_flow(client, db_conn):
    # 1. Register worker and get token
    reg_resp = client.post(
        "/api/v1/worker/register",
        json={
            "node_id": "vps-task-worker-01",
            "name": "Task Test Worker",
            "declared_capabilities": ["test_task"],
        },
    )
    token = reg_resp.json()["worker_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Ensure no pre-existing tasks are pending before starting the test
    db_conn.execute("UPDATE tasks SET status = 'completed' WHERE status IN ('pending', 'approved')")
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

    # 6. Submit task result
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
    submit_resp = client.post(f"/api/v1/worker/tasks/{task_id}/submit", json=sub_payload, headers=headers)
    assert submit_resp.status_code == 200
    assert submit_resp.json()["status"] == "recorded"

    # 7. Check execution history
    hist_resp = client.get("/api/v1/worker/executions", headers=headers)
    assert hist_resp.status_code == 200
    executions = hist_resp.json()
    assert len(executions) >= 1
    assert executions[0]["task_id"] == task_id
    assert executions[0]["status"] == "success"
