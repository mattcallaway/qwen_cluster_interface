# Stage 2 Final Evidence & Semantic Safety Gate Report

**Document Identifier:** `STAGE_02_FINAL_EVIDENCE_GATE.md`  
**Evaluation Date:** 2026-10-19  
**Active Git Branch:** `feature/unified-artifact-deliverables`  
**Baseline Git Commit (Base SHA):** `c98510b9a1569a113c762f083f5bba4395704531`  
**Working Tree Status:** **UNCOMMITTED CHANGES** (20 tracked modified files, 6 untracked files/directories)  
**Exact Working Tree Patch:** Saved in [`docs/remediation/evidence/working_tree_patch.diff`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/working_tree_patch.diff)  
**Execution Environment:** Isolated test environment (SQLite memory/temp DBs, no live background workers, no live production daemons, zero external egress)  
**Master Test Suite Status:** **524 collected, 523 passed, 1 failed, 0 skipped, 0 errors across 57 modules (Exit code: 1)**  
**Single Failing Test Identified:** `tests/test_web_routes.py::test_knowledge_page` (Preexisting Baseline Failure 2; UI template string mismatch tracked for Stage 8)  
**Regressions Introduced:** **0**  

---

## 1. Executive Summary & Verification Objectives

This document provides definitive, reproducible evidence resolving all concerns raised in the Stage 2 Evidence Integrity and Semantic Safety Gate review:

1. **Reproducible Test Evidence & Reconciled Matrix (Section A):**
   - Eliminated all manual transcript reconstruction. All test results are programmatically captured directly from executable pytest runs.
   - Collected: **524 items**, Passed: **523 items**, Failed: **1 item**, Skipped: **0 items**, Errors: **0 items** across **57 test modules**.
   - Verified arithmetic identity: $\text{collected} = \text{passed} + \text{failed} + \text{skipped} + \text{xfailed} + \text{errors}$ ($524 = 523 + 1 + 0 + 0 + 0$).
   - Confirmed single failing test is `tests/test_web_routes.py::test_knowledge_page` (line 36: `assert "Persistent Knowledge Repository" in response.text`), exactly matching Preexisting Failure 2 established during Stage 0 baseline.
   - Raw pytest stdout, stderr, collection lists, JUnit XML, execution metadata, and Git status are archived under [`docs/remediation/evidence/`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/).

2. **Removal of Remaining Silent Deliverable Substitutions (Section B):**
   - Removed `DELIVERABLE_TYPE_ALIASES.get(dt, "document")`. Unknown deliverable types are never coerced to `"document"`; they are preserved verbatim and rejected by `validate_plan()` with structured code `UNSUPPORTED_DELIVERABLE_TYPE`.
   - Removed generic acceptance criteria injection (`"Validated output meets project requirements."`) from `auto_repair_plan()`. Empty criteria are preserved and rejected with `MISSING_ACCEPTANCE_CRITERIA`.
   - Removed arbitrary marking of the last deliverable as `terminal = True`. Plans missing terminal deliverables fail with `MISSING_TERMINAL_DELIVERABLE`. Intermediate task deliverables falsely marked terminal fail with `INCORRECT_TERMINAL_DELIVERABLE`.
   - Added negative tests covering unknown deliverable types, missing acceptance criteria, invalid terminal deliverables, and valid explicit alias normalizations.

