"""
Worker API Endpoints for Qwen Collective (Milestone 2).
Enforces:
- Bearer token authentication per node
- Pull-based task leasing protocol
- Heartbeats and lease extensions
- Idempotent result submissions
- Append-only execution history
"""

import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.services import worker as worker_svc

logger = logging.getLogger("qwen_collective.worker_api")

router = APIRouter(prefix="/api/v1/worker", tags=["worker"])


# ---------------------------------------------------------------------------
# Request & Response Schemas
# ---------------------------------------------------------------------------

class WorkerRegistrationRequest(BaseModel):
    node_id: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=200)
    role: str = Field(default="vps_worker")
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


class WorkerHeartbeatRequest(BaseModel):
    node_id: str
    cpu_percent: Optional[float] = None
    ram_available_mb: Optional[int] = None
    disk_free_gb: Optional[float] = None
    running_jobs_count: int = 0


class TaskLeaseRequest(BaseModel):
    worker_id: str
    supported_categories: List[str] = Field(default_factory=list)
    lease_duration_seconds: int = Field(default=300, ge=30, le=3600)


class TaskResultSubmission(BaseModel):
    task_id: str
    lease_id: str
    idempotency_key: str
    status: str = Field(..., pattern="^(success|error|timeout|cancelled)$")
    exit_code: int = 0
    runtime_seconds: float = 0.0
    stdout_summary: str = ""
    stderr_summary: str = ""
    error_category: Optional[str] = None
    result_payload: Dict[str, Any] = Field(default_factory=dict)
    deliverable_ids: List[str] = Field(default_factory=list)


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
# Worker Endpoints
# ---------------------------------------------------------------------------

@router.post("/register")
def register_worker(
    req: WorkerRegistrationRequest,
    authorization: Optional[str] = Header(None),
    x_worker_token: Optional[str] = Header(None),
):
    """
    Register or update an execution worker node.
    If already registered with a valid token, authenticates it; otherwise provisions or updates node.
    """
    # 1. Update node in infrastructure registry
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
    )

    # 2. Issue a worker token if one does not exist for this node yet
    raw_token = None
    if authorization and authorization.lower().startswith("bearer "):
        raw_token = authorization[7:].strip()
    elif x_worker_token:
        raw_token = x_worker_token.strip()

    token_info = worker_svc.verify_worker_token(raw_token) if raw_token else None
    if not token_info:
        token_id, new_raw_token = worker_svc.create_worker_token(req.node_id)
        reg_result["token_id"] = token_id
        reg_result["worker_token"] = new_raw_token
        reg_result["token_status"] = "provisioned"
    else:
        reg_result["token_id"] = token_info["token_id"]
        reg_result["token_status"] = "authenticated"

    return reg_result


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

    return worker_svc.record_worker_heartbeat(
        node_id=req.node_id,
        cpu_percent=req.cpu_percent,
        ram_available_mb=req.ram_available_mb,
        disk_free_gb=req.disk_free_gb,
        running_jobs_count=req.running_jobs_count,
    )


@router.post("/tasks/lease")
def lease_task(
    req: TaskLeaseRequest,
    worker: Dict[str, Any] = Depends(get_authenticated_worker),
):
    """
    Request eligible task lease matching worker's verified capabilities.
    """
    if worker["node_id"] != req.worker_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token node mismatch.",
        )

    task = worker_svc.lease_task_for_worker(
        worker_id=req.worker_id,
        supported_categories=req.supported_categories,
        lease_duration_seconds=req.lease_duration_seconds,
    )

    if not task:
        return {"status": "no_work", "message": "No eligible approved tasks available."}

    return {"status": "leased", "task": task}


@router.post("/tasks/{lease_id}/heartbeat")
def extend_task_lease(
    lease_id: str,
    additional_seconds: int = 300,
    worker: Dict[str, Any] = Depends(get_authenticated_worker),
):
    """
    Renew active task lease heartbeat to prevent abandonment timeout.
    """
    success = worker_svc.renew_task_lease(
        worker_id=worker["node_id"],
        lease_id=lease_id,
        additional_seconds=additional_seconds,
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active lease not found or already expired/completed.",
        )
    return {"status": "renewed", "lease_id": lease_id}


@router.post("/tasks/{task_id}/submit")
def submit_task_result(
    task_id: str,
    submission: TaskResultSubmission,
    worker: Dict[str, Any] = Depends(get_authenticated_worker),
):
    """
    Submit completed task results with idempotency guard.
    """
    if submission.task_id != task_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Task ID in path does not match payload.",
        )

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
    )

    return result


@router.get("/executions")
def list_executions(
    worker_id: Optional[str] = None,
    limit: int = 50,
    worker: Dict[str, Any] = Depends(get_authenticated_worker),
):
    """List recent executions from the immutable execution ledger."""
    # Allow workers to inspect their own executions, or any if admin
    target_worker = worker_id or worker["node_id"]
    return worker_svc.list_worker_executions(worker_id=target_worker, limit=limit)
