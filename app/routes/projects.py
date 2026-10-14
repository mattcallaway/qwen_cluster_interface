"""Projects UI routes (Milestone 1). All POSTs require an authenticated session and a valid CSRF token."""
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import get_csrf_token, get_current_user, verify_csrf
from app.services import knowledge_meta as km
from app.services import projects as ps
from app.services import tasks as ts
from app.services.knowledge import list_all_notes

router = APIRouter(tags=["projects"])
templates = Jinja2Templates(directory="app/templates")

TABS = ("overview", "paths", "tasks", "knowledge")


def _to(pid: Optional[str] = None, tab: str = "overview", err: str = "", msg: str = "") -> RedirectResponse:
    base = f"/projects/{pid}" if pid else "/projects"
    params = {k: v for k, v in (("tab", tab if pid else ""), ("err", err), ("msg", msg)) if v}
    return RedirectResponse(url=base + ("?" + urlencode(params) if params else ""), status_code=status.HTTP_302_FOUND)


def _guard(request: Request, csrf_token: str) -> Optional[str]:
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return None
    return user


def _run(pid: Optional[str], tab: str, fn, success: str) -> RedirectResponse:
    """Execute a service call; turn validation/ownership errors into a user-visible message."""
    try:
        fn()
    except ps.NotFound as e:
        return _to(pid, tab, err=str(e))
    except ps.ValidationError as e:
        return _to(pid, tab, err=str(e))
    except Exception as e:  # integrity guards from triggers, etc.
        if e.__class__.__name__ == "IntegrityError":
            return _to(pid, tab, err=f"Rejected by data integrity rules: {e}")
        raise
    return _to(pid, tab, msg=success)


def _alert(err: Optional[str], msg: Optional[str]):
    if err:
        return {"type": "error", "message": err}
    if msg:
        return {"type": "success", "message": msg}
    return None


# --- pages -----------------------------------------------------------------

@router.get("/projects", response_class=HTMLResponse)
def projects_index(request: Request, show_archived: int = 0, err: Optional[str] = None, msg: Optional[str] = None):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)
    projects = ps.list_projects(include_archived=bool(show_archived))
    rows = [{"project": p, "summary": ps.project_summary(p)} for p in projects]
    return templates.TemplateResponse(request=request, name="projects/index.html", context={
        "active_nav": "projects", "current_user": user, "csrf_token": get_csrf_token(request),
        "rows": rows, "all_projects": ps.list_projects(include_archived=False),
        "priorities": ps.PRIORITIES, "show_archived": bool(show_archived), "alert": _alert(err, msg),
    })


@router.get("/projects/{project_id}", response_class=HTMLResponse)
def project_detail(request: Request, project_id: str, tab: str = "overview",
                   err: Optional[str] = None, msg: Optional[str] = None):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)
    project = ps.get_project(project_id)
    if not project:
        return _to(err="Project not found.")
    tab = tab if tab in TABS else "overview"

    paths = ps.list_paths(project_id)
    stages_by_path = {p["id"]: ps.list_stages(p["id"]) for p in paths}
    tasks = ts.get_project_tasks(project_id)
    for t in tasks:
        t["problems"] = ts.validate_project_task_dispatch(t) if t["status"] in ("draft", "awaiting_approval") else []
    knowledge = km.list_project_knowledge(project_id)
    parent = ps.get_project(project["parent_id"]) if project["parent_id"] else None
    path_names = {p["id"]: p["name"] for p in paths}
    stage_names = {s["id"]: s["name"] for ss in stages_by_path.values() for s in ss}

    return templates.TemplateResponse(request=request, name="projects/detail.html", context={
        "active_nav": "projects", "current_user": user, "csrf_token": get_csrf_token(request),
        "project": project, "summary": ps.project_summary(project), "tab": tab,
        "parent": parent, "children": ps.list_children(project_id),
        "candidate_parents": [p for p in ps.list_projects() if p["id"] != project_id],
        "paths": paths, "stages_by_path": stages_by_path, "path_names": path_names, "stage_names": stage_names,
        "path_evidence": {p["id"]: km.list_relations_for_target("path", p["id"]) for p in paths},
        "path_transitions": ps.PATH_TRANSITIONS, "path_selectable": ps.PATH_SELECTABLE,
        "path_editable": ps.PATH_EDITABLE, "path_terminal": ps.PATH_TERMINAL,
        "project_transitions": sorted(ps.PROJECT_TRANSITIONS[project["status"]]),
        "stage_m1_statuses": sorted(ps.STAGE_M1_STATUSES), "priorities": ps.PRIORITIES,
        "tasks": tasks, "knowledge": knowledge, "decisions": ps.list_decisions(project_id, 20),
        "knowledge_types": km.KNOWLEDGE_TYPES, "origins": km.ORIGINS, "trust_transitions": km.TRUST_TRANSITIONS,
        "relation_types": km.RELATION_TYPES, "untrusted_origins": km.UNTRUSTED_ORIGINS,
        "vault_notes": list_all_notes() if tab == "knowledge" else [],
        "alert": _alert(err, msg),
    })


