"""
Dedicated Worker Management & Task Leasing Service for Qwen Collective (Milestone 2).
Enforces:
- Outbound pull-based worker leasing protocol
- Scoped Bearer token authentication per node
- Atomic single-claim task leases with lease expiration
- Heartbeat lease extension and auto-recovery of abandoned tasks
- Idempotent result submission with immutable execution history
"""

import base64
import hashlib
import hmac
import json
import logging
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from app.config import settings
from app.database import get_db_connection
from app.services import loop_detection

logger = logging.getLogger("qwen_collective.worker")

DEFAULT_LEASE_DURATION_SECONDS = 300  # 5 minutes
MAX_LEASE_DURATION_SECONDS = 3600  # 1 hour
MAX_ARTIFACT_SIZE = 25 * 1024 * 1024  # 25 MB (26,214,400 bytes)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class TaskAuthorizationError(Exception):
    """Raised when a worker task operation violates security, ownership, or lease fencing."""
    def __init__(self, message: str, status_code: int = 403):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def sanitize_display_filename(raw_name: str) -> str:
    """
    Sanitize untrusted filename from worker to prevent path traversal,
    Windows device collisions, control characters, and Unicode path abuse.
    """
    if not raw_name or not isinstance(raw_name, str):
        return f"artifact_{uuid4().hex[:8]}.bin"

    # Normalize slashes and take basename
    clean = raw_name.replace("\\", "/").strip().split("/")[-1].strip()

    # Strip null bytes and control chars (ASCII < 32 or 127)
    clean = "".join(c for c in clean if ord(c) >= 32 and ord(c) != 127)

    # Replace forbidden path characters
    for bad_char in ('<', '>', ':', '"', '/', '\\', '|', '?', '*'):
        clean = clean.replace(bad_char, '_')

    # Strip leading/trailing dots and spaces
    clean = clean.strip(". ")

    # Check for empty or reserved directory references
    if not clean or clean in (".", ".."):
        return f"artifact_{uuid4().hex[:8]}.bin"

    # Windows reserved device names (CON, PRN, AUX, NUL, COM1..9, LPT1..9)
    stem = Path(clean).stem.upper()
    reserved_names = {
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    }
    if stem in reserved_names:
        clean = f"safe_{clean}"

    return clean[:255]


