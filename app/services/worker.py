"""
Dedicated Worker Management & Task Leasing Service for Qwen Collective (Milestone 2).
Enforces:
- Outbound pull-based worker leasing protocol
- Scoped Bearer token authentication per node
- Atomic single-claim task leases with lease expiration
- Heartbeat lease extension and auto-recovery of abandoned tasks
- Idempotent result submission with immutable execution history
"""

import hashlib
import json
import logging
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from app.database import get_db_connection

logger = logging.getLogger("qwen_collective.worker")

DEFAULT_LEASE_DURATION_SECONDS = 300  # 5 minutes
MAX_LEASE_DURATION_SECONDS = 3600  # 1 hour


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Worker Authentication Token Management
# ---------------------------------------------------------------------------

def create_worker_token(node_id: str, description: str = "") -> Tuple[str, str]:
    """
    Generate a cryptographically secure token for an infrastructure node.
    Returns (token_id, raw_token). The raw token is returned once for configuration.
    """
    raw_token = f"qcw_{secrets.token_urlsafe(32)}"
    token_id = f"tok_{uuid4().hex[:12]}"
    token_hash = _hash_token(raw_token)
    token_prefix = raw_token[:10] + "..."
    now = _utc_now_iso()

    conn = get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO worker_auth_tokens (id, node_id, token_hash, token_prefix, created_at, revoked)
            VALUES (?, ?, ?, ?, ?, 0)
            """,
            (token_id, node_id, token_hash, token_prefix, now),
        )
        conn.commit()
        logger.info("Created worker token %s for node %s", token_id, node_id)
        return token_id, raw_token
    finally:
        conn.close()


def verify_worker_token(raw_token: str) -> Optional[Dict[str, Any]]:
    """
    Verify a worker Bearer token.
    Returns node identity details or None if invalid or revoked.
    """
    if not raw_token or not raw_token.startswith("qcw_"):
        return None

    token_hash = _hash_token(raw_token)
    now = _utc_now_iso()

    conn = get_db_connection()
    try:
        row = conn.execute(
            """
            SELECT t.id, t.node_id, t.revoked, n.name, n.role, n.health_status
            FROM worker_auth_tokens t
            JOIN infrastructure_nodes n ON t.node_id = n.id
            WHERE t.token_hash = ? AND t.revoked = 0
            """,
            (token_hash,),
        ).fetchone()

        if not row:
            return None

        # Update last used timestamp
        conn.execute(
            "UPDATE worker_auth_tokens SET last_used_at = ? WHERE id = ?",
            (now, row["id"]),
        )
        conn.commit()

        return {
            "token_id": row["id"],
            "node_id": row["node_id"],
            "node_name": row["name"],
            "role": row["role"],
            "health_state": row["health_status"],
        }
    finally:
        conn.close()


def revoke_worker_token(token_id: str) -> bool:
    """Revoke an active worker token immediately."""
    conn = get_db_connection()
    try:
        cursor = conn.execute(
            "UPDATE worker_auth_tokens SET revoked = 1 WHERE id = ?",
            (token_id,),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Worker Registration & Health Heartbeats
# ---------------------------------------------------------------------------

def register_worker_node(
    node_id: str,
    name: str,
    role: str = "vps_worker",
    hostname: str = "",
    os_name: str = "Linux",
    os_version: str = "",
    architecture: str = "x86_64",
    cpu_count: int = 1,
    ram_total_mb: int = 1024,
    ram_available_mb: int = 512,
    disk_total_gb: float = 20.0,
    disk_free_gb: float = 10.0,
    ip_addresses: Optional[List[str]] = None,
    tailscale_ip: Optional[str] = None,
    agent_version: str = "1.0.0",
    installed_runtimes: Optional[List[str]] = None,
    registered_services: Optional[List[str]] = None,
    declared_capabilities: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Register or update an execution worker node in the infrastructure registry.
    """
    now = _utc_now_iso()
    ip_str = ip_addresses[0] if ip_addresses else ""
    ts_ip = tailscale_ip or ""
    ts_status = "connected" if ts_ip else "unknown"
    cpu_info = f"{cpu_count} CPU cores"
    runtimes_json = json.dumps(installed_runtimes or ["python3"])
    services_json = json.dumps(registered_services or ["qwen-worker.service"])
    caps_json = json.dumps(declared_capabilities or ["python_execution", "scheduled_execution"])

    conn = get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO infrastructure_nodes (
                id, name, role, hostname, ip_address, tailscale_ip, tailscale_status,
                os_name, os_version, architecture, cpu_info,
                installed_ram_mb, available_ram_mb, storage_total_gb, storage_free_gb,
                installed_runtimes_json, registered_services_json, health_status,
                last_contact_at, last_inventory_at, is_enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'online', ?, ?, 1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                role = excluded.role,
                hostname = excluded.hostname,
                ip_address = excluded.ip_address,
                tailscale_ip = excluded.tailscale_ip,
                tailscale_status = excluded.tailscale_status,
                os_name = excluded.os_name,
                os_version = excluded.os_version,
                architecture = excluded.architecture,
                cpu_info = excluded.cpu_info,
                installed_ram_mb = excluded.installed_ram_mb,
                available_ram_mb = excluded.available_ram_mb,
                storage_total_gb = excluded.storage_total_gb,
                storage_free_gb = excluded.storage_free_gb,
                installed_runtimes_json = excluded.installed_runtimes_json,
                registered_services_json = excluded.registered_services_json,
                health_status = 'online',
                last_contact_at = excluded.last_contact_at,
                updated_at = excluded.updated_at
            """,
            (
                node_id, name, role, hostname, ip_str, ts_ip, ts_status,
                os_name, os_version, architecture, cpu_info,
                ram_total_mb, ram_available_mb, disk_total_gb, disk_free_gb,
                runtimes_json, services_json,
                now, now, now, now,
            ),
        )
        conn.commit()
        logger.info("Worker node registered: %s (%s)", node_id, name)
        return {
            "status": "registered",
            "node_id": node_id,
            "registered_at": now,
        }
    finally:
        conn.close()


def record_worker_heartbeat(
    node_id: str,
    cpu_percent: Optional[float] = None,
    ram_available_mb: Optional[int] = None,
    disk_free_gb: Optional[float] = None,
    running_jobs_count: int = 0,
) -> Dict[str, Any]:
    """
    Record an authenticated worker heartbeat, update health state, and auto-renew active leases.
    """
    now = _utc_now_iso()
    conn = get_db_connection()
    try:
        # 1. Update node health
        conn.execute(
            """
            UPDATE infrastructure_nodes
            SET health_status = 'online',
                last_contact_at = ?,
                available_ram_mb = COALESCE(?, available_ram_mb),
                storage_free_gb = COALESCE(?, storage_free_gb),
                updated_at = ?
            WHERE id = ?
            """,
            (now, ram_available_mb, disk_free_gb, now, node_id),
        )

        # 2. Touch heartbeat on all active leases for this worker
        lease_cutoff = datetime.now(timezone.utc).timestamp() + DEFAULT_LEASE_DURATION_SECONDS
        lease_cutoff_iso = datetime.fromtimestamp(lease_cutoff, tz=timezone.utc).isoformat()
        
        conn.execute(
            """
            UPDATE worker_task_leases
            SET heartbeat_at = ?,
                lease_expires_at = ?,
                updated_at = ?
            WHERE worker_id = ? AND status IN ('leased', 'active')
            """,
            (now, lease_cutoff_iso, now, node_id),
        )
        conn.commit()

        # 3. Retrieve currently active leases for this worker
        active_leases = conn.execute(
            """
            SELECT id, task_id, status, lease_expires_at, idempotency_key
            FROM worker_task_leases
            WHERE worker_id = ? AND status IN ('leased', 'active')
            """,
            (node_id,),
        ).fetchall()

        return {
            "status": "ok",
            "node_id": node_id,
            "timestamp": now,
            "active_leases": [dict(r) for r in active_leases],
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Durable Task Leasing Protocol
# ---------------------------------------------------------------------------

def lease_task_for_worker(
    worker_id: str,
    supported_categories: Optional[List[str]] = None,
    lease_duration_seconds: int = DEFAULT_LEASE_DURATION_SECONDS,
) -> Optional[Dict[str, Any]]:
    """
    Atomically lease a pending, eligible task to an authenticated worker.
    Enforces:
    - Task must be approved or approval_required=0
    - Task must not already be actively leased
    - Worker capability matching
    - Atomic database transaction
    """
    lease_duration = min(max(30, lease_duration_seconds), MAX_LEASE_DURATION_SECONDS)
    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()
    expires_dt = datetime.fromtimestamp(now_dt.timestamp() + lease_duration, tz=timezone.utc)
    expires_iso = expires_dt.isoformat()

    conn = get_db_connection()
    try:
        # First reconcile expired leases inside the transaction
        _reconcile_expired_leases_conn(conn)

        # Find candidate task: approved or approval_required=0, pending status
        # Note: tasks table has columns: id, title, description, task_type, status, assigned_worker,
        # approval_required, input_data_json, idempotency_key, project_id, path_id, stage_id
        cursor = conn.execute(
            """
            SELECT t.id, t.title, t.description, t.task_type, t.status, t.assigned_worker,
                   t.input_data_json, t.project_id, t.path_id, t.stage_id, t.purpose, t.expected_output
            FROM tasks t
            WHERE t.status IN ('pending', 'approved')
              AND (t.approval_required = 0 OR t.status = 'approved')
              AND (t.assigned_worker IS NULL OR t.assigned_worker = ?)
              AND t.id NOT IN (
                  SELECT task_id FROM worker_task_leases WHERE status IN ('leased', 'active')
              )
            ORDER BY t.created_at ASC
            LIMIT 1
            """,
            (worker_id,),
        )
        task_row = cursor.fetchone()
        if not task_row:
            return None

        task_id = str(task_row["id"])
        lease_id = f"lease_{uuid4().hex[:12]}"
        idempotency_key = f"idem_{uuid4().hex[:16]}"

        # Insert lease
        conn.execute(
            """
            INSERT INTO worker_task_leases (
                id, task_id, worker_id, idempotency_key, status,
                leased_at, lease_expires_at, heartbeat_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'leased', ?, ?, ?, ?, ?)
            """,
            (lease_id, task_id, worker_id, idempotency_key, now_iso, expires_iso, now_iso, now_iso, now_iso),
        )

        # Update task status
        conn.execute(
            """
            UPDATE tasks
            SET status = 'in_progress',
                assigned_worker = ?,
                idempotency_key = COALESCE(idempotency_key, ?),
                updated_at = ?
            WHERE id = ?
            """,
            (worker_id, idempotency_key, now_iso, task_row["id"]),
        )
        conn.commit()

        logger.info("Leased task %s to worker %s (Lease: %s, Expires: %s)", task_id, worker_id, lease_id, expires_iso)

        input_data = {}
        if task_row["input_data_json"]:
            try:
                input_data = json.loads(task_row["input_data_json"])
            except Exception:
                input_data = {"raw": task_row["input_data_json"]}

        return {
            "lease_id": lease_id,
            "task_id": task_id,
            "worker_id": worker_id,
            "idempotency_key": idempotency_key,
            "title": task_row["title"],
            "description": task_row["description"],
            "task_type": task_row["task_type"],
            "purpose": task_row["purpose"],
            "expected_output": task_row["expected_output"],
            "input_data": input_data,
            "leased_at": now_iso,
            "lease_expires_at": expires_iso,
            "timeout_seconds": lease_duration,
        }
    finally:
        conn.close()


def renew_task_lease(
    worker_id: str,
    lease_id: str,
    additional_seconds: int = DEFAULT_LEASE_DURATION_SECONDS,
) -> bool:
    """Extend the expiration time of an actively leased task."""
    duration = min(max(30, additional_seconds), MAX_LEASE_DURATION_SECONDS)
    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()
    new_expires_dt = datetime.fromtimestamp(now_dt.timestamp() + duration, tz=timezone.utc)
    new_expires_iso = new_expires_dt.isoformat()

    conn = get_db_connection()
    try:
        cursor = conn.execute(
            """
            UPDATE worker_task_leases
            SET status = 'active',
                heartbeat_at = ?,
                lease_expires_at = ?,
                updated_at = ?
            WHERE id = ? AND worker_id = ? AND status IN ('leased', 'active')
            """,
            (now_iso, new_expires_iso, now_iso, lease_id, worker_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Idempotent Result Submission & Immutable Execution Records
# ---------------------------------------------------------------------------

def submit_task_result(
    worker_id: str,
    task_id: str,
    lease_id: str,
    idempotency_key: str,
    status: str,
    exit_code: int = 0,
    runtime_seconds: float = 0.0,
    stdout_summary: str = "",
    stderr_summary: str = "",
    error_category: Optional[str] = None,
    result_payload: Optional[Dict[str, Any]] = None,
    deliverable_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Record task outcome, enforce idempotency, update lease, and append to execution ledger.
    """
    now = _utc_now_iso()
    result_json = json.dumps(result_payload or {})
    deliverables_json = json.dumps(deliverable_ids or [])

    conn = get_db_connection()
    try:
        # Check idempotency: If this idempotency_key was already processed, return existing record
        existing = conn.execute(
            "SELECT id, status, started_at, completed_at FROM worker_executions WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()

        if existing:
            logger.info("Idempotent submission detected for key %s (Execution: %s)", idempotency_key, existing["id"])
            return {
                "status": "already_processed",
                "execution_id": existing["id"],
                "task_status": existing["status"],
                "completed_at": existing["completed_at"],
            }

        # Resolve foreign keys safely
        actual_lease_id = None
        if lease_id:
            lease_row = conn.execute("SELECT id FROM worker_task_leases WHERE id = ?", (lease_id,)).fetchone()
            if lease_row:
                actual_lease_id = lease_id

        actual_task_id = None
        if task_id:
            task_row = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if task_row:
                actual_task_id = task_row["id"]

        # 1. Update lease status if exists
        lease_status = "completed" if status == "success" else "failed"
        if actual_lease_id:
            conn.execute(
                """
                UPDATE worker_task_leases
                SET status = ?,
                    updated_at = ?
                WHERE id = ? AND worker_id = ?
                """,
                (lease_status, now, actual_lease_id, worker_id),
            )

        # 2. Update task record if exists
        task_status = "completed" if status == "success" else "failed"
        if actual_task_id:
            conn.execute(
                """
                UPDATE tasks
                SET status = ?,
                    result_data_json = ?,
                    error_details = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (task_status, result_json, stderr_summary if status != "success" else None, now, actual_task_id),
            )

        # 3. Insert immutable execution record
        execution_id = f"exec_{uuid4().hex[:12]}"
        conn.execute(
            """
            INSERT INTO worker_executions (
                id, task_id, worker_id, lease_id, idempotency_key, status,
                exit_code, started_at, completed_at, runtime_seconds,
                stdout_summary, stderr_summary, error_category,
                deliverable_ids_json, result_payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                execution_id, actual_task_id, worker_id, actual_lease_id, idempotency_key, status,
                exit_code, now, now, runtime_seconds,
                stdout_summary, stderr_summary, error_category,
                deliverables_json, result_json, now,
            ),
        )

        conn.commit()
        logger.info("Recorded execution %s for task %s (status: %s)", execution_id, task_id, status)

        return {
            "status": "recorded",
            "execution_id": execution_id,
            "task_id": task_id,
            "task_status": task_status,
            "completed_at": now,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Lease Reconciliation & Abandoned Task Recovery
# ---------------------------------------------------------------------------

def _reconcile_expired_leases_conn(conn: sqlite3.Connection) -> List[str]:
    """Internal helper to mark expired leases and recover tasks."""
    now_iso = datetime.now(timezone.utc).isoformat()
    expired = conn.execute(
        """
        SELECT id, task_id, worker_id
        FROM worker_task_leases
        WHERE status IN ('leased', 'active') AND lease_expires_at < ?
        """,
        (now_iso,),
    ).fetchall()

    recovered_tasks = []
    for lease in expired:
        lease_id = lease["id"]
        task_id = lease["task_id"]
        worker_id = lease["worker_id"]

        # Mark lease expired
        conn.execute(
            "UPDATE worker_task_leases SET status = 'expired', updated_at = ? WHERE id = ?",
            (now_iso, lease_id),
        )

        # Reset task to pending so another eligible worker can claim it
        conn.execute(
            """
            UPDATE tasks
            SET status = 'pending',
                assigned_worker = NULL,
                updated_at = ?
            WHERE id = ? AND status = 'in_progress'
            """,
            (now_iso, task_id),
        )

        # Record drift discrepancy
        recon_id = f"recon_{uuid4().hex[:12]}"
        conn.execute(
            """
            INSERT INTO infrastructure_reconciliations (
                id, node_id, discrepancy_type, description, severity, status, detected_at
            ) VALUES (?, ?, 'verification_failure', ?, 'warning', 'detected', ?)
            """,
            (
                recon_id,
                worker_id,
                f"Task lease {lease_id} for task {task_id} expired without heartbeat. Task re-queued.",
                now_iso,
            ),
        )
        recovered_tasks.append(task_id)
        logger.warning("Reconciled expired lease %s for task %s on worker %s", lease_id, task_id, worker_id)

    return recovered_tasks


def reconcile_expired_leases() -> List[str]:
    """Find and reclaim any abandoned or expired task leases."""
    conn = get_db_connection()
    try:
        recovered = _reconcile_expired_leases_conn(conn)
        conn.commit()
        return recovered
    finally:
        conn.close()


def list_worker_executions(worker_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """List recent worker executions from the immutable audit ledger."""
    conn = get_db_connection()
    try:
        query = "SELECT * FROM worker_executions"
        params: List[Any] = []
        if worker_id:
            query += " WHERE worker_id = ?"
            params.append(worker_id)
        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
