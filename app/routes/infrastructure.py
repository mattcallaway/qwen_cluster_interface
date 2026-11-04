"""Infrastructure Registry, Capabilities & Architecture UI routes (Milestone 1)."""
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import get_csrf_token, get_current_user, verify_csrf
from app.services import infrastructure as infra_svc
from app.services.projects import NotFound, ValidationError

router = APIRouter(tags=["infrastructure"])
templates = Jinja2Templates(directory="app/templates")

TABS = ("overview", "workers", "capabilities", "reconciliation", "architecture", "archived")


def _to(tab: str = "overview", err: str = "", msg: str = "") -> RedirectResponse:
    params = {k: v for k, v in (("tab", tab), ("err", err), ("msg", msg)) if v}
    return RedirectResponse(
        url="/infrastructure" + ("?" + urlencode(params) if params else ""),
        status_code=status.HTTP_302_FOUND
    )


def _guard(request: Request, csrf_token: str) -> Optional[str]:
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return None
    return user


def _run(tab: str, fn, success: str) -> RedirectResponse:
    try:
        fn()
    except (NotFound, ValidationError) as e:
        return _to(tab, err=str(e))
    except Exception as e:
        return _to(tab, err=f"Action failed: {e}")
    return _to(tab, msg=success)


def _alert(err: Optional[str], msg: Optional[str]):
    if err:
        return {"type": "error", "message": err}
    if msg:
        return {"type": "success", "message": msg}
    return None


@router.get("/infrastructure", response_class=HTMLResponse)
def infrastructure_dashboard(
    request: Request,
    tab: str = "overview",
    token_budget: int = 2000,
    q: str = "",
    err: Optional[str] = None,
    msg: Optional[str] = None
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)

    tab = tab if tab in TABS else "overview"

    # Ensure local controller node and default capabilities are active
    infra_svc.ensure_controller_node()
    infra_svc.sync_from_existing_machines_and_vps()

    nodes = infra_svc.list_nodes()
    all_nodes = infra_svc.list_nodes(include_retired=True)
    archived_nodes = [n for n in all_nodes if str(n.get("notes", "")).startswith("[RETIRED")]
    capabilities = infra_svc.list_capabilities()
    reconciliations = infra_svc.list_reconciliations()
    context_packet = infra_svc.build_architecture_context_packet(
        token_budget=token_budget,
        user_query=q
    )

    online_nodes = [n for n in nodes if n["health_status"] == "online"]
    verified_caps = [c for c in capabilities if c["verification_status"] == "verified"]

    return templates.TemplateResponse(
        request=request,
        name="infrastructure/index.html",
        context={
            "active_nav": "infrastructure",
            "current_user": user,
            "csrf_token": get_csrf_token(request),
            "tab": tab,
            "nodes": nodes,
            "archived_nodes": archived_nodes,
            "online_nodes": online_nodes,
            "capabilities": capabilities,
            "verified_caps": verified_caps,
            "reconciliations": reconciliations,
            "context_packet": context_packet,
            "token_budget": token_budget,
            "query": q,
            "categories": infra_svc.CAPABILITY_CATEGORIES,
            "alert": _alert(err, msg),
        }
    )


@router.post("/infrastructure/nodes/{node_id}/discover")
def infrastructure_node_discover(
    request: Request,
    node_id: str,
    csrf_token: str = Form(...)
):
    user = _guard(request, csrf_token)
    if not user:
        return _to("workers", err="Session expired or invalid form token.")

    return _run(
        "workers",
        lambda: infra_svc.discover_node_capabilities(node_id),
        f"Read-only capability discovery completed on node '{node_id}'."
    )


@router.post("/infrastructure/capabilities/{capability_id}/verify")
def infrastructure_capability_verify(
    request: Request,
    capability_id: str,
    csrf_token: str = Form(...)
):
    user = _guard(request, csrf_token)
    if not user:
        return _to("capabilities", err="Session expired or invalid form token.")

    return _run(
        "capabilities",
        lambda: infra_svc.verify_capability(capability_id, verified_by=user),
        f"Capability verification probe completed for '{capability_id}'."
    )


@router.post("/infrastructure/reconciliations/{recon_id}/resolve")
def infrastructure_reconciliation_resolve(
    request: Request,
    recon_id: str,
    csrf_token: str = Form(...),
    resolution_notes: str = Form(...)
):
    user = _guard(request, csrf_token)
    if not user:
        return _to("reconciliation", err="Session expired or invalid form token.")

    return _run(
        "reconciliation",
        lambda: infra_svc.resolve_reconciliation(recon_id, resolution_notes),
        "Architecture discrepancy resolved and archived."
    )


@router.post("/infrastructure/sync-vault")
def infrastructure_sync_vault(
    request: Request,
    csrf_token: str = Form(...)
):
    user = _guard(request, csrf_token)
    if not user:
        return _to("architecture", err="Session expired or invalid form token.")

    return _run(
        "architecture",
        lambda: infra_svc.sync_architecture_knowledge(author=user),
        "Architecture reference synchronized to knowledge vault note: system/infrastructure_architecture.md"
    )