@router.get("/projects/{project_id}/knowledge/search", response_class=HTMLResponse)
def project_knowledge_search(request: Request, project_id: str, q: str = ""):
    user = get_current_user(request)
    if not user:
        return HTMLResponse("", status_code=401)
    if not ps.get_project(project_id):
        return HTMLResponse("", status_code=404)
    results = km.search_project_knowledge(project_id, q) if q.strip() else []
    return templates.TemplateResponse(request=request, name="projects/_knowledge_results.html",
                                      context={"results": results, "query": q.strip()})


# --- project actions -------------------------------------------------------

@router.post("/projects/create")
def project_create(request: Request, csrf_token: str = Form(...), name: str = Form(...),
                   objective: str = Form(""), description: str = Form(""), priority: str = Form("medium"),
                   parent_id: str = Form(""), target_date: str = Form("")):
    user = _guard(request, csrf_token)
    if not user:
        return _to(err="Session expired or invalid form token. Please retry.")
    try:
        p = ps.create_project(name, owner=user, objective=objective, description=description,
                              priority=priority, parent_id=parent_id, target_date=target_date)
    except (ps.ValidationError, ps.NotFound) as e:
        return _to(err=str(e))
    return _to(p["id"], msg="Project created.")


@router.post("/projects/{project_id}/update")
def project_update(request: Request, project_id: str, csrf_token: str = Form(...), name: str = Form(...),
                   description: str = Form(""), objective: str = Form(""), scope: str = Form(""),
                   constraints_text: str = Form(""), success_criteria: str = Form(""),
                   priority: str = Form("medium"), parent_id: str = Form(""),
                   target_date: str = Form(""), review_date: str = Form("")):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, err="Session expired or invalid form token.")
    return _run(project_id, "overview", lambda: ps.update_project(
        project_id, user, name=name, description=description, objective=objective, scope=scope,
        constraints_text=constraints_text, success_criteria=success_criteria, priority=priority,
        parent_id=parent_id, target_date=target_date, review_date=review_date), "Project updated.")


@router.post("/projects/{project_id}/status")
def project_status(request: Request, project_id: str, csrf_token: str = Form(...),
                   new_status: str = Form(...), reason: str = Form("")):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, err="Session expired or invalid form token.")
    return _run(project_id, "overview", lambda: ps.change_project_status(project_id, new_status, user, reason),
                f"Project status changed to {new_status}.")


# --- path actions ----------------------------------------------------------

@router.post("/projects/{project_id}/paths/create")
def path_create(request: Request, project_id: str, csrf_token: str = Form(...), name: str = Form(...),
                rationale: str = Form(""), description: str = Form(""), assumptions: str = Form(""),
                constraints_text: str = Form(""), dependencies: str = Form(""),
                resource_requirements: str = Form(""), risks: str = Form(""), estimated_cost: str = Form(""),
                estimated_effort_hours: str = Form(""), expected_outcome: str = Form(""),
                expected_completion_date: str = Form(""), forecast_confidence: str = Form("")):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "paths", err="Session expired or invalid form token.")
    return _run(project_id, "paths", lambda: ps.create_path(
        project_id, user, name=name, rationale=rationale, description=description, assumptions=assumptions,
        constraints_text=constraints_text, dependencies=dependencies, resource_requirements=resource_requirements,
        risks=risks, estimated_cost=estimated_cost, estimated_effort_hours=estimated_effort_hours,
        expected_outcome=expected_outcome, expected_completion_date=expected_completion_date,
        forecast_confidence=forecast_confidence), "Path proposed.")


