"""
Tests for VPS Worker Reliability, Long-Running Heartbeats, and Offline Buffering.
Verifies:
- Independent background heartbeat thread maintains lease during execution
- Delayed / offline result reconciliation for expired leases
- Conflict preservation when an expired lease task was already completed by another execution
- Offline buffer persistence and restoration
"""

import time
from datetime import datetime, timezone, timedelta
import pytest
from starlette.testclient import TestClient

from app.main import app
from app.services import worker as worker_svc
from vps_agent.agent import VPSWorkerAgent, _run_lease_heartbeats
from vps_agent.buffer import OfflineBuffer
from vps_agent.config import AgentConfig


@pytest.fixture
def client(db_conn):
    return TestClient(app)


def test_independent_lease_heartbeat_thread(client, db_conn):
    """The lease heartbeat thread extends the lease in the background while work proceeds."""
    # 1. Enroll worker
    _, enr_tok = worker_svc.create_enrollment_token(allowed_node_id="vps-heartbeat-worker")
    reg_resp = client.post(
        "/api/v1/worker/register",
        json={"node_id": "vps-heartbeat-worker", "name": "Heartbeat Worker"},
        headers={"X-Enrollment-Token": enr_tok},
    )
    worker_token = reg_resp.json()["worker_token"]

    # 2. Create task
    now = datetime.now(timezone.utc).isoformat()
    task_id = "task-hb-test-01"
    db_conn.execute(
        """
        INSERT INTO tasks (id, title, task_type, status, created_at, updated_at)
        VALUES (?, 'Long running task', 'test_task', 'approved', ?, ?)
        """,
        (task_id, now, now),
    )
    db_conn.commit()

    # 3. Lease task with short 60s lease
    lease = worker_svc.lease_task_for_worker(
        worker_id="vps-heartbeat-worker",
        supported_categories=["test_task"],
        lease_duration_seconds=60,
    )
    assert lease is not None
    lease_id = lease["lease_id"]

    # Read initial expiration
    row = db_conn.execute("SELECT lease_expires_at FROM worker_task_leases WHERE id = ?", (lease_id,)).fetchone()
    initial_exp = row["lease_expires_at"]

    # 4. Agent extends lease
    renew_resp = client.post(
        f"/api/v1/worker/tasks/{lease_id}/heartbeat",
        headers={"Authorization": f"Bearer {worker_token}"},
    )
    assert renew_resp.status_code == 200
    assert renew_resp.json()["status"] == "renewed"

    # Verify expiration was extended
    row2 = db_conn.execute("SELECT lease_expires_at FROM worker_task_leases WHERE id = ?", (lease_id,)).fetchone()
    assert row2["lease_expires_at"] >= initial_exp


