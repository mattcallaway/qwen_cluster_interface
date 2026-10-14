from typing import Optional
from fastapi import APIRouter, Request, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.auth import get_current_user, get_csrf_token, verify_csrf
from app.services.tasks import (
    get_all_tasks,
    get_task_by_id,
    create_task,
    approve_task,
    cancel_task,
    get_task_counts
)

router = APIRouter(tags=["tasks"])
templates = Jinja2Templates(directory="app/templates")

@router.get("/tasks", response_class=HTMLResponse)
def tasks_index(request: Request, status_filter: Optional[str] = None):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)

    tasks = get_all_tasks(limit=100, status_filter=status_filter)
    counts = get_task_counts()

    return templates.TemplateResponse(request=request, name="tasks/index.html", context={
        "active_nav": "tasks",
        "current_user": user,
        "csrf_token": get_csrf_token(request),
        "tasks": tasks,
        "task_counts": counts,
        "current_status": status_filter
    })

@router.post("/tasks/create")
def create_task_submit(
    request: Request,
    csrf_token: str = Form(...),
    title: str = Form(...),
    description: Optional[str] = Form(""),
    task_type: str = Form("general"),
    approval_required: int = Form(1)
):
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return RedirectResponse(url="/tasks", status_code=status.HTTP_302_FOUND)

    task = create_task(
        title=title,
        description=description or "",
        task_type=task_type,
        approval_required=bool(approval_required)
    )
    return RedirectResponse(url=f"/tasks/{task['id']}", status_code=status.HTTP_302_FOUND)

@router.get("/tasks/{task_id}", response_class=HTMLResponse)
def task_detail_view(request: Request, task_id: str):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)

    task = get_task_by_id(task_id)
    if not task:
        return RedirectResponse(url="/tasks", status_code=status.HTTP_302_FOUND)

    return templates.TemplateResponse(request=request, name="tasks/detail.html", context={
        "active_nav": "tasks",
        "current_user": user,
        "csrf_token": get_csrf_token(request),
        "task": task
    })

@router.post("/tasks/{task_id}/approve")
def task_approve(request: Request, task_id: str, csrf_token: str = Form(...)):
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return RedirectResponse(url="/tasks", status_code=status.HTTP_302_FOUND)

    approve_task(task_id)
    return RedirectResponse(url=f"/tasks/{task_id}", status_code=status.HTTP_302_FOUND)

@router.post("/tasks/{task_id}/cancel")
def task_cancel(request: Request, task_id: str, csrf_token: str = Form(...)):
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return RedirectResponse(url="/tasks", status_code=status.HTTP_302_FOUND)

    cancel_task(task_id)
    return RedirectResponse(url=f"/tasks/{task_id}", status_code=status.HTTP_302_FOUND)
