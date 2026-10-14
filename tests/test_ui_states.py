import pytest
from starlette.testclient import TestClient
from app.main import app
from app.auth import create_session_token
from app.database import get_db_connection
from app.services.machine_registry import register_machine, update_machine_specs
from app.services.inference import register_inference_backend, update_backend_status

@pytest.fixture
def auth_client():
    client = TestClient(app)
    token = create_session_token("admin")
    client.cookies.set("qc_session", token)
    return client

def test_ui_state_zero_machines(auth_client):
    # Ensure database has 0 machines
    conn = get_db_connection()
    conn.execute("DELETE FROM machine_audits;")
    conn.execute("DELETE FROM machines;")
    conn.execute("DELETE FROM inference_backends;")
    conn.commit()
    conn.close()

    # Dashboard check
    res = auth_client.get("/")
    assert res.status_code == 200
    assert "No Compute Machines Registered" in res.text
    assert "Add First Machine" in res.text

    # Machines list check
    res_mach = auth_client.get("/machines")
    assert res_mach.status_code == 200
    assert "No Machines Registered Yet" in res_mach.text

def test_ui_state_one_offline_machine(auth_client):
    # Register 1 machine and mark offline
    node = register_machine(
        display_name="Offline Laptop",
        ip_address="192.168.1.199",
        role="distributed_worker"
    )
    update_machine_specs(node["id"], status="offline")

    # Dashboard check
    res = auth_client.get("/")
    assert res.status_code == 200
    assert "0 online" in res.text
    assert "1 offline" in res.text

    # Machines list check
    res_mach = auth_client.get("/machines")
    assert res_mach.status_code == 200
    assert "Offline Laptop" in res_mach.text
    assert "badge-offline" in res_mach.text

    # Detail page check
    res_det = auth_client.get(f"/machines/{node['id']}")
    assert res_det.status_code == 200
    assert "badge-offline" in res_det.text

def test_ui_state_failed_inference_connection(auth_client):
    # Register inference backend marked as error / offline
    b = register_inference_backend(
        name="Failed llama-server",
        backend_type="llama_cpp",
        endpoint_url="http://192.168.1.50:8080"
    )
    update_backend_status(b["id"], "error", error_details="Connection refused")

    # Dashboard check: active backend is None or Unavailable
    res = auth_client.get("/")
    assert res.status_code == 200
    assert "Unavailable" in res.text
    assert "No active verified endpoint" in res.text

    # Chat page check: must render explicit offline state banner, not a fake model
    res_chat = auth_client.get("/chat")
    assert res_chat.status_code == 200
    assert "Inference Engine Offline" in res_chat.text
    assert "Configure &amp; Verify Inference Backend" in res_chat.text or "Configure & Verify Inference Backend" in res_chat.text
