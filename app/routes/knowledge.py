"""Unified Knowledge Base router supporting Canonical Structured Knowledge,
Collective Promotion Governance, Knowledge Impact Tracking, and Legacy Markdown Notes Vault.
"""
import re
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Request, Form, status, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.auth import get_current_user, get_csrf_token, verify_csrf
from app.services.knowledge import (
    list_all_notes,
    read_note,
    write_note_atomic,
    search_knowledge,
    rebuild_knowledge_index,
    get_knowledge_categories,
)
from app.services.knowledge_meta import get_metadata_by_path, UNTRUSTED_ORIGINS
from app.services.knowledge_store import (
    create_knowledge_record,
    get_knowledge_record,
    list_knowledge_records,
    search_knowledge_records,
    evaluate_freshness,
    list_promotions,
    approve_promotion,
    reject_promotion,
    list_affected_impacts,
    supersede_knowledge_record,
    VALID_KNOWLEDGE_TYPES,
    VALID_SCOPES,
    VALID_VERIFICATION_STATUSES,
)
from app.database import get_db_connection

router = APIRouter(tags=["knowledge"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/knowledge", response_class=HTMLResponse)
def knowledge_index(
    request: Request,
    view: str = "records",
    q: Optional[str] = None,
    scope: Optional[str] = None,
    knowledge_type: Optional[str] = None,
    verification_status: Optional[str] = None,
    category: Optional[str] = None,
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)

    records: List[Dict[str, Any]] = []
    search_results: List[Dict[str, Any]] = []
    if q and q.strip():
        records = search_knowledge_records(
            q.strip(),
            scope=scope,
            verification_status=verification_status,
            include_collective=True,
            limit=50
        )
        from app.services.knowledge import search_knowledge
        search_results = search_knowledge(q.strip())
    else:
        records = list_knowledge_records(
            scope=scope,
            knowledge_type=knowledge_type,
            verification_status=verification_status,
            include_collective=True,
            limit=50
        )

    # Attach freshness status to records
    for r in records:
        r["freshness"] = evaluate_freshness(r)

    promotions = list_promotions()
    pending_promotions = [p for p in promotions if p["status"] == "proposed"]

    # Freshness policies
    conn = get_db_connection()
    try:
        policies = [dict(p) for p in conn.execute("SELECT * FROM knowledge_freshness_policies ORDER BY max_age_days").fetchall()]
        affected_impacts_all = [dict(r) for r in conn.execute(
            """SELECT i.*, k.subject, k.statement, p.name as project_name
               FROM knowledge_impacts i
               JOIN knowledge_records k ON k.id = i.knowledge_id
               JOIN projects p ON p.id = i.project_id
               WHERE i.is_affected = 1
               ORDER BY i.flagged_at DESC LIMIT 30"""
        ).fetchall()]
    finally:
        conn.close()

    # Legacy vault notes
    categories = get_knowledge_categories()
    vault_notes = list_all_notes(category) if view == "vault" else []

    return templates.TemplateResponse(request=request, name="knowledge/index.html", context={
        "active_nav": "knowledge",
        "current_user": user,
        "csrf_token": get_csrf_token(request),
        "view": view,
        "query": q or "",
        "search_results": search_results,
        "selected_scope": scope or "",
        "selected_type": knowledge_type or "",
        "selected_status": verification_status or "",
        "records": records,
        "promotions": promotions,
        "pending_promotions": pending_promotions,
        "affected_impacts": affected_impacts_all,
        "policies": policies,
        "categories": categories,
        "current_cat": category,
        "vault_notes": vault_notes,
        "knowledge_types": VALID_KNOWLEDGE_TYPES,
        "verification_statuses": VALID_VERIFICATION_STATUSES,
        "scopes": VALID_SCOPES,
    })


@router.post("/knowledge/records/create")
def create_record_submit(
    request: Request,
    csrf_token: str = Form(...),
    subject: str = Form(...),
    statement: str = Form(...),
    summary: str = Form(""),
    knowledge_type: str = Form("fact"),
    scope: str = Form("project"),
    project_id: Optional[str] = Form(None),
    verification_status: str = Form("supported"),
    topics: str = Form(""),
):
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return RedirectResponse(url="/knowledge", status_code=status.HTTP_302_FOUND)

    top_list = [t.strip() for t in topics.split(",") if t.strip()] if topics else []
    try:
        create_knowledge_record(
            subject=subject,
            statement=statement,
            summary=summary,
            knowledge_type=knowledge_type,
            scope=scope,
            project_id=project_id if scope == "project" else None,
            verification_status=verification_status,
            topics=top_list,
            promoted_by=user.get("username") if scope == "collective" else None,
        )
    except Exception:
        pass
    return RedirectResponse(url="/knowledge?view=records", status_code=status.HTTP_302_FOUND)


@router.post("/knowledge/promotions/{promotion_id}/approve")
def approve_promotion_submit(
    request: Request,
    promotion_id: str,
    csrf_token: str = Form(...),
    rationale: str = Form(""),
):
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return RedirectResponse(url="/knowledge?view=promotions", status_code=status.HTTP_302_FOUND)

    try:
        approve_promotion(promotion_id, reviewer=user.get("username", "admin"), rationale=rationale)
    except Exception:
        pass
    return RedirectResponse(url="/knowledge?view=promotions", status_code=status.HTTP_302_FOUND)


@router.post("/knowledge/promotions/{promotion_id}/reject")
def reject_promotion_submit(
    request: Request,
    promotion_id: str,
    csrf_token: str = Form(...),
    rationale: str = Form(""),
):
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return RedirectResponse(url="/knowledge?view=promotions", status_code=status.HTTP_302_FOUND)

    try:
        reject_promotion(promotion_id, reviewer=user.get("username", "admin"), rationale=rationale)
    except Exception:
        pass
    return RedirectResponse(url="/knowledge?view=promotions", status_code=status.HTTP_302_FOUND)


@router.post("/knowledge/records/{record_id}/flag-obsolete")
def flag_obsolete_submit(
    request: Request,
    record_id: str,
    csrf_token: str = Form(...),
    reason: str = Form("Marked obsolete by user"),
):
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return RedirectResponse(url="/knowledge", status_code=status.HTTP_302_FOUND)

    from app.services.knowledge_store import flag_affected_impacts
    from app.database import db_transaction
    with db_transaction() as conn:
        conn.execute(
            "UPDATE knowledge_records SET verification_status = 'outdated', updated_at = datetime('now') WHERE id = ?",
            (record_id,)
        )
    flag_affected_impacts(record_id, reason)
    return RedirectResponse(url="/knowledge?view=records", status_code=status.HTTP_302_FOUND)


# ---------------------------------------------------------------------------
# JSON APIs for programmatic access and Observatory integration
# ---------------------------------------------------------------------------

@router.get("/api/knowledge/records")
def api_list_records(
    project_id: Optional[str] = None,
    scope: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 50
):
    if q and q.strip():
        recs = search_knowledge_records(q.strip(), project_id=project_id, scope=scope, limit=limit)
    else:
        recs = list_knowledge_records(project_id=project_id, scope=scope, limit=limit)
    return JSONResponse(content={"records": recs, "count": len(recs)})


@router.get("/api/knowledge/impacts/{project_id}")
def api_project_impacts(project_id: str):
    impacts = list_affected_impacts(project_id)
    return JSONResponse(content={"affected_impacts": impacts, "count": len(impacts)})


@router.get("/api/knowledge/checkpoints/{project_id}")
def api_project_checkpoints(project_id: str):
    from app.services.orchestration.research_checkpoint import list_project_checkpoints
    checkpoints = list_project_checkpoints(project_id)
    return JSONResponse(content={"checkpoints": checkpoints, "count": len(checkpoints)})


# ---------------------------------------------------------------------------
# Legacy Markdown Vault Routes
# ---------------------------------------------------------------------------

@router.get("/knowledge/view", response_class=HTMLResponse)
def view_note(request: Request, path: str):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)

    note = read_note(path)
    if not note:
        return RedirectResponse(url="/knowledge", status_code=status.HTTP_302_FOUND)

    return templates.TemplateResponse(request=request, name="knowledge/note.html", context={
        "active_nav": "knowledge",
        "current_user": user,
        "csrf_token": get_csrf_token(request),
        "note": note,
        "kmeta": get_metadata_by_path(note["path"]),
        "untrusted_origins": UNTRUSTED_ORIGINS,
    })


@router.get("/knowledge/new", response_class=HTMLResponse)
def new_note_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)

    return templates.TemplateResponse(request=request, name="knowledge/new.html", context={
        "active_nav": "knowledge",
        "current_user": user,
        "csrf_token": get_csrf_token(request)
    })