3. **Verification of Normalization Boundaries (Section C):**
   - Inspected and hardened `_s()`, `_list_s()`, and `normalize_plan()` in [`task_graph.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/task_graph.py).
   - Task identifiers, dependency IDs, action inputs, and agent roles are no longer truncated to 40 characters or sliced to 20 entries during normalization or auto-repair.
   - When schema limits are exceeded (>40 characters for identifiers, >20 items for lists), `validate_plan()` explicitly rejects the plan, embedding the exact offending string and length in diagnostics.

4. **Cryptographic Human Approval & Content Binding (Section D):**
   - Re-verified human approval binding: every approval is bound to an immutable SHA-256 content digest (`plan_digest = sha256(canonical_json)`).
   - In [`approval_manager.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/approval_manager.py), `approve_plan()` verifies `expected_digest` against `curr_digest`. When digest binding is required (`require_digest=True`), omitting `expected_digest` fails closed with `OMITTED_PLAN_DIGEST`.
   - In [`orchestrator.js`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/static/js/orchestrator.js), the user interface binds the operator's approval submission to `window.__orchRevision.plan_digest` and `revision_id` viewed on-screen.
   - Tested stale revisions, omitted digests, concurrent edits, modified requirement ledgers, and restart/replay scenarios. The scheduler refuses to execute any modified plan under a prior revision's approval.

5. **Risk Classifications Preserved (Section E):**
   - **F-14** is maintained as `PARTIALLY_FIXED` (local egress proxy & socket pinning implemented; awaiting multi-machine browser sandbox deployment).
   - **F-18** is maintained as `PARTIALLY_FIXED` (authoritative action registry & capability probes implemented; fleet model-router governance remains for Stage 7).
   - **F-01** and **F-04** are verified with negative tests enforcing explicit errors for unknown deliverables, missing criteria, and boundary violations.

---

## 2. Git Environment & Source State Verification

As required by Section A, the exact Git state is recorded below:

### 2.1 Commit & Working Tree Status
- **Baseline Commit (HEAD):** `c98510b9a1569a113c762f083f5bba4395704531`
- **Working Tree State:** **UNCOMMITTED CHANGES**
- **Branch:** `feature/unified-artifact-deliverables`

```text
$ git rev-parse HEAD
c98510b9a1569a113c762f083f5bba4395704531

$ git status --porcelain
 M app/routes/orchestration.py
 M app/routes/worker.py
 M app/services/orchestration/approval_manager.py
 M app/services/orchestration/planner.py
 M app/services/orchestration/task_graph.py
 M app/services/research/browser.py
 M app/services/research/extraction.py
 M app/services/research/policy.py
 M app/services/research/providers/api_provider.py
 M app/services/research/providers/duckduckgo.py
 M app/services/research/providers/searxng.py
 M app/services/research/retrieval.py
 M app/services/worker.py
 M app/static/js/orchestrator.js
 M tests/conftest.py
 M tests/test_live_web_research.py
 M tests/test_metric_integrity.py
 M tests/test_orchestrator_project_isolation.py
 M tests/test_worker_api.py
 M vps_agent/agent.py
?? app/services/orchestration/action_registry.py
?? app/services/orchestration/intake.py
?? app/services/research/browser_proxy.py
?? docs/
?? run_evidence_suite.py
?? tests/test_stage01_security.py
?? tests/test_stage02_planning.py
```

### 2.2 Diff Stat Across Modified Files
```text
$ git diff --stat
 app/routes/orchestration.py                     |  13 +-
 app/routes/worker.py                            |  82 +++--
 app/services/orchestration/approval_manager.py  |  22 +-
 app/services/orchestration/planner.py           | 223 +++++++++++--
 app/services/orchestration/task_graph.py        | 272 ++++++----------
 app/services/research/browser.py                |  61 +++-
 app/services/research/extraction.py             |  50 ++-
 app/services/research/policy.py                 |  32 +-
 app/services/research/providers/api_provider.py |   4 +-
 app/services/research/providers/duckduckgo.py   | 105 +++++--
 app/services/research/providers/searxng.py      |   4 +-
 app/services/research/retrieval.py              | 133 +++++++-
 app/services/worker.py                          | 402 ++++++++++++++++++++----
 app/static/js/orchestrator.js                   |   6 +-
 tests/conftest.py                               |  25 ++
 tests/test_live_web_research.py                 |   9 +-
 tests/test_metric_integrity.py                  |  44 +--
 tests/test_orchestrator_project_isolation.py    |  23 +-
 tests/test_worker_api.py                        |   8 +-
 vps_agent/agent.py                              |  12 +-
 20 files changed, 1172 insertions(+), 358 deletions(-)
```

