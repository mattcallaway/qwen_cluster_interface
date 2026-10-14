"""
End-to-End Acceptance Test Scenario (Requirement 12).
Tests the complete Intelligence-to-Execution-to-Knowledge closed loop:
Project -> Path -> Stage -> Task -> Worker Enrollment -> Leasing -> Execution -> Result -> Evidence -> Knowledge -> Metrics -> Future Context
"""

import json
import re
from datetime import datetime, timezone
import pytest
from starlette.testclient import TestClient

from app.auth import create_session_token
from app.main import app
from app.services import context_packet as cp_svc
from app.services import deliverables as deliv_svc
from app.services import metrics as met_svc
from app.services import projects as ps
from app.services import tasks as ts
from app.services import worker as worker_svc
from vps_agent.executor import execute_task

CSRF_RE = re.compile(r'name="csrf_token" value="([0-9a-f]+)"')


def _csrf(client, url="/projects"):
    r = client.get(url)
    m = CSRF_RE.search(r.text)
    assert m, "page should embed a CSRF token"
    return m.group(1)


@pytest.fixture
def client(db_conn):
    return TestClient(app)


@pytest.fixture
def auth_client(client, db_conn):
    token = create_session_token("admin")
    client.cookies.set("qc_session", token)
    return client


def test_complete_intelligence_to_execution_to_knowledge_loop(auth_client, db_conn):
    """
    Scenario: Public Records Collection Test
    1. Create Project
    2. Create alternative Paths and select 'Automated collection using the VPS'
    3. Create Stage 'Document acquisition and verification'
    4. Create Metric 'Documents Retrieved'
    5. Create Deliverable 'Validated Public Record'
    6. Create and Approve Task linked to Deliverable
    7. Enroll VPS Worker via Single-Use Enrollment Token
    8. Lease task to Worker
    9. Worker executes task in sanitized environment
    10. Worker submits execution result with metric observation and deliverable reference
    11. Controller updates task, deliverable revisions, execution events, and metrics
    12. Future Project Context Packet reflects verified results and updated measurements
    """
    token = _csrf(auth_client)

    # ---------------------------------------------------------------------------
    # Step 1: Create Project
    # ---------------------------------------------------------------------------
    proj = ps.create_project(
        name="Public Records Collection Test",
        owner="admin",
        objective="Retrieve and index a small, controlled set of public documents.",
        success_criteria="At least one validated document retrieved with provenance.",
    )
    pid = proj["id"]
    assert ps.get_project(pid)["status"] == "draft"

    # ---------------------------------------------------------------------------
    # Step 2: Create Alternative Paths & Approve VPS Path
    # ---------------------------------------------------------------------------
    auth_client.post(f"/projects/{pid}/paths/create", data={
        "csrf_token": token,
        "name": "Manual collection",
        "rationale": "Collect documents manually via web browser",
        "estimated_cost": "200",
        "estimated_effort_hours": "50",
    })
    auth_client.post(f"/projects/{pid}/paths/create", data={
        "csrf_token": token,
        "name": "Automated collection using the VPS",
        "rationale": "Automated remote execution on Linux VPS companion",
        "estimated_cost": "50",
        "estimated_effort_hours": "10",
    })

    paths = {p["name"]: p for p in ps.list_paths(pid)}
    vps_path = paths["Automated collection using the VPS"]

    # Approve and select VPS path
    auth_client.post(f"/projects/{pid}/paths/{vps_path['id']}/status", data={
        "csrf_token": token,
        "new_status": "approved",
        "reason": "Lower cost and faster turnaround",
    })
    auth_client.post(f"/projects/{pid}/paths/{vps_path['id']}/select", data={
        "csrf_token": token,
        "reason": "Approved automated approach",
    })
    assert ps.get_project(pid)["selected_path_id"] == vps_path["id"]

    # Move project to active
    auth_client.post(f"/projects/{pid}/status", data={
        "csrf_token": token,
        "new_status": "active",
        "reason": "Ready to execute automated collection",
    })
    assert ps.get_project(pid)["status"] == "active"

    # ---------------------------------------------------------------------------
    # Step 3: Create Stage
    # ---------------------------------------------------------------------------
    auth_client.post(f"/projects/{pid}/paths/{vps_path['id']}/stages/create", data={
        "csrf_token": token,
        "name": "Document acquisition and verification",
        "objective": "Acquire public document and calculate SHA256 checksum",
    })
    stage = ps.list_stages(vps_path["id"])[0]

    # Move stage to active
    auth_client.post(f"/projects/{pid}/stages/{stage['id']}/status", data={
        "csrf_token": token,
        "new_status": "active",
        "reason": "Starting acquisition stage",
    })

    # ---------------------------------------------------------------------------
    # Step 4: Create Metric
    # ---------------------------------------------------------------------------
    metric = met_svc.define_metric(
        project_id=pid,
        name="Documents Retrieved",
        unit="count",
        owner="admin",
        metric_type="count",
        target_value=1.0,
        path_id=vps_path["id"],
    )
    metric_id = metric["id"]

    # ---------------------------------------------------------------------------
    # Step 5: Create Deliverable
    # ---------------------------------------------------------------------------
    deliverable = deliv_svc.create_deliverable(
        project_id=pid,
        name="Validated Public Record",
        deliverable_type="document",
        created_by="admin",
        stage_id=stage["id"],
    )
    deliv_id = deliverable["id"]

    # ---------------------------------------------------------------------------
    # Step 6: Create & Approve Task Linked to Deliverable
    # ---------------------------------------------------------------------------
    auth_client.post(f"/projects/{pid}/tasks/create", data={
        "csrf_token": token,
        "title": "Retrieve approved test document",
        "stage_id": stage["id"],
        "task_type": "python_execution",
        "purpose": "Download public notice and record metadata",
        "expected_output": "Downloaded document with checksum metadata",
        "completion_criteria": "HTTP 200, valid checksum recorded, telemetry reported",
        "deliverable_id": deliv_id,
    })

    project_tasks = ts.get_project_tasks(pid)
    task = next(t for t in project_tasks if t["title"] == "Retrieve approved test document")
    assert task["status"] == "awaiting_approval"

    # Approve task
    auth_client.post(f"/projects/{pid}/tasks/{task['id']}/approve", data={"csrf_token": token})
    task = ts.get_task_by_id(task["id"])
    assert task["status"] == "queued"

    # Set script input on task for VPS execution
    task_script = (
        "import hashlib, json\n"
        "doc_text = 'Public Record: Municipal Notice 2026-Q3'\n"
        "checksum = hashlib.sha256(doc_text.encode()).hexdigest()\n"
        "print(f'Retrieved {doc_text}. SHA256: {checksum}')\n"
        "print(json.dumps({'metric_observation': {'metric_id': '" + metric_id + "', 'value': 1.0}}))\n"
    )
    db_conn.execute("UPDATE tasks SET input_data_json = ? WHERE id = ?", (json.dumps({"script": task_script}), task["id"]))
    db_conn.commit()

    # ---------------------------------------------------------------------------
    # Step 7: Worker Enrollment
    # ---------------------------------------------------------------------------
    node_id = "vps-e2e-worker-01"
    _, enr_token = worker_svc.create_enrollment_token(allowed_node_id=node_id, created_by="admin")

    reg_resp = auth_client.post("/api/v1/worker/register", json={
        "node_id": node_id,
        "name": "E2E Test Worker Node",
        "role": "vps_worker",
        "declared_capabilities": ["python_execution", "test_task"],
    }, headers={"X-Enrollment-Token": enr_token})

    assert reg_resp.status_code == 200
    worker_token = reg_resp.json()["worker_token"]
    worker_headers = {"Authorization": f"Bearer {worker_token}"}

    # ---------------------------------------------------------------------------
    # Step 8: Lease Task to Worker
    # ---------------------------------------------------------------------------
    lease_resp = auth_client.post("/api/v1/worker/tasks/lease", json={
        "worker_id": node_id,
        "supported_categories": ["python_execution", "test_task"],
    }, headers=worker_headers)

    assert lease_resp.status_code == 200
    lease_data = lease_resp.json()
    assert lease_data["status"] == "leased"
    leased_task = lease_data["task"]
    assert leased_task["task_id"] == task["id"]
    lease_id = leased_task["lease_id"]
    idempotency_key = leased_task["idempotency_key"]

    # ---------------------------------------------------------------------------
    # Step 9: Worker Executes Task (Sandboxed)
    # ---------------------------------------------------------------------------
    exec_outcome = execute_task(leased_task, timeout_seconds=30)
    assert exec_outcome["status"] == "success"
    assert exec_outcome["exit_code"] == 0
    assert "Municipal Notice" in exec_outcome["stdout_summary"]

    # ---------------------------------------------------------------------------
    # Step 10: Worker Submits Result
    # ---------------------------------------------------------------------------
    submit_payload = {
        "task_id": task["id"],
        "lease_id": lease_id,
        "idempotency_key": idempotency_key,
        "status": exec_outcome["status"],
        "exit_code": exec_outcome["exit_code"],
        "runtime_seconds": exec_outcome["runtime_seconds"],
        "stdout_summary": exec_outcome["stdout_summary"],
        "stderr_summary": exec_outcome["stderr_summary"],
        "deliverable_ids": [deliv_id],
        "result_payload": {
            "metric_observation": {
                "metric_id": metric_id,
                "value": 1.0,
                "evidence_reference": f"deliverable:{deliv_id}",
                "artifact_checksum": "283b68cf83948f4741923117d12b64e266ac1885b8cd6bf0efd230449787f3a9",
                "notes": "Automated download verified with SHA256 checksum",
            }
        },
    }

    sub_resp = auth_client.post(f"/api/v1/worker/tasks/{task['id']}/submit", json=submit_payload, headers=worker_headers)
    assert sub_resp.status_code == 200
    assert sub_resp.json()["status"] == "recorded"

    # ---------------------------------------------------------------------------
    # Step 11: Verify Execution Loop Artifacts in Database
    # ---------------------------------------------------------------------------
    # Task marked completed
    task_after = ts.get_task_by_id(task["id"])
    assert task_after["status"] == "completed"

    # Task execution event recorded
    ev_row = db_conn.execute("SELECT * FROM task_execution_events WHERE task_id = ?", (task["id"],)).fetchone()
    assert ev_row is not None
    assert ev_row["outcome"] == "completed"
    assert ev_row["created_by"] == node_id

    # Deliverable status updated to submitted with revision
    deliv_after = deliv_svc.get_deliverable(deliv_id)
    assert deliv_after["status"] == "submitted"
    assert f"execution:{idempotency_key}" in deliv_after["artifact_references"]

    rev_row = db_conn.execute("SELECT * FROM deliverable_revisions WHERE deliverable_id = ?", (deliv_id,)).fetchone()
    assert rev_row is not None
    assert rev_row["status"] == "submitted"

    # Metric observation recorded and verified
    obs_row = db_conn.execute("SELECT * FROM metric_observations WHERE metric_id = ?", (metric_id,)).fetchone()
    assert obs_row is not None
    assert obs_row["value"] == 1.0
    assert obs_row["verification_status"] == "verified"

    # Execution history preserved in immutable ledger
    exec_row = db_conn.execute("SELECT * FROM worker_executions WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
    assert exec_row is not None
    assert exec_row["status"] == "success"
    assert exec_row["worker_id"] == node_id

    # ---------------------------------------------------------------------------
    # Step 12: Verify Future Project Context Packet Reflects New Knowledge & Measurements
    # ---------------------------------------------------------------------------
    future_packet = cp_svc.build_project_context_packet(pid, token_budget=4000)
    assert future_packet is not None
    rendered = future_packet["rendered_markdown"]

    # Must contain the project and path details
    assert "Public Records Collection Test" in rendered
    assert "Automated collection using the VPS" in rendered

    # Must show completed work and deliverable status
    assert "Retrieve approved test document" in rendered
    assert "[completed]" in rendered.lower()
    assert "Validated Public Record" in rendered
    assert "SUBMITTED" in rendered

    # Must show the verified metric observation
    assert "Documents Retrieved" in rendered
    assert "Actual: 1.0" in rendered
