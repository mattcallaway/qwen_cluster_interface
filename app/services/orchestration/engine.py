"""Persistent orchestration engine: durable, idempotent, single-scheduler tick loop over the run state machine."""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from app.database import db_transaction, get_db_connection
from app.services import deliverables as D
from app.services import projects as P
from app.services import tasks as T
from app.services.orchestration import (
    approval_manager, artifact_manager, context_manager, evaluator, model_router, planner, recovery,
    reporting, script_library, states, telemetry,
)
from app.services.orchestration.common import (
    NotFound, OrchestrationError, ValidationError, get_run, get_run_for_project, iso_in, jdump, jload, log_event,
    new_id, now_dt, now_iso, parse_ts, record_event,
)

logger = logging.getLogger("qc.orchestrator.engine")

HOLDER = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
LOCK_NAME = "orchestrator"
LOCK_TTL_S = 30
TICK_INTERVAL_S = 3.0
WAIT_FOR_WORKER_LIMIT_S = 600
INFLIGHT_GRACE_S = 90
MAX_INTAKE_FIELD = 4000
ACTIVE_STATES = ("PLANNING", "REPLANNING", "RUNNING", "EVALUATING", "PAUSED")


# --- single scheduler guard ------------------------------------------------------------------------------

def acquire_lock(holder: str = HOLDER, ttl: int = LOCK_TTL_S) -> bool:
    """DB lease so that two scheduler instances can never dispatch the same task."""
    with db_transaction() as c:
        row = c.execute("SELECT * FROM orchestration_scheduler_lock WHERE name=?", (LOCK_NAME,)).fetchone()
        now = now_dt()
        if row is None:
            c.execute("INSERT INTO orchestration_scheduler_lock (name, holder, expires_at, updated_at) VALUES (?,?,?,?)",
                      (LOCK_NAME, holder, iso_in(ttl), now_iso()))
            return True
        exp = parse_ts(row["expires_at"])
        if row["holder"] == holder or exp is None or exp < now:
            c.execute("UPDATE orchestration_scheduler_lock SET holder=?, expires_at=?, updated_at=? WHERE name=?",
                      (holder, iso_in(ttl), now_iso(), LOCK_NAME))
            return True
        return False


def release_lock(holder: str = HOLDER) -> None:
    with db_transaction() as c:
        c.execute("DELETE FROM orchestration_scheduler_lock WHERE name=? AND holder=?", (LOCK_NAME, holder))


# --- run lifecycle API -------------------------------------------------------------------------------------

INTAKE_FIELDS = ("objective", "deliverables", "constraints", "resources", "deadline", "priority", "success_criteria",
                 "planner_model")


