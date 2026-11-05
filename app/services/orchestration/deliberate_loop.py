"""Deliberate Task-Solving Loop, Adaptive Effort Escalation, Verification/Repair & Role Routing (Increment 18).

Implements the unified persisted task-solving cycle:
Understand -> clarify material gaps -> plan -> select next action -> retrieve context ->
execute -> inspect results -> verify -> revise or deliver.

Key capabilities:
1. Mode classification: Direct-response mode for simple self-contained questions vs Durable-project mode
   for substantial assignments.
2. Bounded 7-step adaptive effort escalation ladder with explicit time, token, call, retry, and tool budgets.
3. Measured role-based model routing (`qwen2.5:3b` for complex planning, multi-constraint reasoning, and
   software/JSON synthesis; `qwen2.5:1.5b` for fast extraction, classification, and short summaries).
4. Truncation detection across prose, JSON, and code outputs.
5. Seven-state verification status tracking (`proposed`, `observed`, `supported`, `verified_for_scope`,
   `partial`, `blocked`, `failed`) and 5-step failure repair cycle under fresh native task/lease execution
   without exposing hidden test oracles in model prompts.
6. Scoped procedure & outcome learning (`procedure_outcome_registry`) with provenance and revocation.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.database import db_transaction, get_db_connection
from app.services import projects as P
from app.services import tasks as T
from app.services.orchestration import (
    artifact_manager,
    context_manager,
    engine,
    evaluator,
    intake as intake_mod,
    model_router,
    planner,
    telemetry,
    tool_catalogue,
)
from app.services.orchestration.common import get_run, get_run_for_project, jdump, jload, new_id, now_iso

logger = logging.getLogger("qc.orchestrator.deliberate_loop")


# ---------------------------------------------------------------------------
# 1. Mode Classifier: Direct Answer vs Durable Project (Section 1 & Section 8)
# ---------------------------------------------------------------------------

SUBSTANTIAL_INDICATORS = re.compile(
    r"\b(project|deliverables?|compare\s+\d+|supplier|quotes?|reconcil|budget\s+ceiling|implement|build\s+a|"
    r"repository|bug\s*fix|unit\s+tests?|web\s+app|dashboard|dataset|csv|multi-step|milestone|audit|research\s+and\s+cite)\b",
    re.I,
)


def classify_request_mode(user_message: str, explicit_intake: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Classify whether a user request should use direct-response mode or durable-project mode."""
    msg = (user_message or "").strip()
    if explicit_intake and (
        explicit_intake.get("deliverables")
        or explicit_intake.get("requested_deliverables")
        or explicit_intake.get("constraints")
        or explicit_intake.get("force_project")
    ):
        return {
            "mode": "durable_project",
            "reason": "Explicit structured deliverables/constraints provided in intake.",
        }

    is_short_question = (
        len(msg) < 160
        and not SUBSTANTIAL_INDICATORS.search(msg)
        and "\n" not in msg
    )
    if is_short_question:
        return {
            "mode": "direct_response",
            "reason": "Self-contained conversational question requiring no durable project machinery.",
        }

    if SUBSTANTIAL_INDICATORS.search(msg) or len(msg) >= 160 or "\n-" in msg or "\n1." in msg:
        return {
            "mode": "durable_project",
            "reason": "Substantial assignment involving deliverables, constraints, research, calculation, or code verification.",
        }

    return {
        "mode": "direct_response",
        "reason": "Simple request handled directly without project overhead.",
    }


# ---------------------------------------------------------------------------
# 2. Bounded 7-Step Escalation Ladder & Adaptive Effort Budgets (Section 4)
# ---------------------------------------------------------------------------

ESCALATION_LADDER: Dict[int, Dict[str, Any]] = {
    1: {
        "step": 1,
        "name": "direct_or_single_pass",
        "description": "Single-pass bounded inference or direct deterministic tool execution.",
        "max_model_calls": 1,
        "max_tokens": 512,
        "max_tool_calls": 2,
        "timeout_seconds": 60,
    },
    2: {
        "step": 2,
        "name": "structured_decomposition_and_tools",
        "description": "Decompose subproblems and invoke deterministic calculation/comparison/inspection tools.",
        "max_model_calls": 2,
        "max_tokens": 1024,
        "max_tool_calls": 5,
        "timeout_seconds": 120,
    },
    3: {
        "step": 3,
        "name": "targeted_retrieval_and_evidence_check",
        "description": "Retrieve scoped source snapshots, strip untrusted directives, and verify claim support.",
        "max_model_calls": 3,
        "max_tokens": 1536,
        "max_tool_calls": 8,
        "timeout_seconds": 180,
    },
    4: {
        "step": 4,
        "name": "role_model_upgrade",
        "description": "Route complex reasoning, code generation, or JSON synthesis to stronger local model (qwen2.5:3b).",
        "max_model_calls": 4,
        "max_tokens": 2048,
        "max_tool_calls": 10,
        "timeout_seconds": 240,
    },
    5: {
        "step": 5,
        "name": "targeted_failure_repair",
        "description": "Run 5-step failure diagnosis and targeted repair prompt against independent verifier feedback.",
        "max_model_calls": 6,
        "max_tokens": 2048,
        "max_tool_calls": 12,
        "timeout_seconds": 300,
    },
    6: {
        "step": 6,
        "name": "selective_replan_preserving_work",
        "description": "Replan failed or invalidated subgraph while preserving verified upstream artifacts.",
        "max_model_calls": 8,
        "max_tokens": 3072,
        "max_tool_calls": 15,
        "timeout_seconds": 360,
    },
    7: {
        "step": 7,
        "name": "honest_blocker_or_human_clarification",
        "description": "Halt unproductive retries; report exact verified partial work, blocker cause, and required user decision.",
        "max_model_calls": 8,
        "max_tokens": 3072,
        "max_tool_calls": 15,
        "timeout_seconds": 360,
    },
}


# ---------------------------------------------------------------------------
# 3. Capability-Aware Role Routing & Truncation Detection (Section 7)
# ---------------------------------------------------------------------------

ROLE_MODEL_PREFERENCES: Dict[str, List[str]] = {
    "complex_reasoning": ["qwen2.5:3b", "qwen2.5:1.5b"],
    "code_generation": ["qwen2.5:3b", "qwen2.5:1.5b"],
    "structured_json": ["qwen2.5:3b", "qwen2.5:1.5b"],
    "planning": ["qwen2.5:3b", "qwen2.5:1.5b"],
    "OptionComparison": ["qwen2.5:3b", "qwen2.5:1.5b"],
    "fast_extraction": ["qwen2.5:1.5b", "qwen2.5:3b"],
    "short_summary": ["qwen2.5:1.5b", "qwen2.5:3b"],
    "direct_qa": ["qwen2.5:1.5b", "qwen2.5:3b"],
}


def select_model_for_role(role: str, available_models: Optional[List[str]] = None) -> Dict[str, Any]:
    """Select the optimal installed local model for a task role based on measured capability."""
    if available_models is None:
        nodes = model_router.node_inventory()
        models_set = set()
        for n in nodes:
            if n.get("enabled") and n.get("health") == "online":
                for m in n.get("models", []):
                    models_set.add(m)
        available_models = sorted(models_set) if models_set else ["qwen2.5:3b", "qwen2.5:1.5b"]

    prefs = ROLE_MODEL_PREFERENCES.get(role, ["qwen2.5:3b", "qwen2.5:1.5b"])
    for cand in prefs:
        if cand in available_models:
            return {
                "role": role,
                "selected_model": cand,
                "available_models": available_models,
                "rationale": (
                    f"Selected '{cand}' for role '{role}': "
                    + (
                        "3B model preferred for multi-constraint reasoning, code generation, and structured JSON fidelity."
                        if "3b" in cand
                        else "1.5B model sufficient for lightweight extraction/summarization with lower latency."
                    )
                ),
            }
    chosen = available_models[0] if available_models else "qwen2.5:1.5b"
    return {
        "role": role,
        "selected_model": chosen,
        "available_models": available_models,
        "rationale": f"Fallback to available model '{chosen}' for role '{role}'.",
    }


def detect_generation_truncation(
    text: str,
    gen_tokens: Optional[int] = None,
    num_predict: Optional[int] = None,
    expected_format: str = "prose",  # "prose" | "json" | "code"
) -> Dict[str, Any]:
    """Detect whether model output was truncated by token limits or unbalanced structured syntax."""
    stripped = (text or "").strip()
    if not stripped:
        return {"truncated": True, "reason": "empty_output"}

    hit_token_cap = bool(
        num_predict is not None
        and gen_tokens is not None
        and int(gen_tokens) >= int(num_predict)
    )

    if expected_format == "json":
        if stripped.count("{") != stripped.count("}") or stripped.count("[") != stripped.count("]"):
            return {
                "truncated": True,
                "reason": f"Unbalanced JSON delimiters (hit_token_cap={hit_token_cap}, gen_tokens={gen_tokens}/{num_predict})",
            }
        try:
            json.loads(stripped)
        except ValueError as ve:
            if hit_token_cap:
                return {"truncated": True, "reason": f"JSON parse failed at token cap ({gen_tokens}/{num_predict}): {ve}"}

    if expected_format == "code":
        if stripped.count("```") % 2 == 1:
            return {"truncated": True, "reason": "Unclosed markdown code fence in code generation"}

    if hit_token_cap and not any(stripped.endswith(c) for c in (".", "!", "?", "}", "]", "```", "---", "\"")):
        return {
            "truncated": True,
            "reason": f"Generation hit token cap ({gen_tokens}/{num_predict}) without terminal punctuation/delimiter",
        }

    return {"truncated": False, "reason": ""}


# ---------------------------------------------------------------------------
# 4. Five-Step Failure Repair Cycle with Hidden Oracle Protection (Section 6)
# ---------------------------------------------------------------------------

def build_sanitized_repair_feedback(
    failure_class: str,
    verification_checks: List[Dict[str, Any]],
    raw_error: str = "",
    attempt_number: int = 1,
) -> Dict[str, Any]:
    """Execute Steps 1-4 of the 5-step failure repair cycle without leaking hidden test code/oracles.

    Step 1: Classify failure (`syntax_error`, `test_assertion_failure`, `constraint_violation`, `truncation`, `missing_evidence`).
    Step 2: Isolate root cause from check names and high-level diagnostic messages, stripping any `assert ... == <secret>`
            or test file paths/contents so the model cannot hardcode hidden test answers.
    Step 3: Identify unaffected upstream components to preserve.
    Step 4: Formulate a targeted repair instruction and recommended escalation step.
    """
    failed_checks = [c for c in verification_checks if not c.get("passed", False)]
    failed_names = [c.get("name", "check") for c in failed_checks]

    # Strip literal expected oracle values or test file contents from error messages
    sanitized_diagnostics: List[str] = []
    for c in failed_checks:
        detail = str(c.get("detail") or "")
        # Redact raw assert equality literals that could leak hidden oracle constants
        redacted = re.sub(r"(AssertionError:.*?)==\s*['\"]?[^'\"\n]+['\"]?", r"\1(expected value hidden by oracle guard)", detail)
        redacted = re.sub(r"tests/[A-Za-z0-9_./-]+", "[independent_test_suite]", redacted)
        sanitized_diagnostics.append(f"{c.get('name')}: {redacted[:240]}")

    if raw_error and not sanitized_diagnostics:
        redacted_err = re.sub(r"(AssertionError:.*?)==\s*['\"]?[^'\"\n]+['\"]?", r"\1(expected value hidden)", raw_error)
        sanitized_diagnostics.append(redacted_err[:240])

    if any("syntax" in n for n in failed_names):
        category = "syntax_error"
        repair_instruction = "Fix the syntax error so the module parses cleanly and exports all required functions."
        next_escalation = 4
    elif any("truncated" in n for n in failed_names):
        category = "truncation"
        repair_instruction = "Emit a concise, complete response within token limits without repeating prompt boilerplate."
        next_escalation = 4
    elif any("test_suite" in n or "isolated_execution" in n for n in failed_names):
        category = "test_assertion_failure"
        repair_instruction = (
            "Review the edge cases, boundary conditions, and return types required by the specification. "
            "Do not hardcode example outputs; implement the general algorithm."
        )
        next_escalation = 5
    elif any("citation" in n or "claim" in n or "factual" in n for n in failed_names):
        category = "missing_or_contradicted_evidence"
        repair_instruction = "Restrict all claims strictly to verified sources in <evidence_data> and include explicit source citations."
        next_escalation = 3
    else:
        category = failure_class or "validation_failure"
        repair_instruction = "Address the failed validation checks while preserving all mandatory user constraints."
        next_escalation = min(7, 3 + attempt_number)

    return {
        "schema": "qc.repair_plan.v1",
        "attempt_number": attempt_number,
        "failure_category": category,
        "failed_check_names": failed_names,
        "sanitized_diagnostics": sanitized_diagnostics,
        "repair_instruction": repair_instruction,
        "recommended_escalation_step": next_escalation,
        "oracle_exposure_prevented": True,
    }


# ---------------------------------------------------------------------------
# 5. Native Worker Inference & Script Execution Helper
# ---------------------------------------------------------------------------

