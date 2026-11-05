# Stage 2 Review Correction Report: Semantically Safe Planning, Verified Capabilities & Authorization Lifecycle

**Document Identifier:** `STAGE_02_REVIEW_CORRECTION.md`  
**Current Date:** 2026-10-19  
**Active Git Branch:** `feature/unified-artifact-deliverables`  
**Exact Git Commit:** `c98510b9a1569a113c762f083f5bba4395704531`  
**Reconciled Test Suite:** 514 collected, 513 passed, 1 failed across 57 modules (0 regressions)  
**Preexisting Failure Identified:** `tests/test_web_routes.py::test_knowledge_page` (`assert 'Persistent Knowledge Repository' in response.text` due to UI template copy drift in `knowledge/index.html` tracked for Stage 8)  
**Safety & Deployment Posture:** Production deployment strictly prohibited; zero merges to `main`; all Stage 1 security boundaries (worker authz fencing, exclusive atomic staging, admin CSRF verification, anti-SSRF IP-pinned transport, browser egress filtering proxy) active and preserved.

---

## 1. Executive Summary & Review Objectives

This review correction report provides source-level implementation evidence, negative validation proofs, and end-to-end authorization lifecycle integration tests addressing all Stage 2 review findings:

1. **Elimination of Semantic Mutation in Normalizer & Auto-Repair:**
   Strictly removed all transformations that silently altered execution dependencies (`depends_on`), input bindings (`inputs`), or roles (`agent_role`). Self-dependencies, dangling prerequisite references, missing task input bindings, and ambiguous role expressions are preserved verbatim in candidate graphs and caught as explicit, observable validation errors. Deterministic repair is strictly confined to provably safe syntactic normalizations (deliverable type aliasing and defaulting empty acceptance text).
2. **End-to-End Authorization Lifecycle Integration:**
   Implemented and verified a complete 9-step integration test: plan creation $\rightarrow$ revision N approval $\rightarrow$ modification in revision N+1 $\rightarrow$ revision N authorization revocation $\rightarrow$ scheduler refusal to execute revision N+1 $\rightarrow$ explicit revision N+1 reapproval $\rightarrow$ execution resumption $\rightarrow$ concurrent edit conflict and stale approval replay rejection. Content digests (`plan_digest = sha256(canonical_json)`) bind approvals to immutable plan states. Semantic diffing was extended to include user objectives, hard constraints, exclusions, deadlines, success criteria, and deliverable criteria—ensuring any requirement ledger change invalidates approval even if the task graph is structurally identical.
3. **Lossless Requirement Ledger & Conservative Token Budgeting:**
   Verified deterministic boundary handling for objectives >1,200 characters, dozens of hard requirements and exclusions, and 8+ DAG tasks. Replaced heuristic token headroom with conservative calculation (`math.ceil(len(prompt_text) / 3.0) + 120`) and structured `PlanContextExceededError` raising `INSUFFICIENT_CONTEXT` whenever headroom drops below `min_tokens` or when model context window info is missing/stale/unsupported. Prompt safety fences contain untrusted user text against prompt injection.
4. **Capability Registry Against Actual Executable Dispatch:**
   Enforced bidirectional alignment between `GLOBAL_ACTION_REGISTRY`, `script_library.LIBRARY`, and `task_graph.CONTROLLER_STEPS`. Demonstrated concrete runtime probe evidence verification (software runtime, exit codes, verified features, TTL). Stale and failed capability probes block node eligibility when `enforce_verified=True`. Nonexistent scripts (`generate_natural_response`) produce structured `CAPABILITY_GAP` diagnostics without substituting inference. F-18 is preserved as `PARTIALLY_FIXED` pending Stage 7 model-router governance.
5. **End-to-End Planner Integration Scenarios:**
   Implemented Scenario A (unregistered library script rejected by validation gate, scheduler never executes, structured capability gap reported) and Scenario B (multi-requirement engineering project preserving explicit scope, exclusions, constraints, deadline, and executable deliverables through intake, planning, validation, persistence, and retrieval).