The exact unified diff patch is captured in [`docs/remediation/evidence/working_tree_patch.diff`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/working_tree_patch.diff).

---

## 3. Programmatic Test Execution & Reconciled Matrix

The master test suite was executed via the automated runner [`run_evidence_suite.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/run_evidence_suite.py), invoking `pytest -v --junitxml=docs/remediation/evidence/junit_report.xml`.

### 3.1 Execution Metadata
- **Command:** `C:\Users\Matt C\AppData\Local\Programs\Python\Python312\python.exe -m pytest -v --junitxml=C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective\docs\remediation\evidence\junit_report.xml`
- **Exit Code:** `1` (due to single documented preexisting baseline failure)
- **Duration:** `169.2 seconds (0:02:49)`
- **Collected:** `524`
- **Passed:** `523`
- **Failed:** `1`
- **Skipped:** `0`
- **Errors:** `0`
- **Arithmetic Check:** $524 \equiv 523 + 1 + 0 + 0$ (**VERIFIED TRUE**)

### 3.2 Programmatically Generated Test Module Matrix (57 Modules)

| # | Test Module Path | Collected | Passed | Failed | Skipped | Status |
|:---:|---|:---:|:---:|:---:|:---:|:---:|
| 1 | `tests/test_audit_repairs.py` | 17 | 17 | 0 | 0 | PASS |
| 2 | `tests/test_audit_security.py` | 19 | 19 | 0 | 0 | PASS |
| 3 | `tests/test_auth.py` | 2 | 2 | 0 | 0 | PASS |
| 4 | `tests/test_auth_ratelimit.py` | 5 | 5 | 0 | 0 | PASS |
| 5 | `tests/test_chat_isolation.py` | 4 | 4 | 0 | 0 | PASS |
| 6 | `tests/test_chat_project_integration.py` | 3 | 3 | 0 | 0 | PASS |
| 7 | `tests/test_continuous_research_knowledge.py` | 8 | 8 | 0 | 0 | PASS |
| 8 | `tests/test_deliverable_artifact_resolution.py` | 2 | 2 | 0 | 0 | PASS |
| 9 | `tests/test_distributed_artifact_lifecycle.py` | 1 | 1 | 0 | 0 | PASS |
| 10 | `tests/test_end_to_end_repair.py` | 1 | 1 | 0 | 0 | PASS |
| 11 | `tests/test_inference.py` | 3 | 3 | 0 | 0 | PASS |
| 12 | `tests/test_infrastructure.py` | 7 | 7 | 0 | 0 | PASS |
| 13 | `tests/test_infrastructure_routes.py` | 9 | 9 | 0 | 0 | PASS |
| 14 | `tests/test_knowledge.py` | 4 | 4 | 0 | 0 | PASS |
| 15 | `tests/test_knowledge_meta.py` | 19 | 19 | 0 | 0 | PASS |
| 16 | `tests/test_live_multistage_research_integration.py` | 1 | 1 | 0 | 0 | PASS |
| 17 | `tests/test_live_web_research.py` | 1 | 1 | 0 | 0 | PASS |
| 18 | `tests/test_machines.py` | 5 | 5 | 0 | 0 | PASS |
| 19 | `tests/test_markdown_safety.py` | 3 | 3 | 0 | 0 | PASS |
| 20 | `tests/test_metric_integrity.py` | 11 | 11 | 0 | 0 | PASS |
| 21 | `tests/test_migration_compatibility.py` | 3 | 3 | 0 | 0 | PASS |
| 22 | `tests/test_migrations.py` | 8 | 8 | 0 | 0 | PASS |
| 23 | `tests/test_milestone2.py` | 8 | 8 | 0 | 0 | PASS |
| 24 | `tests/test_milestone2_routes.py` | 6 | 6 | 0 | 0 | PASS |
| 25 | `tests/test_milestone3.py` | 9 | 9 | 0 | 0 | PASS |
| 26 | `tests/test_milestone3_routes.py` | 6 | 6 | 0 | 0 | PASS |
| 27 | `tests/test_milestone4.py` | 6 | 6 | 0 | 0 | PASS |
| 28 | `tests/test_milestone4_routes.py` | 7 | 7 | 0 | 0 | PASS |
| 29 | `tests/test_milestone5.py` | 5 | 5 | 0 | 0 | PASS |
| 30 | `tests/test_milestone5_routes.py` | 5 | 5 | 0 | 0 | PASS |
| 31 | `tests/test_milestone_2_acceptance.py` | 1 | 1 | 0 | 0 | PASS |
| 32 | `tests/test_model_discovery.py` | 4 | 4 | 0 | 0 | PASS |
| 33 | `tests/test_observatory.py` | 9 | 9 | 0 | 0 | PASS |
| 34 | `tests/test_orchestrator_core.py` | 1 | 1 | 0 | 0 | PASS |
| 35 | `tests/test_orchestrator_project_isolation.py` | 8 | 8 | 0 | 0 | PASS |
| 36 | `tests/test_orchestrator_routes.py` | 7 | 7 | 0 | 0 | PASS |
| 37 | `tests/test_orchestrator_units.py` | 67 | 67 | 0 | 0 | PASS |
| 38 | `tests/test_probe_parser.py` | 9 | 9 | 0 | 0 | PASS |
| 39 | `tests/test_projects_m1.py` | 30 | 30 | 0 | 0 | PASS |
| 40 | `tests/test_projects_routes.py` | 19 | 19 | 0 | 0 | PASS |
| 41 | `tests/test_research_engine.py` | 7 | 7 | 0 | 0 | PASS |
| 42 | `tests/test_research_security.py` | 6 | 6 | 0 | 0 | PASS |
| 43 | `tests/test_ssrf_advanced.py` | 9 | 9 | 0 | 0 | PASS |
| 44 | `tests/test_stage01_security.py` | 26 | 26 | 0 | 0 | PASS |
| 45 | `tests/test_stage02_planning.py` | 36 | 36 | 0 | 0 | PASS |
| 46 | `tests/test_tasks.py` | 4 | 4 | 0 | 0 | PASS |
| 47 | `tests/test_ui_states.py` | 3 | 3 | 0 | 0 | PASS |
| 48 | `tests/test_unified_artifact_deliverables.py` | 9 | 9 | 0 | 0 | PASS |
| 49 | `tests/test_vps.py` | 1 | 1 | 0 | 0 | PASS |
| 50 | `tests/test_vps_agent.py` | 23 | 23 | 0 | 0 | PASS |
| 51 | `tests/test_vps_daemon.py` | 16 | 16 | 0 | 0 | PASS |
| 52 | `tests/test_vps_security.py` | 6 | 6 | 0 | 0 | PASS |
| 53 | `tests/test_vps_worker.py` | 5 | 5 | 0 | 0 | PASS |
| 54 | `tests/test_web_routes.py` | 7 | 6 | 1 | 0 | FAIL |
| 55 | `tests/test_worker_api.py` | 10 | 10 | 0 | 0 | PASS |
| 56 | `tests/test_worker_reliability.py` | 7 | 7 | 0 | 0 | PASS |
| 57 | `tests/test_worker_service.py` | 6 | 6 | 0 | 0 | PASS |
| **TOTAL** | **57 Modules** | **524** | **523** | **1** | **0** | **1 FAIL** |

### 3.3 Root Cause Analysis of the Single Failure

The single failure in `tests/test_web_routes.py::test_knowledge_page` was evaluated against the Stage 0 baseline:

```text
tests\test_web_routes.py:36: in test_knowledge_page
    assert "Persistent Knowledge Repository" in response.text
E   assert 'Persistent Knowledge Repository' in '<!DOCTYPE html>\n...<title>Knowledge Base - Qwen Collective</title>...Collective Knowledge & Evidence Repository...'
```

- **Reconciliation with Stage 0 Baseline:** This failure is verbatim **Preexisting Failure 2** recorded in `docs/remediation/BASELINE.md` (line 42). The HTML template `app/templates/knowledge/index.html` displays `"Collective Knowledge & Evidence Repository"`, whereas the test expects the older legacy copy `"Persistent Knowledge Repository"`.
- **Classification:** Preexisting baseline template drift scheduled for remediation under Stage 8 (Operations, API, UI).
- **Regression Count:** **0**. Zero failures were introduced by Stage 1 or Stage 2 changes.

---

## 4. Code-Level Implementation Evidence

### 4.1 Elimination of Deliverable Mutation & Schema Enforcement ([`task_graph.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/task_graph.py))

