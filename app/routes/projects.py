"""Projects UI routes (Milestone 1). All POSTs require an authenticated session and a valid CSRF token."""
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import get_csrf_token, get_current_user, verify_csrf
from app.services import context_packet as cp_svc
from app.services import deliverables as ds
from app.services import knowledge_meta as km
from app.services import learning as learn_svc
from app.services import metrics as met_svc
from app.services import projects as ps
from app.services import proposals as prop_svc
from app.services import tasks as ts
from app.services.knowledge import list_all_notes

router = APIRouter(tags=["projects"])
templates = Jinja2Templates(directory="app/templates")

TABS = ("overview", "paths", "tasks", "deliverables", "knowledge", "metrics", "proposals", "learning")


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
    deliverables = ds.list_deliverables(project_id)
    deliverable_names = {d["id"]: d["name"] for d in deliverables}
    stage_gates = {s["id"]: ps.evaluate_stage_gate(project_id, s["id"]) for ss in stages_by_path.values() for s in ss}
    path_revisions = {p["id"]: ps.list_path_revisions(p["id"]) for p in paths}
    deliverable_revisions = {d["id"]: ds.list_deliverable_revisions(d["id"]) for d in deliverables}

    tasks = ts.get_project_tasks(project_id)
    for t in tasks:
        t["problems"] = ts.validate_project_task_dispatch(t) if t["status"] in ("draft", "awaiting_approval") else []
        t["dependencies"] = ts.get_task_dependencies(t["id"])
        t["dependents"] = ts.get_task_dependents(t["id"])
        t["execution_events"] = ts.list_task_execution_events(t["id"])
    knowledge = km.list_project_knowledge(project_id)
    from app.services.knowledge_store import list_knowledge_records, list_affected_impacts, list_promotions
    from app.services.orchestration.research_checkpoint import list_project_checkpoints
    from app.services.artifacts import artifact_service

    structured_knowledge = list_knowledge_records(project_id=project_id, include_collective=True)
    checkpoints = list_project_checkpoints(project_id)
    affected_impacts = list_affected_impacts(project_id)
    project_promotions = [p for p in list_promotions() if p.get("source_project_id") == project_id]
    project_artifacts = artifact_service.list_artifacts(project_id=project_id)

    parent = ps.get_project(project["parent_id"]) if project["parent_id"] else None
    path_names = {p["id"]: p["name"] for p in paths}
    stage_names = {s["id"]: s["name"] for ss in stages_by_path.values() for s in ss}

    raw_metrics = met_svc.list_project_metrics(project_id, include_archived=True)
    metrics = [met_svc.get_metric_summary(m["id"]) for m in raw_metrics]
    observations_by_metric = {m["id"]: met_svc.list_metric_observations(m["id"], include_superseded=True) for m in raw_metrics}
    forecasts_by_metric = {m["id"]: met_svc.list_path_forecasts(m["id"]) for m in raw_metrics}
    evaluations_by_metric = {m["id"]: met_svc.list_forecast_evaluations(metric_id=m["id"]) for m in raw_metrics}

    proposals = prop_svc.list_project_proposals(project_id)

    model_calibration = learn_svc.calculate_model_calibration(project_id)
    retrospectives = learn_svc.list_retrospectives(project_id)
    deliverable_evaluations = learn_svc.list_deliverable_evaluations(project_id)
    cross_project_lessons = learn_svc.search_cross_project_lessons(current_project_id=project_id, limit=6) if tab == "learning" else []

    return templates.TemplateResponse(request=request, name="projects/detail.html", context={
        "active_nav": "projects", "current_user": user, "csrf_token": get_csrf_token(request),
        "project": project, "summary": ps.project_summary(project), "tab": tab,
        "parent": parent, "children": ps.list_children(project_id),
        "candidate_parents": [p for p in ps.list_projects() if p["id"] != project_id],
        "paths": paths, "stages_by_path": stages_by_path, "path_names": path_names, "stage_names": stage_names,
        "deliverables": deliverables, "deliverable_names": deliverable_names,
        "project_artifacts": project_artifacts,
        "deliverable_types": ds.DELIVERABLE_TYPES, "deliverable_statuses": ds.DELIVERABLE_STATUSES,
        "deliverable_revisions": deliverable_revisions, "stage_gates": stage_gates,
        "path_revisions": path_revisions,
        "path_evidence": {p["id"]: km.list_relations_for_target("path", p["id"]) for p in paths},
        "path_transitions": ps.PATH_TRANSITIONS, "path_selectable": ps.PATH_SELECTABLE,
        "path_editable": ps.PATH_EDITABLE, "path_terminal": ps.PATH_TERMINAL,
        "project_transitions": sorted(ps.PROJECT_TRANSITIONS[project["status"]]),
        "stage_m1_statuses": sorted(ps.STAGE_M1_STATUSES), "priorities": ps.PRIORITIES,
        "tasks": tasks, "knowledge": knowledge, "decisions": ps.list_decisions(project_id, 20),
        "structured_knowledge": structured_knowledge, "checkpoints": checkpoints,
        "affected_impacts": affected_impacts, "project_promotions": project_promotions,
        "knowledge_types": km.KNOWLEDGE_TYPES, "origins": km.ORIGINS, "trust_transitions": km.TRUST_TRANSITIONS,
        "relation_types": km.RELATION_TYPES, "untrusted_origins": km.UNTRUSTED_ORIGINS,
        "collective_knowledge": km.list_collective_knowledge() if tab == "knowledge" else [],
        "vault_notes": list_all_notes() if tab == "knowledge" else [],
        "metrics": metrics,
        "observations_by_metric": observations_by_metric,
        "forecasts_by_metric": forecasts_by_metric,
        "evaluations_by_metric": evaluations_by_metric,
        "metric_types": met_svc.METRIC_TYPES,
        "measurement_methods": met_svc.MEASUREMENT_METHODS,
        "collection_frequencies": met_svc.COLLECTION_FREQUENCIES,
        "directions_of_improvement": met_svc.DIRECTIONS_OF_IMPROVEMENT,
        "forecast_methods": met_svc.FORECAST_METHODS,
        "confidence_levels": met_svc.CONFIDENCE_LEVELS,
        "proposals": proposals,
        "proposal_types": prop_svc.PROPOSAL_TYPES,
        "proposal_statuses": prop_svc.PROPOSAL_STATUSES,
        "model_calibration": model_calibration,
        "retrospectives": retrospectives,
        "deliverable_evaluations": deliverable_evaluations,
        "cross_project_lessons": cross_project_lessons,
        "deliverable_evaluation_types": learn_svc.EVALUATION_TYPES,
        "deliverable_outcome_statuses": learn_svc.OUTCOME_STATUSES,
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


@router.post("/projects/{project_id}/paths/{path_id}/revise")
def path_revise(request: Request, project_id: str, path_id: str, csrf_token: str = Form(...),
                revision_reason: str = Form(...), name: str = Form(""),
                rationale: str = Form(""), assumptions: str = Form(""), risks: str = Form(""),
                dependencies: str = Form(""), resource_requirements: str = Form(""),
                estimated_cost: str = Form(""), estimated_effort_hours: str = Form(""),
                expected_outcome: str = Form(""), forecast_confidence: str = Form("")):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "paths", err="Session expired or invalid form token.")
    return _run(project_id, "paths", lambda: ps.revise_path(
        project_id, path_id, user, revision_reason, name=name or None, rationale=rationale or None,
        assumptions=assumptions or None, risks=risks or None, dependencies=dependencies or None,
        resource_requirements=resource_requirements or None, estimated_cost=estimated_cost or None,
        estimated_effort_hours=estimated_effort_hours or None, expected_outcome=expected_outcome or None,
        forecast_confidence=forecast_confidence or None), "Path revised and previous snapshot archived.")


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


@router.post("/projects/{project_id}/stages/{stage_id}/advance")
def stage_advance(request: Request, project_id: str, stage_id: str, csrf_token: str = Form(...),
                  rationale: str = Form("")):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "paths", err="Session expired or invalid form token.")
    return _run(project_id, "paths", lambda: ps.advance_stage(project_id, stage_id, user, rationale),
                "Stage completion gate verified and stage advanced.")


