import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from app.database import get_db_connection

logger = logging.getLogger("qwen_collective.tasks")

VALID_STATUSES = ["draft", "queued", "running", "awaiting_approval", "completed", "failed", "cancelled"]

def create_task(
    title: str,
    description: str = "",
    task_type: str = "general",
    approval_required: bool = False,
    assigned_worker: Optional[str] = None,
    input_data: Optional[Dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
    related_knowledge_path: Optional[str] = None
) -> Dict[str, Any]:
    task_id = f"task-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()
    status = "awaiting_approval" if approval_required else "queued"
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if idempotency_key:
        cursor.execute("SELECT * FROM tasks WHERE idempotency_key = ?", (idempotency_key,))
        existing = cursor.fetchone()
        if existing:
            conn.close()
            return dict(existing)
            
    cursor.execute("""
    INSERT INTO tasks (
        id, title, description, task_type, status,
        assigned_worker, attempt_count, max_retries, approval_required,
        idempotency_key, input_data_json, result_data_json,
        related_knowledge_path, created_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, 0, 3, ?, ?, ?, '{}', ?, ?, ?)
    """, (
        task_id, title.strip(), description.strip(), task_type, status,
        assigned_worker, 1 if approval_required else 0,
        idempotency_key, json.dumps(input_data or {}),
        related_knowledge_path, now, now
    ))
    conn.commit()
    conn.close()
    return get_task_by_id(task_id) # type: ignore

def get_task_by_id(task_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    try:
        d["input_data"] = json.loads(d.get("input_data_json", "{}"))
        d["result_data"] = json.loads(d.get("result_data_json", "{}"))
    except Exception:
        d["input_data"] = {}
        d["result_data"] = {}
    return d

def get_all_tasks(limit: int = 50, status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    if status_filter and status_filter in VALID_STATUSES:
        cursor.execute("SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC LIMIT ?", (status_filter, limit))
    else:
        cursor.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    
    results = []
    for r in rows:
        d = dict(r)
        try:
            d["input_data"] = json.loads(d.get("input_data_json", "{}"))
            d["result_data"] = json.loads(d.get("result_data_json", "{}"))
        except Exception:
            d["input_data"] = {}
            d["result_data"] = {}
        results.append(d)
    return results

def approve_task(task_id: str) -> bool:
    """Approve a task currently awaiting approval, moving it to queued."""
    task = get_task_by_id(task_id)
    if not task or task["status"] != "awaiting_approval":
        return False
    conn = get_db_connection()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("UPDATE tasks SET status = 'queued', updated_at = ? WHERE id = ?", (now, task_id))
    conn.commit()
    conn.close()
    return True

def cancel_task(task_id: str) -> bool:
    """Cancel a task."""
    conn = get_db_connection()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("UPDATE tasks SET status = 'cancelled', updated_at = ? WHERE id = ? AND status NOT IN ('completed', 'cancelled')", (now, task_id))
    conn.commit()
    conn.close()
    return True

def update_task_progress(task_id: str, status: str, result_data: Optional[Dict[str, Any]] = None, error_details: Optional[str] = None):
    conn = get_db_connection()
    now = datetime.now(timezone.utc).isoformat()
    res_str = json.dumps(result_data) if result_data is not None else None
    conn.execute("""
    UPDATE tasks SET
        status = ?,
        result_data_json = COALESCE(?, result_data_json),
        error_details = ?,
        attempt_count = attempt_count + CASE WHEN ? = 'running' THEN 1 ELSE 0 END,
        updated_at = ?
    WHERE id = ?
    """, (status, res_str, error_details, status, now, task_id))
    conn.commit()
    conn.close()

def get_task_counts() -> Dict[str, int]:
    """Return task counts by status."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status, count(*) as count FROM tasks GROUP BY status")
    rows = cursor.fetchall()
    conn.close()
    counts = {s: 0 for s in VALID_STATUSES}
    for r in rows:
        counts[r["status"]] = r["count"]
    return counts
