"""
Tests for Repair C: Metric Verification and Evidence Integrity.
Ensures:
- Worker-submitted claims cannot grant themselves authoritative verification status.
- Reported measurements vs validated measurements separation.
- Checksum validation, artifact existence verification, and failure rejection.
- Deduplication of identical metric observations.
- Project Context Packet distinction between verified and unverified observations.
- Forecast calculations handle unverified observations appropriately.
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone
import pytest
from starlette.testclient import TestClient

from app.main import app
from app.services import metrics as met_svc
from app.services import projects as proj_svc
from app.services import worker as worker_svc
from app.services.context_packet import build_project_context_packet


@pytest.fixture
def client(db_conn):
    return TestClient(app)


def _setup_project_and_worker(client, db_conn, node_id="vps-metric-test-worker"):
    # 1. Create unique project & metric
    uid = uuid.uuid4().hex[:6]
    p = proj_svc.create_project(name=f"Metric Integrity Project {uid}", owner="admin")
    pid = p["id"]
    metric = met_svc.define_metric(
        project_id=pid,
        name=f"Files Processed {uid}",
        unit="count",
        owner="admin",
        metric_type="count",
        target_value=10.0,
    )
    metric_id = metric["id"]

    # 2. Enroll worker
    _, enr_tok = worker_svc.create_enrollment_token(allowed_node_id=node_id)
    reg_resp = client.post(
        "/api/v1/worker/register",
        json={"node_id": node_id, "name": f"Metric Worker {uid}"},
        headers={"X-Enrollment-Token": enr_tok},
    )
    worker_token = reg_resp.json()["worker_token"]
    return pid, metric_id, node_id, worker_token


def test_worker_cannot_self_verify_arbitrary_metric(client, db_conn):
    """Worker attempting to set verification_status = 'verified' is overridden to 'unverified'."""
    pid, metric_id, node_id, worker_token = _setup_project_and_worker(client, db_conn, "vps-self-verify-01")

    now = datetime.now(timezone.utc).isoformat()
    task_id = f"task-metric-self-verify-{uuid.uuid4().hex[:6]}"
    idem_key = f"idem-metric-self-verify-{uuid.uuid4().hex[:6]}"
    db_conn.execute(
        """
        INSERT INTO tasks (id, title, task_type, status, project_id, created_at, updated_at)
        VALUES (?, 'Self Verify Task', 'test_task', 'in_progress', ?, ?, ?)
        """,
        (task_id, pid, now, now),
    )
    db_conn.commit()

    sub_payload = {
        "task_id": task_id,
        "idempotency_key": idem_key,
        "status": "success",
        "exit_code": 0,
        "runtime_seconds": 1.0,
        "result_payload": {
            "metric_observation": {
                "metric_id": metric_id,
                "value": 42.0,
                "verification_status": "verified",  # Worker claiming verification
                "notes": "Worker asserts this is verified without evidence",
            }
        },
    }

    resp = client.post(
        f"/api/v1/worker/tasks/{task_id}/submit",
        json=sub_payload,
        headers={"Authorization": f"Bearer {worker_token}"},
    )
    assert resp.status_code == 200

    obs = db_conn.execute("SELECT * FROM metric_observations WHERE metric_id = ?", (metric_id,)).fetchone()
    assert obs is not None
    assert obs["value"] == 42.0
    # Must NOT be verified!
    assert obs["verification_status"] == "unverified"
    assert "Reported: Missing independent evidence" in obs["notes"]


def test_metric_observation_invalid_checksum_rejected(client, db_conn):
    """Corrupted or invalid checksum format rejects observation."""
    pid, metric_id, node_id, worker_token = _setup_project_and_worker(client, db_conn, "vps-invalid-cs-01")

    now = datetime.now(timezone.utc).isoformat()
    task_id = f"task-invalid-cs-{uuid.uuid4().hex[:6]}"
    idem_key = f"idem-invalid-cs-{uuid.uuid4().hex[:6]}"
    db_conn.execute(
        """
        INSERT INTO tasks (id, title, task_type, status, project_id, created_at, updated_at)
        VALUES (?, 'Invalid CS Task', 'test_task', 'in_progress', ?, ?, ?)
        """,
        (task_id, pid, now, now),
    )
    db_conn.commit()

    sub_payload = {
        "task_id": task_id,
        "idempotency_key": idem_key,
        "status": "success",
        "exit_code": 0,
        "result_payload": {
            "metric_observation": {
                "metric_id": metric_id,
                "value": 1.0,
                "evidence_reference": "some_doc.pdf",
                "artifact_checksum": "not-a-valid-sha256-hex",  # Invalid checksum
            }
        },
    }

    resp = client.post(
        f"/api/v1/worker/tasks/{task_id}/submit",
        json=sub_payload,
        headers={"Authorization": f"Bearer {worker_token}"},
    )
    assert resp.status_code == 200

    obs = db_conn.execute("SELECT * FROM metric_observations WHERE metric_id = ?", (metric_id,)).fetchone()
    assert obs is not None
    assert obs["verification_status"] == "rejected"
    assert "Invalid artifact checksum format" in obs["notes"]


def test_metric_observation_checksum_mismatch_on_file_rejected(client, db_conn, tmp_path):
    """Tampered artifact file with checksum mismatch is rejected by controller."""
    pid, metric_id, node_id, worker_token = _setup_project_and_worker(client, db_conn, "vps-mismatch-cs-01")

    # Create dummy artifact file
    art_file = tmp_path / "actual_artifact.txt"
    art_file.write_text("Legitimate file content", encoding="utf-8")

    now = datetime.now(timezone.utc).isoformat()
    task_id = f"task-mismatch-cs-{uuid.uuid4().hex[:6]}"
    idem_key = f"idem-mismatch-cs-{uuid.uuid4().hex[:6]}"
    db_conn.execute(
        """
        INSERT INTO tasks (id, title, task_type, status, project_id, created_at, updated_at)
        VALUES (?, 'Mismatch CS Task', 'test_task', 'in_progress', ?, ?, ?)
        """,
        (task_id, pid, now, now),
    )
    db_conn.commit()

    # Supply an invalid SHA256 that does not match the file
    wrong_checksum = "0000000000000000000000000000000000000000000000000000000000000000"

    sub_payload = {
        "task_id": task_id,
        "idempotency_key": idem_key,
        "status": "success",
        "exit_code": 0,
        "result_payload": {
            "metric_observation": {
                "metric_id": metric_id,
                "value": 1.0,
                "file_path": str(art_file),
                "artifact_checksum": wrong_checksum,
            }
        },
    }

    resp = client.post(
        f"/api/v1/worker/tasks/{task_id}/submit",
        json=sub_payload,
        headers={"Authorization": f"Bearer {worker_token}"},
    )
    assert resp.status_code == 200

    obs = db_conn.execute("SELECT * FROM metric_observations WHERE metric_id = ?", (metric_id,)).fetchone()
    assert obs is not None
    assert obs["verification_status"] == "rejected"
    assert "Checksum mismatch" in obs["notes"]


def test_metric_observation_valid_evidence_and_checksum_verified(client, db_conn, tmp_path):
    """Valid artifact file with matching SHA256 checksum is verified by controller."""
    pid, metric_id, node_id, worker_token = _setup_project_and_worker(client, db_conn, "vps-valid-cs-01")

    # Create dummy artifact file
    art_content = "Official Audited Report Content 2026"
    art_file = tmp_path / "audited_report.txt"
    art_file.write_text(art_content, encoding="utf-8")
    actual_checksum = hashlib.sha256(art_content.encode("utf-8")).hexdigest()

    now = datetime.now(timezone.utc).isoformat()
    task_id = f"task-valid-cs-{uuid.uuid4().hex[:6]}"
    idem_key = f"idem-valid-cs-{uuid.uuid4().hex[:6]}"
    db_conn.execute(
        """
        INSERT INTO tasks (id, title, task_type, status, project_id, created_at, updated_at)
        VALUES (?, 'Valid CS Task', 'test_task', 'in_progress', ?, ?, ?)
        """,
        (task_id, pid, now, now),
    )
    db_conn.commit()

    sub_payload = {
        "task_id": task_id,
        "idempotency_key": idem_key,
        "status": "success",
        "exit_code": 0,
        "result_payload": {
            "metric_observation": {
                "metric_id": metric_id,
                "value": 1.0,
                "file_path": str(art_file),
                "evidence_reference": str(art_file),
                "artifact_checksum": actual_checksum,
            }
        },
    }

    resp = client.post(
        f"/api/v1/worker/tasks/{task_id}/submit",
        json=sub_payload,
        headers={"Authorization": f"Bearer {worker_token}"},
    )
    assert resp.status_code == 200

    obs = db_conn.execute("SELECT * FROM metric_observations WHERE metric_id = ?", (metric_id,)).fetchone()
    assert obs is not None
    assert obs["verification_status"] == "verified"
    assert "Artifact checksum matches" in obs["notes"]


def test_metric_observation_deduplication(client, db_conn):
    """Submitting same metric observation twice with same evidence reference is deduplicated."""
    pid, metric_id, node_id, worker_token = _setup_project_and_worker(client, db_conn, "vps-dedup-01")

    now = datetime.now(timezone.utc).isoformat()
    task_id_1 = f"task-dedup-1-{uuid.uuid4().hex[:6]}"
    task_id_2 = f"task-dedup-2-{uuid.uuid4().hex[:6]}"
    db_conn.execute(
        """
        INSERT INTO tasks (id, title, task_type, status, project_id, created_at, updated_at)
        VALUES (?, 'Dedup Task 1', 'test_task', 'in_progress', ?, ?, ?),
               (?, 'Dedup Task 2', 'test_task', 'in_progress', ?, ?, ?)
        """,
        (task_id_1, pid, now, now, task_id_2, pid, now, now),
    )
    db_conn.commit()

    checksum = "a" * 64
    payload = {
        "status": "success",
        "exit_code": 0,
        "result_payload": {
            "metric_observation": {
                "metric_id": metric_id,
                "value": 5.0,
                "evidence_reference": "shared_evidence_ref",
                "artifact_checksum": checksum,
            }
        },
    }

    # First submission
    payload["task_id"] = task_id_1
    payload["idempotency_key"] = f"idem-dedup-1-{uuid.uuid4().hex[:6]}"
    resp1 = client.post(f"/api/v1/worker/tasks/{task_id_1}/submit", json=payload, headers={"Authorization": f"Bearer {worker_token}"})
    assert resp1.status_code == 200

    # Second submission with same metric and evidence_reference
    payload["task_id"] = task_id_2
    payload["idempotency_key"] = f"idem-dedup-2-{uuid.uuid4().hex[:6]}"
    resp2 = client.post(f"/api/v1/worker/tasks/{task_id_2}/submit", json=payload, headers={"Authorization": f"Bearer {worker_token}"})
    assert resp2.status_code == 200

    count = db_conn.execute("SELECT count(*) FROM metric_observations WHERE metric_id = ?", (metric_id,)).fetchone()[0]
    assert count == 1  # Deduplicated


def test_context_packet_distinguishes_verified_vs_unverified(db_conn):
    """The Project Context Packet clearly distinguishes verified vs unverified observations."""
    uid = uuid.uuid4().hex[:6]
    p = proj_svc.create_project(name=f"Context Metric Distinction {uid}", owner="admin")
    pid = p["id"]

    # 1. Metric with unverified observation
    m_unverified = met_svc.define_metric(project_id=pid, name=f"Unverified Metric {uid}", unit="items", owner="admin")
    met_svc.record_observation(
        metric_id=m_unverified["id"],
        value=75.0,
        verification_status="unverified",
        created_by="admin",
    )

    # 2. Metric with verified observation
    m_verified = met_svc.define_metric(project_id=pid, name=f"Verified Metric {uid}", unit="items", owner="admin")
    met_svc.record_observation(
        metric_id=m_verified["id"],
        value=100.0,
        verification_status="verified",
        created_by="admin",
    )

    packet = build_project_context_packet(pid)
    formatted_text = packet["rendered_markdown"]

    # Verify formatting distinctions
    assert f"Unverified Metric {uid}" in formatted_text
    assert "Reported: 75.0 (unverified)" in formatted_text

    assert f"Verified Metric {uid}" in formatted_text
    assert "Actual: 100.0 (verified)" in formatted_text


def test_forecast_calculation_ignores_unverified_observations(db_conn):
    """Forecasting algorithms with verified_only=True ignore unverified observations."""
    uid = uuid.uuid4().hex[:6]
    p = proj_svc.create_project(name=f"Forecast Verification Project {uid}", owner="admin")
    pid = p["id"]
    m = met_svc.define_metric(project_id=pid, name=f"Revenue Growth {uid}", unit="k", owner="admin")
    mid = m["id"]

    # Verified observations: (t0=10, t1=20)
    met_svc.record_observation(metric_id=mid, value=10.0, verification_status="verified", measured_at="2026-01-01T00:00:00Z", created_by="admin")
    met_svc.record_observation(metric_id=mid, value=20.0, verification_status="verified", measured_at="2026-02-01T00:00:00Z", created_by="admin")

    # Unverified wild outlier claim: 9999.0
    met_svc.record_observation(metric_id=mid, value=9999.0, verification_status="unverified", measured_at="2026-03-01T00:00:00Z", created_by="admin")

    # Calculate linear trend forecast with verified_only=True
    fct = met_svc.calculate_linear_trend_forecast(
        metric_id=mid,
        target_date_iso="2026-04-01T00:00:00Z",
        verified_only=True,
    )
    assert fct["status"] == "ok"
    # If 9999 was included, predicted value would be huge; with only verified 10 and 20, predicted value is around 30.0 - 40.0
    assert fct["predicted_value"] < 50.0

    # In contrast, calculate without verified_only (including unverified):
    fct_all = met_svc.calculate_linear_trend_forecast(
        metric_id=mid,
        target_date_iso="2026-04-01T00:00:00Z",
        verified_only=False,
    )
    assert fct_all["predicted_value"] > 5000.0
