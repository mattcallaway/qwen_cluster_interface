import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from app.database import get_db_connection

logger = logging.getLogger("qwen_collective.tasks")

VALID_STATUSES = ["draft", "queued", "running", "awaiting_approval", "completed", "failed", "cancelled"]


def validate_project_task_dispatch(task: Dict[str, Any]) -> List[str]:
    """Deterministic drift checks before a project task may be queued. Returns problems ([] = OK).

    A project task must trace Project -> selected active Path -> valid stage, and must state
    its purpose, expected output and completion criteria. Model-generated text is never
    accepted as authorization; only these persisted facts are checked.
    """
    if not task.get("project_id"):
        return []
    problems: List[str] = []
    conn = get_db_connection()
    try:
        project = conn.execute("SELECT status, selected_path_id FROM projects WHERE id = ?",
                               (task["project_id"],)).fetchone()
        if not project:
            return ["Linked project no longer exists."]
        if project["status"] != "active":
            problems.append(f"Project is '{project['status']}', not active.")
        if not project["selected_path_id"]:
            problems.append("Project has no selected Path.")
        if not task.get("path_id"):
            problems.append("Task is not linked to a Path.")
        elif task["path_id"] != project["selected_path_id"]:
            problems.append("Task belongs to a Path that is not the project's selected Path.")
        else:
            path = conn.execute("SELECT status FROM project_paths WHERE id = ?", (task["path_id"],)).fetchone()
            if not path or path["status"] != "active":
                problems.append(f"Selected Path is '{path['status'] if path else 'missing'}', not active.")
        if not task.get("stage_id"):
            problems.append("Task is not linked to a stage.")
        else:
            stage = conn.execute("SELECT path_id, status FROM path_stages WHERE id = ?", (task["stage_id"],)).fetchone()
            if not stage or stage["path_id"] != task.get("path_id"):
                problems.append("Stage does not belong to the task's Path.")
            elif stage["status"] not in ("planned", "active"):
                problems.append(f"Stage is '{stage['status']}'; work can only be queued for planned or active stages.")
    finally:
        conn.close()
    for field, label in (("purpose", "Purpose"), ("expected_output", "Expected output"),
                         ("completion_criteria", "Completion criteria")):
        if not (task.get(field) or "").strip():
            problems.append(f"{label} is not defined.")
    return problems


def create_task(
    title: str,
    description: str = "",
    task_type: str = "general",
    approval_required: bool = False,
    assigned_worker: Optional[str] = None,
    input_data: Optional[Dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
    related_knowledge_path: Optional[str] = None,
    project_id: Optional[str] = None,
    path_id: Optional[str] = None,
    stage_id: Optional[str] = None,
    purpose: Optional[str] = None,
    expected_output: Optional[str] = None,
    completion_criteria: Optional[str] = None,
) -> Dict[str, Any]:
    task_id = f"task-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()
    status = "awaiting_approval" if approval_required else "queued"
    if project_id:
        # Project work is never queued directly. Fully traceable work waits for approval; else it is a draft.
        approval_required = True
        draft = {"project_id": project_id, "path_id": path_id, "stage_id": stage_id, "purpose": purpose,
                 "expected_output": expected_output, "completion_criteria": completion_criteria}
        status = "awaiting_approval" if not validate_project_task_dispatch(draft) else "draft"
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if idempotency_key:
        cursor.execute("SELECT * FROM tasks WHERE idempotency_key = ?", (idempotency_key,))
        existing = cursor.fetchone()
        if existing:
            conn.close()
            return dict(existing)
            
    try:
        cursor.execute("""
        INSERT INTO tasks (
            id, title, description, task_type, status,
            assigned_worker, attempt_count, max_retries, approval_required,
            idempotency_key, input_data_json, result_data_json,
            related_knowledge_path, created_at, updated_at,
            project_id, path_id, stage_id, purpose, expected_output, completion_criteria
        ) VALUES (?, ?, ?, ?, ?, ?, 0, 3, ?, ?, ?, '{}', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            task_id, title.strip(), description.strip(), task_type, status,
            assigned_worker, 1 if approval_required else 0,
            idempotency_key, json.dumps(input_data or {}),
            related_knowledge_path, now, now,
            project_id, path_id, stage_id,
            (purpose or "").strip() or None, (expected_output or "").strip() or None,
            (completion_criteria or "").strip() or None,
        ))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
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
    """Approve a task currently awaiting approval, moving it to queued.

    Project tasks are re-validated at approval time (the selected Path or stage may have changed).
    """
    task = get_task_by_id(task_id)
    if not task or task["status"] != "awaiting_approval":
        return False
    if validate_project_task_dispatch(task):
        return False
    conn = get_db_connection()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("UPDATE tasks SET status = 'queued', updated_at = ? WHERE id = ? AND status = 'awaiting_approval'",
                 (now, task_id))
    conn.commit()
    conn.close()
    return True


def submit_task_for_approval(task_id: str) -> List[str]:
    """Move a draft project task to awaiting_approval if it passes drift checks. Returns problems."""
    task = get_task_by_id(task_id)
    if not task:
        return ["Task not found."]
    if task["status"] != "draft":
        return [f"Task is '{task['status']}', not draft."]
    problems = validate_project_task_dispatch(task)
    if problems:
        return problems
    conn = get_db_connection()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("UPDATE tasks SET status = 'awaiting_approval', approval_required = 1, updated_at = ? "
                 "WHERE id = ? AND status = 'draft'", (now, task_id))
    conn.commit()
    conn.close()
    return []


def get_project_tasks(project_id: str) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        rows = conn.execute("SELECT * FROM tasks WHERE project_id = ? ORDER BY created_at DESC", (project_id,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]

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
