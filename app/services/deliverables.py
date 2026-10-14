"""Deliverables tracking, verification, artifact references and acceptance workflow (Milestone 2).

Task completion does not automatically mean Deliverable acceptance. Acceptance requires
defined criteria, verification results, and recorded human decision.
"""
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.database import db_transaction, get_db_connection
from app.services.projects import NotFound, ValidationError, NAME_MAX, TEXT_MAX, record_decision

DELIVERABLE_TYPES = [
    "document", "dataset", "report", "software_artifact", "configuration",
    "research_result", "published_material", "completed_workflow", "other"
]

DELIVERABLE_STATUSES = [
    "planned", "in_progress", "submitted", "changes_requested", "accepted", "rejected"
]

DELIVERABLE_TRANSITIONS = {
    "planned": {"in_progress", "submitted", "rejected"},
    "in_progress": {"submitted", "rejected", "planned"},
    "submitted": {"changes_requested", "accepted", "rejected"},
    "changes_requested": {"in_progress", "submitted", "rejected"},
    "accepted": set(),  # terminal
    "rejected": {"in_progress", "planned"},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(val: Optional[str], field: str, max_len: int = TEXT_MAX, required: bool = False) -> str:
    v = (val or "").strip()
    if required and not v:
        raise ValidationError(f"{field} is required.")
    if len(v) > max_len:
        raise ValidationError(f"{field} must be at most {max_len} characters.")
    return v


def _load_deliverable(conn: sqlite3.Connection, deliverable_id: str) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM deliverables WHERE id = ?", (deliverable_id,)).fetchone()
    if not row:
        raise NotFound("Deliverable not found.")
    return dict(row)


def create_deliverable(
    project_id: str,
    name: str,
    deliverable_type: str,
    created_by: str,
    path_id: Optional[str] = None,
    stage_id: Optional[str] = None,
    description: str = "",
    acceptance_criteria: str = "",
    due_date: Optional[str] = None
) -> Dict[str, Any]:
    """Create a new planned deliverable associated with a project, and optionally Path/stage."""
    clean_name = _clean(name, "Name", NAME_MAX, required=True)
    if deliverable_type not in DELIVERABLE_TYPES:
        raise ValidationError(f"Invalid deliverable type: '{deliverable_type}'.")
    clean_desc = _clean(description, "Description")
    clean_crit = _clean(acceptance_criteria, "Acceptance criteria")
    clean_due = _clean(due_date, "Due date", 20) or None

    with db_transaction() as conn:
        proj = conn.execute("SELECT id, status FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not proj:
            raise NotFound("Project not found.")

        # Stage and Path consistency validation
        if stage_id:
            stg = conn.execute("SELECT id, path_id FROM path_stages WHERE id = ?", (stage_id,)).fetchone()
            if not stg:
                raise NotFound("Stage not found.")
            if path_id and path_id != stg["path_id"]:
                raise ValidationError("Specified Path does not match the stage's parent Path.")
            path_id = stg["path_id"]

        if path_id:
            pth = conn.execute("SELECT id, project_id FROM project_paths WHERE id = ?", (path_id,)).fetchone()
            if not pth or pth["project_id"] != project_id:
                raise ValidationError("Path does not belong to this project.")

        deliv_id = f"del-{uuid.uuid4().hex}"
        now = _now()
        conn.execute("""
            INSERT INTO deliverables (
                id, project_id, path_id, stage_id, name, description, deliverable_type,
                acceptance_criteria, due_date, status, artifact_references, verification_results,
                acceptance_decision, accepted_by, accepted_at, created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned', '', '', '', NULL, NULL, ?, ?, ?)
        """, (deliv_id, project_id, path_id, stage_id, clean_name, clean_desc, deliverable_type,
              clean_crit, clean_due, created_by, now, now))

        conn.execute("UPDATE projects SET last_activity_at = ? WHERE id = ?", (now, project_id))

    return get_deliverable(deliv_id)  # type: ignore[return-value]


def update_deliverable(
    deliverable_id: str,
    user: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    acceptance_criteria: Optional[str] = None,
    due_date: Optional[str] = None,
    artifact_references: Optional[str] = None,
    stage_id: Optional[str] = None
) -> Dict[str, Any]:
    """Update metadata or artifact references while in draft/in_progress/changes_requested state."""
    with db_transaction() as conn:
        cur = _load_deliverable(conn, deliverable_id)
        if cur["status"] in ("accepted", "rejected"):
            raise ValidationError(f"Cannot edit deliverable in '{cur['status']}' state.")

        updates = []
        params = []
        if name is not None:
            updates.append("name = ?")
            params.append(_clean(name, "Name", NAME_MAX, required=True))
        if description is not None:
            updates.append("description = ?")
            params.append(_clean(description, "Description"))
        if acceptance_criteria is not None:
            updates.append("acceptance_criteria = ?")
            params.append(_clean(acceptance_criteria, "Acceptance criteria"))
        if due_date is not None:
            updates.append("due_date = ?")
            params.append(_clean(due_date, "Due date", 20) or None)
        if artifact_references is not None:
            updates.append("artifact_references = ?")
            params.append(_clean(artifact_references, "Artifact references"))
        if stage_id is not None:
            if stage_id:
                stg = conn.execute("SELECT id, path_id FROM path_stages WHERE id = ?", (stage_id,)).fetchone()
                if not stg:
                    raise NotFound("Stage not found.")
                pth = conn.execute("SELECT project_id FROM project_paths WHERE id = ?", (stg["path_id"],)).fetchone()
                if not pth or pth["project_id"] != cur["project_id"]:
                    raise ValidationError("Stage does not belong to this project.")
                updates.append("stage_id = ?")
                params.append(stage_id)
                updates.append("path_id = ?")
                params.append(stg["path_id"])
            else:
                updates.append("stage_id = NULL")
                updates.append("path_id = NULL")

        if not updates:
            return cur

        now = _now()
        updates.append("updated_at = ?")
        params.append(now)
        params.append(deliverable_id)

        conn.execute(f"UPDATE deliverables SET {', '.join(updates)} WHERE id = ?", params)
        conn.execute("UPDATE projects SET last_activity_at = ? WHERE id = ?", (now, cur["project_id"]))

    return get_deliverable(deliverable_id)  # type: ignore[return-value]


def submit_deliverable(
    deliverable_id: str,
    user: str,
    artifact_references: str,
    verification_results: str = ""
) -> Dict[str, Any]:
    """Submit a deliverable for formal review with artifact evidence."""
    clean_artifacts = _clean(artifact_references, "Artifact references", required=True)
    clean_verif = _clean(verification_results, "Verification results")

    with db_transaction() as conn:
        cur = _load_deliverable(conn, deliverable_id)
        if cur["status"] not in ("planned", "in_progress", "changes_requested"):
            raise ValidationError(f"Cannot submit deliverable from '{cur['status']}' state.")

        now = _now()
        conn.execute("""
            UPDATE deliverables SET status = 'submitted', artifact_references = ?,
                   verification_results = ?, updated_at = ?
            WHERE id = ?
        """, (clean_artifacts, clean_verif, now, deliverable_id))

        # Record revision history
        rev_count = conn.execute(
            "SELECT COUNT(*) FROM deliverable_revisions WHERE deliverable_id = ?",
            (deliverable_id,)
        ).fetchone()[0]
        conn.execute("""
            INSERT INTO deliverable_revisions (
                deliverable_id, revision_number, status, artifact_references,
                verification_results, decision_rationale, decided_by, created_at
            ) VALUES (?, ?, 'submitted', ?, ?, 'Submitted for review', ?, ?)
        """, (deliverable_id, rev_count + 1, clean_artifacts, clean_verif, user, now))

        conn.execute("UPDATE projects SET last_activity_at = ? WHERE id = ?", (now, cur["project_id"]))

    return get_deliverable(deliverable_id)  # type: ignore[return-value]


def review_deliverable(
    deliverable_id: str,
    new_status: str,
    reviewer: str,
    rationale: str,
    verification_results: Optional[str] = None
) -> Dict[str, Any]:
    """Human review decision for a submitted or in-review deliverable."""
    if new_status not in DELIVERABLE_STATUSES:
        raise ValidationError(f"Invalid deliverable status: '{new_status}'.")
    clean_rat = _clean(rationale, "Decision rationale", required=True)

    with db_transaction() as conn:
        cur = _load_deliverable(conn, deliverable_id)
        if new_status not in DELIVERABLE_TRANSITIONS.get(cur["status"], set()):
            raise ValidationError(f"Cannot transition deliverable from '{cur['status']}' to '{new_status}'.")

        now = _now()
        verif = _clean(verification_results, "Verification results") if verification_results is not None else cur["verification_results"]

        accepted_by = reviewer if new_status == "accepted" else None
        accepted_at = now if new_status == "accepted" else None

        conn.execute("""
            UPDATE deliverables SET status = ?, verification_results = ?, acceptance_decision = ?,
                   accepted_by = ?, accepted_at = ?, updated_at = ?
            WHERE id = ?
        """, (new_status, verif, clean_rat, accepted_by, accepted_at, now, deliverable_id))

        # Snapshot into revision history
        rev_count = conn.execute(
            "SELECT COUNT(*) FROM deliverable_revisions WHERE deliverable_id = ?",
            (deliverable_id,)
        ).fetchone()[0]
        conn.execute("""
            INSERT INTO deliverable_revisions (
                deliverable_id, revision_number, status, artifact_references,
                verification_results, decision_rationale, decided_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (deliverable_id, rev_count + 1, new_status, cur["artifact_references"], verif, clean_rat, reviewer, now))

        # Append to project decisions
        record_decision(
            conn,
            cur["project_id"],
            "deliverable_acceptance",
            clean_rat,
            reviewer,
            subject_id=deliverable_id,
            from_value=cur["status"],
            to_value=new_status
        )
        conn.execute("UPDATE projects SET last_activity_at = ? WHERE id = ?", (now, cur["project_id"]))

    return get_deliverable(deliverable_id)  # type: ignore[return-value]


def get_deliverable(deliverable_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT * FROM deliverables WHERE id = ?", (deliverable_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_deliverables(project_id: str, stage_id: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        if stage_id:
            rows = conn.execute(
                "SELECT * FROM deliverables WHERE project_id = ? AND stage_id = ? ORDER BY created_at ASC",
                (project_id, stage_id)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM deliverables WHERE project_id = ? ORDER BY created_at ASC",
                (project_id,)
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_deliverable_revisions(deliverable_id: str) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM deliverable_revisions WHERE deliverable_id = ? ORDER BY revision_number DESC",
            (deliverable_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