def verify_worker_task_authorization(
    worker_id: str,
    task_id: str,
    operation: str = "upload",
    lease_id: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """
    Authoritative single policy gate for worker task operations (F-10).
    Enforces:
    - Worker identity must be valid and active
    - Task must exist
    - Task must be assigned to worker_id (if assigned)
    - Worker must hold an active lease for upload operations (fenced against expiration)
    - Reconciled expired lease handling for submit operations
    - Rejection of cross-worker operations and conflicting active leases
    - Task state must be eligible for the requested operation
    """
    own_conn = conn is None
    c = get_db_connection() if own_conn else conn
    try:
        # 1. Verify task existence and assignment
        task_row = c.execute(
            "SELECT * FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if not task_row:
            raise TaskAuthorizationError(f"Task '{task_id}' not found.", status_code=404)
        task = dict(task_row)

        assigned_worker = task.get("assigned_worker")
        if assigned_worker and assigned_worker != worker_id:
            raise TaskAuthorizationError(
                f"Worker '{worker_id}' is not authorized for task '{task_id}' (assigned to '{assigned_worker}').",
                status_code=403,
            )

        # 2. Check for conflicting active lease held by another worker
        other_lease = c.execute(
            "SELECT worker_id, status FROM worker_task_leases WHERE task_id = ? AND status IN ('leased', 'active') AND worker_id != ?",
            (task_id, worker_id),
        ).fetchone()
        if other_lease:
            raise TaskAuthorizationError(
                f"Task '{task_id}' is actively leased to worker '{other_lease['worker_id']}', not '{worker_id}'.",
                status_code=403,
            )

        # 3. Check worker's own lease
        now_iso = _utc_now_iso()
        lease_query = """
            SELECT id, worker_id, task_id, status, lease_expires_at, attempt_count, created_at
            FROM worker_task_leases
            WHERE task_id = ? AND worker_id = ?
        """
        params = [task_id, worker_id]
        if lease_id:
            lease_query += " AND id = ?"
            params.append(lease_id)
        lease_query += " ORDER BY created_at DESC LIMIT 1"

        lease_row = c.execute(lease_query, tuple(params)).fetchone()
        lease = dict(lease_row) if lease_row else None

        # F-10: Worker cannot obtain execution authority solely because a task has no lease record.
        # Unassigned tasks strictly require a valid lease.
        if not lease:
            if assigned_worker is None:
                raise TaskAuthorizationError(
                    f"Task '{task_id}' is unassigned. Worker '{worker_id}' cannot obtain execution authority without a valid lease.",
                    status_code=403,
                )

            # Explicit legacy direct assignment rules:
            # 1. assigned_worker must explicitly match worker_id
            # 2. Uploads strictly require an active DB lease record (reject unleased uploads)
            # 3. Submit is permitted only if NO lease was ever created for this task
            if assigned_worker == worker_id:
                if operation == "upload":
                    raise TaskAuthorizationError(
                        f"Task '{task_id}' has no active lease record. Artifact uploads require an active task lease.",
                        status_code=409,
                    )
                any_leases = c.execute(
                    "SELECT count(*) FROM worker_task_leases WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0]
                if any_leases > 0:
                    raise TaskAuthorizationError(
                        f"Task '{task_id}' requires lease-based execution; no active lease found for worker '{worker_id}'.",
                        status_code=403,
                    )
                lease = {
                    "id": lease_id or f"direct_{worker_id}",
                    "worker_id": worker_id,
                    "task_id": task_id,
                    "status": "active",
                    "lease_expires_at": "9999-12-31T23:59:59Z",
                    "attempt_count": task.get("attempt_count") or 1,
                }
            else:
                raise TaskAuthorizationError(
                    f"No active lease found for worker '{worker_id}' on task '{task_id}'.",
                    status_code=403,
                )

        # F-10: Check fencing generation — reject stale lease if newer execution attempt exists
        if lease_row:
            cur_attempt = lease.get("attempt_count") or 1
            newer_lease = c.execute(
                "SELECT id, attempt_count, worker_id FROM worker_task_leases WHERE task_id = ? AND attempt_count > ?",
                (task_id, cur_attempt),
            ).fetchone()
            if newer_lease:
                raise TaskAuthorizationError(
                    f"Stale lease fencing generation (attempt {cur_attempt}). Task '{task_id}' has been superseded by attempt {newer_lease['attempt_count']}.",
                    status_code=409,
                )

        # Check lease status and expiration
        lease_status = lease["status"]
        lease_expires_at = lease.get("lease_expires_at") or "9999-12-31T23:59:59Z"
        is_expired = lease_status == "expired" or (
            lease_status in ("leased", "active") and lease_expires_at <= now_iso
        )

        if is_expired and lease_status in ("leased", "active") and not str(lease["id"]).startswith("direct_"):
            c.execute(
                "UPDATE worker_task_leases SET status = 'expired', updated_at = ? WHERE id = ?",
                (now_iso, lease["id"]),
            )
            if own_conn:
                c.commit()
            lease_status = "expired"
            lease["status"] = "expired"

        # 4. Operation-specific checks
        task_status = task.get("status")
        if operation == "upload":
            if is_expired or lease_status in ("expired", "cancelled", "failed"):
                raise TaskAuthorizationError(
                    f"Task lease '{lease['id']}' has expired or is invalid. Artifact upload rejected.",
                    status_code=409,
                )
            if task_status in ("completed", "cancelled", "failed"):
                raise TaskAuthorizationError(
                    f"Cannot upload artifacts to task '{task_id}' in terminal state '{task_status}'.",
                    status_code=409,
                )
        elif operation == "submit":
            if is_expired:
                # Expired lease submitting: verify task was not reacquired by another worker
                reacquired = c.execute(
                    "SELECT worker_id FROM worker_task_leases WHERE task_id = ? AND worker_id != ? AND status IN ('leased', 'active', 'completed')",
                    (task_id, worker_id),
                ).fetchone()
                if reacquired or (assigned_worker and assigned_worker != worker_id):
                    reassigned_to = reacquired['worker_id'] if reacquired else assigned_worker
                    raise TaskAuthorizationError(
                        f"Task '{task_id}' was reacquired by worker '{reassigned_to}' after lease expiration. Stale submission rejected.",
                        status_code=409,
                    )
            if task_status in ("cancelled", "failed"):
                raise TaskAuthorizationError(
                    f"Cannot submit results to task '{task_id}' in terminal state '{task_status}'.",
                    status_code=409,
                )

        return {
            "task": task,
            "lease": lease,
            "project_id": task.get("project_id"),
            "was_expired_lease": is_expired,
            "has_db_lease": lease_row is not None,
        }
    finally:
        if own_conn:
            c.close()


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
# Worker Enrollment Token Management (Security Enrollment Gate)
# ---------------------------------------------------------------------------

def create_enrollment_token(
    allowed_node_id: Optional[str] = None,
    expires_in_seconds: int = 86400,
    created_by: str = "admin",
) -> Tuple[str, str]:
    """
    Generate an administrator-issued, single-use, expiring enrollment token.
    Returns (token_id, raw_token). The raw token is returned once to provide to the worker.
    """
    raw_token = f"qcwe_{secrets.token_urlsafe(32)}"
    token_id = f"enr_{uuid4().hex[:12]}"
    token_hash = _hash_token(raw_token)
    token_prefix = raw_token[:10] + "..."
    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()
    expires_dt = datetime.fromtimestamp(now_dt.timestamp() + expires_in_seconds, tz=timezone.utc)
    expires_iso = expires_dt.isoformat()

    conn = get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO worker_enrollment_tokens (
                id, token_hash, token_prefix, allowed_node_id, created_by,
                created_at, expires_at, used_at, used_by_node_id, revoked
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, 0)
            """,
            (token_id, token_hash, token_prefix, allowed_node_id, created_by, now_iso, expires_iso),
        )
        conn.commit()
        logger.info("Created worker enrollment token %s (allowed_node_id: %s, expires: %s)", token_id, allowed_node_id, expires_iso)
        return token_id, raw_token
    finally:
        conn.close()


def verify_and_consume_enrollment_token(raw_token: str, node_id: str) -> Tuple[bool, str]:
    """
    Verify and atomically consume an enrollment token for node_id.
    Returns (success, reason_or_message).
    """
    if not raw_token or not raw_token.startswith("qcwe_"):
        return False, "Invalid enrollment token format"

    token_hash = _hash_token(raw_token)
    now_iso = _utc_now_iso()

    conn = get_db_connection()
    try:
        row = conn.execute(
            """
            SELECT id, allowed_node_id, expires_at, used_at, revoked
            FROM worker_enrollment_tokens
            WHERE token_hash = ?
            """,
            (token_hash,),
        ).fetchone()

        if not row:
            return False, "Enrollment token not found"
        if row["revoked"] == 1:
            return False, "Enrollment token has been revoked"
        if row["used_at"] is not None:
            return False, f"Enrollment token was already consumed at {row['used_at']}"
        if row["expires_at"] < now_iso:
            return False, f"Enrollment token expired at {row['expires_at']}"
        if row["allowed_node_id"] and row["allowed_node_id"] != node_id:
            return False, f"Enrollment token restricted to node '{row['allowed_node_id']}', not '{node_id}'"

        # Atomically consume the single-use token
        cursor = conn.execute(
            """
            UPDATE worker_enrollment_tokens
            SET used_at = ?, used_by_node_id = ?
            WHERE id = ? AND used_at IS NULL AND revoked = 0
            """,
            (now_iso, node_id, row["id"]),
        )
        conn.commit()
        if cursor.rowcount == 0:
            return False, "Enrollment token was concurrently consumed"

        logger.info("Successfully consumed enrollment token %s for node %s", row["id"], node_id)
        return True, "Enrollment token validated and consumed"
    finally:
        conn.close()


def revoke_enrollment_token(token_id: str) -> bool:
    """Revoke an active worker enrollment token."""
    conn = get_db_connection()
    try:
        cursor = conn.execute(
            "UPDATE worker_enrollment_tokens SET revoked = 1 WHERE id = ?",
            (token_id,),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def list_enrollment_tokens() -> List[Dict[str, Any]]:
    """List all enrollment tokens with security masking."""
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, token_prefix, allowed_node_id, created_by, created_at,
                   expires_at, used_at, used_by_node_id, revoked
            FROM worker_enrollment_tokens
            ORDER BY created_at DESC
            """
        ).fetchall()
        now_iso = _utc_now_iso()
        results = []
        for r in rows:
            is_valid = (r["revoked"] == 0 and r["used_at"] is None and r["expires_at"] > now_iso)
            results.append({
                "id": r["id"],
                "token_prefix": r["token_prefix"],
                "allowed_node_id": r["allowed_node_id"],
                "created_by": r["created_by"],
                "created_at": r["created_at"],
                "expires_at": r["expires_at"],
                "used_at": r["used_at"],
                "used_by_node_id": r["used_by_node_id"],
                "revoked": bool(r["revoked"]),
                "is_valid": is_valid,
            })
        return results
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
    available_models: Optional[List[str]] = None,
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
    models_json = json.dumps(available_models or [])

    conn = get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO infrastructure_nodes (
                id, name, role, hostname, ip_address, tailscale_ip, tailscale_status,
                os_name, os_version, architecture, cpu_info,
                installed_ram_mb, available_ram_mb, storage_total_gb, storage_free_gb,
                installed_runtimes_json, registered_services_json, available_models_json, health_status,
                last_contact_at, last_inventory_at, is_enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'online', ?, ?, 1, ?, ?)
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
                available_models_json = excluded.available_models_json,
                health_status = 'online',
                last_contact_at = excluded.last_contact_at,
                updated_at = excluded.updated_at
            """,
            (
                node_id, name, role, hostname, ip_str, ts_ip, ts_status,
                os_name, os_version, architecture, cpu_info,
                ram_total_mb, ram_available_mb, disk_total_gb, disk_free_gb,
                runtimes_json, services_json, models_json,
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
    available_models: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Record an authenticated worker heartbeat, update health state, and auto-renew active leases.
    """
    now = _utc_now_iso()
    conn = get_db_connection()
    try:
        # 1. Update node health
        if available_models is not None:
            models_json = json.dumps(available_models)
            conn.execute(
                """
                UPDATE infrastructure_nodes
                SET health_status = 'online',
                    last_contact_at = ?,
                    available_ram_mb = COALESCE(?, available_ram_mb),
                    storage_free_gb = COALESCE(?, storage_free_gb),
                    available_models_json = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, ram_available_mb, disk_free_gb, models_json, now, node_id),
            )
        else:
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

        # 3. Record sample in telemetry_samples
        try:
            from app.services.observatory import storage as obs_storage
            node_row = conn.execute("SELECT installed_ram_mb, storage_total_gb FROM infrastructure_nodes WHERE id = ?", (node_id,)).fetchone()
            ram_total = node_row["installed_ram_mb"] if node_row and node_row["installed_ram_mb"] else (ram_available_mb or 1024)
            ram_used = max(0, ram_total - ram_available_mb) if ram_available_mb is not None else None
            ram_pct = round((ram_used / ram_total) * 100.0, 1) if (ram_used is not None and ram_total > 0) else None
            disk_total = node_row["storage_total_gb"] if node_row and node_row["storage_total_gb"] else None
            disk_used = round(disk_total - disk_free_gb, 2) if (disk_total and disk_free_gb is not None) else None

            obs_storage.record_telemetry_sample(node_id, {
                "timestamp": now,
                "cpu_percent": cpu_percent,
                "ram_total_mb": ram_total,
                "ram_used_mb": ram_used,
                "ram_available_mb": ram_available_mb,
                "ram_percent": ram_pct,
                "disk_total_gb": disk_total,
                "disk_used_gb": disk_used,
                "disk_free_gb": disk_free_gb,
                "worker_service_status": "running",
            })
        except Exception as e:
            logger.debug("Failed to record heartbeat telemetry sample for %s: %s", node_id, e)

        # 4. Retrieve currently active leases for this worker
        active_leases = conn.execute(
            """
            SELECT id, task_id, status, lease_expires_at, idempotency_key
            FROM worker_task_leases
            WHERE worker_id = ? AND status IN ('leased', 'active')
            """,
            (node_id,),
        ).fetchall()

        conn.commit()
        return {
            "status": "ok",
            "node_id": node_id,
            "timestamp": now,
            "active_leases": [dict(r) for r in active_leases],
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Class B Execution Manifest & Artifact Verification
# ---------------------------------------------------------------------------

def create_execution_manifest(
    task_id: str,
    lease_id: str,
    worker_id: str,
    task_type: str,
    script_content: str,
    timeout_seconds: int = DEFAULT_LEASE_DURATION_SECONDS,
    allowed_capabilities: Optional[List[str]] = None,
    network_access: bool = False,
    secret_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generate an authenticated, tamper-evident execution manifest for Class B execution.
    Binds authorization to:
    - Target task_id, lease_id, and worker_id
    - Exact SHA256 content checksum of approved code
    - Strict execution limits and permissions
    - Controller HMAC-SHA256 signature
    """
    key = (secret_key or settings.secret_key).encode("utf-8")
    script_checksum = hashlib.sha256(script_content.encode("utf-8")).hexdigest()
    now_iso = _utc_now_iso()
    manifest: Dict[str, Any] = {
        "task_id": task_id,
        "lease_id": lease_id,
        "worker_id": worker_id,
        "task_type": task_type,
        "script_checksum": script_checksum,
        "max_runtime_seconds": timeout_seconds,
        "allowed_capabilities": allowed_capabilities or ["python_execution"],
        "network_access": network_access,
        "issued_at": now_iso,
    }
    canonical = json.dumps(manifest, sort_keys=True)
    manifest["signature"] = hmac.new(key, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    return manifest


def verify_execution_manifest(
    manifest: Dict[str, Any],
    script_content: str,
    expected_task_id: Optional[str] = None,
    expected_worker_id: Optional[str] = None,
    secret_key: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Independently verify the authenticity and integrity of an execution manifest.
    """
    if not isinstance(manifest, dict):
        return False, "Manifest is missing or not a dictionary."

    required_fields = ["task_id", "lease_id", "worker_id", "script_checksum", "signature"]
    for f in required_fields:
        if not manifest.get(f):
            return False, f"Manifest missing required field '{f}'."

    if expected_task_id and manifest["task_id"] != expected_task_id:
        return False, f"Manifest task_id '{manifest['task_id']}' does not match expected '{expected_task_id}'."

    if expected_worker_id and manifest["worker_id"] != expected_worker_id:
        return False, f"Manifest worker_id '{manifest['worker_id']}' does not match expected '{expected_worker_id}'."

    actual_checksum = hashlib.sha256(script_content.encode("utf-8")).hexdigest()
    if actual_checksum.lower() != str(manifest["script_checksum"]).lower():
        return False, f"Script checksum '{actual_checksum}' does not match manifest '{manifest['script_checksum']}'."

    key = (secret_key or settings.secret_key).encode("utf-8")
    expected_sig = manifest.get("signature", "")
    unsigned_copy = {k: v for k, v in manifest.items() if k != "signature"}
    canonical = json.dumps(unsigned_copy, sort_keys=True)
    computed_sig = hmac.new(key, canonical.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(str(expected_sig), computed_sig):
        return False, "Manifest HMAC signature verification failed."

    return True, "Manifest verified."


def store_task_artifact(
    task_id: str,
    filename: str,
    content_bytes: bytes,
    expected_sha256: Optional[str] = None,
    worker_id: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """
    Store a physical artifact in controlled evidence storage and compute independent SHA256 checksum.
    Enforces Stage 1 hardening:
    - Path traversal prevention and Windows reserved name sanitization
    - File size constraints (max 25MB)
    - Independent controller-side SHA256 hashing BEFORE disk write
    - Strict verification against expected worker checksum (fails closed with ValueError; 0 bytes persisted)
    - Non-overwriting immutable evidence storage
    """
    MAX_ARTIFACT_SIZE = 25 * 1024 * 1024  # 25 MB
    if len(content_bytes) > MAX_ARTIFACT_SIZE:
        raise ValueError(f"Artifact exceeds maximum allowed size of {MAX_ARTIFACT_SIZE} bytes.")

    # 1. Independently compute SHA256 over raw incoming bytes BEFORE any disk write
    computed_sha256 = hashlib.sha256(content_bytes).hexdigest()

    # 2. Strict checksum verification: fail closed if expected_sha256 is supplied and does not match
    if expected_sha256:
        clean_expected = expected_sha256.strip().lower()
        if not re.match(r"^[0-9a-fA-F]{64}$", clean_expected):
            raise ValueError(f"Invalid expected checksum format: '{expected_sha256}'. Must be a 64-character hexadecimal SHA-256 string.")
        if computed_sha256.lower() != clean_expected:
            logger.warning(
                "Artifact upload rejected for task %s: checksum mismatch (expected %s, computed %s)",
                task_id, clean_expected, computed_sha256
            )
            raise ValueError(f"Checksum mismatch: expected '{clean_expected}', computed '{computed_sha256}'. Upload rejected.")

    # 3. Sanitize display filename
    safe_name = sanitize_display_filename(filename)
    stem = Path(safe_name).stem
    suffix = Path(safe_name).suffix

    # 4. Resolve storage directory and enforce collision-proof atomic exclusive creation (O_CREAT | O_EXCL)
    evidence_base = settings.evidence_dir
    task_evidence_dir = evidence_base / f"task_{task_id}"
    task_evidence_dir.mkdir(parents=True, exist_ok=True)

    dest_file = task_evidence_dir / safe_name

    # Atomic exclusive creation: open with 'xb' to guarantee no concurrent overwrite
    try:
        with open(dest_file, "xb") as f:
            f.write(content_bytes)
            f.flush()
            os.fsync(f.fileno())
    except FileExistsError:
        artifact_uid = uuid4().hex[:12]
        unique_name = f"{stem}_{computed_sha256[:12]}_{artifact_uid}{suffix}"
        dest_file = task_evidence_dir / unique_name
        with open(dest_file, "xb") as f:
            f.write(content_bytes)
            f.flush()
            os.fsync(f.fileno())

    # 5. Post-write byte length and hash verification
    written_bytes = dest_file.read_bytes()
    if len(written_bytes) != len(content_bytes) or hashlib.sha256(written_bytes).hexdigest() != computed_sha256:
        try:
            dest_file.unlink(missing_ok=True)
        except Exception:
            pass
        raise IOError(f"Artifact post-write verification failed for {dest_file.name}. Stored file rolled back.")

    logger.info(
        "Stored artifact %s for task %s (%d bytes, sha256=%s, dest=%s)",
        safe_name, task_id, len(content_bytes), computed_sha256, dest_file.name
    )

    canonical_art_id = None
    try:
        from app.services.artifacts import artifact_service
        own_conn = conn is None
        c = get_db_connection() if own_conn else conn
        proj_id = None
        try:
            trow = c.execute("SELECT project_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if trow:
                proj_id = trow["project_id"]
        finally:
            if own_conn:
                c.close()

        canonical_art = artifact_service.register_artifact(
            name=safe_name,
            data=content_bytes,
            project_id=proj_id,
            description=f"Artifact uploaded from task {task_id} by worker {worker_id or 'unknown'}",
            task_id=task_id,
            worker_id=worker_id,
            created_by=worker_id or "worker",
            expected_sha256=expected_sha256,
            status="available",
            validation_status="valid",
            validation_detail="Checksum verified",
            db_conn=conn,
        )
        canonical_art_id = canonical_art["id"]
    except Exception as ex:
        logger.warning("Failed to register canonical artifact for task %s: %s", task_id, ex)

    return {
        "artifact_id": canonical_art_id,
        "task_id": task_id,
        "filename": safe_name,
        "stored_path": str(dest_file.resolve()),
        "file_size": len(content_bytes),
        "computed_sha256": computed_sha256,
        "expected_sha256": expected_sha256,
        "checksum_matches": True,
        "verified_at": _utc_now_iso(),
    }



# ---------------------------------------------------------------------------
# Durable Task Leasing Protocol
# ---------------------------------------------------------------------------

def lease_task_for_worker(
    worker_id: str,
    supported_categories: Optional[List[str]] = None,
    lease_duration_seconds: int = DEFAULT_LEASE_DURATION_SECONDS,
    available_models: Optional[List[str]] = None,
    available_ram_mb: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Atomically lease a pending, eligible task to an authenticated worker.
    Enforces:
    - Task must be approved or approval_required=0
    - Task must not already be actively leased
    - Worker capability matching
    - Resource-aware and model-aware constraints matching
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

        # Controller-side RAM fallback: do not trust a worker to self-report in lease polls.
        # Use the last RAM figure from an authenticated heartbeat/registration when absent.
        if available_ram_mb is None:
            try:
                _r = conn.execute(
                    "SELECT available_ram_mb FROM infrastructure_nodes WHERE id = ?", (worker_id,)
                ).fetchone()
                if _r and _r["available_ram_mb"] is not None:
                    available_ram_mb = int(_r["available_ram_mb"])
            except Exception:
                available_ram_mb = None

        # Find candidate tasks: approved or approval_required=0, pending status
        # Note: tasks table has columns: id, title, description, task_type, status, assigned_worker,
        # approval_required, input_data_json, idempotency_key, project_id, path_id, stage_id
        query = """
            SELECT t.id, t.title, t.description, t.task_type, t.status, t.assigned_worker,
                   t.approval_required, t.input_data_json, t.project_id, t.path_id, t.stage_id, t.purpose, t.expected_output
            FROM tasks t
            WHERE t.status IN ('queued', 'pending', 'approved')
              AND (t.approval_required = 0 OR t.status IN ('queued', 'approved'))
              AND (t.assigned_worker IS NULL OR t.assigned_worker = ?)
              AND t.id NOT IN (
                  SELECT task_id FROM worker_task_leases WHERE status IN ('leased', 'active')
              )
        """
        params: List[Any] = [worker_id]
        if supported_categories:
            placeholders = ",".join("?" for _ in supported_categories)
            query += f" AND t.task_type IN ({placeholders})"
            params.extend(supported_categories)

        query += " ORDER BY t.created_at ASC LIMIT 25"
        cursor = conn.execute(query, params)
        candidate_rows = cursor.fetchall()
        if not candidate_rows:
            return None

        # Resource-aware & capability-aware filtering
        task_row = None
        for cand in candidate_rows:
            input_data = {}
            if cand["input_data_json"]:
                try:
                    input_data = json.loads(cand["input_data_json"])
                except Exception:
                    input_data = {}

            # Check model constraint if specified by task
            req_model = input_data.get("required_model") or input_data.get("model")
            if req_model and available_models is not None:
                # Require model to be present in worker's available models
                if not any(req_model.lower() in m.lower() or m.lower() in req_model.lower() for m in available_models):
                    continue

            # Check RAM constraint if specified by task
            req_ram = input_data.get("min_ram_mb") or input_data.get("required_ram_mb")
            if req_ram:
                # Fail closed: unknown RAM never satisfies an explicit RAM requirement.
                try:
                    _need = int(req_ram)
                except (TypeError, ValueError):
                    continue
                if available_ram_mb is None or available_ram_mb < _need:
                    continue

            task_row = cand
            break

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

        # Class B security signing: if script is approved, generate immutable checksum and signed manifest
        script_content = (
            input_data.get("script")
            or input_data.get("python_script")
            or input_data.get("code")
        )
        manifest = None
        if script_content and isinstance(script_content, str):
            is_approved = bool(
                task_row["status"] in ("queued", "approved")
                or task_row["approval_required"] == 0
                or input_data.get("is_approved")
                or input_data.get("approved")
                or input_data.get("approved_script_id")
            )
            if is_approved:
                input_data["is_approved"] = True
                input_data["approved"] = True
                script_checksum = hashlib.sha256(script_content.encode("utf-8")).hexdigest()
                input_data["script_checksum"] = script_checksum
                manifest = create_execution_manifest(
                    task_id=task_id,
                    lease_id=lease_id,
                    worker_id=worker_id,
                    task_type=task_row["task_type"],
                    script_content=script_content,
                    timeout_seconds=lease_duration,
                    allowed_capabilities=["python_execution"],
                    network_access=bool(input_data.get("network_access", False)),
                )
                input_data["execution_manifest"] = manifest

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
            "execution_manifest": manifest,
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
    tool_name: Optional[str] = None,
    tool_args: Optional[Dict[str, Any]] = None,
    result_summary: Optional[Any] = None,
    error_category: Optional[str] = None,
    progress_milestone: Optional[bool] = None,
) -> bool:
    """Extend the expiration time of an actively leased task and record operation telemetry."""
    duration = min(max(30, additional_seconds), MAX_LEASE_DURATION_SECONDS)
    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()
    new_expires_dt = datetime.fromtimestamp(now_dt.timestamp() + duration, tz=timezone.utc)
    new_expires_iso = new_expires_dt.isoformat()

    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT task_id, attempt_count FROM worker_task_leases WHERE id = ? AND worker_id = ? AND status IN ('leased', 'active') AND lease_expires_at > ?",
            (lease_id, worker_id, now_iso),
        ).fetchone()
        if not row:
            return False

        cursor = conn.execute(
            """
            UPDATE worker_task_leases
            SET status = 'active',
                heartbeat_at = ?,
                lease_expires_at = ?,
                updated_at = ?
            WHERE id = ? AND worker_id = ? AND status IN ('leased', 'active') AND lease_expires_at > ?
            """,
            (now_iso, new_expires_iso, now_iso, lease_id, worker_id, now_iso),
        )
        conn.commit()

        if cursor.rowcount > 0:
            task_id = row["task_id"]
            attempt = row["attempt_count"]
            loop_detection.detector.record_heartbeat(task_id, now_iso)
            if tool_name or error_category or progress_milestone is not None:
                loop_detection.detector.record_operation(
                    task_id=task_id,
                    tool_name=tool_name or "heartbeat",
                    arguments=tool_args,
                    result=result_summary,
                    attempt=attempt,
                    error_category=error_category,
                    explicit_progress=progress_milestone,
                    operation_ts=now_iso,
                )
            return True
        return False
    finally:
        conn.close()


def record_task_telemetry(
    worker_id: str,
    lease_id: str,
    tool_name: str,
    arguments: Any = None,
    result: Any = None,
    error_category: Optional[str] = None,
    progress_milestone: Optional[bool] = None,
) -> bool:
    """Record operation telemetry for an active task lease."""
    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()

    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT task_id, attempt_count FROM worker_task_leases WHERE id = ? AND worker_id = ? AND status IN ('leased', 'active') AND lease_expires_at > ?",
            (lease_id, worker_id, now_iso),
        ).fetchone()
        if not row:
            return False

        task_id = row["task_id"]
        attempt = row["attempt_count"]
        loop_detection.detector.record_heartbeat(task_id, now_iso)
        loop_detection.detector.record_operation(
            task_id=task_id,
            tool_name=tool_name,
            arguments=arguments,
            result=result,
            attempt=attempt,
            error_category=error_category,
            explicit_progress=progress_milestone,
            operation_ts=now_iso,
        )
        return True
    finally:
        conn.close()


def _evaluate_and_record_metric_observation(
    conn: sqlite3.Connection,
    worker_id: str,
    actual_task_id: str,
    task_row: Optional[sqlite3.Row],
    idempotency_key: str,
    metric_obs: Dict[str, Any],
    status: str,
    exit_code: int,
    runtime_seconds: float,
    now: str,
) -> Optional[str]:
    """
    Evaluate and record a worker-submitted metric observation under strict controller verification rules.
    Enforces Repair C:
    - Worker-submitted verification_status is discarded; worker cannot self-verify.
    - Check metric exists and is active.
    - Ensure measurement value is a valid, finite number.
    - Authoritative telemetry: runtime duration uses controller-observed runtime_seconds.
    - Check evidence references and artifact checksum integrity.
    - Corrupted or invalid checksums reject the observation.
    - Missing evidence leaves observation 'unverified'.
    - Deduplicates identical observations for the same metric & evidence reference.
    """
    metric_id = metric_obs.get("metric_id")
    raw_val = metric_obs.get("value")
    if not metric_id or raw_val is None:
        return None

    try:
        val = float(raw_val)
        if not math.isfinite(val):
            return None
    except (ValueError, TypeError):
        return None

    # Check metric exists and is active
    metric_row = conn.execute(
        "SELECT id, name, unit, metric_type, measurement_method, status FROM project_metrics WHERE id = ?",
        (metric_id,),
    ).fetchone()
    if not metric_row or metric_row["status"] != "active":
        logger.warning("Metric observation rejected: metric %s does not exist or is inactive", metric_id)
        return None

    # For runtime telemetry metrics, use controller-observed duration
    collection_method = metric_row["measurement_method"] or "task_telemetry"
    if metric_row["metric_type"] == "duration" or (collection_method == "task_telemetry" and metric_obs.get("is_runtime_telemetry")):
        val = float(runtime_seconds)

    # Worker-submitted claims cannot self-verify: evaluate verification status independently
    evidence_ref = (
        metric_obs.get("evidence_reference")
        or metric_obs.get("artifact_path")
        or metric_obs.get("artifact_reference")
        or (f"deliverable:{task_row['deliverable_id']}" if task_row and task_row["deliverable_id"] else "")
        or ""
    ).strip()
    checksum = (metric_obs.get("artifact_checksum") or metric_obs.get("checksum") or "").strip()
    file_path_str = (metric_obs.get("file_path") or metric_obs.get("path") or "").strip()

    verification_status = "unverified"
    notes = metric_obs.get("notes") or f"Automated observation from task {actual_task_id}"

    # Rule 1: Failed task execution rejects observation
    if status != "success" or exit_code != 0:
        verification_status = "rejected"
        notes += " [Execution failed or returned non-zero exit code]"

    # Rule 2: Checksum validation if checksum is provided
    elif checksum:
        # Checksum must be valid 64-char hex
        is_valid_hex = len(checksum) == 64 and all(c in "0123456789abcdefABCDEF" for c in checksum)
        if not is_valid_hex:
            verification_status = "rejected"
            notes += f" [Rejected: Invalid artifact checksum format: '{checksum}']"
        else:
            # Search candidate artifact files on controller
            candidate_paths = []
            if file_path_str:
                candidate_paths.append(Path(file_path_str))
                candidate_paths.append(settings.evidence_dir / f"task_{actual_task_id}" / Path(file_path_str).name)
            if evidence_ref:
                candidate_paths.append(Path(evidence_ref))
                candidate_paths.append(settings.evidence_dir / f"task_{actual_task_id}" / Path(evidence_ref).name)
            task_ev_dir = settings.evidence_dir / f"task_{actual_task_id}"
            if task_ev_dir.exists():
                for p in task_ev_dir.iterdir():
                    if p.is_file():
                        candidate_paths.append(p)

            target_path = None
            for cp in candidate_paths:
                if cp.exists() and cp.is_file():
                    target_path = cp
                    break

            if target_path and target_path.exists() and target_path.is_file():
                try:
                    file_bytes = target_path.read_bytes()
                    computed = hashlib.sha256(file_bytes).hexdigest()
                    if computed.lower() == checksum.lower():
                        verification_status = "verified"
                        notes += f" [Verified: Artifact checksum matches {target_path.name}]"
                    else:
                        verification_status = "rejected"
                        notes += f" [Rejected: Checksum mismatch (expected {checksum}, computed {computed})]"
                except Exception as ex:
                    verification_status = "unverified"
                    notes += f" [Error verifying artifact file: {ex}]"
            elif file_path_str and (target_path is None or not target_path.exists()):
                verification_status = "rejected"
                notes += f" [Rejected: Referenced artifact file '{file_path_str}' not found on controller]"
            else:
                # Shortcut A eliminated: Checksum provided without retrievable artifact file on controller CANNOT verify!
                verification_status = "unverified"
                notes += " [Reported: Checksum provided without retrievable artifact file on controller]"

    # Rule 3: Deliverable or trusted evidence linkage
    elif evidence_ref:
        if evidence_ref.startswith("execution:"):
            # Controller execution telemetry
            verification_status = "verified"
            notes += " [Verified: Controller execution telemetry]"
        elif task_row and task_row["deliverable_id"]:
            # Shortcut B eliminated: Deliverable linkage alone does NOT verify!
            verification_status = "unverified"
            notes += f" [Reported: Linked to deliverable '{task_row['deliverable_id']}', awaiting independent artifact validation]"
        else:
            # Evidence reference provided but no independent checksum
            verification_status = "unverified"
            notes += " [Reported: Evidence reference provided, pending checksum verification]"
    else:
        # Missing evidence
        evidence_ref = f"execution:{idempotency_key}"
        verification_status = "unverified"
        notes += " [Reported: Missing independent evidence reference]"

    # Deduplication check: check if an identical observation was already recorded for this metric
    existing_obs = conn.execute(
        "SELECT id, verification_status FROM metric_observations WHERE metric_id = ? AND evidence_reference = ?",
        (metric_id, evidence_ref),
    ).fetchone()
    if existing_obs:
        logger.info("Deduplicated observation for metric %s (existing: %s)", metric_id, existing_obs["id"])
        return existing_obs["id"]

    obs_id = f"obs_{uuid4().hex[:12]}"
    conn.execute(
        """
        INSERT INTO metric_observations (
            id, metric_id, value, measured_at, recorded_at,
            source, collection_method, verification_status,
            evidence_reference, notes, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            obs_id,
            metric_id,
            val,
            now,
            now,
            f"vps_worker:{worker_id}",
            collection_method,
            verification_status,
            evidence_ref,
            notes,
            worker_id,
        ),
    )
    logger.info("Recorded metric observation %s (metric: %s, val: %s, status: %s)", obs_id, metric_id, val, verification_status)
    return obs_id


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
    artifacts: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Record task outcome, enforce idempotency, update lease, and append to execution ledger.
    """
    now = _utc_now_iso()
    result_json = json.dumps(result_payload or {})
    deliverables_json = json.dumps(deliverable_ids or [])

    conn = get_db_connection()
    try:
        # 1. Check idempotency: If this idempotency_key was already processed, return existing record or conflict
        existing = conn.execute(
            "SELECT id, status, started_at, completed_at, result_payload_json FROM worker_executions WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()

        if existing:
            # Check payload and status compatibility
            existing_status = existing["status"]
            if existing_status != status:
                raise TaskAuthorizationError(
                    f"Idempotency conflict: key '{idempotency_key}' previously submitted with status '{existing_status}', cannot resubmit with '{status}'.",
                    status_code=409,
                )
            existing_payload = json.loads(existing["result_payload_json"] or "{}")
            submitted_payload = result_payload or {}
            has_conflict = False
            for k in submitted_payload:
                if k in existing_payload and submitted_payload[k] != existing_payload[k]:
                    has_conflict = True
                    break
            if not has_conflict and existing_payload and submitted_payload:
                if not (set(submitted_payload.keys()).issubset(set(existing_payload.keys())) or
                        set(existing_payload.keys()).issubset(set(submitted_payload.keys()))):
                    has_conflict = True

            if has_conflict:
                raise TaskAuthorizationError(
                    f"Idempotency conflict: key '{idempotency_key}' previously submitted with different output content. Cannot corrupt or replace accepted result.",
                    status_code=409,
                )
            logger.info("Idempotent submission detected for key %s (Execution: %s)", idempotency_key, existing["id"])
            return {
                "status": "already_processed",
                "execution_id": existing["id"],
                "task_status": existing["status"],
                "completed_at": existing["completed_at"],
            }

        # 2. Authoritative policy verification (F-10)
        auth_context = verify_worker_task_authorization(
            worker_id=worker_id,
            task_id=task_id,
            operation="submit",
            lease_id=lease_id,
            conn=conn,
        )
        task_row = auth_context["task"]
        lease_row = auth_context["lease"]
        actual_task_id = task_row["id"]
        actual_lease_id = lease_row["id"] if auth_context.get("has_db_lease") else None
        was_expired_lease = bool(auth_context.get("was_expired_lease") or lease_row.get("status") == "expired")

        # 3. Check for terminal task conflict (preserve record without overwriting terminal task: completed, cancelled, failed)
        task_already_terminal = bool(task_row["status"] in ("completed", "cancelled", "failed"))
        if task_already_terminal:
            # Another worker or process already terminated this task!
            # Preserve execution record as reconciled conflict without overwriting the terminal task
            execution_id = f"exec_{uuid4().hex[:12]}"
            conn.execute(
                """
                INSERT INTO worker_executions (
                    id, task_id, worker_id, lease_id, idempotency_key, status,
                    exit_code, started_at, completed_at, runtime_seconds,
                    stdout_summary, stderr_summary, error_category,
                    deliverable_ids_json, result_payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, 'cancelled', ?, ?, ?, ?, ?, ?, 'reconciled_conflict', ?, ?, ?)
                """,
                (
                    execution_id, actual_task_id, worker_id, actual_lease_id, idempotency_key,
                    exit_code, now, now, runtime_seconds,
                    stdout_summary, stderr_summary,
                    deliverables_json, result_json, now,
                ),
            )
            if actual_lease_id:
                conn.execute(
                    "UPDATE worker_task_leases SET status = 'cancelled', updated_at = ? WHERE id = ?",
                    (now, actual_lease_id),
                )
            conn.commit()
            logger.warning("Recorded conflicting delayed execution %s for terminal task %s (%s) (preserved for operator review)", execution_id, task_id, task_row["status"])
            return {
                "status": "reconciled_conflict",
                "execution_id": execution_id,
                "task_id": task_id,
                "task_status": task_row["status"],
                "completed_at": now,
                "message": f"Task was already in terminal state '{task_row['status']}'; result preserved as conflicting evidence without modifying task status.",
            }

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
            if was_expired_lease:
                conn.execute(
                    """
                    UPDATE infrastructure_reconciliations
                    SET status = 'resolved',
                        resolved_at = ?,
                        resolution_notes = ?
                    WHERE status = 'detected' AND description LIKE ?
                    """,
                    (now, f"Reconciled by delayed submission from worker {worker_id}", f"%{actual_lease_id}%"),
                )

        # 2. Update task record if exists with compare-and-swap (CAS)
        task_status = "completed" if status == "success" else "failed"
        if actual_task_id:
            cursor = conn.execute(
                """
                UPDATE tasks
                SET status = ?,
                    result_data_json = ?,
                    error_details = ?,
                    updated_at = ?
                WHERE id = ? AND status IN ('in_progress', 'pending', 'queued', 'approved')
                """,
                (task_status, result_json, stderr_summary if status != "success" else None, now, actual_task_id),
            )
            if cursor.rowcount == 0:
                t_row = conn.execute("SELECT status FROM tasks WHERE id = ?", (actual_task_id,)).fetchone()
                cur_status = t_row["status"] if t_row else "unknown"
                raise TaskAuthorizationError(
                    f"State transition conflict: task '{actual_task_id}' is in state '{cur_status}', cannot transition to '{task_status}'.",
                    status_code=409,
                )

            # Record task operational event in task_execution_events
            try:
                attempt_row = conn.execute(
                    "SELECT count(*) FROM task_execution_events WHERE task_id = ?",
                    (actual_task_id,),
                ).fetchone()
                attempt_num = (attempt_row[0] if attempt_row else 0) + 1
                conn.execute(
                    """
                    INSERT INTO task_execution_events (
                        task_id, attempt_number, outcome, attempted, actual_result,
                        evidence_summary, assumptions_feedback, error_details,
                        reusable_lessons, resulting_knowledge_id, created_by, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        actual_task_id,
                        attempt_num,
                        "completed" if status == "success" else "failed",
                        f"VPS task execution by {worker_id}",
                        stdout_summary[:500] if stdout_summary else (status if status == "success" else "execution failure"),
                        f"Exit code {exit_code}, runtime {runtime_seconds}s",
                        "",
                        stderr_summary[:500] if stderr_summary else "",
                        f"Runtime {runtime_seconds}s",
                        None,
                        worker_id,
                        now,
                    ),
                )
            except Exception as ev_err:
                logger.warning("Failed to record task_execution_event for %s: %s", actual_task_id, ev_err)

            # Process physical artifacts submitted by worker
            processed_artifacts = []
            raw_artifacts = artifacts or (result_payload.get("artifacts") if isinstance(result_payload, dict) else None)
            if not raw_artifacts and isinstance(result_payload, dict) and "artifact" in result_payload:
                raw_artifacts = [result_payload["artifact"]]

            if raw_artifacts and isinstance(raw_artifacts, list):
                for art in raw_artifacts:
                    if not isinstance(art, dict):
                        continue
                    filename = art.get("filename") or f"artifact_{uuid4().hex[:8]}.bin"
                    expected_sha = art.get("expected_sha256") or art.get("sha256") or art.get("checksum")
                    content_bytes = b""
                    if "content_base64" in art and art["content_base64"]:
                        try:
                            content_bytes = base64.b64decode(art["content_base64"])
                        except Exception as b64_err:
                            logger.warning("Failed to decode artifact %s base64: %s", filename, b64_err)
                            continue
                    elif "content" in art and art["content"]:
                        content_bytes = art["content"].encode("utf-8") if isinstance(art["content"], str) else art["content"]

                    if content_bytes:
                        try:
                            stored = store_task_artifact(
                                task_id=actual_task_id or task_id,
                                filename=filename,
                                content_bytes=content_bytes,
                                expected_sha256=expected_sha,
                                worker_id=worker_id,
                                conn=conn,
                            )
                            processed_artifacts.append(stored)
                        except Exception as art_err:
                            logger.warning("Failed to store task artifact %s: %s", filename, art_err)

            # Update deliverable if task is linked to one
            if task_row and task_row.get("deliverable_id"):
                try:
                    deliv_id = task_row["deliverable_id"]
                    deliv = conn.execute("SELECT * FROM deliverables WHERE id = ?", (deliv_id,)).fetchone()
                    if deliv and deliv["status"] in ("planned", "in_progress"):
                        new_deliv_status = "submitted" if status == "success" else deliv["status"]
                        artifact_ref_parts = [f"execution:{idempotency_key}"]
                        for pa in processed_artifacts:
                            artifact_ref_parts.append(f"artifact:{actual_task_id}/{pa['filename']}")
                        artifact_ref = ";".join(artifact_ref_parts)

                        primary_art_id = None
                        for pa in processed_artifacts:
                            if pa.get("artifact_id"):
                                primary_art_id = pa["artifact_id"]
                                break

                        conn.execute(
                            """
                            UPDATE deliverables
                            SET status = ?,
                                primary_artifact_id = COALESCE(primary_artifact_id, ?),
                                artifact_references = CASE WHEN artifact_references = '' THEN ? ELSE artifact_references || ';' || ? END,
                                updated_at = ?
                            WHERE id = ?
                            """,
                            (new_deliv_status, primary_art_id, artifact_ref, artifact_ref, now, deliv_id),
                        )
                        # Add revision
                        rev_count = conn.execute(
                            "SELECT count(*) FROM deliverable_revisions WHERE deliverable_id = ?",
                            (deliv_id,),
                        ).fetchone()[0]
                        conn.execute(
                            """
                            INSERT INTO deliverable_revisions (
                                deliverable_id, revision_number, status, artifact_references,
                                verification_results, decision_rationale, decided_by, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                deliv_id,
                                rev_count + 1,
                                new_deliv_status,
                                artifact_ref,
                                f"exit_code={exit_code}, runtime={runtime_seconds}s, artifacts={len(processed_artifacts)}",
                                f"Automatic submission from VPS worker {worker_id}",
                                worker_id,
                                now,
                            ),
                        )
                except Exception as deliv_err:
                    logger.warning("Failed to update deliverable for task %s: %s", actual_task_id, deliv_err)

            # Record validated metric observation if supplied in result_payload (enforcing Repair C)
            if result_payload and isinstance(result_payload, dict):
                metric_obs = result_payload.get("metric_observation")
                if isinstance(metric_obs, dict) and "metric_id" in metric_obs and "value" in metric_obs:
                    try:
                        _evaluate_and_record_metric_observation(
                            conn=conn,
                            worker_id=worker_id,
                            actual_task_id=actual_task_id,
                            task_row=task_row,
                            idempotency_key=idempotency_key,
                            metric_obs=metric_obs,
                            status=status,
                            exit_code=exit_code,
                            runtime_seconds=runtime_seconds,
                            now=now,
                        )
                    except Exception as m_err:
                        logger.warning("Failed to evaluate/record metric observation from task %s: %s", actual_task_id, m_err)

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
        logger.info("Recorded execution %s for task %s (status: %s, artifacts: %d)", execution_id, task_id, status, len(processed_artifacts))

        # Record inference telemetry if task was an inference task
        if result_payload and isinstance(result_payload, dict):
            if result_payload.get("execution_class") == "Inference" or result_payload.get("gen_tokens") is not None:
                try:
                    from app.services.observatory import storage as obs_storage
                    obs_storage.record_inference_telemetry({
                        "request_id": f"inf-{actual_task_id or task_id}-{idempotency_key[:8]}",
                        "task_id": actual_task_id,
                        "project_id": task_row["project_id"] if task_row else None,
                        "node_id": worker_id,
                        "model": result_payload.get("model_requested") or result_payload.get("model_returned") or "qwen2.5:1.5b",
                        "provider": result_payload.get("engine", "ollama"),
                        "status": status,
                        "prompt_tokens": result_payload.get("prompt_tokens") or 0,
                        "gen_tokens": result_payload.get("gen_tokens") or 0,
                        "prompt_eval_duration_ms": (result_payload.get("prompt_eval_duration_s") or 0.0) * 1000.0,
                        "eval_duration_ms": (result_payload.get("eval_duration_s") or 0.0) * 1000.0,
                        "load_duration_ms": (result_payload.get("load_duration_s") or 0.0) * 1000.0,
                        "total_duration_ms": (result_payload.get("total_duration_s") or runtime_seconds) * 1000.0,
                        "tokens_per_second": result_payload.get("tokens_per_second") if result_payload.get("tokens_per_second") is not None else 0.0,
                        "error_category": error_category,
                        "created_at": now,
                        "completed_at": now,
                    })
                except Exception as inf_err:
                    logger.warning("Failed to record inference telemetry for task %s: %s", task_id, inf_err)

        return {
            "status": "recorded",
            "execution_id": execution_id,
            "task_id": task_id,
            "task_status": task_status,
            "completed_at": now,
            "stored_artifacts": processed_artifacts,
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

        # Fetch current attempt count and max_retries for task
        cur = conn.execute("SELECT attempt_count, max_retries FROM tasks WHERE id = ?", (task_id,))
        task_row = cur.fetchone()
        cur_attempts = (task_row["attempt_count"] if task_row and task_row["attempt_count"] is not None else 0)
        max_retries = (task_row["max_retries"] if task_row and task_row["max_retries"] is not None else 3)
        new_attempts = cur_attempts + 1

        if new_attempts >= max_retries:
            # Mark task failed due to retry bounds exhausted
            conn.execute(
                """
                UPDATE tasks
                SET status = 'failed',
                    assigned_worker = NULL,
                    attempt_count = ?,
                    error_details = ?,
                    updated_at = ?
                WHERE id = ? AND status = 'in_progress'
                """,
                (
                    new_attempts,
                    f"Task exceeded maximum retry attempts ({max_retries}) due to worker lease expiry/abandonment.",
                    now_iso,
                    task_id,
                ),
            )
            logger.warning("Task %s failed after reaching max retries (%s) during lease expiry reconciliation", task_id, max_retries)
        else:
            # Reset task to pending so another eligible worker can claim it
            conn.execute(
                """
                UPDATE tasks
                SET status = 'pending',
                    assigned_worker = NULL,
                    attempt_count = ?,
                    updated_at = ?
                WHERE id = ? AND status = 'in_progress'
                """,
                (new_attempts, now_iso, task_id),
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
                f"Task lease {lease_id} for task {task_id} expired without heartbeat. Task re-queued (attempt {new_attempts}/{max_retries}).",
                now_iso,
            ),
        )
        recovered_tasks.append(task_id)
        logger.warning("Reconciled expired lease %s for task %s on worker %s (attempt %s/%s)", lease_id, task_id, worker_id, new_attempts, max_retries)

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
