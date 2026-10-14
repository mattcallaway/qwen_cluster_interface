import pytest
from starlette.testclient import TestClient
from app.main import app
from app.database import get_db_connection
from app.routes.chat import (
    get_or_create_conversation,
    build_context_messages,
    get_user_conversations
)
from app.auth import create_session_token

@pytest.fixture(autouse=True)
def clean_chat():
    conn = get_db_connection()
    conn.execute("DELETE FROM chat_messages")
    conn.execute("DELETE FROM chat_conversations")
    conn.commit()
    conn.close()
    yield
    conn = get_db_connection()
    conn.execute("DELETE FROM chat_messages")
    conn.execute("DELETE FROM chat_conversations")
    conn.commit()
    conn.close()

def test_conversation_creation_and_isolation():
    # User 1 creates conversation
    conv1_id, _ = get_or_create_conversation("user1")
    assert conv1_id.startswith("conv-")

    # User 2 gets a separate conversation
    conv2_id, _ = get_or_create_conversation("user2")
    assert conv2_id.startswith("conv-")
    assert conv1_id != conv2_id

    # User 1 cannot be hijacked by User 2
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        get_or_create_conversation("user2", session_id=conv1_id)
    assert exc.value.status_code == 403

def test_legacy_default_session_preserved():
    # Accessing legacy 'default-session' preserves/registers it for current user
    session_id, title = get_or_create_conversation("admin", session_id="default-session")
    assert session_id == "default-session"

    conn = get_db_connection()
    row = conn.execute("SELECT * FROM chat_conversations WHERE id = 'default-session'").fetchone()
    conn.close()
    assert row is not None
    assert row["user_id"] == "admin"

def test_recent_context_budgeting_and_chronological_order():
    conv_id, _ = get_or_create_conversation("admin")
    conn = get_db_connection()

    # Insert 30 conversation turns (15 user, 15 assistant)
    for i in range(30):
        role = "user" if i % 2 == 0 else "assistant"
        content = f"Turn_{i:02d}: This is message number {i}."
        conn.execute(
            "INSERT INTO chat_messages (session_id, role, content, created_at) VALUES (?, ?, ?, '2026-10-14T00:00:00Z')",
            (conv_id, role, content)
        )
    conn.commit()
    conn.close()

    # Build context with budget
    context = build_context_messages(conv_id, char_budget=2000)
    
    # Must contain the MOST RECENT messages, NOT just the first 20!
    contents = [m["content"] for m in context]
    assert any("Turn_29" in c for c in contents), "Most recent turn (Turn_29) must be in context!"
    assert any("Turn_28" in c for c in contents), "Recent turn (Turn_28) must be in context!"

    # Verify strictly chronological ordering
    indices = [int(m["content"].split(":")[0].replace("Turn_", "")) for m in context]
    assert indices == sorted(indices), "Context messages must be in strictly ascending chronological order!"

def test_chat_view_authenticated(monkeypatch):
    client = TestClient(app)
    token = create_session_token("admin")
    client.cookies.set("qc_session", token)

    # Load /chat
    resp = client.get("/chat")
    assert resp.status_code == 200
    assert "Local Model Conversation" in resp.text
