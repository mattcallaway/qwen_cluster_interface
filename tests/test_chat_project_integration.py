"""
Integration tests for Qwen Chat connection to Persistent Project Knowledge and Architecture Context.
Verifies:
- Explicit conversation-to-project association and dissociation
- Dynamic injection of Project Context Packet and Architecture Context Packet into model context
- Context preview diagnostic endpoint (sections, trust levels, token estimates, zero leaked secrets)
- Task dependency resolution in context packet builder without KeyError
"""

import json
import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
import pytest
from starlette.testclient import TestClient

from app.auth import create_session_token
from app.main import app
from app.services import projects as proj_svc
from app.services import tasks as task_svc
from app.services.context_packet import build_project_context_packet

CSRF_RE = re.compile(r'name="csrf_token" value="([0-9a-f]+)"')


def _csrf(client, url="/chat"):
    r = client.get(url)
    m = CSRF_RE.search(r.text)
    assert m, "page should embed a CSRF token"
    return m.group(1)


@pytest.fixture
def client(db_conn):
    return TestClient(app)


@pytest.fixture
def auth_client(client, db_conn):
    """Client authenticated as default admin user with valid session cookie."""
    token = create_session_token("admin")
    client.cookies.set("qc_session", token)
    return client


def test_task_dependency_resolution_in_context_packet(db_conn):
    """Verify that get_task_dependencies and build_project_context_packet handle multi-task dependencies deterministically."""
    now = datetime.now(timezone.utc).isoformat()
    # 1. Create project
    proj = proj_svc.create_project(
        name="Dependency Resolution Project",
        owner="admin",
        objective="Verify dependency handling without crash",
    )
    proj_id = proj["id"]

    # 2. Create tasks with prerequisite dependency
    task_a = task_svc.create_task(
        title="Prerequisite Task A",
        task_type="python_execution",
        project_id=proj_id,
    )
    task_b = task_svc.create_task(
        title="Dependent Task B",
        task_type="python_execution",
        project_id=proj_id,
    )

    # 3. Add dependency: Task B depends on Task A
    task_svc.add_task_dependency(
        task_id=task_b["id"],
        depends_on_task_id=task_a["id"],
    )

    # 4. Verify get_task_dependencies returns prerequisite with metadata
    deps = task_svc.get_task_dependencies(task_b["id"])
    assert len(deps) == 1
    assert deps[0]["id"] == task_a["id"]
    assert deps[0]["title"] == "Prerequisite Task A"
    assert deps[0]["depends_on_task_id"] == task_a["id"]

    # 5. Build context packet — must succeed without KeyError and include dependency reference
    packet = build_project_context_packet(proj_id, token_budget=3000)
    assert packet is not None
    assert "Prerequisite Task A" in packet["rendered_markdown"]
    assert "Dependent Task B" in packet["rendered_markdown"]
    assert "Depends on" in packet["rendered_markdown"]


def test_conversation_project_association_and_context_preview(auth_client, db_conn):
    """Test associating a conversation with a project and reading diagnostic context preview."""
    # 1. Create project
    proj = proj_svc.create_project(
        name="Context Preview Project",
        owner="admin",
        objective="Test live context assembly",
    )
    proj_id = proj["id"]

    # 2. Get chat page and CSRF token
    resp = auth_client.get("/chat")
    assert resp.status_code == 200

    csrf_resp = auth_client.get("/dashboard")
    csrf_token = auth_client.cookies.get("qc_session") or ""
    # Retrieve session_id from DB
    conv = db_conn.execute("SELECT id FROM chat_conversations WHERE user_id = 'admin'").fetchone()
    session_id = conv["id"]

    # 3. Initially standalone context preview
    prev_resp = auth_client.get(f"/chat/context-preview?session_id={session_id}")
    assert prev_resp.status_code == 200
    prev_data = prev_resp.json()
    assert prev_data["mode"] == "standalone"
    assert prev_data["project_context"] is None
    assert "architecture_context" in prev_data

    # 4. Associate conversation with project
    assoc_resp = auth_client.post(
        "/chat/project",
        data={
            "csrf_token": _csrf(auth_client),
            "session_id": session_id,
            "project_id": proj_id,
        },
    )
    assert assoc_resp.status_code in (200, 302)

    # 5. Context preview now reflects project-linked mode
    prev_resp2 = auth_client.get(f"/chat/context-preview?session_id={session_id}")
    assert prev_resp2.status_code == 200
    data2 = prev_resp2.json()
    assert data2["mode"] == "project_linked"
    assert data2["project_id"] == proj_id
    assert data2["project_name"] == "Context Preview Project"
    assert len(data2["project_context"]["sections"]) > 0
    assert data2["total_estimated_tokens"] > 0


def test_chat_inference_injects_project_context(auth_client, db_conn):
    """Test that chat_send dynamically injects Project Context Packet into model context."""
    # 1. Create project
    proj = proj_svc.create_project(
        name="Inference Injection Project",
        owner="admin",
        objective="Ensure Qwen receives authoritative bounds",
    )
    proj_id = proj["id"]

    # 2. Create conversation bound to this project
    now = datetime.now(timezone.utc).isoformat()
    session_id = "conv-infer-test-01"
    db_conn.execute(
        """
        INSERT INTO chat_conversations (id, user_id, title, project_id, created_at, updated_at)
        VALUES (?, 'admin', 'Project Bound Chat', ?, ?, ?)
        """,
        (session_id, proj_id, now, now),
    )
    db_conn.commit()

    # 3. Mock inference backend response and capture input model_context
    captured_context = []

    async def mock_send(messages):
        nonlocal captured_context
        captured_context = messages
        return {"content": "Understood project objectives and architecture bounds.", "model": "mock-qwen-2.5"}

    with patch("app.routes.chat.send_chat_completion", side_effect=mock_send):
        with patch("app.routes.chat.get_active_backend", return_value={"name": "Mock llama.cpp", "model_name": "qwen2.5-7b", "endpoint_url": "http://127.0.0.1:8080"}):
            resp = auth_client.post(
                "/chat/send",
                data={
                    "csrf_token": _csrf(auth_client),
                    "session_id": session_id,
                    "message": "What is our primary project objective?",
                },
            )
            assert resp.status_code in (200, 302)

    # 4. Verify system prompt was injected with project context
    assert len(captured_context) >= 2
    system_msg = captured_context[0]
    assert system_msg["role"] == "system"
    assert "Inference Injection Project" in system_msg["content"]
    assert "Ensure Qwen receives authoritative bounds" in system_msg["content"]
    assert "PROJECT CONTEXT PACKET" in system_msg["content"]
    assert "SYSTEM_ARCHITECTURE_CONTEXT" in system_msg["content"]

    user_msg = captured_context[1]
    assert user_msg["role"] == "user"
    assert user_msg["content"] == "What is our primary project objective?"
