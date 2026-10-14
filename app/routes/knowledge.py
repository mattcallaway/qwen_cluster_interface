import re
from typing import Optional
from fastapi import APIRouter, Request, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.auth import get_current_user, get_csrf_token, verify_csrf
from app.services.knowledge import (
    list_all_notes,
    read_note,
    write_note_atomic,
    search_knowledge,
    rebuild_knowledge_index,
    get_knowledge_categories
)

router = APIRouter(tags=["knowledge"])
templates = Jinja2Templates(directory="app/templates")

@router.get("/knowledge", response_class=HTMLResponse)
def knowledge_index(request: Request, q: Optional[str] = None, category: Optional[str] = None):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)

    categories = get_knowledge_categories()
    if q and q.strip():
        results = search_knowledge(q.strip())
        return templates.TemplateResponse(request=request, name="knowledge/index.html", context={
            "active_nav": "knowledge",
            "current_user": user,
            "csrf_token": get_csrf_token(request),
            "categories": categories,
            "query": q.strip(),
            "search_results": results,
            "notes": []
        })

    notes = list_all_notes(category)
    return templates.TemplateResponse(request=request, name="knowledge/index.html", context={
        "active_nav": "knowledge",
        "current_user": user,
        "csrf_token": get_csrf_token(request),
        "categories": categories,
        "current_cat": category,
        "query": None,
        "notes": notes,
        "search_results": []
    })

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
        "note": note
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
