import logging
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
from app.services import deliverables as deliv_svc

logger = logging.getLogger("qwen_collective.routes.chat")
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
    all_projects = proj_svc.list_projects()

    # If no explicit project_id, attempt to match project name or ID mentioned in search_query
    if not project_id and search_query:
        query_lower = search_query.lower()
        for p in all_projects:
            if p["name"].lower() in query_lower or p["id"].lower() in query_lower:
                project_id = p["id"]
                break

    if not project_id:
        # Build portfolio overview across all projects
        arch_packet = None
        try:
            arch_packet = build_architecture_context_packet(token_budget=1000, user_query=search_query)
        except Exception as ae:
            logger.warning("Could not build architecture context packet: %s", ae)

        proj_summary_lines = ["### Portfolio of Collective Projects"]
        if all_projects:
            for p in all_projects[:8]:
                desc = p.get("objective") or p.get("description") or "No objective recorded"
                proj_summary_lines.append(f"- **{p['name']}** (ID: `{p['id']}` | Status: {p['status'].upper()}): {desc}")
                try:
                    delivs = deliv_svc.list_deliverables(p["id"])
                    for d in delivs[:3]:
                        proj_summary_lines.append(f"  - Deliverable: **{d['name']}** [{d['status'].upper()}]" + (f" (Artifacts: {d['artifact_references']})" if d.get('artifact_references') else ""))
                except Exception:
                    pass
        else:
            proj_summary_lines.append("- *No projects registered.*")

        parts = [
            "You are Qwen Collective's coordinating operational intelligence.",
            "You have complete visibility into all projects, deliverables, tasks, and system architecture across the collective.\n",
            "\n".join(proj_summary_lines) + "\n",
        ]
        if arch_packet:
            parts.append(arch_packet["rendered_prompt"])

        parts.append(
            "\n### OPERATIONAL RULES FOR QWEN INTELLIGENCE\n"
            "1. Authoritative Bounds: Follow approved project paths and active stages. Do not invent goals or alter accepted deliverables.\n"
            "2. Deliverables & Knowledge Access: You have direct visibility into all project definitions, stages, tasks, deliverables, and attached artifact outputs provided in this context. Use this authoritative evidence to explain findings, analyze outputs, and answer user questions in detail.\n"
            "3. Separation of Concerns: External or retrieved assertions must be validated before becoming authoritative.\n"
        )
        return "\n".join(parts)

    try:
        proj_packet = build_project_context_packet(
            project_id=project_id,
            token_budget=3500,
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
        "You operate strictly within approved project bounds, verified infrastructure reality, and delivered project outputs.\n",
    ]

    if proj_packet:
        parts.append(proj_packet["rendered_markdown"])

    if arch_packet:
        parts.append(arch_packet["rendered_prompt"])

    parts.append(
        "\n### OPERATIONAL RULES FOR QWEN INTELLIGENCE\n"
        "1. Authoritative Bounds: Follow the approved Path and active Stage. Do not invent goals or alter accepted deliverables.\n"
        "2. Deliverables & Knowledge Access: You have direct visibility into the active project definition, tasks, deliverables, and attached artifact outputs provided in the context above. Use this authoritative evidence to explain findings, analyze outputs, and answer user questions in detail.\n"
        "3. Artifact Content: When asked about deliverable contents or specific output files (such as reports, summaries, recommendations), directly present and explain the findings, recommendations, and text from the attached artifacts.\n"
        "4. Separation of Concerns: External or retrieved assertions must be validated before becoming authoritative.\n"
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
        # Skip legacy refusal turns where assistant hallucinated lack of deliverable/project access
        if r["role"] == "assistant" and any(phrase in r["content"].lower() for phrase in [
            "cannot access or provide details about any project",
            "cannot access or provide details about",
            "please provide the content or details of the file `t3_ai_output.txt`",
            "please provide the context or contents of `t3_ai_output.txt`"
        ]):
            continue
        budget_turns.append({"role": r["role"], "content": r["content"]})
        current_chars += length

    # Reverse to restore strict chronological order for the model
    budget_turns.reverse()

    # Prepend authoritative system prompt with Project Context Packet & Architecture Context
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
    actual_backend = response.get("backend") or (active_backend["name"] if active_backend else "Unknown Backend")
    actual_model = response.get("model") or (active_backend.get("model_name") if active_backend else "Unknown Model")
    if response.get("fallback_occurred"):
        actual_backend = f"{actual_backend} (Fallback Tier)"

    conn = get_db_connection()
    conn.execute(
        "INSERT INTO chat_messages (session_id, role, content, backend_used, model_used, created_at) VALUES (?, 'assistant', ?, ?, ?, ?)",
        (active_session_id, assistant_content, actual_backend, actual_model, resp_now)
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


@router.post("/api/v1/chat/deliberate")
async def chat_deliberate_api(request: Request):
    """Unified conversational entry point supporting direct-response mode and durable-project mode (Increment 18)."""
    user = get_current_user(request)
    if not user:
        auth_hdr = request.headers.get("Authorization", "")
        if auth_hdr.startswith("Bearer "):
            from app.auth import verify_session_token
            from app.config import settings
            tok = auth_hdr[7:].strip()
            user = verify_session_token(tok) or ("admin" if tok == settings.admin_password else None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    try:
        body = await request.json()
    except Exception:
        body = {}

    message = str(body.get("message") or "").strip()
    project_id = body.get("project_id")
    intake_override = body.get("intake")
    action = str(body.get("action") or "auto")
    should_advance = bool(body.get("advance", False) or action == "advance")
    use_live_worker = bool(body.get("use_live_worker", False))
    actor_name = user if isinstance(user, str) else user["username"]

    from starlette.concurrency import run_in_threadpool
    from app.services.orchestration import (
        context_manager,
        deliberate_loop,
        engine,
        intake as intake_mod,
        working_memory as wm,
    )

    if action == "park" and project_id:
        try:
            park_res = await run_in_threadpool(
                wm.park_project,
                project_id,
                actor=actor_name,
                reason=message or "User requested project park via chat",
                active_process_cleanup_confirmed=None,
            )
            return {
                "ok": True,
                "mode": "durable_project",
                "action": "park",
                "park_result": park_res,
                "project_status": deliberate_loop.build_coherent_user_status(project_id),
            }
        except wm.ParkOrCancelCleanupError as exc:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={
                    "ok": False,
                    "mode": "durable_project",
                    "action": "park",
                    "error": "cleanup_unconfirmed",
                    "detail": str(exc),
                    "project_status": deliberate_loop.build_coherent_user_status(project_id),
                },
            )

    if action == "cancel" and project_id:
        try:
            cancel_res = await run_in_threadpool(
                wm.cancel_task_or_project,
                project_id,
                task_key=body.get("task_key"),
                actor=actor_name,
                reason=message or "User requested cancellation via chat",
                process_cleanup_confirmed=None,
            )
            return {
                "ok": True,
                "mode": "durable_project",
                "action": "cancel",
                "cancel_result": cancel_res,
                "project_status": deliberate_loop.build_coherent_user_status(project_id),
            }
        except wm.ParkOrCancelCleanupError as exc:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={
                    "ok": False,
                    "mode": "durable_project",
                    "action": "cancel",
                    "error": "cleanup_unconfirmed",
                    "detail": str(exc),
                    "project_status": deliberate_loop.build_coherent_user_status(project_id),
                },
            )

    if action == "resume" and project_id:
        res_out = await run_in_threadpool(wm.resume_project, project_id, actor=actor_name)
        adv_out = None
        if should_advance:
            adv_out = await run_in_threadpool(
                deliberate_loop.advance_deliberate_project,
                project_id,
                max_steps=int(body.get("max_steps", 5)),
                use_live_worker=use_live_worker,
                preferred_model=str(body.get("preferred_model") or "qwen2.5:3b"),
                quotes_data=body.get("quotes"),
                simulate_first_attempt_defect=bool(body.get("simulate_first_attempt_defect", False)),
            )
        return {
            "ok": True,
            "mode": "durable_project",
            "action": "resume",
            "resume_result": res_out,
            "advance_result": adv_out,
            "project_status": deliberate_loop.build_coherent_user_status(project_id),
        }

    if action in ("steer", "clarify") and project_id:
        run = engine.get_run_for_project(project_id)
        steer_res = await run_in_threadpool(
            intake_mod.apply_user_steering_update,
            project_id,
            run_id=run["id"] if run else None,
            actor=actor_name,
            action=action,
            updated_constraints=body.get("updated_constraints"),
            replaced_req_codes=body.get("replaced_req_codes"),
            added_deliverables=body.get("added_deliverables"),
            resolved_gaps=body.get("resolved_gaps"),
            reason=message or "Conversational steering update",
        )
        changed_reqs = list((body.get("replaced_req_codes") or {}).keys()) + list(steer_res.get("updated_req_ids") or [])
        closure_res = await run_in_threadpool(
            wm.invalidate_dependency_closure,
            project_id,
            changed_requirement_ids=changed_reqs,
            reason=message or "Conversational steering update",
        )
        adv_out = None
        if should_advance:
            adv_out = await run_in_threadpool(
                deliberate_loop.advance_deliberate_project,
                project_id,
                max_steps=int(body.get("max_steps", 5)),
                use_live_worker=use_live_worker,
                preferred_model=str(body.get("preferred_model") or "qwen2.5:3b"),
                quotes_data=body.get("quotes"),
                simulate_first_attempt_defect=bool(body.get("simulate_first_attempt_defect", False)),
            )
        return {
            "ok": True,
            "mode": "durable_project",
            "action": action,
            "steering_result": steer_res,
            "closure_invalidation": closure_res,
            "advance_result": adv_out,
            "project_status": deliberate_loop.build_coherent_user_status(project_id),
        }

    if action == "advance" and project_id:
        adv_out = await run_in_threadpool(
            deliberate_loop.advance_deliberate_project,
            project_id,
            max_steps=int(body.get("max_steps", 5)),
            use_live_worker=use_live_worker,
            preferred_model=str(body.get("preferred_model") or "qwen2.5:3b"),
            quotes_data=body.get("quotes"),
            simulate_first_attempt_defect=bool(body.get("simulate_first_attempt_defect", False)),
        )
        return {
            "ok": True,
            "mode": "durable_project",
            "action": "advance",
            "project_id": project_id,
            "advance_result": adv_out,
            "project_status": deliberate_loop.build_coherent_user_status(project_id),
        }

    mode_info = deliberate_loop.classify_request_mode(message, explicit_intake=intake_override)
    if mode_info["mode"] == "direct_response" and not project_id:
        direct_out = await run_in_threadpool(
            deliberate_loop.execute_direct_response_turn,
            message,
            preferred_model=body.get("preferred_model"),
        )
        return {
            "ok": True,
            "mode": "direct_response",
            "reason": mode_info["reason"],
            "created_project": False,
            "created_artifacts": [],
            "message": message,
            "response": direct_out["response"],
            "producing_actor": direct_out["producing_actor"],
            "verification_status": direct_out["verification_status"],
            "lease_id": direct_out.get("lease_id"),
            "model": direct_out.get("model"),
        }

    # Durable project mode
    quotes_from_intake = None
    if isinstance(intake_override, dict) and isinstance(intake_override.get("quotes"), list):
        quotes_from_intake = intake_override["quotes"]
    elif isinstance(body.get("quotes"), list):
        quotes_from_intake = body["quotes"]

    if not project_id:
        intake_payload = dict(intake_override or {"objective": message})
        if isinstance(intake_payload.get("constraints"), list):
            intake_for_engine = {**intake_payload, "constraints": "\n".join(str(x) for x in intake_payload["constraints"])}
        else:
            intake_for_engine = dict(intake_payload)
        if isinstance(intake_payload.get("deliverables"), list):
            intake_for_engine["deliverables"] = "\n".join(str(x) for x in intake_payload["deliverables"])
        if isinstance(intake_payload.get("success_criteria"), list):
            intake_for_engine["success_criteria"] = "\n".join(str(x) for x in intake_payload["success_criteria"])

        created = engine.create_project_with_run(
            name=message[:48] or "Deliberate Assignment",
            actor=actor_name,
            intake=intake_for_engine,
            autonomy_level=2,
        )
        project_id = created["project"]["id"]
        run_id = created["run"]["id"]
        ledger = intake_mod.parse_intake_requirements(intake_payload)
        intake_mod.persist_requirement_ledger(project_id, ledger, run_id=run_id, actor=actor_name)
        conflict_info = ledger.detect_conflicts_and_ambiguities()
        phase = "clarify" if (conflict_info["has_material_gaps"] or conflict_info["has_conflicts"]) else "plan"
        context_manager.save_project_memory_state(
            project_id,
            run_id=run_id,
            current_cycle_phase=phase,
            escalation_step=1,
            assumptions=conflict_info["disclosed_assumptions"],
            unresolved_questions=conflict_info["material_gaps"],
            contradictions=conflict_info["conflicts"],
            next_action={"action": "await_clarification" if phase == "clarify" else "execute_plan"},
        )
        if phase != "clarify":
            deliberate_loop.ensure_deliberate_task_contracts(project_id, run_id=run_id)
        if quotes_from_intake:
            wm.record_epistemic_item(
                project_id,
                epistemic_category="source_observation",
                statement=f"Attached {len(quotes_from_intake)} supplier quote records at intake.",
                verification_status="observed",
                run_id=run_id,
                qualifiers={"raw_quotes": quotes_from_intake},
                idempotency_key=f"intake:raw_quotes:{project_id}",
            )

    adv_out = None
    if should_advance:
        adv_out = await run_in_threadpool(
            deliberate_loop.advance_deliberate_project,
            project_id,
            max_steps=int(body.get("max_steps", 5)),
            use_live_worker=use_live_worker,
            preferred_model=str(body.get("preferred_model") or "qwen2.5:3b"),
            quotes_data=quotes_from_intake,
            simulate_first_attempt_defect=bool(body.get("simulate_first_attempt_defect", False)),
        )

    return {
        "ok": True,
        "mode": "durable_project",
        "reason": mode_info["reason"],
        "project_id": project_id,
        "advance_result": adv_out,
        "project_status": deliberate_loop.build_coherent_user_status(project_id),
    }
