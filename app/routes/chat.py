import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from fastapi import APIRouter, Request, Form, Query, status, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.auth import get_current_user, get_csrf_token, verify_csrf
from app.database import get_db_connection
from app.services.inference import get_active_backend, send_chat_completion

router = APIRouter(tags=["chat"])
templates = Jinja2Templates(directory="app/templates")

# Configurable character budget for prompt context (approx 2,500 tokens)
DEFAULT_CONTEXT_CHAR_BUDGET = 10000

def get_user_conversations(username: str) -> List[Dict[str, Any]]:
    """Retrieve all conversations belonging to the user."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM chat_conversations WHERE user_id = ? ORDER BY updated_at DESC",
        (username,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_or_create_conversation(username: str, session_id: Optional[str] = None) -> Tuple[str, str]:
    """
    Ensure the user accesses an authorized conversation.
    Prevents context inheritance and unauthorized access across users.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()

    # 1. If explicit session_id provided, verify ownership or migrate legacy session
    if session_id:
        cursor.execute("SELECT * FROM chat_conversations WHERE id = ?", (session_id,))
        conv = cursor.fetchone()
        if conv:
            if conv["user_id"] != username:
                conn.close()
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to conversation.")
            conn.close()
            return conv["id"], conv["title"]
        elif session_id == "default-session":
            # Preserve legacy session by registering it to current user
            cursor.execute("""
            INSERT INTO chat_conversations (id, user_id, title, created_at, updated_at)
            VALUES ('default-session', ?, 'Default Conversation', ?, ?)
            """, (username, now, now))
            conn.commit()
            conn.close()
            return "default-session", "Default Conversation"

    # 2. If no session_id, retrieve user's most recent conversation
    cursor.execute(
        "SELECT * FROM chat_conversations WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1",
        (username,)
    )
    last_conv = cursor.fetchone()
    if last_conv:
        conn.close()
        return last_conv["id"], last_conv["title"]

    # 3. Create a fresh conversation with unique persistent ID
    new_id = f"conv-{uuid.uuid4().hex[:8]}"
    cursor.execute("""
    INSERT INTO chat_conversations (id, user_id, title, created_at, updated_at)
    VALUES (?, ?, 'New Conversation', ?, ?)
    """, (new_id, username, now, now))
    conn.commit()
    conn.close()
    return new_id, "New Conversation"

def build_context_messages(session_id: str, char_budget: int = DEFAULT_CONTEXT_CHAR_BUDGET) -> List[Dict[str, str]]:
    """
    Select the most recent conversation turns within context budget,
    returning them in true chronological order to the model.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    # Fetch recent messages newest first to apply context budget
    cursor.execute(
        "SELECT id, role, content FROM chat_messages WHERE session_id = ? ORDER BY id DESC LIMIT 50",
        (session_id,)
    )
    recent_turns = cursor.fetchall()
    conn.close()

    budget_turns = []
    current_chars = 0
    for r in recent_turns:
        length = len(r["content"])
        if current_chars + length > char_budget and budget_turns:
            break
        budget_turns.append({"role": r["role"], "content": r["content"]})
        current_chars += length

    # Reverse to restore strict chronological order for the model
    budget_turns.reverse()
    return budget_turns

@router.get("/chat", response_class=HTMLResponse)
def chat_view(request: Request, session_id: Optional[str] = Query(None)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)

    username = user if isinstance(user, str) else user["username"]
    active_session_id, active_title = get_or_create_conversation(username, session_id)
    conversations = get_user_conversations(username)
    active_backend = get_active_backend()

    # Load complete stored message history for active conversation UI display
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY id ASC",
        (active_session_id,)
    )
    messages = [dict(r) for r in cursor.fetchall()]
    conn.close()

    return templates.TemplateResponse(request=request, name="chat.html", context={
        "active_nav": "chat",
        "current_user": user,
        "csrf_token": get_csrf_token(request),
        "active_backend": active_backend,
        "messages": messages,
        "session_id": active_session_id,
        "active_title": active_title,
        "conversations": conversations
    })

@router.post("/chat/new")
def chat_new(request: Request, csrf_token: str = Form(...)):
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return RedirectResponse(url="/chat", status_code=status.HTTP_302_FOUND)

    username = user if isinstance(user, str) else user["username"]
    new_id = f"conv-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO chat_conversations (id, user_id, title, created_at, updated_at)
    VALUES (?, ?, 'New Conversation', ?, ?)
    """, (new_id, username, now, now))
    conn.commit()
    conn.close()

    return RedirectResponse(url=f"/chat?session_id={new_id}", status_code=status.HTTP_302_FOUND)

