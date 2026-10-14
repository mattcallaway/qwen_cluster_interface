from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.auth import get_current_user, get_csrf_token
from app.config import settings
from app.database import get_db_connection
from app.services.machine_registry import get_all_machines
from app.services.inference import get_active_backend
from app.services.vps import get_vps_nodes
from app.services.tasks import get_task_counts, get_all_tasks

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")

@router.get("/", response_class=HTMLResponse)
def dashboard_view(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)

    machines = get_all_machines()
    total_machines = len(machines)
    online_machines = sum(1 for m in machines if m.get("connection_status") == "online")
    offline_machines = total_machines - online_machines

    active_backend = get_active_backend()
    vps_nodes = get_vps_nodes()
    vps_node = vps_nodes[0] if vps_nodes else None

    task_counts = get_task_counts()
    pending_tasks = get_all_tasks(limit=10, status_filter="awaiting_approval")
    if not pending_tasks:
        pending_tasks = [t for t in get_all_tasks(limit=10) if t.get("status") in ("queued", "running")]

    # Get recent audit events
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM machine_audits ORDER BY timestamp DESC LIMIT 6")
    recent_events = [dict(r) for r in cursor.fetchall()]
    conn.close()

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "active_nav": "dashboard",
            "current_user": user,
            "csrf_token": get_csrf_token(request),
            "server_host": settings.host,
            "server_port": settings.port,
            "total_machines": total_machines,
            "online_machines": online_machines,
            "offline_machines": offline_machines,
            "active_backend": active_backend,
            "vps_node": vps_node,
            "task_counts": task_counts,
            "pending_tasks": pending_tasks,
            "recent_events": recent_events,
        }
    )
