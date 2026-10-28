import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from app.database import get_db_connection, db_transaction
from app.services.projects import ValidationError, NotFound

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

        # Deliverable reference verification
        if task.get("deliverable_id"):
            deliv = conn.execute("SELECT project_id, stage_id FROM deliverables WHERE id = ?",
                                 (task["deliverable_id"],)).fetchone()
            if not deliv:
                problems.append("Referenced deliverable does not exist.")
            elif deliv["project_id"] != task.get("project_id"):
                problems.append("Referenced deliverable belongs to a different project.")
    finally:
        conn.close()

    # Check task prerequisite dependencies
    if task.get("id"):
        for dep in get_task_dependencies(task["id"]):
            if dep["status"] != "completed":
                problems.append(f"Prerequisite task '{dep['title']}' ({dep['id']}) is not completed (status: {dep['status']}).")

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
    deliverable_id: Optional[str] = None,
) -> Dict[str, Any]:
    task_id = f"task-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()
    status = "awaiting_approval" if approval_required else "queued"
    if stage_id and not path_id:
        conn_init = get_db_connection()
        try:
            stg_row = conn_init.execute("SELECT path_id FROM path_stages WHERE id = ?", (stage_id,)).fetchone()
            if stg_row:
                path_id = stg_row[0]
        finally:
            conn_init.close()

    if project_id:
        # Project work is never queued directly. Fully traceable work waits for approval; else it is a draft.
        approval_required = True
        draft = {"project_id": project_id, "path_id": path_id, "stage_id": stage_id, "purpose": purpose,
                 "expected_output": expected_output, "completion_criteria": completion_criteria,
                 "deliverable_id": deliverable_id}
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
            project_id, path_id, stage_id, purpose, expected_output, completion_criteria, deliverable_id
        ) VALUES (?, ?, ?, ?, ?, ?, 0, 3, ?, ?, ?, '{}', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            task_id, title.strip(), description.strip(), task_type, status,
            assigned_worker, 1 if approval_required else 0,
            idempotency_key, json.dumps(input_data or {}),
            related_knowledge_path, now, now,
            project_id, path_id, stage_id,
            (purpose or "").strip() or None, (expected_output or "").strip() or None,
            (completion_criteria or "").strip() or None,
            deliverable_id,
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
    WHERE id = ? AND status != 'cancelled'
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


# --- Task Dependencies & Cycle Prevention (Milestone 2) ---------------------

def _has_dependency_path(conn: sqlite3.Connection, start_id: str, target_id: str) -> bool:
    """Check if target_id is reachable from start_id through dependency edges (graph traversal)."""
    visited = set()
    queue = [start_id]
    while queue:
        current = queue.pop(0)
        if current == target_id:
            return True
        if current in visited:
            continue
        visited.add(current)
        rows = conn.execute("SELECT depends_on_task_id FROM task_dependencies WHERE task_id = ?", (current,)).fetchall()
        for r in rows:
            dep_id = r["depends_on_task_id"]
            if dep_id not in visited:
                queue.append(dep_id)
    return False


def add_task_dependency(task_id: str, depends_on_task_id: str) -> None:
    """Declare that task_id depends on depends_on_task_id (depends_on_task_id must complete first)."""
    if task_id == depends_on_task_id:
        raise ValidationError("A task cannot depend on itself.")

    with db_transaction() as conn:
        t1 = conn.execute("SELECT id, project_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        t2 = conn.execute("SELECT id, project_id FROM tasks WHERE id = ?", (depends_on_task_id,)).fetchone()
        if not t1 or not t2:
            raise NotFound("One or both tasks not found.")

        # Project tasks can only depend on tasks in the same project or global tasks
        if t1["project_id"] and t2["project_id"] and t1["project_id"] != t2["project_id"]:
            raise ValidationError("Task cannot depend on a task from another project.")

        # Cycle prevention: if depends_on_task_id already depends on task_id, adding this would form a cycle
        if _has_dependency_path(conn, start_id=depends_on_task_id, target_id=task_id):
            raise ValidationError("Circular task dependency detected.")

        try:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("""
                INSERT INTO task_dependencies (task_id, depends_on_task_id, created_at)
                VALUES (?, ?, ?)
            """, (task_id, depends_on_task_id, now))
        except sqlite3.IntegrityError:
            raise ValidationError("Dependency relationship already exists.")


def remove_task_dependency(task_id: str, depends_on_task_id: str) -> None:
    """Remove a declared dependency between tasks."""
    with db_transaction() as conn:
        conn.execute("DELETE FROM task_dependencies WHERE task_id = ? AND depends_on_task_id = ?",
                     (task_id, depends_on_task_id))


def get_task_dependencies(task_id: str) -> List[Dict[str, Any]]:
    """Return all prerequisite tasks that task_id depends on."""
    conn = get_db_connection()
    try:
        rows = conn.execute("""
            SELECT t.*, td.depends_on_task_id, td.task_id AS dependent_task_id
            FROM tasks t
            JOIN task_dependencies td ON td.depends_on_task_id = t.id
            WHERE td.task_id = ?
            ORDER BY t.created_at ASC
        """, (task_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_task_dependents(task_id: str) -> List[Dict[str, Any]]:
    """Return all tasks that depend on task_id completing."""
    conn = get_db_connection()
    try:
        rows = conn.execute("""
            SELECT t.*, td.depends_on_task_id, td.task_id AS dependent_task_id
            FROM tasks t
            JOIN task_dependencies td ON td.task_id = t.id
            WHERE td.depends_on_task_id = ?
            ORDER BY t.created_at ASC
        """, (task_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# --- Task Execution Operational Events & Knowledge Capture (Milestone 2) ----

def record_task_execution(
    task_id: str,
    outcome: str,
    user: str,
    attempted: str,
    actual_result: str,
    evidence_summary: str = "",
    assumptions_feedback: str = "",
    error_details: str = "",
    reusable_lessons: str = "",
    create_knowledge_note: bool = False,
    knowledge_title: str = ""
) -> Dict[str, Any]:
    """Record an operational execution event for a task, and optionally capture durable Markdown knowledge."""
    if outcome not in ("completed", "failed", "aborted"):
        raise ValidationError(f"Invalid execution outcome: '{outcome}'.")

    task = get_task_by_id(task_id)
    if not task:
        raise NotFound("Task not found.")
    if task["status"] == "cancelled":
        logger.warning("Attempted to record completion for cancelled task %s; preserving cancelled state", task_id)
        return task

    kn_id = None
    if create_knowledge_note and task.get("project_id"):
        from app.services import knowledge_meta as km
        title = (knowledge_title or "").strip() or f"Result: {task['title'][:60]}"
        body = f"""## Task Execution Result
- **Task ID**: `{task_id}`
- **Task Title**: {task['title']}
- **Outcome**: {outcome}
- **Recorded by**: {user}

### What was attempted
{attempted}

### Actual outcome
{actual_result}

### Evidence produced
{evidence_summary or 'None specified.'}

### Assumptions supported / challenged
{assumptions_feedback or 'None specified.'}

### Reusable lessons
{reusable_lessons or 'None specified.'}
"""
        kn_meta = km.create_project_note(
            project_id=task["project_id"],
            title=title,
            knowledge_type="task_result" if outcome == "completed" else "lesson_learned",
            origin="human",
            body=body,
            author=user,
            tags="task-result, execution"
        )
        kn_id = kn_meta["id"]
        km.add_relation(kn_id, "task", task_id, "result_of", user, scope_project_id=task["project_id"])

    now = datetime.now(timezone.utc).isoformat()
    with db_transaction() as conn:
        attempt_num = conn.execute(
            "SELECT COUNT(*) FROM task_execution_events WHERE task_id = ?", (task_id,)
        ).fetchone()[0] + 1

        conn.execute("""
            INSERT INTO task_execution_events (
                task_id, attempt_number, outcome, attempted, actual_result, evidence_summary,
                assumptions_feedback, error_details, reusable_lessons, resulting_knowledge_id,
                created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (task_id, attempt_num, outcome, attempted, actual_result, evidence_summary,
              assumptions_feedback, error_details, reusable_lessons, kn_id, user, now))

        new_status = "completed" if outcome == "completed" else ("failed" if outcome == "failed" else "cancelled")
        conn.execute("""
            UPDATE tasks SET status = ?, error_details = ?, attempt_count = attempt_count + 1, updated_at = ?
            WHERE id = ? AND status != 'cancelled'
        """, (new_status, error_details or None, now, task_id))

        if task.get("project_id"):
            conn.execute("UPDATE projects SET last_activity_at = ? WHERE id = ?", (now, task["project_id"]))

    return get_task_by_id(task_id)  # type: ignore[return-value]


def list_task_execution_events(task_id: str) -> List[Dict[str, Any]]:
    """Return all execution event records for a task."""
    conn = get_db_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM task_execution_events WHERE task_id = ? ORDER BY attempt_number ASC
        """, (task_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