def _clean_intake(intake: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k in INTAKE_FIELDS:
        v = intake.get(k)
        if v is None:
            continue
        v = str(v).strip()
        if len(v) > MAX_INTAKE_FIELD:
            raise ValidationError(f"{k} must be at most {MAX_INTAKE_FIELD} characters.")
        if v:
            out[k] = v
    return out


def create_run(project_id: str, actor: str, intake: Dict[str, Any], autonomy_level: int = 1) -> Dict[str, Any]:
    if not P.get_project(project_id):
        raise NotFound("Project not found.")
    if not isinstance(autonomy_level, int) or not 0 <= autonomy_level <= 3:
        raise ValidationError("autonomy_level must be 0..3.")
    clean = _clean_intake(intake)
    if not clean.get("objective"):
        raise ValidationError("An objective is required.")
    if get_run_for_project(project_id):
        raise OrchestrationError("This project already has an orchestration run.")
    rid = new_id("run")
    with db_transaction() as c:
        c.execute("""INSERT INTO orchestration_runs (id, project_id, state, autonomy_level, intake_json, created_by, created_at, updated_at)
                     VALUES (?,?, 'DRAFT', ?, ?, ?, ?, ?)""", (rid, project_id, autonomy_level, jdump(clean), actor, now_iso(), now_iso()))
        telemetry.ensure_budget(c, rid)
        run = get_run(rid, c)
        log_event(c, run, "run_created", actor, f"autonomy level {autonomy_level}", new_state="DRAFT")
    return get_run(rid)


def create_project_with_run(name: str, actor: str, intake: Dict[str, Any], autonomy_level: int = 1) -> Dict[str, Any]:
    clean = _clean_intake(intake)
    fields: Dict[str, Any] = {"objective": clean.get("objective", ""), "description": clean.get("objective", "")[:1000],
                              "constraints_text": clean.get("constraints", ""), "success_criteria": clean.get("success_criteria", "")}
    if clean.get("deadline"):
        fields["target_date"] = clean["deadline"]
    if clean.get("priority"):
        fields["priority"] = clean["priority"]
    project = P.create_project(name, actor, **fields)
    run = create_run(project["id"], actor, intake, autonomy_level)
    return {"project": project, "run": run}


def _require_run(project_id: str) -> Dict[str, Any]:
    run = get_run_for_project(project_id)
    if not run:
        raise NotFound("This project has no orchestration run.")
    return run


def set_autonomy(run_id: str, level: int, actor: str) -> Dict[str, Any]:
    if not isinstance(level, int) or not 0 <= level <= 3:
        raise ValidationError("autonomy_level must be 0..3.")
    with db_transaction() as c:
        run = get_run(run_id, c)
        if run["state"] in states.TERMINAL:
            raise OrchestrationError("Run is finished.")
        c.execute("UPDATE orchestration_runs SET autonomy_level=?, updated_at=? WHERE id=?", (level, now_iso(), run_id))
        log_event(c, run, "autonomy_changed", actor, f"{run['autonomy_level']} -> {level}")
    return get_run(run_id)


def start_run(run_id: str, actor: str) -> Dict[str, Any]:
    run = get_run(run_id)
    if run["state"] != "READY":
        raise OrchestrationError(f"Run must be READY to start (it is {run['state']}); approve a plan first.")
    return states.transition(run_id, "RUNNING", "execution_started", actor, "user started execution", expected_state="READY")


def pause_run(run_id: str, actor: str, reason: str = "") -> Dict[str, Any]:
    return states.transition(run_id, "PAUSED", "paused", actor, reason or "paused by user")


def resume_run(run_id: str, actor: str) -> Dict[str, Any]:
    run = get_run(run_id)
    if run["state"] != "PAUSED":
        raise OrchestrationError("Run is not paused.")
    target = run["resume_state"] if run["resume_state"] in ("RUNNING", "READY", "EVALUATING") else "RUNNING"
    return states.transition(run_id, target, "resumed", actor, "resumed", expected_state="PAUSED")


def cancel_run(run_id: str, actor: str, reason: str = "") -> Dict[str, Any]:
    run = get_run(run_id)
    if run["state"] in states.TERMINAL:
        return run
    c = get_db_connection()
    try:
        inflight = [dict(r) for r in c.execute(
            "SELECT id, current_task_id FROM orchestration_tasks WHERE run_id=? AND state='dispatched'", (run_id,))]
        reqs = [r["id"] for r in c.execute("SELECT id FROM model_inference_requests WHERE run_id=? AND status='submitted'", (run_id,))]
    finally:
        c.close()
    for r in reqs:
        model_router.get_provider().cancel(r)
    for ot in inflight:
        if ot["current_task_id"]:
            try:
                T.cancel_task(ot["current_task_id"])
            except Exception as e:
                logger.info("cancel_task failed: %s", e)
    with db_transaction() as c:
        c.execute("UPDATE orchestration_tasks SET state='cancelled', updated_at=? WHERE run_id=? AND state IN ('pending','dispatched','blocked','failed')",
                  (now_iso(), run_id))
        c.execute("UPDATE orchestration_approvals SET status='superseded' WHERE run_id=? AND status='pending'", (run_id,))
    return states.transition(run_id, "CANCELLED", "cancelled", actor, reason or "cancelled by user")


def request_replan(run_id: str, actor: str, reason: str = "", mode: str = "auto") -> Dict[str, Any]:
    run = get_run(run_id)
    if run["state"] not in ("RUNNING", "BLOCKED", "EVALUATING", "FAILED"):
        raise OrchestrationError(f"Replanning is not available while the run is {run['state']} (pause/resume first if paused).")
    states.transition(run_id, "REPLANNING", "replan_requested", actor, reason or "replan requested")
    return planner.generate_plan(run_id, actor, mode=mode)


def retry_task(run_id: str, orch_task_id: str, actor: str) -> Dict[str, Any]:
    with db_transaction() as c:
        run = get_run(run_id, c)
        ot = c.execute("SELECT * FROM orchestration_tasks WHERE id=? AND run_id=?", (orch_task_id, run_id)).fetchone()
        if not ot:
            raise NotFound("Task not found.")
        if ot["state"] not in ("failed", "blocked"):
            raise OrchestrationError(f"Only failed or blocked tasks can be retried (task is {ot['state']}).")
        c.execute("""UPDATE orchestration_tasks SET state='pending', max_attempts=?, backoff_until=NULL, last_error='',
                     failure_class=NULL, dispatched_at=NULL, updated_at=? WHERE id=?""",
                  (ot["attempts"] + 1, now_iso(), orch_task_id))
        log_event(c, run, "task_retry_requested", actor, "manual retry", task_id=orch_task_id)
    run = get_run(run_id)
    if run["state"] == "BLOCKED":
        states.transition(run_id, "RUNNING", "unblocked", actor, "task retry requested", expected_state="BLOCKED")
    return get_run(run_id)


def submit_clarification(run_id: str, answers: Dict[str, str], actor: str) -> Dict[str, Any]:
    run = get_run(run_id)
    if run["state"] != "AWAITING_CLARIFICATION":
        raise OrchestrationError("No clarification is pending.")
    intake = jload(run["intake_json"])
    if answers.get("objective"):
        intake["objective"] = str(answers["objective"])[:MAX_INTAKE_FIELD]
    intake["clarifications"] = {k: str(v)[:1000] for k, v in answers.items() if k != "objective"}
    with db_transaction() as c:
        c.execute("UPDATE orchestration_runs SET intake_json=? WHERE id=?", (jdump(intake), run_id))
        c.execute("UPDATE orchestration_approvals SET status='approved', decided_by=?, decided_at=? WHERE run_id=? AND kind='clarification' AND status='pending'",
                  (actor, now_iso(), run_id))
    return planner.generate_plan(run_id, actor)


# --- tick ---------------------------------------------------------------------------------------------------

def tick_all() -> int:
    c = get_db_connection()
    try:
        ids = [r["id"] for r in c.execute(
            f"SELECT id FROM orchestration_runs WHERE state IN ({','.join('?' * len(ACTIVE_STATES))})", ACTIVE_STATES)]
    finally:
        c.close()
    for rid in ids:
        try:
            tick(rid)
        except Exception as e:  # failure isolation: one run must never stop the others
            logger.exception("tick failed for %s", rid)
            try:
                record_event(rid, "engine_error", "system", f"{type(e).__name__}: {e}"[:500])
            except Exception:
                pass
    return len(ids)


def tick(run_id: str, manual: bool = False) -> Dict[str, Any]:
    """One idempotent pass. Safe to call repeatedly and after a restart (all state is in the database)."""
    run = get_run(run_id)
    st = run["state"]
    if st in ("PLANNING", "REPLANNING"):
        planner.advance_planning(run)
        return get_run(run_id)
    if st not in ("RUNNING", "EVALUATING", "PAUSED"):
        return run
    nodes = model_router.node_inventory()
    _collect(run, nodes)
    run = get_run(run_id)
    if run["state"] == "RUNNING":
        if _escalate_if_failed(run):
            return get_run(run_id)
        if run["autonomy_level"] > 0 or manual:
            _dispatch(run, nodes, manual)
        run = get_run(run_id)
        if run["state"] == "RUNNING":
            _maybe_finish(run)
    elif run["state"] == "EVALUATING":
        _evaluate_deliverables(run)
    return get_run(run_id)


def _orch_tasks(run_id: str, conn=None) -> List[Dict[str, Any]]:
    own = conn is None
    c = conn or get_db_connection()
    try:
        return [dict(r) for r in c.execute(
            "SELECT * FROM orchestration_tasks WHERE run_id=? AND state<>'superseded' ORDER BY priority, created_at, plan_key", (run_id,))]
    finally:
        if own:
            c.close()


def _deps(run_id: str) -> Dict[str, List[str]]:
    c = get_db_connection()
    try:
        out: Dict[str, List[str]] = {}
        for r in c.execute("""SELECT d.orch_task_id, d.depends_on_id FROM orchestration_task_deps d
                              JOIN orchestration_tasks t ON t.id = d.orch_task_id WHERE t.run_id=? AND t.state<>'superseded'""", (run_id,)):
            out.setdefault(r["orch_task_id"], []).append(r["depends_on_id"])
        return out
    finally:
        c.close()


# --- collection / evaluation --------------------------------------------------------------------------------------

def _collect(run: Dict[str, Any], nodes: List[Dict[str, Any]]) -> None:
    for ot in _orch_tasks(run["id"]):
        if ot["state"] != "dispatched":
            continue
        try:
            _collect_one(run, ot, nodes)
        except Exception as e:
            logger.exception("collect failed for %s", ot["id"])
            record_event(run["id"], "engine_error", "system", f"collect {ot['plan_key']}: {e}"[:500], task_id=ot["id"])


def _close_attempt(ot: Dict[str, Any], outcome: str, failure_class: Optional[str], evaluation: Dict[str, Any]) -> None:
    with db_transaction() as c:
        c.execute("""UPDATE orchestration_attempts SET finished_at=?, outcome=?, failure_class=?, evaluation_json=?
                     WHERE orch_task_id=? AND attempt_number=? AND finished_at IS NULL""",
                  (now_iso(), outcome, failure_class, jdump(evaluation), ot["id"], ot["attempts"]))


def _collect_one(run: Dict[str, Any], ot: Dict[str, Any], nodes: List[Dict[str, Any]]) -> None:
    action = jload(ot["action_json"])
    tid = ot["current_task_id"]
    t = T.get_task_by_id(tid) if tid else None
    if t is None:
        return _fail(run, ot, "controller_restart", "underlying task missing")
    dispatched = parse_ts(ot["dispatched_at"]) or now_dt()
    elapsed = (now_dt() - dispatched).total_seconds()
    if action["type"] == "inference":
        c = get_db_connection()
        try:
            req = c.execute("SELECT id FROM model_inference_requests WHERE orch_task_id=? AND task_id=?", (ot["id"], tid)).fetchone()
        finally:
            c.close()
        if not req:
            return _fail(run, ot, "controller_restart", "inference request record missing")
        res = model_router.get_provider().poll(req["id"], ot["timeout_seconds"] + INFLIGHT_GRACE_S)
        if res["status"] == "submitted":
            return _check_inflight(run, ot, t, nodes, elapsed)
        if res["status"] != "completed":
            return _fail(run, ot, res.get("error_class") or "task_execution_failure", f"inference {res['status']}")
        ev = evaluator.evaluate_inference(ot, res, (t.get("input_data") or {}).get("prompt", ""))
        if ev["accepted"]:
            aid = artifact_manager.write_controller_artifact(
                run, ot["id"], f"{ot['plan_key']}_ai_output.txt", ev["result"]["text"].encode("utf-8"), provenance="model_output")
            artifact_manager.set_validation(aid, "valid", "structural checks passed; content is AI-generated and unverified")
            ev["result"]["artifact_id"] = aid
        return _decide_result(run, ot, ev, tid)
    if t["status"] == "completed":
        artifact_manager.register_task_artifacts(run, ot["id"], tid, ot["dispatched_node"], "worker_telemetry")
        ev = evaluator.evaluate_script_task(ot, t)
        if ev["accepted"]:
            c = get_db_connection()
            try:
                row = c.execute("SELECT id FROM artifact_metadata WHERE task_id=? AND filename='result.json'", (tid,)).fetchone()
            finally:
                c.close()
            if row:
                artifact_manager.set_validation(row["id"], "valid", "validated against " + str(ev["result"].get("schema")))
        else:
            for a in artifact_manager.list_artifacts(run["project_id"]):
                if a["task_id"] == tid:
                    artifact_manager.set_validation(a["id"], "invalid", "task evaluation failed")
        return _decide_result(run, ot, ev, tid)
    if t["status"] in ("failed", "cancelled"):
        return _fail(run, ot, recovery.classify_task_failure(t), (t.get("error_details") or "")[:400])
    _check_inflight(run, ot, t, nodes, elapsed)


def _check_inflight(run, ot, t, nodes, elapsed) -> None:
    if t["status"] == "pending":
        # Expired-lease reconciliation resets the task to 'pending'; it was already authorised by this run's approvals.
        with db_transaction() as c:
            cur = c.execute("UPDATE tasks SET status='queued', updated_at=? WHERE id=? AND status='pending'", (now_iso(), t["id"]))
            if cur.rowcount:
                log_event(c, get_run(run["id"], c), "task_requeued_after_lease_expiry", "orchestrator-recovery",
                          ot["plan_key"], task_id=ot["id"])
        t = T.get_task_by_id(t["id"])
    node = next((n for n in nodes if n["id"] == ot["dispatched_node"]), None)
    never_started = t["status"] in ("queued", "awaiting_approval")
    if never_started and node is not None and (not node["authenticated"] or node["health"] != "online") and elapsed > 45:
        _cancel_underlying(t)
        return _fail(run, ot, "worker_unavailable", f"node {ot['dispatched_node']} became unavailable before starting")
    if elapsed > ot["timeout_seconds"] + INFLIGHT_GRACE_S:
        _cancel_underlying(t)
        fc = "worker_unavailable" if never_started else ("model_timeout" if jload(ot["action_json"])["type"] == "inference" else "task_execution_failure")
        return _fail(run, ot, fc, f"no result after {int(elapsed)}s (limit {ot['timeout_seconds']}s)")


def _cancel_underlying(t: Dict[str, Any]) -> None:
    try:
        T.cancel_task(t["id"])
    except Exception as e:
        logger.info("cancel_task(%s): %s", t["id"], e)


def _decide_result(run, ot, ev, task_id) -> None:
    if ev["accepted"]:
        _close_attempt(ot, "accepted", None, {"checks": ev["checks"]})
        with db_transaction() as c:
            cur = c.execute("""UPDATE orchestration_tasks SET state='accepted', accepted_result_json=?, last_error='', failure_class=NULL, updated_at=?
                               WHERE id=? AND state='dispatched'""", (jdump(ev["result"]), now_iso(), ot["id"]))
            if cur.rowcount == 1:  # idempotent: a duplicate collection never double-accepts
                log_event(c, get_run(run["id"], c), "task_accepted", "orchestrator-evaluator",
                          f"{ot['plan_key']} on {ev.get('node')}", task_id=ot["id"],
                          detail={"checks": ev["checks"], "suspicious_output": ev.get("suspicious_output", False)})
        return
    failed = "; ".join(f"{c['name']}: {c['detail']}" for c in ev["checks"] if not c["passed"])
    _fail(run, ot, "artifact_validation_failure" if any(c["name"] in ("schema_valid", "structured_output_present", "artifact_result_json_matches") for c in ev["checks"] if not c["passed"]) else "invalid_inference_output" if jload(ot["action_json"])["type"] == "inference" else "task_execution_failure",
          failed[:500], evaluation={"checks": ev["checks"]})


def _fail(run, ot, failure_class: str, error: str, evaluation: Optional[Dict[str, Any]] = None) -> None:
    ot = _fresh(ot["id"])
    if ot["state"] != "dispatched" and ot["state"] != "pending":
        return
    _close_attempt(ot, "rejected" if failure_class == "artifact_validation_failure" else "failed", failure_class, evaluation or {"error": error})
    action = jload(ot["action_json"])
    idem = True
    if action.get("type") == "library_script":
        idem = bool(script_library.LIBRARY.get(action.get("script_id"), {}).get("idempotent", False))
    b = telemetry.get_budget(run["id"])
    decision = recovery.decide(failure_class, ot["attempts"], ot["max_attempts"], idem, b["used_retries"], b["max_retries_total"])
    with db_transaction() as c:
        r = get_run(run["id"], c)
        if decision["action"] == "retry":
            c.execute("""UPDATE orchestration_tasks SET state='pending', backoff_until=?, failure_class=?, last_error=?, updated_at=?
                         WHERE id=? AND state IN ('dispatched','pending')""",
                      (iso_in(decision["delay_seconds"]), failure_class, error[:500], now_iso(), ot["id"]))
            log_event(c, r, "task_retry_scheduled", "orchestrator-recovery",
                      f"{ot['plan_key']}: {failure_class}; retry in {decision['delay_seconds']}s ({ot['attempts']}/{ot['max_attempts']})",
                      task_id=ot["id"], detail={"failure_class": failure_class, "error": error[:300]})
        else:
            c.execute("""UPDATE orchestration_tasks SET state='failed', failure_class=?, last_error=?, updated_at=? WHERE id=?""",
                      (failure_class, error[:500], now_iso(), ot["id"]))
            log_event(c, r, "task_failed", "orchestrator-recovery", f"{ot['plan_key']}: {failure_class}: {decision['reason']}",
                      task_id=ot["id"], detail={"diagnosis": recovery.diagnose(failure_class, error)})
    if decision["action"] == "retry":
        telemetry.record_usage(run["id"], retries=1)


def _fresh(orch_task_id: str) -> Dict[str, Any]:
    c = get_db_connection()
    try:
        return dict(c.execute("SELECT * FROM orchestration_tasks WHERE id=?", (orch_task_id,)).fetchone())
    finally:
        c.close()


def _escalate_if_failed(run: Dict[str, Any]) -> bool:
    failed = [t for t in _orch_tasks(run["id"]) if t["state"] == "failed"]
    if not failed:
        return False
    t = failed[0]
    reason = f"Task '{t['title']}' failed ({t['failure_class']}): {t['last_error'][:200]}. Human decision required: retry, edit, or replan."
    meta = jload(run["analysis_json"])
    if run["autonomy_level"] == 3 and meta.get("auto_replans", 0) < 1:
        meta["auto_replans"] = meta.get("auto_replans", 0) + 1
        with db_transaction() as c:
            c.execute("UPDATE orchestration_runs SET analysis_json=? WHERE id=?", (jdump(meta), run["id"]))
        states.transition(run["id"], "REPLANNING", "auto_replan", "orchestrator", "Level 3 autonomous replan after failure: " + reason[:200])
        planner.generate_plan(run["id"], "orchestrator", mode="auto")
        return True
    states.transition(run["id"], "BLOCKED", "escalated", "orchestrator", reason, set_fields={"block_reason": reason[:1000]},
                      expected_state="RUNNING")
    return True


# --- dispatch --------------------------------------------------------------------------------------------------------

def _dispatch(run: Dict[str, Any], nodes: List[Dict[str, Any]], manual: bool) -> None:
    rows = _orch_tasks(run["id"])
    deps = _deps(run["id"])
    state_by_id = {r["id"]: r["state"] for r in rows}
    dispatched = [r for r in rows if r["state"] == "dispatched"]
    budget = telemetry.get_budget(run["id"])
    in_flight = len(dispatched)
    busy: Dict[str, int] = {}
    for r in dispatched:
        if r["dispatched_node"]:
            busy[r["dispatched_node"]] = busy.get(r["dispatched_node"], 0) + 1
    now = now_dt()
    for ot in rows:
        if ot["state"] != "pending":
            continue
        if any(state_by_id.get(d) != "accepted" for d in deps.get(ot["id"], [])):
            continue
        bu = parse_ts(ot["backoff_until"])
        if bu and bu > now:
            continue
        action = jload(ot["action_json"])
        if action["type"] != "controller_step" and in_flight >= budget["max_concurrent"]:
            continue
        ok, why = telemetry.check_budget(run["id"], will_add_task=True, will_add_inference=action["type"] == "inference",
                                         elapsed_runtime_seconds=_elapsed(run))
        if not ok:
            _block_for_budget(run, why)
            return
        if not _approval_gate(run, ot, manual):
            continue
        if action["type"] == "controller_step":
            _run_controller_step(run, ot, action)
            if manual:
                return
            continue
        node, model, reasons = model_router.select_node(ot, nodes, busy)
        if not node:
            _note_waiting(run, ot, nodes, reasons)
            continue
        try:
            _dispatch_one(run, ot, action, node, model)
        except Exception as e:
            logger.exception("dispatch failed")
            _open_attempt(ot, node["id"], model, None)
            _fail(run, _fresh(ot["id"]), "sandbox_rejection" if isinstance(e, ValidationError) else "task_execution_failure", str(e)[:400])
            continue
        in_flight += 1
        busy[node["id"]] = busy.get(node["id"], 0) + 1
        if manual:
            return


def _elapsed(run: Dict[str, Any]) -> float:
    st = parse_ts(run.get("started_at"))
    return (now_dt() - st).total_seconds() if st else 0.0


def _block_for_budget(run: Dict[str, Any], why: str) -> None:
    with db_transaction() as c:
        approval_manager.request_approval(c, run, "budget", "run", f"Budget exceeded: {why}. Approve doubling the budget?", "medium")
    states.transition(run["id"], "BLOCKED", "budget_exceeded", "orchestrator", why,
                      set_fields={"block_reason": f"Budget: {why}"}, expected_state="RUNNING")


def _approval_gate(run: Dict[str, Any], ot: Dict[str, Any], manual: bool) -> bool:
    level = run["autonomy_level"]
    forced = ot["approval_mode"] == "required" or ot["risk"] == "high"
    with db_transaction() as c:
        if forced or level == 1 or (level == 0 and not manual):
            if has_task_approval(c, run["id"], ot["id"]):
                return True
            if level == 0 and manual and not forced:
                return True
            approval_manager.request_approval(c, run, "task", ot["id"],
                                              f"Run task '{ot['title']}' ({jload(ot['action_json']).get('type')}, risk {ot['risk']})",
                                              "high" if ot["risk"] == "high" else "medium" if forced else "low")
            return False
        return True  # level >= 2 (or manual at level 0) and not forced


def has_task_approval(conn, run_id: str, ot_id: str) -> bool:
    return approval_manager.has_approval(conn, run_id, "task", ot_id)


def _note_waiting(run, ot, nodes, reasons) -> None:
    action = jload(ot["action_json"])
    permanent = not model_router.eligible_nodes(ot, nodes, require_fresh=False)["eligible"]
    msg = "; ".join(reasons)[:480]
    if permanent:
        _open_attempt(ot, None, None, None)
        _fail(run, _fresh(ot["id"]), "unsupported_capability", "no compatible node exists: " + msg)
        return
    waiting_since = parse_ts(ot["dispatched_at"])
    with db_transaction() as c:
        if waiting_since is None:
            # while pending, dispatched_at marks when the task started waiting for a worker
            c.execute("UPDATE orchestration_tasks SET dispatched_at=? WHERE id=?", (now_iso(), ot["id"]))
        if ot["last_error"] != "waiting: " + msg:
            c.execute("UPDATE orchestration_tasks SET last_error=? WHERE id=?", ("waiting: " + msg, ot["id"]))
            log_event(c, get_run(run["id"], c), "waiting_for_worker", "orchestrator", msg, task_id=ot["id"])
    if waiting_since and (now_dt() - waiting_since).total_seconds() > WAIT_FOR_WORKER_LIMIT_S:
        _open_attempt(ot, None, None, None)
        _fail(run, _fresh(ot["id"]), "worker_unavailable", f"no worker available for {WAIT_FOR_WORKER_LIMIT_S}s: {msg}")


def _open_attempt(ot: Dict[str, Any], node_id: Optional[str], model: Optional[str], task_id: Optional[str]) -> int:
    with db_transaction() as c:
        n = c.execute("SELECT attempts FROM orchestration_tasks WHERE id=?", (ot["id"],)).fetchone()["attempts"] + 1
        c.execute("UPDATE orchestration_tasks SET attempts=?, state=CASE WHEN state='pending' THEN 'dispatched' ELSE state END, dispatched_node=?, dispatched_model=?, dispatched_at=?, current_task_id=COALESCE(?, current_task_id) WHERE id=?",
                  (n, node_id, model, now_iso(), task_id, ot["id"]))
        c.execute("INSERT OR IGNORE INTO orchestration_attempts (id, orch_task_id, attempt_number, task_id, node_id, model, started_at) VALUES (?,?,?,?,?,?,?)",
                  (new_id("att"), ot["id"], n, task_id, node_id, model, now_iso()))
    return n


def _task_context(run: Dict[str, Any], ot: Dict[str, Any], action: Dict[str, Any]) -> Dict[str, Any]:
    meta = jload(run["analysis_json"])
    return {"project_id": run["project_id"], "path_id": meta["path_id"], "stage_id": ot["stage_id"],
            "purpose": ot["title"], "description": ot["title"],
            "expected_output": "Structured output validated against acceptance criteria",
            "completion_criteria": jdump(jload(ot["acceptance_json"])) if ot["acceptance_json"] != "{}" else f"{action['type']} output passes deterministic checks"}


def _accepted_inputs(run_id: str, keys: List[str]) -> Dict[str, Dict[str, Any]]:
    c = get_db_connection()
    try:
        out = {}
        for r in c.execute("SELECT plan_key, accepted_result_json FROM orchestration_tasks WHERE run_id=? AND state='accepted'", (run_id,)):
            if r["plan_key"] in keys:
                out[r["plan_key"]] = jload(r["accepted_result_json"])
        return out
    finally:
        c.close()


def _dispatch_one(run, ot, action, node, model) -> None:
    attempt = ot["attempts"] + 1
    ctx = _task_context(run, ot, action)
    key = f"orch:{ot['id']}:{attempt}"
    if action["type"] == "inference":
        inputs = _accepted_inputs(run["id"], action.get("inputs") or [])
        prompt = context_manager.build_inference_prompt(action["instruction"], inputs)
        rid = model_router.get_provider().submit(
            run_id=run["id"], orch_task_id=ot["id"], purpose="task", node_id=node["id"], model=model, prompt=prompt,
            max_tokens=int(action.get("max_tokens", 200)), timeout_seconds=ot["timeout_seconds"], project_ctx=ctx, attempt_key=key)
        c = get_db_connection()
        try:
            task_id = c.execute("SELECT task_id FROM model_inference_requests WHERE id=?", (rid,)).fetchone()["task_id"]
        finally:
            c.close()
        telemetry.record_usage(run["id"], tasks=1)
    else:
        lib = script_library.get_script(action["script_id"])
        t = T.create_task(title=f"[orch] {ot['title']}", description=ot["title"], task_type="python_execution",
                          assigned_worker=node["id"], input_data={"script": lib["script"], "label": f"orch:{ot['plan_key']}",
                                                                  "timeout_seconds": ot["timeout_seconds"]},
                          idempotency_key=key, project_id=ctx["project_id"], path_id=ctx["path_id"], stage_id=ctx["stage_id"],
                          purpose=ctx["purpose"], expected_output=ctx["expected_output"], completion_criteria=ctx["completion_criteria"])
        if t["status"] == "awaiting_approval":
            T.approve_task(t["id"])
            t = T.get_task_by_id(t["id"])
        if t["status"] != "queued":
            raise ValidationError(f"Underlying task could not be queued (status {t['status']}).")
        task_id = t["id"]
        telemetry.record_usage(run["id"], tasks=1)
    _open_attempt(ot, node["id"], model, task_id)
    record_event(run["id"], "task_dispatched", "orchestrator", f"{ot['plan_key']} -> {node['id']}" + (f" ({model})" if model else ""),
                 task_id=ot["id"], detail={"underlying_task": task_id, "attempt": attempt, "node": node["id"], "model": model})


def _run_controller_step(run, ot, action) -> None:
    _open_attempt(ot, "controller-local", None, None)
    ot = _fresh(ot["id"])
    inputs = _accepted_inputs(run["id"], action.get("inputs") or [])
    c = get_db_connection()
    try:
        by_key = {r["plan_key"]: dict(r) for r in c.execute("SELECT plan_key, attempts, dispatched_node FROM orchestration_tasks WHERE run_id=? AND state<>'superseded'", (run["id"],))}
        rev = c.execute("SELECT plan_json FROM orchestration_plan_revisions WHERE run_id=? AND status='approved' ORDER BY revision_number DESC LIMIT 1", (run["id"],)).fetchone()
    finally:
        c.close()
    plan = jload(rev["plan_json"]) if rev else {}
    title = next((d["name"] for d in plan.get("deliverables", []) if d["produced_by"] == ot["plan_key"]), "Project Report")
    prov = [{"key": k, "node": v.get("node"), "attempts": by_key.get(k, {}).get("attempts"), "kind": v.get("kind")} for k, v in sorted(inputs.items())]
    try:
        rep = reporting.assemble_health_report(run, title, inputs, prov)
        ev = evaluator.evaluate_report(rep["markdown"], reporting.REQUIRED_SECTIONS)
        ev["checks"].append({"name": "has_measured_data", "passed": any(v.get("kind") == "worker_telemetry" for v in inputs.values()),
                             "detail": "at least one validated telemetry source"})
        ev["accepted"] = ev["accepted"] and all(c["passed"] for c in ev["checks"])
        ids = []
        if ev["accepted"]:
            for fname, content in (("health_report.md", rep["markdown"]), ("health_report.json", rep["json_text"])):
                aid = artifact_manager.write_controller_artifact(run, ot["id"], fname, content.encode("utf-8"))
                artifact_manager.set_validation(aid, "valid", "report sections and labels verified")
                ids.append(aid)
        ev["result"] = {"kind": "controller_generated", "artifacts": ids, "node": "controller-local"} if ev["accepted"] else {}
        ev["node"] = "controller-local"
    except Exception as e:
        ev = {"accepted": False, "checks": [{"name": "assemble", "passed": False, "detail": str(e)[:200]}], "result": {}}
    _decide_result(run, ot, ev, None)


def _maybe_finish(run: Dict[str, Any]) -> None:
    rows = _orch_tasks(run["id"])
    if not rows:
        return
    if all(r["state"] == "accepted" for r in rows):
        states.transition(run["id"], "EVALUATING", "all_tasks_accepted", "orchestrator", "all tasks passed acceptance checks; validating deliverables",
                          expected_state="RUNNING")
        _evaluate_deliverables(get_run(run["id"]))
        return
    if any(r["state"] == "blocked" for r in rows) and not any(r["state"] in ("dispatched",) for r in rows):
        ready_pending = [r for r in rows if r["state"] == "pending"]
        deps = _deps(run["id"])
        sb = {r["id"]: r["state"] for r in rows}
        if not any(all(sb.get(d) == "accepted" for d in deps.get(r["id"], [])) for r in ready_pending):
            reason = "Remaining tasks are blocked (approval denied or prerequisites blocked). Edit the plan or replan."
            states.transition(run["id"], "BLOCKED", "escalated", "orchestrator", reason, set_fields={"block_reason": reason},
                              expected_state="RUNNING")


# --- deliverable evaluation / completion ----------------------------------------------------------------------------------

def _evaluate_deliverables(run: Dict[str, Any]) -> None:
    meta = jload(run["analysis_json"])
    dmap = meta.get("deliverable_map", {})
    c = get_db_connection()
    try:
        rev = c.execute("SELECT plan_json FROM orchestration_plan_revisions WHERE run_id=? AND status='approved' ORDER BY revision_number DESC LIMIT 1", (run["id"],)).fetchone()
    finally:
        c.close()
    plan = jload(rev["plan_json"]) if rev else {}
    rows = {r["plan_key"]: r for r in _orch_tasks(run["id"])}
    arts = artifact_manager.list_artifacts(run["project_id"])
    all_accepted = True
    for d in plan.get("deliverables", []):
        did = dmap.get(d["key"])
        dd = D.get_deliverable(did) if did else None
        producer = rows.get(d["produced_by"])
        if not dd or not producer or producer["state"] != "accepted":
            all_accepted = False
            continue
        if dd["status"] == "accepted":
            continue
        valid = [a for a in arts if a["orch_task_id"] == producer["id"] and a["validation_state"] == "valid"]
        if not valid:
            all_accepted = False
            if dd["status"] != "rejected":
                record_event(run["id"], "deliverable_validation_failed", "orchestrator-evaluator", f"{d['key']}: no validated artifact")
                states.transition(run["id"], "BLOCKED", "escalated", "orchestrator-evaluator",
                                  f"Deliverable '{d['name']}' has no validated artifact.",
                                  set_fields={"block_reason": f"Deliverable '{d['name']}' has no validated artifact."})
                return
            continue
        if dd["status"] in ("planned", "in_progress", "changes_requested"):
            refs = "; ".join(f"{a['filename']} ({a['id']}, sha256 {a['sha256'][:12]})" for a in valid)
            D.submit_deliverable(did, "orchestrator", refs, f"Deterministic checks passed; provenance: {valid[0]['provenance']}")
            dd = D.get_deliverable(did)
        if dd["status"] == "submitted":
            if run["autonomy_level"] >= 2:
                D.review_deliverable(did, "accepted", "orchestrator (auto)",
                                     f"Auto-accepted: all deterministic acceptance checks passed (autonomy level {run['autonomy_level']}).")
                record_event(run["id"], "deliverable_accepted", "orchestrator (auto)", d["name"])
            else:
                with db_transaction() as cn:
                    ap = cn.execute("SELECT status, decided_by, rationale FROM orchestration_approvals WHERE run_id=? AND kind='deliverable' AND subject_id=? ORDER BY requested_at DESC LIMIT 1",
                                    (run["id"], did)).fetchone()
                    ap = dict(ap) if ap else None
                    if not ap or ap["status"] == "superseded":
                        approval_manager.request_approval(cn, run, "deliverable", did, f"Accept deliverable '{d['name']}'", "medium")
                if ap and ap["status"] == "approved":
                    D.review_deliverable(did, "accepted", ap["decided_by"] or "user", ap["rationale"] or "Accepted by user.")
            dd = D.get_deliverable(did)
        if dd["status"] != "accepted":
            all_accepted = False
    if all_accepted and plan.get("deliverables"):
        _write_index(run, plan, dmap)
        states.transition(run["id"], "COMPLETED", "completed", "orchestrator", "all deliverables accepted", expected_state="EVALUATING")


def _write_index(run, plan, dmap) -> None:
    arts = artifact_manager.list_artifacts(run["project_id"])
    idx = {"schema": "qc.deliverables_index.v1", "run_id": run["id"], "generated_utc": now_iso(),
           "deliverables": [{"key": d["key"], "name": d["name"], "terminal": d["terminal"],
                             "status": (D.get_deliverable(dmap.get(d["key"])) or {}).get("status"),
                             "artifacts": [{"id": a["id"], "filename": a["filename"], "sha256": a["sha256"], "provenance": a["provenance"]}
                                           for a in arts if a["plan_key"] == d["produced_by"]]} for d in plan["deliverables"]]}
    import json
    aid = artifact_manager.write_controller_artifact(run, None, "deliverables_index.json", json.dumps(idx, indent=2).encode("utf-8"))
    artifact_manager.set_validation(aid, "valid", "generated from accepted deliverables")


# --- recovery & background loop -------------------------------------------------------------------------------------------------

def recover_after_restart() -> int:
    """Called once at startup: in-flight work is DB-backed, so recovery = log and let the next tick reconcile."""
    c = get_db_connection()
    try:
        ids = [r["id"] for r in c.execute("SELECT id FROM orchestration_runs WHERE state IN ('PLANNING','REPLANNING','RUNNING','EVALUATING')")]
    finally:
        c.close()
    for rid in ids:
        record_event(rid, "controller_restart_recovered", "system", "controller started; run state restored from database")
    return len(ids)


_thread: Optional[threading.Thread] = None
_stop = threading.Event()


def _loop() -> None:
    logger.info("orchestrator loop started (holder %s)", HOLDER)
    while not _stop.is_set():
        try:
            if acquire_lock():
                tick_all()
        except Exception:
            logger.exception("orchestrator loop error")
        _stop.wait(TICK_INTERVAL_S)
    try:
        release_lock()
    except Exception:
        pass


def start_background() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    try:
        recover_after_restart()
    except Exception:
        logger.exception("recovery scan failed")
    _thread = threading.Thread(target=_loop, name="orchestrator", daemon=True)
    _thread.start()


def stop_background() -> None:
    _stop.set()
    if _thread:
        _thread.join(timeout=5)


# --- read models --------------------------------------------------------------------------------------------------------------------

def status(project_id: str) -> Dict[str, Any]:
    run = _require_run(project_id)
    rows = _orch_tasks(run["id"])
    deps = _deps(run["id"])
    key_by_id = {r["id"]: r["plan_key"] for r in rows}
    tasks = [{"id": r["id"], "key": r["plan_key"], "title": r["title"], "state": r["state"], "agent_role": r["agent_role"],
              "action_type": jload(r["action_json"]).get("type"), "node": r["dispatched_node"], "model": r["dispatched_model"],
              "attempts": r["attempts"], "max_attempts": r["max_attempts"], "failure_class": r["failure_class"],
              "last_error": r["last_error"], "risk": r["risk"], "backoff_until": r["backoff_until"],
              "dispatched_at": r["dispatched_at"] if r["state"] == "dispatched" else None,
              "depends_on": [key_by_id.get(d, d) for d in deps.get(r["id"], [])], "stage_id": r["stage_id"],
              "underlying_task_id": r["current_task_id"]} for r in rows]
    done = sum(1 for t in tasks if t["state"] == "accepted")
    pending = approval_manager.list_pending(run["id"])
    c = get_db_connection()
    try:
        last = c.execute("SELECT ts FROM orchestration_events WHERE run_id=? ORDER BY id DESC LIMIT 1", (run["id"],)).fetchone()
    finally:
        c.close()
    stages = []
    if tasks:
        try:
            stages = [{"id": s["id"], "name": s["name"], "status": s["status"]} for s in P.list_stages(jload(run["analysis_json"]).get("path_id"))]
        except Exception:
            stages = []
    return {"run": {k: run[k] for k in ("id", "project_id", "state", "autonomy_level", "block_reason", "planning_error",
                                          "created_at", "updated_at", "started_at", "completed_at", "current_revision_id", "resume_state")},
            "intake": jload(run["intake_json"]), "analysis": jload(run["analysis_json"]),
            "progress": {"total": len(tasks), "accepted": done, "percent": int(100 * done / len(tasks)) if tasks else 0,
                         "active": sum(1 for t in tasks if t["state"] == "dispatched"),
                         "remaining": len(tasks) - done},
            "tasks": tasks, "stages": stages, "decisions_required": pending,
            "blockers": ([run["block_reason"]] if run["block_reason"] and run["state"] == "BLOCKED" else []) +
                        ([f"Planning error: {run['planning_error']}"] if run["planning_error"] and run["state"] == "DRAFT" else []),
            "budget": telemetry.get_budget(run["id"]), "last_activity": last["ts"] if last else run["updated_at"],
            "revisions": planner.list_revisions(run["id"]),
            "models": model_router.model_health(), "scheduler_lock_held_by_this_process": acquire_lock_peek()}


def acquire_lock_peek() -> bool:
    c = get_db_connection()
    try:
        r = c.execute("SELECT holder, expires_at FROM orchestration_scheduler_lock WHERE name=?", (LOCK_NAME,)).fetchone()
        return bool(r and r["holder"] == HOLDER)
    finally:
        c.close()