# --- task actions ----------------------------------------------------------

@router.post("/projects/{project_id}/tasks/create")
def project_task_create(request: Request, project_id: str, csrf_token: str = Form(...), title: str = Form(...),
                        stage_id: str = Form(""), deliverable_id: str = Form(""), purpose: str = Form(""),
                        description: str = Form(""), expected_output: str = Form(""),
                        completion_criteria: str = Form(""), task_type: str = Form("general")):
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
                           path_id=path_id, stage_id=stage_id or None, deliverable_id=deliverable_id or None,
                           purpose=purpose, expected_output=expected_output,
                           completion_criteria=completion_criteria)
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


@router.post("/projects/{project_id}/tasks/{task_id}/dependencies/add")
def task_dependency_add(request: Request, project_id: str, task_id: str, csrf_token: str = Form(...),
                        depends_on_task_id: str = Form(...)):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "tasks", err="Session expired or invalid form token.")
    return _run(project_id, "tasks", lambda: ts.add_task_dependency(task_id, depends_on_task_id),
                "Prerequisite task dependency added.")


@router.post("/projects/{project_id}/tasks/{task_id}/dependencies/remove")
def task_dependency_remove(request: Request, project_id: str, task_id: str, csrf_token: str = Form(...),
                           depends_on_task_id: str = Form(...)):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "tasks", err="Session expired or invalid form token.")
    return _run(project_id, "tasks", lambda: ts.remove_task_dependency(task_id, depends_on_task_id),
                "Prerequisite task dependency removed.")


