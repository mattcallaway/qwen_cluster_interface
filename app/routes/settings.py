from fastapi import APIRouter, Request, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.auth import get_current_user, get_csrf_token, verify_csrf
from app.config import settings
from app.services.vps import (
    get_vps_nodes,
    register_vps_node,
    probe_vps_health,
    execute_vps_test_task
)
from app.services.tasks import create_task, update_task_progress

router = APIRouter(tags=["settings"])
templates = Jinja2Templates(directory="app/templates")

@router.get("/settings", response_class=HTMLResponse)
def settings_index(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)

    vps_nodes = get_vps_nodes()
    vps_node = vps_nodes[0] if vps_nodes else None

    config_paths = {
        "data_dir": str(settings.data_dir),
        "db_path": str(settings.db_path),
        "knowledge_dir": str(settings.knowledge_dir),
        "host": settings.host,
        "port": settings.port
    }

    return templates.TemplateResponse(request=request, name="settings.html", context={
        "active_nav": "settings",
        "current_user": user,
        "csrf_token": get_csrf_token(request),
        "vps_node": vps_node,
        "config_paths": config_paths
    })

@router.post("/settings/vps/register")
def register_vps_submit(
    request: Request,
    csrf_token: str = Form(...),
    name: str = Form(...),
    endpoint_url: str = Form(...),
    auth_token: str = Form(...),
    permission_policy: str = Form("approval_required")
):
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return RedirectResponse(url="/settings", status_code=status.HTTP_302_FOUND)

    register_vps_node(
        name=name,
        endpoint_url=endpoint_url,
        auth_token=auth_token,
        permission_policy=permission_policy
    )
    return RedirectResponse(url="/settings", status_code=status.HTTP_302_FOUND)

@router.post("/settings/vps/probe")
async def probe_vps_submit(request: Request, csrf_token: str = Form(...), vps_id: str = Form(...)):
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return RedirectResponse(url="/settings", status_code=status.HTTP_302_FOUND)

    await probe_vps_health(vps_id)
    return RedirectResponse(url="/settings", status_code=status.HTTP_302_FOUND)

@router.post("/settings/vps/test-task")
async def execute_vps_test(request: Request, csrf_token: str = Form(...), vps_id: str = Form(...)):
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return RedirectResponse(url="/settings", status_code=status.HTTP_302_FOUND)

    # Record test task in persistent queue
    task = create_task(
        title="Probe VPS Health & Echo Task",
        description="Harmless connectivity and capability verification test on Layer C worker",
        task_type="vps_action",
        approval_required=False,
        assigned_worker="VPS Companion Worker"
    )

    update_task_progress(task["id"], "running")
    res = await execute_vps_test_task(vps_id)

    if res.get("success"):
        update_task_progress(task["id"], "completed", result_data=res.get("result"))
    else:
        update_task_progress(task["id"], "failed", error_details=res.get("error"))

    return RedirectResponse(url=f"/tasks/{task['id']}", status_code=status.HTTP_302_FOUND)
