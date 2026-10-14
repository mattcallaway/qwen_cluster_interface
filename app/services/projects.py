"""Projects, Paths, Stages and append-only project decisions (Milestone 1).

State changes are explicit functions with validated transitions. Nothing here is
callable by a language model directly; routes require an authenticated user + CSRF.
"""
import re
import sqlite3
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from app.database import db_transaction, get_db_connection


class ValidationError(ValueError):
    """User-correctable input or state-transition error."""


class NotFound(LookupError):
    """Entity does not exist or does not belong to the referenced parent."""


NAME_MAX = 200
TEXT_MAX = 20_000
MAX_PROJECT_DEPTH = 3  # project -> sub-project -> sub-sub-project

PRIORITIES = ["low", "medium", "high", "critical"]
PROJECT_STATUSES = ["draft", "planning", "active", "paused", "completed", "cancelled", "archived"]
PROJECT_TRANSITIONS = {
    "draft": {"planning", "active", "cancelled", "archived"},
    "planning": {"draft", "active", "paused", "cancelled"},
    "active": {"paused", "completed", "cancelled"},
    "paused": {"active", "planning", "cancelled"},
    "completed": {"archived"},
    "cancelled": {"archived"},
    "archived": set(),
}

PATH_STATUSES = ["proposed", "under_review", "approved", "active", "paused", "superseded", "completed", "rejected"]
# 'active' is reachable only through select_path(); 'superseded' is also set by select_path().
PATH_TRANSITIONS = {
    "proposed": {"under_review", "approved", "rejected"},
    "under_review": {"proposed", "approved", "rejected"},
    "approved": {"rejected", "superseded"},
    "active": {"paused", "completed"},
    "paused": {"superseded"},
    "superseded": set(),
    "completed": set(),
    "rejected": set(),
}
PATH_EDITABLE = {"proposed", "under_review"}          # estimates are frozen once approved
PATH_SELECTABLE = {"approved", "paused"}
PATH_TERMINAL = {"superseded", "completed", "rejected"}

STAGE_STATUSES = ["planned", "active", "blocked", "awaiting_review", "completed", "skipped"]
STAGE_TRANSITIONS = {
    "planned": {"active", "skipped"},
    "active": {"blocked", "awaiting_review", "completed"},
    "blocked": {"active", "planned"},
    "awaiting_review": {"active", "completed", "blocked"},
    "completed": set(),
    "skipped": {"planned"},
}
STAGE_M1_STATUSES = {"planned", "active", "blocked"}

PROJECT_TEXT_FIELDS = ["description", "objective", "scope", "constraints_text", "success_criteria"]
PATH_TEXT_FIELDS = ["description", "assumptions", "constraints_text", "dependencies",
                    "resource_requirements", "risks", "expected_outcome"]
STAGE_TEXT_FIELDS = ["objective", "description", "prerequisites", "expected_outputs", "completion_criteria"]


# --- helpers ---------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _text(value: Optional[str], field: str, max_len: int = TEXT_MAX, required: bool = False) -> str:
    v = (value or "").strip()
    if required and not v:
        raise ValidationError(f"{field} is required.")
    if len(v) > max_len:
        raise ValidationError(f"{field} must be at most {max_len} characters.")
    return v


def _date(value: Optional[str], field: str) -> Optional[str]:
    v = (value or "").strip()
    if not v:
        return None
    try:
        return date.fromisoformat(v).isoformat()
    except ValueError:
        raise ValidationError(f"{field} must be a date in YYYY-MM-DD format.")


def _number(value: Any, field: str, minimum: float = 0.0, maximum: Optional[float] = None) -> Optional[float]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{field} must be a number.")
    if n != n or n in (float("inf"), float("-inf")):
        raise ValidationError(f"{field} must be a finite number.")
    if n < minimum or (maximum is not None and n > maximum):
        rng = f"between {minimum} and {maximum}" if maximum is not None else f"at least {minimum}"
        raise ValidationError(f"{field} must be {rng}.")
    return n


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (s or "project")[:48]


