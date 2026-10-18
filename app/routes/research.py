"""Autonomous Research routes: authenticated + CSRF-protected JSON API and Research UI."""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from app.auth import get_csrf_token, get_current_user, verify_csrf
from app.database import get_db_connection
from app.services import projects as ps
from app.services.orchestration.common import NotFound
from app.services.research.manager import ResearchManager

router = APIRouter(tags=["research"])
templates = Jinja2Templates(directory="app/templates")


def _err(status: int, message: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status)


async def _auth(request: Request, mutating: bool = False):
    """Verify session user and CSRF token for mutating calls."""
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
    except Exception as e:
        return _err(400, str(e))


# --- UI Pages -----------------------------------------------------------------------------------------

@router.get("/projects/{project_id}/research", response_class=HTMLResponse)
async def project_research_page(project_id: str, request: Request):
    user = get_current_user(request)
    if not user:
        return _err(401, "Authentication required.")
    project = ps.get_project(project_id)
    if not project:
        raise NotFound("Project not found.")

    mgr = ResearchManager()
    stats = mgr.telemetry.get_project_stats(project_id)
    sources = mgr.source_library.list_sources(project_id)
    claims = mgr.evidence_ledger.list_claims(project_id)
    budget = mgr.create_or_get_budget(project_id)

    return templates.TemplateResponse(
        request=request,
        name="research/index.html",
        context={
            "user": user,
            "csrf_token": get_csrf_token(request),
            "project": project,
            "stats": stats,
            "sources": sources,
            "claims": claims,
            "budget": budget.to_dict(),
        },
    )


# --- JSON API Endpoints -------------------------------------------------------------------------------

@router.post("/api/v1/projects/{project_id}/research/plan")
async def api_plan_research(project_id: str, request: Request):
    user, err = await _auth(request, mutating=True)
    if err:
        return err
    project = ps.get_project(project_id)
    if not project:
        return _err(404, "Project not found.")

    b = await _body(request)
    objective = b.get("objective") or project.get("description") or project.get("name")
    mgr = ResearchManager()
    strategy = mgr.planner.generate_strategy(objective)
    return JSONResponse({"ok": True, "strategy": strategy.to_dict()})


@router.post("/api/v1/projects/{project_id}/research/start")
async def api_start_research(project_id: str, request: Request):
    user, err = await _auth(request, mutating=True)
    if err:
        return err
    project = ps.get_project(project_id)
    if not project:
        return _err(404, "Project not found.")

    b = await _body(request)
    objective = b.get("objective") or project.get("description") or project.get("name")

    def _execute():
        mgr = ResearchManager()
        return mgr.run_full_cycle(project_id, objective)

    return await _do(_execute)


@router.get("/api/v1/projects/{project_id}/research/status")
async def api_research_status(project_id: str, request: Request):
    user, err = await _auth(request)
    if err:
        return err
    mgr = ResearchManager()
    stats = mgr.telemetry.get_project_stats(project_id)
    return JSONResponse({"ok": True, "stats": stats})


@router.get("/api/v1/projects/{project_id}/research/queries")
async def api_research_queries(project_id: str, request: Request):
    user, err = await _auth(request)
    if err:
        return err
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, query_text, provider, results_count, duration_ms, status, created_at FROM research_queries WHERE project_id = ? ORDER BY created_at DESC", (project_id,))
        rows = [dict(r) for r in cursor.fetchall()]
        return JSONResponse({"ok": True, "queries": rows})
    finally:
        conn.close()


@router.get("/api/v1/projects/{project_id}/research/sources")
async def api_research_sources(project_id: str, request: Request):
    user, err = await _auth(request)
    if err:
        return err
    mgr = ResearchManager()
    sources = mgr.source_library.list_sources(project_id)
    return JSONResponse({"ok": True, "sources": sources})


@router.get("/api/v1/projects/{project_id}/research/claims")
async def api_research_claims(project_id: str, request: Request):
    user, err = await _auth(request)
    if err:
        return err
    mgr = ResearchManager()
    claims = mgr.evidence_ledger.list_claims(project_id)
    return JSONResponse({"ok": True, "claims": claims})


@router.get("/api/v1/projects/{project_id}/research/citations")
async def api_research_citations(project_id: str, request: Request):
    user, err = await _auth(request)
    if err:
        return err
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT c.id, c.citation_num as citation_marker, c.citation_num, c.formatted_citation,
                   c.is_verified, c.validation_notes, c.created_at,
                   s.title as source_title, s.original_url, s.publisher, s.quality_score
            FROM research_citations c
            LEFT JOIN research_sources s ON s.id = c.source_id
            WHERE c.project_id = ? ORDER BY c.citation_num
            """,
            (project_id,)
        )
        rows = [dict(r) for r in cursor.fetchall()]
        return JSONResponse({"ok": True, "citations": rows})
    finally:
        conn.close()


@router.api_route("/api/v1/projects/{project_id}/research/export", methods=["GET", "POST"])
async def api_research_export(project_id: str, request: Request):
    user = get_current_user(request)
    if not user:
        return _err(401, "Authentication required.")

    fmt = request.query_params.get("format", "markdown")
    if request.method == "POST":
        b = await _body(request)
        fmt = b.get("format") or fmt

    mgr = ResearchManager()
    sources = mgr.source_library.list_sources(project_id)
    claims = mgr.evidence_ledger.list_claims(project_id)

    if fmt == "bibtex":
        lines = []
        for i, s in enumerate(sources, start=1):
            key = f"source{i}"
            lines.append(f"@misc{{{key},")
            lines.append(f"  title = {{{s.get('title', '')}}},")
            lines.append(f"  url = {{{s.get('original_url', '')}}},")
            if s.get("publisher"):
                lines.append(f"  publisher = {{{s.get('publisher')}}},")
            if s.get("publication_date"):
                lines.append(f"  year = {{{s.get('publication_date')[:4]}}},")
            lines.append(f"  note = {{Accessed: {s.get('retrieved_at', '')[:10]}}}")
            lines.append("}\n")
        return Response(content="\n".join(lines), media_type="text/plain")

    # Default markdown format
    report_md = mgr.synthesizer.synthesize_report(
        project_id=project_id,
        topic="Research Synthesis",
        objective="Autonomous Research Deliverable",
        sources=sources,
        claims=claims,
    )
    return Response(content=report_md, media_type="text/markdown")
