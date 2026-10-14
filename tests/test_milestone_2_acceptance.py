"""
Milestone 2 Acceptance Test:
A harmless test task is assigned to the VPS worker, leased, executed, and verified through the controller.
Verifies:
- Outbound pull-based task leasing protocol
- Dedicated worker authentication
- Harmless execution without root or host disruption
- Disconnect resilience and local buffer drainage
"""

import json
from datetime import datetime, timezone
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from app.main import app
from app.services import worker as worker_svc
from vps_agent.agent import VPSWorkerAgent
from vps_agent.config import AgentConfig


class MockClientSession:
    """Adapts Starlette TestClient to requests.Session interface for in-process testing."""
    def __init__(self, client: TestClient):
        self.client = client

    def post(self, url, json=None, headers=None, timeout=None):
        path = url.replace("http://127.0.0.1:8000", "")
        return self.client.post(path, json=json, headers=headers)

    def get(self, url, headers=None, timeout=None):
        path = url.replace("http://127.0.0.1:8000", "")
        return self.client.get(path, headers=headers)


def test_milestone_2_full_acceptance_flow(tmp_path, db_conn):
    client = TestClient(app)

    # 1. Clear any lingering pending tasks in test database
    db_conn.execute("UPDATE tasks SET status = 'completed' WHERE status IN ('pending', 'approved')")
    db_conn.commit()

    # 2. Configure VPS Worker Agent with Mock Session to controller
    config = AgentConfig(
        controller_url="http://127.0.0.1:8000",
        worker_id="vps-production-worker-01",
        worker_name="Production VPS Companion Node",
        worker_role="vps_worker",
        data_dir=tmp_path / "vps_data",
        poll_interval=1,
        heartbeat_interval=1,
    )
    agent = VPSWorkerAgent(config)
    agent.session = MockClientSession(client)

    # 3. Agent registers with controller
    reg_success = agent.register()
    assert reg_success is True
    assert agent.is_registered is True
    assert agent.token.startswith("qcw_")

    # 4. Agent sends health heartbeat
    hb_success = agent.send_heartbeat()
    assert hb_success is True

    # 5. Controller receives and verifies node in infrastructure registry
    node_row = db_conn.execute(
        "SELECT * FROM infrastructure_nodes WHERE id = 'vps-production-worker-01'"
    ).fetchone()
    assert node_row is not None
    assert node_row["name"] == "Production VPS Companion Node"
    assert node_row["health_status"] == "online"

    # 6. Assign a harmless test task on the controller
    now = datetime.now(timezone.utc).isoformat()
    acceptance_task_id = "task-m2-acceptance-01"
    db_conn.execute(
        """
        INSERT INTO tasks (
            id, title, description, task_type, status, approval_required,
            assigned_worker, input_data_json, created_at, updated_at
        ) VALUES (
            ?, 'Harmless VPS Acceptance Probe', 'Verify VPS worker pull protocol and execution',
            'test_task', 'approved', 1, 'vps-production-worker-01', '{"probe": "hardware_echo"}', ?, ?
        )
        """,
        (acceptance_task_id, now, now),
    )
    db_conn.commit()

    # 7. Agent runs a polling cycle: leases, executes, submits
    cycle_result = agent.run_cycle()
    assert cycle_result["executed_task"] is True
    assert cycle_result["pending_buffer_count"] == 0

    # 8. Controller confirms task is completed
    task_row = db_conn.execute(
        "SELECT status, result_data_json, assigned_worker FROM tasks WHERE id = ?",
        (acceptance_task_id,),
    ).fetchone()
    assert task_row["status"] == "completed"
    assert task_row["assigned_worker"] == "vps-production-worker-01"
    result_data = json.loads(task_row["result_data_json"])
    assert result_data["verified"] is True
    assert "Harmless" in result_data["message"]

    # 9. Execution is immutably logged in worker_executions
    exec_row = db_conn.execute(
        "SELECT * FROM worker_executions WHERE task_id = ?",
        (acceptance_task_id,),
    ).fetchone()
    assert exec_row is not None
    assert exec_row["worker_id"] == "vps-production-worker-01"
    assert exec_row["status"] == "success"
    assert exec_row["exit_code"] == 0
    assert exec_row["runtime_seconds"] >= 0.0

    # 10. Disconnect Resilience Test: Buffer a result locally when controller is disconnected
    disconnected_task_id = "task-m2-offline-01"
    db_conn.execute(
        """
        INSERT INTO tasks (
            id, title, description, task_type, status, approval_required,
            assigned_worker, input_data_json, created_at, updated_at
        ) VALUES (
            ?, 'Offline Buffered Task', 'Testing network drop buffering',
            'test_task', 'in_progress', 1, 'vps-production-worker-01', '{}', ?, ?
        )
        """,
        (disconnected_task_id, now, now),
    )
    db_conn.commit()

    agent.buffer.buffer_submission(
        task_id=disconnected_task_id,
        lease_id="lease-offline-01",
        idempotency_key="idem-offline-01",
        submission_payload={
            "task_id": disconnected_task_id,
            "lease_id": "lease-offline-01",
            "idempotency_key": "idem-offline-01",
            "status": "success",
            "exit_code": 0,
            "runtime_seconds": 0.15,
            "stdout_summary": "Buffered offline execution completed.",
        },
    )
    assert agent.buffer.count_pending() == 1

    # Now flush buffer with connection restored
    flushed = agent.flush_offline_buffer()
    assert flushed == 1
    assert agent.buffer.count_pending() == 0