@router.post("/chat/send")
async def chat_send(
    request: Request,
    csrf_token: str = Form(...),
    message: str = Form(...),
    session_id: str = Form("default-session")
):
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return RedirectResponse(url=f"/chat?session_id={session_id}", status_code=status.HTTP_302_FOUND)

    username = user if isinstance(user, str) else user["username"]
    active_session_id, _ = get_or_create_conversation(username, session_id)

    active_backend = get_active_backend()
    if not active_backend:
        return RedirectResponse(url=f"/chat?session_id={active_session_id}", status_code=status.HTTP_302_FOUND)

    now = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Store user message
    cursor.execute(
        "INSERT INTO chat_messages (session_id, role, content, backend_used, model_used, created_at) VALUES (?, 'user', ?, ?, ?, ?)",
        (active_session_id, message.strip(), active_backend["name"], active_backend.get("model_name"), now)
    )
    # Update conversation timestamp and title if first prompt
    cursor.execute("SELECT COUNT(*) as c FROM chat_messages WHERE session_id = ?", (active_session_id,))
    msg_count = cursor.fetchone()["c"]
    if msg_count <= 1:
        new_title = message.strip()[:35] + ("..." if len(message.strip()) > 35 else "")
        cursor.execute(
            "UPDATE chat_conversations SET title = ?, updated_at = ? WHERE id = ?",
            (new_title, now, active_session_id)
        )
    else:
        cursor.execute("UPDATE chat_conversations SET updated_at = ? WHERE id = ?", (now, active_session_id))
    conn.commit()
    conn.close()

    # Build budgeted context for model inference (newest turns in chronological order)
    model_context = build_context_messages(active_session_id)

    # Query active backend
    response = await send_chat_completion(model_context)
    
    assistant_content = response.get("content") or f"Error from model backend: {response.get('error')}"
    resp_now = datetime.now(timezone.utc).isoformat()

    conn = get_db_connection()
    conn.execute(
        "INSERT INTO chat_messages (session_id, role, content, backend_used, model_used, created_at) VALUES (?, 'assistant', ?, ?, ?, ?)",
        (active_session_id, assistant_content, active_backend["name"], response.get("model"), resp_now)
    )
    conn.execute("UPDATE chat_conversations SET updated_at = ? WHERE id = ?", (resp_now, active_session_id))
    conn.commit()
    conn.close()

    return RedirectResponse(url=f"/chat?session_id={active_session_id}", status_code=status.HTTP_302_FOUND)

@router.post("/chat/clear")
def chat_clear(request: Request, csrf_token: str = Form(...), session_id: str = Form("default-session")):
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return RedirectResponse(url="/chat", status_code=status.HTTP_302_FOUND)

    username = user if isinstance(user, str) else user["username"]
    active_session_id, _ = get_or_create_conversation(username, session_id)

    conn = get_db_connection()
    conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (active_session_id,))
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("UPDATE chat_conversations SET updated_at = ? WHERE id = ?", (now, active_session_id))
    conn.commit()
    conn.close()

    return RedirectResponse(url=f"/chat?session_id={active_session_id}", status_code=status.HTTP_302_FOUND)