@router.post("/projects/{project_id}/tasks/{task_id}/execute")
def task_record_execution(request: Request, project_id: str, task_id: str, csrf_token: str = Form(...),
                          outcome: str = Form(...), attempted: str = Form(...),
                          actual_result: str = Form(...), evidence_summary: str = Form(""),
                          assumptions_feedback: str = Form(""), reusable_lessons: str = Form(""),
                          error_details: str = Form(""), create_knowledge_note: str = Form("")):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "tasks", err="Session expired or invalid form token.")
    make_note = bool(create_knowledge_note and create_knowledge_note not in ("0", "false", "off"))
    return _run(project_id, "tasks", lambda: ts.record_task_execution(
        task_id=task_id, outcome=outcome, user=user, attempted=attempted, actual_result=actual_result,
        evidence_summary=evidence_summary, assumptions_feedback=assumptions_feedback,
        reusable_lessons=reusable_lessons, error_details=error_details,
        create_knowledge_note=make_note), f"Task execution recorded with outcome: {outcome}.")


# --- deliverable actions ---------------------------------------------------

@router.post("/projects/{project_id}/deliverables/create")
def deliverable_create(request: Request, project_id: str, csrf_token: str = Form(...),
                       name: str = Form(...), deliverable_type: str = Form("document"),
                       stage_id: str = Form(""), description: str = Form(""),
                       acceptance_criteria: str = Form(""), due_date: str = Form("")):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "deliverables", err="Session expired or invalid form token.")
    return _run(project_id, "deliverables", lambda: ds.create_deliverable(
        project_id, name, deliverable_type, user, stage_id=stage_id or None,
        description=description, acceptance_criteria=acceptance_criteria, due_date=due_date or None),
        "Deliverable planned.")


@router.post("/projects/{project_id}/deliverables/{deliverable_id}/submit")
def deliverable_submit(request: Request, project_id: str, deliverable_id: str, csrf_token: str = Form(...),
                       artifact_references: str = Form(...), verification_results: str = Form("")):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "deliverables", err="Session expired or invalid form token.")
    return _run(project_id, "deliverables", lambda: ds.submit_deliverable(
        deliverable_id, user, artifact_references, verification_results),
        "Deliverable submitted for formal review.")


