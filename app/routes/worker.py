"""
Worker API Endpoints for Qwen Collective (Milestone 2).
Enforces:
- Bearer token authentication per node
- Pull-based task leasing protocol
- Heartbeats and lease extensions
- Idempotent result submissions
- Append-only execution history
"""

import base64
import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.auth import get_current_user, verify_csrf
from app.services import worker as worker_svc
from app.services.upload_security import PayloadTooLargeError
from app.services.observatory import alerting as obs_alerting
from app.services.observatory import collector as obs_collector
from app.services.observatory.models import TelemetrySamplePayload

logger = logging.getLogger("qwen_collective.worker_api")

router = APIRouter(prefix="/api/v1/worker", tags=["worker"])


# ---------------------------------------------------------------------------
# Request & Response Schemas
# ---------------------------------------------------------------------------

class ArtifactUploadRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255)
    content_base64: str = Field(..., min_length=1)
    lease_id: Optional[str] = None
    expected_sha256: Optional[str] = None
    artifact_type: str = "general"


class ScriptStartRequest(BaseModel):
    model_config = {'extra':'forbid'}
    idempotency_key: str = Field(...,min_length=1,max_length=256)
    nonce: str = Field(...,pattern='^[0-9a-f]{64}$')
    manifest: Dict[str, Any]


class EnrollmentTokenCreateRequest(BaseModel):
    allowed_node_id: Optional[str] = None
    expires_in_seconds: int = Field(default=86400, ge=60, le=2592000)


class WorkerRegistrationRequest(BaseModel):
    node_id: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=200)
    role: str = Field(default="vps_worker")
    enrollment_token: Optional[str] = None
    hostname: str = Field(default="")
    os_name: str = Field(default="Linux")
    os_version: str = Field(default="")
    architecture: str = Field(default="x86_64")
    cpu_count: int = Field(default=1, ge=1)
    ram_total_mb: int = Field(default=1024, ge=128)
    ram_available_mb: int = Field(default=512, ge=0)
    disk_total_gb: float = Field(default=20.0, ge=1.0)
    disk_free_gb: float = Field(default=10.0, ge=0.0)
    ip_addresses: List[str] = Field(default_factory=list)
    tailscale_ip: Optional[str] = None
    agent_version: str = Field(default="1.0.0")
    installed_runtimes: List[str] = Field(default_factory=list)
    registered_services: List[str] = Field(default_factory=list)
    declared_capabilities: List[str] = Field(default_factory=list)
    available_models: List[str] = Field(default_factory=list)


class WorkerHeartbeatRequest(BaseModel):
    node_id: str
    cpu_percent: Optional[float] = None
    ram_available_mb: Optional[int] = None
    disk_free_gb: Optional[float] = None
    running_jobs_count: int = 0
    available_models: Optional[List[str]] = None
    # Extended System Observatory metrics
    ram_total_mb: Optional[int] = None
    swap_total_mb: Optional[int] = None
    swap_used_mb: Optional[int] = None
    swap_percent: Optional[float] = None
    cpu_temp_c: Optional[float] = None
    battery_percent: Optional[float] = None
    is_ac_online: Optional[int] = -1
    worker_res_mb: Optional[float] = None
    ollama_res_mb: Optional[float] = None
    latency_to_controller_ms: Optional[float] = None
    gpu: Optional[Dict[str, Any]] = None
    per_core: Optional[List[float]] = None


class TaskLeaseRequest(BaseModel):
    worker_id: str
    supported_categories: List[str] = Field(default_factory=list)
    lease_duration_seconds: int = Field(default=300, ge=30, le=3600)
    available_models: List[str] = Field(default_factory=list)
    available_ram_mb: Optional[int] = None


class TaskResultSubmission(BaseModel):
    task_id: str
    lease_id: Optional[str] = None
    idempotency_key: str
    status: str = Field(..., pattern="^(success|error|timeout|cancelled)$")
    exit_code: int = 0
    runtime_seconds: float = 0.0
    stdout_summary: str = ""
    stderr_summary: str = ""
    error_category: Optional[str] = None
    result_payload: Dict[str, Any] = Field(default_factory=dict)
    deliverable_ids: List[str] = Field(default_factory=list)
    artifacts: List[Dict[str, Any]] = Field(default_factory=list)