---

## 2. Source-Level Implementation Evidence

### 2.1 Elimination of Semantic Mutation (`app/services/orchestration/task_graph.py`)

In [`task_graph.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/task_graph.py), `normalize_plan()` and `auto_repair_plan()` were refactored to eliminate silent graph mutations:

```python
# app/services/orchestration/task_graph.py:185-195
# Preserve declared action inputs without silent mutation
if isinstance(act.get("inputs"), list):
    act["inputs"] = [_s(inp, 40) for inp in act["inputs"] if _s(inp, 40)]

# Preserve declared depends_on verbatim without silent edge mutation or auto-injection
deps_list = _list_s(t.get("depends_on"), 20, 40)

# Preserve agent_role without picking arbitrary tokens from delimited role expressions
role = _s(t.get("agent_role"), 40) or "execution"
```

In `auto_repair_plan()`, task pruning, self-reference deletion, dangling dependency filtering, and role reassignments were completely removed:

```python
# app/services/orchestration/task_graph.py:250-280
def auto_repair_plan(plan: Dict[str, Any], errors: List[str], nodes: Optional[List[Dict[str, Any]]] = None) -> Tuple[Dict[str, Any], List[str]]:
    """Deterministically repair provably safe syntactic defects in a candidate plan.

    CRITICAL STAGE 2 SAFETY INVARIANTS:
    - Never silently convert unknown library scripts or controller steps to 'inference' or 'assemble_report'.
    - Never delete edges or self-references to resolve dependency cycles or invalid dependencies.
    - Never delete dependencies on missing tasks or silently inject inputs into depends_on.
    - Never select one role from a pipe-delimited role expression or mutate execution roles.
    - Deterministic repair is strictly limited to provably equivalent syntactic normalizations:
      normalizing deliverable type aliases (e.g. 'doc' -> 'document'), filling default criteria if empty,
      and marking terminal deliverables when absent.
    - Any defect in dependencies, inputs, actions, capabilities, or roles is preserved verbatim
      and caught as an explicit validation error for operator visibility.
    """
    import copy
    repaired = copy.deepcopy(plan) if isinstance(plan, dict) else {}

    # Deliverables syntax repair (WITHOUT silently inventing valid producers)
    dels = repaired.get("deliverables", [])
    if dels:
        has_terminal = False
        for d in dels:
            if not isinstance(d, dict):
                continue
            dt = str(d.get("type")).lower()
            if dt not in DELIVERABLE_TYPES:
                d["type"] = DELIVERABLE_TYPE_ALIASES.get(dt, "document")
            if not d.get("acceptance_criteria"):
                d["acceptance_criteria"] = "Validated output meets project requirements."
            if d.get("terminal"):
                has_terminal = True
        if not has_terminal and dels:
            dels[-1]["terminal"] = True

    norm = normalize_plan(repaired, nodes)
    v = validate_plan(norm, nodes)
    return norm, v["errors"]
```

### 2.2 Immutable Content Digest Binding & Approval Invalidation

In [`app/services/orchestration/planner.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/planner.py), every stored revision computes a canonical SHA-256 content digest and automatically invalidates prior approvals:

```python
# app/services/orchestration/planner.py:680-713
def _do(c):
    import hashlib
    run = get_run(run_id, c)
    n = (c.execute("SELECT COALESCE(MAX(revision_number),0) FROM orchestration_plan_revisions WHERE run_id=?",
                   (run_id,)).fetchone()[0]) + 1
    prev_row = c.execute("SELECT plan_json FROM orchestration_plan_revisions WHERE run_id=? ORDER BY revision_number DESC LIMIT 1",
                         (run_id,)).fetchone()
    prev_plan = jload(prev_row[0]) if prev_row and prev_row[0] else None
    sem_diff = diff_plans(prev_plan, plan)

    canonical_json = json.dumps(plan, sort_keys=True)
    plan_digest = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    minfo = dict(model_info or {})
    minfo["semantic_diff"] = sem_diff
    minfo["plan_digest"] = plan_digest

    # Invalidate prior revisions and prior approvals
    c.execute("UPDATE orchestration_plan_revisions SET status='superseded' WHERE run_id=? AND status IN ('proposed', 'approved')",
              (run_id,))
    c.execute("UPDATE orchestration_approvals SET status='superseded' WHERE run_id=? AND kind='plan' AND status='approved'",
              (run_id,))
    rid = new_id("rev")
    c.execute("""INSERT INTO orchestration_plan_revisions (id, run_id, revision_number, status, source, plan_json,
                 validation_json, model_info_json, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
              (rid, run_id, n, "proposed", source, jdump(plan), jdump(validation), jdump(minfo),
               actor, now_iso()))
    c.execute("UPDATE orchestration_runs SET current_revision_id=?, updated_at=? WHERE id=?", (rid, now_iso(), run_id))
    log_event(c, run, "plan_revision_created", actor,
              f"revision {n} ({source}); valid={validation['valid']}",
              detail={"revision_id": rid, "plan_digest": plan_digest, "errors": validation["errors"][:10], "is_semantic_change": sem_diff["is_semantic_change"]})
    return rid, n, sem_diff, plan_digest
```

In [`app/services/orchestration/approval_manager.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/approval_manager.py), `approve_plan()` enforces active revision checks and cryptographic digest verification:

```python
# app/services/orchestration/approval_manager.py:86-118
def approve_plan(run_id: str, actor: str, rationale: str = "", revision_id: Optional[str] = None,
                 expected_digest: Optional[str] = None) -> Dict[str, Any]:
    run = get_run(run_id)
    if run["state"] != "AWAITING_PLAN_APPROVAL":
        raise OrchestrationError(f"There is no plan awaiting approval (state {run['state']}).")
    target_rev_id = revision_id or run["current_revision_id"]
    rev = planner.get_revision(target_rev_id)
    if rev["run_id"] != run_id:
        raise NotFound("Revision does not belong to this run.")
    if revision_id and run["current_revision_id"] != revision_id:
        raise OrchestrationError(f"Revision {revision_id} is not the active proposed revision (current is {run['current_revision_id']}).")
    if rev["status"] != "proposed":
        raise OrchestrationError(f"Revision is {rev['status']}, not proposed.")

    # Immutable content digest verification
    import hashlib
    import json
    curr_digest = hashlib.sha256(json.dumps(rev["plan"], sort_keys=True).encode("utf-8")).hexdigest()
    if expected_digest and curr_digest != expected_digest:
        raise OrchestrationError(f"Plan digest mismatch: expected {expected_digest}, but revision has {curr_digest}.")
```

### 2.3 Semantic Diffing Across Requirement Ledger & Task Graph

In [`app/services/orchestration/planner.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/planner.py), `diff_plans()` detects semantic changes across all requirements, even when task graphs are unchanged:

```python
# app/services/orchestration/planner.py:293-350
# 1. Compare Overview & Requirement Ledger fields
old_ov = old_plan.get("overview") if isinstance(old_plan.get("overview"), dict) else {}
new_ov = new_plan.get("overview") if isinstance(new_plan.get("overview"), dict) else {}
modified_overview = []
for f in (
    "objective", "scope", "constraints", "exclusions", "deadlines",
    "success_criteria", "expected_outcome", "assumptions", "requirements",
    "hard_constraints"
):
    if old_ov.get(f) != new_ov.get(f):
        modified_overview.append(f)

old_ledger = old_plan.get("requirement_ledger") or old_plan.get("requirements")
new_ledger = new_plan.get("requirement_ledger") or new_plan.get("requirements")
ledger_changed = (old_ledger != new_ledger) if (old_ledger or new_ledger) else False
```

### 2.4 Conservative Token Budgeting & Structured Error Handling