@router.post("/projects/{project_id}/deliverables/{deliverable_id}/review")
def deliverable_review(request: Request, project_id: str, deliverable_id: str, csrf_token: str = Form(...),
                       decision: str = Form(...), rationale: str = Form(...),
                       verification_results: str = Form("")):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "deliverables", err="Session expired or invalid form token.")
    target_status = "accepted" if decision == "accept" else ("rejected" if decision == "reject" else "changes_requested")
    return _run(project_id, "deliverables", lambda: ds.review_deliverable(
        deliverable_id, target_status, user, rationale, verification_results or None),
        f"Deliverable marked as {target_status}.")


@router.post("/projects/{project_id}/deliverables/{deliverable_id}/update")
def deliverable_update(request: Request, project_id: str, deliverable_id: str, csrf_token: str = Form(...),
                       name: str = Form(""), description: str = Form(""),
                       acceptance_criteria: str = Form(""), due_date: str = Form(""),
                       stage_id: str = Form("")):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "deliverables", err="Session expired or invalid form token.")
    return _run(project_id, "deliverables", lambda: ds.update_deliverable(
        deliverable_id, user, name=name or None, description=description or None,
        acceptance_criteria=acceptance_criteria or None, due_date=due_date or None,
        stage_id=stage_id or None), "Deliverable updated.")


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


@router.post("/projects/{project_id}/knowledge/{kn_id}/promote")
def project_knowledge_promote(request: Request, project_id: str, kn_id: str, csrf_token: str = Form(...),
                              rationale: str = Form("")):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "knowledge", err="Session expired or invalid form token.")
    return _run(project_id, "knowledge", lambda: km.promote_to_collective(kn_id, user, rationale),
                "Knowledge verified and promoted to Collective Memory.")


# --- metrics actions (Milestone 3) -----------------------------------------

@router.post("/projects/{project_id}/metrics/create")
def project_metric_create(
    request: Request,
    project_id: str,
    csrf_token: str = Form(...),
    name: str = Form(...),
    unit: str = Form(...),
    metric_type: str = Form("count"),
    path_id: str = Form(""),
    description: str = Form(""),
    measurement_method: str = Form("manual"),
    source: str = Form(""),
    collection_frequency: str = Form("ad_hoc"),
    target_value: str = Form(""),
    target_date: str = Form(""),
    direction_of_improvement: str = Form("higher_is_better"),
):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "metrics", err="Session expired or invalid form token.")

    pid = path_id.strip() or None
    t_val = float(target_value.strip()) if target_value.strip() else None
    t_date = target_date.strip() or None

    return _run(
        project_id,
        "metrics",
        lambda: met_svc.define_metric(
            project_id=project_id,
            name=name,
            unit=unit,
            metric_type=metric_type,
            path_id=pid,
            description=description,
            measurement_method=measurement_method,
            source=source,
            collection_frequency=collection_frequency,
            target_value=t_val,
            target_date=t_date,
            direction_of_improvement=direction_of_improvement,
            owner=user,
        ),
        f"Metric '{name}' defined successfully."
    )


@router.post("/projects/{project_id}/metrics/{metric_id}/update")
def project_metric_update(
    request: Request,
    project_id: str,
    metric_id: str,
    csrf_token: str = Form(...),
    description: str = Form(""),
    target_value: str = Form(""),
    target_date: str = Form(""),
    direction_of_improvement: str = Form(""),
    collection_frequency: str = Form(""),
):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "metrics", err="Session expired or invalid form token.")

    t_val = float(target_value.strip()) if target_value.strip() else None
    t_date = target_date.strip() or None
    dir_imp = direction_of_improvement.strip() or None
    freq = collection_frequency.strip() or None

    return _run(
        project_id,
        "metrics",
        lambda: met_svc.update_metric(
            metric_id=metric_id,
            description=description,
            target_value=t_val,
            target_date=t_date,
            direction_of_improvement=dir_imp,
            collection_frequency=freq,
        ),
        "Metric updated."
    )


@router.post("/projects/{project_id}/metrics/{metric_id}/archive")
def project_metric_archive(request: Request, project_id: str, metric_id: str, csrf_token: str = Form(...)):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "metrics", err="Session expired or invalid form token.")
    return _run(project_id, "metrics", lambda: met_svc.archive_metric(metric_id), "Metric archived.")


