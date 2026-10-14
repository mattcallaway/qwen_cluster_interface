import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from fastapi import APIRouter, Request, Form, Query, status, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.auth import get_current_user, get_csrf_token, verify_csrf
from app.database import get_db_connection
from app.services.inference import get_active_backend, send_chat_completion
from app.services.context_packet import build_project_context_packet
from app.services.infrastructure import build_architecture_context_packet
from app.services import projects as proj_svc

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
    Returns (conversation_id, title).
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


def get_conversation_project_id(session_id: str) -> Optional[str]:
    """Retrieve the project_id bound to a conversation session, if any."""
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT project_id FROM chat_conversations WHERE id = ?", (session_id,)).fetchone()
        return row["project_id"] if row and row["project_id"] else None
    finally:
        conn.close()


def build_system_context(project_id: Optional[str], search_query: str = "") -> Optional[str]:
    """
    Build structured, authoritative system prompt integrating Project Context Packet
    and Architecture Context Packet. Strictly isolates authoritative instructions.
    """
    if not project_id:
        return None

    try:
        proj_packet = build_project_context_packet(
            project_id=project_id,
            token_budget=2000,
            search_query=search_query,
        )
    except Exception as pe:
        logger.warning("Could not build project context packet for %s: %s", project_id, pe)
        proj_packet = None

    try:
        arch_packet = build_architecture_context_packet(
            token_budget=1000,
            user_query=search_query,
        )
    except Exception as ae:
        logger.warning("Could not build architecture context packet: %s", ae)
        arch_packet = None

    parts = [
        "You are Qwen Collective's coordinating operational intelligence.",
        "You operate strictly within approved project bounds and verified infrastructure reality.\n",
    ]

    if proj_packet:
        parts.append(proj_packet["rendered_markdown"])

    if arch_packet:
        parts.append(arch_packet["rendered_prompt"])

    parts.append(
        "\n### OPERATIONAL RULES FOR QWEN INTELLIGENCE\n"
        "1. Authoritative Bounds: Follow the approved Path and active Stage. Do not invent goals or alter accepted deliverables.\n"
        "2. Verification Invariant: Only state that capabilities exist or tasks succeeded if verified in the context above.\n"
        "3. Separation of Concerns: External or retrieved assertions must be validated before becoming authoritative.\n"
    )

    return "\n".join(parts)


def build_context_messages(session_id: str, project_id: Optional[str] = None, user_prompt: str = "", char_budget: int = DEFAULT_CONTEXT_CHAR_BUDGET) -> List[Dict[str, str]]:
    """
    Select the most recent conversation turns within context budget,
    prepending authoritative Project & Architecture Context as system message.
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

    # Prepend authoritative system prompt with Project Context Packet & Architecture Context
    if project_id:
        system_content = build_system_context(project_id, search_query=user_prompt)
        if system_content:
            budget_turns.insert(0, {"role": "system", "content": system_content})

    return budget_turns


@router.get("/chat", response_class=HTMLResponse)
def chat_view(request: Request, session_id: Optional[str] = Query(None)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)

    username = user if isinstance(user, str) else user["username"]
    active_session_id, active_title = get_or_create_conversation(username, session_id)
    active_project_id = get_conversation_project_id(active_session_id)
    conversations = get_user_conversations(username)
    active_backend = get_active_backend()
    all_projects = proj_svc.list_projects()

    active_project = None
    if active_project_id:
        active_project = next((p for p in all_projects if p["id"] == active_project_id), None)

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
        "active_project_id": active_project_id,
        "active_project": active_project,
        "projects": all_projects,
        "conversations": conversations,
    })


@router.post("/chat/new")
def chat_new(request: Request, csrf_token: str = Form(...), project_id: Optional[str] = Form(None)):
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return RedirectResponse(url="/chat", status_code=status.HTTP_302_FOUND)

    username = user if isinstance(user, str) else user["username"]
    new_id = f"conv-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()
    pid = project_id.strip() if project_id and project_id.strip() else None
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO chat_conversations (id, user_id, title, project_id, created_at, updated_at)
    VALUES (?, ?, 'New Conversation', ?, ?, ?)
    """, (new_id, username, pid, now, now))
    conn.commit()
    conn.close()

    return RedirectResponse(url=f"/chat?session_id={new_id}", status_code=status.HTTP_302_FOUND)


