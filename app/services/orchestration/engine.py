"""Persistent orchestration engine: durable, idempotent, single-scheduler tick loop over the run state machine."""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import os
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path
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
_NATIVE_RETRIEVAL_VERIFIER = None
_PROTECTED_CODE_TRANSPORT = None


def configure_protected_code_verifier(transport=None):
    """Explicit isolated controller injection; no route, startup or worker flag.

    This development bridge never enables generated-code worker dispatch or
    changes the evaluator's independent software certification hold.
    """
    if transport is not None and not callable(transport):
        raise ValueError('Trusted protected-verifier transport required')
    global _PROTECTED_CODE_TRANSPORT
    _PROTECTED_CODE_TRANSPORT=transport


def verify_native_code_proposal(project_id,run_id,deliverable_key,artifact_id,image_sha256,*,seconds=5):
    """Controller service API: native evidence -> pinned tests -> protected runner.

    Deliberately opt-in and separate from scheduler ticks until a live leased
    model canary and deployment review authorize automatic dispatch.
    """
    if _PROTECTED_CODE_TRANSPORT is None or os.environ.get('QC_ENV')!='development':
        raise ValueError('Native code verification held; isolated development transport required')
    from .code_proposals import CodeProposalReview
    from .protected_verification import ProtectedVerification
    c=get_db_connection()
    try:
        reviewer=CodeProposalReview(c,project_id,run_id)
        if artifact_id is None:
            artifact_id=reviewer.capture_native_response(deliverable_key)
        review=reviewer.review(deliverable_key,artifact_id); c.commit()
        return ProtectedVerification(reviewer,_PROTECTED_CODE_TRANSPORT,nonproduction=True).verify(
            review['id'],image_sha256,seconds=seconds)
    finally: c.close()


def configure_native_retrieval_verifier(verifier):
    """Explicit controller startup injection. Never loads keys or enables nodes."""
    global _NATIVE_RETRIEVAL_VERIFIER
    _NATIVE_RETRIEVAL_VERIFIER=verifier

# Bounded capacity and execution pool for asynchronous controller step execution (Finding F-13 & Stage 3 Safety)
MAX_CONTROLLER_WORKERS = 4
MAX_OUTSTANDING_CONTROLLER_TASKS = 6
MAX_CONTROLLER_TASKS_PER_RUN = 2

_CONTROLLER_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=MAX_CONTROLLER_WORKERS, thread_name_prefix="qc-controller-step"
)
_ACTIVE_FUTURES: Dict[str, concurrent.futures.Future] = {}
_ACTIVE_METADATA: Dict[str, Dict[str, Any]] = {}
_FUTURES_LOCK = threading.Lock()


def await_active_controller_steps(run_id: Optional[str] = None, timeout: float = 10.0) -> None:
    """Wait for in-flight background controller steps to complete (for tests and manual coordination)."""
    with _FUTURES_LOCK:
        if run_id:
            futs = [_ACTIVE_FUTURES[k] for k, m in _ACTIVE_METADATA.items() if m.get("run_id") == run_id and k in _ACTIVE_FUTURES]
        else:
            futs = list(_ACTIVE_FUTURES.values())
    if futs:
        concurrent.futures.wait(futs, timeout=timeout)



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
    c = get_db_connection()
    try:
        rev_row = c.execute("SELECT plan_json FROM orchestration_plan_revisions WHERE id=?", (run["current_revision_id"],)).fetchone()
    finally:
        c.close()
    if rev_row and rev_row["plan_json"]:
        from app.services.orchestration import task_graph, model_router
        plan = jload(rev_row["plan_json"])
        v = task_graph.validate_plan(plan, model_router.node_inventory())
        if not v["valid"]:
            raise OrchestrationError("Cannot start run with invalid plan: " + "; ".join(v["errors"][:5]))
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
    try:
        from app.services.worker import reconcile_expired_leases
        reconcile_expired_leases()
    except Exception:
        logger.exception("Failed to reconcile expired worker leases during tick_all")

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
        if run["autonomy_level"] > 0 or manual:
            _dispatch(run, nodes, manual)
        run = get_run(run_id)
        if run["state"] == "RUNNING":
            if _escalate_if_failed(run):
                return get_run(run_id)
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

    if action.get("type") == "controller_step":
        dispatched = parse_ts(ot["dispatched_at"]) or now_dt()
        elapsed = (now_dt() - dispatched).total_seconds()
        fut_key = f"{ot['id']}:{ot['attempts']}"
        with _FUTURES_LOCK:
            fut = _ACTIVE_FUTURES.get(fut_key)

        # Recovery of orphaned dispatched tasks after controller restart
        if fut is None:
            if elapsed > 1.0:
                logger.warning("Detected orphaned controller task %s attempt %s after restart; failing for recovery", ot["id"], ot["attempts"])
                return _fail(run, ot, "controller_restart", "Controller process restarted while step was in-flight; orphaned attempt rescheduled")
            return

        if fut.done():
            exc = fut.exception()
            if exc:
                return _fail(run, ot, "task_execution_failure", f"controller step error: {exc}")
            return
        if elapsed > ot["timeout_seconds"] + INFLIGHT_GRACE_S:
            if fut and not fut.done():
                fut.cancel()
            return _fail(run, ot, "task_execution_failure", f"controller step timed out after {int(elapsed)}s")
        return

    t = T.get_task_by_id(tid) if tid else None
    if t is None:
        return _fail(run, ot, "controller_restart", "underlying task missing")
    if action.get('type')=='research_fetch':
        if t['status']=='completed':
            if _NATIVE_RETRIEVAL_VERIFIER is None:
                return _fail(run,ot,'task_execution_failure','Controller retrieval verifier is not configured')
            try:
                from app.services.research.manager import ResearchManager
                c=get_db_connection()
                try:
                    rr=c.execute('SELECT id FROM research_runs WHERE orchestration_run_id=? AND project_id=?',
                        (run['id'],run['project_id'])).fetchall()
                    receipts=c.execute('SELECT id FROM worker_executions WHERE task_id=? AND status=? AND exit_code=0',
                        (tid,'success')).fetchall()
                finally: c.close()
                if len(rr)!=1 or len(receipts)!=1: raise ValueError('Native retrieval scope is ambiguous')
                manager=ResearchManager(feedback_only=True)
                result=manager.prepare_native_retrieval_feedback(run['project_id'],rr[0]['id'],receipts[0]['id'],
                    _NATIVE_RETRIEVAL_VERIFIER)
                if not result.get('snapshot_id'): raise ValueError('Retrieval budget exhausted')
                return _decide_result(run,ot,{'accepted':True,'node':ot['dispatched_node'],
                    'checks':[{'name':'controller_observed_retrieval','passed':True,'detail':'Source claims remain unverified'}],
                    'result':result},tid)
            except Exception as exc:
                return _fail(run,ot,'task_execution_failure',str(exc)[:400])
        if t['status'] in ('failed','cancelled'):
            return _fail(run,ot,'task_execution_failure','Worker retrieval failed')
        dispatched=parse_ts(ot['dispatched_at']) or now_dt()
        return _check_inflight(run,ot,t,nodes,(now_dt()-dispatched).total_seconds())
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
    rows = _orch_tasks(run["id"])
    failed = [t for t in rows if t["state"] == "failed"]
    if not failed:
        return False

    deps = _deps(run["id"])
    state_by_id = {r["id"]: r["state"] for r in rows}
    now = now_dt()

    # If independent deliverable synthesis tasks exist, allow remaining eligible ones to complete
    deliverable_tasks = [t for t in rows if t["plan_key"].startswith("synthesize_")]
    if deliverable_tasks:
        deliverable_in_flight = [t for t in deliverable_tasks if t["state"] == "dispatched"]
        deliverable_eligible = [
            t for t in deliverable_tasks
            if t["state"] == "pending"
            and all(state_by_id.get(d) == "accepted" for d in deps.get(t["id"], []))
            and not (parse_ts(t.get("backoff_until")) and parse_ts(t.get("backoff_until")) > now)
        ]
        if deliverable_in_flight or deliverable_eligible:
            return False

    t = failed[0]
    accepted_tasks = [t for t in rows if t["state"] == "accepted"]
    failed_names = ", ".join(t["title"] for t in failed)
    reason = (
        f"Task '{t['title']}' failed ({t['failure_class']}): {t['last_error'][:200]}. "
        f"Independent deliverable failure isolation: {len(accepted_tasks)} of {len(rows)} tasks accepted. "
        f"Failed tasks: {failed_names}. Meaningful partial deliverables preserved. "
        "Human decision required: retry, edit, or replan."
    )
    meta = jload(run["analysis_json"])
    meta["partial_completion"] = {
        "accepted_tasks_count": len(accepted_tasks),
        "failed_tasks_count": len(failed),
        "total_tasks_count": len(rows),
        "failed_tasks": [t["plan_key"] for t in failed],
        "isolated_at": now_iso(),
    }
    with db_transaction() as c:
        c.execute("UPDATE orchestration_runs SET analysis_json=? WHERE id=?", (jdump(meta), run["id"]))

    record_event(run["id"], "independent_failure_isolated", "orchestrator", reason,
                 detail={"accepted_count": len(accepted_tasks), "failed_count": len(failed)})

    from app.services.orchestration.research_checkpoint import execute_research_checkpoint
    try:
        execute_research_checkpoint(
            project_id=run["project_id"],
            run_id=run["id"],
            stage_phase="replanning",
            stage_name="Failure Isolation Checkpoint",
            objective=jload(run["intake_json"]).get("objective", ""),
            context_input={"failure_reason": reason, "partial_deliverables_preserved": True}
        )
    except Exception as ex:
        logger.warning("Failure isolation research checkpoint failed: %s", ex)

    states.transition(run["id"], "BLOCKED", "escalated", "orchestrator", reason,
                      set_fields={"block_reason": reason[:1000]}, expected_state="RUNNING")
    return True


