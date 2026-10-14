"""Typed knowledge metadata, provenance, trust review and explicit relations (Milestone 1).

Narrative content stays in Markdown files in the vault. SQLite holds structure:
type, origin, trust status, provenance, project association, supersession and
typed relationships. Trust status is a *review workflow state*, not a truth claim.
"""
import os
import re
import sqlite3
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings
from app.database import db_transaction, get_db_connection
from app.services.knowledge import search_knowledge, write_note_atomic
from app.services.projects import NotFound, ValidationError, NAME_MAX, TEXT_MAX

KNOWLEDGE_TYPES = ["source", "finding", "assumption", "decision", "procedure", "task_result",
                   "lesson_learned", "forecast_rationale", "observation", "project_brief", "general_reference"]
ORIGINS = ["human", "ai_generated", "external_source", "vps_extracted", "machine_measurement", "system"]
UNTRUSTED_ORIGINS = {"ai_generated", "external_source", "vps_extracted"}
TRUST_STATUSES = ["captured", "proposed", "verified", "accepted", "disputed", "superseded"]
TRUST_TRANSITIONS = {
    "captured": {"proposed", "verified", "disputed"},
    "proposed": {"captured", "verified", "disputed"},
    "verified": {"accepted", "disputed", "superseded"},
    "accepted": {"disputed", "superseded"},
    "disputed": {"proposed", "verified", "superseded"},
    "superseded": set(),
}
TARGET_TYPES = ["project", "path", "stage", "task", "knowledge"]
RELATION_TYPES = ["about", "supports", "challenges", "evidence_for", "rationale_for",
                  "result_of", "procedure_for", "derived_from", "related_to"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Optional[str], field: str, max_len: int = TEXT_MAX, required: bool = False) -> str:
    v = (value or "").strip()
    if required and not v:
        raise ValidationError(f"{field} is required.")
    if len(v) > max_len:
        raise ValidationError(f"{field} must be at most {max_len} characters.")
    return v


def _single_line(value: str) -> str:
    """Frontmatter values must not break out of their line."""
    return re.sub(r"[\r\n]+", " ", value).replace("---", "—").strip()


def _validate_url(url: str) -> Optional[str]:
    if not url:
        return None
    if not re.match(r"^https?://[^\s]+$", url, re.IGNORECASE) or len(url) > 2000:
        raise ValidationError("Source URL must be an http(s) URL.")
    return url


def _validate_date(value: str, field: str) -> Optional[str]:
    if not value:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValidationError(f"{field} must be YYYY-MM-DD.")


def _safe_note_path(rel_path: str) -> str:
    """Normalize a vault-relative path and refuse anything outside the vault."""
    rel = Path((rel_path or "").strip()).as_posix().lstrip("/\\")
    if not rel or not rel.endswith(".md"):
        raise ValidationError("Note path must be a .md file inside the knowledge vault.")
    full = (settings.knowledge_dir / rel).resolve()
    if not full.is_relative_to(settings.knowledge_dir.resolve()):
        raise ValidationError("Note path is outside the knowledge vault.")
    return full.relative_to(settings.knowledge_dir.resolve()).as_posix()


def _load_project(conn: sqlite3.Connection, project_id: str) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        raise NotFound("Project not found.")
    return dict(row)


def _common_fields(knowledge_type: str, origin: str, source_url: str, source_retrieved_at: str,
                   source_citation: str, tags: str) -> Dict[str, Any]:
    if knowledge_type not in KNOWLEDGE_TYPES:
        raise ValidationError("Invalid knowledge type.")
    if origin not in ORIGINS:
        raise ValidationError("Invalid origin.")
    url = _validate_url(_clean(source_url, "Source URL", 2000))
    retrieved = _validate_date(_clean(source_retrieved_at, "Retrieved date", 10), "Retrieved date")
    citation = _clean(source_citation, "Citation", 2000)
    if origin in ("external_source", "vps_extracted") and not (url or citation):
        raise ValidationError("External and VPS-extracted knowledge must record a source URL or citation.")
    return {"knowledge_type": knowledge_type, "origin": origin, "source_url": url,
            "source_retrieved_at": retrieved, "source_citation": citation or None,
            "tags": _single_line(_clean(tags, "Tags", 500))}


