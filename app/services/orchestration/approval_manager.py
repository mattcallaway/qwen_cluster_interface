"""Human approval gates and plan materialisation onto the existing project/path/stage/deliverable tables."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.database import db_transaction, get_db_connection
from app.services import deliverables as D
from app.services import projects as P
from app.services.orchestration import planner, states, task_graph, telemetry
from app.services.orchestration.common import (
    NotFound, OrchestrationError, get_run, jdump, jload, log_event, new_id, now_iso,
)

logger = logging.getLogger("qc.orchestrator.approvals")

# Actions that always need a specific, separate human authorisation regardless of autonomy level.
HIGH_IMPACT_KEYWORDS = ("deploy", "publish", "send email", "delete", "payment", "purchase", "install", "credential",
                        "modify account", "firewall", "external communication")


# --- approvals table helpers --------------------------------------------------------------------------------

def request_approval(conn, run: Dict[str, Any], kind: str, subject_id: str, requested_action: str,
                     risk: str = "low") -> str:
    """Idempotent: at most one pending approval per (run, kind, subject)."""
    row = conn.execute("SELECT id FROM orchestration_approvals WHERE run_id=? AND kind=? AND subject_id=? AND status='pending'",
                       (run["id"], kind, subject_id)).fetchone()
    if row:
        return row["id"]
    aid = new_id("apr")
    conn.execute("""INSERT INTO orchestration_approvals (id, run_id, kind, subject_id, requested_action, risk, status, requested_at)
                    VALUES (?,?,?,?,?,?,'pending',?)""", (aid, run["id"], kind, subject_id, requested_action[:500], risk, now_iso()))
    log_event(conn, run, "approval_requested", "system", f"{kind}: {requested_action[:200]}", detail={"approval_id": aid, "risk": risk})
    return aid


def has_approval(conn, run_id: str, kind: str, subject_id: str) -> bool:
    return conn.execute("SELECT 1 FROM orchestration_approvals WHERE run_id=? AND kind=? AND subject_id=? AND status='approved'",
                        (run_id, kind, subject_id)).fetchone() is not None


def list_pending(run_id: str) -> List[Dict[str, Any]]:
    c = get_db_connection()
    try:
        return [dict(r) for r in c.execute(
            "SELECT * FROM orchestration_approvals WHERE run_id=? AND status='pending' ORDER BY requested_at", (run_id,))]
    finally:
        c.close()


def decide(run_id: str, approval_id: str, approve: bool, actor: str, rationale: str = "") -> Dict[str, Any]:
    from app.services.orchestration import engine
    with db_transaction() as c:
        run = get_run(run_id, c)
        row = c.execute("SELECT * FROM orchestration_approvals WHERE id=? AND run_id=?", (approval_id, run_id)).fetchone()
        if not row:
            raise NotFound("Approval not found.")
        if row["status"] != "pending":
            return dict(row)  # idempotent
        c.execute("UPDATE orchestration_approvals SET status=?, decided_by=?, decided_at=?, rationale=? WHERE id=?",
                  ("approved" if approve else "denied", actor, now_iso(), rationale[:500], approval_id))
        log_event(c, run, "approval_decided", actor, f"{row['kind']} {'approved' if approve else 'denied'}: {rationale[:200]}",
                  task_id=row["subject_id"] if row["kind"] == "task" else None, detail={"approval_id": approval_id})
        kind, subject = row["kind"], row["subject_id"]
        if not approve and kind == "task":
            c.execute("UPDATE orchestration_tasks SET state='blocked', last_error='approval denied' WHERE id=? AND state='pending'", (subject,))
    if approve and kind == "budget":
        telemetry.set_limits(run_id, {"max_tasks": 2 * telemetry.get_budget(run_id)["max_tasks"],
                                      "max_inference_requests": 2 * telemetry.get_budget(run_id)["max_inference_requests"],
                                      "max_tokens": 2 * telemetry.get_budget(run_id)["max_tokens"],
                                      "max_retries_total": 2 * telemetry.get_budget(run_id)["max_retries_total"],
                                      "max_runtime_seconds": 2 * telemetry.get_budget(run_id)["max_runtime_seconds"]})
        if get_run(run_id)["state"] == "BLOCKED":
            states.transition(run_id, "RUNNING", "budget_expanded", actor, "budget expansion approved")
    if not approve and kind == "deliverable" and get_run(run_id)["state"] == "EVALUATING":
        states.transition(run_id, "BLOCKED", "deliverable_rejected", actor, f"Deliverable rejected: {rationale[:200]}",
                          set_fields={"block_reason": f"Deliverable rejected by {actor}: {rationale[:200]}"})
    if approve and kind == "clarification":
        pass  # answers are supplied through the clarify endpoint
    return {"id": approval_id, "status": "approved" if approve else "denied"}


# --- plan approval + materialisation -------------------------------------------------------------------------------

def approve_plan(run_id: str, actor: str, rationale: str = "", revision_id: Optional[str] = None) -> Dict[str, Any]:
    run = get_run(run_id)
    if run["state"] != "AWAITING_PLAN_APPROVAL":
        raise OrchestrationError(f"There is no plan awaiting approval (state {run['state']}).")
    rev = planner.get_revision(revision_id or run["current_revision_id"])
    if rev["run_id"] != run_id:
        raise NotFound("Revision does not belong to this run.")
    if rev["status"] != "proposed":
        raise OrchestrationError(f"Revision is {rev['status']}, not proposed.")
    # Re-validate against the *current* fleet; never trust a stored validation blindly.
    from app.services.orchestration import model_router
    v = task_graph.validate_plan(rev["plan"], model_router.node_inventory())
    if not v["valid"]:
        raise OrchestrationError("Plan is not valid: " + "; ".join(v["errors"][:5]))
    if v["capability_issues"]:
        raise OrchestrationError("Plan cannot run on the current fleet: " + "; ".join(v["capability_issues"][:3]))
    materialize(run, rev, actor)
    with db_transaction() as c:
        c.execute("UPDATE orchestration_plan_revisions SET status='superseded' WHERE run_id=? AND status='approved'", (run_id,))
        c.execute("UPDATE orchestration_plan_revisions SET status='approved', approved_by=?, approved_at=? WHERE id=?",
                  (actor, now_iso(), rev["id"]))
        run = get_run(run_id, c)
        c.execute("""UPDATE orchestration_approvals SET status='superseded' WHERE run_id=? AND kind='plan' AND status='pending'""", (run_id,))
        aid = new_id("apr")
        c.execute("""INSERT INTO orchestration_approvals (id, run_id, kind, subject_id, requested_action, risk, status,
                     requested_at, decided_by, decided_at, rationale) VALUES (?,?,'plan',?,?,'medium','approved',?,?,?,?)""",
                  (aid, run_id, rev["id"], f"Execute plan revision {rev['revision_number']}", now_iso(), actor, now_iso(), rationale[:500]))
        log_event(c, run, "plan_approved", actor, f"revision {rev['revision_number']}: {rationale[:200]}",
                  detail={"revision_id": rev["id"]})
    return states.transition(run_id, "READY", "plan_approved", actor, f"revision {rev['revision_number']} approved",
                             expected_state="AWAITING_PLAN_APPROVAL")


def reject_plan(run_id: str, actor: str, reason: str = "") -> Dict[str, Any]:
    run = get_run(run_id)
    if run["state"] != "AWAITING_PLAN_APPROVAL":
        raise OrchestrationError("No plan is awaiting approval.")
    with db_transaction() as c:
        c.execute("UPDATE orchestration_plan_revisions SET status='rejected' WHERE id=? AND status='proposed'",
                  (run["current_revision_id"],))
    target = "REPLANNING" if _has_materialized(run_id) else "DRAFT"
    return states.transition(run_id, target, "plan_rejected", actor, reason or "plan rejected",
                             expected_state="AWAITING_PLAN_APPROVAL")


def _has_materialized(run_id: str) -> bool:
    c = get_db_connection()
    try:
        return c.execute("SELECT 1 FROM orchestration_tasks WHERE run_id=? LIMIT 1", (run_id,)).fetchone() is not None
    finally:
        c.close()


def materialize(run: Dict[str, Any], rev: Dict[str, Any], actor: str) -> None:
    """Create/extend path, stages, deliverables and orchestration task rows for an approved revision.

    Real ``tasks`` rows are created lazily at dispatch (one per attempt); here we only create the durable
    plan structures. Re-materialisation for a replan reuses the path/stages/deliverables by name and carries
    accepted work over when the action is unchanged.
    """
    plan = rev["plan"]
    run_id, project_id = run["id"], run["project_id"]
    meta = jload(run["analysis_json"])
    stage_map: Dict[str, str] = dict(meta.get("stage_map", {}))
    deliv_map: Dict[str, str] = dict(meta.get("deliverable_map", {}))
    proj = P.get_project(project_id)
    if not proj:
        raise NotFound("Project not found.")
    main = plan["paths"][0]
    path_id = meta.get("path_id")
    if not path_id:
        ov = plan["overview"]
        path = P.create_path(project_id, actor, name=main["name"][:120], rationale=main["rationale"],
                             description=ov.get("scope", ""), assumptions="\n".join(ov.get("assumptions", [])),
                             constraints_text="\n".join(ov.get("constraints", [])),
                             risks="\n".join(f"{r['description']} -> {r['mitigation']}" for r in plan.get("risks", [])),
                             expected_outcome=ov.get("expected_outcome", ""))
        path_id = path["id"]
        P.change_path_status(project_id, path_id, "approved", actor, f"Orchestration plan revision {rev['revision_number']} approved")
        P.select_path(project_id, path_id, actor, "Selected by orchestration plan approval")
        for alt in plan["paths"][1:]:
            try:
                P.create_path(project_id, actor, name=alt["name"][:120] or "Alternative", rationale=alt["rationale"] or "Alternative approach")
            except Exception as e:
                logger.info("alternative path not recorded: %s", e)
    proj = P.get_project(project_id)
    if proj["status"] in ("draft", "planning", "paused"):
        P.change_project_status(project_id, "active", actor, "Orchestration plan approved")
    existing_stages = {s["name"]: s["id"] for s in P.list_stages(path_id)} if hasattr(P, "list_stages") else {}
    for s in main["stages"]:
        if s["key"] in stage_map:
            continue
        if s["name"] in existing_stages:
            stage_map[s["key"]] = existing_stages[s["name"]]
            continue
        st = P.create_stage(project_id, path_id, actor, name=s["name"], objective=s["objective"],
                            completion_criteria="All tasks in the stage accepted by deterministic evaluation.")
        stage_map[s["key"]] = st["id"]
    existing_dels = {d["name"]: d["id"] for d in D.list_deliverables(project_id)}
    for d in plan["deliverables"]:
        if d["key"] in deliv_map:
            continue
        if d["name"] in existing_dels:
            deliv_map[d["key"]] = existing_dels[d["name"]]
            continue
        dd = D.create_deliverable(project_id, d["name"], d["type"], actor, path_id=path_id,
                                  stage_id=stage_map.get(d["stage"]) if d["stage"] else None,
                                  description=d["description"], acceptance_criteria=d["acceptance_criteria"])
        deliv_map[d["key"]] = dd["id"]
    with db_transaction() as c:
        run = get_run(run_id, c)
        prior = {r["plan_key"]: dict(r) for r in c.execute(
            "SELECT * FROM orchestration_tasks WHERE run_id=? AND state NOT IN ('superseded','cancelled')", (run_id,)).fetchall()}
        id_by_key: Dict[str, str] = {}
        for t in plan["tasks"]:
            otid = new_id("ot")
            id_by_key[t["key"]] = otid
            action_json = jdump(t["action"])
            carry = prior.get(t["key"])
            state, accepted_json, cur_task = "pending", "{}", None
            if carry and carry["state"] == "accepted" and carry["action_json"] == action_json:
                state, accepted_json, cur_task = "accepted", carry["accepted_result_json"], carry["current_task_id"]
            c.execute("""INSERT INTO orchestration_tasks (id, run_id, revision_id, plan_key, stage_id, title, agent_role,
                action_json, acceptance_json, required_capabilities_json, preferred_models_json, allowed_nodes_json,
                priority, max_attempts, timeout_seconds, approval_mode, risk, state, accepted_result_json, current_task_id,
                created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (otid, run_id, rev["id"], t["key"], stage_map.get(t["stage"]), t["title"], t["agent_role"], action_json,
                       jdump(t["acceptance"]), jdump(t["required_capabilities"]), jdump(t["preferred_models"]),
                       jdump(t["allowed_nodes"]), t["priority"], t["max_attempts"], t["timeout_seconds"], t["approval"],
                       t["risk"], state, accepted_json, cur_task, now_iso(), now_iso()))
        for t in plan["tasks"]:
            for dep in t["depends_on"]:
                c.execute("INSERT INTO orchestration_task_deps (orch_task_id, depends_on_id) VALUES (?,?)",
                          (id_by_key[t["key"]], id_by_key[dep]))
        # Everything from earlier revisions that is not carried over is superseded (history retained).
        c.execute("""UPDATE orchestration_tasks SET state='superseded', updated_at=? WHERE run_id=? AND revision_id<>?
                     AND state<>'superseded'""", (now_iso(), run_id, rev["id"]))
        meta.update({"path_id": path_id, "stage_map": stage_map, "deliverable_map": deliv_map})
        c.execute("UPDATE orchestration_runs SET analysis_json=? WHERE id=?", (jdump(meta), run_id))
        telemetry.ensure_budget(c, run_id)
        log_event(c, run, "plan_materialized", actor, f"{len(plan['tasks'])} tasks, {len(plan['deliverables'])} deliverables",
                  detail={"path_id": path_id, "revision_id": rev["id"]})