# --- dispatch --------------------------------------------------------------------------------------------------------

def _dispatch(run: Dict[str, Any], nodes: List[Dict[str, Any]], manual: bool) -> None:
    # Ensure execution research checkpoint has run for this execution cycle
    c = get_db_connection()
    try:
        cp = c.execute("SELECT id FROM stage_research_checkpoints WHERE run_id=? AND stage_phase='execution'", (run["id"],)).fetchone()
    finally:
        c.close()
    analysis = jload(run.get("analysis_json") or "{}")
    if not cp and (analysis.get("research_match") or analysis.get("research_recommended")):
        from app.services.orchestration.research_checkpoint import execute_research_checkpoint
        try:
            execute_research_checkpoint(
                project_id=run["project_id"],
                run_id=run["id"],
                stage_phase="execution",
                stage_name="Execution Checkpoint",
                objective=jload(run["intake_json"]).get("objective", "")
            )
        except Exception as ex:
            logger.warning("Execution research checkpoint failed: %s", ex)

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
        action = jload(ot["action_json"]) or {}
        act_type = action.get("type") if isinstance(action, dict) else None
        if act_type == "controller_step":
            # 1. Backpressure check: bound maximum outstanding controller tasks across the engine
            with _FUTURES_LOCK:
                total_active = len(_ACTIVE_FUTURES)
                run_active = sum(1 for m in _ACTIVE_METADATA.values() if m.get("run_id") == run["id"])

            if total_active >= MAX_OUTSTANDING_CONTROLLER_TASKS:
                logger.info("Controller dispatch deferred: queue capacity reached (%s/%s)", total_active, MAX_OUTSTANDING_CONTROLLER_TASKS)
                record_event(run["id"], "controller_capacity_saturated", "orchestrator",
                             f"Controller task {ot['plan_key']} deferred: capacity ({total_active}/{MAX_OUTSTANDING_CONTROLLER_TASKS}) reached",
                             task_id=ot["id"])
                continue

            # 2. Fairness check: prevent one project from starving other projects
            if run_active >= MAX_CONTROLLER_TASKS_PER_RUN:
                logger.info("Controller dispatch deferred for run %s: per-run fairness limit reached (%s/%s)", run["id"], run_active, MAX_CONTROLLER_TASKS_PER_RUN)
                continue

        if act_type != "controller_step" and in_flight >= budget["max_concurrent"]:
            continue
        ok, why = telemetry.check_budget(run["id"], will_add_task=True, will_add_inference=act_type == "inference",
                                         elapsed_runtime_seconds=_elapsed(run))
        if not ok:
            _block_for_budget(run, why)
            return
        if not _approval_gate(run, ot, manual):
            continue
        if act_type == "controller_step":
            _dispatch_controller_step(run, ot, action, sync=manual)
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
    forced = ot["approval_mode"] == "required" or ot["risk"] == "high" or jload(ot['action_json']).get('type')=='research_fetch'
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
    permanent = not model_router.eligible_nodes(ot, nodes, require_fresh=False, enforce_verified=True)["eligible"]
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
    timeout_s = ot.get("timeout_seconds") or 600
    deadline_iso = (now_dt() + timedelta(seconds=timeout_s + INFLIGHT_GRACE_S)).isoformat()
    init_eval = {"deadline_at": deadline_iso, "timeout_seconds": timeout_s}
    with db_transaction() as c:
        n = c.execute("SELECT attempts FROM orchestration_tasks WHERE id=?", (ot["id"],)).fetchone()["attempts"] + 1
        c.execute("UPDATE orchestration_tasks SET attempts=?, state=CASE WHEN state='pending' THEN 'dispatched' ELSE state END, dispatched_node=?, dispatched_model=?, dispatched_at=?, current_task_id=COALESCE(?, current_task_id) WHERE id=?",
                  (n, node_id, model, now_iso(), task_id, ot["id"]))
        c.execute("INSERT OR IGNORE INTO orchestration_attempts (id, orch_task_id, attempt_number, task_id, node_id, model, started_at, evaluation_json) VALUES (?,?,?,?,?,?,?,?)",
                  (new_id("att"), ot["id"], n, task_id, node_id, model, now_iso(), jdump(init_eval)))
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
        raw_inputs = action.get("inputs") or []
        if ot["plan_key"] in raw_inputs:
            raise ValidationError(f"Task '{ot['plan_key']}' cannot declare itself as an input.")
        inputs = _accepted_inputs(run["id"], [k for k in raw_inputs if k != ot["plan_key"]])
        from app.services.knowledge_store import search_knowledge_records, record_knowledge_impact
        kn_recs = search_knowledge_records(
            action.get("instruction", ot["title"]),
            project_id=run["project_id"],
            include_collective=True,
            limit=3
        )
        for kr in kn_recs:
            record_knowledge_impact(
                knowledge_id=kr["id"],
                project_id=run["project_id"],
                run_id=run["id"],
                task_id=ot["id"],
                usage_type="task_context",
                impact_summary=f"Supplied evidence context for task '{ot['plan_key']}'"
            )
        prompt = context_manager.build_inference_prompt(action["instruction"], inputs, knowledge_records=kn_recs)
        rid = model_router.get_provider().submit(
            run_id=run["id"], orch_task_id=ot["id"], purpose="task", node_id=node["id"], model=model, prompt=prompt,
            max_tokens=int(action.get("max_tokens", 200)), timeout_seconds=ot["timeout_seconds"], project_ctx=ctx, attempt_key=key)
        c = get_db_connection()
        try:
            task_id = c.execute("SELECT task_id FROM model_inference_requests WHERE id=?", (rid,)).fetchone()["task_id"]
        finally:
            c.close()
        telemetry.record_usage(run["id"], tasks=1)
    elif action['type']=='research_fetch':
        from app.services.worker import check_worker_leasing_authority
        from app.services.research.feedback import ProjectEvidenceFeedback
        if _NATIVE_RETRIEVAL_VERIFIER is None:
            raise ValidationError('Controller retrieval verifier is not configured')
        c=get_db_connection()
        try:
            if not has_task_approval(c,run['id'],ot['id']):
                raise ValidationError('Explicit retrieval-task approval required')
            check_worker_leasing_authority(c,node['id'],['research_fetch'])
            rev=c.execute('''SELECT rev.plan_json FROM orchestration_runs r
                JOIN orchestration_plan_revisions rev ON rev.id=r.current_revision_id
                WHERE r.id=? AND rev.id=? AND rev.status='approved' ''',(run['id'],ot['revision_id'])).fetchone()
            if not rev or not any(t.get('key')==ot['plan_key'] and t.get('action')==action
                    for t in jload(rev['plan_json']).get('tasks',[])):
                raise ValidationError('Retrieval must match the current approved plan')
            rr=c.execute('SELECT id FROM research_runs WHERE orchestration_run_id=? AND project_id=? AND status=?',
                (run['id'],run['project_id'],'active')).fetchall()
            if len(rr)!=1: raise ValidationError('Missing or ambiguous active research run')
            ledger=ProjectEvidenceFeedback(c,run['project_id'],rr[0]['id'])
            if not any(p['id']==action.get('feedback_path_id') for p in ledger.items('path')):
                raise ValidationError('Missing scoped feedback proposal')
        finally: c.close()
        t=T.create_task(title=ot['title'],task_type='research_fetch',assigned_worker=node['id'],
            input_data={'url':action['url'],'feedback_path_id':action['feedback_path_id']},idempotency_key=key,
            project_id=ctx['project_id'],path_id=ctx['path_id'],stage_id=ctx['stage_id'],
            purpose=ctx['purpose'],expected_output=ctx['expected_output'],completion_criteria=ctx['completion_criteria'])
        if t['status']=='awaiting_approval': T.approve_task(t['id'])
        t=T.get_task_by_id(t['id'])
        if t['status']!='queued': raise ValidationError('Native retrieval could not be queued')
        task_id=t['id']
        telemetry.record_usage(run['id'],tasks=1)
    else:
        if action.get("script_id") not in script_library.LIBRARY:
            raise ValidationError(f"Unknown library script '{action.get('script_id')}'.")
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


