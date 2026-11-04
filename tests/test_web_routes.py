import pytest
from starlette.testclient import TestClient
from app.main import app
from app.auth import create_session_token

@pytest.fixture
def client():
    return TestClient(app)

@pytest.fixture
def auth_client():
    client = TestClient(app)
    token = create_session_token("admin")
    client.cookies.set("qc_session", token)
    return client

def test_unauthenticated_redirect(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert "/auth/login" in response.headers["location"]

def test_authenticated_dashboard(auth_client):
    response = auth_client.get("/")
    assert response.status_code == 200
    assert "System Overview" in response.text
    assert "Control Server" in response.text

def test_machines_page(auth_client):
    response = auth_client.get("/machines")
    assert response.status_code == 200
    assert "Machine Registry" in response.text

def test_knowledge_page(auth_client):
    response = auth_client.get("/knowledge")
    assert response.status_code == 200
    assert "Collective Knowledge & Evidence Repository" in response.text

def test_tasks_page(auth_client):
    response = auth_client.get("/tasks")
    assert response.status_code == 200
    assert "Persistent Task Queue" in response.text

def test_inference_page(auth_client):
    response = auth_client.get("/inference")
    assert response.status_code == 200
    assert "Inference Management &amp; Distributed Models" in response.text or "Inference Management" in response.text

def test_settings_page(auth_client):
    response = auth_client.get("/settings")
    assert response.status_code == 200
    assert "Online Execution Worker (VPS)" in response.text
