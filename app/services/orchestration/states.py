"""Explicit orchestration lifecycle: valid transitions in code, every transition audited, idempotent."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.database import db_transaction
from app.services.orchestration.common import (
    InvalidTransition, StaleState, get_run, log_event, now_iso,
)

logger = logging.getLogger("qc.orchestrator.states")

STATES = (
    "DRAFT", "ANALYZING", "AWAITING_CLARIFICATION", "PLANNING", "AWAITING_PLAN_APPROVAL", "READY",
    "RUNNING", "EVALUATING", "COMPLETED", "PAUSED", "BLOCKED", "REPLANNING", "FAILED", "CANCELLED",
)
TERMINAL = {"COMPLETED", "CANCELLED"}
EXECUTING = {"RUNNING", "EVALUATING"}

TRANSITIONS: Dict[str, set] = {
    "DRAFT": {"ANALYZING", "PLANNING", "CANCELLED"},
    "ANALYZING": {"AWAITING_CLARIFICATION", "PLANNING", "DRAFT", "CANCELLED"},
    "AWAITING_CLARIFICATION": {"ANALYZING", "PLANNING", "DRAFT", "CANCELLED"},
    "PLANNING": {"AWAITING_PLAN_APPROVAL", "DRAFT", "FAILED", "CANCELLED"},
    "AWAITING_PLAN_APPROVAL": {"READY", "PLANNING", "DRAFT", "CANCELLED"},
    "READY": {"RUNNING", "PLANNING", "PAUSED", "CANCELLED"},
    "RUNNING": {"EVALUATING", "PAUSED", "BLOCKED", "REPLANNING", "FAILED", "CANCELLED"},
    "EVALUATING": {"COMPLETED", "RUNNING", "BLOCKED", "REPLANNING", "FAILED", "CANCELLED"},
    "PAUSED": {"RUNNING", "READY", "CANCELLED"},
    "BLOCKED": {"RUNNING", "REPLANNING", "FAILED", "CANCELLED"},
    "REPLANNING": {"AWAITING_PLAN_APPROVAL", "RUNNING", "BLOCKED", "FAILED", "CANCELLED"},
    "FAILED": {"REPLANNING", "PLANNING", "CANCELLED"},
    "COMPLETED": set(),
    "CANCELLED": set(),
}


def can_transition(old: str, new: str) -> bool:
    return new in TRANSITIONS.get(old, set())


def transition(run_id: str, new_state: str, event: str, actor: str, reason: str = "",
               expected_state: Optional[str] = None, task_id: Optional[str] = None,
               stage_id: Optional[str] = None, set_fields: Optional[Dict[str, Any]] = None,
               conn=None, detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Move a run to ``new_state``.

    * Invalid transitions raise :class:`InvalidTransition` and change nothing.
    * ``expected_state`` gives optimistic idempotency: if the run is no longer in that state a
      :class:`StaleState` is raised (callers treat it as "someone else already did this").
    * Re-requesting the state the run is already in is a no-op (no duplicate audit event).
    The project-level status mirror is synchronised by :func:`sync_project_status` after commit.
    """
    if new_state not in STATES:
        raise InvalidTransition(f"Unknown orchestration state '{new_state}'.")

    def _do(c):
        run = get_run(run_id, c)
        old = run["state"]
        if expected_state is not None and old != expected_state:
            if old == new_state:
                return run, False
            raise StaleState(f"Run is '{old}', expected '{expected_state}'.")
        if old == new_state:
            return run, False
        if not can_transition(old, new_state):
            raise InvalidTransition(f"Cannot move from {old} to {new_state}.")
        fields = dict(set_fields or {})
        if new_state == "PAUSED":
            fields.setdefault("resume_state", old if old in ("RUNNING", "READY", "EVALUATING") else "RUNNING")
        if new_state == "COMPLETED":
            fields["completed_at"] = now_iso()
        if new_state == "RUNNING" and not run.get("started_at"):
            fields["started_at"] = now_iso()
        sets = ["state = ?", "version = version + 1", "updated_at = ?"]
        params = [new_state, now_iso()]
        for k, v in fields.items():
            sets.append(f"{k} = ?")
            params.append(v)
        params.extend([run_id, run["version"]])
        cur = c.execute(f"UPDATE orchestration_runs SET {', '.join(sets)} WHERE id = ? AND version = ?", params)
        if cur.rowcount != 1:
            raise StaleState("Run was modified concurrently.")
        log_event(c, run, event, actor, reason, task_id=task_id, stage_id=stage_id,
                  previous_state=old, new_state=new_state, detail=detail)
        return get_run(run_id, c), True

    if conn is not None:
        result, changed = _do(conn)
        return result
    with db_transaction() as c:
        result, changed = _do(c)
    if changed:
        sync_project_status(result, actor, reason)
    return result


def sync_project_status(run: Dict[str, Any], actor: str, reason: str) -> None:
    """Mirror terminal orchestration states onto the existing project lifecycle (after commit)."""
    from app.services import projects as P
    target = {"COMPLETED": "completed", "CANCELLED": "cancelled"}.get(run["state"])
    if not target:
        return
    try:
        proj = P.get_project(run["project_id"])
        if proj and proj["status"] != target:
            P.change_project_status(run["project_id"], target, actor, reason or f"Orchestration {run['state']}")
    except Exception as e:  # never let a mirror failure corrupt orchestration state
        logger.warning("Project status mirror failed for %s: %s", run["project_id"], e)
        from app.services.orchestration.common import record_event
        record_event(run["id"], "project_status_sync_failed", "system", str(e)[:500])