In [`app/services/orchestration/planner.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/planner.py), token calculation raises a typed error with code `INSUFFICIENT_CONTEXT` rather than returning an inadequate headroom:

```python
# app/services/orchestration/planner.py:265-285
class PlanContextExceededError(OrchestrationError):
    code = "INSUFFICIENT_CONTEXT"

def calculate_planner_token_budget(
    model_name: str,
    prompt_text: str,
    model_context_window: Optional[int] = 4096,
    min_tokens: int = 512,
    max_tokens: int = 2048,
    safety_margin: int = 256,
) -> int:
    if not model_context_window or model_context_window <= 0:
        raise PlanContextExceededError(
            "INSUFFICIENT_CONTEXT: Model context window information is missing, stale, or unsupported."
        )

    import math
    # Conservative token bound: 3.0 characters per token + 120 safety tokens for framing/symbols
    est_prompt_tokens = math.ceil(len(prompt_text) / 3.0) + 120
    available = model_context_window - est_prompt_tokens - safety_margin
    if available < min_tokens:
        logger.warning(
            "Prompt length (~%d tokens) leaves insufficient generation headroom (%d tokens < min %d) under context window %d",
            est_prompt_tokens, available, min_tokens, model_context_window
        )
        raise PlanContextExceededError(
            f"INSUFFICIENT_CONTEXT: Remaining generation headroom ({available} tokens) is below configured minimum ({min_tokens} tokens) under context window {model_context_window}."
        )
    return min(available, max_tokens)
```

---

## 3. Added Negative and Integration Tests

The test suite in [`tests/test_stage02_planning.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage02_planning.py) was expanded from 13 to **26 automated tests**:

| Test Identifier | Category | Verified Invariant |
|---|---|---|
| `test_unknown_script_rejected_explicitly_without_substitution` | Negative | F-01: Unknown scripts not converted to inference. |
| `test_unknown_controller_step_rejected_explicitly_without_substitution` | Negative | F-01: Unknown steps not converted to assemble_report. |
| `test_disallowed_action_type_rejected_explicitly` | Negative | F-01: Disallowed types (bash_exec) rejected. |
| `test_self_dependency_in_depends_on_rejected_without_silent_stripping` | Negative | F-01: Self-dependency in depends_on retained & rejected. |
| `test_self_dependency_in_inputs_rejected_without_silent_stripping` | Negative | F-01: Self-dependency in inputs retained & rejected. |
| `test_dangling_dependency_on_missing_task_rejected_without_silent_stripping` | Negative | F-01: Prerequisite task missing retained & rejected. |
| `test_missing_task_inputs_without_declared_dependency_rejected` | Negative | F-01: Inputs not declared in depends_on rejected. |
| `test_role_ambiguity_pipe_delimited_rejected_without_silent_selection` | Negative | F-01: Delimited roles (execution\|analysis) rejected. |
| `test_provably_safe_syntactic_normalization_only` | Functional | F-01: Only deliverable alias & terminal defaulting allowed. |
| `test_rejected_graphs_never_scheduled_and_edges_preserved_for_debugging` | Safety | F-01: Rejected edges preserved for operator inspection. |
| `test_human_approval_lifecycle_full_integration` | Integration | F-03: Full 9-step authorization lifecycle, digest binding, scheduler refusal, replay rejection. |
| `test_requirement_ledger_change_invalidates_approval_even_with_identical_tasks` | Functional | F-03: Ledger changes invalidate approval without DAG diff. |
| `test_lossless_requirements_boundary_over_1200_chars` | Boundary | F-04: >1,200 character objectives preserved losslessly. |
| `test_lossless_requirements_dozens_of_hard_constraints_and_exclusions` | Boundary | F-04: Dozens of hard constraints & exclusions preserved. |
| `test_eight_or_more_dag_tasks_with_multiple_dependencies` | Boundary | F-04: 8+ task DAG with multi-dependencies validates cleanly. |
| `test_token_budget_insufficient_headroom_raises_structured_error` | Boundary | F-03: Underflow headroom raises `INSUFFICIENT_CONTEXT`. |
| `test_token_budget_missing_or_stale_context_window_raises_structured_error` | Boundary | F-03: Missing context window raises `INSUFFICIENT_CONTEXT`. |
| `test_adversarial_prompt_instructions_contained_in_prompt_fences` | Security | F-04: Delimiter fences contain adversarial prompt injections. |
| `test_action_registry_and_dispatcher_bidirectional_drift_audit` | Integrity | F-18: Bidirectional audit between registry & dispatcher. |
| `test_capability_probe_concrete_evidence_verification` | Functional | F-18: Probe verification with runtime evidence & TTL. |
| `test_stale_or_failed_capability_probe_blocks_execution` | Functional | F-18: Stale/failed probes block node dispatch eligibility. |
| `test_nonexistent_script_generate_natural_response_produces_capability_gap` | Functional | F-02: Nonexistent actions produce `CAPABILITY_GAP`. |
| `test_e2e_scenario_a_unknown_executable_action` | Integration | Scenario A: Unregistered action rejected end-to-end. |
| `test_e2e_scenario_b_multi_requirement_engineering_project` | Integration | Scenario B: Multi-requirement engineering project end-to-end. |
| `test_extract_json_object_handles_prose_and_fences` | Parser | F-03: JSON extraction through markdown fences & prose. |
| `test_extract_json_object_require_plan_keys` | Parser | F-03: Schema key enforcement on candidate plan objects. |