def _run_research_controller_step(run, ot, action, plan, inputs) -> Dict[str, Any]:
    import json
    import uuid
    from datetime import datetime, timezone
    from app.services.research.manager import ResearchManager
    from app.services.research.extraction import extract_claims_with_spans

    step = action.get("step")
    project_id = run["project_id"]
    objective = plan.get("overview", {}).get("objective") or "Autonomous Web Research"
    mgr = ResearchManager()

    # Finding F-09: Research run scope isolation
    orch_run_id = run["id"]
    research_run_id = f"rrun-{orch_run_id}"
    conn = get_db_connection()
    try:
        r_row = conn.execute("SELECT id FROM research_runs WHERE orchestration_run_id = ?", (orch_run_id,)).fetchone()
        if r_row:
            research_run_id = r_row["id"]
        else:
            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """INSERT INTO research_runs (id, project_id, orchestration_run_id, status, objective, created_at, updated_at)
                   VALUES (?, ?, ?, 'active', ?, ?, ?)""",
                (research_run_id, project_id, orch_run_id, objective, now_iso, now_iso)
            )
            conn.commit()
    finally:
        conn.close()

    if step == "research_search":
        strategy = mgr.planner.generate_strategy(objective)
        res = mgr.execute_search_step(
            project_id, ot["id"], strategy.initial_queries, research_run_id=research_run_id
        )
        ev = evaluator.evaluate_research_step(step, res)
        ids = []
        if ev["accepted"]:
            aid = artifact_manager.write_controller_artifact(
                run, ot["id"], "search_results.json", json.dumps(res, indent=2).encode("utf-8")
            )
            artifact_manager.set_validation(aid, "valid", f"{res.get('results_count')} search results verified")
            ids.append(aid)
        ev["result"] = {"kind": "controller_generated", "artifacts": ids, "node": "controller-local", "data": res}
        ev["node"] = "controller-local"
        return ev

    elif step in ("research_fetch", "research_browser_read", "research_extract"):
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            # Finding F-09: Scope search results strictly to current research_run_id
            cursor.execute(
                """
                SELECT r.url, r.title, r.snippet, r.provider, r.provider AS search_provider, COALESCE(q.query_text, '') AS search_query
                FROM research_search_results r
                JOIN research_queries q ON q.id = r.query_id
                WHERE r.project_id = ? AND q.research_run_id = ?
                ORDER BY r.rank
                LIMIT 10
                """,
                (project_id, research_run_id),
            )
            results = [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()
        res = mgr.execute_fetch_and_extract_step(
            project_id, ot["id"], results, research_run_id=research_run_id
        )
        ev = evaluator.evaluate_research_step(step, res)
        ids = []
        if ev["accepted"]:
            aid = artifact_manager.write_controller_artifact(
                run, ot["id"], "retrieved_sources.json", json.dumps(res, indent=2).encode("utf-8")
            )
            artifact_manager.set_validation(aid, "valid", f"{res.get('sources_count')} sources retrieved and extracted")
            ids.append(aid)
        ev["result"] = {"kind": "controller_generated", "artifacts": ids, "node": "controller-local", "data": res}
        ev["node"] = "controller-local"
        return ev

    elif step == "research_verify":
        # Finding F-09: Scope source library listing strictly to current research run
        sources = mgr.source_library.list_sources(project_id, research_run_id=research_run_id)
        candidate_claims = []
        # Finding F-06: Accurate sentence/assertion extraction with character & line spans
        for src in sources:
            text = src.get("extracted_text", "") or src.get("clean_text", "")
            sq = float(src.get("quality_score", 1.0))
            spans = extract_claims_with_spans(text, source_id=src["source_id"], source_quality=sq)
            for sp in spans[:4]:
                candidate_claims.append({
                    "claim_text": sp.claim_text,
                    "claim_type": sp.category,
                    "confidence_score": sp.confidence_score,
                    "evidence": [{
                        "source_id": src["source_id"],
                        "passage": sp.context_passage,
                        "exact_quote": sp.exact_quote,
                        "start_char": sp.start_char,
                        "end_char": sp.end_char,
                        "start_line": sp.start_line,
                        "end_line": sp.end_line,
                        "confidence": sp.confidence_score,
                    }]
                })
        res = mgr.execute_verify_step(
            project_id, ot["id"], candidate_claims[:15], research_run_id=research_run_id
        )
        ev = evaluator.evaluate_research_step(step, res)
        ids = []
        if ev["accepted"]:
            aid = artifact_manager.write_controller_artifact(
                run, ot["id"], "evidence_ledger.json", json.dumps(res, indent=2).encode("utf-8")
            )
            artifact_manager.set_validation(aid, "valid", f"{res.get('claims_count')} claims corroborated")
            ids.append(aid)
        ev["result"] = {"kind": "controller_generated", "artifacts": ids, "node": "controller-local", "data": res}
        ev["node"] = "controller-local"
        return ev

    elif step in ("research_coverage", "research_evidence_coverage"):
        contracts = []
        raw_contracts = plan.get("deliverable_contracts", [])
        if raw_contracts:
            from app.services.orchestration.contract import DeliverableRequirementContract
            for c in raw_contracts:
                contracts.append(DeliverableRequirementContract(**c) if isinstance(c, dict) else c)
        else:
            from app.services.orchestration.contract import decompose_deliverable_requirements
            for d in plan.get("deliverables", []):
                contracts.append(decompose_deliverable_requirements(
                    {"objective": objective, "instructions": plan.get("overview", {}).get("scope", "")},
                    d.get("name", ""),
                    d.get("key", "")
                ))

        res = mgr.execute_coverage_step(
            project_id=project_id,
            task_id=ot["id"],
            contracts=contracts,
            project_objective=objective,
            research_run_id=research_run_id,
        )
        ev = evaluator.evaluate_research_step(step, res)
        ids = []
        if ev["accepted"]:
            assessment_data = res.get("assessment", {})
            aid = artifact_manager.write_controller_artifact(
                run, ot["id"], "evidence_sufficiency_assessment.json",
                json.dumps(assessment_data, indent=2).encode("utf-8")
            )
            artifact_manager.set_validation(
                aid, "valid", f"Evidence coverage assessed: {assessment_data.get('overall_status')}"
            )
            ids.append(aid)
        ev["result"] = {"kind": "controller_generated", "artifacts": ids, "node": "controller-local", "data": res}
        ev["node"] = "controller-local"
        return ev

    elif step in ("research_synthesize", "research_citation_audit"):
        deliv_name = next((d["name"] for d in plan.get("deliverables", []) if d.get("produced_by") == ot.get("plan_key")), None)
        topic = deliv_name or plan.get("overview", {}).get("objective") or "Research Deliverable"
        plan_task = next((t for t in plan.get("tasks", []) if t.get("key") == ot.get("plan_key")), {})
        expected_md = next((out for out in plan_task.get("expected_outputs", []) if out.endswith(".md")), None)
        filename = expected_md or f"{ot.get('plan_key', 'research_report')}.md"
        audit_filename = f"citation_audit_{ot.get('plan_key', 'report')}.json"

        ai_inputs = [v for v in inputs.values() if isinstance(v, dict) and (v.get("kind") == "model_output" or v.get("role") == "model_output")]

        coverage_data = None
        for v in inputs.values():
            if isinstance(v, dict) and "data" in v and isinstance(v["data"], dict) and "assessment" in v["data"]:
                coverage_data = v["data"]["assessment"]
                break
            elif isinstance(v, dict) and "overall_status" in v:
                coverage_data = v
                break

        deliv_obj = next((d for d in plan.get("deliverables", []) if d.get("produced_by") == ot.get("plan_key")), None)
        deliv_contract = None
        if deliv_obj:
            from app.services.orchestration.contract import decompose_deliverable_requirements
            deliv_contract = decompose_deliverable_requirements(
                {"objective": objective, "instructions": plan.get("overview", {}).get("scope", "")},
                deliv_obj.get("name", ""),
                deliv_obj.get("key", "")
            )

        res = mgr.execute_synthesis_and_citation_step(
            project_id, ot["id"], objective, topic,
            research_run_id=research_run_id,
            ai_inputs=ai_inputs,
            deliverable_contract=deliv_contract,
            coverage_assessment=coverage_data,
        )
        ev = evaluator.evaluate_research_step(step, res)
        ids = []
        if ev["accepted"]:
            rep_md = res.get("report_markdown", "")
            if "## Provenance" not in rep_md and "Provenance" not in rep_md:
                rep_md += f"\n\n## Provenance\n\n| Attribute | Value |\n| :--- | :--- |\n| Project ID | {project_id} |\n| Orchestration Run | {run['id']} |\n| Generating Node | controller-local |\n| Upstream Sources | {len(res.get('audit', {}).get('resolved_sources', []))} verified sources |\n| Evaluation Status | Objective Verified |\n"
            aid1 = artifact_manager.write_controller_artifact(
                run, ot["id"], filename, rep_md.encode("utf-8")
            )
            artifact_manager.set_validation(aid1, "valid", f"Deliverable '{topic}' with deterministic citations validated")
            ids.append(aid1)

            aid2 = artifact_manager.write_controller_artifact(
                run, ot["id"], audit_filename, json.dumps(res.get("audit", {}), indent=2).encode("utf-8")
            )
            artifact_manager.set_validation(aid2, "valid", "Citation audit passed")
            ids.append(aid2)
        ev["result"] = {"kind": "controller_generated", "artifacts": ids, "node": "controller-local", "data": res}
        ev["node"] = "controller-local"
        return ev

    elif step == "research_full_cycle":
        res = mgr.run_full_cycle(project_id, objective, ot["id"])
        ev = evaluator.evaluate_research_step(step, res)
        ids = []
        if ev["accepted"]:
            rep_md = res.get("report_markdown", "")
            aid1 = artifact_manager.write_controller_artifact(
                run, ot["id"], "research_report.md", rep_md.encode("utf-8")
            )
            artifact_manager.set_validation(aid1, "valid", "Research report verified")
            ids.append(aid1)

            aid2 = artifact_manager.write_controller_artifact(
                run, ot["id"], "research_summary.json", json.dumps(res, indent=2).encode("utf-8")
            )
            artifact_manager.set_validation(aid2, "valid", "Full cycle summary validated")
            ids.append(aid2)
        ev["result"] = {"kind": "controller_generated", "artifacts": ids, "node": "controller-local", "data": res}
        ev["node"] = "controller-local"
        return ev

    return {"accepted": False, "checks": [{"name": "step_unknown", "passed": False, "detail": f"unknown research step {step}"}], "result": {}}


def _execute_controller_step_payload(run: Dict[str, Any], ot: Dict[str, Any], action: Dict[str, Any]) -> Dict[str, Any]:
    c = get_db_connection()
    try:
        by_key = {r["plan_key"]: dict(r) for r in c.execute("SELECT plan_key, attempts, dispatched_node FROM orchestration_tasks WHERE run_id=? AND state<>'superseded'", (run["id"],))}
        rev = c.execute("SELECT plan_json FROM orchestration_plan_revisions WHERE run_id=? AND status='approved' ORDER BY revision_number DESC LIMIT 1", (run["id"],)).fetchone()
    finally:
        c.close()
    plan = jload(rev["plan_json"]) if rev else {}
    declared = action.get("inputs") or [r["plan_key"] for r in by_key.values() if r["plan_key"] != ot["plan_key"]]
    inputs = _accepted_inputs(run["id"], declared)

    step = action.get("step", "")
    from app.services import loop_detection
    loop_detection.detector.record_operation(
        task_id=ot["id"],
        tool_name=f"controller_step:{step or 'unknown'}",
        arguments=action,
        attempt=ot.get("attempts", 1),
        graph_node=ot.get("plan_key"),
    )
    if step.startswith("research_"):
        try:
            ev = _run_research_controller_step(run, ot, action, plan, inputs)
        except Exception as e:
            logger.exception("Error executing research step %s", step)
            ev = {"accepted": False, "checks": [{"name": "research_step", "passed": False, "detail": str(e)[:200]}], "result": {}}
        return ev

    if step == "pipeline_kv_diagnostics":
        try:
            from app.services.orchestration.pipeline_kv_diagnostics import run_diagnostics
            diag_res = run_diagnostics()
            all_passed = diag_res.get("status") == "PASSED"
            passed_cnt = diag_res.get("passed_tests", 0)
            total_cnt = diag_res.get("total_tests", 0)
            faults_cnt = diag_res.get("faults_trapped_and_contained", 0)
            total_faults = diag_res.get("faults_injected", 0)
            checks = [
                {"name": "diagnostics_execution_passed", "passed": all_passed, "detail": f"{passed_cnt}/{total_cnt} tests passed"},
                {"name": "fault_containment_verified", "passed": faults_cnt == total_faults and total_faults > 0, "detail": f"{faults_cnt}/{total_faults} adversarial faults contained"},
            ]
            ids = []
            if all_passed:
                aid = artifact_manager.write_controller_artifact(
                    run, ot["id"], "pipeline_kv_diagnostics.json", json.dumps(diag_res, indent=2).encode("utf-8")
                )
                artifact_manager.set_validation(aid, "valid", f"All {total_cnt} tests passed; {faults_cnt} faults contained")
                ids.append(aid)
            ev = {
                "accepted": all_passed,
                "checks": checks,
                "result": {"kind": "controller_generated", "artifacts": ids, "node": "controller-local", "data": diag_res} if all_passed else {},
                "node": "controller-local",
            }
        except Exception as e:
            logger.exception("Error executing pipeline_kv_diagnostics step")
            ev = {"accepted": False, "checks": [{"name": "diagnostics_error", "passed": False, "detail": str(e)[:200]}], "result": {}}
        return ev

    title = next((d["name"] for d in plan.get("deliverables", []) if d["produced_by"] == ot["plan_key"]), "Project Report")
    prov = [{"key": k, "node": v.get("node"), "attempts": by_key.get(k, {}).get("attempts"), "kind": v.get("kind")} for k, v in sorted(inputs.items())]
    try:
        has_telemetry = any(v.get("kind") == "worker_telemetry" for v in inputs.values()) if inputs else False
        has_ai = any(v.get("kind") == "model_output" for v in inputs.values()) if inputs else False
        has_data = any(v.get("kind") in ("worker_telemetry", "model_output", "controller_generated") for v in inputs.values()) if inputs else False

        rep = reporting.assemble_project_report(run, title, inputs, prov, objective=plan.get("overview", {}).get("objective"))
        ev = evaluator.evaluate_report(rep["markdown"], has_telemetry=has_telemetry, has_ai=has_ai, has_telemetry_data=has_data)
        ev["checks"].append({"name": "has_valid_inputs", "passed": has_data,
                             "detail": "at least one validated input source" if has_data else "no input data found"})
        ev["accepted"] = ev["accepted"] and all(c["passed"] for c in ev["checks"])
        ids = []
        if ev["accepted"]:
            for fname, content in (("health_report.md", rep["markdown"]), ("health_report.json", rep["json_text"])):
                aid = artifact_manager.write_controller_artifact(run, ot["id"], fname, content.encode("utf-8"))
                artifact_manager.set_validation(aid, "valid", "report sections and labels verified")
                ids.append(aid)
        ev["result"] = {"kind": "controller_generated", "artifacts": ids, "node": "controller-local"} if ev["accepted"] else {}
        ev["node"] = "controller-local"
        return ev
    except Exception as e:
        logger.exception("Error assembling project report: %s", e)
        return {"accepted": False, "checks": [{"name": "assemble", "passed": False, "detail": str(e)[:200]}], "result": {}}


def _commit_controller_attempt(
    run_id: str,
    orch_task_id: str,
    attempt_number: int,
    ev: Dict[str, Any],
    err: Optional[str] = None,
    plan_revision_id: Optional[str] = None,
    plan_digest: Optional[str] = None,
) -> None:
    """
    Fencing-verified commit of controller step execution results (Finding F-13 & Stage 3 Safety).
    Ensures that an old/slow execution attempt cannot commit results after a newer
    attempt has been issued, or after the task has been cancelled/superseded, or after
    the approved plan revision has changed.
    """
    with db_transaction() as c:
        ot_row = c.execute("SELECT * FROM orchestration_tasks WHERE id=?", (orch_task_id,)).fetchone()
        if not ot_row:
            logger.warning("Controller task %s no longer exists; commit ignored", orch_task_id)
            return
        ot = dict(ot_row)

        # Plan revision & digest fencing check
        if plan_revision_id:
            curr_rev = c.execute(
                "SELECT id, plan_json FROM orchestration_plan_revisions WHERE run_id=? AND status='approved' ORDER BY revision_number DESC LIMIT 1",
                (run_id,)
            ).fetchone()
            curr_rev_id = curr_rev["id"] if curr_rev else None
            curr_digest = hashlib.sha256(curr_rev["plan_json"].encode("utf-8")).hexdigest() if curr_rev else None

            if curr_rev_id != plan_revision_id or (plan_digest and curr_digest != plan_digest):
                logger.warning(
                    "Fencing rejection: controller step %s attempt %s plan revision superseded (dispatched=%s, current=%s). Result discarded.",
                    orch_task_id, attempt_number, plan_revision_id, curr_rev_id
                )
                c.execute("""UPDATE orchestration_attempts SET finished_at=?, outcome='rejected', failure_class='plan_superseded_fencing',
                             evaluation_json=? WHERE orch_task_id=? AND attempt_number=?""",
                          (now_iso(), jdump({"error": "Plan revision superseded by newer revision"}), orch_task_id, attempt_number))
                log_event(c, get_run(run_id, c), "task_attempt_superseded_fencing", "orchestrator",
                          f"Attempt {attempt_number} for {ot['plan_key']} rejected: plan revision superseded", task_id=orch_task_id)
                return

        # Attempt and state fencing check
        if ot["attempts"] != attempt_number or ot["state"] != "dispatched":
            logger.warning(
                "Fencing rejection: controller step %s attempt %s superseded (current attempt=%s, state=%s). Result discarded.",
                orch_task_id, attempt_number, ot["attempts"], ot["state"]
            )
            c.execute("""UPDATE orchestration_attempts SET finished_at=?, outcome='rejected', failure_class='stale_attempt_fencing',
                         evaluation_json=? WHERE orch_task_id=? AND attempt_number=?""",
                      (now_iso(), jdump({"error": "Superseded by newer attempt or task cancelled"}), orch_task_id, attempt_number))
            log_event(c, get_run(run_id, c), "task_attempt_superseded_fencing", "orchestrator",
                      f"Attempt {attempt_number} for {ot['plan_key']} rejected by fencing (current attempt {ot['attempts']})",
                      task_id=orch_task_id)
            return

    # Task is still actively awaiting this attempt: decide result
    run = get_run(run_id)
    _decide_result(run, ot, ev, None)


def _dispatch_controller_step(run: Dict[str, Any], ot: Dict[str, Any], action: Dict[str, Any], sync: bool = False) -> None:
    """
    Dispatch a controller step (e.g. research fetching or report assembly).
    In accordance with Finding F-13:
    - Never hold the scheduler leader lock during slow I/O.
    - Transactionally records the dispatch attempt in SQLite.
    - Offloads execution to bounded background thread pool.
    - Applies cryptographic/attempt fencing verification before committing results.
    """
    attempt = _open_attempt(ot, "controller-local", None, None)
    ot_fresh = _fresh(ot["id"])
    record_event(run["id"], "task_dispatched", "orchestrator", f"{ot['plan_key']} -> controller-local (async={not sync})",
                 task_id=ot["id"], detail={"attempt": attempt, "node": "controller-local", "async": not sync})

    # Capture active approved plan revision ID and digest for commit fencing
    c = get_db_connection()
    try:
        rev_row = c.execute("SELECT id, plan_json FROM orchestration_plan_revisions WHERE run_id=? AND status='approved' ORDER BY revision_number DESC LIMIT 1", (run["id"],)).fetchone()
    finally:
        c.close()
    plan_rev_id = rev_row["id"] if rev_row else None
    plan_digest = hashlib.sha256(rev_row["plan_json"].encode("utf-8")).hexdigest() if rev_row else None

    if sync:
        ev = _execute_controller_step_payload(run, ot_fresh, action)
        _commit_controller_attempt(run["id"], ot["id"], attempt, ev, plan_revision_id=plan_rev_id, plan_digest=plan_digest)
        return

    fut_key = f"{ot['id']}:{attempt}"

    def _worker():
        try:
            ev = _execute_controller_step_payload(run, ot_fresh, action)
            _commit_controller_attempt(run["id"], ot["id"], attempt, ev, plan_revision_id=plan_rev_id, plan_digest=plan_digest)
        except Exception as ex:
            logger.exception("Async controller step %s failed: %s", ot["id"], ex)
            ev = {"accepted": False, "checks": [{"name": "controller_step", "passed": False, "detail": str(ex)[:300]}], "result": {}}
            _commit_controller_attempt(run["id"], ot["id"], attempt, ev, err=str(ex), plan_revision_id=plan_rev_id, plan_digest=plan_digest)
        finally:
            with _FUTURES_LOCK:
                _ACTIVE_FUTURES.pop(fut_key, None)
                _ACTIVE_METADATA.pop(fut_key, None)

    with _FUTURES_LOCK:
        fut = _CONTROLLER_EXECUTOR.submit(_worker)
        _ACTIVE_FUTURES[fut_key] = fut
        _ACTIVE_METADATA[fut_key] = {
            "run_id": run["id"],
            "task_id": ot["id"],
            "attempt": attempt,
            "dispatched_at": now_dt(),
        }


def _run_controller_step(run: Dict[str, Any], ot: Dict[str, Any], action: Dict[str, Any]) -> None:
    """Synchronous compatibility wrapper for _dispatch_controller_step."""
    _dispatch_controller_step(run, ot, action, sync=True)


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

def _block_deliverable_evaluation(run, deliverable_key, reason, reason_code, evaluation=None):
    """Commit blocked state and its diagnostics together; never certify a precheck.

    Re-read metadata in the write transaction so other recorded findings survive.
    A stale evaluation must not overwrite a paused/cancelled/completed run.
    """
    with db_transaction() as c:
        current = get_run(run["id"], c)
        if current["state"] == "BLOCKED":
            return current
        meta = jload(current.get("analysis_json"))
        finding = {
            "substantively_fulfilled": False,
            "substantive_status": "BLOCKED",
            "fulfillment_score": 0.0,
            "evaluation_scope": "deliverable_precheck",
            "objective_evaluation_performed": False,
            "deliverable_key": deliverable_key,
            "reason_code": reason_code,
            "gap_summary": reason,
            "checks": (evaluation or {}).get("checks", []),
            "recorded_at": now_iso(),
        }
        meta["substantive_evaluation"] = finding
        return states.transition(run["id"], "BLOCKED", "deliverable_validation_blocked",
            "orchestrator-evaluator", reason, expected_state="EVALUATING", conn=c,
            set_fields={"block_reason": reason, "analysis_json": jdump(meta)}, detail=finding)


def _evaluate_deliverables(run: Dict[str, Any]) -> None:
    meta = jload(run["analysis_json"])
    dmap = meta.get("deliverable_map", {})
    from app.services.orchestration.research_checkpoint import execute_research_checkpoint
    try:
        execute_research_checkpoint(
            project_id=run["project_id"],
            run_id=run["id"],
            stage_phase="evaluation",
            stage_name="Deliverables Evaluation Checkpoint",
            objective=jload(run["intake_json"]).get("objective", "")
        )
    except Exception as ex:
        logger.warning("Evaluation research checkpoint failed: %s", ex)

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
        if not dd:
            delivs = D.list_deliverables(run["project_id"])
            matching = [x for x in delivs if x.get("name") == d.get("name")]
            if matching:
                dd = matching[0]
                did = dd["id"]
                dmap[d["key"]] = did
            else:
                try:
                    dd = D.create_deliverable(
                        project_id=run["project_id"],
                        name=d.get("name", d.get("key")),
                        deliverable_type=d.get("deliverable_type", "document"),
                        created_by="orchestrator",
                        description=d.get("description", ""),
                    )
                    did = dd["id"]
                    dmap[d["key"]] = did
                except Exception:
                    pass
        producer = rows.get(d["produced_by"])
        if not dd or not producer or producer["state"] != "accepted":
            all_accepted = False
            continue
        if dd["status"] == "accepted":
            continue

        pres = jload(producer.get("accepted_result_json")) if producer.get("accepted_result_json") else {}
        explicit_aids = set()
        if isinstance(pres.get("artifacts"), list):
            explicit_aids.update(pres["artifacts"])
        if pres.get("artifact_id"):
            explicit_aids.add(pres["artifact_id"])

        matching_task_ids = {producer["id"]}
        for t in _orch_tasks(run["id"]):
            if t.get("plan_key") == producer.get("plan_key"):
                matching_task_ids.add(t["id"])

        candidate_artifacts = [
            a for a in arts
            if (a.get("id") in explicit_aids or a.get("orch_task_id") in matching_task_ids)
            and a.get("validation_state") == "valid"
        ]

        # Verify physical file presence in storage backend / CAS (Test L)
        valid = []
        for a in candidate_artifacts:
            p = Path(a["storage_path"])
            if p.is_file():
                valid.append(a)
            elif a.get("cas_hash"):
                from app.services.artifacts.storage import storage_backend
                if storage_backend.has_cas_blob(a["cas_hash"]):
                    valid.append(a)
                else:
                    logger.warning("Artifact %s physically missing from CAS and storage_path", a["id"])
            else:
                logger.warning("Artifact %s physically missing from storage_path %s", a["id"], a["storage_path"])

        # If a deliverable requires an artifact but all candidates are physically missing, block transition
        if not valid and (d.get("terminal") or d.get("required", True)):
            all_accepted = False
            reason = f"Required artifact for deliverable '{d['name']}' is missing from storage."
            _block_deliverable_evaluation(run, d["key"], reason, "REQUIRED_ARTIFACT_MISSING")
            return

        # 3-Layer Acceptance Evaluation (Finding F-12)
        # Check all accepted task outputs in the run to verify upstream evidence (e.g. telemetry, model output)
        inputs = _accepted_inputs(run["id"], list(rows.keys()))
        acceptance_crit = d.get("acceptance_criteria") or {}
        eval_report = evaluator.evaluate_deliverable_acceptance(
            deliverable=d,
            artifacts=valid,
            acceptance_criteria=acceptance_crit,
            context_inputs=inputs,
            autonomy_level=run["autonomy_level"],
            project_id=run["project_id"],
            run_id=run["id"],
        )

        decision = eval_report.get("decision", "REJECTED")

        if decision == "BLOCKED":
            all_accepted = False
            reason = eval_report.get("summary") or f"Deliverable '{d['name']}' is blocked on missing evidence."
            _block_deliverable_evaluation(run, d["key"], reason, "DELIVERABLE_EVIDENCE_BLOCKED", eval_report)
            return

        if decision == "REJECTED":
            all_accepted = False
            reason = eval_report.get("summary") or f"Deliverable '{d['name']}' failed acceptance checks."
            record_event(run["id"], "deliverable_validation_failed", "orchestrator-evaluator", f"{d['key']}: {reason}")
            if dd["status"] != "rejected":
                try:
                    D.review_deliverable(did, "rejected", "orchestrator-evaluator", reason)
                except Exception:
                    pass
            _block_deliverable_evaluation(run, d["key"], reason, "DELIVERABLE_ACCEPTANCE_REJECTED", eval_report)
            return

        # Prepare references for submission
        refs = "; ".join(f"{a['filename']} ({a['id']}, sha256 {a['sha256'][:12]})" for a in valid) if valid else "evidence_verified"
        if dd["status"] in ("planned", "in_progress", "changes_requested"):
            provenance_info = valid[0]['provenance'] if valid else "orchestrator"
            D.submit_deliverable(did, "orchestrator", refs, f"3-layer checks evaluated: {decision}; provenance: {provenance_info}")
            dd = D.get_deliverable(did)

        if dd["status"] == "submitted":
            if decision == "ACCEPTED" and run["autonomy_level"] >= 2:
                D.review_deliverable(did, "accepted", "orchestrator (auto)",
                                     f"Auto-accepted: all 3-layer deterministic acceptance checks passed (autonomy level {run['autonomy_level']}).")
                record_event(run["id"], "deliverable_accepted", "orchestrator (auto)", d["name"])
            else:
                # Autonomy < 2 or decision is NEEDS_HUMAN_REVIEW: require human approval
                with db_transaction() as cn:
                    ap = cn.execute("SELECT status, decided_by, rationale FROM orchestration_approvals WHERE run_id=? AND kind='deliverable' AND subject_id=? ORDER BY requested_at DESC LIMIT 1",
                                    (run["id"], did)).fetchone()
                    ap = dict(ap) if ap else None
                    if not ap or ap["status"] == "superseded":
                        req_msg = f"Accept deliverable '{d['name']}'"
                        if decision == "NEEDS_HUMAN_REVIEW":
                            req_msg += f" (Human review required: {eval_report.get('summary', 'unverifiable output')})"
                        approval_manager.request_approval(cn, run, "deliverable", did, req_msg, "medium")
                if ap and ap["status"] == "approved":
                    D.review_deliverable(did, "accepted", ap["decided_by"] or "user", ap["rationale"] or "Accepted by user.")

            dd = D.get_deliverable(did)
            if dd and dd["status"] == "accepted":
                from app.services.knowledge_store import record_knowledge_impact
                for a in valid:
                    try:
                        record_knowledge_impact(
                            knowledge_id=a["id"],
                            project_id=run["project_id"],
                            run_id=run["id"],
                            deliverable_id=did,
                            usage_type="deliverable_claim",
                            impact_summary=f"Deliverable '{d['name']}' verified with artifact {a['filename']}"
                        )
                    except Exception:
                        pass

        if dd["status"] != "accepted":
            all_accepted = False

    # Stale/superseded attempt verification on all required tasks (Finding F-13 & Stage 3 Safety)
    for d in plan.get("deliverables", []):
        producer = rows.get(d["produced_by"])
        if producer:
            c = get_db_connection()
            try:
                latest_att = c.execute(
                    "SELECT attempt_number, outcome, finished_at FROM orchestration_attempts WHERE orch_task_id=? ORDER BY attempt_number DESC LIMIT 1",
                    (producer["id"],)
                ).fetchone()
            finally:
                c.close()
            if not latest_att or latest_att["attempt_number"] != producer["attempts"] or latest_att["outcome"] != "accepted":
                logger.warning("Deliverable %s producer %s latest attempt is not accepted (attempt=%s vs task_attempts=%s, outcome=%s)",
                               d["key"], producer["id"], (latest_att or {}).get("attempt_number"), producer["attempts"], (latest_att or {}).get("outcome"))
                all_accepted = False

    # Terminal deliverables enforcement (Finding F-12, Stage 2/3 contract)
    terminal_deliverables = [d for d in plan.get("deliverables", []) if d.get("terminal")]
    if not terminal_deliverables:
        terminal_deliverables = plan.get("deliverables", [])

    all_terminal_accepted = all(
        (D.get_deliverable(dmap.get(d["key"])) or {}).get("status") == "accepted"
        for d in terminal_deliverables
    )

    if all_accepted and all_terminal_accepted and plan.get("deliverables"):
        try:
            execute_research_checkpoint(
                project_id=run["project_id"],
                run_id=run["id"],
                stage_phase="final_reporting",
                stage_name="Final Reporting & Knowledge Archival Checkpoint",
                objective=jload(run["intake_json"]).get("objective", "")
            )
        except Exception as ex:
            logger.warning("Final reporting research checkpoint failed: %s", ex)

        from app.services.knowledge_store import list_knowledge_records, evaluate_promotion_eligibility, propose_promotion
        try:
            projs_k = list_knowledge_records(project_id=run["project_id"], scope="project")
            for kr in projs_k:
                elig = evaluate_promotion_eligibility(kr)
                if elig["eligible"]:
                    try:
                        propose_promotion(kr["id"], run["project_id"], "public_technical", "Evaluated at project completion")
                    except Exception:
                        pass
        except Exception as ex:
            logger.warning("Collective knowledge promotion evaluation failed: %s", ex)

        # Record exact acceptance criteria tested and satisfying evidence in run metadata
        tested_summary = []
        for d in plan.get("deliverables", []):
            did = dmap.get(d["key"])
            producer = rows.get(d["produced_by"])
            p_arts = [a for a in arts if (producer and a.get("orch_task_id") == producer["id"]) and a.get("validation_state") == "valid"]
            tested_summary.append({
                "key": d["key"],
                "name": d["name"],
                "deliverable_type": d.get("deliverable_type"),
                "status": (D.get_deliverable(did) or {}).get("status"),
                "criteria_tested": d.get("acceptance_criteria") or {},
                "evidence_satisfied": [{"id": a["id"], "filename": a["filename"], "sha256": a["sha256"]} for a in p_arts],
            })

        # Level 3 Substantive Objective Fulfillment Evaluation (Phase 6)
        coverage_data = None
        cov_art = next((a for a in arts if a.get("filename") == "evidence_sufficiency_assessment.json"), None)
        if cov_art:
            try:
                raw_bytes = artifact_manager.read_artifact_bytes(cov_art["id"])
                coverage_data = json.loads(raw_bytes.decode("utf-8"))
            except Exception:
                coverage_data = None

        fulfillment = evaluator.evaluate_project_objective_fulfillment(
            project_id=run["project_id"],
            run=run,
            plan=plan,
            intake=jload(run["intake_json"]),
            coverage_assessment=coverage_data,
            deliverables_tested=tested_summary,
        )

        meta = jload(run["analysis_json"])
        meta["acceptance_summary"] = {
            "completed_at": now_iso(),
            "deliverables_tested": tested_summary,
        }
        meta["substantive_evaluation"] = fulfillment
        with db_transaction() as c:
            c.execute("UPDATE orchestration_runs SET analysis_json=? WHERE id=?", (jdump(meta), run["id"]))

        _write_index(run, plan, dmap)

        if fulfillment["substantively_fulfilled"]:
            states.transition(run["id"], "COMPLETED", "completed", "orchestrator", "all deliverables accepted with verified criteria and objective fulfilled", expected_state="EVALUATING")
            record_event(run["id"], "run_completed", "orchestrator", "Run completed: all deliverables accepted and objective fulfilled",
                         detail={"deliverables_tested": tested_summary, "fulfillment": fulfillment})
        else:
            block_reason = f"Substantive objective unfulfilled: {fulfillment.get('gap_summary', 'Required evidence or criteria unmet')}"
            states.transition(run["id"], "BLOCKED", "blocked", "orchestrator", block_reason[:500], expected_state="EVALUATING")
            record_event(run["id"], "run_blocked", "orchestrator", f"Run blocked from premature completion: {block_reason}",
                         detail={"deliverables_tested": tested_summary, "fulfillment": fulfillment})


def assemble_deliverables_index(run: Dict[str, Any], plan: Optional[Dict[str, Any]] = None, dmap: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Assemble deliverables_index.json referencing accepted deliverables and supporting artifacts."""
    if plan is None:
        conn = get_db_connection()
        try:
            row = conn.execute("SELECT plan_json FROM orchestration_plan_revisions WHERE run_id=? ORDER BY revision_number DESC LIMIT 1", (run["id"],)).fetchone()
            plan = json.loads(row["plan_json"]) if row else {"deliverables": []}
        finally:
            conn.close()
    if dmap is None:
        dmap = {}
        for d in plan.get("deliverables", []):
            dmap[d.get("key", "")] = d.get("id")
    arts = artifact_manager.list_artifacts(run["project_id"])
    idx = {
        "schema": "qc.deliverables_index.v1",
        "run_id": run["id"],
        "project_id": run["project_id"],
        "generated_utc": now_iso(),
        "deliverables": [
            {
                "key": d.get("key"),
                "name": d.get("name"),
                "terminal": d.get("terminal", False),
                "status": (D.get_deliverable(dmap.get(d.get("key"))) or {}).get("status", "accepted"),
                "artifacts": [
                    {"id": a["id"], "filename": a["filename"], "sha256": a["sha256"], "provenance": a["provenance"]}
                    for a in arts if not d.get("produced_by") or a.get("plan_key") == d.get("produced_by") or a.get("orch_task_id")
                ]
            }
            for d in plan.get("deliverables", [])
        ],
        "artifacts": [
            {"id": a["id"], "filename": a["filename"], "sha256": a["sha256"], "provenance": a["provenance"]}
            for a in arts
        ]
    }
    aid = artifact_manager.write_controller_artifact(run, None, "deliverables_index.json", json.dumps(idx, indent=2).encode("utf-8"))
    artifact_manager.set_validation(aid, "valid", "generated from accepted deliverables")
    return aid


def _write_index(run, plan, dmap) -> None:
    assemble_deliverables_index(run, plan, dmap)


# --- recovery & background loop -------------------------------------------------------------------------------------------------

def recover_after_restart() -> int:
    """Called once at startup: in-flight work is DB-backed, so recovery = log and let the next tick reconcile."""
    try:
        from app.services.worker import reconcile_expired_leases
        reconcile_expired_leases()
    except Exception:
        logger.exception("Failed to reconcile expired worker leases during restart recovery")

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