```python
# app/services/orchestration/task_graph.py:270-285
def auto_repair_plan(plan: Dict[str, Any], errors: List[str], nodes: Optional[List[Dict[str, Any]]] = None) -> Tuple[Dict[str, Any], List[str]]:
    """Deterministically repair provably safe syntactic defects in a candidate plan.

    CRITICAL STAGE 2 SAFETY INVARIANTS:
    - Never silently convert unknown library scripts or controller steps to 'inference' or 'assemble_report'.
    - Never delete edges or self-references to resolve dependency cycles or invalid dependencies.
    - Never delete dependencies on missing tasks or silently inject inputs into depends_on.
    - Never select one role from a pipe-delimited role expression or mutate execution roles.
    - Never silently convert unknown deliverable types to 'document' (unknown types must produce UNSUPPORTED_DELIVERABLE_TYPE).
    - Never insert generic acceptance criteria such as 'Validated output meets project requirements.' (missing criteria must produce MISSING_ACCEPTANCE_CRITERIA).
    - Never arbitrarily mark the last deliverable terminal (terminal status must follow validated requirements or fail with MISSING_TERMINAL_DELIVERABLE).
    """
    import copy
    repaired = copy.deepcopy(plan) if isinstance(plan, dict) else {}

    # Deliverables explicit alias normalization ONLY
    dels = repaired.get("deliverables", [])
    if isinstance(dels, list):
        for d in dels:
            if not isinstance(d, dict):
                continue
            dt = str(d.get("type") or "").strip().lower()
            if dt in DELIVERABLE_TYPE_ALIASES:
                d["type"] = DELIVERABLE_TYPE_ALIASES[dt]
            # Unknown types preserved verbatim so validate_plan catches UNSUPPORTED_DELIVERABLE_TYPE
            # Empty acceptance_criteria is NOT populated with generic strings
            # Terminal status is NOT arbitrarily forced
```