def execute_native_worker_inference(
    *,
    project_id: str,
    run_id: Optional[str],
    orch_task_id: Optional[str],
    purpose: str,
    prompt: str,
    role: str = "complex_reasoning",
    preferred_model: Optional[str] = None,
    allowed_nodes: Optional[List[str]] = None,
    max_tokens: int = 512,
    timeout_seconds: int = 180,
    queue_timeout_seconds: int = 30,
    poll_interval_seconds: float = 0.15,
    approval_required: bool = False,
    approval_granted: bool = False,
    worker_poll_step: Optional[Callable[[str, str], None]] = None,
) -> Dict[str, Any]:
    """Dispatch inference through the authoritative worker protocol without runtime self-enrollment or SQL bypasses.

    Enforces:
    1. Project dispatch gate (`working_memory.is_project_dispatch_stopped`) and run budget (`telemetry.check_budget`).
    2. Explicit worker enrollment, active authentication credential (`worker_auth_tokens`), administrative
       enablement, fresh heartbeat, operational hold checks, RAM floor, and verified capability probe
       (`infrastructure_verifications`) via `model_router.select_node(..., enforce_verified=True)`.
    3. Normal approval policy (`approval_required` / `autonomy_level`); never auto-approves protected work.
    4. Normal provider submission (`WorkerOllamaProvider.submit`) and polling (`WorkerOllamaProvider.poll`)
       where the separate authenticated worker leases via `W.lease_task_for_worker` and submits via
       `W.submit_task_result`. Never executes `vps_agent.executor.execute_task` in-process on the controller
       and never inserts direct SQL 2099 leases.
    """
    from app.services.orchestration import working_memory as wm

    # 1. Check project dispatch gate
    if project_id and wm.is_project_dispatch_stopped(project_id):
        return {
            "status": "blocked",
            "error_class": "dispatch_stopped",
            "error": f"Project '{project_id}' dispatch is stopped for park/cancel lifecycle.",
            "lease_id": None,
        }

    # 2. Check run budget and autonomy approval policy
    effective_run_id = run_id
    run_obj = get_run(run_id) if run_id else (get_run_for_project(project_id) if project_id else None)
    if run_obj and not effective_run_id:
        effective_run_id = run_obj["id"]

    if effective_run_id:
        budget_ok, budget_reason = telemetry.check_budget(
            effective_run_id, will_add_task=True, will_add_inference=True
        )
        if not budget_ok:
            return {
                "status": "blocked",
                "error_class": "budget_exhausted",
                "error": f"Run budget exhausted: {budget_reason}",
                "lease_id": None,
            }

    needs_approval = bool(
        approval_required
        or (run_obj is not None and int(run_obj.get("autonomy_level", 2)) == 0)
    )

    # 3. Select eligible, authenticated, capability-verified worker node
    routing = select_model_for_role(role)
    preferred_candidates = (
        [preferred_model]
        if preferred_model
        else ROLE_MODEL_PREFERENCES.get(role, ["qwen2.5:3b", "qwen2.5:1.5b"])
    )
    synth_ot = {
        "action": {"type": "inference", "model": preferred_model},
        "preferred_models": preferred_candidates,
        "allowed_nodes": allowed_nodes or [],
    }
    node, model_name, reasons = model_router.select_node(synth_ot, model_router.node_inventory())
    if not node or not model_name:
        return {
            "status": "blocked",
            "error_class": "no_eligible_worker",
            "reasons": reasons,
            "error": f"No eligible authenticated worker with verified capability: {'; '.join(reasons)}",
            "lease_id": None,
        }

    node_id = str(node["id"])
    provider = model_router.get_provider()
    valid_purposes = {"analysis", "planning", "plan_repair", "plan_critique", "task", "evaluation"}
    db_purpose = purpose if purpose in valid_purposes else "task"

    # 4. Submit task via WorkerOllamaProvider (respecting approval_required without auto-approving)
    req_id = provider.submit(
        run_id=effective_run_id,
        orch_task_id=orch_task_id,
        purpose=db_purpose,
        node_id=node_id,
        model=model_name,
        prompt=prompt,
        max_tokens=min(int(max_tokens), 2048),
        timeout_seconds=int(timeout_seconds),
        project_id=project_id,
        approval_required=bool(needs_approval and not approval_granted),
    )

    conn = get_db_connection()
    try:
        req_row = conn.execute(
            "SELECT task_id FROM model_inference_requests WHERE id = ?",
            (req_id,),
        ).fetchone()
        task_id = str(req_row["task_id"]) if req_row else ""
    finally:
        conn.close()

    if needs_approval and not approval_granted:
        return {
            "status": "awaiting_approval",
            "error_class": "approval_required",
            "request_id": req_id,
            "task_id": task_id,
            "worker_task_id": task_id,
            "node_id": node_id,
            "model": model_name,
            "lease_id": None,
            "error": f"Task '{task_id}' requires explicit operator approval before worker dispatch.",
        }

    if effective_run_id:
        telemetry.record_usage(effective_run_id, tasks=1)

    # 5. Poll for authenticated worker lease & completion
    deadline = time.monotonic() + float(timeout_seconds)
    effective_queue_timeout = min(float(timeout_seconds), float(queue_timeout_seconds))
    poll_res: Dict[str, Any] = {"status": "submitted"}

    while time.monotonic() < deadline:
        if worker_poll_step is not None:
            worker_poll_step(node_id, task_id)

        poll_res = provider.poll(
            req_id,
            timeout_seconds=int(timeout_seconds),
            queue_timeout_seconds=max(1, int(effective_queue_timeout)),
        )
        if poll_res["status"] != "submitted":
            break
        time.sleep(poll_interval_seconds)

    if poll_res["status"] == "submitted":
        provider.cancel(req_id)
        poll_res = {
            "status": "timeout",
            "error_class": "model_timeout",
            "timeout_type": "execution_timeout",
            "task_id": task_id,
            "node_id": node_id,
            "model": model_name,
        }

    # 6. Look up authoritative lease provenance from worker_task_leases
    lease_id: Optional[str] = None
    idem_key: Optional[str] = None
    conn = get_db_connection()
    try:
        lease_row = conn.execute(
            """SELECT id, idempotency_key, status, lease_expires_at
               FROM worker_task_leases
               WHERE task_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (task_id,),
        ).fetchone()
        if lease_row:
            lease_id = str(lease_row["id"])
            idem_key = str(lease_row["idempotency_key"])
    finally:
        conn.close()

    response_text = str(poll_res.get("response") or "")
    gen_tokens = int(poll_res.get("gen_tokens") or 0)
    prompt_tokens = int(poll_res.get("prompt_tokens") or 0)
    runtime_s = float(poll_res.get("latency_s") or 0.0)
    trunc = (
        detect_generation_truncation(response_text, gen_tokens=gen_tokens, num_predict=max_tokens)
        if poll_res["status"] == "completed"
        else {"truncated": False, "reason": ""}
    )

    return {
        "status": poll_res["status"],
        "error_class": poll_res.get("error_class"),
        "request_id": req_id,
        "task_id": task_id,
        "worker_task_id": task_id,
        "lease_id": lease_id,
        "idempotency_key": idem_key,
        "node_id": node_id,
        "model": poll_res.get("model") or model_name,
        "role": role,
        "routing_rationale": routing["rationale"],
        "response": response_text,
        "gen_tokens": gen_tokens,
        "prompt_tokens": prompt_tokens,
        "total_tokens": gen_tokens + prompt_tokens,
        "runtime_seconds": runtime_s,
        "truncated": trunc["truncated"],
        "truncation_reason": trunc["reason"],
        "error_class": poll_res.get("error_class"),
        "error": poll_res.get("error_class") if poll_res["status"] != "completed" else None,
    }


# ---------------------------------------------------------------------------
# 6. Scoped Procedure & Outcome Learning Registry (Section 9)
# ---------------------------------------------------------------------------

def record_procedure_outcome(
    *,
    task_category: str,
    procedure_key: str,
    outcome_status: str,
    procedure_summary: str,
    project_id: Optional[str] = None,
    scope_type: str = "task_category",
    model_id: Optional[str] = None,
    failure_pattern: str = "",
    evidence_run_id: Optional[str] = None,
    evidence_task_id: Optional[str] = None,
    verification_digest: str = "",
    latency_seconds: Optional[float] = None,
    tokens_used: Optional[int] = None,
) -> Dict[str, Any]:
    """Record a verified procedure outcome or failure pattern with provenance in `procedure_outcome_registry`."""
    rec_id = new_id("proc")
    now = now_iso()
    with db_transaction() as conn:
        conn.execute(
            """INSERT INTO procedure_outcome_registry (
                id, scope_type, project_id, task_category, procedure_key, model_id,
                outcome_status, procedure_summary, failure_pattern, evidence_run_id,
                evidence_task_id, verification_digest, latency_seconds, tokens_used,
                revoked, revoked_reason, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '', ?, ?)""",
            (
                rec_id,
                scope_type,
                project_id,
                task_category,
                procedure_key,
                model_id,
                outcome_status,
                procedure_summary,
                failure_pattern,
                evidence_run_id,
                evidence_task_id,
                verification_digest,
                latency_seconds,
                tokens_used,
                now,
                now,
            ),
        )
    return {"id": rec_id, "task_category": task_category, "procedure_key": procedure_key, "outcome_status": outcome_status}


def query_verified_procedures(task_category: str, include_revoked: bool = False) -> List[Dict[str, Any]]:
    """Retrieve non-revoked verified procedures for a task category."""
    conn = get_db_connection()
    try:
        sql = "SELECT * FROM procedure_outcome_registry WHERE task_category = ?"
        if not include_revoked:
            sql += " AND revoked = 0 AND outcome_status = 'verified_success'"
        sql += " ORDER BY created_at DESC LIMIT 10"
        return [dict(r) for r in conn.execute(sql, (task_category,)).fetchall()]
    finally:
        conn.close()


def revoke_procedure_outcome(procedure_id: str, reason: str) -> bool:
    """Revoke a previously recorded procedure so it is never reused."""
    with db_transaction() as conn:
        cur = conn.execute(
            "UPDATE procedure_outcome_registry SET revoked = 1, outcome_status = 'revoked', revoked_reason = ?, updated_at = ? WHERE id = ?",
            (reason, now_iso(), procedure_id),
        )
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# 7. Coherent User Progress Snapshot (Section 8)
# ---------------------------------------------------------------------------

def build_coherent_user_status(project_id: str) -> Dict[str, Any]:
    """Assemble the user-facing structured progress report for a durable project."""
    ledger = intake_mod.load_durable_requirement_ledger(project_id)
    mem = context_manager.get_project_memory_state(project_id)
    run = get_run_for_project(project_id)
    arts = artifact_manager.list_artifacts(project_id)
    valid_arts = [a for a in arts if a.get("validation_state") == "valid"]
    budget = telemetry.get_budget(run["id"]) if run else {}

    objs = ledger.get_items_by_category(intake_mod.RequirementKind.OBJECTIVE)
    constraints = ledger.get_items_by_category(intake_mod.RequirementKind.CONSTRAINT)
    deliverables = ledger.get_items_by_category(intake_mod.RequirementKind.DELIVERABLE)
    missing = ledger.get_items_by_category(intake_mod.RequirementKind.MISSING_INFO)
    assumptions = ledger.get_items_by_category(intake_mod.RequirementKind.ASSUMPTION)

    return {
        "project_id": project_id,
        "run_id": run["id"] if run else None,
        "run_state": run["state"] if run else "DRAFT",
        "cycle_phase": mem.get("current_cycle_phase", "understand"),
        "escalation_step": mem.get("escalation_step", 1),
        "understanding": {
            "objective": objs[0].text if objs else "",
            "active_constraints": [c.text for c in constraints],
            "requested_deliverables": [d.text for d in deliverables],
            "requirement_revision": ledger.revision_number,
        },
        "current_action": mem.get("next_action") or {"description": "Ready for next step"},
        "completed_work": {
            "artifacts_count": len(valid_arts),
            "artifacts": [
                {
                    "id": a["id"],
                    "filename": a["filename"],
                    "sha256": a["sha256"],
                    "validation_state": a["validation_state"],
                    "provenance": a["provenance"],
                }
                for a in valid_arts
            ],
            "completed_steps": mem.get("completed_work", []),
        },
        "uncertainty_and_blockers": {
            "material_missing_info": [m.text for m in missing],
            "disclosed_assumptions": [a.text for a in assumptions] + mem.get("assumptions", []),
            "unresolved_questions": mem.get("unresolved_questions", []),
            "contradictions": mem.get("contradictions", []),
        },
        "remaining_budget": {
            "tasks_remaining": max(0, int(budget.get("max_tasks", 20)) - int(budget.get("used_tasks", 0))) if budget else 20,
            "inference_remaining": max(0, int(budget.get("max_inference_requests", 25)) - int(budget.get("used_inference_requests", 0))) if budget else 25,
            "tokens_remaining": max(0, int(budget.get("max_tokens", 50000)) - int(budget.get("used_tokens", 0))) if budget else 50000,
        },
    }


# ---------------------------------------------------------------------------
# 8. Direct-Response Execution & Durable Coordinator Consumer (Finding 6)
# ---------------------------------------------------------------------------

def _try_deterministic_direct_answer(user_message: str) -> Optional[Dict[str, Any]]:
    """Evaluate self-contained arithmetic/Decimal or technical facts deterministically."""
    import ast
    from decimal import Decimal, ROUND_HALF_UP

    msg = (user_message or "").strip()
    low = msg.lower()

    if "sha-256" in low or "sha256" in low:
        if "length" in low or "char" in low or "hex" in low:
            return {
                "response": "A SHA-256 hexadecimal digest is 64 characters long (representing 256 bits / 32 bytes).",
                "producing_actor": "deterministic_tool",
                "verification_status": "verified_for_scope",
                "tool_used": "deterministic_knowledge",
            }

    # Extract safe arithmetic expression if present
    expr_match = re.search(r"(\d+(?:\.\d+)?(?:\s*[\+\-\*\/]\s*\d+(?:\.\d+)?)+)", msg.replace(",", ""))
    if expr_match:
        expr_str = expr_match.group(1).strip()
        try:
            tree = ast.parse(expr_str, mode="eval")

            def _eval_dec(node: ast.AST) -> Decimal:
                if isinstance(node, ast.Expression):
                    return _eval_dec(node.body)
                if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                    return Decimal(str(node.value))
                if isinstance(node, ast.BinOp):
                    left = _eval_dec(node.left)
                    right = _eval_dec(node.right)
                    if isinstance(node.op, ast.Add):
                        return left + right
                    if isinstance(node.op, ast.Sub):
                        return left - right
                    if isinstance(node.op, ast.Mult):
                        return left * right
                    if isinstance(node.op, ast.Div):
                        return left / right
                raise ValueError("Unsupported AST node")

            val = _eval_dec(tree)
            quantized = val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if "." in expr_str else val
            return {
                "response": f"Exact Decimal calculation for `{expr_str}`: {quantized}",
                "calculated_value": str(quantized),
                "producing_actor": "deterministic_tool",
                "verification_status": "verified_for_scope",
                "tool_used": "decimal_calculate_and_reconcile",
            }
        except Exception:
            pass

    return None


def execute_direct_response_turn(
    user_message: str,
    *,
    preferred_model: Optional[str] = None,
    timeout_seconds: int = 30,
    worker_poll_step: Optional[Callable[[str, str], None]] = None,
) -> Dict[str, Any]:
    """Answer a direct-response question via deterministic calculation/tool or authenticated worker inference.

    Never echoes the input message as the answer.
    """
    det = _try_deterministic_direct_answer(user_message)
    if det is not None:
        return {
            "status": "completed",
            "response": det["response"],
            "producing_actor": det["producing_actor"],
            "verification_status": det["verification_status"],
            "tool_used": det.get("tool_used"),
            "lease_id": None,
            "model": None,
        }

    # Route conversational question to authenticated worker node if available
    inf_res = execute_native_worker_inference(
        project_id="",
        run_id=None,
        orch_task_id=None,
        purpose="direct_response_qa",
        prompt=f"Answer the following question concisely and accurately:\n\n{user_message}",
        role="direct_qa",
        preferred_model=preferred_model,
        max_tokens=256,
        timeout_seconds=timeout_seconds,
        queue_timeout_seconds=min(timeout_seconds, 10),
        worker_poll_step=worker_poll_step,
    )
    if inf_res.get("status") == "completed" and inf_res.get("response"):
        return {
            "status": "completed",
            "response": inf_res["response"],
            "producing_actor": "authenticated_worker_model",
            "verification_status": "proposed",
            "lease_id": inf_res.get("lease_id"),
            "task_id": inf_res.get("task_id"),
            "node_id": inf_res.get("node_id"),
            "model": inf_res.get("model"),
        }

    return {
        "status": "worker_unavailable",
        "response": (
            f"Direct model inference could not be completed because no eligible authenticated worker was available "
            f"({inf_res.get('error') or inf_res.get('status')})."
        ),
        "producing_actor": "controller_status",
        "verification_status": "unknown",
        "lease_id": None,
        "model": None,
        "worker_diagnostic": inf_res,
    }


def extract_constraints_from_ledger(ledger: intake_mod.DurableRequirementLedger) -> Dict[str, Any]:
    """Extract structured constraint parameters from active requirement ledger items across domains."""
    max_budget: Optional[str] = None
    max_delivery_days: Optional[int] = None
    required_capabilities: List[str] = []
    required_dependencies: List[str] = []
    excluded_suppliers: List[str] = []
    ranking_preference = "lowest_cost"
    quantity = 10
    target_file: Optional[str] = None
    public_test_rel_path: Optional[str] = None
    min_free_reserve: Optional[int] = None
    allow_expired_batches: Optional[bool] = None
    require_positive_order_qty: bool = True

    for item in ledger.items:
        if item.status != "active":
            continue
        txt = item.statement
        low = txt.lower()

        # Quantity extraction (e.g., "for 10 units" or "quantity: 12")
        q_m = re.search(r"\b(?:for|quantity[:\s]+)\s*(\d+)\s+units?\b", low)
        if q_m:
            quantity = int(q_m.group(1))

        # Budget ceiling extraction
        if "budget" in low or "ceiling" in low or "stay under" in low or ("max" in low and "$" in txt):
            b_m = re.search(r"\$\s*([0-9,]+(?:\.\d{1,2})?)", txt)
            if b_m:
                max_budget = b_m.group(1).replace(",", "")

        # Delivery days extraction
        if "delivery" in low or "lead time" in low:
            d_m = re.search(r"(\d+)\s*days?", low)
            if d_m:
                max_delivery_days = int(d_m.group(1))

        # Capabilities extraction
        if "required capabilities:" in low:
            after = txt.split(":", 1)[1]
            for cap in after.split(","):
                c_clean = cap.strip().strip(".")
                if c_clean and c_clean not in required_capabilities:
                    required_capabilities.append(c_clean)
        elif "must support sso" in low and "sso" not in required_capabilities:
            required_capabilities.append("sso")

        # Dependencies extraction
        if "required dependencies:" in low:
            after = txt.split(":", 1)[1]
            for dep in after.split(","):
                d_clean = dep.strip().strip(".")
                if d_clean and d_clean not in required_dependencies:
                    required_dependencies.append(d_clean)

        # Exclusions extraction
        if item.kind == intake_mod.RequirementKind.EXCLUSION or "exclude" in low:
            for sup_m in re.findall(r"\b(SUP-[A-Z0-9_-]+)\b", txt):
                if sup_m not in excluded_suppliers:
                    excluded_suppliers.append(sup_m)

        # Ranking preference
        if "fastest_delivery" in low or "fastest delivery" in low:
            ranking_preference = "fastest_delivery"
        elif "lowest_cost" in low or "lowest cost" in low:
            ranking_preference = "lowest_cost"

        # Repository / software bug-fix domain constraint extraction
        tf_m = re.search(r"\btarget[_\s]+(?:file|module)[:\s]+([A-Za-z0-9_./-]+\.py)\b", txt, re.I)
        if tf_m:
            target_file = tf_m.group(1).strip()
        pt_m = re.search(r"\bpublic[_\s]+tests?[:\s]+([A-Za-z0-9_./-]+\.py)\b", txt, re.I)
        if pt_m:
            public_test_rel_path = pt_m.group(1).strip()
        res_m = re.search(r"\bsafety[_\s]+reserve[:\s]+(\d+)\b", low)
        if res_m:
            min_free_reserve = int(res_m.group(1))
        if "never allocate expired" in low or "exclude expired" in low:
            allow_expired_batches = False

    out: Dict[str, Any] = {
        "ranking_preference": ranking_preference,
        "quantity": quantity,
    }
    if max_budget is not None:
        out["max_budget"] = max_budget
    if max_delivery_days is not None:
        out["max_delivery_days"] = max_delivery_days
    if required_capabilities:
        out["required_capabilities"] = required_capabilities
    if required_dependencies:
        out["required_dependencies"] = required_dependencies
    if excluded_suppliers:
        out["excluded_suppliers"] = excluded_suppliers
        out["excluded_vendors"] = excluded_suppliers
    if target_file:
        out["target_file"] = target_file
    if public_test_rel_path:
        out["public_test_rel_path"] = public_test_rel_path
    if min_free_reserve is not None:
        out["min_free_reserve"] = min_free_reserve
    if allow_expired_batches is not None:
        out["allow_expired_batches"] = allow_expired_batches
    if require_positive_order_qty:
        out["require_positive_order_qty"] = True
    return out


# ---------------------------------------------------------------------------
# 9. Generalized Coordinator: Domain Detection, Action Contracts, Validation & Verifiers (Increment 19)
# ---------------------------------------------------------------------------

FORBIDDEN_PROPOSAL_AUTHORITY_KEYS = frozenset({
    "verified",
    "validation_state",
    "verification_status",
    "trusted_receipt",
    "bypass_permissions",
    "auto_approve",
    "override_verifier_id",
    "override_permitted_effects",
    "force_publish",
})


def strip_private_chain_of_thought(text: str) -> str:
    """Remove any <think>...</think> or raw chain-of-thought blocks before persistence or user display."""
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", str(text or ""), flags=re.I)
    cleaned = re.sub(r"<reasoning>[\s\S]*?</reasoning>", "", cleaned, flags=re.I)
    return cleaned.strip()


def summarize_operational_decision(
    *,
    task_key: str,
    selected_tool: str,
    verification_method: str,
    rationale: str,
    escalation_step: int = 1,
) -> str:
    """Create a concise, auditable operational decision summary without raw private chain-of-thought."""
    clean_rat = strip_private_chain_of_thought(rationale)
    first_line = clean_rat.splitlines()[0].strip() if clean_rat else "Selected next bounded contract action."
    return (
        f"[task={task_key} | tool={selected_tool} | verify={verification_method} | step={escalation_step}] "
        f"{first_line[:220]}"
    )


def detect_project_domain(
    project_id: str,
    ledger: Optional[intake_mod.DurableRequirementLedger] = None,
) -> str:
    """Classify the project's primary execution domain from its durable requirement ledger and epistemic metadata."""
    if ledger is None:
        ledger = intake_mod.load_durable_requirement_ledger(project_id)

    conn = get_db_connection()
    try:
        ws_row = conn.execute(
            """SELECT qualifiers_json FROM project_epistemic_items
               WHERE project_id = ? AND qualifiers_json LIKE '%"workspace_path"%'
               ORDER BY created_at DESC LIMIT 1""",
            (project_id,),
        ).fetchone()
        if ws_row:
            return "repository_bugfix"
    finally:
        conn.close()

    combined_text = " ".join(it.statement for it in ledger.items if it.status == "active").lower()
    if any(k in combined_text for k in ("repository", "bug", "allocator.py", "unit test", "pytest", "patch", "defect", "holdout")):
        return "repository_bugfix"
    return "supplier_option_evaluation"


def validate_proposed_action(
    project_id: str,
    contract: Dict[str, Any],
    proposed_action: Dict[str, Any],
    *,
    current_requirement_revision: Optional[int] = None,
    all_contracts_by_key: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Trusted controller validation gate for any model-proposed or coordinator-proposed action (Milestone 2).

    Enforces that proposed actions cannot:
    1. Invent unregistered tools (`GLOBAL_TOOL_CATALOGUE.get_tool`).
    2. Supply self-attestation or authority-escalation flags (`FORBIDDEN_PROPOSAL_AUTHORITY_KEYS`).
    3. Exceed the task contract's `permitted_effects` (e.g. writing files during a read-only inspection task).
    4. Execute when upstream `depends_on_task_keys` are not `verified`.
    5. Override `bound_requirement_revision` or execute when retry budget (`max_attempts`) is exhausted.
    6. Traverse outside the scoped repository workspace (`..` or absolute external paths).
    """
    from app.services.orchestration import working_memory as wm

    if wm.is_project_dispatch_stopped(project_id):
        return {
            "valid": False,
            "error_class": "dispatch_stopped",
            "reason": f"Project '{project_id}' dispatch is stopped.",
        }

    # 1. Reject forged verification / authority-escalation keys in proposal
    present_forbidden = sorted(set(proposed_action.keys()) & FORBIDDEN_PROPOSAL_AUTHORITY_KEYS)
    if present_forbidden:
        return {
            "valid": False,
            "error_class": "authority_escalation_rejected",
            "reason": f"Proposed action contains forbidden authority/verification override fields: {present_forbidden}",
        }

    # 2. Check requirement revision fence
    if current_requirement_revision is not None:
        bound_rev = int(contract.get("bound_requirement_revision") or 1)
        if bound_rev != int(current_requirement_revision):
            return {
                "valid": False,
                "error_class": "stale_requirement_revision",
                "reason": f"Contract bound_requirement_revision={bound_rev} does not match current revision={current_requirement_revision}",
            }

    # 3. Check upstream task dependency completion
    if all_contracts_by_key is not None:
        unmet = [
            dep for dep in (contract.get("depends_on_task_keys") or [])
            if all_contracts_by_key.get(dep, {}).get("status") != "verified"
        ]
        if unmet:
            return {
                "valid": False,
                "error_class": "unmet_task_dependencies",
                "reason": f"Cannot execute '{contract.get('task_key')}': upstream dependencies not verified: {unmet}",
            }

    # 4. Check task retry budget
    attempt_count = int(contract.get("attempts_used") or contract.get("attempt_count") or 0)
    max_attempts = int(contract.get("max_retries") or contract.get("max_attempts") or 2)
    if attempt_count >= max_attempts:
        return {
            "valid": False,
            "error_class": "task_retry_budget_exhausted",
            "reason": f"Task '{contract.get('task_key')}' exhausted retry budget ({attempt_count}/{max_attempts}).",
        }

    # 5. Check tool existence in authoritative ToolCatalogue
    tool_id = str(proposed_action.get("selected_tool") or proposed_action.get("tool_id") or contract.get("selected_tool") or "").strip()
    if not tool_id:
        return {
            "valid": False,
            "error_class": "missing_tool_id",
            "reason": "Proposed action must specify a registered tool_id.",
        }

    spec = tool_catalogue.GLOBAL_TOOL_CATALOGUE.get_tool(tool_id)
    if spec is None:
        return {
            "valid": False,
            "error_class": "unregistered_tool",
            "reason": f"Tool '{tool_id}' is not registered in GLOBAL_TOOL_CATALOGUE.",
        }

    # 6. Check side-effect permissions against contract's permitted_effects
    permitted_effects = set(contract.get("permitted_effects") or ["read_only", "write_project_artifact"])
    required_effects = set(spec.required_effects)
    missing_effects = sorted(required_effects - permitted_effects)
    if missing_effects:
        return {
            "valid": False,
            "error_class": "permission_denied",
            "reason": (
                f"Tool '{tool_id}' requires effects {missing_effects} which are not permitted by contract "
                f"'{contract.get('task_key')}' (permitted={sorted(permitted_effects)})."
            ),
        }

    # 7. Validate arguments schema and workspace path confinement
    raw_args = proposed_action.get("arguments")
    if not isinstance(raw_args, dict):
        return {
            "valid": False,
            "error_class": "invalid_arguments_schema",
            "reason": "Proposed action 'arguments' must be a JSON object/dict.",
        }

    if "target_file" in raw_args:
        tf = str(raw_args.get("target_file") or "")
        if ".." in tf or tf.startswith("/") or tf.startswith("\\") or ":" in tf:
            return {
                "valid": False,
                "error_class": "workspace_escape_rejected",
                "reason": f"Target file '{tf}' violates workspace path confinement.",
            }

    return {
        "valid": True,
        "selected_tool": tool_id,
        "tool_spec": spec.to_dict(),
        "validated_arguments": dict(raw_args),
        "verifier_id": contract.get("verifier_id") or "",
        "verification_method": contract.get("verification_method") or "independent_acceptance_verifier",
        "authority_scope": contract.get("authority_scope") or "controller_validated",
        "permitted_effects": sorted(permitted_effects),
    }


def parse_and_validate_model_action_proposal(
    raw_model_text: str,
    *,
    project_id: str,
    contract: Dict[str, Any],
    gen_tokens: Optional[int] = None,
    num_predict: Optional[int] = None,
    current_requirement_revision: Optional[int] = None,
    all_contracts_by_key: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Parse structured JSON action proposal from model output, detect truncation, strip CoT, and validate via controller gate."""
    cleaned = strip_private_chain_of_thought(raw_model_text)
    trunc = detect_generation_truncation(cleaned, gen_tokens=gen_tokens, num_predict=num_predict, expected_format="json")
    if trunc["truncated"]:
        return {
            "valid": False,
            "error_class": "truncated_model_output",
            "reason": trunc["reason"],
            "sanitized_raw": cleaned[:300],
        }

    # Extract JSON block if wrapped in ```json ... ```
    json_str = cleaned
    fence_m = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned, re.I)
    if fence_m:
        json_str = fence_m.group(1).strip()

    try:
        parsed = json.loads(json_str)
    except ValueError as exc:
        return {
            "valid": False,
            "error_class": "malformed_json_output",
            "reason": f"Invalid JSON in model action proposal: {exc}",
            "sanitized_raw": cleaned[:300],
        }

    if not isinstance(parsed, dict):
        return {
            "valid": False,
            "error_class": "malformed_json_output",
            "reason": "Model action proposal root must be a JSON object.",
        }

    val_res = validate_proposed_action(
        project_id,
        contract,
        parsed,
        current_requirement_revision=current_requirement_revision,
        all_contracts_by_key=all_contracts_by_key,
    )
    if not val_res["valid"]:
        return val_res

    decision_summary = summarize_operational_decision(
        task_key=contract["task_key"],
        selected_tool=val_res["selected_tool"],
        verification_method=val_res["verification_method"],
        rationale=str(parsed.get("decision_summary") or parsed.get("rationale") or contract["purpose"]),
        escalation_step=int(contract.get("escalation_step") or 1),
    )
    return {
        **val_res,
        "decision_summary": decision_summary,
    }


def extract_python_code_from_model_response(raw_text: str) -> str:
    """Extract candidate Python code from a model response (fenced or unfenced) after stripping any CoT tags."""
    cleaned = strip_private_chain_of_thought(raw_text)
    fenced = re.findall(r"```(?:python|py)?\s*([\s\S]*?)```", cleaned, re.I)
    code = max(fenced, key=len).strip() if fenced else cleaned.strip()
    # Ensure any directive-stripping marker copied from <evidence_data> is a valid Python comment
    code = re.sub(r"(?m)^(\s*)(\[UNTRUSTED_DIRECTIVE_STRIPPED[^\n]*\])", r"\1# \2", code)
    # Guard against model omitting opening triple-quote on first-line module docstring
    lines = code.splitlines()
    if lines and lines[0].endswith('"""') and not lines[0].lstrip().startswith(('"""', "'''", "#")):
        lines[0] = '"""' + lines[0]
        code = "\n".join(lines)
    return code + "\n"


# ---------------------------------------------------------------------------
# 10. Registered Independent Controller Verifiers (Milestone 2 & Milestone 3)
# ---------------------------------------------------------------------------

def _verify_quote_reconciliation(pkt: Dict[str, Any], raw: Dict[str, Any], staged: Dict[str, Any]) -> Dict[str, Any]:
    obs = raw.get("observation_payload") or {}
    tkey = pkt.get("task_key") or (pkt.get("task_contract") or {}).get("task_key") or "t1"
    checks = [
        {"name": "artifact_present", "passed": bool(staged.get("artifact_id") and staged.get("artifact_sha256"))},
        {"name": "active_quotes_resolved", "passed": len(obs.get("resolved_quotes", {})) >= 1},
        {"name": "superseded_tracked", "passed": "superseded_revisions" in obs},
    ]
    return {
        "passed": all(c["passed"] for c in checks),
        "verification_method": "deterministic_tool_check",
        "declared_scope": f"task:{tkey}:rev{pkt.get('requirement_revision', 1)}",
        "checks": checks,
    }


def _verify_option_constraint_comparison(pkt: Dict[str, Any], raw: Dict[str, Any], staged: Dict[str, Any]) -> Dict[str, Any]:
    cmp_res = raw.get("observation_payload") or {}
    rec_opt = cmp_res.get("recommended_option") or {}
    rec_cost = rec_opt.get("total_cost")
    tkey = pkt.get("task_key") or (pkt.get("task_contract") or {}).get("task_key") or "t2"
    checks = [
        {"name": "artifact_present", "passed": bool(staged.get("artifact_id") and staged.get("artifact_sha256"))},
        {"name": "feasible_option_found", "passed": cmp_res.get("status") in ("verified_for_scope", "feasible") and int(cmp_res.get("feasible_count") or 0) >= 1},
        {"name": "zero_hard_violations_on_recommended", "passed": len(rec_opt.get("hard_violations", rec_opt.get("hard_constraint_violations", []))) == 0 and bool(rec_opt)},
        {"name": "decimal_cost_present", "passed": bool(rec_cost)},
    ]
    return {
        "passed": all(c["passed"] for c in checks),
        "verification_method": "deterministic_tool_check",
        "declared_scope": f"task:{tkey}:rev{pkt.get('requirement_revision', 1)}",
        "checks": checks,
    }


def _verify_grounded_recommendation_memo(pkt: Dict[str, Any], raw: Dict[str, Any], staged: Dict[str, Any]) -> Dict[str, Any]:
    chk_list = list(raw.get("memo_checks") or [])
    chk_list.append({"name": "artifact_present", "passed": bool(staged.get("artifact_id") and staged.get("artifact_sha256"))})
    tkey = pkt.get("task_key") or (pkt.get("task_contract") or {}).get("task_key") or "t3"
    return {
        "passed": all(c["passed"] for c in chk_list) and len(chk_list) >= 2,
        "verification_method": "independent_acceptance_verifier",
        "declared_scope": f"task:{tkey}:rev{pkt.get('requirement_revision', 1)}",
        "checks": chk_list,
    }


def _verify_repo_inspection_and_reproduction(pkt: Dict[str, Any], raw: Dict[str, Any], staged: Dict[str, Any]) -> Dict[str, Any]:
    obs = raw.get("observation_payload") or {}
    insp = obs.get("inspection") or {}
    repro = obs.get("initial_test_run") or {}
    tkey = pkt.get("task_key") or (pkt.get("task_contract") or {}).get("task_key") or "t1_inspect_and_reproduce"
    checks = [
        {"name": "artifact_present", "passed": bool(staged.get("artifact_id") and staged.get("artifact_sha256"))},
        {"name": "workspace_files_inspected", "passed": int(insp.get("file_count") or 0) >= 2},
        {"name": "directive_injection_scan_completed", "passed": "contains_injection_attempt" in insp},
        {"name": "initial_defect_reproduced_in_bounded_runner", "passed": repro.get("tests_run", 0) > 0 and repro.get("passed") is False},
        {"name": "process_cleanup_confirmed", "passed": bool(repro.get("cleanup_confirmed", False))},
    ]
    return {
        "passed": all(c["passed"] for c in checks),
        "verification_method": "deterministic_tool_check",
        "declared_scope": f"task:{tkey}:rev{pkt.get('requirement_revision', 1)}",
        "checks": checks,
    }


def _verify_protected_repo_bugfix(pkt: Dict[str, Any], raw: Dict[str, Any], staged: Dict[str, Any]) -> Dict[str, Any]:
    patch_res = raw.get("observation_payload") or {}
    checks = list(patch_res.get("checks") or [])
    checks.append({"name": "artifact_present", "passed": bool(staged.get("artifact_id") and staged.get("artifact_sha256"))})
    checks.append({"name": "non_empty_unified_diff", "passed": len(str(patch_res.get("unified_diff") or "").strip()) > 0})
    tkey = pkt.get("task_key") or (pkt.get("task_contract") or {}).get("task_key") or "t2_propose_and_verify_patch"
    return {
        "passed": bool(patch_res.get("accepted")) and all(c["passed"] for c in checks),
        "verification_method": "protected_test_suite_verifier",
        "declared_scope": f"task:{tkey}:rev{pkt.get('requirement_revision', 1)}",
        "checks": checks,
    }


def _verify_repo_change_summary(pkt: Dict[str, Any], raw: Dict[str, Any], staged: Dict[str, Any]) -> Dict[str, Any]:
    report_checks = list(raw.get("report_checks") or [])
    report_checks.append({"name": "artifact_present", "passed": bool(staged.get("artifact_id") and staged.get("artifact_sha256"))})
    tkey = pkt.get("task_key") or (pkt.get("task_contract") or {}).get("task_key") or "t3_publish_change_report"
    return {
        "passed": all(c["passed"] for c in report_checks) and len(report_checks) >= 3,
        "verification_method": "independent_acceptance_verifier",
        "declared_scope": f"task:{tkey}:rev{pkt.get('requirement_revision', 1)}",
        "checks": report_checks,
    }


REGISTERED_CONTROLLER_VERIFIERS: Dict[str, Callable[[Dict[str, Any], Dict[str, Any], Dict[str, Any]], Dict[str, Any]]] = {
    "verifier:quote_reconciliation_v1": _verify_quote_reconciliation,
    "verifier:option_constraint_comparison_v1": _verify_option_constraint_comparison,
    "verifier:grounded_recommendation_memo_v1": _verify_grounded_recommendation_memo,
    "verifier:repo_inspection_and_reproduction_v1": _verify_repo_inspection_and_reproduction,
    "verifier:protected_repo_bugfix_v1": _verify_protected_repo_bugfix,
    "verifier:repo_change_summary_v1": _verify_repo_change_summary,
}


def ensure_deliberate_task_contracts(
    project_id: str,
    *,
    run_id: Optional[str] = None,
    explicit_contracts: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Decompose the project's durable requirement ledger into persisted action contracts (`project_task_contracts`).

    Supports:
    - Explicit caller/planner task contracts (`explicit_contracts`)
    - Software / repository bug-fix domain (`repository_bugfix`)
    - Supplier / option evaluation domain (`supplier_option_evaluation`, retained as regression case)
    """
    from app.services.orchestration import working_memory as wm

    existing = wm.list_task_contracts(project_id)
    if existing:
        return existing

    ledger = intake_mod.load_durable_requirement_ledger(project_id)
    run = get_run(run_id) if run_id else get_run_for_project(project_id)
    eff_run_id = run["id"] if run else run_id

    obj_ids = [r.req_id for r in ledger.items if r.status == "active" and r.kind == intake_mod.RequirementKind.OBJECTIVE]
    con_ids = [
        r.req_id for r in ledger.items
        if r.status == "active" and r.kind in (intake_mod.RequirementKind.CONSTRAINT, intake_mod.RequirementKind.EXCLUSION, intake_mod.RequirementKind.DEPENDENCY)
    ]
    del_ids = [r.req_id for r in ledger.items if r.status == "active" and r.kind == intake_mod.RequirementKind.DELIVERABLE]
    crit_ids = [r.req_id for r in ledger.items if r.status == "active" and r.kind == intake_mod.RequirementKind.SUCCESS_CRITERION]

    t1_reqs = obj_ids + (del_ids[:1] if del_ids else []) + (crit_ids[:1] if crit_ids else [])
    t2_reqs = con_ids + (del_ids[1:2] if len(del_ids) > 1 else del_ids) + (crit_ids[1:2] if len(crit_ids) > 1 else [])
    t3_reqs = (del_ids[2:] if len(del_ids) > 2 else del_ids[-1:]) + (crit_ids[2:] if len(crit_ids) > 2 else crit_ids[-1:])

    if explicit_contracts:
        registered_keys: List[str] = []
        for spec in explicit_contracts:
            tkey = str(spec["task_key"])
            val_args = dict(spec.get("validated_arguments") or {})
            val_args.setdefault("action_origin", str(spec.get("action_origin") or "caller_supplied_contract"))
            wm.register_task_contract(
                project_id,
                run_id=eff_run_id,
                task_key=tkey,
                purpose=str(spec.get("purpose") or tkey),
                expected_output=str(spec.get("expected_output") or f"{tkey}.json"),
                bound_requirement_ids=spec.get("bound_requirement_ids") or obj_ids,
                depends_on_task_keys=spec.get("depends_on_task_keys") or [],
                acceptance_checks=spec.get("acceptance_checks") or ["verified"],
                selected_tool=str(spec.get("selected_tool") or "inspect_document_or_repo"),
                validated_arguments=val_args,
                expected_observation=str(spec.get("expected_observation") or "structured_observation"),
                verification_method=str(spec.get("verification_method") or "deterministic_tool_check"),
                verifier_id=str(spec.get("verifier_id") or "verifier:quote_reconciliation_v1"),
                authority_scope=str(spec.get("authority_scope") or "controller_validated"),
                permitted_effects=spec.get("permitted_effects") or ["read_only", "write_project_artifact"],
                max_retries=int(spec.get("max_retries") or spec.get("max_attempts") or 2),
            )
            registered_keys.append(tkey)
        wm.create_working_checkpoint(
            project_id,
            boundary_type="task_boundary",
            next_action={"action": f"execute_{registered_keys[0]}" if registered_keys else "idle"},
            delta_summary={"domain": "custom_contracts", "decomposed_contracts": registered_keys},
            created_by="orchestrator",
        )
        return wm.list_task_contracts(project_id)

    domain = detect_project_domain(project_id, ledger=ledger)

    if domain == "repository_bugfix":
        wm.register_task_contract(
            project_id,
            run_id=eff_run_id,
            task_key="t1_inspect_and_reproduce",
            purpose="Inspect scoped repository workspace files with directive stripping and reproduce the failing public unit test suite.",
            expected_output="repo_inspection_and_reproduction.json",
            bound_requirement_ids=t1_reqs or obj_ids,
            depends_on_task_keys=[],
            acceptance_checks=["workspace_files_inspected", "directive_injection_scan_completed", "initial_defect_reproduced_in_bounded_runner"],
            selected_tool="inspect_repository_workspace",
            validated_arguments={"action_origin": "template"},
            expected_observation="qc.repo_inspection_and_reproduction.v1",
            verification_method="deterministic_tool_check",
            verifier_id="verifier:repo_inspection_and_reproduction_v1",
            authority_scope="scoped_workspace_read_and_test",
            permitted_effects=["read_only", "read_scoped_workspace", "isolated_code_sandbox", "write_project_artifact"],
        )
        wm.register_task_contract(
            project_id,
            run_id=eff_run_id,
            task_key="t2_propose_and_verify_patch",
            purpose="Synthesize a minimal code repair via authenticated worker model inference, apply patch to target module, and verify against public and protected holdout test suites.",
            expected_output="verified_bugfix_patch.diff",
            bound_requirement_ids=t2_reqs or con_ids or obj_ids,
            depends_on_task_keys=["t1_inspect_and_reproduce"],
            acceptance_checks=["python_syntax_valid", "public_test_suite_passed", "protected_holdout_suite_passed", "unrelated_files_preserved"],
            selected_tool="apply_and_verify_repository_patch",
            validated_arguments={"action_origin": "template"},
            expected_observation="qc.repo_patch_verification.v1",
            verification_method="protected_test_suite_verifier",
            verifier_id="verifier:protected_repo_bugfix_v1",
            authority_scope="scoped_workspace_patch_and_test",
            permitted_effects=["read_only", "read_scoped_workspace", "write_scoped_workspace", "isolated_code_sandbox", "worker_inference", "write_project_artifact"],
        )
        wm.register_task_contract(
            project_id,
            run_id=eff_run_id,
            task_key="t3_publish_change_report",
            purpose="Synthesize and publish verified repository bug-fix report with root cause, unified diff, test verification receipt, and untouched-file SHA-256 audit.",
            expected_output="bugfix_verification_report.md",
            bound_requirement_ids=t3_reqs or del_ids or obj_ids,
            depends_on_task_keys=["t2_propose_and_verify_patch"],
            acceptance_checks=["cites_target_file", "cites_public_and_holdout_pass", "cites_unrelated_files_preserved"],
            selected_tool="worker_model_inference",
            validated_arguments={"action_origin": "template"},
            expected_observation="qc.repo_change_summary.v1",
            verification_method="independent_acceptance_verifier",
            verifier_id="verifier:repo_change_summary_v1",
            authority_scope="artifact_publication",
            permitted_effects=["read_only", "worker_inference", "write_project_artifact"],
        )
        wm.create_working_checkpoint(
            project_id,
            boundary_type="task_boundary",
            next_action={"action": "execute_t1_inspect_and_reproduce"},
            delta_summary={
                "domain": "repository_bugfix",
                "decomposed_contracts": [
                    "t1_inspect_and_reproduce",
                    "t2_propose_and_verify_patch",
                    "t3_publish_change_report",
                ],
            },
            created_by="orchestrator",
        )
        return wm.list_task_contracts(project_id)

    # Default / regression domain: supplier_option_evaluation
    wm.register_task_contract(
        project_id,
        run_id=eff_run_id,
        task_key="t1_reconcile_quotes",
        purpose="Resolve latest valid supplier quote revisions and superseded/withdrawn quotes.",
        expected_output="quote_reconciliation_ledger.json",
        bound_requirement_ids=t1_reqs or obj_ids,
        depends_on_task_keys=[],
        acceptance_checks=["valid_json", "latest_revisions_selected", "withdrawn_excluded"],
        selected_tool="resolve_quote_revisions",
        validated_arguments={"action_origin": "template"},
        expected_observation="qc.quote_resolution.v1",
        verification_method="deterministic_tool_check",
        verifier_id="verifier:quote_reconciliation_v1",
        authority_scope="deterministic_compute",
        permitted_effects=["read_only", "deterministic_compute", "write_project_artifact"],
    )
    wm.register_task_contract(
        project_id,
        run_id=eff_run_id,
        task_key="t2_evaluate_constraints",
        purpose="Calculate exact Decimal costs and compare options against hard/soft constraints.",
        expected_output="supplier_recommendation.json",
        bound_requirement_ids=t2_reqs or con_ids or obj_ids,
        depends_on_task_keys=["t1_reconcile_quotes"],
        acceptance_checks=["decimal_exactness", "hard_constraints_satisfied"],
        selected_tool="compare_options_against_constraints",
        validated_arguments={"action_origin": "template"},
        expected_observation="qc.option_comparison.v1",
        verification_method="deterministic_tool_check",
        verifier_id="verifier:option_constraint_comparison_v1",
        authority_scope="deterministic_compute",
        permitted_effects=["read_only", "deterministic_compute", "write_project_artifact"],
    )
    wm.register_task_contract(
        project_id,
        run_id=eff_run_id,
        task_key="t3_synthesize_recommendation",
        purpose="Synthesize evidence-backed supplier recommendation report.",
        expected_output="supplier_recommendation.md",
        bound_requirement_ids=t3_reqs or del_ids or obj_ids,
        depends_on_task_keys=["t2_evaluate_constraints"],
        acceptance_checks=["cites_recommended_supplier", "cites_exact_cost", "no_disqualified_recommended"],
        selected_tool="worker_model_inference",
        validated_arguments={"action_origin": "template"},
        expected_observation="qc.grounded_recommendation_memo.v1",
        verification_method="independent_acceptance_verifier",
        verifier_id="verifier:grounded_recommendation_memo_v1",
        authority_scope="authenticated_worker_synthesis",
        permitted_effects=["read_only", "worker_inference", "write_project_artifact"],
    )
    wm.create_working_checkpoint(
        project_id,
        boundary_type="task_boundary",
        next_action={"action": "execute_t1_reconcile_quotes"},
        delta_summary={
            "domain": "supplier_option_evaluation",
            "decomposed_contracts": [
                "t1_reconcile_quotes",
                "t2_evaluate_constraints",
                "t3_synthesize_recommendation",
            ],
        },
        created_by="orchestrator",
    )
    return wm.list_task_contracts(project_id)


def _load_project_domain_inputs(
    project_id: str,
    *,
    quotes_data: Optional[List[Dict[str, Any]]] = None,
    workspace_path: Optional[str] = None,
    holdout_test_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Load persisted domain inputs (supplier quotes or repository workspace paths) from `project_epistemic_items`."""
    from app.services.orchestration import acceptance_harness, working_memory as wm

    run = get_run_for_project(project_id)
    run_id = run["id"] if run else None

    conn = get_db_connection()
    persisted_quotes = None
    persisted_ws = None
    persisted_holdout = None
    try:
        rows = conn.execute(
            "SELECT qualifiers_json FROM project_epistemic_items WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
        for r in rows:
            qd = jload(r["qualifiers_json"], {})
            if persisted_quotes is None and isinstance(qd.get("raw_quotes"), list):
                persisted_quotes = qd["raw_quotes"]
            if persisted_ws is None and qd.get("workspace_path"):
                persisted_ws = str(qd["workspace_path"])
            if persisted_holdout is None and qd.get("holdout_test_dir"):
                persisted_holdout = str(qd["holdout_test_dir"])
    finally:
        conn.close()

    eff_quotes = quotes_data if quotes_data is not None else persisted_quotes
    if quotes_data is not None:
        wm.record_epistemic_item(
            project_id,
            epistemic_category="source_observation",
            statement=f"Attached {len(quotes_data)} supplier quote records for evaluation.",
            verification_status="observed",
            run_id=run_id,
            qualifiers={"raw_quotes": quotes_data},
            idempotency_key=f"intake:raw_quotes:{project_id}",
        )

    eff_ws = workspace_path or persisted_ws
    eff_holdout = holdout_test_dir or persisted_holdout
    if workspace_path or holdout_test_dir:
        wm.record_epistemic_item(
            project_id,
            epistemic_category="source_observation",
            statement=f"Bound scoped repository workspace '{eff_ws}' for bug-fix verification.",
            verification_status="observed",
            run_id=run_id,
            qualifiers={
                "workspace_path": eff_ws,
                "holdout_test_dir": eff_holdout,
            },
            idempotency_key=f"intake:workspace_config:{project_id}",
        )

    return {
        "quotes": eff_quotes if eff_quotes is not None else acceptance_harness.SYNTHETIC_SUPPLIER_QUOTES,
        "workspace_path": eff_ws,
        "holdout_test_dir": eff_holdout,
    }


def build_repository_repair_prompt(
    *,
    target_file: str,
    attempt: int,
    obj_texts: List[str],
    constraint_texts: List[str],
    target_source: str,
    pub_test_source: str,
    pub_failure_excerpt: str,
    last_repair: Dict[str, Any],
) -> str:
    """Build a domain-neutral repair prompt for repository bug-fix tasks.

    Contains ONLY:
    - Active user requirements (`obj_texts`, `constraint_texts`).
    - Relevant source (`target_source`) and public development tests (`pub_test_source`).
    - Publicly observable failure diagnostics (`pub_failure_excerpt`).
    - Independently observed contract violations (`failed_check_names`, `sanitized_diagnostics`).
    - A concise description of what the previous attempt failed to establish (`repair_instruction`).

    NEVER contains hidden expected answers, holdout source, or developer-authored replacement logic.
    """
    return context_manager.build_inference_prompt(
        instruction=(
            f"REPAIR ATTEMPT {attempt} for `{target_file}` (Objective: {'; '.join(obj_texts)}).\n"
            f"What the previous attempt failed to establish: {last_repair.get('repair_instruction', '')}\n"
            f"Independently observed contract violations: {', '.join(last_repair.get('failed_check_names', []))}\n"
            f"Publicly observable failure diagnostics: {'; '.join(last_repair.get('sanitized_diagnostics', []))}\n\n"
            "Inspect the `<evidence_data>` (the current target file source, public unit tests, and public test failure output) "
            "and `<user_constraints>`.\n"
            "Requirements:\n"
            "- Preserve all public function signatures and return dict keys.\n"
            "- Address every failing public test assertion and active user constraint.\n"
            "- Output ONLY the complete, valid Python module for the target file inside a single ```python ... ``` block."
        ),
        inputs={
            "target_file_source": target_source,
            "public_tests_and_failure": f"{pub_test_source}\n\nFAILURE OUTPUT:\n{pub_failure_excerpt[:600]}",
        },
        budget_chars=context_manager.MAX_EXPANDED_CONTEXT_CHARS,
        user_constraints=constraint_texts,
    )


def evaluate_next_action_appropriateness(
    state_context: Dict[str, Any],
    proposed_action: Dict[str, Any],
) -> Dict[str, Any]:
    """Independently evaluate a proposed action across 4 separate dimensions:
    1. `proposal_validity`: registered tool or policy action, valid arguments, permitted authority/effects.
    2. `action_appropriateness`: whether the action advances the active requirement or resolves a relevant uncertainty.
    3. `execution_outcome`: whether the tool/policy execution succeeded (`"succeeded" | "failed" | "held" | "not_executed"`).
    4. `task_outcome`: whether independent acceptance criteria passed (`"verified" | "failed" | "blocked" | "pending"`).
    """
    policy_actions = {
        "request_clarification",
        "wait_for_active_lease",
        "stop_unproductive_retry",
        "report_blocker",
    }
    action_id = str(proposed_action.get("selected_tool") or proposed_action.get("policy_action") or "")
    args = proposed_action.get("arguments")
    action_origin = str(proposed_action.get("action_origin") or "model_proposal")

    # 1. Check syntactic & authority validity
    has_forbidden_keys = any(k in proposed_action for k in FORBIDDEN_PROPOSAL_AUTHORITY_KEYS)
    if has_forbidden_keys or not action_id or not isinstance(args, dict):
        proposal_validity = False
        invalid_reason = "Forbidden authority key or malformed arguments"
    elif action_id in policy_actions:
        proposal_validity = True
        invalid_reason = None
    else:
        spec = tool_catalogue.GLOBAL_TOOL_CATALOGUE.get_tool(action_id)
        if spec is None:
            proposal_validity = False
            invalid_reason = f"Unregistered tool '{action_id}'"
        else:
            missing_req = [
                k for k, t in spec.input_schema.items()
                if "None" not in str(t) and k not in args
            ]
            proposal_validity = len(missing_req) == 0
            invalid_reason = None if proposal_validity else f"Missing required arguments: {missing_req}"

    # 2. Determine the independently required appropriate action(s) from `state_context`
    expected_actions: List[str] = []
    if state_context.get("has_material_gaps") or state_context.get("has_conflicts"):
        expected_actions = ["request_clarification"]
    elif state_context.get("has_active_draining_lease"):
        expected_actions = ["wait_for_active_lease"]
    elif int(state_context.get("unproductive_attempt_count") or 0) >= 2:
        expected_actions = ["stop_unproductive_retry"]
    elif state_context.get("all_options_infeasible") or state_context.get("protected_backend_held"):
        expected_actions = ["report_blocker"]
    elif state_context.get("missing_external_source"):
        expected_actions = ["search_and_retrieve_sources"]
    elif state_context.get("domain") == "repository_bugfix":
        if not state_context.get("workspace_inspected"):
            expected_actions = ["inspect_repository_workspace"]
        elif not state_context.get("defect_reproduced"):
            expected_actions = ["run_repository_test_suite"]
        elif not state_context.get("patch_verified"):
            expected_actions = ["apply_and_verify_repository_patch", "worker_model_inference"]
        else:
            expected_actions = ["worker_model_inference", "create_or_revise_artifact"]
    elif state_context.get("domain") == "supplier_option_evaluation":
        if not state_context.get("quotes_reconciled"):
            expected_actions = ["resolve_quote_revisions"]
        elif not state_context.get("constraints_evaluated"):
            expected_actions = ["compare_options_against_constraints"]
        else:
            expected_actions = ["worker_model_inference"]

    if not expected_actions:
        appropriateness: Any = "unmeasured"
    else:
        appropriateness = bool(proposal_validity and action_id in expected_actions)

    exec_outcome = str(proposed_action.get("execution_outcome") or ("succeeded" if proposal_validity else "not_executed"))
    task_outcome = str(
        proposed_action.get("task_outcome")
        or ("verified" if (proposal_validity and appropriateness is True and exec_outcome == "succeeded") else "blocked")
    )

    return {
        "action_id": action_id,
        "action_origin": action_origin,
        "proposal_validity": proposal_validity,
        "invalid_reason": invalid_reason,
        "expected_appropriate_actions": expected_actions,
        "action_appropriateness": appropriateness,
        "execution_outcome": exec_outcome,
        "task_outcome": task_outcome,
    }


def run_action_selection_benchmark() -> Dict[str, Any]:
    """Run independently judged next-action selection benchmark cases covering:
    - Inspection (`inspect_repository_workspace`)
    - Testing (`run_repository_test_suite`)
    - Retrieval (`search_and_retrieve_sources`)
    - Clarification (`request_clarification`)
    - Waiting (`wait_for_active_lease`)
    - Stopping (`stop_unproductive_retry`)
    - Reporting a blocker (`report_blocker`)
    - Plus syntactically valid but inappropriate tool proposals.
    """
    cases = [
        {
            "case_id": "appropriate_01_inspect_repo",
            "state_context": {"domain": "repository_bugfix", "workspace_inspected": False},
            "proposal": {
                "selected_tool": "inspect_repository_workspace",
                "arguments": {"workspace_path": "/tmp/repo", "max_file_chars": 8000},
                "action_origin": "model_proposal",
            },
            "expect_valid": True,
            "expect_appropriate": True,
        },
        {
            "case_id": "appropriate_02_run_public_tests",
            "state_context": {"domain": "repository_bugfix", "workspace_inspected": True, "defect_reproduced": False},
            "proposal": {
                "selected_tool": "run_repository_test_suite",
                "arguments": {"workspace_path": "/tmp/repo", "test_rel_path": "tests/test_public.py", "timeout_seconds": 10.0},
                "action_origin": "model_proposal",
            },
            "expect_valid": True,
            "expect_appropriate": True,
        },
        {
            "case_id": "appropriate_03_retrieve_missing_source",
            "state_context": {"domain": "research", "missing_external_source": True},
            "proposal": {
                "selected_tool": "search_and_retrieve_sources",
                "arguments": {"queries": ["RFC 9110 caching"], "project_id": "p1", "as_of_date": "2026-11-04"},
                "action_origin": "model_proposal",
            },
            "expect_valid": True,
            "expect_appropriate": True,
        },
        {
            "case_id": "appropriate_04_request_clarification",
            "state_context": {"domain": "supplier_option_evaluation", "has_material_gaps": True},
            "proposal": {
                "policy_action": "request_clarification",
                "arguments": {"questions": ["What is the maximum delivery lead time?"]},
                "action_origin": "controller_policy",
            },
            "expect_valid": True,
            "expect_appropriate": True,
        },
        {
            "case_id": "appropriate_05_wait_for_active_lease",
            "state_context": {"domain": "repository_bugfix", "has_active_draining_lease": True},
            "proposal": {
                "policy_action": "wait_for_active_lease",
                "arguments": {"lease_id": "lease_active_01"},
                "action_origin": "controller_policy",
            },
            "expect_valid": True,
            "expect_appropriate": True,
        },
        {
            "case_id": "appropriate_06_stop_unproductive_retry",
            "state_context": {"domain": "repository_bugfix", "unproductive_attempt_count": 2},
            "proposal": {
                "policy_action": "stop_unproductive_retry",
                "arguments": {"task_key": "t2_propose_and_verify_patch"},
                "action_origin": "controller_policy",
            },
            "expect_valid": True,
            "expect_appropriate": True,
        },
        {
            "case_id": "appropriate_07_report_blocker",
            "state_context": {"domain": "repository_bugfix", "protected_backend_held": True},
            "proposal": {
                "policy_action": "report_blocker",
                "arguments": {"blocker": "held_protected_backend_required"},
                "action_origin": "controller_policy",
            },
            "expect_valid": True,
            "expect_appropriate": True,
        },
        {
            "case_id": "inappropriate_08_valid_tool_wrong_domain",
            "state_context": {"domain": "repository_bugfix", "workspace_inspected": False},
            "proposal": {
                "selected_tool": "resolve_quote_revisions",
                "arguments": {"quotes": [], "as_of_date": "2026-11-04"},
                "action_origin": "model_proposal",
            },
            "expect_valid": True,
            "expect_appropriate": False,
        },
        {
            "case_id": "inappropriate_09_patch_before_inspection",
            "state_context": {"domain": "repository_bugfix", "workspace_inspected": False, "defect_reproduced": False},
            "proposal": {
                "selected_tool": "apply_and_verify_repository_patch",
                "arguments": {
                    "workspace_path": "/tmp/repo",
                    "target_file": "pkg/mod.py",
                    "patched_content": "x = 1\n",
                    "public_test_rel_path": "tests/test_mod.py",
                    "holdout_test_dir": "/tmp/holdout",
                },
                "action_origin": "model_proposal",
            },
            "expect_valid": True,
            "expect_appropriate": False,
        },
        {
            "case_id": "inappropriate_10_execute_when_clarification_needed",
            "state_context": {"domain": "supplier_option_evaluation", "has_material_gaps": True},
            "proposal": {
                "selected_tool": "resolve_quote_revisions",
                "arguments": {"quotes": [], "as_of_date": "2026-11-04"},
                "action_origin": "model_proposal",
            },
            "expect_valid": True,
            "expect_appropriate": False,
        },
    ]

    results = []
    appropriate_passed = 0
    inappropriate_rejected = 0
    for c in cases:
        ev = evaluate_next_action_appropriateness(c["state_context"], c["proposal"])
        passed_check = (
            ev["proposal_validity"] is c["expect_valid"]
            and ev["action_appropriateness"] is c["expect_appropriate"]
        )
        if c["expect_appropriate"] and passed_check:
            appropriate_passed += 1
        if not c["expect_appropriate"] and ev["proposal_validity"] is True and ev["action_appropriateness"] is False:
            inappropriate_rejected += 1
        results.append({
            "case_id": c["case_id"],
            "evaluation": ev,
            "benchmark_assertion_passed": passed_check,
        })

    return {
        "schema": "qc.action_selection_benchmark.v1",
        "total_cases": sum(1 for c in cases if c["expect_appropriate"]),
        "appropriate_cases_passed": appropriate_passed,
        "inappropriate_but_syntactically_valid_rejected": inappropriate_rejected,
        "all_cases_count": len(cases),
        "cases": results,
    }


def advance_deliberate_project(
    project_id: str,
    *,
    max_steps: int = 5,
    use_live_worker: bool = True,
    preferred_model: str = "qwen2.5:3b",
    quotes_data: Optional[List[Dict[str, Any]]] = None,
    workspace_path: Optional[str] = None,
    holdout_test_dir: Optional[str] = None,
    as_of_date: str = "2026-11-04",
    worker_poll_step: Optional[Callable[[str, str], None]] = None,
    simulate_first_attempt_defect: bool = False,
    injected_first_attempt_candidate: Optional[str] = None,
) -> Dict[str, Any]:
    """Generalized evidence-driven next-action coordinator for durable projects (Increment 19).

    Executes the 8-step bounded cycle across any persisted `project_task_contracts` DAG:
    1. Read authoritative project intent, active task contracts, working-memory state, and remaining budget.
    2. Retrieve relevant verified/observed evidence and open questions.
    3. Choose next bounded action (or clarify if material gaps exist).
    4. Validate proposed action via `validate_proposed_action` and execute through registered tools / authenticated worker leases.
    5. Record raw observations as `observed` or `proposed`, never `verified_for_scope`.
    6. Apply independent deterministic or test-based check from `REGISTERED_CONTROLLER_VERIFIERS`.
    7. Decide whether to continue, repair (5-step failure repair cycle), replan, block, or deliver.
    8. Commit a version-fenced checkpoint with a concise operational decision summary.
    """
    from app.services.orchestration import working_memory as wm

    empty_stats: Dict[str, Any] = {
        "total_proposals": 0,
        "valid_proposals": 0,
        "valid_selections": 0,
        "rejected_proposals": 0,
        "proposal_validation_rate": "not_evaluated",
        "model_tool_selection_accuracy": "not_evaluated",
        "tool_selection_accuracy": "not_evaluated",
        "action_origin_counts": {
            "template": 0,
            "caller_supplied_contract": 0,
            "model_proposal": 0,
            "controller_policy": 0,
        },
    }

    wm.reconcile_crash_publication_journal(project_id)
    if wm.is_project_dispatch_stopped(project_id):
        return {
            "project_id": project_id,
            "status": "dispatch_stopped",
            "detail": "Project is parked or cancelling; dispatch is stopped.",
            "tool_selection_stats": empty_stats,
        }

    run = get_run_for_project(project_id)
    run_id = run["id"] if run else None
    ledger = intake_mod.load_durable_requirement_ledger(project_id)

    conflict_info = ledger.detect_conflicts_and_ambiguities()
    if conflict_info["has_material_gaps"] or conflict_info["has_conflicts"]:
        context_manager.save_project_memory_state(
            project_id,
            run_id=run_id,
            current_cycle_phase="clarify",
            unresolved_questions=conflict_info["material_gaps"],
            contradictions=conflict_info["conflicts"],
            next_action={"action": "clarify_material_gaps", "gaps": conflict_info["material_gaps"]},
        )
        clarify_stats = copy.deepcopy(empty_stats)
        clarify_stats["action_origin_counts"]["controller_policy"] = 1
        return {
            "project_id": project_id,
            "status": "clarify",
            "material_gaps": conflict_info["material_gaps"],
            "conflicts": conflict_info["conflicts"],
            "tool_selection_stats": clarify_stats,
        }

    domain_inputs = _load_project_domain_inputs(
        project_id,
        quotes_data=quotes_data,
        workspace_path=workspace_path,
        holdout_test_dir=holdout_test_dir,
    )
    quotes = domain_inputs["quotes"]
    eff_ws = domain_inputs["workspace_path"]
    eff_holdout = domain_inputs["holdout_test_dir"]

    ensure_deliberate_task_contracts(project_id, run_id=run_id)
    domain = detect_project_domain(project_id, ledger=ledger)

    steps_executed: List[Dict[str, Any]] = []
    native_leases: List[str] = []
    repair_events: List[Dict[str, Any]] = []
    decision_summaries: List[str] = []
    tool_selection_stats: Dict[str, Any] = copy.deepcopy(empty_stats)
    previous_action_signatures: Dict[str, int] = {}

    for _ in range(max(1, int(max_steps))):
        if wm.is_project_dispatch_stopped(project_id):
            break

        # Check run-level budget before selecting next action
        if run_id:
            budget_ok, budget_reason = telemetry.check_budget(run_id, will_add_task=True, will_add_inference=False)
            if not budget_ok:
                context_manager.save_project_memory_state(
                    project_id,
                    run_id=run_id,
                    current_cycle_phase="blocked",
                    escalation_step=7,
                    next_action={"action": "halt_budget_exhausted", "reason": budget_reason},
                )
                tool_selection_stats["action_origin_counts"]["controller_policy"] += 1
                steps_executed.append({
                    "status": "blocked",
                    "error_class": "budget_exhausted",
                    "reason": budget_reason,
                    "action_origin": "controller_policy",
                    "proposal_validity": True,
                    "action_appropriateness": True,
                    "execution_outcome": "not_executed",
                    "task_outcome": "blocked",
                })
                break

        ledger = intake_mod.load_durable_requirement_ledger(project_id)
        all_list = wm.list_task_contracts(project_id)
        contracts = {tc["task_key"]: tc for tc in all_list}

        # Step 1 & 2: Find first runnable or stale task contract in topological order whose dependencies are verified
        next_tc: Optional[Dict[str, Any]] = None
        for tc in all_list:
            if tc["status"] in ("pending", "ready", "stale"):
                deps_ok = all(
                    contracts.get(dep, {}).get("status") == "verified"
                    for dep in tc["depends_on_task_keys"]
                )
                if deps_ok:
                    next_tc = tc
                    break

        if not next_tc:
            break

        tkey = next_tc["task_key"]
        # Refresh contract revision binding if it was invalidated (`stale`) by user steering
        if int(next_tc["bound_requirement_revision"]) != int(ledger.revision_number) or next_tc["status"] == "stale":
            if tkey.startswith("t1_"):
                rebound_req_ids = list(next_tc["bound_requirement_ids"])
            else:
                active_req_ids = [
                    r.req_id
                    for r in ledger.items
                    if r.status == "active" and r.kind != intake_mod.RequirementKind.MISSING_INFO
                ]
                rebound_req_ids = list(dict.fromkeys(list(next_tc["bound_requirement_ids"]) + active_req_ids))
            wm.register_task_contract(
                project_id,
                run_id=run_id,
                task_key=tkey,
                purpose=next_tc["purpose"],
                expected_output=next_tc["expected_output"],
                bound_requirement_revision=ledger.revision_number,
                bound_requirement_ids=rebound_req_ids,
                depends_on_task_keys=next_tc["depends_on_task_keys"],
                acceptance_checks=next_tc["acceptance_checks"],
                selected_tool=next_tc["selected_tool"],
                validated_arguments=next_tc["validated_arguments"],
                expected_observation=next_tc["expected_observation"],
                verification_method=next_tc["verification_method"],
                verifier_id=next_tc["verifier_id"],
                authority_scope=next_tc["authority_scope"],
                permitted_effects=next_tc["permitted_effects"],
                checkpoint_refs=next_tc["checkpoint_refs"],
                max_retries=next_tc.get("max_retries", 2),
                status="ready",
            )
            next_tc = wm.get_task_contract(project_id, tkey) or next_tc
            contracts[tkey] = next_tc

        active_constraints = extract_constraints_from_ledger(ledger)
        verifier_id = str(next_tc.get("verifier_id") or "")
        verifier_fn = REGISTERED_CONTROLLER_VERIFIERS.get(verifier_id)

        raw_val_args = dict(next_tc.get("validated_arguments") or {})
        action_origin = str(raw_val_args.pop("action_origin", "template") or "template")
        if action_origin not in tool_selection_stats["action_origin_counts"]:
            action_origin = "template"
        tool_selection_stats["action_origin_counts"][action_origin] += 1

        # Build proposed action for the current contract
        proposed_action = {
            "selected_tool": next_tc.get("selected_tool"),
            "arguments": raw_val_args,
            "action_origin": action_origin,
            "decision_summary": f"Execute contract '{tkey}' via tool '{next_tc.get('selected_tool')}' (origin={action_origin}) under revision {ledger.revision_number}.",
        }
        tool_selection_stats["total_proposals"] += 1
        val_gate = validate_proposed_action(
            project_id,
            next_tc,
            proposed_action,
            current_requirement_revision=ledger.revision_number,
            all_contracts_by_key=contracts,
        )
        if not val_gate["valid"]:
            tool_selection_stats["rejected_proposals"] += 1
            wm.record_epistemic_item(
                project_id,
                epistemic_category="verification_failure",
                statement=f"Controller rejected invalid action proposal for '{tkey}': {val_gate['reason']}",
                verification_status="blocked",
                run_id=run_id,
                task_key=tkey,
                qualifiers=val_gate,
            )
            with db_transaction() as c:
                c.execute(
                    "UPDATE project_task_contracts SET status = 'blocked', blocker_conditions_json = ?, updated_at = ? WHERE project_id = ? AND task_key = ?",
                    (jdump([val_gate["reason"]]), now_iso(), project_id, tkey),
                )
            steps_executed.append({
                "task_key": tkey,
                "status": "blocked",
                "error_class": val_gate["error_class"],
                "reason": val_gate["reason"],
                "action_origin": action_origin,
                "proposal_validity": False,
                "action_appropriateness": False,
                "execution_outcome": "not_executed",
                "task_outcome": "blocked",
            })
            break

        tool_selection_stats["valid_proposals"] += 1
        tool_selection_stats["valid_selections"] += 1
        op_summary = summarize_operational_decision(
            task_key=tkey,
            selected_tool=val_gate["selected_tool"],
            verification_method=val_gate["verification_method"],
            rationale=proposed_action["decision_summary"],
            escalation_step=int(next_tc.get("escalation_step") or 1),
        )
        decision_summaries.append(op_summary)

        # Guard against repeated unproductive identical actions within the same run loop
        sig = f"{tkey}:{val_gate['selected_tool']}:{ledger.revision_number}"
        previous_action_signatures[sig] = previous_action_signatures.get(sig, 0) + 1
        if previous_action_signatures[sig] > int(next_tc.get("max_attempts") or 3):
            steps_executed.append({
                "task_key": tkey,
                "status": "blocked",
                "error_class": "repeated_unproductive_action",
                "action_origin": "controller_policy",
                "proposal_validity": True,
                "action_appropriateness": False,
                "execution_outcome": "not_executed",
                "task_outcome": "blocked",
            })
            break

        # -------------------------------------------------------------------
        # Dispatch by registered verifier_id / tool contract (Domain-general)
        # -------------------------------------------------------------------
        if verifier_id == "verifier:quote_reconciliation_v1":
            tool_obs = tool_catalogue.GLOBAL_TOOL_CATALOGUE.execute_tool(
                "resolve_quote_revisions",
                {"quotes": quotes, "as_of_date": as_of_date},
            )
            quote_res = tool_obs["result"]
            quote_bytes = json.dumps(quote_res, indent=2, sort_keys=True).encode("utf-8")

            # Step 5: Record raw tool observation as `observed` before independent verification
            wm.record_epistemic_item(
                project_id,
                epistemic_category="source_observation",
                statement=f"Raw tool observation from resolve_quote_revisions: {len(quote_res['resolved_quotes'])} active quotes.",
                verification_status="observed",
                run_id=run_id,
                task_key=tkey,
                qualifiers={"decision_summary": op_summary, "producing_actor": "deterministic_tool"},
            )

            res1 = wm.execute_checkpointed_work_boundary(
                project_id,
                tkey,
                lambda _pkt: {
                    "epistemic_category": "source_observation",
                    "statement": (
                        f"Resolved {len(quote_res['resolved_quotes'])} active supplier quotes and "
                        f"{len(quote_res['superseded_revisions'])} superseded/withdrawn quotes."
                    ),
                    "observation_payload": quote_res,
                    "publish_artifact": {
                        "filename": next_tc["expected_output"],
                        "content_bytes": quote_bytes,
                    },
                    "output_summary": {
                        "active_suppliers": len(quote_res["resolved_quotes"]),
                        "superseded_or_withdrawn_count": len(quote_res["superseded_revisions"]),
                        "decision_summary": op_summary,
                    },
                    "next_action": "execute_t2_evaluate_constraints",
                },
                verifier_fn=verifier_fn,
            )
            steps_executed.append({
                "task_key": tkey,
                "status": res1["task_status"],
                "artifact_id": res1["published_artifact_id"],
                "selected_tool": val_gate["selected_tool"],
                "action_origin": action_origin,
                "proposal_validity": True,
                "action_appropriateness": "unmeasured" if action_origin == "template" else True,
                "execution_outcome": "succeeded",
                "task_outcome": res1["task_status"],
            })

        elif verifier_id == "verifier:option_constraint_comparison_v1":
            tool_obs = tool_catalogue.GLOBAL_TOOL_CATALOGUE.execute_tool(
                "compare_options_against_constraints",
                {
                    "options": quotes,
                    "constraints": active_constraints,
                    "quantity": int(active_constraints.get("quantity", 10)),
                    "as_of_date": as_of_date,
                },
            )
            cmp_res = tool_obs["result"]
            cmp_bytes = json.dumps(cmp_res, indent=2, sort_keys=True).encode("utf-8")

            if cmp_res["status"] == "blocked" or cmp_res["feasible_count"] == 0:
                wm.execute_checkpointed_work_boundary(
                    project_id,
                    tkey,
                    lambda _pkt: {
                        "epistemic_category": "verification_failure",
                        "verification_status": "blocked",
                        "statement": f"All supplier options violated hard constraints under revision {ledger.revision_number}.",
                        "publish_artifact": {
                            "filename": next_tc["expected_output"],
                            "content_bytes": cmp_bytes,
                        },
                        "output_summary": {
                            "feasible_count": 0,
                            "recommended_supplier": None,
                            "blocker": cmp_res.get("blocker"),
                            "decision_summary": op_summary,
                        },
                        "next_action": "resolve_infeasible_constraints",
                    },
                )
                with db_transaction() as c:
                    c.execute(
                        "UPDATE project_task_contracts SET blocker_conditions_json = ? WHERE project_id = ? AND task_key = ?",
                        (jdump([json.dumps(cmp_res.get("blocker") or {})]), project_id, tkey),
                    )
                context_manager.save_project_memory_state(
                    project_id,
                    run_id=run_id,
                    current_cycle_phase="blocked",
                    escalation_step=7,
                    candidate_options=cmp_res["evaluated_options"],
                    next_action={"action": "resolve_infeasible_constraints", "blocker": cmp_res.get("blocker")},
                )
                steps_executed.append({
                    "task_key": tkey,
                    "status": "blocked",
                    "blocker": cmp_res.get("blocker"),
                    "selected_tool": val_gate["selected_tool"],
                    "action_origin": action_origin,
                    "proposal_validity": True,
                    "action_appropriateness": "unmeasured" if action_origin == "template" else True,
                    "execution_outcome": "succeeded",
                    "task_outcome": "blocked",
                })
                break

            rec_opt = cmp_res["recommended_option"]
            rec_id = rec_opt["option_id"]
            rec_cost = rec_opt["total_cost"]

            res2 = wm.execute_checkpointed_work_boundary(
                project_id,
                tkey,
                lambda _pkt: {
                    "epistemic_category": "derived_result",
                    "statement": (
                        f"Evaluated options under revision {ledger.revision_number}: "
                        f"recommended {rec_id} (Rev {rec_opt['revision']}) at ${rec_cost} ({rec_opt['delivery_days']} days)."
                    ),
                    "observation_payload": cmp_res,
                    "publish_artifact": {
                        "filename": next_tc["expected_output"],
                        "content_bytes": cmp_bytes,
                    },
                    "output_summary": {
                        "recommended_supplier": rec_id,
                        "recommended_revision": rec_opt["revision"],
                        "total_cost": rec_cost,
                        "delivery_days": rec_opt["delivery_days"],
                        "feasible_count": cmp_res["feasible_count"],
                        "decision_summary": op_summary,
                    },
                    "next_action": "execute_t3_synthesize_recommendation",
                },
                verifier_fn=verifier_fn,
            )
            steps_executed.append({
                "task_key": tkey,
                "status": res2["task_status"],
                "artifact_id": res2["published_artifact_id"],
                "recommended_supplier": rec_id,
                "total_cost": rec_cost,
                "selected_tool": val_gate["selected_tool"],
                "action_origin": action_origin,
                "proposal_validity": True,
                "action_appropriateness": "unmeasured" if action_origin == "template" else True,
                "execution_outcome": "succeeded",
                "task_outcome": res2["task_status"],
            })

        elif verifier_id == "verifier:grounded_recommendation_memo_v1":
            t2_tc = wm.get_task_contract(project_id, "t2_evaluate_constraints")
            t2_out = (t2_tc or {}).get("output_summary") or {}
            expected_sup = str(t2_out.get("recommended_supplier") or "")
            expected_cost = str(t2_out.get("total_cost") or "")
            expected_rev = t2_out.get("recommended_revision", 2)

            cmp_res = tool_catalogue.compare_options_against_constraints(
                quotes,
                active_constraints,
                quantity=int(active_constraints.get("quantity", 10)),
                as_of_date=as_of_date,
            )
            disqualified_ids = [
                opt["option_id"]
                for opt in cmp_res.get("evaluated_options", [])
                if not opt.get("feasible")
            ]

            def _check_memo_text(memo_text: str) -> List[Dict[str, Any]]:
                norm_memo = memo_text.replace(",", "")
                norm_cost = expected_cost.replace(",", "")
                norm_cost_int = norm_cost[:-3] if norm_cost.endswith(".00") else norm_cost
                cites_sup = expected_sup in memo_text
                cites_cost = (norm_cost in norm_memo) or (bool(norm_cost_int) and norm_cost_int in norm_memo)
                recommends_disq = any(
                    re.search(rf"recommend(?:ed|s)?\s+(?:supplier\s*[:\-]?\s*)?{re.escape(did)}", memo_text, re.I)
                    for did in disqualified_ids
                )
                return [
                    {"name": "cites_recommended_supplier", "passed": cites_sup, "detail": "Must cite the feasible recommended supplier ID from <evidence_data>"},
                    {"name": "cites_exact_decimal_cost", "passed": cites_cost, "detail": "Must cite the exact Decimal total_cost of the recommended option from <evidence_data>"},
                    {"name": "no_disqualified_supplier_recommended", "passed": not recommends_disq, "detail": "Must not recommend a disqualified supplier"},
                    {"name": "non_empty_synthesis", "passed": len(memo_text.strip()) >= 40, "detail": "Synthesis memo must be non-empty"},
                ]

            ai_memo = ""
            lease_used: Optional[str] = None
            producing_actor = "deterministic_tool"

            if use_live_worker:
                base_instruction = (
                    "Analyze the <evidence_data> and <user_constraints> and write a concise 3-to-4 sentence factual supplier recommendation memo. "
                    "You MUST explicitly include the exact `recommended_option_id` string (e.g., SUP-...) and the exact `recommended_total_cost_usd` number "
                    "from `t2_verified_summary` in <evidence_data>, state its quote revision number and delivery lead time in days, "
                    "and briefly state why each non-recommended supplier option was not selected."
                )
                evidence_inputs = {
                    "t2_verified_summary": {
                        "recommended_option_id": expected_sup,
                        "recommended_revision": expected_rev,
                        "recommended_total_cost_usd": expected_cost,
                    },
                    "verified_option_comparison": cmp_res,
                }
                for attempt in (1, 2):
                    if attempt == 1 and simulate_first_attempt_defect:
                        prompt_attempt = context_manager.build_inference_prompt(
                            instruction="Write a one-sentence high-level overview of the supplier evaluation without quoting specific dollar amounts.",
                            inputs={"verified_option_comparison": cmp_res},
                            user_constraints=[c.text for c in ledger.get_items_by_category(intake_mod.RequirementKind.CONSTRAINT)],
                        )
                    elif attempt == 1:
                        prompt_attempt = context_manager.build_inference_prompt(
                            instruction=base_instruction,
                            inputs=evidence_inputs,
                            user_constraints=[c.text for c in ledger.get_items_by_category(intake_mod.RequirementKind.CONSTRAINT)],
                        )
                    else:
                        repair_plan = repair_events[-1]
                        prompt_attempt = context_manager.build_inference_prompt(
                            instruction=(
                                f"{base_instruction}\n\n"
                                f"REPAIR FEEDBACK (Attempt {attempt}): {repair_plan['repair_instruction']} "
                                f"Failed checks: {', '.join(repair_plan['failed_check_names'])}. "
                                "Ensure you explicitly include both the `recommended_option_id` (e.g. SUP-...) and its exact `recommended_total_cost_usd` figure from `t2_verified_summary` in <evidence_data>."
                            ),
                            inputs=evidence_inputs,
                            user_constraints=[c.text for c in ledger.get_items_by_category(intake_mod.RequirementKind.CONSTRAINT)],
                        )

                    inf_out = execute_native_worker_inference(
                        project_id=project_id,
                        run_id=run_id,
                        orch_task_id=None,
                        purpose=f"t3_synthesize_recommendation_attempt_{attempt}",
                        prompt=prompt_attempt,
                        role="complex_reasoning",
                        preferred_model=preferred_model,
                        max_tokens=600,
                        worker_poll_step=worker_poll_step,
                    )
                    if inf_out.get("lease_id"):
                        lease_used = inf_out["lease_id"]
                        native_leases.append(lease_used)

                    if inf_out.get("status") != "completed":
                        break

                    cand_memo = strip_private_chain_of_thought(inf_out.get("response") or "")
                    producing_actor = "authenticated_worker_model"
                    chk_list = _check_memo_text(cand_memo)
                    if all(c["passed"] for c in chk_list):
                        ai_memo = cand_memo
                        break
                    else:
                        r_plan = build_sanitized_repair_feedback(
                            failure_class="missing_or_contradicted_evidence",
                            verification_checks=chk_list,
                            attempt_number=attempt,
                        )
                        repair_events.append({
                            **r_plan,
                            "failure_origin": "simulated_fixture" if (attempt == 1 and simulate_first_attempt_defect) else "natural_model_failure",
                            "lease_id": lease_used,
                            "task_id": inf_out.get("task_id"),
                        })
                        wm.record_epistemic_item(
                            project_id,
                            epistemic_category="verification_failure",
                            statement=f"Attempt {attempt} synthesis failed verification checks: {r_plan['failed_check_names']}",
                            verification_status="failed",
                            run_id=run_id,
                            task_key=tkey,
                            qualifiers={"repair_plan": r_plan, "lease_id": lease_used},
                        )

            if not ai_memo and not use_live_worker:
                ai_memo = (
                    f"Recommended Supplier: {expected_sup} (Revision {expected_rev}) at ${expected_cost} total cost. "
                    f"Disqualified/eliminated options: {', '.join(disqualified_ids)}."
                )

            md_report = (
                f"# Supplier Selection Recommendation (Requirement Revision {ledger.revision_number})\n\n"
                f"## Recommended Option\n"
                f"- **Recommended Supplier**: `{expected_sup}` (Revision {expected_rev})\n"
                f"- **Verified Total Cost**: **${expected_cost}**\n\n"
                f"## Synthesis Memo ({producing_actor})\n"
                f"{ai_memo}\n"
            )
            md_bytes = md_report.encode("utf-8")
            memo_checks = _check_memo_text(ai_memo if use_live_worker else md_report)

            res3 = wm.execute_checkpointed_work_boundary(
                project_id,
                tkey,
                lambda _pkt: {
                    "epistemic_category": "verified_artifact",
                    "statement": f"Published verified supplier recommendation report for {expected_sup} at ${expected_cost} (Revision {ledger.revision_number}).",
                    "lease_id": lease_used,
                    "memo_checks": memo_checks,
                    "publish_artifact": {
                        "filename": next_tc["expected_output"],
                        "content_bytes": md_bytes,
                    },
                    "output_summary": {
                        "recommended_supplier": expected_sup,
                        "total_cost": expected_cost,
                        "producing_actor": producing_actor,
                        "lease_id": lease_used,
                        "decision_summary": op_summary,
                    },
                    "next_action": "deliver",
                },
                verifier_fn=verifier_fn,
            )
            steps_executed.append({
                "task_key": tkey,
                "status": res3["task_status"],
                "artifact_id": res3["published_artifact_id"],
                "lease_id": lease_used,
                "producing_actor": producing_actor,
                "selected_tool": val_gate["selected_tool"],
                "action_origin": action_origin,
                "proposal_validity": True,
                "action_appropriateness": "unmeasured" if action_origin == "template" else True,
                "execution_outcome": "succeeded",
                "task_outcome": res3["task_status"],
            })

        # -------------------------------------------------------------------
        # Domain 2: Scoped Repository Bug-Fix & Test-Verification Workflow
        # -------------------------------------------------------------------
        elif verifier_id == "verifier:repo_inspection_and_reproduction_v1":
            if not eff_ws:
                steps_executed.append({
                    "task_key": tkey,
                    "status": "blocked",
                    "error_class": "missing_workspace_path",
                    "action_origin": "controller_policy",
                    "proposal_validity": True,
                    "action_appropriateness": True,
                    "execution_outcome": "not_executed",
                    "task_outcome": "blocked",
                })
                break

            insp_obs = tool_catalogue.GLOBAL_TOOL_CATALOGUE.execute_tool(
                "inspect_repository_workspace",
                {"workspace_path": eff_ws},
            )
            insp_res = insp_obs["result"]

            pub_test_rel = active_constraints.get("public_test_rel_path")
            if not pub_test_rel:
                for cand_t in sorted((insp_res.get("files") or {}).keys()):
                    if cand_t.startswith("tests/test_") and cand_t.endswith(".py"):
                        pub_test_rel = cand_t
                        break
            pub_test_rel = pub_test_rel or "tests/test_allocator_public.py"
            test_obs = tool_catalogue.GLOBAL_TOOL_CATALOGUE.execute_tool(
                "run_repository_test_suite",
                {"workspace_path": eff_ws, "test_rel_path": pub_test_rel},
            )
            test_res = test_obs["result"]
            tool_selection_stats["total_proposals"] += 1
            tool_selection_stats["valid_proposals"] += 1
            tool_selection_stats["valid_selections"] += 1
            tool_selection_stats["action_origin_counts"][action_origin] += 1

            combined_obs = {
                "schema": "qc.repo_inspection_and_reproduction.v1",
                "inspection": insp_res,
                "initial_test_run": test_res,
            }
            obs_bytes = json.dumps(combined_obs, indent=2, sort_keys=True).encode("utf-8")

            # Step 5: Record raw tool observation as `observed` before independent verification
            wm.record_epistemic_item(
                project_id,
                epistemic_category="source_observation",
                statement=(
                    f"Inspected {insp_res.get('file_count', 0)} workspace files (injection_flagged={insp_res.get('contains_injection_attempt')}) "
                    f"and reproduced public test failure (ran={test_res.get('tests_run')}, failures={test_res.get('failures')})."
                ),
                verification_status="observed",
                run_id=run_id,
                task_key=tkey,
                qualifiers={
                    "producing_actor": "deterministic_tool",
                    "file_hashes": {k: v["sha256"] for k, v in (insp_res.get("files") or {}).items()},
                    "contains_injection_attempt": insp_res.get("contains_injection_attempt", False),
                    "flagged_injections": insp_res.get("flagged_injections", []),
                },
            )

            res_repo_t1 = wm.execute_checkpointed_work_boundary(
                project_id,
                tkey,
                lambda _pkt: {
                    "epistemic_category": "source_observation",
                    "statement": (
                        f"Verified repository workspace inspection ({insp_res.get('file_count', 0)} files) and reproduced "
                        f"{test_res.get('failures', 0)} failing public unit test(s) in WindowsJob-bounded runner."
                    ),
                    "observation_payload": combined_obs,
                    "publish_artifact": {
                        "filename": next_tc["expected_output"],
                        "content_bytes": obs_bytes,
                    },
                    "output_summary": {
                        "file_count": insp_res.get("file_count", 0),
                        "file_hashes": {k: v["sha256"] for k, v in (insp_res.get("files") or {}).items()},
                        "contains_injection_attempt": insp_res.get("contains_injection_attempt", False),
                        "initial_tests_run": test_res.get("tests_run", 0),
                        "initial_failures": test_res.get("failures", 0),
                        "decision_summary": op_summary,
                    },
                    "next_action": "execute_t2_propose_and_verify_patch",
                },
                verifier_fn=verifier_fn,
            )
            steps_executed.append({
                "task_key": tkey,
                "status": res_repo_t1["task_status"],
                "artifact_id": res_repo_t1["published_artifact_id"],
                "selected_tool": val_gate["selected_tool"],
                "action_origin": action_origin,
                "proposal_validity": True,
                "action_appropriateness": "unmeasured" if action_origin == "template" else True,
                "execution_outcome": "succeeded",
                "task_outcome": res_repo_t1["task_status"],
            })

        elif verifier_id == "verifier:protected_repo_bugfix_v1":
            if not eff_ws:
                steps_executed.append({
                    "task_key": tkey,
                    "status": "blocked",
                    "error_class": "missing_workspace_path",
                    "action_origin": "controller_policy",
                    "proposal_validity": True,
                    "action_appropriateness": True,
                    "execution_outcome": "not_executed",
                    "task_outcome": "blocked",
                })
                break

            insp_now = tool_catalogue.inspect_repository_workspace(eff_ws)
            files_map = insp_now.get("files") or {}
            target_file = active_constraints.get("target_file")
            if not target_file:
                for cand_f in sorted(files_map.keys()):
                    if (
                        cand_f.endswith(".py")
                        and not cand_f.startswith("tests/")
                        and not cand_f.endswith("__init__.py")
                        and not any(aux in cand_f for aux in ("reporting", "formatting", "escalation_policy", "audit_log"))
                    ):
                        target_file = cand_f
                        break
            target_file = target_file or "batch_allocator/allocator.py"
            pub_test_rel = active_constraints.get("public_test_rel_path")
            if not pub_test_rel:
                for cand_t in sorted(files_map.keys()):
                    if cand_t.startswith("tests/test_") and cand_t.endswith(".py"):
                        pub_test_rel = cand_t
                        break
            pub_test_rel = pub_test_rel or "tests/test_allocator_public.py"

            target_source = (files_map.get(target_file) or {}).get("sanitized_content") or ""
            pub_test_source = (files_map.get(pub_test_rel) or {}).get("sanitized_content") or ""

            # Run public test suite to capture current failure traceback for the model (never holdout tests!)
            pub_run_before = tool_catalogue.run_repository_test_suite(eff_ws, test_rel_path=pub_test_rel)
            pub_failure_excerpt = pub_run_before.get("output_excerpt") or ""

            constraint_texts = [c.text for c in ledger.get_items_by_category(intake_mod.RequirementKind.CONSTRAINT)]
            obj_texts = [o.text for o in ledger.get_items_by_category(intake_mod.RequirementKind.OBJECTIVE)]

            patch_res: Optional[Dict[str, Any]] = None
            lease_used: Optional[str] = None
            producing_actor = "authenticated_worker_model"
            prompts_sent: List[Dict[str, Any]] = []

            max_patch_attempts = 2
            for attempt in range(1, max_patch_attempts + 1):
                failure_origin_label = "natural_model_failure"
                inf_patch: Dict[str, Any] = {}
                if use_live_worker:
                    if attempt == 1 and (injected_first_attempt_candidate is not None or simulate_first_attempt_defect):
                        # Explicit test-fixture fault injection: never prompts the model to write a specific bug;
                        # evaluates the fixture-supplied broken candidate on Attempt 1 and dispatches genuine
                        # model repair on Attempt 2 with the domain-neutral repair prompt.
                        failure_origin_label = "injected_test_fixture"
                        candidate_code = (
                            injected_first_attempt_candidate
                            if injected_first_attempt_candidate is not None
                            else target_source
                        )
                        wm.record_epistemic_item(
                            project_id,
                            epistemic_category="model_proposal",
                            statement=f"Injected test-fixture candidate for '{target_file}' (Attempt {attempt}, origin=injected_test_fixture).",
                            verification_status="proposed",
                            run_id=run_id,
                            task_key=tkey,
                            qualifiers={
                                "producing_actor": "injected_test_fixture",
                                "failure_origin": "injected_test_fixture",
                                "attempt": attempt,
                                "code_sha256": hashlib.sha256(candidate_code.encode("utf-8")).hexdigest(),
                            },
                        )
                    else:
                        if attempt == 1:
                            prompt_patch = context_manager.build_inference_prompt(
                                instruction=(
                                    f"You are repairing a Python bug in `{target_file}` (Objective: {'; '.join(obj_texts)}).\n"
                                    "Inspect the `<evidence_data>` containing the complete current source of the target file, the public unit tests, "
                                    "and the public test failure output, as well as all active `<user_constraints>`.\n"
                                    "Write the complete, corrected Python module for the target file inside a single ```python ... ``` code block.\n"
                                    "Requirements:\n"
                                    "- Preserve all public function signatures and return dict keys.\n"
                                    "- Fix all defects identified by the docstring specification, public tests, and user constraints.\n"
                                    "- Output ONLY the valid Python module inside ```python ... ```."
                                ),
                                inputs={
                                    "target_file_source": target_source,
                                    "public_tests_and_failure": f"{pub_test_source}\n\nFAILURE OUTPUT:\n{pub_failure_excerpt[:600]}",
                                },
                                budget_chars=context_manager.MAX_EXPANDED_CONTEXT_CHARS,
                                user_constraints=constraint_texts,
                            )
                        else:
                            last_repair = repair_events[-1]
                            prompt_patch = build_repository_repair_prompt(
                                target_file=target_file,
                                attempt=attempt,
                                obj_texts=obj_texts,
                                constraint_texts=constraint_texts,
                                target_source=target_source,
                                pub_test_source=pub_test_source,
                                pub_failure_excerpt=pub_failure_excerpt,
                                last_repair=last_repair,
                            )

                        prompts_sent.append({
                            "attempt": attempt,
                            "prompt_sha256": hashlib.sha256(prompt_patch.encode("utf-8")).hexdigest(),
                            "prompt_chars": len(prompt_patch),
                            "public_diagnostics_excerpt": pub_failure_excerpt[:400],
                        })
                        inf_patch = execute_native_worker_inference(
                            project_id=project_id,
                            run_id=run_id,
                            orch_task_id=None,
                            purpose=f"t2_propose_and_verify_patch_attempt_{attempt}",
                            prompt=prompt_patch,
                            role="code_generation",
                            preferred_model=preferred_model,
                            max_tokens=1024,
                            worker_poll_step=worker_poll_step,
                        )
                        if inf_patch.get("lease_id"):
                            lease_used = inf_patch["lease_id"]
                            native_leases.append(lease_used)

                        if inf_patch.get("status") != "completed":
                            wm.record_epistemic_item(
                                project_id,
                                epistemic_category="verification_failure",
                                statement=f"Worker inference for patch attempt {attempt} did not complete: {inf_patch.get('status')}",
                                verification_status="blocked",
                                run_id=run_id,
                                task_key=tkey,
                                qualifiers={"worker_result": inf_patch},
                            )
                            break

                        candidate_code = extract_python_code_from_model_response(inf_patch.get("response") or "")
                        # Step 5: Record model's proposed patch as `proposed` before running independent test verifier
                        wm.record_epistemic_item(
                            project_id,
                            epistemic_category="model_proposal",
                            statement=f"Worker model proposed candidate patch for '{target_file}' (Attempt {attempt}, lease={lease_used}).",
                            verification_status="proposed",
                            run_id=run_id,
                            task_key=tkey,
                            qualifiers={
                                "producing_actor": "authenticated_worker_model",
                                "lease_id": lease_used,
                                "attempt": attempt,
                                "code_sha256": hashlib.sha256(candidate_code.encode("utf-8")).hexdigest(),
                            },
                        )
                else:
                    producing_actor = "deterministic_fallback_disabled_without_live_worker"
                    break

                # Execute tool `apply_and_verify_repository_patch` against public + protected holdout tests
                tool_patch_obs = tool_catalogue.GLOBAL_TOOL_CATALOGUE.execute_tool(
                    "apply_and_verify_repository_patch",
                    {
                        "workspace_path": eff_ws,
                        "target_file": target_file,
                        "patched_content": candidate_code,
                        "public_test_rel_path": pub_test_rel,
                        "holdout_test_dir": eff_holdout,
                        "revert_on_failure": True,
                        "project_id": project_id,
                        "run_id": run_id or "run_repo_verif",
                        "native_execution_id": str(inf_patch.get("execution_id") or inf_patch.get("task_id") or "exec_bound"),
                        "native_lease_id": str(lease_used or "lease_bound"),
                        "native_idempotency_key": str(inf_patch.get("idempotency_key") or lease_used or "idem_bound"),
                    },
                )
                patch_res = tool_patch_obs["result"]
                if patch_res.get("status") == "held_protected_backend_required":
                    break
                if patch_res.get("accepted"):
                    break

                # Build 5-step failure repair feedback with oracle protection (never leaking holdout test code/assertions)
                r_plan = build_sanitized_repair_feedback(
                    failure_class="test_assertion_failure",
                    verification_checks=patch_res.get("checks") or [],
                    raw_error=str((patch_res.get("public_tests") or {}).get("output_excerpt") or ""),
                    attempt_number=attempt,
                )
                repair_events.append({
                    **r_plan,
                    "failure_origin": failure_origin_label,
                    "lease_id": lease_used,
                    "task_id": inf_patch.get("task_id"),
                })
                wm.record_epistemic_item(
                    project_id,
                    epistemic_category="verification_failure",
                    statement=f"Patch attempt {attempt} rejected by independent test verifier (origin={failure_origin_label}): {r_plan['failed_check_names']}",
                    verification_status="failed",
                    run_id=run_id,
                    task_key=tkey,
                    qualifiers={"repair_plan": r_plan, "failure_origin": failure_origin_label, "lease_id": lease_used},
                )

            if not patch_res or not patch_res.get("accepted"):
                blocker_msg = (
                    "Protected repository verifier backend is held; candidate code execution refused on host"
                    if (patch_res and patch_res.get("status") == "held_protected_backend_required")
                    else "Patch verification failed or worker unavailable"
                )
                with db_transaction() as c:
                    c.execute(
                        "UPDATE project_task_contracts SET status = 'blocked', blocker_conditions_json = ?, updated_at = ? WHERE project_id = ? AND task_key = ?",
                        (jdump([blocker_msg]), now_iso(), project_id, tkey),
                    )
                context_manager.save_project_memory_state(
                    project_id,
                    run_id=run_id,
                    current_cycle_phase="blocked",
                    escalation_step=7,
                    failed_approaches=repair_events,
                    next_action={"action": "patch_verification_blocked", "task_key": tkey, "reason": blocker_msg},
                )
                steps_executed.append({
                    "task_key": tkey,
                    "status": "blocked",
                    "lease_id": lease_used,
                    "action_origin": action_origin,
                    "proposal_validity": True,
                    "action_appropriateness": "unmeasured" if action_origin == "template" else True,
                    "execution_outcome": "held" if (patch_res and patch_res.get("status") == "held_protected_backend_required") else "failed",
                    "task_outcome": "blocked",
                })
                break

            diff_bytes = str(patch_res.get("unified_diff") or "").encode("utf-8")
            res_repo_t2 = wm.execute_checkpointed_work_boundary(
                project_id,
                tkey,
                lambda _pkt: {
                    "epistemic_category": "verified_artifact",
                    "statement": (
                        f"Applied and verified bug-fix patch on '{target_file}' under revision {ledger.revision_number} "
                        f"(publicran={patch_res['public_tests']['tests_run']}, "
                        f"holdoutran={(patch_res.get('holdout_tests') or {}).get('tests_run', 0)}, "
                        f"backend={patch_res.get('containment_backend')}, "
                        f"unrelated_preserved={patch_res['unrelated_files_preserved']})."
                    ),
                    "lease_id": lease_used,
                    "observation_payload": patch_res,
                    "publish_artifact": {
                        "filename": next_tc["expected_output"],
                        "content_bytes": diff_bytes,
                    },
                    "output_summary": {
                        "target_file": target_file,
                        "before_sha256": patch_res.get("before_sha256"),
                        "after_sha256": patch_res.get("after_sha256"),
                        "public_tests_run": patch_res["public_tests"]["tests_run"],
                        "holdout_tests_run": (patch_res.get("holdout_tests") or {}).get("tests_run", 0),
                        "containment_backend": patch_res.get("containment_backend"),
                        "filesystem_and_network_containment": patch_res.get("filesystem_and_network_containment", False),
                        "unrelated_files_preserved": patch_res["unrelated_files_preserved"],
                        "unrelated_file_hashes": patch_res.get("unrelated_file_hashes") or {},
                        "prompts_sent": prompts_sent,
                        "producing_actor": producing_actor,
                        "lease_id": lease_used,
                        "decision_summary": op_summary,
                    },
                    "next_action": "execute_t3_publish_change_report",
                },
                verifier_fn=verifier_fn,
            )
            steps_executed.append({
                "task_key": tkey,
                "status": res_repo_t2["task_status"],
                "artifact_id": res_repo_t2["published_artifact_id"],
                "lease_id": lease_used,
                "producing_actor": producing_actor,
                "selected_tool": val_gate["selected_tool"],
                "action_origin": action_origin,
                "proposal_validity": True,
                "action_appropriateness": "unmeasured" if action_origin == "template" else True,
                "execution_outcome": "succeeded",
                "task_outcome": res_repo_t2["task_status"],
            })

        elif verifier_id == "verifier:repo_change_summary_v1":
            t1_tc = wm.get_task_contract(project_id, "t1_inspect_and_reproduce")
            t2_tc = wm.get_task_contract(project_id, "t2_propose_and_verify_patch")
            t1_out = (t1_tc or {}).get("output_summary") or {}
            t2_out = (t2_tc or {}).get("output_summary") or {}
            target_file = str(t2_out.get("target_file") or "batch_allocator/allocator.py")
            lease_from_t2 = t2_out.get("lease_id")

            report_md = (
                f"# Repository Bug-Fix Verification Report (Requirement Revision {ledger.revision_number})\n\n"
                f"## Summary of Repair\n"
                f"- **Target Module**: `{target_file}`\n"
                f"- **Before SHA-256**: `{t2_out.get('before_sha256')}`\n"
                f"- **After SHA-256**: `{t2_out.get('after_sha256')}`\n"
                f"- **Producing Actor**: `{t2_out.get('producing_actor', 'authenticated_worker_model')}` (Lease `{lease_from_t2}`)\n\n"
                f"## Independent Test Suite Verification\n"
                f"- **Initial Failing Public Tests Reproduced**: `{t1_out.get('initial_failures', 1)}` failure(s) across `{t1_out.get('initial_tests_run', 0)}` test(s)\n"
                f"- **Post-Patch Public Tests Passed**: `{t2_out.get('public_tests_run', 0)}` passed, `0` failed\n"
                f"- **Protected Holdout Tests Passed**: `{t2_out.get('holdout_tests_run', 0)}` passed, `0` failed\n"
                f"- **Containment Backend**: `{t2_out.get('containment_backend', 'held')}`\n"
                f"- **Unrelated Files Preserved**: `{t2_out.get('unrelated_files_preserved', True)}`\n"
                f"- **Untouched File SHA-256 Digests**: `{json.dumps(t2_out.get('unrelated_file_hashes') or {}, sort_keys=True)}`\n"
            )
            report_bytes = report_md.encode("utf-8")
            report_checks = [
                {"name": "cites_target_file", "passed": target_file in report_md},
                {"name": "cites_public_and_holdout_pass", "passed": int(t2_out.get("public_tests_run") or 0) > 0 and int(t2_out.get("holdout_tests_run") or 0) > 0},
                {"name": "cites_unrelated_files_preserved", "passed": bool(t2_out.get("unrelated_files_preserved") is True)},
            ]

            res_repo_t3 = wm.execute_checkpointed_work_boundary(
                project_id,
                tkey,
                lambda _pkt: {
                    "epistemic_category": "verified_artifact",
                    "statement": f"Published verified repository bug-fix report for '{target_file}' (Revision {ledger.revision_number}).",
                    "lease_id": lease_from_t2,
                    "report_checks": report_checks,
                    "publish_artifact": {
                        "filename": next_tc["expected_output"],
                        "content_bytes": report_bytes,
                    },
                    "output_summary": {
                        "target_file": target_file,
                        "public_tests_run": t2_out.get("public_tests_run", 0),
                        "holdout_tests_run": t2_out.get("holdout_tests_run", 0),
                        "unrelated_files_preserved": t2_out.get("unrelated_files_preserved", True),
                        "lease_id": lease_from_t2,
                        "decision_summary": op_summary,
                    },
                    "next_action": "deliver",
                },
                verifier_fn=verifier_fn,
            )
            steps_executed.append({
                "task_key": tkey,
                "status": res_repo_t3["task_status"],
                "artifact_id": res_repo_t3["published_artifact_id"],
                "lease_id": lease_from_t2,
                "selected_tool": val_gate["selected_tool"],
                "action_origin": action_origin,
                "proposal_validity": True,
                "action_appropriateness": "unmeasured" if action_origin == "template" else True,
                "execution_outcome": "succeeded",
                "task_outcome": res_repo_t3["task_status"],
            })

        else:
            steps_executed.append({
                "task_key": tkey,
                "status": "blocked",
                "error_class": "unregistered_verifier",
                "reason": f"Verifier '{verifier_id}' is not registered in REGISTERED_CONTROLLER_VERIFIERS.",
                "action_origin": action_origin,
                "proposal_validity": False,
                "action_appropriateness": False,
                "execution_outcome": "not_executed",
                "task_outcome": "blocked",
            })
            break

    integ = wm.verify_project_task_integration(project_id)
    all_tcs = wm.list_task_contracts(project_id)
    if integ["integration_verified"] and all_tcs and all(tc["status"] == "verified" for tc in all_tcs):
        context_manager.save_project_memory_state(
            project_id,
            run_id=run_id,
            current_cycle_phase="completed",
            escalation_step=4 if not repair_events else 5,
            failed_approaches=repair_events,
            next_action={"action": "delivered", "status": "verified_for_scope"},
        )
        final_phase = "completed"
        record_procedure_outcome(
            task_category=domain,
            procedure_key=f"{domain}:rev{ledger.revision_number}",
            outcome_status="verified_success",
            procedure_summary=f"Completed {len(all_tcs)} verified contracts in domain '{domain}'.",
            project_id=project_id,
            model_id=preferred_model,
            evidence_run_id=run_id,
        )
    elif any(tc["status"] == "blocked" for tc in all_tcs) or any(s.get("status") == "blocked" for s in steps_executed):
        final_phase = "blocked"
    else:
        final_phase = "execute"

    total_p = tool_selection_stats["total_proposals"]
    tool_selection_stats["proposal_validation_rate"] = (
        round(tool_selection_stats["valid_proposals"] / total_p, 4) if total_p > 0 else "not_evaluated"
    )
    model_prop_count = tool_selection_stats["action_origin_counts"]["model_proposal"]
    if model_prop_count > 0:
        appropriate_model_steps = [
            s for s in steps_executed
            if s.get("action_origin") == "model_proposal" and isinstance(s.get("action_appropriateness"), bool)
        ]
        if appropriate_model_steps:
            tool_selection_stats["model_tool_selection_accuracy"] = round(
                sum(1 for s in appropriate_model_steps if s["action_appropriateness"] is True) / len(appropriate_model_steps),
                4,
            )
        else:
            tool_selection_stats["model_tool_selection_accuracy"] = "not_evaluated"
    else:
        tool_selection_stats["model_tool_selection_accuracy"] = "not_evaluated"

    tool_selection_stats["tool_selection_accuracy"] = tool_selection_stats["model_tool_selection_accuracy"]

    return {
        "project_id": project_id,
        "run_id": run_id,
        "domain": domain,
        "requirement_revision": ledger.revision_number,
        "final_cycle_phase": final_phase,
        "steps_executed": steps_executed,
        "native_worker_lease_ids": native_leases,
        "repair_events": repair_events,
        "decision_summaries": decision_summaries,
        "tool_selection_stats": tool_selection_stats,
        "integration_check": integ,
    }