class TaskHeartbeatPayload(BaseModel):
    additional_seconds: Optional[int] = 300
    tool_name: Optional[str] = None
    tool_args: Optional[Dict[str, Any]] = None
    result_summary: Optional[Any] = None
    error_category: Optional[str] = None
    progress_milestone: Optional[bool] = None


class TaskTelemetryPayload(BaseModel):
    tool_name: str
    tool_args: Optional[Dict[str, Any]] = None
    result_summary: Optional[Any] = None
    error_category: Optional[str] = None
    progress_milestone: Optional[bool] = None


# ---------------------------------------------------------------------------
# Authentication Dependency
# ---------------------------------------------------------------------------

def get_authenticated_worker(
    authorization: Optional[str] = Header(None),
    x_worker_token: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Validate Bearer or X-Worker-Token header."""
    raw_token = None
    if authorization and authorization.lower().startswith("bearer "):
        raw_token = authorization[7:].strip()
    elif x_worker_token:
        raw_token = x_worker_token.strip()

    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing worker authentication token. Provide 'Authorization: Bearer <token>'.",
        )

    worker_info = worker_svc.verify_worker_token(raw_token)
    if not worker_info:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked worker authentication token.",
        )

    return worker_info


# ---------------------------------------------------------------------------
# Worker Registration & Enrollment Endpoints
# ---------------------------------------------------------------------------

@router.post("/register")
def register_worker(
    req: WorkerRegistrationRequest,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_worker_token: Optional[str] = Header(None),
    x_enrollment_token: Optional[str] = Header(None),
):
    """
    Register or update an execution worker node.
    Requires one of:
    1. Existing valid worker Bearer token matching req.node_id (inventory update)
    2. Single-use, valid, unexpired enrollment token (X-Enrollment-Token header or body field)
    3. Active administrator session cookie
    """
    raw_worker_token = None
    if authorization and authorization.lower().startswith("bearer "):
        raw_worker_token = authorization[7:].strip()
    elif x_worker_token:
        raw_worker_token = x_worker_token.strip()

    cand_enrollment_token = x_enrollment_token or req.enrollment_token
    token_status = "unauthorized"
    new_worker_token = None
    token_id = None

    # Case 1: Existing authenticated worker token (inventory update)
    if raw_worker_token:
        token_info = worker_svc.verify_worker_token(raw_worker_token)
        if not token_info:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Provided worker token is invalid or revoked.",
            )
        if token_info["node_id"] != req.node_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Worker token node mismatch. Token belongs to '{token_info['node_id']}', requested '{req.node_id}'.",
            )
        token_id = token_info["token_id"]
        token_status = "authenticated"

    # Case 2: Explicit single-use enrollment token
    elif cand_enrollment_token:
        ok, msg = worker_svc.verify_and_consume_enrollment_token(cand_enrollment_token, req.node_id)
        if not ok:
            logger.warning("Enrollment token rejected for node %s: %s", req.node_id, msg)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Enrollment token rejected: {msg}",
            )
        token_status = "provisioned"

    # Case 3: Administrator session (requires CSRF verification)
    elif get_current_user(request):
        csrf_token = request.headers.get("X-CSRF-Token")
        if not verify_csrf(request, csrf_token):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid or missing CSRF token for administrator registration.",
            )
        token_status = "provisioned"

    # Case 4: No valid enrollment authorization
    else:
        logger.warning("Unauthenticated registration attempt for node %s", req.node_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Worker enrollment authorization required. Please provide a valid single-use enrollment token "
                "via the 'X-Enrollment-Token' header or authenticate with an administrator session."
            ),
        )

    # 2. Update node in infrastructure registry only after authorization is validated
    reg_result = worker_svc.register_worker_node(
        node_id=req.node_id,
        name=req.name,
        role=req.role,
        hostname=req.hostname,
        os_name=req.os_name,
        os_version=req.os_version,
        architecture=req.architecture,
        cpu_count=req.cpu_count,
        ram_total_mb=req.ram_total_mb,
        ram_available_mb=req.ram_available_mb,
        disk_total_gb=req.disk_total_gb,
        disk_free_gb=req.disk_free_gb,
        ip_addresses=req.ip_addresses,
        tailscale_ip=req.tailscale_ip,
        agent_version=req.agent_version,
        installed_runtimes=req.installed_runtimes,
        registered_services=req.registered_services,
        declared_capabilities=req.declared_capabilities,
        available_models=req.available_models,
    )

    # 3. Create persistent worker Bearer token for newly enrolled nodes
    if token_status == "provisioned":
        token_id, new_worker_token = worker_svc.create_worker_token(req.node_id)

    reg_result["token_id"] = token_id
    reg_result["token_status"] = token_status
    if new_worker_token:
        reg_result["worker_token"] = new_worker_token

    return reg_result


# ---------------------------------------------------------------------------
# Administrator Enrollment Token Management
# ---------------------------------------------------------------------------

@router.post("/enrollment-tokens")
def create_enrollment_token(
    req: EnrollmentTokenCreateRequest,
    request: Request,
):
    """Generate an administrator-issued enrollment token for a new worker node."""
    admin_user = get_current_user(request)
    if not admin_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Administrator login required.")
    csrf_token = request.headers.get("X-CSRF-Token")
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid or missing CSRF token.")

    token_id, raw_token = worker_svc.create_enrollment_token(
        allowed_node_id=req.allowed_node_id,
        expires_in_seconds=req.expires_in_seconds,
        created_by=admin_user,
    )
    return {
        "status": "created",
        "token_id": token_id,
        "enrollment_token": raw_token,
        "allowed_node_id": req.allowed_node_id,
        "expires_in_seconds": req.expires_in_seconds,
    }


@router.get("/enrollment-tokens")
def list_enrollment_tokens(request: Request):
    """List all worker enrollment tokens (admin only)."""
    admin_user = get_current_user(request)
    if not admin_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Administrator login required.")

    return worker_svc.list_enrollment_tokens()


@router.delete("/enrollment-tokens/{token_id}")
def revoke_enrollment_token(token_id: str, request: Request):
    """Revoke an active worker enrollment token (admin only)."""
    admin_user = get_current_user(request)
    if not admin_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Administrator login required.")
    csrf_token = request.headers.get("X-CSRF-Token")
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid or missing CSRF token.")

    success = worker_svc.revoke_enrollment_token(token_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Enrollment token not found.")
    return {"status": "revoked", "token_id": token_id}


@router.delete("/tokens/{token_id}")
def revoke_worker_token_route(token_id: str, request: Request):
    """Revoke an active worker Bearer token (admin only)."""
    admin_user = get_current_user(request)
    if not admin_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Administrator login required.")
    csrf_token = request.headers.get("X-CSRF-Token")
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid or missing CSRF token.")

    success = worker_svc.revoke_worker_token(token_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker token not found.")
    return {"status": "revoked", "token_id": token_id}


# ---------------------------------------------------------------------------
# Worker Runtime Operations
# ---------------------------------------------------------------------------

@router.post("/heartbeat")
def worker_heartbeat(
    req: WorkerHeartbeatRequest,
    worker: Dict[str, Any] = Depends(get_authenticated_worker),
):
    """
    Worker health check and heartbeat ping. Auto-renews active leases.
    """
    if worker["node_id"] != req.node_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token does not grant access to the specified node_id.",
        )

    res = worker_svc.record_worker_heartbeat(
        node_id=req.node_id,
        cpu_percent=req.cpu_percent,
        ram_available_mb=req.ram_available_mb,
        disk_free_gb=req.disk_free_gb,
        running_jobs_count=req.running_jobs_count,
        available_models=req.available_models,
    )

    # Evaluate alerts and record extended sample if provided
    try:
        sample_dict = {
            "cpu_percent": req.cpu_percent,
            "ram_total_mb": req.ram_total_mb,
            "ram_available_mb": req.ram_available_mb,
            "swap_total_mb": req.swap_total_mb,
            "swap_used_mb": req.swap_used_mb,
            "swap_percent": req.swap_percent,
            "disk_free_gb": req.disk_free_gb,
            "cpu_temp_c": req.cpu_temp_c,
            "battery_percent": req.battery_percent,
            "is_ac_online": req.is_ac_online,
            "worker_res_mb": req.worker_res_mb,
            "ollama_res_mb": req.ollama_res_mb,
            "latency_to_controller_ms": req.latency_to_controller_ms,
            "gpu": req.gpu,
            "per_core": req.per_core or [],
        }
        obs_alerting.evaluate_telemetry_for_alerts(req.node_id, sample_dict)
    except Exception as e:
        logger.debug("Failed to evaluate heartbeat alerts for %s: %s", req.node_id, e)

    return res


@router.post("/telemetry")
def submit_worker_telemetry(
    req: TelemetrySamplePayload,
    worker: Dict[str, Any] = Depends(get_authenticated_worker),
):
    """
    Submit structured hardware telemetry from an authenticated worker.
    Strictly binds submitted sample to worker's authenticated node_id to prevent spoofing.
    """
    if worker["node_id"] != req.node_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Token node '{worker['node_id']}' cannot report telemetry for '{req.node_id}'.",
        )

    sample_dict = req.model_dump()
    try:
        sample_id = obs_collector.record_worker_telemetry_submission(worker["node_id"], sample_dict)
        obs_alerting.evaluate_telemetry_for_alerts(worker["node_id"], sample_dict)
        return {
            "status": "recorded",
            "sample_id": sample_id,
            "node_id": worker["node_id"],
            "timestamp": sample_dict.get("timestamp"),
        }
    except Exception as ex:
        logger.error("Failed to ingest worker telemetry for %s: %s", worker["node_id"], ex)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ex))


@router.post("/tasks/lease")
@router.post("/lease-task")
def lease_task(
    req: TaskLeaseRequest,
    worker: Dict[str, Any] = Depends(get_authenticated_worker),
):
    """
    Request eligible task lease matching worker's verified capabilities and authoritative authorization.
    """
    if worker["node_id"] != req.worker_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token node mismatch.",
        )

    try:
        task = worker_svc.lease_task_for_worker(
            worker_id=req.worker_id,
            supported_categories=req.supported_categories,
            lease_duration_seconds=req.lease_duration_seconds,
            available_models=req.available_models,
            available_ram_mb=req.available_ram_mb,
        )
    except worker_svc.TaskAuthorizationError as tae:
        raise HTTPException(
            status_code=tae.status_code,
            detail=tae.message,
        )

    if not task:
        return {"status": "no_work", "message": "No eligible approved tasks available."}

    return {"status": "leased", "task": task}


@router.post('/tasks/{task_id}/authorize-script')
def authorize_script_start(task_id: str, req: ScriptStartRequest,
                           worker: Dict[str, Any] = Depends(get_authenticated_worker)):
    from app.services.orchestration.script_authorization import authorize_start
    try:
        return authorize_start(worker['node_id'],task_id,req.idempotency_key,req.manifest,req.nonce)
    except worker_svc.TaskAuthorizationError as exc:
        raise HTTPException(status_code=exc.status_code,detail=exc.message)


@router.post("/tasks/{lease_id}/heartbeat")
def extend_task_lease(
    lease_id: str,
    payload: Optional[TaskHeartbeatPayload] = Body(None),
    additional_seconds: Optional[int] = None,
    worker: Dict[str, Any] = Depends(get_authenticated_worker),
):
    """
    Renew active task lease heartbeat to prevent abandonment timeout.
    Optionally records tool operation telemetry and progress milestones.
    """
    sec = 300
    if payload and payload.additional_seconds is not None:
        sec = payload.additional_seconds
    elif additional_seconds is not None:
        sec = additional_seconds

    tool_name = payload.tool_name if payload else None
    tool_args = payload.tool_args if payload else None
    result_summary = payload.result_summary if payload else None
    error_category = payload.error_category if payload else None
    progress_milestone = payload.progress_milestone if payload else None

    try:
        success = worker_svc.renew_task_lease(
            worker_id=worker["node_id"],
            lease_id=lease_id,
            additional_seconds=sec,
            tool_name=tool_name,
            tool_args=tool_args,
            result_summary=result_summary,
            error_category=error_category,
            progress_milestone=progress_milestone,
        )
    except worker_svc.TaskAuthorizationError as tae:
        raise HTTPException(
            status_code=tae.status_code,
            detail=tae.message,
        )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active lease not found or already expired/completed.",
        )
    return {"status": "renewed", "lease_id": lease_id}


@router.post("/tasks/{lease_id}/telemetry")
def submit_task_telemetry(
    lease_id: str,
    payload: TaskTelemetryPayload,
    worker: Dict[str, Any] = Depends(get_authenticated_worker),
):
    """
    Submit intermediate tool operation telemetry or progress milestones during task execution.
    """
    success = worker_svc.record_task_telemetry(
        worker_id=worker["node_id"],
        lease_id=lease_id,
        tool_name=payload.tool_name,
        arguments=payload.tool_args,
        result=payload.result_summary,
        error_category=payload.error_category,
        progress_milestone=payload.progress_milestone,
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active lease not found or already expired/completed.",
        )
    return {"status": "recorded", "lease_id": lease_id}


@router.post("/tasks/{task_id}/submit")
def submit_task_result(
    task_id: str,
    submission: TaskResultSubmission,
    worker: Dict[str, Any] = Depends(get_authenticated_worker),
):
    """
    Submit completed task results with idempotency guard.
    Enforces F-10:
    - Authoritative task assignment and lease fencing validation
    - Idempotent deduplication and conflict detection
    """
    if submission.task_id != task_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Task ID in path does not match payload.",
        )

    try:
        result = worker_svc.submit_task_result(
            worker_id=worker["node_id"],
            task_id=task_id,
            lease_id=submission.lease_id,
            idempotency_key=submission.idempotency_key,
            status=submission.status,
            exit_code=submission.exit_code,
            runtime_seconds=submission.runtime_seconds,
            stdout_summary=submission.stdout_summary,
            stderr_summary=submission.stderr_summary,
            error_category=submission.error_category,
            result_payload=submission.result_payload,
            deliverable_ids=submission.deliverable_ids,
            artifacts=submission.artifacts,
        )
        return result
    except worker_svc.TaskAuthorizationError as tae:
        raise HTTPException(status_code=tae.status_code, detail=tae.message)
    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(pe))
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))


@router.post("/tasks/{task_id}/artifacts")
async def upload_task_artifact(
    task_id: str,
    request: Request,
    worker: Dict[str, Any] = Depends(get_authenticated_worker),
):
    """
    Upload a physical artifact produced by a worker task.
    Enforces F-10 & Task 8C.3:
    - Authoritative task assignment and lease fencing validation
    - Support for both streaming multipart/form-data and Base64 JSON
    - Incremental 25MB payload size enforcement (fails closed with 413 Payload Too Large)
    - Independent controller-side SHA256 checksum computation
    - Strict verification against expected worker checksum (fails closed with 400 Bad Request on mismatch)
    - Path traversal sanitization and non-overwriting storage
    """
    content_type = request.headers.get("content-type", "").lower()
    
    filename = None
    expected_sha256 = None
    lease_id = None
    content_bytes = None
    
    if "multipart/form-data" in content_type:
        try:
            form = await request.form()
        except PayloadTooLargeError:
            raise
        except Exception as ex:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid multipart form data: {ex}")
            
        file_obj = form.get("file")
        if not file_obj:
            for val in form.values():
                if hasattr(val, "read"):
                    file_obj = val
                    break
        if not file_obj:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing required 'file' field in multipart upload.")
        
        filename = form.get("filename") or getattr(file_obj, "filename", None) or "artifact.bin"
        expected_sha256 = form.get("expected_sha256")
        lease_id = form.get("lease_id")
        
        total_multipart_payload = 0
        content_bytes = None
        for val in form.values():
            if hasattr(val, "read"):
                try:
                    part_bytes = await val.read()
                    total_multipart_payload += len(part_bytes)
                    if val is file_obj or content_bytes is None:
                        content_bytes = part_bytes
                finally:
                    await val.close()
                    
        if total_multipart_payload > worker_svc.MAX_ARTIFACT_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"Artifact exceeds maximum allowed size of {worker_svc.MAX_ARTIFACT_SIZE} bytes across multipart file parts."
            )
    elif "application/octet-stream" in content_type:
        filename = request.headers.get("x-artifact-filename") or "artifact.bin"
        expected_sha256 = request.headers.get("x-expected-sha256")
        lease_id = request.headers.get("x-lease-id")
        content_bytes = await request.body()
    else:
        # Default / application/json
        try:
            body = await request.json()
            req = ArtifactUploadRequest(**body)
            filename = req.filename
            expected_sha256 = req.expected_sha256
            lease_id = req.lease_id
            content_bytes = base64.b64decode(req.content_base64, validate=True)
        except (PayloadTooLargeError, HTTPException):
            raise
        except ValueError as ve:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
        except Exception as ex:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid base64 encoding or JSON payload: {ex}")

    # Enforce exact 25MB raw artifact payload limit (Task 8C.3)
    if len(content_bytes) > worker_svc.MAX_ARTIFACT_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Artifact exceeds maximum allowed size of {worker_svc.MAX_ARTIFACT_SIZE} bytes."
        )

    # Enforce task authorization and active lease fencing (F-10)
    try:
        worker_svc.verify_worker_task_authorization(
            worker_id=worker["node_id"],
            task_id=task_id,
            operation="upload",
            lease_id=lease_id,
        )
    except worker_svc.TaskAuthorizationError as tae:
        raise HTTPException(status_code=tae.status_code, detail=tae.message)

    try:
        stored = worker_svc.store_task_artifact(
            task_id=task_id,
            filename=filename,
            content_bytes=content_bytes,
            expected_sha256=expected_sha256,
            worker_id=worker["node_id"],
        )
        return {"status": "stored", "artifact": stored}
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as ex:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(ex))


@router.get("/tasks/{task_id}/artifacts/{artifact_id}")
def download_task_artifact(
    task_id: str,
    artifact_id: str,
    version: Optional[int] = None,
    worker: Dict[str, Any] = Depends(get_authenticated_worker),
):
    """
    Allow an authenticated worker to materialize an authorized input/project artifact for its assigned task.
    Enforces least privilege: worker must be assigned/leased to task_id, and artifact must belong or be granted to the task's project.
    """
    from fastapi.responses import FileResponse
    from app.services.artifacts import artifact_service

    try:
        auth_context = worker_svc.verify_worker_task_authorization(
            worker_id=worker["node_id"],
            task_id=task_id,
            operation="artifact_download",
        )
    except worker_svc.TaskAuthorizationError as tae:
        raise HTTPException(status_code=tae.status_code, detail=tae.message)

    proj_id = auth_context["project_id"]
    if not artifact_service.check_project_access(artifact_id, proj_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Task project '{proj_id}' is not authorized to access artifact '{artifact_id}'.",
        )

    try:
        dl = artifact_service.resolve_download(artifact_id, version_number=version, project_id=proj_id)
        return FileResponse(
            path=str(dl["file_path"]),
            media_type=dl["mime_type"],
            filename=dl["display_name"],
            headers={
                "X-Artifact-Id": dl["artifact_id"],
                "X-Artifact-Version": str(dl["version_number"]),
                "X-Artifact-Checksum-SHA256": dl["checksum_sha256"],
            },
        )
    except Exception as ex:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(ex))


@router.get("/executions")
def list_executions(
    worker_id: Optional[str] = None,
    limit: int = 50,
    worker: Dict[str, Any] = Depends(get_authenticated_worker),
):
    """List recent executions from the immutable execution ledger."""
    # Worker may only inspect its own executions
    if worker_id and worker_id != worker["node_id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workers are forbidden from accessing execution records of other nodes.",
        )
    target_worker = worker["node_id"]
    return worker_svc.list_worker_executions(worker_id=target_worker, limit=limit)
