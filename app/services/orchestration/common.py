"""Shared helpers for the Project Orchestrator (additive layer over projects/tasks/workers)."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.database import db_transaction, get_db_connection
from app.services.projects import NotFound, ValidationError  # re-exported error types


class OrchestrationError(ValidationError):
    """Invalid orchestration request (maps to HTTP 400/409)."""


class InvalidTransition(OrchestrationError):
    """A state transition not allowed by the state machine."""


class StaleState(OrchestrationError):
    """The run changed since the caller observed it (optimistic concurrency); safe to ignore/retry."""


def now_dt() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_dt().isoformat()


def iso_in(seconds: float) -> str:
    return (now_dt() + timedelta(seconds=seconds)).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def jdump(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)


def jload(text: Optional[str], default: Any = None) -> Any:
    if not text:
        return {} if default is None else default
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return {} if default is None else default


def parse_ts(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(ts)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def get_run(run_id: str, conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    own = conn is None
    c = conn or get_db_connection()
    try:
        row = c.execute("SELECT * FROM orchestration_runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            raise NotFound("Orchestration run not found.")
        return dict(row)
    finally:
        if own:
            c.close()


def get_run_for_project(project_id: str) -> Optional[Dict[str, Any]]:
    c = get_db_connection()
    try:
        row = c.execute("SELECT * FROM orchestration_runs WHERE project_id = ?", (project_id,)).fetchone()
        return dict(row) if row else None
    finally:
        c.close()


def log_event(conn: sqlite3.Connection, run: Dict[str, Any], event_type: str, actor: str, reason: str = "",
              task_id: Optional[str] = None, stage_id: Optional[str] = None,
              previous_state: Optional[str] = None, new_state: Optional[str] = None,
              detail: Optional[Dict[str, Any]] = None) -> None:
    """Append to the immutable audit log (must be called inside the caller's transaction)."""
    conn.execute(
        """INSERT INTO orchestration_events (run_id, project_id, ts, event_type, previous_state, new_state,
               actor, reason, task_id, stage_id, detail_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (run["id"], run["project_id"], now_iso(), event_type, previous_state, new_state, actor,
         (reason or "")[:2000], task_id, stage_id, jdump(detail or {})))


def record_event(run_id: str, event_type: str, actor: str, reason: str = "", **kw: Any) -> None:
    with db_transaction() as conn:
        run = get_run(run_id, conn)
        log_event(conn, run, event_type, actor, reason, **kw)


def list_events(project_id: str, after_id: int = 0, limit: int = 200) -> List[Dict[str, Any]]:
    c = get_db_connection()
    try:
        rows = c.execute("SELECT * FROM orchestration_events WHERE project_id = ? AND id > ? ORDER BY id ASC LIMIT ?",
                         (project_id, after_id, min(max(limit, 1), 1000))).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["detail"] = jload(d.pop("detail_json"))
            out.append(d)
        return out
    finally:
        c.close()
