import pytest
from starlette.testclient import TestClient
from worker.vps_worker import app, AUTH_TOKEN

client = TestClient(app)

def test_vps_worker_unauthorized():
    res = client.get("/health")
    assert res.status_code == 401

def test_vps_worker_forbidden():
    res = client.get("/health", headers={"Authorization": "Bearer wrong-token"})
    assert res.status_code == 403

def test_vps_worker_health():
    res = client.get("/health", headers={"Authorization": f"Bearer {AUTH_TOKEN}"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert "health_check" in data["capabilities"]
    assert "test_task" in data["capabilities"]

def test_vps_worker_execute_harmless_echo():
    payload = {"task_type": "echo_ping", "payload": {"message": "Hello from Control Server"}}
    res = client.post(
        "/tasks/execute",
        json=payload,
        headers={"Authorization": f"Bearer {AUTH_TOKEN}"}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["echo"] == "Hello from Control Server"

def test_vps_worker_reject_arbitrary_shell():
    payload = {"task_type": "shell_exec", "payload": {"cmd": "rm -rf /"}}
    res = client.post(
        "/tasks/execute",
        json=payload,
        headers={"Authorization": f"Bearer {AUTH_TOKEN}"}
    )
    assert res.status_code == 400
    assert "strictly prohibited" in res.json()["detail"]