@router.post("/projects/{project_id}/metrics/{metric_id}/observations/create")
def project_observation_create(
    request: Request,
    project_id: str,
    metric_id: str,
    csrf_token: str = Form(...),
    value: str = Form(...),
    measured_at: str = Form(""),
    source: str = Form(""),
    collection_method: str = Form(""),
    verification_status: str = Form("unverified"),
    evidence_reference: str = Form(""),
    notes: str = Form(""),
):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "metrics", err="Session expired or invalid form token.")

    val = float(value.strip())
    meas_time = measured_at.strip() or None

    return _run(
        project_id,
        "metrics",
        lambda: met_svc.record_observation(
            metric_id=metric_id,
            value=val,
            measured_at=meas_time,
            source=source,
            collection_method=collection_method,
            verification_status=verification_status,
            evidence_reference=evidence_reference,
            notes=notes,
            created_by=user,
        ),
        "Actual observation recorded."
    )


@router.post("/projects/{project_id}/metrics/{metric_id}/observations/correct")
def project_observation_correct(
    request: Request,
    project_id: str,
    metric_id: str,
    csrf_token: str = Form(...),
    supersedes_id: str = Form(...),
    correction_reason: str = Form(...),
    value: str = Form(...),
    measured_at: str = Form(""),
    source: str = Form(""),
    collection_method: str = Form(""),
    verification_status: str = Form("verified"),
    evidence_reference: str = Form(""),
    notes: str = Form(""),
):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "metrics", err="Session expired or invalid form token.")

    val = float(value.strip())
    meas_time = measured_at.strip() or None

    return _run(
        project_id,
        "metrics",
        lambda: met_svc.record_observation_correction(
            metric_id=metric_id,
            supersedes_id=supersedes_id,
            correction_reason=correction_reason,
            value=val,
            measured_at=meas_time,
            source=source,
            collection_method=collection_method,
            verification_status=verification_status,
            evidence_reference=evidence_reference,
            notes=notes,
            created_by=user,
        ),
        "Auditable observation correction recorded."
    )


@router.post("/projects/{project_id}/metrics/{metric_id}/forecasts/create")
def project_forecast_create(
    request: Request,
    project_id: str,
    metric_id: str,
    csrf_token: str = Form(...),
    predicted_value: str = Form(...),
    method: str = Form("manual_estimate"),
    path_id: str = Form(""),
    assumptions: str = Form(""),
    evidence: str = Form(""),
    confidence_score: str = Form(""),
    confidence_level: str = Form("medium"),
    revision_reason: str = Form(""),
    prediction_period: str = Form(""),
    target_date: str = Form(""),
):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "metrics", err="Session expired or invalid form token.")

    val = float(predicted_value.strip())
    pid = path_id.strip() or None
    cs = float(confidence_score.strip()) if confidence_score.strip() else None
    t_date = target_date.strip() or None

    return _run(
        project_id,
        "metrics",
        lambda: met_svc.record_forecast(
            metric_id=metric_id,
            predicted_value=val,
            method=method,
            assumptions=assumptions,
            evidence=evidence,
            confidence_score=cs,
            confidence_level=confidence_level,
            revision_reason=revision_reason,
            created_by=user,
            path_id=pid,
            prediction_period=prediction_period,
            target_date=t_date,
        ),
        "Versioned forecast recorded."
    )


