"""Project Orchestrator routes: authenticated + CSRF-protected JSON API and the orchestrator UI pages."""
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from app.auth import get_csrf_token, get_current_user, verify_csrf
from app.services import projects as ps
from app.services.orchestration import (
    approval_manager, artifact_manager, engine, model_router, planner, task_graph, telemetry,
)
from app.services.orchestration.common import (
    InvalidTransition, NotFound, OrchestrationError, StaleState, get_run_for_project, jload, list_events,
)

router = APIRouter(tags=["orchestration"])
templates = Jinja2Templates(directory="app/templates")
API = "/api/v1/projects/{project_id}/orchestration"


def _err(status: int, message: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status)


async def _auth(request: Request, mutating: bool = False):
    """Return (user, None) or (None, error response). Mutating calls require the CSRF header."""
    user = get_current_user(request)
    if not user:
        return None, _err(401, "Authentication required.")
    if mutating and not verify_csrf(request, request.headers.get("X-CSRF-Token")):
        return None, _err(403, "Invalid or missing CSRF token.")
    return user, None


async def _body(request: Request) -> Dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


async def _do(fn: Callable[[], Any]) -> JSONResponse:
    try:
        result = await run_in_threadpool(fn)
        return JSONResponse({"ok": True, "result": result}, status_code=200)
    except NotFound as e:
        return _err(404, str(e))
    except (InvalidTransition, StaleState) as e:
        return _err(409, str(e))
    except (OrchestrationError, ps.ValidationError) as e:
        return _err(400, str(e))


def _run(project_id: str) -> Dict[str, Any]:
    run = get_run_for_project(project_id)
    if not run:
        raise NotFound("This project has no orchestration run.")
    return run


# --- creation ----------------------------------------------------------------------------------------------

@router.post("/api/v1/orchestration/projects")
async def create_orchestrated_project(request: Request):
    user, bad = await _auth(request, True)
    if bad:
        return bad
    b = await _body(request)

    def go():
        out = engine.create_project_with_run(str(b.get("name", "")).strip(), user, b, int(b.get("autonomy_level", 1)))
        return {"project_id": out["project"]["id"], "run_id": out["run"]["id"], "state": out["run"]["state"]}
    return await _do(go)


@router.post(API + "/init")
async def init_run_for_existing_project(request: Request, project_id: str):
    """Attach an orchestration run to an existing (legacy) project; the project itself is not modified."""
    user, bad = await _auth(request, True)
    if bad:
        return bad
    b = await _body(request)

    def go():
        proj = ps.get_project(project_id)
        if not proj:
            raise NotFound("Project not found.")
        intake = {"objective": b.get("objective") or proj.get("objective") or proj.get("description") or ""}
        run = engine.create_run(project_id, user, intake, int(b.get("autonomy_level", 1)))
        return {"run_id": run["id"], "state": run["state"]}
    return await _do(go)


# --- plan -------------------------------------------------------------------------------------------------------

@router.post(API + "/plan")
async def generate_plan(request: Request, project_id: str):
    user, bad = await _auth(request, True)
    if bad:
        return bad
    b = await _body(request)
    return await _do(lambda: planner.generate_plan(_run(project_id)["id"], user, mode=str(b.get("mode", "auto")),
                                                    planner_model=b.get("planner_model")))


@router.get(API + "/plan")
async def get_plan(request: Request, project_id: str):
    user, bad = await _auth(request)
    if bad:
        return bad

    def go():
        run = _run(project_id)
        cur = None
        if run["current_revision_id"]:
            rev = planner.get_revision(run["current_revision_id"])
            cur = {k: rev[k] for k in ("id", "revision_number", "status", "source", "plan", "validation", "model_info", "created_at", "approved_by", "approved_at")}
            cur["graph"] = task_graph.summarize_graph(rev["plan"]) if rev["plan"].get("tasks") else {"nodes": [], "edges": []}
        return {"state": run["state"], "current": cur, "revisions": planner.list_revisions(run["id"]),
                "planning_error": run["planning_error"], "analysis": jload(run["analysis_json"])}
    return await _do(go)


@router.put(API + "/plan")
async def edit_plan(request: Request, project_id: str):
    user, bad = await _auth(request, True)
    if bad:
        return bad
    b = await _body(request)
    return await _do(lambda: planner.edit_plan(_run(project_id)["id"], b.get("plan") or {}, user))


@router.post(API + "/approve")
async def approve(request: Request, project_id: str):
    user, bad = await _auth(request, True)
    if bad:
        return bad
    b = await _body(request)

    def go():
        run = approval_manager.approve_plan(_run(project_id)["id"], user, str(b.get("rationale", "")), b.get("revision_id"))
        return {"state": run["state"]}
    return await _do(go)


@router.post(API + "/reject")
async def reject(request: Request, project_id: str):
    user, bad = await _auth(request, True)
    if bad:
        return bad
    b = await _body(request)
    return await _do(lambda: {"state": approval_manager.reject_plan(_run(project_id)["id"], user, str(b.get("reason", "")))["state"]})


