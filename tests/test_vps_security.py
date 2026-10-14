import os
import pytest
from starlette.testclient import TestClient
from worker.vps_worker import app, validate_auth_token, validate_host_binding, IMPLEMENTED_CAPABILITIES

VALID_TOKEN = "a-very-strong-and-cryptographically-secure-vps-token-12345"

def test_vps_token_validation_failure_on_missing():
    with pytest.raises(RuntimeError, match="unique, strong QC_AUTH_TOKEN"):
        validate_auth_token(None)
    with pytest.raises(RuntimeError, match="unique, strong QC_AUTH_TOKEN"):
        validate_auth_token("")

def test_vps_token_validation_failure_on_weak_or_default():
    weak_tokens = [
        "default-vps-secret-token",
        "secret",
        "admin123",
        "changeme",
        "short-token-less-than-32-chars"
    ]
    for wt in weak_tokens:
        with pytest.raises(RuntimeError, match="unique, strong QC_AUTH_TOKEN"):
            validate_auth_token(wt)

def test_vps_token_validation_success():
    assert validate_auth_token(VALID_TOKEN) == VALID_TOKEN

def test_vps_host_binding_security():
    # 0.0.0.0 rejected by default
    with pytest.raises(RuntimeError, match="exposes the worker to all network interfaces"):
        validate_host_binding("0.0.0.0", allow_public=False)
    with pytest.raises(RuntimeError, match="exposes the worker to all network interfaces"):
        validate_host_binding("::", allow_public=False)

    # 127.0.0.1 or explicit IP accepted
    assert validate_host_binding("127.0.0.1", allow_public=False) == "127.0.0.1"
    assert validate_host_binding("100.122.228.19", allow_public=False) == "100.122.228.19"
    # 0.0.0.0 accepted only with explicit allow_public flag
    assert validate_host_binding("0.0.0.0", allow_public=True) == "0.0.0.0"

def test_vps_worker_auth_endpoints(monkeypatch):
    monkeypatch.setenv("QC_AUTH_TOKEN", VALID_TOKEN)
    client = TestClient(app)

    # 1. Root is accessible without token
    res_root = client.get("/")
    assert res_root.status_code == 200

    # 2. Health without auth header fails 401
    res_unauth = client.get("/health")
    assert res_unauth.status_code == 401

    # 3. Health with invalid token fails 403
    res_bad = client.get("/health", headers={"Authorization": "Bearer wrong-token"})
    assert res_bad.status_code == 403

    # 4. Health with correct token succeeds and reports actual capabilities
    res_ok = client.get("/health", headers={"Authorization": f"Bearer {VALID_TOKEN}"})
    assert res_ok.status_code == 200
    data = res_ok.json()
    assert data["status"] == "healthy"
    assert data["capabilities"] == IMPLEMENTED_CAPABILITIES
    assert "page_retrieval_sandbox" not in data["capabilities"]
    assert "structured_research_inbox" not in data["capabilities"]

def test_vps_worker_task_execution(monkeypatch):
    monkeypatch.setenv("QC_AUTH_TOKEN", VALID_TOKEN)
    client = TestClient(app)

    # Echo ping task
    res_echo = client.post(
        "/tasks/execute",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        json={"task_type": "echo_ping", "payload": {"message": "hello cluster"}}
    )
    assert res_echo.status_code == 200
    assert res_echo.json()["echo"] == "hello cluster"

    # Network probe task
    res_probe = client.post(
        "/tasks/execute",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        json={"task_type": "network_probe", "payload": {}}
    )
    assert res_probe.status_code == 200
    assert res_probe.json()["dns_resolution"] == "operational"

    # Arbitrary shell or unsupported task strictly rejected (400)
    res_unsupported = client.post(
        "/tasks/execute",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        json={"task_type": "run_shell_command", "payload": {"cmd": "whoami"}}
    )
    assert res_unsupported.status_code == 400
    assert "strictly prohibited" in res_unsupported.json()["detail"]