def create_project_note(project_id: str, title: str, knowledge_type: str, origin: str, body: str, author: str,
                        source_url: str = "", source_retrieved_at: str = "", source_citation: str = "",
                        tags: str = "") -> Dict[str, Any]:
    """Create a Markdown note in the project's vault folder and register its metadata (status: captured)."""
    clean_title = _single_line(_clean(title, "Title", NAME_MAX, required=True))
    common = _common_fields(knowledge_type, origin, source_url, source_retrieved_at, source_citation, tags)
    clean_body = _clean(body, "Content", 200_000)

    conn = get_db_connection()
    try:
        project = _load_project(conn, project_id)
    finally:
        conn.close()
    slug = re.sub(r"[^a-z0-9]+", "_", clean_title.lower()).strip("_")[:60] or "note"
    note_path = _safe_note_path(f"{project['knowledge_dir']}/{slug}.md")
    full_path = settings.knowledge_dir / note_path
    if full_path.exists():
        raise ValidationError("A note with that title already exists in this project. Choose a different title.")

    kn_id = f"kn-{uuid.uuid4().hex}"
    content = (f"---\ntitle: {clean_title}\nkn_id: {kn_id}\ntype: {knowledge_type}\n"
               f"project: {project_id}\n---\n\n{clean_body}\n")
    write_note_atomic(note_path, content)
    try:
        _insert_metadata(kn_id, note_path, clean_title, common, project_id, author)
    except Exception:
        # Keep vault and metadata consistent: remove the file we just created.
        try:
            os.remove(full_path)
        except OSError:
            pass
        raise
    return get_metadata(kn_id)  # type: ignore[return-value]


def register_existing_note(note_path: str, knowledge_type: str, origin: str, author: str,
                           project_id: Optional[str] = None, title: str = "", source_url: str = "",
                           source_retrieved_at: str = "", source_citation: str = "", tags: str = "") -> Dict[str, Any]:
    """Attach metadata to an existing vault note without moving or modifying the file."""
    rel = _safe_note_path(note_path)
    if not (settings.knowledge_dir / rel).is_file():
        raise ValidationError("That note does not exist in the knowledge vault.")
    common = _common_fields(knowledge_type, origin, source_url, source_retrieved_at, source_citation, tags)
    clean_title = _single_line(_clean(title, "Title", NAME_MAX)) or Path(rel).stem.replace("_", " ").title()
    if project_id:
        conn = get_db_connection()
        try:
            _load_project(conn, project_id)
        finally:
            conn.close()
    kn_id = f"kn-{uuid.uuid4().hex}"
    _insert_metadata(kn_id, rel, clean_title, common, project_id, author)
    return get_metadata(kn_id)  # type: ignore[return-value]