@router.post("/knowledge/create")
def create_note_submit(
    request: Request,
    csrf_token: str = Form(...),
    title: str = Form(...),
    category: str = Form(...),
    content: str = Form(...)
):
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return RedirectResponse(url="/knowledge", status_code=status.HTTP_302_FOUND)

    clean_filename = re.sub(r'[^\w\-_\. ]', '', title.strip().lower()).replace(" ", "_")
    if not clean_filename.endswith(".md"):
        clean_filename += ".md"

    rel_path = f"{category}/{clean_filename}"
    full_content = f"""---
title: {title.strip()}
category: {category}
---

{content.strip()}
"""
    write_note_atomic(rel_path, full_content)
    return RedirectResponse(url=f"/knowledge/view?path={rel_path}", status_code=status.HTTP_302_FOUND)


@router.post("/knowledge/save")
def save_note_submit(
    request: Request,
    csrf_token: str = Form(...),
    path: str = Form(...),
    content: str = Form(...)
):
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return RedirectResponse(url=f"/knowledge/view?path={path}", status_code=status.HTTP_302_FOUND)

    write_note_atomic(path, content)
    return RedirectResponse(url=f"/knowledge/view?path={path}", status_code=status.HTTP_302_FOUND)


@router.post("/knowledge/reindex")
def reindex_submit(request: Request, csrf_token: str = Form(...)):
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return RedirectResponse(url="/knowledge", status_code=status.HTTP_302_FOUND)

    rebuild_knowledge_index()
    return RedirectResponse(url="/knowledge", status_code=status.HTTP_302_FOUND)
