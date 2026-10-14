from datetime import datetime, timezone
from fastapi import APIRouter, Request, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.auth import get_current_user, get_csrf_token, verify_csrf
from app.database import get_db_connection
from app.services.inference import get_active_backend, send_chat_completion

router = APIRouter(tags=["chat"])
templates = Jinja2Templates(directory="app/templates")

SESSION_ID = "default-session"

@router.get("/chat", response_class=HTMLResponse)
def chat_view(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)

    active_backend = get_active_backend()

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM chat_messages WHERE session_id = ? ORDER BY created_at ASC", (SESSION_ID,))
    messages = [dict(r) for r in cursor.fetchall()]
    conn.close()

    return templates.TemplateResponse(request=request, name="chat.html", context={
        "active_nav": "chat",
        "current_user": user,
        "csrf_token": get_csrf_token(request),
        "active_backend": active_backend,
        "messages": messages
    })

@router.post("/chat/send")
async def chat_send(request: Request, csrf_token: str = Form(...), message: str = Form(...)):
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return RedirectResponse(url="/chat", status_code=status.HTTP_302_FOUND)

    active_backend = get_active_backend()
    if not active_backend:
        return RedirectResponse(url="/chat", status_code=status.HTTP_302_FOUND)

    now = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Store user message
    cursor.execute(
        "INSERT INTO chat_messages (session_id, role, content, backend_used, model_used, created_at) VALUES (?, 'user', ?, ?, ?, ?)",
        (SESSION_ID, message.strip(), active_backend["name"], active_backend.get("model_name"), now)
    )
    conn.commit()

    # Fetch conversation context
    cursor.execute("SELECT role, content FROM chat_messages WHERE session_id = ? ORDER BY created_at ASC LIMIT 20", (SESSION_ID,))
    history = [{"role": r["role"], "content": r["content"]} for r in cursor.fetchall()]
    conn.close()

    # Query active backend
    response = await send_chat_completion(history)
    
    assistant_content = response.get("content") or f"Error from model backend: {response.get('error')}"
    resp_now = datetime.now(timezone.utc).isoformat()

    conn = get_db_connection()
    conn.execute(
        "INSERT INTO chat_messages (session_id, role, content, backend_used, model_used, created_at) VALUES (?, 'assistant', ?, ?, ?, ?)",
        (SESSION_ID, assistant_content, active_backend["name"], response.get("model"), resp_now)
    )
    conn.commit()
    conn.close()

    return RedirectResponse(url="/chat", status_code=status.HTTP_302_FOUND)

@router.post("/chat/clear")
def chat_clear(request: Request, csrf_token: str = Form(...)):
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return RedirectResponse(url="/chat", status_code=status.HTTP_302_FOUND)

    conn = get_db_connection()
    conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (SESSION_ID,))
    conn.commit()
    conn.close()

    return RedirectResponse(url="/chat", status_code=status.HTTP_302_FOUND)