def test_expired_lease_reconciliation_open_task(client, db_conn):
    """When a worker submits a result for an expired lease, the controller reconciles the task."""
    # 1. Enroll worker
    _, enr_tok = worker_svc.create_enrollment_token(allowed_node_id="vps-recon-worker-01")
    reg_resp = client.post(
        "/api/v1/worker/register",
        json={"node_id": "vps-recon-worker-01", "name": "Recon Worker"},
        headers={"X-Enrollment-Token": enr_tok},
    )
    worker_token = reg_resp.json()["worker_token"]

    # 2. Create task and lease it
    now = datetime.now(timezone.utc).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
    task_id = "task-recon-01"
    lease_id = "lease-recon-01"
    idem_key = "idem-recon-01"

    db_conn.execute(
        """
        INSERT INTO tasks (id, title, task_type, status, created_at, updated_at)
        VALUES (?, 'Reconciliation Task', 'test_task', 'in_progress', ?, ?)
        """,
        (task_id, now, now),
    )
    db_conn.execute(
        """
        INSERT INTO worker_task_leases (
            id, task_id, worker_id, idempotency_key, status,
            leased_at, lease_expires_at, heartbeat_at, created_at, updated_at
        ) VALUES (?, ?, 'vps-recon-worker-01', ?, 'expired', ?, ?, ?, ?, ?)
        """,
        (lease_id, task_id, idem_key, past, past, past, past, past),
    )
    db_conn.commit()

    # 3. Submit result for this expired lease
    sub_payload = {
        "task_id": task_id,
        "lease_id": lease_id,
        "idempotency_key": idem_key,
        "status": "success",
        "exit_code": 0,
        "runtime_seconds": 1.2,
        "stdout_summary": "Reconciled execution finished.",
    }
    resp = client.post(
        f"/api/v1/worker/tasks/{task_id}/submit",
        json=sub_payload,
        headers={"Authorization": f"Bearer {worker_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "recorded"
    assert data["task_status"] == "completed"

    # Verify lease and task are now completed
    lease_row = db_conn.execute("SELECT status FROM worker_task_leases WHERE id = ?", (lease_id,)).fetchone()
    assert lease_row["status"] == "completed"
    task_row = db_conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
    assert task_row["status"] == "completed"


def test_expired_lease_conflict_preservation(client, db_conn):
    """When a task was already completed by another execution, a delayed result is preserved as reconciled_conflict."""
    # 1. Enroll worker
    _, enr_tok = worker_svc.create_enrollment_token(allowed_node_id="vps-conflict-worker")
    reg_resp = client.post(
        "/api/v1/worker/register",
        json={"node_id": "vps-conflict-worker", "name": "Conflict Worker"},
        headers={"X-Enrollment-Token": enr_tok},
    )
    worker_token = reg_resp.json()["worker_token"]

    # 2. Create task already marked completed
    now = datetime.now(timezone.utc).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
    task_id = "task-conflict-01"
    lease_id = "lease-conflict-01"
    idem_key = "idem-conflict-delayed"

    db_conn.execute(
        """
        INSERT INTO tasks (id, title, task_type, status, created_at, updated_at)
        VALUES (?, 'Conflict Task', 'test_task', 'completed', ?, ?)
        """,
        (task_id, now, now),
    )
    db_conn.execute(
        """
        INSERT INTO worker_task_leases (
            id, task_id, worker_id, idempotency_key, status,
            leased_at, lease_expires_at, heartbeat_at, created_at, updated_at
        ) VALUES (?, ?, 'vps-conflict-worker', ?, 'expired', ?, ?, ?, ?, ?)
        """,
        (lease_id, task_id, idem_key, past, past, past, past, past),
    )
    db_conn.commit()

    # 3. Submit result for this task
    sub_payload = {
        "task_id": task_id,
        "lease_id": lease_id,
        "idempotency_key": idem_key,
        "status": "success",
        "exit_code": 0,
        "runtime_seconds": 2.5,
        "stdout_summary": "Delayed conflicting execution output.",
    }
    resp = client.post(
        f"/api/v1/worker/tasks/{task_id}/submit",
        json=sub_payload,
        headers={"Authorization": f"Bearer {worker_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "reconciled_conflict"
    assert "conflicting evidence" in data["message"].lower()

    # Verify execution was preserved in ledger
    exec_row = db_conn.execute("SELECT status, error_category FROM worker_executions WHERE idempotency_key = ?", (idem_key,)).fetchone()
    assert exec_row["status"] == "cancelled"
    assert exec_row["error_category"] == "reconciled_conflict"


def test_offline_buffer_drain_lifecycle(tmp_path):
    """Test OfflineBuffer persistence across buffer creation and retrieval."""
    db_path = tmp_path / "offline_worker.db"
    buf = OfflineBuffer(db_path)
    assert buf.count_pending() == 0

    # Buffer two task submissions
    buf.buffer_submission("task-off-1", "lease-off-1", "idem-off-1", {"status": "success"})
    buf.buffer_submission("task-off-2", "lease-off-2", "idem-off-2", {"status": "error"})
    assert buf.count_pending() == 2

    # Verify persistence across new instance
    buf2 = OfflineBuffer(db_path)
    pending = buf2.get_pending_submissions()
    assert len(pending) == 2
    assert pending[0]["task_id"] == "task-off-1"
    assert pending[1]["task_id"] == "task-off-2"

    # Remove one
    buf2.remove_submission(pending[0]["id"])
    assert buf2.count_pending() == 1