### 4.2 Explicit Schema Validation on Boundary Limits ([`task_graph.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/task_graph.py))

```python
# app/services/orchestration/task_graph.py:348-360
    for t in tasks:
        k = t["key"]
        if len(k) > 40 or not KEY_RE.match(k):
            errors.append(f"Invalid task key '{k}' (violates schema limits: exceeds 40 characters or invalid format; length={len(k)}).")
            continue
        keys.add(k)
        by_key[k] = t
        deps[k] = list(t["depends_on"])
        if len(t["depends_on"]) > 20:
            errors.append(f"Task '{k}' has {len(t['depends_on'])} dependencies, exceeding maximum limit of 20 (SCHEMA_LIMIT_EXCEEDED).")
        for dep in t["depends_on"]:
            if len(dep) > 40 or not KEY_RE.match(dep):
                errors.append(f"Task '{k}' dependency identifier '{dep}' violates schema limits (exceeds 40 characters or invalid format; length={len(dep)}).")
```

### 4.3 Human Approval Content Binding & Digest Enforcement ([`approval_manager.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/approval_manager.py))

```python
# app/services/orchestration/approval_manager.py:86-107
def approve_plan(run_id: str, actor: str, rationale: str = "", revision_id: Optional[str] = None,
                 expected_digest: Optional[str] = None, require_digest: bool = False) -> Dict[str, Any]:
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
    if require_digest and not expected_digest:
        raise OrchestrationError("Approval rejected: expected_digest is required to verify human approval against displayed plan content (OMITTED_PLAN_DIGEST).")
    if expected_digest and curr_digest != expected_digest:
        raise OrchestrationError(f"Plan digest mismatch: expected {expected_digest}, but revision has {curr_digest}.")
```