@router.post("/projects/{project_id}/paths/{path_id}/status")
def path_status(request: Request, project_id: str, path_id: str, csrf_token: str = Form(...),
                new_status: str = Form(...), reason: str = Form("")):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "paths", err="Session expired or invalid form token.")
    return _run(project_id, "paths", lambda: ps.change_path_status(project_id, path_id, new_status, user, reason),
                f"Path status changed to {new_status}.")


@router.post("/projects/{project_id}/paths/{path_id}/select")
def path_select(request: Request, project_id: str, path_id: str, csrf_token: str = Form(...),
                reason: str = Form("")):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "paths", err="Session expired or invalid form token.")
    return _run(project_id, "paths", lambda: ps.select_path(project_id, path_id, user, reason),
                "Path selected as the project's active strategy.")


@router.post("/projects/{project_id}/paths/{path_id}/stages/create")
def stage_create(request: Request, project_id: str, path_id: str, csrf_token: str = Form(...),
                 name: str = Form(...), objective: str = Form(""), expected_outputs: str = Form(""),
                 completion_criteria: str = Form(""), prerequisites: str = Form(""),
                 estimated_effort_hours: str = Form("")):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "paths", err="Session expired or invalid form token.")
    return _run(project_id, "paths", lambda: ps.create_stage(
        project_id, path_id, user, name=name, objective=objective, expected_outputs=expected_outputs,
        completion_criteria=completion_criteria, prerequisites=prerequisites,
        estimated_effort_hours=estimated_effort_hours), "Stage added.")


@router.post("/projects/{project_id}/stages/{stage_id}/move")
def stage_move(request: Request, project_id: str, stage_id: str, csrf_token: str = Form(...),
               direction: str = Form(...)):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "paths", err="Session expired or invalid form token.")
    return _run(project_id, "paths", lambda: ps.move_stage(project_id, stage_id, direction), "Stage moved.")


@router.post("/projects/{project_id}/stages/{stage_id}/status")
def stage_status(request: Request, project_id: str, stage_id: str, csrf_token: str = Form(...),
                 new_status: str = Form(...)):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "paths", err="Session expired or invalid form token.")
    return _run(project_id, "paths", lambda: ps.change_stage_status(project_id, stage_id, new_status),
                f"Stage marked {new_status}.")


# --- task actions ----------------------------------------------------------

@router.post("/projects/{project_id}/tasks/create")
def project_task_create(request: Request, project_id: str, csrf_token: str = Form(...), title: str = Form(...),
                        stage_id: str = Form(""), purpose: str = Form(""), description: str = Form(""),
                        expected_output: str = Form(""), completion_criteria: str = Form(""),
                        task_type: str = Form("general")):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "tasks", err="Session expired or invalid form token.")
    if not ps.get_project(project_id):
        return _to(err="Project not found.")
    if not title.strip():
        return _to(project_id, "tasks", err="Task title is required.")
    path_id = None
    if stage_id:
        stage = ps.get_stage(stage_id)
        path = ps.get_path(stage["path_id"]) if stage else None
        if not stage or not path or path["project_id"] != project_id:
            return _to(project_id, "tasks", err="Stage not found in this project.")
        path_id = path["id"]

    def _create():
        t = ts.create_task(title=title, description=description, task_type=task_type, project_id=project_id,
                           path_id=path_id, stage_id=stage_id or None, purpose=purpose,
                           expected_output=expected_output, completion_criteria=completion_criteria)
        if t["status"] == "draft":
            raise ps.ValidationError("Task saved as DRAFT (not dispatchable): "
                                     + " ".join(ts.validate_project_task_dispatch(t)))
    return _run(project_id, "tasks", _create, "Task created and awaiting approval.")