@router.post(API + "/clarify")
async def clarify(request: Request, project_id: str):
    user, bad = await _auth(request, True)
    if bad:
        return bad
    b = await _body(request)
    return await _do(lambda: engine.submit_clarification(_run(project_id)["id"], b.get("answers") or {}, user))


# --- execution controls -------------------------------------------------------------------------------------------

def _simple(fn_name: str):
    async def handler(request: Request, project_id: str):
        user, bad = await _auth(request, True)
        if bad:
            return bad
        b = await _body(request)

        def go():
            rid = _run(project_id)["id"]
            if fn_name == "start":
                return {"state": engine.start_run(rid, user)["state"]}
            if fn_name == "pause":
                return {"state": engine.pause_run(rid, user, str(b.get("reason", "")))["state"]}
            if fn_name == "resume":
                return {"state": engine.resume_run(rid, user)["state"]}
            if fn_name == "cancel":
                return {"state": engine.cancel_run(rid, user, str(b.get("reason", "")))["state"]}
            if fn_name == "replan":
                return engine.request_replan(rid, user, str(b.get("reason", "")), str(b.get("mode", "auto")))
            if fn_name == "step":
                return {"state": engine.tick(rid, manual=True)["state"]}
            raise OrchestrationError("Unknown action.")
        return await _do(go)
    return handler


for _name in ("start", "pause", "resume", "cancel", "replan", "step"):
    router.add_api_route(API + "/" + _name, _simple(_name), methods=["POST"], name=f"orchestration_{_name}")


@router.post(API + "/autonomy")
async def set_autonomy(request: Request, project_id: str):
    user, bad = await _auth(request, True)
    if bad:
        return bad
    b = await _body(request)
    return await _do(lambda: {"autonomy_level": engine.set_autonomy(_run(project_id)["id"], b.get("level"), user)["autonomy_level"]})


@router.post(API + "/tasks/{task_id}/retry")
async def retry_task(request: Request, project_id: str, task_id: str):
    user, bad = await _auth(request, True)
    if bad:
        return bad
    return await _do(lambda: {"state": engine.retry_task(_run(project_id)["id"], task_id, user)["state"]})


@router.post(API + "/approvals/{approval_id}")
async def decide_approval(request: Request, project_id: str, approval_id: str):
    user, bad = await _auth(request, True)
    if bad:
        return bad
    b = await _body(request)
    return await _do(lambda: approval_manager.decide(_run(project_id)["id"], approval_id, bool(b.get("approve")), user,
                                                     str(b.get("rationale", ""))))


# --- read models --------------------------------------------------------------------------------------------------------

@router.get(API + "/status")
async def status(request: Request, project_id: str):
    user, bad = await _auth(request)
    if bad:
        return bad
    return await _do(lambda: engine.status(project_id))


@router.get(API + "/events")
async def events(request: Request, project_id: str, after_id: int = 0, limit: int = 200):
    user, bad = await _auth(request)
    if bad:
        return bad
    return await _do(lambda: (_run(project_id), list_events(project_id, after_id, limit))[1])


@router.get(API + "/artifacts")
async def artifacts(request: Request, project_id: str):
    user, bad = await _auth(request)
    if bad:
        return bad
    return await _do(lambda: (_run(project_id), artifact_manager.list_artifacts(project_id))[1])


@router.get(API + "/artifacts/{artifact_id}/download")
async def download_artifact(request: Request, project_id: str, artifact_id: str):
    user, bad = await _auth(request)
    if bad:
        return bad
    try:
        meta = await run_in_threadpool(artifact_manager.resolve_for_download, project_id, artifact_id)
    except NotFound as e:
        return _err(404, str(e))
    return FileResponse(meta["path"], filename=meta["filename"], media_type="application/octet-stream",
                        headers={"X-Content-Type-Options": "nosniff", "X-Artifact-SHA256": meta["sha256"],
                                 "Content-Security-Policy": "default-src 'none'; sandbox"})


@router.get("/api/v1/orchestration/models")
async def models(request: Request):
    user, bad = await _auth(request)
    if bad:
        return bad
    return await _do(lambda: {"models": model_router.model_health(),
                              "nodes": [{k: n[k] for k in ("id", "role", "health", "authenticated", "capabilities", "models", "available_ram_mb", "load")}
                                        for n in model_router.node_inventory() if n["is_worker"]]})


# --- UI pages --------------------------------------------------------------------------------------------------------------

@router.get("/projects/orchestrate/new", response_class=HTMLResponse)
async def new_orchestrated_project_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=302)
    return templates.TemplateResponse(request=request, name="projects/orchestrator_new.html", context={
        "active_nav": "projects", "current_user": user, "csrf_token": get_csrf_token(request),
        "priorities": ps.PRIORITIES})


@router.get("/projects/{project_id}/orchestrator", response_class=HTMLResponse)
async def orchestrator_page(request: Request, project_id: str):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=302)
    project = ps.get_project(project_id)
    if not project:
        return RedirectResponse(url="/projects?err=Project+not+found.", status_code=302)
    return templates.TemplateResponse(request=request, name="projects/orchestrator.html", context={
        "active_nav": "projects", "current_user": user, "csrf_token": get_csrf_token(request),
        "project": project, "has_run": get_run_for_project(project_id) is not None})