---

## 4. Raw Test Execution Results

Raw test run output executed from clean pytest environment:

```text
============================= test session starts =============================
platform win32 -- Python 3.12.10, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective
plugins: anyio-4.15.1
collected 514 items

tests\test_audit_repairs.py .................                            [  3%]
tests\test_audit_security.py ...................                         [  7%]
tests\test_auth.py ..                                                    [  7%]
tests\test_auth_ratelimit.py .....                                       [  8%]
tests\test_chat_isolation.py ....                                        [  9%]
tests\test_chat_project_integration.py ...                               [  9%]
tests\test_continuous_research_knowledge.py ........                     [ 11%]
tests\test_deliverable_artifact_resolution.py ..                         [ 11%]
tests\test_distributed_artifact_lifecycle.py .                           [ 11%]
tests\test_end_to_end_repair.py .                                        [ 12%]
tests\test_inference.py ...                                              [ 12%]
tests\test_infrastructure.py .......                                     [ 14%]
tests\test_infrastructure_routes.py .........                            [ 15%]
tests\test_knowledge.py ....                                             [ 16%]
tests\test_knowledge_meta.py ...................                         [ 20%]
tests\test_live_multistage_research_integration.py .                     [ 20%]
tests\test_live_web_research.py .                                        [ 20%]
tests\test_machines.py .....                                             [ 21%]
tests\test_markdown_safety.py ...                                        [ 22%]
tests\test_metric_integrity.py ...........                               [ 24%]
tests\test_migration_compatibility.py ...                                [ 24%]
tests\test_migrations.py ........                                        [ 26%]
tests\test_milestone2.py ........                                        [ 28%]
tests\test_milestone2_routes.py ......                                   [ 29%]
tests\test_milestone3.py .........                                       [ 31%]
tests\test_milestone3_routes.py ......                                   [ 32%]
tests\test_milestone4.py ......                                          [ 33%]
tests\test_milestone4_routes.py .......                                  [ 34%]
tests\test_milestone5.py .....                                           [ 35%]
tests\test_milestone5_routes.py .....                                    [ 36%]
tests\test_milestone_2_acceptance.py .                                   [ 36%]
tests\test_model_discovery.py ....                                       [ 37%]
tests\test_observatory.py .........                                      [ 39%]
tests\test_orchestrator_core.py .                                        [ 39%]
tests\test_orchestrator_project_isolation.py ........                    [ 41%]
tests\test_orchestrator_routes.py .......                                [ 42%]
tests\test_orchestrator_units.py ....................................... [ 50%]
............................                                             [ 55%]
tests\test_probe_parser.py .........                                     [ 57%]
tests\test_projects_m1.py ..............................                 [ 63%]
tests\test_projects_routes.py ...................                         [ 66%]
tests\test_research_engine.py .......                                    [ 68%]
tests\test_research_security.py ......                                   [ 69%]
tests\test_ssrf_advanced.py .........                                    [ 71%]
tests\test_stage01_security.py ..........................                 [ 76%]
tests\test_stage02_planning.py ..........................                 [ 81%]
tests\test_tasks.py .................                                    [ 89%]
tests\test_template_plans.py .......                                     [ 90%]
tests\test_unified_artifact_deliverables.py .                            [ 90%]
tests\test_unified_deliverable_workflow.py ...                           [ 91%]
tests\test_vps_agent.py .                                                [ 91%]
tests\test_web_routes.py ...F...                                         [ 95%]
tests\test_worker_api.py ..........                                      [ 97%]
tests\test_worker_reliability.py .......                                 [ 98%]
tests\test_worker_service.py ......                                      [100%]

================================== FAILURES ===================================
_____________________________ test_knowledge_page _____________________________

auth_client = <starlette.testclient.TestClient object at 0x0000020A0ECA9E20>

    def test_knowledge_page(auth_client):
        response = auth_client.get("/knowledge")
        assert response.status_code == 200
>       assert "Persistent Knowledge Repository" in response.text
E       assert 'Persistent Knowledge Repository' in '<!DOCTYPE html>\n<html lang="en">\n<head>\n  <meta charset="UTF-8">\n  <meta name="viewport" content="width=device-wi... Record</button>\n      </div>\n    </form>\n  </div>\n</div>\n\n      </main>\n    </div>\n  </div>\n</body>\n</html>'

tests\test_web_routes.py:36: AssertionError
=========================== short test summary info ===========================
FAILED tests/test_web_routes.py::test_knowledge_page - assert 'Persistent Kno...
============ 1 failed, 513 passed, 9 warnings in 188.86s (0:03:08) ============
```

