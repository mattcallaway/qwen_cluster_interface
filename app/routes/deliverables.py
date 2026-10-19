"""
Global Deliverables & Artifacts Hub router for Qwen Collective.
Provides:
- Web views: Global Deliverables library (/deliverables) and detail page (/deliverables/{artifact_id})
- One-click Open/Preview and Download endpoints with integrity verification
- Lineage, Versioning, and Knowledge Base extraction actions
- Reuse in same project, reuse in another project, and Artifact-Aware Project Creation
- REST API (/api/v1/artifacts/*)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.auth import get_current_user, get_csrf_token, verify_csrf
from app.database import get_db_connection
from app.services.artifacts import artifact_service
from app.services.projects import list_projects

logger = logging.getLogger("qwen_collective.routes.deliverables")

router = APIRouter(tags=["deliverables"])
templates = Jinja2Templates(directory="app/templates")


# ---------------------------------------------------------------------------
# Web UI Pages
# ---------------------------------------------------------------------------

@router.get("/deliverables", response_class=HTMLResponse)
def deliverables_library(
    request: Request,
    q: Optional[str] = None,
    type: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    project_id: Optional[str] = None,
    worker_id: Optional[str] = None,
    knowledge_status: Optional[str] = None,
    sort: str = "recent",
    limit: int = 60,
    offset: int = 0,
):
    """Global Deliverables & Artifacts Hub."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)

    artifacts = artifact_service.list_artifacts(
        project_id=project_id,
        artifact_type=type,
        worker_id=worker_id,
        status=status_filter,
        knowledge_status=knowledge_status,
        search_query=q,
        sort_by=sort,
        limit=limit,
        offset=offset,
    )

    # Compute high-level statistics
    conn = get_db_connection()
    try:
        total_count = conn.execute("SELECT count(*) FROM artifacts").fetchone()[0]
        total_storage = conn.execute("SELECT COALESCE(sum(size_bytes), 0) FROM artifacts").fetchone()[0]
        deliverables_count = conn.execute("SELECT count(*) FROM deliverables WHERE primary_artifact_id IS NOT NULL").fetchone()[0]
        reuse_count = conn.execute("SELECT count(*) FROM artifact_lineage WHERE relationship_type IN ('uses_as_input', 'derived_from', 'revised_from')").fetchone()[0]
        projects_list = [dict(r) for r in conn.execute("SELECT id, name FROM projects ORDER BY name ASC").fetchall()]
    finally:
        conn.close()

    artifact_types = [
        "document", "dataset", "report", "code", "image", "chart",
        "json", "log", "research_notes", "presentation", "archive", "other"
    ]

    return templates.TemplateResponse(
        "deliverables/index.html",
        {
            "request": request,
            "user": user,
            "csrf_token": get_csrf_token(request),
            "artifacts": artifacts,
            "total_count": total_count,
            "total_storage": total_storage,
            "deliverables_count": deliverables_count,
            "reuse_count": reuse_count,
            "projects": projects_list,
            "artifact_types": artifact_types,
            "selected_project_id": project_id,
            "selected_type": type,
            "selected_status": status_filter,
            "selected_knowledge_status": knowledge_status,
            "search_query": q or "",
            "selected_sort": sort,
        },
    )


