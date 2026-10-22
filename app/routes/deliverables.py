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

from app.auth import get_current_user, get_csrf_token, verify_csrf, verify_session_token
from app.config import settings
from app.database import get_db_connection
from app.services.artifacts import artifact_service
from app.services.projects import list_projects

logger = logging.getLogger("qwen_collective.routes.deliverables")


def _authenticate_request(request: Request, mutating: bool = True) -> tuple[str, bool]:
    """
    Authenticate request via cookie session or Bearer token.
    Returns (username, is_bearer).
    Raises HTTPException(401) if unauthenticated, or HTTPException(403) if CSRF fails on mutating cookie requests.
    """
    user = get_current_user(request)
    is_bearer = False
    if not user:
        auth_hdr = request.headers.get("Authorization")
        if auth_hdr and auth_hdr.startswith("Bearer "):
            raw_token = auth_hdr[7:].strip()
            session_u = verify_session_token(raw_token)
            if session_u:
                user = session_u
                is_bearer = True
            elif raw_token == settings.admin_password:
                user = "admin"
                is_bearer = True
            else:
                c = get_db_connection()
                try:
                    row = c.execute("SELECT node_id FROM worker_auth_tokens WHERE token_hash = ? AND revoked = 0", (raw_token,)).fetchone()
                    if row:
                        user = f"node:{row['node_id']}"
                        is_bearer = True
                finally:
                    c.close()

    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")

    if mutating and not is_bearer:
        csrf_tok = request.headers.get("X-CSRF-Token")
        if not verify_csrf(request, csrf_tok):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid or missing CSRF token.")

    return user, is_bearer


def _get_project_owner(project_id: str) -> Optional[str]:
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT owner FROM projects WHERE id = ?", (project_id,)).fetchone()
        return row["owner"] if row else None
    finally:
        conn.close()


def _user_can_access_project(user: str, project_id: str) -> bool:
    if not project_id:
        return True
    if user in ("admin", "operator") or user.startswith("operator") or user.startswith("node:") or user.startswith("worker:"):
        return True
    owner = _get_project_owner(project_id)
    if owner is None:
        return False
    return owner == user