def _insert_metadata(kn_id: str, note_path: str, title: str, common: Dict[str, Any],
                     project_id: Optional[str], author: str):
    now = _now()
    try:
        with db_transaction() as conn:
            conn.execute("""
                INSERT INTO knowledge_metadata (id, note_path, title, knowledge_type, origin, trust_status,
                    source_url, source_retrieved_at, source_citation, project_id, author, tags, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'captured', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (kn_id, note_path, title, common["knowledge_type"], common["origin"], common["source_url"],
                 common["source_retrieved_at"], common["source_citation"], project_id, author,
                 common["tags"], now, now))
            if project_id:
                conn.execute("UPDATE projects SET last_activity_at = ? WHERE id = ?", (now, project_id))
    except sqlite3.IntegrityError as e:
        if "knowledge_metadata.note_path" in str(e):
            raise ValidationError("That note is already registered.")
        raise


def review_knowledge(kn_id: str, new_status: str, reviewer: str, note: str = "",
                     superseded_by: Optional[str] = None) -> Dict[str, Any]:
    """Human review action. The only way knowledge becomes verified, accepted, disputed or superseded."""
    if new_status not in TRUST_STATUSES:
        raise ValidationError("Invalid trust status.")
    review_note = _clean(note, "Review note", 2000)
    with db_transaction() as conn:
        row = conn.execute("SELECT * FROM knowledge_metadata WHERE id = ?", (kn_id,)).fetchone()
        if not row:
            raise NotFound("Knowledge entry not found.")
        cur = dict(row)
        if new_status not in TRUST_TRANSITIONS[cur["trust_status"]]:
            raise ValidationError(f"Cannot move knowledge from {cur['trust_status']} to {new_status}.")
        if new_status in ("disputed", "superseded") and not review_note:
            raise ValidationError("A review note is required when disputing or superseding knowledge.")
        replacement = None
        if new_status == "superseded":
            replacement = (superseded_by or "").strip()
            if not replacement or replacement == kn_id:
                raise ValidationError("Choose a different knowledge entry that supersedes this one.")
            rep = conn.execute("SELECT id, trust_status FROM knowledge_metadata WHERE id = ?", (replacement,)).fetchone()
            if not rep:
                raise ValidationError("The superseding knowledge entry does not exist.")
            if rep["trust_status"] == "superseded":
                raise ValidationError("The superseding entry is itself superseded.")
        now = _now()
        conn.execute("""
            UPDATE knowledge_metadata SET trust_status=?, superseded_by=?, reviewed_by=?, reviewed_at=?,
                review_note=?, review_count=review_count + 1,
                last_verified_at=CASE WHEN ? IN ('verified','accepted') THEN ? ELSE last_verified_at END,
                updated_at=? WHERE id=?""",
            (new_status, replacement, reviewer, now, review_note or None, new_status, now, now, kn_id))
    return get_metadata(kn_id)  # type: ignore[return-value]


def _target_project_id(conn: sqlite3.Connection, target_type: str, target_id: str) -> Optional[str]:
    """Return the owning project of a relation target, or raise if the target does not exist."""
    if target_type == "project":
        row = conn.execute("SELECT id FROM projects WHERE id = ?", (target_id,)).fetchone()
        pid = row["id"] if row else None
    elif target_type == "path":
        row = conn.execute("SELECT project_id FROM project_paths WHERE id = ?", (target_id,)).fetchone()
        pid = row["project_id"] if row else None
    elif target_type == "stage":
        row = conn.execute("""SELECT p.project_id FROM path_stages s JOIN project_paths p ON p.id = s.path_id
                              WHERE s.id = ?""", (target_id,)).fetchone()
        pid = row["project_id"] if row else None
    elif target_type == "task":
        row = conn.execute("SELECT project_id FROM tasks WHERE id = ?", (target_id,)).fetchone()
        if not row:
            raise ValidationError("Relation target task does not exist.")
        return row["project_id"]
    elif target_type == "knowledge":
        row = conn.execute("SELECT project_id FROM knowledge_metadata WHERE id = ?", (target_id,)).fetchone()
        if not row:
            raise ValidationError("Relation target knowledge entry does not exist.")
        return row["project_id"]
    else:
        raise ValidationError("Invalid relation target type.")
    if not row:
        raise ValidationError(f"Relation target {target_type} does not exist.")
    return pid


def add_relation(kn_id: str, target_type: str, target_id: str, relation_type: str, user: str,
                 note: str = "", scope_project_id: Optional[str] = None) -> None:
    """Link knowledge to a project object. With a scope, the *target* must belong to that project.

    The knowledge entry itself may come from another project (cross-project reuse); its original
    project association and provenance are never changed by linking.
    """
    if target_type not in TARGET_TYPES:
        raise ValidationError("Invalid relation target type.")
    if relation_type not in RELATION_TYPES:
        raise ValidationError("Invalid relation type.")
    target_id = (target_id or "").strip()
    if target_type == "knowledge" and target_id == kn_id:
        raise ValidationError("A knowledge entry cannot relate to itself.")
    with db_transaction() as conn:
        if not conn.execute("SELECT 1 FROM knowledge_metadata WHERE id = ?", (kn_id,)).fetchone():
            raise NotFound("Knowledge entry not found.")
        owner = _target_project_id(conn, target_type, target_id)
        if scope_project_id and owner != scope_project_id:
            raise ValidationError("Relation target does not belong to this project.")
        try:
            conn.execute("""
                INSERT INTO knowledge_relations (knowledge_id, target_type, target_id, relation_type, note,
                                                 created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (kn_id, target_type, target_id, relation_type, _clean(note, "Relation note", 1000), user, _now()))
        except sqlite3.IntegrityError as e:
            if "UNIQUE" in str(e):
                raise ValidationError("That relation already exists.")
            raise


def get_metadata(kn_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT * FROM knowledge_metadata WHERE id = ?", (kn_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_metadata_by_path(note_path: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT * FROM knowledge_metadata WHERE note_path = ?", (note_path,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_project_knowledge(project_id: str) -> List[Dict[str, Any]]:
    """Knowledge owned by the project plus knowledge linked to any object inside it (reuse).

    Each entry carries `link_kind`: 'owned' or 'linked', and `relations` within this project.
    """
    conn = get_db_connection()
    try:
        rows = conn.execute("""
            WITH targets(target_type, target_id) AS (
                SELECT 'project', ?
                UNION ALL SELECT 'path', id FROM project_paths WHERE project_id = ?
                UNION ALL SELECT 'stage', s.id FROM path_stages s JOIN project_paths p ON p.id = s.path_id
                          WHERE p.project_id = ?
                UNION ALL SELECT 'task', id FROM tasks WHERE project_id = ?
            )
            SELECT km.*, CASE WHEN km.project_id = ? THEN 'owned' ELSE 'linked' END AS link_kind
            FROM knowledge_metadata km
            WHERE km.project_id = ?
               OR km.id IN (SELECT r.knowledge_id FROM knowledge_relations r
                            JOIN targets t ON t.target_type = r.target_type AND t.target_id = r.target_id)
            ORDER BY CASE km.trust_status WHEN 'accepted' THEN 0 WHEN 'verified' THEN 1 WHEN 'proposed' THEN 2
                     WHEN 'captured' THEN 3 WHEN 'disputed' THEN 4 ELSE 5 END, km.updated_at DESC
        """, (project_id,) * 6).fetchall()
        entries = [dict(r) for r in rows]
        for e in entries:
            e["relations"] = [dict(r) for r in conn.execute(
                "SELECT * FROM knowledge_relations WHERE knowledge_id = ? ORDER BY created_at", (e["id"],))]
            e["untrusted_origin"] = e["origin"] in UNTRUSTED_ORIGINS
        return entries
    finally:
        conn.close()


def list_relations_for_target(target_type: str, target_id: str) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        return [dict(r) for r in conn.execute("""
            SELECT r.*, km.title, km.trust_status, km.knowledge_type, km.origin, km.note_path
            FROM knowledge_relations r JOIN knowledge_metadata km ON km.id = r.knowledge_id
            WHERE r.target_type = ? AND r.target_id = ? ORDER BY r.created_at""", (target_type, target_id))]
    finally:
        conn.close()


def search_project_knowledge(project_id: str, query: str) -> List[Dict[str, Any]]:
    """FTS5 search restricted to the project's owned and linked knowledge, annotated with trust labels."""
    entries = {e["note_path"]: e for e in list_project_knowledge(project_id)}
    if not entries:
        return []
    results = []
    for hit in search_knowledge(query, restrict_paths=list(entries.keys())):
        meta = entries.get(hit["path"])
        if meta:
            results.append({**hit, "kn_id": meta["id"], "trust_status": meta["trust_status"],
                            "knowledge_type": meta["knowledge_type"], "origin": meta["origin"],
                            "link_kind": meta["link_kind"]})
    return results


def promote_to_collective(kn_id: str, user: str, rationale: str = "") -> Dict[str, Any]:
    """Promote verified/accepted project knowledge into collective memory.

    Only knowledge in 'verified' or 'accepted' trust_status may be promoted.
    Records a 'knowledge_promotion' decision in project_decisions.
    """
    with db_transaction() as conn:
        row = conn.execute("SELECT * FROM knowledge_metadata WHERE id = ?", (kn_id,)).fetchone()
        if not row:
            raise NotFound("Knowledge entry not found.")
        meta = dict(row)
        if meta["trust_status"] not in ("verified", "accepted"):
            raise ValidationError("Only verified or accepted knowledge can be promoted to collective memory.")
        if meta.get("is_collective"):
            return meta

        now = _now()
        conn.execute("""
            UPDATE knowledge_metadata
            SET is_collective = 1, promoted_by = ?, promoted_at = ?, updated_at = ?
            WHERE id = ?
        """, (user, now, now, kn_id))

        if meta.get("project_id"):
            from app.services.projects import record_decision
            record_decision(
                conn=conn,
                project_id=meta["project_id"],
                decision_type="knowledge_promotion",
                rationale=rationale or f"Promoted knowledge '{meta['title']}' to collective memory.",
                decided_by=user,
                subject_id=kn_id,
                from_value="project",
                to_value="collective",
                evidence_knowledge_id=kn_id,
            )

        updated = conn.execute("SELECT * FROM knowledge_metadata WHERE id = ?", (kn_id,)).fetchone()
        return dict(updated)


def list_collective_knowledge() -> List[Dict[str, Any]]:
    """List all knowledge promoted to collective memory across all projects."""
    conn = get_db_connection()
    try:
        rows = conn.execute("""
            SELECT km.*, p.name AS project_name
            FROM knowledge_metadata km
            LEFT JOIN projects p ON p.id = km.project_id
            WHERE km.is_collective = 1
            ORDER BY km.promoted_at DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