@router.get("/deliverables/{artifact_id}", response_class=HTMLResponse)
def deliverable_detail(
    artifact_id: str,
    request: Request,
    version: Optional[int] = None,
):
    """Artifact & Deliverable Detail Page."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)

    art = artifact_service.get_artifact(artifact_id)
    if not art:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Artifact '{artifact_id}' not found.")

    selected_version = version or art["current_version_number"]
    lineage = artifact_service.get_artifact_lineage(artifact_id)
    preview = artifact_service.get_preview_data(artifact_id, version_number=selected_version)

    # Fetch knowledge links
    conn = get_db_connection()
    try:
        klinks = conn.execute(
            """
            SELECT akl.*, kr.statement, kr.verification_status, kr.knowledge_type
            FROM artifact_knowledge_links akl
            JOIN knowledge_records kr ON kr.id = akl.knowledge_record_id
            WHERE akl.artifact_id = ?
            ORDER BY akl.created_at DESC
            """,
            (artifact_id,),
        ).fetchall()
        knowledge_links = [dict(k) for k in klinks]

        # Fetch projects list for reuse dropdown
        projects_list = [dict(r) for r in conn.execute("SELECT id, name FROM projects ORDER BY name ASC").fetchall()]
    finally:
        conn.close()

    return templates.TemplateResponse(
        "deliverables/detail.html",
        {
            "request": request,
            "user": user,
            "csrf_token": get_csrf_token(request),
            "artifact": art,
            "selected_version": selected_version,
            "lineage": lineage,
            "preview": preview,
            "knowledge_links": knowledge_links,
            "projects": projects_list,
        },
    )


# ---------------------------------------------------------------------------
# UI Workflow Form Handlers
# ---------------------------------------------------------------------------

@router.post("/deliverables/{artifact_id}/reuse")
def handle_artifact_reuse_form(
    artifact_id: str,
    request: Request,
    target_project_id: str = Form(...),
    action_type: str = Form("analyze"),
    instructions: str = Form(""),
    csrf_token: str = Form(...),
):
    """Handle 'Use in This Project' or 'Use in Another Project' web form."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)
    verify_csrf(request, csrf_token)

    try:
        res = artifact_service.reuse_artifact_in_project(
            artifact_id=artifact_id,
            project_id=target_project_id,
            action_type=action_type,
            instructions=instructions,
            actor=user.get("username", "admin"),
        )
        return RedirectResponse(
            url=f"/projects/{target_project_id}?tab=tasks",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except Exception as ex:
        logger.error("Failed to reuse artifact %s: %s", artifact_id, ex)
        return RedirectResponse(
            url=f"/deliverables/{artifact_id}?error={str(ex)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )


@router.post("/deliverables/{artifact_id}/create-project")
def handle_create_project_from_artifact(
    artifact_id: str,
    request: Request,
    user_objective: str = Form(...),
    title: Optional[str] = Form(None),
    csrf_token: str = Form(...),
):
    """Handle 'Create Project From This' web form."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)
    verify_csrf(request, csrf_token)

    try:
        res = artifact_service.create_project_from_artifact(
            artifact_id=artifact_id,
            user_objective=user_objective,
            title=title,
            created_by=user.get("username", "admin"),
        )
        new_proj_id = res["project"]["id"]
        return RedirectResponse(
            url=f"/projects/{new_proj_id}?tab=overview",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except Exception as ex:
        logger.error("Failed to create project from artifact %s: %s", artifact_id, ex)
        return RedirectResponse(
            url=f"/deliverables/{artifact_id}?error={str(ex)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )


@router.post("/deliverables/{artifact_id}/knowledge")
def handle_extract_knowledge_form(
    artifact_id: str,
    request: Request,
    csrf_token: str = Form(...),
):
    """Handle 'Add to Knowledge' extraction web form."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)
    verify_csrf(request, csrf_token)

    try:
        artifact_service.extract_knowledge_from_artifact(
            artifact_id=artifact_id,
            actor=user.get("username", "admin"),
        )
        return RedirectResponse(
            url=f"/deliverables/{artifact_id}?msg=Knowledge+extracted+successfully",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except Exception as ex:
        logger.error("Failed to extract knowledge from artifact %s: %s", artifact_id, ex)
        return RedirectResponse(
            url=f"/deliverables/{artifact_id}?error={str(ex)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )


# ---------------------------------------------------------------------------
# REST API Endpoints (/api/v1/artifacts/*)
# ---------------------------------------------------------------------------

class ArtifactReuseRequest(BaseModel):
    target_project_id: str
    action_type: str = "analyze"
    instructions: str = ""


class ArtifactReviseRequest(BaseModel):
    content_base64: str
    change_description: str = ""
    expected_sha256: Optional[str] = None


class CreateProjectFromArtifactRequest(BaseModel):
    user_objective: str
    title: Optional[str] = None


@router.get("/api/v1/artifacts")
def api_list_artifacts(
    project_id: Optional[str] = None,
    type: Optional[str] = None,
    worker_id: Optional[str] = None,
    status: Optional[str] = None,
    validation_status: Optional[str] = None,
    knowledge_status: Optional[str] = None,
    q: Optional[str] = None,
    sort: str = "recent",
    limit: int = 50,
    offset: int = 0,
):
    """List & search artifacts via REST API."""
    artifacts = artifact_service.list_artifacts(
        project_id=project_id,
        artifact_type=type,
        worker_id=worker_id,
        status=status,
        validation_status=validation_status,
        knowledge_status=knowledge_status,
        search_query=q,
        sort_by=sort,
        limit=limit,
        offset=offset,
    )
    return {"total": len(artifacts), "artifacts": artifacts}


@router.get("/api/v1/artifacts/{artifact_id}")
def api_get_artifact(artifact_id: str):
    """Get artifact details, versions, and linkages."""
    art = artifact_service.get_artifact(artifact_id)
    if not art:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found.")
    return art


@router.get("/api/v1/artifacts/{artifact_id}/preview")
def api_get_artifact_preview(
    artifact_id: str,
    version: Optional[int] = None,
    project_id: Optional[str] = None,
):
    """Get safe preview JSON data for artifact."""
    try:
        return artifact_service.get_preview_data(artifact_id, version_number=version, project_id=project_id)
    except FileNotFoundError as fe:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(fe))
    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(pe))
    except Exception as ex:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(ex))


@router.get("/api/v1/artifacts/{artifact_id}/download")
def api_download_artifact(
    artifact_id: str,
    version: Optional[int] = None,
    project_id: Optional[str] = None,
    inline: int = 0,
):
    """One-click download with cryptographic checksum headers."""
    try:
        dl = artifact_service.resolve_download(artifact_id, version_number=version, project_id=project_id)
        disposition_type = "inline" if inline else "attachment"
        return FileResponse(
            path=str(dl["file_path"]),
            media_type=dl["mime_type"],
            filename=dl["display_name"],
            content_disposition_type=disposition_type,
            headers={
                "X-Artifact-Id": dl["artifact_id"],
                "X-Artifact-Version": str(dl["version_number"]),
                "X-Artifact-Checksum-SHA256": dl["checksum_sha256"],
            },
        )
    except FileNotFoundError as fe:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(fe))
    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(pe))
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))