@router.post("/projects/{project_id}/metrics/{metric_id}/forecasts/generate")
def project_forecast_generate(
    request: Request,
    project_id: str,
    metric_id: str,
    csrf_token: str = Form(...),
    method: str = Form(...),
    path_id: str = Form(""),
    target_date: str = Form(""),
    window_size: int = Form(3),
    revision_reason: str = Form(""),
):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "metrics", err="Session expired or invalid form token.")

    pid = path_id.strip() or None
    t_date = target_date.strip() or None

    def _calc():
        if method == "linear_trend":
            calc = met_svc.calculate_linear_trend_forecast(metric_id, target_date_iso=t_date)
        elif method == "rolling_average":
            calc = met_svc.calculate_rolling_average_forecast(metric_id, window_size=window_size)
        elif method == "rate_extrapolation":
            calc = met_svc.calculate_rate_extrapolation_forecast(metric_id, target_date_iso=t_date)
        else:
            raise ps.ValidationError(f"Unsupported automated method '{method}'.")

        if calc["status"] == "insufficient_data":
            raise ps.ValidationError(f"Insufficient data for {method}: {calc.get('reason')}")

        return met_svc.record_forecast(
            metric_id=metric_id,
            predicted_value=calc["predicted_value"],
            method=method,
            assumptions=calc.get("assumptions", ""),
            confidence_level=calc.get("confidence_level", "medium"),
            revision_reason=revision_reason,
            created_by=user,
            path_id=pid,
            target_date=t_date,
        )

    return _run(project_id, "metrics", _calc, "Automated forecast calculated and recorded.")


@router.post("/projects/{project_id}/metrics/{metric_id}/forecasts/{forecast_id}/evaluate")
def project_forecast_evaluate(
    request: Request,
    project_id: str,
    metric_id: str,
    forecast_id: str,
    csrf_token: str = Form(...),
    actual_observation_id: str = Form(""),
    evaluation_notes: str = Form(""),
):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "metrics", err="Session expired or invalid form token.")

    obs_id = actual_observation_id.strip() or None

    return _run(
        project_id,
        "metrics",
        lambda: met_svc.evaluate_forecast(
            forecast_id=forecast_id,
            actual_observation_id=obs_id,
            evaluated_by=user,
            notes=evaluation_notes,
        ),
        "Forecast evaluated against actual performance."
    )


# --- Milestone 4: Context Packets and AI Proposals -------------------------

@router.get("/projects/{project_id}/context-packet", response_class=HTMLResponse)
def project_context_packet_inspect(
    request: Request,
    project_id: str,
    user_instruction: str = "",
    token_budget: int = 4000,
    path_id: Optional[str] = None,
    stage_id: Optional[str] = None,
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)
    project = ps.get_project(project_id)
    if not project:
        return _to(err="Project not found.")

    packet = cp_svc.build_project_context_packet(
        project_id=project_id,
        user_instruction=user_instruction,
        path_id=path_id,
        stage_id=stage_id,
        token_budget=int(token_budget),
    )

    paths = ps.list_paths(project_id)
    stages = ps.list_stages(path_id) if path_id else []

    return templates.TemplateResponse(request=request, name="projects/context_packet.html", context={
        "active_nav": "projects",
        "current_user": user,
        "csrf_token": get_csrf_token(request),
        "project": project,
        "packet": packet,
        "user_instruction": user_instruction,
        "token_budget": int(token_budget),
        "path_id": path_id or "",
        "stage_id": stage_id or "",
        "paths": paths,
        "stages": stages,
    })


@router.post("/projects/{project_id}/proposals/generate-path")
def project_proposal_generate_path(
    request: Request,
    project_id: str,
    csrf_token: str = Form(...),
    user_guidance: str = Form(""),
    token_budget: int = Form(4000),
):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "proposals", err="Session expired or invalid form token.")

    return _run(
        project_id,
        "proposals",
        lambda: prop_svc.generate_path_proposals(
            project_id=project_id,
            user_guidance=user_guidance,
            token_budget=token_budget,
            created_by=user,
        ),
        "AI Path proposal generated for review. Inspect and authorize below."
    )


@router.post("/projects/{project_id}/proposals/generate-tasks")
def project_proposal_generate_tasks(
    request: Request,
    project_id: str,
    csrf_token: str = Form(...),
    stage_id: str = Form(...),
    user_guidance: str = Form(""),
    token_budget: int = Form(4000),
):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "proposals", err="Session expired or invalid form token.")

    return _run(
        project_id,
        "proposals",
        lambda: prop_svc.generate_task_decomposition(
            project_id=project_id,
            stage_id=stage_id,
            user_guidance=user_guidance,
            token_budget=token_budget,
            created_by=user,
        ),
        "AI Task decomposition generated for review. Inspect and authorize below."
    )