### 4.4 Dashboard Operator Binding ([`orchestrator.js`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/static/js/orchestrator.js))

```javascript
// app/static/js/orchestrator.js:100-107
    if (s === "AWAITING_PLAN_APPROVAL") {
      box.appendChild(ctl("Approve Plan", "btn-primary", function (b) {
        var why = prompt("Approval note (optional):", "") ;
        if (why === null) return;
        var rev = window.__orchRevision;
        var revId = rev ? rev.id : null;
        var digest = rev ? rev.plan_digest : null;
        act("Approving", b, function () { return api("POST", "/approve", { rationale: why, revision_id: revId, expected_digest: digest }); }, "Plan approved. Review it, then Start Execution.");
      }, "approve"));
```

---

## 5. Dedicated Stage 2 Test Suite Verification (36/36 Passed)

The dedicated Stage 2 test module [`tests/test_stage02_planning.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage02_planning.py) contains 36 unit and integration tests covering all requirements:

| Test Identifier | Category | Verifies | Result |
|---|---|---|:---:|
| `test_unknown_script_rejected_explicitly_without_substitution` | F-01 | Rejection of unknown library script without fallback | **PASS** |
| `test_unknown_controller_step_rejected_explicitly_without_substitution` | F-01 | Rejection of unknown step without conversion | **PASS** |
| `test_disallowed_action_type_rejected_explicitly` | F-01 | Rejection of non-allowlisted action types | **PASS** |
| `test_self_dependency_in_depends_on_rejected_without_silent_stripping` | F-01 | Self-dependencies in `depends_on` preserved and rejected | **PASS** |
| `test_self_dependency_in_inputs_rejected_without_silent_stripping` | F-01 | Self-dependencies in `inputs` preserved and rejected | **PASS** |
| `test_dangling_dependency_on_missing_task_rejected_without_silent_stripping`| F-01 | Missing prerequisites preserved and rejected | **PASS** |
| `test_missing_task_inputs_without_declared_dependency_rejected` | F-01 | Undeclared input dependencies not auto-injected | **PASS** |
| `test_role_ambiguity_pipe_delimited_rejected_without_silent_selection` | F-01 | Pipe roles (`execution\|analysis`) preserved and rejected | **PASS** |
| `test_unknown_deliverable_type_rejected_as_unsupported` | F-01/B | Unknown deliverable types produce `UNSUPPORTED_DELIVERABLE_TYPE` | **PASS** |
| `test_missing_acceptance_criteria_rejected` | F-04/B | Missing acceptance criteria produce `MISSING_ACCEPTANCE_CRITERIA` | **PASS** |
| `test_incorrect_terminal_deliverables_rejected` | F-01/B | Missing terminal and intermediate terminal deliverables rejected | **PASS** |
| `test_valid_explicit_deliverable_aliases_normalized` | B | Explicit aliases (`doc`, `code`) normalize cleanly | **PASS** |
| `test_task_identifier_longer_than_40_chars_preserved_and_rejected` | C | Keys >40 chars not truncated; error records value and length | **PASS** |
| `test_dependency_identifier_longer_than_40_chars_preserved_and_rejected` | C | Dependency IDs >40 chars preserved and rejected | **PASS** |
| `test_dependency_list_exceeding_20_entries_preserved_and_rejected` | C | Dependencies >20 not sliced; fails `SCHEMA_LIMIT_EXCEEDED` | **PASS** |
| `test_action_inputs_exceeding_boundaries_preserved_and_rejected` | C | Inputs >40 chars or >20 items preserved and rejected | **PASS** |
| `test_agent_role_exceeding_boundaries_preserved_and_rejected` | C | Roles >40 chars preserved and rejected with length | **PASS** |
| `test_omitted_digest_rejected_when_digest_required` | F-03/D | Omitting `expected_digest` fails with `OMITTED_PLAN_DIGEST` | **PASS** |
| `test_digest_mismatch_rejected` | F-03/D | Mismatched plan digest fails closed | **PASS** |
| `test_rejected_graphs_never_scheduled_and_edges_preserved_for_debugging` | F-02 | Invalid DAG retained for operator inspection | **PASS** |
| `test_human_approval_lifecycle_full_integration` | F-03 | 9-step authorization lifecycle end-to-end | **PASS** |
| `test_requirement_ledger_change_invalidates_approval_even_with_identical_tasks` | F-03/F-04 | Overview/ledger diff triggers mandatory re-approval | **PASS** |
| `test_lossless_requirements_boundary_over_1200_chars` | F-04 | Intake text >1,200 chars preserved without loss | **PASS** |
| `test_lossless_requirements_dozens_of_hard_constraints_and_exclusions` | F-04 | Dozens of constraints/exclusions preserved | **PASS** |
| `test_eight_or_more_dag_tasks_with_multiple_dependencies` | F-04 | Complex DAGs (>8 tasks) preserved losslessly | **PASS** |
| `test_token_budget_insufficient_headroom_raises_structured_error` | F-03 | Headroom exhaustion raises `PlanContextExceededError` | **PASS** |
| `test_token_budget_missing_or_stale_context_window_raises_structured_error` | F-03 | Missing model context window raises structured error | **PASS** |
| `test_adversarial_prompt_instructions_contained_in_prompt_fences` | Stage 1/2 | Untrusted prompts contained inside delimiter fences | **PASS** |
| `test_action_registry_and_dispatcher_bidirectional_drift_audit` | F-02/F-18 | Action registry, script library, controller synchronization | **PASS** |
| `test_capability_probe_concrete_evidence_verification` | F-18 | Dynamic capability probe validation with TTL | **PASS** |
| `test_stale_or_failed_capability_probe_blocks_execution` | F-18 | Expired/failed probes block node dispatch | **PASS** |
| `test_nonexistent_script_generate_natural_response_produces_capability_gap` | F-02/F-18 | Missing scripts report structured capability gap | **PASS** |
| `test_e2e_scenario_a_unknown_executable_action` | E2E | Unregistered actions fail closed before scheduler | **PASS** |
| `test_e2e_scenario_b_multi_requirement_engineering_project` | E2E | Full engineering project scope, constraints, deliverables preserved | **PASS** |
| `test_extract_json_object_handles_prose_and_fences` | F-01 | JSON extractor handles Markdown fences and preamble | **PASS** |
| `test_extract_json_object_require_plan_keys` | F-01 | JSON extractor rejects arbitrary objects lacking schema | **PASS** |

---

## 6. Analysis of Approval Digest Omission (Section D Verification)

### Finding: Does omitting `expected_digest` allow approval of an altered revision?
**Answer: YES, if not strictly enforced.**

1. **Vulnerability Analysis:**
   If a client submits an approval specifying only a project or run ID without `expected_digest`, a time-of-check to time-of-use (TOCTOU) race condition exists:
   - Operator views Revision 1 (Digest $D_1$) in their browser.
   - A concurrent background replan or edit occurs, creating Revision 2 (Digest $D_2$).
   - If the operator's submission omits `expected_digest`, the server would target `run["current_revision_id"]` (Revision 2) and approve it, even though the human operator only inspected Revision 1.
2. **Hardening Implemented:**
   - In [`approval_manager.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/approval_manager.py), `approve_plan(..., expected_digest=None, require_digest=True)` raises `OrchestrationError("Approval rejected: expected_digest is required to verify human approval against displayed plan content (OMITTED_PLAN_DIGEST).")`.
   - In [`orchestrator.js`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/static/js/orchestrator.js), the dashboard captures the active revision ID and cryptographic content digest `window.__orchRevision.plan_digest` at the moment the plan is rendered on screen and transmits them in the `/approve` POST body.
   - If the revision on the server has changed in the interim, `approve_plan()` detects the digest mismatch and aborts execution.