@router.get("/api/v1/artifacts/{artifact_id}/lineage")
def api_get_artifact_lineage(artifact_id: str):
    """Get upstream and downstream lineage graph."""
    return artifact_service.get_artifact_lineage(artifact_id)


@router.post("/api/v1/artifacts/{artifact_id}/reuse")
def api_reuse_artifact(
    artifact_id: str,
    req: ArtifactReuseRequest,
    request: Request,
):
    """Send artifact into project as structured task input."""
    user = get_current_user(request)
    actor = user.get("username", "api_user") if user else "api_user"
    try:
        return artifact_service.reuse_artifact_in_project(
            artifact_id=artifact_id,
            project_id=req.target_project_id,
            action_type=req.action_type,
            instructions=req.instructions,
            actor=actor,
        )
    except Exception as ex:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ex))


@router.post("/api/v1/artifacts/{artifact_id}/revise")
def api_revise_artifact(
    artifact_id: str,
    req: ArtifactReviseRequest,
    request: Request,
):
    """Create a new immutable revision of an artifact."""
    import base64
    user = get_current_user(request)
    actor = user.get("username", "api_user") if user else "api_user"
    try:
        content_bytes = base64.b64decode(req.content_base64)
    except Exception as ex:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid base64: {ex}")

    try:
        return artifact_service.create_artifact_version(
            artifact_id=artifact_id,
            data=content_bytes,
            change_description=req.change_description,
            created_by=actor,
            expected_sha256=req.expected_sha256,
        )
    except Exception as ex:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ex))


@router.post("/api/v1/artifacts/{artifact_id}/knowledge")
def api_extract_knowledge(artifact_id: str, request: Request):
    """Extract findings into knowledge base."""
    user = get_current_user(request)
    actor = user.get("username", "api_user") if user else "api_user"
    try:
        records = artifact_service.extract_knowledge_from_artifact(artifact_id, actor=actor)
        return {"status": "extracted", "records_created": len(records), "records": records}
    except Exception as ex:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ex))


@router.post("/api/v1/artifacts/{artifact_id}/create-project")
def api_create_project_from_artifact(
    artifact_id: str,
    req: CreateProjectFromArtifactRequest,
    request: Request,
):
    """Initialize a new project using an existing artifact."""
    user = get_current_user(request)
    actor = user.get("username", "api_user") if user else "api_user"
    try:
        return artifact_service.create_project_from_artifact(
            artifact_id=artifact_id,
            user_objective=req.user_objective,
            title=req.title,
            created_by=actor,
        )
    except Exception as ex:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ex))


@router.get("/api/v1/projects/{project_id}/artifacts")
def api_get_project_artifacts(project_id: str):
    """List all artifacts associated with a project."""
    return artifact_service.list_artifacts(project_id=project_id)