@router.post("/projects/{project_id}/proposals/generate-review")
def project_proposal_generate_review(
    request: Request,
    project_id: str,
    csrf_token: str = Form(...),
    path_id: str = Form(""),
    stage_id: str = Form(""),
    token_budget: int = Form(4000),
):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "proposals", err="Session expired or invalid form token.")

    return _run(
        project_id,
        "proposals",
        lambda: prop_svc.generate_path_review(
            project_id=project_id,
            path_id=path_id.strip() or None,
            stage_id=stage_id.strip() or None,
            token_budget=token_budget,
            created_by=user,
        ),
        "AI Path review proposal generated. Inspect evaluation and recommendations below."
    )


@router.post("/projects/{project_id}/proposals/{proposal_id}/review")
def project_proposal_review(
    request: Request,
    project_id: str,
    proposal_id: str,
    csrf_token: str = Form(...),
    action: str = Form(...),
    review_notes: str = Form(""),
):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "proposals", err="Session expired or invalid form token.")

    return _run(
        project_id,
        "proposals",
        lambda: prop_svc.review_proposal(
            proposal_id=proposal_id,
            action=action,
            reviewed_by=user,
            review_notes=review_notes,
        ),
        f"Proposal successfully {action}ed."
    )


# --- Milestone 5: Learning, Retrospectives & Deliverable Evaluations -------

@router.post("/projects/{project_id}/retrospectives/generate")
def project_retrospective_generate(
    request: Request,
    project_id: str,
    csrf_token: str = Form(...),
    user_guidance: str = Form(""),
):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "learning", err="Session expired or invalid form token.")

    return _run(
        project_id,
        "learning",
        lambda: learn_svc.generate_project_retrospective(
            project_id=project_id,
            user_guidance=user_guidance,
            created_by=user,
        ),
        "Project retrospective synthesized in draft state. Review, edit, or publish below."
    )


@router.post("/projects/{project_id}/retrospectives/{retrospective_id}/publish")
def project_retrospective_publish(
    request: Request,
    project_id: str,
    retrospective_id: str,
    csrf_token: str = Form(...),
    promote_to_collective: bool = Form(False),
):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "learning", err="Session expired or invalid form token.")

    return _run(
        project_id,
        "learning",
        lambda: learn_svc.publish_project_retrospective(
            retrospective_id=retrospective_id,
            reviewed_by=user,
            promote_to_collective=bool(promote_to_collective),
        ),
        "Retrospective published to knowledge vault and registered in project memory."
    )


@router.post("/projects/{project_id}/deliverables/evaluate")
@router.post("/projects/{project_id}/deliverables/{deliverable_id}/evaluate")
def project_deliverable_evaluate(
    request: Request,
    project_id: str,
    csrf_token: str = Form(...),
    evaluation_type: str = Form(...),
    outcome_status: str = Form(...),
    impact_summary: str = Form(...),
    deliverable_id: Optional[str] = None,
    form_deliverable_id: str = Form("", alias="deliverable_id"),
    metric_observation_id: str = Form(""),
    evidence_reference: str = Form(""),
    lessons_learned: str = Form(""),
):
    user = _guard(request, csrf_token)
    if not user:
        return _to(project_id, "learning", err="Session expired or invalid form token.")

    target_deliv_id = deliverable_id or form_deliverable_id
    if not target_deliv_id:
        return _to(project_id, "learning", err="Please select a deliverable to evaluate.")

    return _run(
        project_id,
        "learning",
        lambda: learn_svc.record_deliverable_evaluation(
            deliverable_id=target_deliv_id,
            evaluation_type=evaluation_type,
            outcome_status=outcome_status,
            impact_summary=impact_summary,
            evaluated_by=user,
            metric_observation_id=metric_observation_id.strip() or None,
            evidence_reference=evidence_reference.strip(),
            lessons_learned=lessons_learned.strip(),
            project_id=project_id,
        ),
        "Deliverable outcome evaluation successfully recorded."
    )

