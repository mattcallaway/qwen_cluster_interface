"""
Unit and Integration Tests for Model Discovery, Capability Reporting,
and Resource-Aware Task Scheduling.
"""

import json
from unittest.mock import patch, MagicMock
import pytest
from starlette.testclient import TestClient

from app.main import app
from app.services import worker as worker_svc
from vps_agent.capabilities import discover_local_hardware, discover_local_models


@pytest.fixture
def client(db_conn):
    return TestClient(app)


def test_discover_local_models_mock():
    """Verify discover_local_models parses Ollama tags API response."""
    fake_ollama_response = json.dumps({
        "models": [
            {"name": "qwen2.5:1.5b", "size": 986000000},
            {"name": "llama3.2:1b", "size": 1300000000},
        ]
    }).encode("utf-8")

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = fake_ollama_response
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        models = discover_local_models()
        assert "qwen2.5:1.5b" in models
        assert "llama3.2:1b" in models


def test_discover_local_hardware_with_models():
    """Verify hardware discovery registers model capabilities when models are found."""
    with patch("vps_agent.capabilities.discover_local_models", return_value=["qwen2.5:1.5b", "llama3.2:1b"]):
        hw = discover_local_hardware()
        assert "ollama" in hw["installed_runtimes"]
        assert "model_inference" in hw["declared_capabilities"]
        assert "qwen_inference" in hw["declared_capabilities"]
        assert "llama_inference" in hw["declared_capabilities"]
        assert hw["available_models"] == ["qwen2.5:1.5b", "llama3.2:1b"]


def test_worker_registration_and_heartbeat_stores_models(client, monkeypatch):
    """Verify registration and heartbeat persist available_models into infrastructure_nodes."""
    node_id = "test-inference-node"
    models = ["qwen2.5:1.5b", "llama3.2:1b"]

    # Generate enrollment token
    t_id, raw_enr = worker_svc.create_enrollment_token(allowed_node_id=node_id, created_by="admin")

    reg_payload = {
        "node_id": node_id,
        "name": "Test Inference Node",
        "role": "inference_node",
        "hostname": "test-box",
        "available_models": models,
        "declared_capabilities": ["model_inference", "qwen_inference", "llama_inference"],
    }
    resp = client.post(
        "/api/v1/worker/register",
        json=reg_payload,
        headers={"X-Enrollment-Token": raw_enr},
    )
    assert resp.status_code == 200
    data = resp.json()
    worker_token = data["worker_token"]

    # Verify database has stored available_models_json
    conn = worker_svc.get_db_connection()
    row = conn.execute("SELECT available_models_json FROM infrastructure_nodes WHERE id = ?", (node_id,)).fetchone()
    conn.close()
    assert row is not None
    stored_models = json.loads(row["available_models_json"])
    assert stored_models == models

    # Test heartbeat updating models
    updated_models = ["qwen2.5:1.5b", "llama3.2:1b", "qwen2.5:7b"]
    hb_resp = client.post(
        "/api/v1/worker/heartbeat",
        json={
            "node_id": node_id,
            "ram_available_mb": 4096,
            "available_models": updated_models,
        },
        headers={"Authorization": f"Bearer {worker_token}"},
    )
    assert hb_resp.status_code == 200

    conn = worker_svc.get_db_connection()
    row = conn.execute("SELECT available_models_json, available_ram_mb FROM infrastructure_nodes WHERE id = ?", (node_id,)).fetchone()
    conn.close()
    assert json.loads(row["available_models_json"]) == updated_models
    assert row["available_ram_mb"] == 4096


def test_resource_aware_task_scheduling(client):
    """Verify that tasks requiring specific models or RAM are leased only to capable workers."""
    import uuid
    suffix = uuid.uuid4().hex[:6]
    w_a = f"worker_a_{suffix}"
    w_b = f"worker_b_{suffix}"
    w_c = f"worker_c_{suffix}"
    w_d = f"worker_d_{suffix}"

    t_qwen = f"task_qwen_{suffix}"
    t_heavy = f"task_heavy_{suffix}"
    t_gen = f"task_gen_{suffix}"

    # Pre-register workers in infrastructure_nodes to satisfy foreign key
    worker_svc.register_worker_node(w_a, "Worker A", "inference_node")
    worker_svc.register_worker_node(w_b, "Worker B", "inference_node")
    worker_svc.register_worker_node(w_c, "Worker C", "inference_node")
    worker_svc.register_worker_node(w_d, "Worker D", "inference_node")

    conn = worker_svc.get_db_connection()
    try:
        # Insert Task 1: requires qwen model
        conn.execute(
            """
            INSERT INTO tasks (id, title, task_type, status, approval_required, input_data_json, created_at, updated_at)
            VALUES (?, 'Run Qwen Inference', 'inference', 'queued', 0, '{"required_model": "qwen2.5:1.5b", "min_ram_mb": 1024}', '2026-10-15T00:00:00Z', '2026-10-15T00:00:00Z')
            """,
            (t_qwen,),
        )
        # Insert Task 2: requires large RAM (8192 MB)
        conn.execute(
            """
            INSERT INTO tasks (id, title, task_type, status, approval_required, input_data_json, created_at, updated_at)
            VALUES (?, 'Heavy Computation', 'inference', 'queued', 0, '{"min_ram_mb": 8192}', '2026-10-15T00:01:00Z', '2026-10-15T00:01:00Z')
            """,
            (t_heavy,),
        )
        # Insert Task 3: general task (no constraints)
        conn.execute(
            """
            INSERT INTO tasks (id, title, task_type, status, approval_required, input_data_json, created_at, updated_at)
            VALUES (?, 'General Health Check', 'general', 'queued', 0, '{}', '2026-10-15T00:02:00Z', '2026-10-15T00:02:00Z')
            """,
            (t_gen,),
        )
        conn.commit()
    finally:
        conn.close()

    # Worker A: has only llama, RAM 2048 MB -> should NOT get task_qwen or task_heavy
    lease_a = worker_svc.lease_task_for_worker(
        worker_id=w_a,
        supported_categories=["inference", "general"],
        available_models=["llama3.2:1b"],
        available_ram_mb=2048,
    )
    assert lease_a is not None
    assert lease_a["task_id"] == t_gen

    # Worker B: has qwen2.5:1.5b, RAM 2048 MB -> should get task_qwen!
    lease_b = worker_svc.lease_task_for_worker(
        worker_id=w_b,
        supported_categories=["inference", "general"],
        available_models=["qwen2.5:1.5b", "llama3.2:1b"],
        available_ram_mb=2048,
    )
    assert lease_b is not None
    assert lease_b["task_id"] == t_qwen

    # Worker C: has RAM 2048 MB -> cannot lease task_heavy (needs 8192 MB)
    lease_c = worker_svc.lease_task_for_worker(
        worker_id=w_c,
        supported_categories=["inference"],
        available_models=["qwen2.5:1.5b"],
        available_ram_mb=2048,
    )
    assert lease_c is None  # No matching task

    # Worker D: has RAM 16384 MB -> CAN lease task_heavy!
    lease_d = worker_svc.lease_task_for_worker(
        worker_id=w_d,
        supported_categories=["inference"],
        available_models=["qwen2.5:1.5b"],
        available_ram_mb=16384,
    )
    assert lease_d is not None
    assert lease_d["task_id"] == t_heavy