def _user_can_access_artifact(user: str, art: Dict[str, Any]) -> bool:
    if user in ("admin", "operator") or user.startswith("operator") or user.startswith("node:") or user.startswith("worker:"):
        return True
    if art.get("visibility") in ("shared", "collective"):
        return True
    origin_pid = art.get("origin_project_id")
    if origin_pid and _user_can_access_project(user, origin_pid):
        return True
    if art.get("created_by") == user:
        return True
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT p.owner FROM artifact_project_links apl
            JOIN projects p ON p.id = apl.project_id
            WHERE apl.artifact_id = ?
            """,
            (art["id"],),
        ).fetchall()
        for r in rows:
            if r["owner"] == user:
                return True
    finally:
        conn.close()
    return False


def _verify_artifact_access_or_raise(user: str, artifact_id: str, project_id: Optional[str] = None) -> Dict[str, Any]:
    art = artifact_service.get_artifact(artifact_id)
    if not art:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Artifact '{artifact_id}' not found.")
    if project_id:
        if not _user_can_access_project(user, project_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to requested project.")
        if art.get("origin_project_id") != project_id:
            conn = get_db_connection()
            try:
                link = conn.execute(
                    "SELECT 1 FROM artifact_project_links WHERE artifact_id = ? AND project_id = ?",
                    (artifact_id, project_id),
                ).fetchone()
            finally:
                conn.close()
            if not link and art.get("visibility") not in ("shared", "collective"):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Artifact is not associated with this project.")
    elif not _user_can_access_artifact(user, art):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to this artifact.")
    return art


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

    if project_id and not _user_can_access_project(user, project_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to requested project.")

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

    is_admin = user in ("admin", "operator") or user.startswith("operator")
    if not is_admin and not project_id:
        artifacts = [a for a in artifacts if _user_can_access_artifact(user, a)]

    # Compute statistics scoped by authorization
    conn = get_db_connection()
    try:
        if is_admin:
            total_count = conn.execute("SELECT count(*) FROM artifacts").fetchone()[0]
            total_storage = conn.execute("SELECT COALESCE(sum(size_bytes), 0) FROM artifacts").fetchone()[0]
            deliverables_count = conn.execute("SELECT count(*) FROM deliverables WHERE primary_artifact_id IS NOT NULL").fetchone()[0]
            reuse_count = conn.execute("SELECT count(*) FROM artifact_lineage WHERE relationship_type IN ('uses_as_input', 'derived_from', 'revised_from')").fetchone()[0]
            projects_list = [dict(r) for r in conn.execute("SELECT id, name FROM projects ORDER BY name ASC").fetchall()]
        else:
            projects_list = [dict(r) for r in conn.execute("SELECT id, name FROM projects WHERE owner = ? ORDER BY name ASC", (user,)).fetchall()]
            user_pids = [p["id"] for p in projects_list]
            if user_pids:
                ph = ",".join("?" for _ in user_pids)
                total_count = conn.execute(f"SELECT count(*) FROM artifacts WHERE origin_project_id IN ({ph}) OR visibility IN ('shared', 'collective')", user_pids).fetchone()[0]
                total_storage = conn.execute(f"SELECT COALESCE(sum(size_bytes), 0) FROM artifacts WHERE origin_project_id IN ({ph}) OR visibility IN ('shared', 'collective')", user_pids).fetchone()[0]
                deliverables_count = conn.execute(f"SELECT count(*) FROM deliverables WHERE project_id IN ({ph}) AND primary_artifact_id IS NOT NULL", user_pids).fetchone()[0]
            else:
                total_count = 0
                total_storage = 0
                deliverables_count = 0
            reuse_count = 0
    finally:
        conn.close()

    artifact_types = [
        "document", "dataset", "report", "code", "image", "chart",
        "json", "log", "research_notes", "presentation", "archive", "other"
    ]

    return templates.TemplateResponse(
        request=request,
        name="deliverables/index.html",
        context={
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

    art = _verify_artifact_access_or_raise(user, artifact_id)
    selected_version = version or art["current_version_number"]
    lineage = artifact_service.get_artifact_lineage(artifact_id)

    try:
        preview = artifact_service.get_preview_data(artifact_id, version_number=selected_version)
    except FileNotFoundError:
        preview = {"preview_type": "error", "content": "Artifact file not found in storage.", "truncated": False}
    except PermissionError as pe:
        preview = {"preview_type": "error", "content": f"Access denied: {pe}", "truncated": False}
    except ValueError as ve:
        preview = {"preview_type": "error", "content": f"Integrity error: {ve}", "truncated": False}
    except Exception as ex:
        preview = {"preview_type": "error", "content": f"Preview unavailable: {ex}", "truncated": False}

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

        # Fetch projects list for reuse dropdown (scoped to user's projects unless admin)
        if user in ("admin", "operator") or user.startswith("operator"):
            projects_list = [dict(r) for r in conn.execute("SELECT id, name FROM projects ORDER BY name ASC").fetchall()]
        else:
            projects_list = [dict(r) for r in conn.execute("SELECT id, name FROM projects WHERE owner = ? ORDER BY name ASC", (user,)).fetchall()]
    finally:
        conn.close()

    return templates.TemplateResponse(
        request=request,
        name="deliverables/detail.html",
        context={
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
            actor=user if isinstance(user, str) else (user.get("username", "admin") if user else "admin"),
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
            created_by=user if isinstance(user, str) else (user.get("username", "admin") if user else "admin"),
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
            actor=user if isinstance(user, str) else (user.get("username", "admin") if user else "admin"),
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


class ArtifactTransferRequest(BaseModel):
    target_project_id: str


class ArtifactPromoteRequest(BaseModel):
    vault_path: Optional[str] = None
    tags: List[str] = Field(default_factory=list)


class ArtifactReviseRequest(BaseModel):
    content_base64: str
    change_description: str = ""
    expected_sha256: Optional[str] = None


class CreateProjectFromArtifactRequest(BaseModel):
    user_objective: str
    title: Optional[str] = None


@router.get("/api/v1/artifacts")
def api_list_artifacts(
    request: Request,
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
    """List & search artifacts via REST API with complete authorization."""
    user, is_bearer = _authenticate_request(request, mutating=False)

    if project_id and not _user_can_access_project(user, project_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Principal '{user}' is not authorized to access artifacts for project '{project_id}'."
        )

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

    is_admin = user in ("admin", "operator") or user.startswith("operator") or user.startswith("node:") or user.startswith("worker:")
    if not is_admin and not project_id:
        artifacts = [a for a in artifacts if _user_can_access_artifact(user, a)]

    return {"total": len(artifacts), "artifacts": artifacts}


@router.get("/api/v1/artifacts/{artifact_id}")
def api_get_artifact(artifact_id: str, request: Request):
    """Get artifact details, versions, and linkages."""
    user, is_bearer = _authenticate_request(request, mutating=False)
    art = _verify_artifact_access_or_raise(user, artifact_id)
    return art


@router.get("/api/v1/artifacts/{artifact_id}/preview")
def api_get_artifact_preview(
    artifact_id: str,
    request: Request,
    version: Optional[int] = None,
    project_id: Optional[str] = None,
):
    """Get safe preview JSON data for artifact."""
    user, is_bearer = _authenticate_request(request, mutating=False)
    _verify_artifact_access_or_raise(user, artifact_id, project_id=project_id)
    try:
        return artifact_service.get_preview_data(artifact_id, version_number=version, project_id=project_id)
    except FileNotFoundError as fe:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(fe))
    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(pe))
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as ex:
        logger.error("Preview generation error for artifact %s: %s", artifact_id, ex)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Preview generation failed.")


@router.get("/api/v1/artifacts/{artifact_id}/download")
def api_download_artifact(
    artifact_id: str,
    request: Request,
    version: Optional[int] = None,
    project_id: Optional[str] = None,
    inline: int = 0,
):
    """One-click download with cryptographic checksum headers."""
    user, is_bearer = _authenticate_request(request, mutating=False)
    _verify_artifact_access_or_raise(user, artifact_id, project_id=project_id)
    try:
        dl = artifact_service.resolve_download(artifact_id, version_number=version, project_id=project_id)
        # Security hardening against active script execution in origin:
        # Never allow HTML or SVG to execute as active DOM in the privileged browser origin.
        mime = dl["mime_type"].lower()
        name = dl["display_name"].lower()
        is_active_markup = (
            mime in ("text/html", "application/xhtml+xml", "image/svg+xml")
            or name.endswith((".html", ".htm", ".svg"))
        )
        disposition_type = "inline" if (inline and not is_active_markup) else "attachment"
        served_mime = "text/plain" if (inline and is_active_markup) else dl["mime_type"]

        headers = {
            "X-Artifact-Id": dl["artifact_id"],
            "X-Artifact-Version": str(dl["version_number"]),
            "X-Artifact-Checksum-SHA256": dl["checksum_sha256"],
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
        }

        return FileResponse(
            path=str(dl["file_path"]),
            media_type=served_mime,
            filename=dl["display_name"],
            content_disposition_type=disposition_type,
            headers=headers,
        )
    except FileNotFoundError as fe:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(fe))
    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(pe))
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))


@router.get("/api/v1/artifacts/{artifact_id}/lineage")
def api_get_artifact_lineage(artifact_id: str, request: Request):
    """Get upstream and downstream lineage graph."""
    user, is_bearer = _authenticate_request(request, mutating=False)
    _verify_artifact_access_or_raise(user, artifact_id)
    return artifact_service.get_artifact_lineage(artifact_id)


@router.post("/api/v1/artifacts/{artifact_id}/reuse")
def api_reuse_artifact(
    artifact_id: str,
    req: ArtifactReuseRequest,
    request: Request,
):
    """Send artifact into project as structured task input."""
    user, is_bearer = _authenticate_request(request, mutating=True)
    _verify_artifact_access_or_raise(user, artifact_id)
    if not _user_can_access_project(user, req.target_project_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Principal '{user}' is not authorized to reuse artifacts in project '{req.target_project_id}'."
        )
    actor = user
    try:
        return artifact_service.reuse_artifact_in_project(
            artifact_id=artifact_id,
            project_id=req.target_project_id,
            action_type=req.action_type,
            instructions=req.instructions,
            actor=actor,
        )
    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(pe))
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(ve))
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
    user, is_bearer = _authenticate_request(request, mutating=True)
    _verify_artifact_access_or_raise(user, artifact_id)
    actor = user
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
    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(pe))
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as ex:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ex))


@router.post("/api/v1/artifacts/{artifact_id}/knowledge")
def api_extract_knowledge(artifact_id: str, request: Request):
    """Extract findings into knowledge base."""
    user, is_bearer = _authenticate_request(request, mutating=True)
    _verify_artifact_access_or_raise(user, artifact_id)
    actor = user
    try:
        records = artifact_service.extract_knowledge_from_artifact(artifact_id, actor=actor)
        return {"status": "extracted", "records_created": len(records), "records": records}
    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(pe))
    except Exception as ex:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ex))


@router.post("/api/v1/artifacts/{artifact_id}/transfer", status_code=status.HTTP_201_CREATED)
def api_transfer_artifact(
    artifact_id: str,
    req: ArtifactTransferRequest,
    request: Request,
):
    """API Specification 4.3: Transfer Artifact Across Projects."""
    user, is_bearer = _authenticate_request(request, mutating=True)

    # Worker automation tokens cannot transfer artifacts across projects
    if user.startswith("node:") or user.startswith("worker:"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Worker automation tokens cannot transfer artifacts across projects.",
        )

    # Verify source artifact exists
    art = artifact_service.get_artifact(artifact_id)
    if not art:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Artifact '{artifact_id}' not found.")

    # Cross-project authorization check
    conn = get_db_connection()
    try:
        t_row = conn.execute("SELECT owner FROM projects WHERE id = ?", (req.target_project_id,)).fetchone()
        if not t_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Target project '{req.target_project_id}' not found.",
            )
        target_owner = t_row["owner"]

        src_owner = ""
        if art.get("origin_project_id"):
            s_row = conn.execute("SELECT owner FROM projects WHERE id = ?", (art["origin_project_id"],)).fetchone()
            src_owner = s_row["owner"] if s_row else ""
    finally:
        conn.close()

    if user not in ("admin", "operator"):
        if src_owner and src_owner != user:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Principal '{user}' is not authorized to transfer artifact from project '{art.get('origin_project_id')}'.",
            )
        if target_owner and target_owner != user:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Principal '{user}' is not authorized to transfer artifact into target project '{req.target_project_id}'.",
            )

    actor = user
    try:
        res = artifact_service.reuse_artifact_in_project(
            artifact_id=artifact_id,
            project_id=req.target_project_id,
            action_type="transferred_to",
            instructions=f"Transferred from source project to {req.target_project_id}",
            actor=actor,
        )
        return JSONResponse(
            {
                "new_artifact_id": artifact_id,
                "target_project_id": req.target_project_id,
                "lineage_edge": "transferred_to",
                "reuse_id": res.get("reuse_id"),
            },
            status_code=status.HTTP_201_CREATED,
        )
    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(pe))
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(ve))
    except Exception as ex:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ex))


@router.post("/api/v1/artifacts/{artifact_id}/promote")
def api_promote_artifact(
    artifact_id: str,
    req: ArtifactPromoteRequest,
    request: Request,
):
    """API Specification 4.4: Promote Deliverable to Obsidian Vault."""
    user, is_bearer = _authenticate_request(request, mutating=True)

    # Worker automation tokens cannot promote artifacts without human operator review
    if user.startswith("node:") or user.startswith("worker:"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Worker automation tokens cannot promote artifacts to knowledge vault; human review required.",
        )

    art = artifact_service.get_artifact(artifact_id)
    if not art:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Artifact '{artifact_id}' not found.")

    if user not in ("admin", "operator"):
        origin_pid = art.get("origin_project_id")
        if origin_pid:
            conn = get_db_connection()
            try:
                prow = conn.execute("SELECT owner FROM projects WHERE id = ?", (origin_pid,)).fetchone()
                owner = prow["owner"] if prow else ""
            finally:
                conn.close()
            if owner and owner != user:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Principal '{user}' is not authorized to promote artifacts for project '{origin_pid}'.",
                )

    actor = user
    try:
        records = artifact_service.extract_knowledge_from_artifact(artifact_id, actor=actor)
        note_id = records[0]["id"] if records else f"not-{artifact_id[:12]}"
        return {
            "status": "promoted",
            "note_id": note_id,
            "indexed_fts": True,
            "vault_path": req.vault_path or f"knowledge/{artifact_id}.md",
        }
    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(pe))
    except Exception as ex:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ex))


@router.post("/api/v1/artifacts/{artifact_id}/create-project")
def api_create_project_from_artifact(
    artifact_id: str,
    req: CreateProjectFromArtifactRequest,
    request: Request,
):
    """Initialize a new project using an existing artifact."""
    user, is_bearer = _authenticate_request(request, mutating=True)
    _verify_artifact_access_or_raise(user, artifact_id)
    actor = user
    try:
        return artifact_service.create_project_from_artifact(
            artifact_id=artifact_id,
            user_objective=req.user_objective,
            title=req.title,
            created_by=actor,
        )
    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(pe))
    except Exception as ex:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ex))


@router.get("/api/v1/projects/{project_id}/artifacts")
def api_get_project_artifacts(project_id: str, request: Request):
    """List all artifacts associated with a project."""
    user, is_bearer = _authenticate_request(request, mutating=False)
    if not _user_can_access_project(user, project_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Principal '{user}' is not authorized to access artifacts for project '{project_id}'."
        )
    return artifact_service.list_artifacts(project_id=project_id)


@router.get("/api/v1/artifacts/{artifact_id}/sandboxed-preview")
def api_sandboxed_preview(
    artifact_id: str,
    request: Request,
    version: Optional[int] = None,
    project_id: Optional[str] = None,
):
    """Serve HTML or SVG with strict sandbox CSP preventing any script execution."""
    from fastapi import Response
    user, is_bearer = _authenticate_request(request, mutating=False)
    _verify_artifact_access_or_raise(user, artifact_id, project_id=project_id)
    try:
        dl = artifact_service.resolve_download(artifact_id, version_number=version, project_id=project_id)
        raw_bytes = dl["file_path"].read_bytes()
        headers = {
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "SAMEORIGIN",
        }
        return Response(content=raw_bytes, media_type=dl["mime_type"], headers=headers)
    except FileNotFoundError as fe:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(fe))
    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(pe))
    except Exception as ex:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ex))


class ArtifactRevokeRequest(BaseModel):
    reason: str = Field("Authorization revoked", description="Reason for revocation")


@router.post("/api/v1/artifacts/{artifact_id}/revoke")
def api_revoke_artifact(
    artifact_id: str,
    req: ArtifactRevokeRequest,
    request: Request,
):
    """Revoke artifact access authorization."""
    user, is_bearer = _authenticate_request(request, mutating=True)
    art = artifact_service.get_artifact(artifact_id)
    if not art:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Artifact '{artifact_id}' not found.")
    is_admin = user in ("admin", "operator") or user.startswith("operator")
    if not is_admin:
        origin_pid = art.get("origin_project_id")
        if not origin_pid or not _user_can_access_project(user, origin_pid):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Principal '{user}' is not authorized to revoke artifact '{artifact_id}'."
            )
    actor = user
    try:
        artifact_service.revoke_artifact(artifact_id, revoked_by=actor, reason=req.reason)
        return {"status": "revoked", "artifact_id": artifact_id}
    except Exception as ex:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ex))