---

## 7. Outstanding Risk Classifications (Section E)

| Finding | Title | Remediation Scope | Current Status | Justification |
|:---:|---|---|:---:|---|
| **F-01** | Silent Action Substitution & Graph Mutation | Stage 2 | **FIXED** | Verified with 12 negative tests rejecting invalid actions, self-dependencies, dangling dependencies, undeclared inputs, ambiguous roles, unknown deliverable types (`UNSUPPORTED_DELIVERABLE_TYPE`), missing criteria (`MISSING_ACCEPTANCE_CRITERIA`), and intermediate terminal deliverables (`INCORRECT_TERMINAL_DELIVERABLE`). |
| **F-02** | Invented Executable Actions | Stage 2 | **FIXED** | Authoritative `GLOBAL_ACTION_REGISTRY` synchronized bidirectionally with `script_library.LIBRARY` and `task_graph.CONTROLLER_STEPS`. Nonexistent scripts emit structured `CAPABILITY_GAP` diagnostics. |
| **F-03** | Approval Re-use on Plan Mutation | Stage 2 | **FIXED** | 9-step authorization lifecycle verified: revisions $1 \dots N$, automatic approval revocation, SHA-256 digest binding, scheduler refusal, replay rejection, and concurrent edit conflict rejection. |
| **F-04** | Prompt Truncation & Constraint Loss | Stage 2 | **FIXED** | Verified with boundary tests for objectives $>1,200$ chars, dozens of constraints/exclusions, and $8+$ tasks. Conservative token budgeting with fail-closed `INSUFFICIENT_CONTEXT` errors. |
| **F-10** | Worker Authorization & Fencing | Stage 1 | **FIXED** | Worker authentication, task lease fencing generations, exclusive artifact creation (`open(..., 'xb')`, 25MB limit), and CAS upload validation verified. |
| **F-14** | Research Web Proxy & SSRF Boundaries | Stage 1 / Stage 4 | **PARTIALLY_FIXED** | Local proxy egress filtering, DNS rebinding socket pinning, and RFC1918 blocking verified. Production container isolation gate pending. |
| **F-15** | Admin CSRF Protection | Stage 1 | **FIXED** | CSRF tokens enforced on all administrative mutating POST endpoints; cross-session and missing tokens rejected. |
| **F-18** | Action Registry & Fleet Model Governance | Stage 2 / Stage 7 | **PARTIALLY_FIXED** | Action registry and capability probe verification with TTL completed for Stage 2. Fleet model-router governance scheduled for Stage 7. |

---

## 8. Stage 3 GO/NO-GO Recommendation

### Recommendation: **GO FOR STAGE 3 AUTHORIZATION**

**Justification:**
1. **Evidence Integrity:** All test evidence has been captured directly from automated execution without manual reconstruction. Module counts reconcile arithmetically ($524 = 523 + 1 + 0 + 0$).
2. **Zero Semantic Mutations:** Deliverable types, acceptance criteria, and terminal flags are no longer subject to default fallbacks or arbitrary assignment.
3. **Boundary Safety:** Schema limits on identifiers and dependency lists are enforced with structured diagnostics preserving offending inputs.
4. **Approval Immutability:** Human approval is cryptographically bound to plan content digests, preventing altered plans from executing under stale approvals.
5. **Security Isolation:** All development remains strictly confined to `feature/unified-artifact-deliverables` in temporary test environments. Zero merges to `main` have occurred, zero live services have been touched, and all Stage 1 security boundaries remain intact.

**Awaiting Explicit Human Authorization:**
Per project instructions, Stage 3 implementation will NOT begin until the user reviews this report and issues the explicit command to proceed.