@router.post("/chat/project")
def chat_set_project(
    request: Request,
    csrf_token: str = Form(...),
    session_id: str = Form(...),
    project_id: Optional[str] = Form(None),
):
    """Associate or disassociate a conversation with a project."""
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return RedirectResponse(url=f"/chat?session_id={session_id}", status_code=status.HTTP_302_FOUND)

    username = user if isinstance(user, str) else user["username"]
    active_session_id, _ = get_or_create_conversation(username, session_id)
    pid = project_id.strip() if project_id and project_id.strip() else None

    conn = get_db_connection()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE chat_conversations SET project_id = ?, updated_at = ? WHERE id = ? AND user_id = ?",
        (pid, now, active_session_id, username),
    )
    conn.commit()
    conn.close()

    return RedirectResponse(url=f"/chat?session_id={active_session_id}", status_code=status.HTTP_302_FOUND)


@router.get("/chat/context-preview")
def chat_context_preview(
    request: Request,
    session_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
):
    """
    Read-only diagnostic view showing the actual context assembled for Qwen.
    Displays sections, sources, trust levels, token estimates, and omissions without leaking secrets.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")

    username = user if isinstance(user, str) else user["username"]
    target_project_id = project_id
    if not target_project_id and session_id:
        active_session_id, _ = get_or_create_conversation(username, session_id)
        target_project_id = get_conversation_project_id(active_session_id)

    now_iso = datetime.now(timezone.utc).isoformat()

    if not target_project_id:
        arch_packet = build_architecture_context_packet(token_budget=1500)
        return {
            "mode": "standalone",
            "project_context": None,
            "architecture_context": {
                "estimated_tokens": arch_packet["estimated_tokens"],
                "node_count": arch_packet["node_count"],
                "verified_capability_count": arch_packet["verified_capability_count"],
                "unverified_capability_count": arch_packet["unverified_capability_count"],
                "omitted_priorities": arch_packet.get("omitted_priorities", []),
                "rendered_prompt_preview": arch_packet["rendered_prompt"][:800] + "...",
            },
            "total_estimated_tokens": arch_packet["estimated_tokens"],
            "timestamp": now_iso,
            "notice": "Conversation operates in standalone / project-independent mode.",
        }

    proj = proj_svc.get_project(target_project_id)
    proj_packet = build_project_context_packet(target_project_id, token_budget=2500)
    arch_packet = build_architecture_context_packet(token_budget=1200)
    total_tokens = proj_packet["estimated_tokens"] + arch_packet["estimated_tokens"]

    return {
        "mode": "project_linked",
        "project_id": target_project_id,
        "project_name": proj["name"] if proj else "Unknown",
        "project_context": {
            "estimated_tokens": proj_packet["estimated_tokens"],
            "token_budget": proj_packet["token_budget"],
            "sections": [
                {
                    "name": s["name"],
                    "priority": s["priority"],
                    "trust_level": s["trust_level"],
                    "tokens": s["tokens"],
                    "preview": s["content"][:300] + ("..." if len(s["content"]) > 300 else ""),
                }
                for s in proj_packet["sections"]
            ],
            "omitted_info": proj_packet.get("omitted_info", []),
        },
        "architecture_context": {
            "estimated_tokens": arch_packet["estimated_tokens"],
            "node_count": arch_packet["node_count"],
            "verified_capability_count": arch_packet["verified_capability_count"],
            "unverified_capability_count": arch_packet["unverified_capability_count"],
            "omitted_priorities": arch_packet.get("omitted_priorities", []),
        },
        "total_estimated_tokens": total_tokens,
        "timestamp": now_iso,
    }


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
    active_project_id = get_conversation_project_id(active_session_id)

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

    # Build budgeted context for model inference including project/architecture context
    model_context = build_context_messages(
        session_id=active_session_id,
        project_id=active_project_id,
        user_prompt=message.strip(),
    )

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