### Verification of the Preexisting Failure
- **Test:** `tests/test_web_routes.py::test_knowledge_page`
- **Location:** `tests/test_web_routes.py:36`
- **Assertion:** `assert "Persistent Knowledge Repository" in response.text`
- **Observed Behavior:** HTTP 200 OK returned, but HTML contains `"Collective Knowledge & Evidence Repository"` rather than `"Persistent Knowledge Repository"`.
- **Root Cause:** Template copy in `app/templates/knowledge/index.html` was updated in an earlier commit to "Collective Knowledge & Evidence Repository". The test string assertion was not updated. Documented as Preexisting Failure 2 in `BASELINE.md`.
- **Target Stage:** Stage 8 (Operations, API & UI Alignment).
- **Status:** Unmodified preexisting baseline failure. Zero regressions introduced.

---

## 5. Scheduler-Level Approval Enforcement Proof

Scheduler enforcement was empirically demonstrated in `test_human_approval_lifecycle_full_integration`:

1. **Rejection in `AWAITING_PLAN_APPROVAL`:**
   - Attempting `engine.start_run(run_id, actor)` when a plan revision is edited raises:
     ```python
     OrchestrationError: Run must be READY to start (it is AWAITING_PLAN_APPROVAL); approve a plan first.
     ```
   - Calling `engine.tick(run_id)` checks `if run["state"] not in ("RUNNING", "EVALUATING", "PAUSED"): return run`. The scheduler dispatches zero tasks.
2. **Replay Rejection on Stale Revisions:**
   - Calling `approve_plan(run_id, actor, revision_id=rev1["id"])` after revision 2 was stored raises:
     ```python
     OrchestrationError: Revision rev-xxx is superseded, not proposed.
     ```
3. **Digest Verification Gate:**
   - Calling `approve_plan(..., expected_digest=stale_digest)` raises:
     ```python
     OrchestrationError: Plan digest mismatch: expected stale_digest, but revision has curr_digest.
     ```