def _row(r: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    return dict(r) if r else None


def _touch(conn: sqlite3.Connection, project_id: str):
    now = _now()
    conn.execute("UPDATE projects SET last_activity_at = ?, updated_at = ? WHERE id = ?", (now, now, project_id))


def record_decision(conn: sqlite3.Connection, project_id: str, decision_type: str, rationale: str,
                    decided_by: str, subject_id: Optional[str] = None, from_value: Optional[str] = None,
                    to_value: Optional[str] = None, evidence_knowledge_id: Optional[str] = None) -> str:
    did = _new_id("dec")
    conn.execute("""
        INSERT INTO project_decisions (id, project_id, decision_type, subject_id, from_value, to_value,
                                       rationale, decided_by, decided_at, evidence_knowledge_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (did, project_id, decision_type, subject_id, from_value, to_value,
         _text(rationale, "Rationale", required=True), decided_by, _now(), evidence_knowledge_id))
    return did


# --- projects --------------------------------------------------------------

def _load_project(conn: sqlite3.Connection, project_id: str) -> Dict[str, Any]:
    p = _row(conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone())
    if not p:
        raise NotFound("Project not found.")
    return p


def _check_parent(conn: sqlite3.Connection, project_id: Optional[str], parent_id: Optional[str]):
    if not parent_id:
        return
    if parent_id == project_id:
        raise ValidationError("A project cannot be its own parent.")
    _load_project(conn, parent_id)
    # Walk up from the proposed parent: reject cycles and excessive depth.
    depth, cursor_id, seen = 1, parent_id, set()
    while cursor_id:
        if cursor_id == project_id or cursor_id in seen:
            raise ValidationError("That parent would create a circular project hierarchy.")
        seen.add(cursor_id)
        row = conn.execute("SELECT parent_id FROM projects WHERE id = ?", (cursor_id,)).fetchone()
        cursor_id = row["parent_id"] if row else None
        if cursor_id:
            depth += 1
    # depth = number of ancestors the project will have; add descendants' height for moves.
    height = _subtree_height(conn, project_id) if project_id else 0
    if depth + 1 + height > MAX_PROJECT_DEPTH:
        raise ValidationError(f"Project nesting is limited to {MAX_PROJECT_DEPTH} levels.")


def _subtree_height(conn: sqlite3.Connection, project_id: str) -> int:
    children = [r["id"] for r in conn.execute("SELECT id FROM projects WHERE parent_id = ?", (project_id,))]
    return 0 if not children else 1 + max(_subtree_height(conn, c) for c in children)


def create_project(name: str, owner: str, **fields) -> Dict[str, Any]:
    clean_name = _text(name, "Name", NAME_MAX, required=True)
    pid = _new_id("prj")
    now = _now()
    values = {f: _text(fields.get(f), f.replace("_", " ").capitalize()) for f in PROJECT_TEXT_FIELDS}
    priority = fields.get("priority") or "medium"
    if priority not in PRIORITIES:
        raise ValidationError("Invalid priority.")
    parent_id = (fields.get("parent_id") or "").strip() or None
    try:
        with db_transaction() as conn:
            _check_parent(conn, None, parent_id)
            conn.execute("""
                INSERT INTO projects (id, name, description, objective, scope, constraints_text, success_criteria,
                    priority, status, parent_id, owner, knowledge_dir, target_date, review_date,
                    created_at, updated_at, last_activity_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (pid, clean_name, values["description"], values["objective"], values["scope"],
                 values["constraints_text"], values["success_criteria"], priority, parent_id, owner,
                 f"projects/{slugify(clean_name)}-{pid[-8:]}",
                 _date(fields.get("target_date"), "Target date"), _date(fields.get("review_date"), "Review date"),
                 now, now, now))
    except sqlite3.IntegrityError as e:
        if "projects.name" in str(e) or "ux_projects_name" in str(e):
            raise ValidationError("A project with that name already exists.")
        raise
    return get_project(pid)  # type: ignore[return-value]


def update_project(project_id: str, user: str, **fields) -> Dict[str, Any]:
    try:
        with db_transaction() as conn:
            p = _load_project(conn, project_id)
            if p["status"] == "archived":
                raise ValidationError("Archived projects are read-only.")
            name = _text(fields.get("name", p["name"]), "Name", NAME_MAX, required=True)
            vals = {f: _text(fields.get(f, p[f]), f.replace("_", " ").capitalize()) for f in PROJECT_TEXT_FIELDS}
            priority = fields.get("priority") or p["priority"]
            if priority not in PRIORITIES:
                raise ValidationError("Invalid priority.")
            if "parent_id" in fields:
                new_parent = (fields["parent_id"] or "").strip() or None
            else:
                new_parent = p["parent_id"]
            if new_parent != p["parent_id"]:
                _check_parent(conn, project_id, new_parent)
                record_decision(conn, project_id, "project_parent_change",
                                "Parent project changed by user.", user,
                                from_value=p["parent_id"], to_value=new_parent)
            conn.execute("""
                UPDATE projects SET name=?, description=?, objective=?, scope=?, constraints_text=?,
                    success_criteria=?, priority=?, parent_id=?, target_date=?, review_date=?, updated_at=?
                WHERE id=?""",
                (name, vals["description"], vals["objective"], vals["scope"], vals["constraints_text"],
                 vals["success_criteria"], priority, new_parent,
                 _date(fields.get("target_date", p["target_date"]), "Target date"),
                 _date(fields.get("review_date", p["review_date"]), "Review date"), _now(), project_id))
            _touch(conn, project_id)
    except sqlite3.IntegrityError as e:
        if "ux_projects_name" in str(e) or "projects.name" in str(e):
            raise ValidationError("A project with that name already exists.")
        raise
    return get_project(project_id)  # type: ignore[return-value]


def change_project_status(project_id: str, new_status: str, user: str, reason: str = "") -> Dict[str, Any]:
    if new_status not in PROJECT_STATUSES:
        raise ValidationError("Invalid project status.")
    with db_transaction() as conn:
        p = _load_project(conn, project_id)
        if new_status not in PROJECT_TRANSITIONS[p["status"]]:
            raise ValidationError(f"Cannot change project status from {p['status']} to {new_status}.")
        today = date.today().isoformat()
        start = p["start_date"] or (today if new_status == "active" else None)
        completed = today if new_status == "completed" else p["completed_date"]
        conn.execute("UPDATE projects SET status=?, start_date=?, completed_date=?, updated_at=? WHERE id=?",
                     (new_status, start, completed, _now(), project_id))
        record_decision(conn, project_id, "project_status_change",
                        _text(reason, "Reason") or "Status changed by user without a stated reason.",
                        user, subject_id=project_id, from_value=p["status"], to_value=new_status)
        _touch(conn, project_id)
    return get_project(project_id)  # type: ignore[return-value]


def get_project(project_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        return _row(conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone())
    finally:
        conn.close()


def list_projects(include_archived: bool = False) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        sql = "SELECT * FROM projects"
        if not include_archived:
            sql += " WHERE status <> 'archived'"
        sql += " ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, last_activity_at DESC"
        return [dict(r) for r in conn.execute(sql)]
    finally:
        conn.close()


def list_children(project_id: str) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM projects WHERE parent_id = ? ORDER BY name", (project_id,))]
    finally:
        conn.close()


def list_decisions(project_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM project_decisions WHERE project_id = ? ORDER BY decided_at DESC LIMIT ?",
            (project_id, limit))]
    finally:
        conn.close()


# --- paths -----------------------------------------------------------------

def _load_path(conn: sqlite3.Connection, project_id: str, path_id: str) -> Dict[str, Any]:
    p = _row(conn.execute("SELECT * FROM project_paths WHERE id = ? AND project_id = ?",
                          (path_id, project_id)).fetchone())
    if not p:
        raise NotFound("Path not found in this project.")
    return p


def _path_values(fields: Dict[str, Any], current: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cur = current or {}
    v = {
        "name": _text(fields.get("name", cur.get("name")), "Path name", NAME_MAX, required=True),
        "rationale": _text(fields.get("rationale", cur.get("rationale")),
                           "Strategic rationale (why this Path advances the objective)", required=True),
        "estimated_cost": _number(fields.get("estimated_cost", cur.get("estimated_cost")), "Estimated cost"),
        "estimated_effort_hours": _number(fields.get("estimated_effort_hours", cur.get("estimated_effort_hours")),
                                          "Estimated effort (hours)"),
        "forecast_confidence": _number(fields.get("forecast_confidence", cur.get("forecast_confidence")),
                                       "Forecast confidence", 0.0, 1.0),
        "expected_completion_date": _date(fields.get("expected_completion_date",
                                                     cur.get("expected_completion_date")), "Expected completion date"),
    }
    for f in PATH_TEXT_FIELDS:
        v[f] = _text(fields.get(f, cur.get(f)), f.replace("_", " ").capitalize())
    return v


def create_path(project_id: str, user: str, **fields) -> Dict[str, Any]:
    v = _path_values(fields)
    path_id = _new_id("pth")
    now = _now()
    try:
        with db_transaction() as conn:
            proj = _load_project(conn, project_id)
            if proj["status"] in ("completed", "cancelled", "archived"):
                raise ValidationError(f"Cannot add Paths to a {proj['status']} project.")
            conn.execute("""
                INSERT INTO project_paths (id, project_id, name, rationale, description, assumptions,
                    constraints_text, dependencies, resource_requirements, risks, estimated_cost,
                    estimated_effort_hours, expected_outcome, expected_completion_date, forecast_confidence,
                    status, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?)""",
                (path_id, project_id, v["name"], v["rationale"], v["description"], v["assumptions"],
                 v["constraints_text"], v["dependencies"], v["resource_requirements"], v["risks"],
                 v["estimated_cost"], v["estimated_effort_hours"], v["expected_outcome"],
                 v["expected_completion_date"], v["forecast_confidence"], user, now, now))
            _touch(conn, project_id)
    except sqlite3.IntegrityError as e:
        if "project_paths.project_id, project_paths.name" in str(e):
            raise ValidationError("This project already has a Path with that name.")
        raise
    return get_path(path_id)  # type: ignore[return-value]


def update_path(project_id: str, path_id: str, user: str, **fields) -> Dict[str, Any]:
    with db_transaction() as conn:
        p = _load_path(conn, project_id, path_id)
        if p["status"] not in PATH_EDITABLE:
            raise ValidationError("Path details are frozen once approved. Propose a new Path to change strategy.")
        v = _path_values(fields, p)
        conn.execute("""
            UPDATE project_paths SET name=?, rationale=?, description=?, assumptions=?, constraints_text=?,
                dependencies=?, resource_requirements=?, risks=?, estimated_cost=?, estimated_effort_hours=?,
                expected_outcome=?, expected_completion_date=?, forecast_confidence=?, updated_at=?
            WHERE id=?""",
            (v["name"], v["rationale"], v["description"], v["assumptions"], v["constraints_text"],
             v["dependencies"], v["resource_requirements"], v["risks"], v["estimated_cost"],
             v["estimated_effort_hours"], v["expected_outcome"], v["expected_completion_date"],
             v["forecast_confidence"], _now(), path_id))
        _touch(conn, project_id)
    return get_path(path_id)  # type: ignore[return-value]


def change_path_status(project_id: str, path_id: str, new_status: str, user: str, reason: str) -> Dict[str, Any]:
    if new_status not in PATH_STATUSES:
        raise ValidationError("Invalid Path status.")
    if new_status == "active":
        raise ValidationError("Use 'Select as active Path' to activate a Path.")
    rationale = _text(reason, "Reason", required=True)
    with db_transaction() as conn:
        p = _load_path(conn, project_id, path_id)
        if new_status not in PATH_TRANSITIONS[p["status"]]:
            raise ValidationError(f"Cannot change Path status from {p['status']} to {new_status}.")
        conn.execute("UPDATE project_paths SET status=?, updated_at=? WHERE id=?", (new_status, _now(), path_id))
        record_decision(conn, project_id, "path_status_change", rationale, user,
                        subject_id=path_id, from_value=p["status"], to_value=new_status)
        _touch(conn, project_id)
    return get_path(path_id)  # type: ignore[return-value]


def select_path(project_id: str, path_id: str, user: str, reason: str) -> Dict[str, Any]:
    """Make an approved (or paused) Path the project's single active Path; supersede the previous one."""
    rationale = _text(reason, "Reason for selecting this Path", required=True)
    with db_transaction() as conn:
        proj = _load_project(conn, project_id)
        if proj["status"] in ("completed", "cancelled", "archived"):
            raise ValidationError(f"Cannot change the Path of a {proj['status']} project.")
        new = _load_path(conn, project_id, path_id)
        if new["status"] not in PATH_SELECTABLE:
            raise ValidationError("Only an approved (or paused) Path can be selected. Approve it first.")
        previous_id = proj["selected_path_id"]
        now = _now()
        # Demote whichever Path is currently active (normally the previously selected one).
        current_active = conn.execute(
            "SELECT id FROM project_paths WHERE project_id = ? AND status = 'active'", (project_id,)).fetchone()
        for old_id in {x for x in (previous_id, current_active["id"] if current_active else None) if x and x != path_id}:
            old = _load_path(conn, project_id, old_id)
            if old["status"] in ("active", "paused", "approved"):
                conn.execute("UPDATE project_paths SET status='superseded', updated_at=? WHERE id=?", (now, old_id))
        conn.execute("UPDATE project_paths SET status='active', updated_at=? WHERE id=?", (now, path_id))
        conn.execute("UPDATE projects SET selected_path_id=?, updated_at=? WHERE id=?", (path_id, now, project_id))
        record_decision(conn, project_id, "path_selection", rationale, user,
                        subject_id=path_id, from_value=previous_id, to_value=path_id)
        _touch(conn, project_id)
    return get_path(path_id)  # type: ignore[return-value]


def get_path(path_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        return _row(conn.execute("SELECT * FROM project_paths WHERE id = ?", (path_id,)).fetchone())
    finally:
        conn.close()


def list_paths(project_id: str) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        return [dict(r) for r in conn.execute("""
            SELECT * FROM project_paths WHERE project_id = ?
            ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'approved' THEN 1 WHEN 'under_review' THEN 2
                     WHEN 'proposed' THEN 3 WHEN 'paused' THEN 4 ELSE 5 END, created_at""", (project_id,))]
    finally:
        conn.close()


# --- stages ----------------------------------------------------------------

def _load_stage(conn: sqlite3.Connection, project_id: str, stage_id: str) -> Dict[str, Any]:
    s = _row(conn.execute("""
        SELECT s.* FROM path_stages s JOIN project_paths p ON p.id = s.path_id
        WHERE s.id = ? AND p.project_id = ?""", (stage_id, project_id)).fetchone())
    if not s:
        raise NotFound("Stage not found in this project.")
    return s


def create_stage(project_id: str, path_id: str, user: str, **fields) -> Dict[str, Any]:
    name = _text(fields.get("name"), "Stage name", NAME_MAX, required=True)
    vals = {f: _text(fields.get(f), f.replace("_", " ").capitalize()) for f in STAGE_TEXT_FIELDS}
    est = _number(fields.get("estimated_effort_hours"), "Estimated effort (hours)")
    stage_id = _new_id("stg")
    now = _now()
    with db_transaction() as conn:
        path = _load_path(conn, project_id, path_id)
        if path["status"] in PATH_TERMINAL:
            raise ValidationError(f"Cannot add stages to a {path['status']} Path.")
        pos = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM path_stages WHERE path_id = ?",
                           (path_id,)).fetchone()[0]
        conn.execute("""
            INSERT INTO path_stages (id, path_id, position, name, objective, description, prerequisites,
                expected_outputs, completion_criteria, estimated_effort_hours, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned', ?, ?)""",
            (stage_id, path_id, pos, name, vals["objective"], vals["description"], vals["prerequisites"],
             vals["expected_outputs"], vals["completion_criteria"], est, now, now))
        _touch(conn, project_id)
    return get_stage(stage_id)  # type: ignore[return-value]


def move_stage(project_id: str, stage_id: str, direction: str) -> None:
    if direction not in ("up", "down"):
        raise ValidationError("Invalid direction.")
    with db_transaction() as conn:
        s = _load_stage(conn, project_id, stage_id)
        path = _load_path(conn, project_id, s["path_id"])
        if path["status"] in PATH_TERMINAL:
            raise ValidationError(f"Stages of a {path['status']} Path cannot be reordered.")
        op, order = ("<", "DESC") if direction == "up" else (">", "ASC")
        neighbor = conn.execute(
            f"SELECT id, position FROM path_stages WHERE path_id = ? AND position {op} ? ORDER BY position {order} LIMIT 1",
            (s["path_id"], s["position"])).fetchone()
        if not neighbor:
            return
        now = _now()
        # Swap through a temporary position to respect UNIQUE(path_id, position).
        conn.execute("UPDATE path_stages SET position = -1 WHERE id = ?", (s["id"],))
        conn.execute("UPDATE path_stages SET position = ?, updated_at = ? WHERE id = ?", (s["position"], now, neighbor["id"]))
        conn.execute("UPDATE path_stages SET position = ?, updated_at = ? WHERE id = ?", (neighbor["position"], now, s["id"]))
        _touch(conn, project_id)


def evaluate_stage_gate(project_id: str, stage_id: str) -> Dict[str, Any]:
    """Evaluate deterministic completion conditions for a stage (Milestone 2).

    A stage completion gate is satisfied if:
    1. All tasks belonging to the stage are completed.
    2. All deliverables belonging to the stage are accepted.
    3. The stage has at least one recorded completed task, accepted deliverable, or linked verified evidence.
    """
    conn = get_db_connection()
    try:
        tasks = [dict(r) for r in conn.execute(
            "SELECT id, title, status FROM tasks WHERE stage_id = ? ORDER BY created_at ASC",
            (stage_id,)
        ).fetchall()]

        deliverables = [dict(r) for r in conn.execute(
            "SELECT id, name, status, deliverable_type FROM deliverables WHERE stage_id = ? ORDER BY created_at ASC",
            (stage_id,)
        ).fetchall()]

        evidence = [dict(r) for r in conn.execute("""
            SELECT kr.id, km.title, km.trust_status FROM knowledge_relations kr
            JOIN knowledge_metadata km ON km.id = kr.knowledge_id
            WHERE kr.target_type = 'stage' AND kr.target_id = ? AND km.trust_status IN ('verified', 'accepted')
        """, (stage_id,)).fetchall()]

        blocking_reasons: List[str] = []

        # Check tasks
        incomplete_tasks = [t for t in tasks if t["status"] != "completed"]
        for t in incomplete_tasks:
            blocking_reasons.append(f"Task '{t['title']}' is not completed (current status: {t['status']}).")

        # Check deliverables
        unaccepted_delivs = [d for d in deliverables if d["status"] != "accepted"]
        for d in unaccepted_delivs:
            blocking_reasons.append(f"Deliverable '{d['name']}' is not accepted (current status: {d['status']}).")

        # Must have at least one verified task, deliverable, or verified evidence
        if len(tasks) == 0 and len(deliverables) == 0 and len(evidence) == 0:
            blocking_reasons.append("Stage has no associated tasks, deliverables, or linked verified evidence to prove completion.")

        is_satisfied = len(blocking_reasons) == 0
        gate_status = "gate_requirements_satisfied" if is_satisfied else "blocked"

        return {
            "stage_id": stage_id,
            "is_satisfied": is_satisfied,
            "gate_status": gate_status,
            "tasks": tasks,
            "deliverables": deliverables,
            "evidence": evidence,
            "blocking_reasons": blocking_reasons,
        }
    finally:
        conn.close()


def change_stage_status(project_id: str, stage_id: str, new_status: str) -> Dict[str, Any]:
    """Manual stage status changes among working statuses (planned, active, blocked).

    Formal completion must use advance_stage() which enforces completion gates.
    """
    if new_status not in STAGE_M1_STATUSES:
        raise ValidationError("Stage completion requires completion gates (use advance_stage). "
                              "Available manual statuses: planned, active, blocked.")
    with db_transaction() as conn:
        s = _load_stage(conn, project_id, stage_id)
        if s["status"] not in STAGE_M1_STATUSES:
            raise ValidationError(f"Stage is {s['status']} and cannot be changed manually.")
        path = _load_path(conn, project_id, s["path_id"])
        if new_status == "active" and path["status"] != "active":
            raise ValidationError("Only stages of the project's active Path can be started.")
        now = _now()
        started = s["started_at"] or (now if new_status == "active" else None)
        conn.execute("UPDATE path_stages SET status=?, started_at=?, updated_at=? WHERE id=?",
                     (new_status, started, now, stage_id))
        _touch(conn, project_id)
    return get_stage(stage_id)  # type: ignore[return-value]


def advance_stage(
    project_id: str,
    stage_id: str,
    user: str = "admin",
    rationale: str = ""
) -> Dict[str, Any]:
    """Advance a stage through its completion gate to 'completed', and auto-activate the next stage."""
    with db_transaction() as conn:
        s = _load_stage(conn, project_id, stage_id)
        if s["status"] == "completed":
            raise ValidationError("Stage is already completed.")

        path = _load_path(conn, project_id, s["path_id"])
        if path["status"] != "active":
            raise ValidationError("Only stages of the project's active Path can be advanced.")

        # Strict deterministic completion gate validation
        gate = evaluate_stage_gate(project_id, stage_id)
        if not gate["is_satisfied"]:
            raise ValidationError("Stage completion gate blocked: " + " ".join(gate["blocking_reasons"]))

        now = _now()
        started = s["started_at"] or now
        conn.execute("""
            UPDATE path_stages SET status = 'completed', started_at = ?, completed_at = ?, updated_at = ?
            WHERE id = ?
        """, (started, now, now, stage_id))

        decision_type = "stage_advancement"
        rat = rationale.strip() or f"Stage '{s['name']}' completion gate passed and stage completed."
        record_decision(
            conn,
            project_id,
            decision_type,
            rat,
            user,
            subject_id=stage_id,
            from_value=s["status"],
            to_value="completed"
        )

        # If completed, find if there is an immediately following planned stage on the same active Path
        next_stg = conn.execute("""
            SELECT id, name, status FROM path_stages
            WHERE path_id = ? AND position > ? AND status = 'planned'
            ORDER BY position ASC LIMIT 1
        """, (s["path_id"], s["position"])).fetchone()
        if next_stg:
            conn.execute("UPDATE path_stages SET status = 'active', started_at = ?, updated_at = ? WHERE id = ?",
                         (now, now, next_stg["id"]))
            record_decision(
                conn,
                project_id,
                "stage_status_change",
                f"Automatically activated following completion of stage '{s['name']}'",
                user,
                subject_id=next_stg["id"],
                from_value="planned",
                to_value="active"
            )

        _touch(conn, project_id)

    return get_stage(stage_id)  # type: ignore[return-value]


# --- Path Revisions (Milestone 2) -------------------------------------------

def revise_path(
    project_id: str,
    path_id: str,
    user: str,
    revision_reason: str,
    name: Optional[str] = None,
    rationale: Optional[str] = None,
    assumptions: Optional[str] = None,
    risks: Optional[str] = None,
    dependencies: Optional[str] = None,
    resource_requirements: Optional[str] = None,
    estimated_cost: Optional[str] = None,
    estimated_effort_hours: Optional[str] = None,
    expected_outcome: Optional[str] = None,
    forecast_confidence: Optional[str] = None
) -> Dict[str, Any]:
    """Revise a Path, snapshotting previous estimates and strategic assumptions into history."""
    clean_reason = _text(revision_reason, "Revision reason", required=True)
    with db_transaction() as conn:
        p = _load_path(conn, project_id, path_id)
        if p["status"] in PATH_TERMINAL:
            raise ValidationError(f"Cannot revise a {p['status']} Path.")

        now = _now()
        rev_count = conn.execute(
            "SELECT COUNT(*) FROM path_revisions WHERE path_id = ?", (path_id,)
        ).fetchone()[0]

        conn.execute("""
            INSERT INTO path_revisions (
                path_id, revision_number, name, rationale, assumptions, risks,
                dependencies, resource_requirements, estimated_cost, estimated_effort_hours,
                expected_outcome, forecast_confidence, revision_reason, revised_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            path_id, rev_count + 1, p["name"], p["rationale"], p["assumptions"], p["risks"],
            p["dependencies"], p["resource_requirements"], p["estimated_cost"],
            p["estimated_effort_hours"], p["expected_outcome"], p["forecast_confidence"],
            clean_reason, user, now
        ))

        new_name = _text(name, "Name", NAME_MAX, required=True) if name is not None else p["name"]
        new_rat = _text(rationale, "Strategic rationale", required=True) if rationale is not None else p["rationale"]
        new_assump = _text(assumptions, "Assumptions") if assumptions is not None else p["assumptions"]
        new_risks = _text(risks, "Risks") if risks is not None else p["risks"]
        new_deps = _text(dependencies, "Dependencies") if dependencies is not None else p["dependencies"]
        new_res = _text(resource_requirements, "Resource requirements") if resource_requirements is not None else p["resource_requirements"]
        new_cost = _number(estimated_cost, "Estimated cost") if estimated_cost is not None else p["estimated_cost"]
        new_effort = _number(estimated_effort_hours, "Estimated effort") if estimated_effort_hours is not None else p["estimated_effort_hours"]
        new_outcome = _text(expected_outcome, "Expected outcome") if expected_outcome is not None else p["expected_outcome"]
        new_conf = _number(forecast_confidence, "Forecast confidence", 0.0, 1.0) if forecast_confidence is not None else p["forecast_confidence"]

        conn.execute("""
            UPDATE project_paths SET
                name = ?, rationale = ?, assumptions = ?, risks = ?, dependencies = ?,
                resource_requirements = ?, estimated_cost = ?, estimated_effort_hours = ?,
                expected_outcome = ?, forecast_confidence = ?, updated_at = ?
            WHERE id = ?
        """, (new_name, new_rat, new_assump, new_risks, new_deps, new_res,
              new_cost, new_effort, new_outcome, new_conf, now, path_id))

        record_decision(
            conn,
            project_id,
            "path_revision",
            clean_reason,
            user,
            subject_id=path_id,
            from_value=f"rev_{rev_count}",
            to_value=f"rev_{rev_count + 1}"
        )
        _touch(conn, project_id)

    return get_path(path_id)  # type: ignore[return-value]


def list_path_revisions(path_id: str) -> List[Dict[str, Any]]:
    """Return all historical revisions of a Path."""
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM path_revisions WHERE path_id = ? ORDER BY revision_number DESC",
            (path_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()



def get_stage(stage_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        return _row(conn.execute("SELECT * FROM path_stages WHERE id = ?", (stage_id,)).fetchone())
    finally:
        conn.close()


def list_stages(path_id: str) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM path_stages WHERE path_id = ? ORDER BY position", (path_id,))]
    finally:
        conn.close()


def current_stage(path_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """First stage (by order) that is active, else first planned/blocked stage. None if no stages."""
    if not path_id:
        return None
    stages = list_stages(path_id)
    for wanted in ("active", "blocked", "planned"):
        for s in stages:
            if s["status"] == wanted:
                return s
    return None


# --- summaries -------------------------------------------------------------

def project_summary(project: Dict[str, Any]) -> Dict[str, Any]:
    """Real, persisted counts for dashboards. No derived 'progress percentage'."""
    conn = get_db_connection()
    try:
        pid = project["id"]
        task_counts = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, count(*) AS n FROM tasks WHERE project_id = ? GROUP BY status", (pid,))}
        path_counts = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, count(*) AS n FROM project_paths WHERE project_id = ? GROUP BY status", (pid,))}
        knowledge_count = conn.execute(
            "SELECT count(*) FROM knowledge_metadata WHERE project_id = ?", (pid,)).fetchone()[0]
        selected = _row(conn.execute("SELECT * FROM project_paths WHERE id = ?",
                                     (project["selected_path_id"],)).fetchone()) if project["selected_path_id"] else None
    finally:
        conn.close()
    return {
        "selected_path": selected,
        "current_stage": current_stage(selected["id"]) if selected else None,
        "task_counts": task_counts,
        "task_total": sum(task_counts.values()),
        "pending_approvals": task_counts.get("awaiting_approval", 0),
        "draft_tasks": task_counts.get("draft", 0),
        "path_counts": path_counts,
        "paths_awaiting_decision": path_counts.get("proposed", 0) + path_counts.get("under_review", 0),
        "knowledge_count": knowledge_count,
    }