@router.post("/projects/{project_id}/tasks/{task_id}/submit")
def project_task_submit(request: Request, project_id: str, task_id: str, csrf_token: str = Form(...)):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "tasks", err="Session expired or invalid form token.")
    task = ts.get_task_by_id(task_id)
    if not task or task.get("project_id") != project_id:
        return _to(project_id, "tasks", err="Task not found in this project.")
    problems = ts.submit_task_for_approval(task_id)
    if problems:
        return _to(project_id, "tasks", err="Cannot submit: " + " ".join(problems))
    return _to(project_id, "tasks", msg="Task submitted for approval.")


@router.post("/projects/{project_id}/tasks/{task_id}/approve")
def project_task_approve(request: Request, project_id: str, task_id: str, csrf_token: str = Form(...)):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "tasks", err="Session expired or invalid form token.")
    task = ts.get_task_by_id(task_id)
    if not task or task.get("project_id") != project_id:
        return _to(project_id, "tasks", err="Task not found in this project.")
    if not ts.approve_task(task_id):
        problems = ts.validate_project_task_dispatch(task)
        return _to(project_id, "tasks", err="Not approved: " + (" ".join(problems) or "task is not awaiting approval."))
    return _to(project_id, "tasks", msg="Task approved and queued.")


# --- knowledge actions -----------------------------------------------------

@router.post("/projects/{project_id}/knowledge/create")
def project_knowledge_create(request: Request, project_id: str, csrf_token: str = Form(...),
                             title: str = Form(...), knowledge_type: str = Form(...), origin: str = Form(...),
                             body: str = Form(""), source_url: str = Form(""), source_retrieved_at: str = Form(""),
                             source_citation: str = Form(""), tags: str = Form("")):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "knowledge", err="Session expired or invalid form token.")
    return _run(project_id, "knowledge", lambda: km.create_project_note(
        project_id, title, knowledge_type, origin, body, user, source_url=source_url,
        source_retrieved_at=source_retrieved_at, source_citation=source_citation, tags=tags),
        "Knowledge captured (status: captured — not yet reviewed).")


@router.post("/projects/{project_id}/knowledge/register")
def project_knowledge_register(request: Request, project_id: str, csrf_token: str = Form(...),
                               note_path: str = Form(...), knowledge_type: str = Form(...),
                               origin: str = Form(...), source_url: str = Form(""),
                               source_citation: str = Form("")):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "knowledge", err="Session expired or invalid form token.")
    return _run(project_id, "knowledge", lambda: km.register_existing_note(
        note_path, knowledge_type, origin, user, project_id=project_id, source_url=source_url,
        source_citation=source_citation), "Existing note linked to this project (file not moved).")


@router.post("/projects/{project_id}/knowledge/{kn_id}/review")
def project_knowledge_review(request: Request, project_id: str, kn_id: str, csrf_token: str = Form(...),
                             new_status: str = Form(...), note: str = Form(""), superseded_by: str = Form("")):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "knowledge", err="Session expired or invalid form token.")
    visible = {e["id"] for e in km.list_project_knowledge(project_id)}
    if kn_id not in visible:
        return _to(project_id, "knowledge", err="Knowledge entry is not part of this project.")
    return _run(project_id, "knowledge", lambda: km.review_knowledge(
        kn_id, new_status, user, note, superseded_by or None), f"Knowledge marked {new_status}.")


@router.post("/projects/{project_id}/knowledge/{kn_id}/relate")
def project_knowledge_relate(request: Request, project_id: str, kn_id: str, csrf_token: str = Form(...),
                             target: str = Form(...), relation_type: str = Form(...), note: str = Form("")):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "knowledge", err="Session expired or invalid form token.")
    target_type, _, target_id = target.partition(":")
    return _run(project_id, "knowledge", lambda: km.add_relation(
        kn_id, target_type, target_id, relation_type, user, note, scope_project_id=project_id),
        "Relation recorded.")