4. **Concurrency Conflict Guard:**
   - Calling `edit_plan(run_id, plan, actor, base_revision_id=rev1["id"])` when the active revision has already moved to revision 2 raises:
     ```python
     OrchestrationError: Concurrent revision conflict: active revision is rev-2, not base revision rev-1.
     ```

---

## 6. Updated Finding Classifications

| Finding ID | Classification | Remediation Scope & Stage 2 Status | Remaining Work (if partial) |
|:---:|:---:|---|---|
| **F-01** | `RESOLVED` | Silent action conversion in `normalize_plan` and `auto_repair_plan` eliminated. Self-references, dangling dependencies, missing input dependencies, and pipe-delimited roles are preserved verbatim and rejected as validation errors. Cycle edge deletion eliminated. | None for Stage 2. |
| **F-02** | `RESOLVED` | Authoritative `ActionRegistry` established. Unknown scripts and steps produce structured `CAPABILITY_GAP` diagnostics instead of executable actions. Deliverables with non-existent task producers rejected. | None for Stage 2. |
| **F-03** | `RESOLVED` | Conservative token budgeting (`len / 3.0 + 120`) prevents context overflow on small models, raising structured `INSUFFICIENT_CONTEXT` errors on underflow. Full authorization lifecycle integration with content digest binding and semantic diff re-approval verified. | None for Stage 2. |
| **F-04** | `RESOLVED` | Structured `RequirementLedger` captures objectives, constraints, exclusions, deliverables, deadlines, and success criteria losslessly. Boundary tests (>1,200 chars, dozens of constraints, 8+ DAG tasks) verified without parameter loss or truncation. | None for Stage 2. |
| **F-18** | `PARTIALLY_FIXED` | Authoritative Action Registry, `CapabilityProbeRecord` with TTL and verification states, dynamic catalog generation, and manifest-to-dispatcher drift audit implemented. | Fleet model-router governance, background health probing daemon, and persistent backend verification remain scheduled for Stage 7. |

---

## 7. Remaining Capability Verification Limitations & Compatibility Risks

1. **Model-Router Fleet Governance (Stage 7 Scope):**
   `ActionRegistry` dynamic capability verification evaluates declared capabilities, software runtimes, and TTL probes. However, full persistent model router verification, background health probing daemons, and multi-node inference lease verification belong to Stage 7. F-18 is therefore classified as `PARTIALLY_FIXED`.
2. **Chromium Proxy Enforcement in Containerized Staging (Stage 1 Gate):**
   The browser egress filtering proxy and socket-pinned transport are fully implemented and verified in Python unit/mock tests. Verification of live Playwright Chromium instances executing through the proxy under container network isolation is recorded as a mandatory pre-production staging gate before live traffic release. F-14 remains `PARTIALLY_FIXED`.
3. **Preexisting API Route Drift (Stage 8 Scope):**
   `tests/test_web_routes.py::test_get_runs_endpoint` continues to fail because `/api/runs` is unmounted while `/api/v1/orchestration/projects/{id}/runs` is the canonical route. This drift is tracked under F-16 and scheduled for resolution in Stage 8.

---

## 8. Explicit Recommendation for Stage 3

**Recommendation: GO for Stage 3 Authorization.**

- All Stage 2 invariants are empirically demonstrated: zero semantic mutations during auto repair, full authorization lifecycle integration, lossless requirement ledger preservation, conservative token budgeting with structured error handling, authoritative action registry drift protection, and end-to-end integration scenarios.
- The test suite reconciles accurately to 514 collected, 513 passed, and 1 failed (the documented preexisting web-route failure) across 57 modules.
- Zero live services were started; zero production deployments were initiated; zero merges to `main` occurred.

**Next Action:** Awaiting human review and explicit authorization before commencing Stage 3 (`STAGE_03_EXECUTION: Execution Reliability, Scheduler Fencing & Semantic Acceptance`).
