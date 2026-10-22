# Stage 2 Remediation Report: Semantically Safe Planning, Verified Capabilities & Prompt Fidelity

**Stage Identifier:** `STAGE_02_PLANNING`  
**Execution Date:** 2026-10-18  
**Author:** Antigravity Autonomous Remediation Agent  
**Active Git Branch:** `feature/unified-artifact-deliverables`  
**Baseline Git Commit:** `c98510b9a1569a113c762f083f5bba4395704531`  
**Stage Status:** `READY_FOR_REVIEW`  
**Target Invariants:** Invariant I-01 (Plan Fidelity), Invariant I-02 (Feasibility Before Approval), Invariant I-03 (Plan-Version Immutability), Invariant I-13 (Approvals Persist Through Failure), Invariant I-14 (Explicit Degraded States)  
**Covered Findings:** F-01 (Silent Action Conversion), F-02 (Capability Hallucination), F-03 (Token Truncation & Revision Diffing), F-04 (Objective & Constraint Truncation), F-18 (Untrusted Capability Discovery)

---

## 1. Executive Summary & Verification of Pre-Conditions

Pursuant to the **Conditional Continue — Stage 2 Planning Remediation** authorization, all pre-flight documentation corrections and structural verifications were completed prior to enacting planner code changes. Stage 2 development was executed strictly within the isolated local environment with zero live service restarts, zero database migrations to `~/.qwen-collective`, and zero deployments to production.

### Reconciliation of Pre-Stage 2 Conditions

1. **Regression Totals Reconciled:**
   - The authoritative raw pytest totals from the Stage 1 review run across 56 test modules are: **488 collected, 486 passed, 2 failed, 9 warnings**.
   - Discrepancies in previous documentation reporting 485 passed have been corrected across `STATUS.md`, `TEST_MATRIX.md`, and `STAGE_01_SECURITY_REVIEW_CORRECTION.md`. The 2 baseline failures were `test_orchestrator_units.py::test_extract_json_object_handles_prose_and_fences` (now resolved in Stage 2) and `test_web_routes.py::test_web_root_serves_single_page_app`.
2. **Preservation of F-14 as `PARTIALLY_FIXED`:**
   - F-14 remains classified as `PARTIALLY_FIXED` in all tracking matrices.
   - The loopback-bound forward proxy (`OutboundFilteringProxy`) is implemented and verified at the network socket layer. A mandatory production-staging test gate is documented in `OPEN_ISSUES.md` (Issue 5), requiring end-to-end verification of Chromium/Playwright process-level sandbox flags and network namespace isolation before F-14 can be certified as fully closed.
   - Playwright browser research strictly fails closed (503 Service Unavailable / `BrowserRenderResult(error=...)`) with zero unproxied outbound socket connections in any environment lacking secure proxy availability.
3. **Clarification of WebSocket Enforcement Boundaries:**
   - **Plaintext HTTP:** The proxy inspects the unencrypted HTTP request headers. Any request with `Upgrade: websocket` or `Connection: upgrade` is immediately rejected with HTTP 403 Forbidden.
   - **Encrypted HTTPS CONNECT Tunnels:** The proxy enforces destination boundary controls at the TCP connection/socket level. The destination hostname is resolved per-connection and pinned to its validated public IP. Connections to RFC1918, loopback, link-local, or cloud metadata IPs are rejected with HTTP 403 before any TCP handshake or TLS negotiation occurs. The proxy does **not** perform TLS interception/decryption (MITM) on CONNECT tunnels; therefore, application payload inspection (including encrypted WebSocket frames) is not claimed or attempted.
4. **Stage 0 Test-Isolation Verification & Pollution Inventory:**
   - The `tests/conftest.py` pre-suite guard was verified against runtime SQLite and file paths.
   - A read-only inventory of test pollution from prior faulty test harness runs was generated: 75 test project directories under repo `data/research_sources/` and 2 test project rows in `~/.qwen-collective/data/qwen_collective.db`. Zero records or files were deleted, respecting non-destructive quarantine rules.
5. **Follow-Up Request-Body Limits Requirement:**
   - Documented in `OPEN_ISSUES.md` (Issue 6): Requirement for ASGI streaming request body length checks prior to full JSON/base64 payload memory allocation.
6. **Git Baseline & Changed File Catalog:**
   - Baseline commit: `c98510b9a1569a113c762f083f5bba4395704531`.
   - Complete inventory of Stage 1 and Stage 2 source and test artifacts recorded below.

---

## 2. Stage 2 Remediation Workstreams & Architecture

Stage 2 resolves the fundamental planning vulnerabilities that allowed LLMs to hallucinate actions, silently mutate tasks, drop constraints, and truncate outputs.

```
+-----------------------------------------------------------------------------------------+
|                                    STAGE 2 ARCHITECTURE                                 |
+-----------------------------------------------------------------------------------------+
|                                                                                         |
|  [ User Intake ] ---> [ Lossless RequirementLedger ] ---> [ Dynamic Planning Prompt ]   |
|                            (app/services/                      - Preserves Objectives   |
|                         orchestration/intake.py)               - Strict Constraints     |
|                                                                - Scope Exclusions       |
|                                                                - Terminal Deliverables  |
|                                                                - Authoritative Tools    |
|                                                                                         |
|  [ Cluster Fleet ] -> [ Verified ActionRegistry ]   -----> [ calculate_token_budget() ] |
|  - Dynamic Probes         (app/services/                       (Dynamic headroom based  |
|  - TTL Expiration      orchestration/action_registry.py)        on model context window)|
|                                                                                         |
|                                         |                                               |
|                                         v                                               |
|                           [ Small Qwen Model Execution ]                                |
|                                         |                                               |
|                                         v                                               |
|                           [ extract_json_object() ]                                     |
|                                 (Fences / Prose / Schema)                               |
|                                         |                                               |
|                                         v                                               |
|                           [ auto_repair_plan() ]                                        |
|                                (task_graph.py)                                          |
|                                                                                         |
|      +-------------------------------------------------------------------------+        |
|      | STRICT ANTI-SUBSTITUTION INVARIANTS:                                    |        |
|      | 1. Unknown scripts are NOT converted to inference -> CAPABILITY_GAP     |        |
|      | 2. Unknown controller steps are NOT converted to assemble_report        |        |
|      | 3. Dependency cycles are NOT broken by edge pruning -> CYCLE_DETECTED   |        |
|      | 4. Missing deliverable producers are NOT reassigned -> REJECTED         |        |
|      | 5. Safe repairs ONLY: Self-references stripped, deliverable aliases     |        |
|      +-------------------------------------------------------------------------+        |
|                                         |                                               |
|                                         v                                               |
|                           [ diff_plans() & store_revision() ]                           |
|                              - Detects Semantic Changes                                 |
|                              - Enforces Mandatory Human Re-Approval (I-13)              |
+-----------------------------------------------------------------------------------------+
```

### Workstream 2A: Anti-Substitution Task Graph Invariants (F-01, F-02)

**Vulnerability:** Previously, `app/services/orchestration/task_graph.py` contained permissive auto-repair logic that silently mutated unknown `library_script` actions into `inference` tasks, replaced unknown `controller_step` values with `assemble_report`, and deleted graph edges to arbitrarily "break" dependency cycles. This masked model hallucinations and caused unintended executions.

**Remediation in `app/services/orchestration/task_graph.py`:**
1. **Elimination of Keyword-Based Guessing:**
   - In `normalize_plan()`, removed heuristic inference that defaulted tasks containing "health", "collect", "report", or "assemble" in their titles to specific action types or script IDs.
2. **Strict Anti-Substitution in `auto_repair_plan()`:**
   - Removed all code blocks that substituted `library_script` with `inference`.
   - Removed code blocks that replaced unknown `step` with `assemble_report`.
   - Removed code blocks that converted unknown action types to `inference`.
   - Retained only safe syntax repairs:
     - Stripping self-references from `inputs` and `depends_on`.
     - Removing dangling references to tasks that do not exist.
     - Aligning action `inputs` with declared `depends_on`.
     - Normalizing non-canonical deliverable types via `DELIVERABLE_TYPE_ALIASES` (e.g. `response` $\rightarrow$ `document`).
     - Sanitizing pipe-delimited model roles (e.g. `analysis|execution` $\rightarrow$ `analysis`).
3. **Preservation of Cyclic Graph Edges:**
   - Completely deleted the cycle edge-pruning loop (`t["depends_on"] = [d for d in ... if task_keys.index(d) < task_keys.index(t["key"])]`).
   - Kahn's algorithm in `detect_cycle()` detects cyclic task dependencies, and `validate_plan()` explicitly returns `Dependency cycle among tasks: {keys}`, halting plan execution.
4. **Deliverable Producer Verification:**
   - In `auto_repair_plan()`, eliminated arbitrary reassignment of invalid `produced_by` references to `task_keys[-1]`. Deliverables without valid task producers are explicitly flagged and rejected.

### Workstream 2B: Authoritative Action Registry & Dynamic Capability Probing (F-02, F-18)

**Architecture:** Implemented [`app/services/orchestration/action_registry.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/action_registry.py):
1. **`ActionManifest` & `ActionKind`:**
   - Authoritative catalog of implemented actions across three strict kinds: `inference`, `controller_step`, and `library_script`.
   - Each manifest specifies required capabilities, allowed platforms, timeout budget, side-effect flags, and implementation handlers.
2. **Capability Verification Lifecycle:**
   - Disassociates simple declared node capabilities from verified capabilities.
   - States: `DECLARED`, `DISCOVERED`, `VERIFIED`, `STALE`, `FAILED`.
   - `CapabilityProbeRecord` records node ID, capability, verification timestamp, TTL (default 3600 seconds), and evidence dict.
   - `is_valid()` validates probe freshness against UTC timestamps, automatically expiring stale probes.
3. **Dynamic Prompt Tool Catalog:**
   - `build_prompt_catalog(nodes)` inspects verified cluster state and generates an authoritative markdown catalog of genuine executable actions.
   - Injected into the planner system prompt so the model is only exposed to registered tools.
4. **Structured Capability Gaps:**
   - If a requested action or required capability cannot be satisfied by registered, online, verified nodes, `create_capability_gap()` records a structured gap (`CAPABILITY_GAP`) containing the required capability, reason, and candidate nodes, preventing hallucinated executable actions.

### Workstream 2C: Lossless Structured Intake & Requirement Ledger (F-04)

**Architecture:** Implemented [`app/services/orchestration/intake.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/intake.py):
1. **`RequirementLedger` & `RequirementItem`:**
   - Structured parsing across five requirement categories: `OBJECTIVE`, `CONSTRAINT`, `DELIVERABLE`, `EXCLUSION`, and `SUCCESS_CRITERION`.
   - Supports hardness levels: `MUST` (strict) vs `SHOULD` (advisory).
2. **Lossless Preservation:**
   - Completely eliminates hard character slicing (previously `[:1200]`).
   - `format_prompt_summary()` compiles the entire requirement ledger into the model prompt under untrusted data boundaries (`<<<REQUIREMENTS ... REQUIREMENTS>>>`).
3. **Machine-Checkable Plan Coverage:**
   - `validate_plan_coverage()` verifies that candidate plans produce all requested deliverables, satisfy declared constraints, and execute zero tasks matching scope exclusions (`EXCLUSION_VIOLATED`).

### Workstream 2D: Token-Aware Planning & Robust JSON Extraction (F-03)

**Remediation in `app/services/orchestration/planner.py`:**
1. **Dynamic Token Budgeting (`calculate_planner_token_budget`):**
   - Calculates prompt length using char-to-token heuristics.
   - Computes safe token headroom under the model's actual context window (e.g. 4096 tokens for Qwen2.5 1.5B), subtracting prompt tokens and a 256-token safety margin.
   - Bounded by configurable `min_tokens` (default 512) and `max_tokens` (default 2048), preventing context overflow while ensuring sufficient output length for full DAG generation.
2. **Robust Balanced JSON Extraction (`extract_json_object`):**
   - Strips markdown code fences (` ```json ` and ` ``` `).
   - Scans string-aware balanced braces `{ ... }`, properly ignoring braces nested inside quoted strings or escape sequences.
   - Added `require_plan_keys: bool = False` flag:
     - When `False` (default): Extracts and parses any valid top-level JSON dict, resolving the preexisting baseline failure in `test_orchestrator_units.py::test_extract_json_object_handles_prose_and_fences`.
     - When `True` (used in asynchronous model planning ticks): Requires the presence of plan schema keys (`schema`, `tasks`, `paths`, `overview`) to prevent shallow intro text matches.

### Workstream 2E: Plan Revisions, Semantic Diffs & Mandatory Re-Approval (F-03, Invariant I-13)

**Remediation in `app/services/orchestration/planner.py`:**
1. **Semantic Diff Computation (`diff_plans`):**
   - Compares previous and proposed plan revisions across tasks (added, removed, modified actions/inputs/roles/dependencies) and deliverables (added, removed, modified producers/types/acceptance).
   - Sets `is_semantic_change: True` whenever structural or execution changes occur.
2. **Enforcement in `store_revision()`:**
   - Calculates semantic diff against the latest stored revision.
   - Records `semantic_diff` inside `model_info_json` and logs audit events.
3. **Mandatory Human Approval Gate:**
   - If a plan is edited or replanned with semantic differences, state transitions strictly reset run state to `AWAITING_PLAN_APPROVAL`. Previous human approval never transfers to modified plans.

---

## 3. Verification Test Evidence

### Dedicated Stage 2 Test Suite (`tests/test_stage02_planning.py`)

Thirteen positive and negative test cases were authored and verified:

```
tests/test_stage02_planning.py::test_unknown_script_rejected_explicitly_without_substitution PASSED [  7%]
tests/test_stage02_planning.py::test_unknown_controller_step_rejected_explicitly_without_substitution PASSED [ 15%]
tests/test_stage02_planning.py::test_disallowed_action_type_rejected_explicitly PASSED [ 23%]
tests/test_stage02_planning.py::test_dependency_cycle_rejected_without_edge_deletion PASSED [ 30%]
tests/test_stage02_planning.py::test_missing_deliverable_producer_rejected_not_reassigned PASSED [ 38%]
tests/test_stage02_planning.py::test_safe_structural_repairs_preserved PASSED [ 46%]
tests/test_stage02_planning.py::test_authoritative_action_registry_dynamic_probes PASSED [ 53%]
tests/test_stage02_planning.py::test_structured_capability_gaps PASSED   [ 61%]
tests/test_stage02_planning.py::test_lossless_intake_requirement_ledger PASSED [ 69%]
tests/test_stage02_planning.py::test_token_aware_resource_aware_planning PASSED [ 76%]
tests/test_stage02_planning.py::test_extract_json_object_handles_prose_and_fences PASSED [ 84%]
tests/test_stage02_planning.py::test_extract_json_object_require_plan_keys PASSED [ 92%]
tests/test_stage02_planning.py::test_plan_revisions_semantic_diff_computation PASSED [100%]

============================= 13 passed in 0.52s ==============================
```

### Preexisting Test Alignments & Full Regression Status

1. **`tests/test_orchestrator_project_isolation.py` (8/8 Passed):**
   - Updated `test_auto_repair_converts_hallucinated_scripts_and_sanitizes_dependencies` to assert anti-substitution invariants. Verified that `generate_natural_response` remains a `library_script` and is rejected with remaining validation errors rather than silently converted to inference.
2. **`tests/test_orchestrator_units.py` (67/67 Passed):**
   - Preexisting baseline failure in `test_extract_json_object_handles_prose_and_fences` was fully resolved by making `require_plan_keys` default to `False`. All 67 unit tests now pass.
3. **`tests/test_stage01_security.py` (26/26 Passed):**
   - Verified that Stage 2 changes introduce zero regressions into Stage 1 authorization, CAS, CSRF, or network proxy security boundaries.
4. **Overall Suite Totals:**
   - Baseline: 460 passed, 2 failed across 55 modules.
   - Stage 1: 486 passed, 2 failed across 56 modules.
   - Stage 2: **500 passed, 1 failed across 57 modules**.
   - Exactly one remaining known failure: `tests/test_web_routes.py::test_web_root_serves_single_page_app` (HTML dashboard template copy drift, scheduled for Stage 8).

---

## 4. Invariant Adherence & Traceability Matrix

| Invariant | Description | Stage 2 Status | Implementation Proof |
|---|---|:---:|---|
| **I-01** | Plan fidelity (zero dropped constraints or parameters) | **ENFORCED** | `RequirementLedger` captures all constraints, exclusions, deliverables, and criteria; machine-checked via `validate_plan_coverage`. |
| **I-02** | Feasibility before approval (real capability registry) | **ENFORCED** | `ActionRegistry` validates required capabilities against verified probe records with TTL; missing tools yield `CAPABILITY_GAP`. |
| **I-03** | Plan-version immutability (no silent mutations) | **ENFORCED** | Silent action conversion and cycle edge pruning eliminated in `auto_repair_plan()`; all defects reported as explicit validation errors. |
| **I-04** | Authenticated and authorized execution | **ENFORCED** | Enforced in Stage 1 (`test_stage01_security.py`). Unchanged. |
| **I-05** | At-least-once processing with effectively-once recording | PARTIAL | Stage 1 fencing active; Stage 3 asynchronous scheduler offload pending. |
| **I-06** | Restricted web research | **PARTIALLY_ENFORCED** | Egress proxy and socket pinning enforced in Stage 1; browser integration staging gate documented. |
| **I-07** | Honest evidence (claim-to-source attribution) | VIOLATED | Scheduled for Stage 4 research integrity workstream. |
| **I-08** | Honest completion (separate verification from inference) | VIOLATED | Scheduled for Stage 3 evaluation and Stage 4 research integrity. |
| **I-09** | Immutable outputs (CAS storage, zero mutable overwriting) | PARTIAL | Stage 1 atomic staging active; Stage 6 full CAS cutover pending. |
| **I-10** | Full lineage and explicit reuse | PARTIAL | Scheduled for Stage 4 through Stage 6. |
| **I-11** | Knowledge trust tiers | PARTIAL | Scheduled for Stage 7 knowledge vault governance. |
| **I-12** | Recovery consistency (DB + CAS blobs + knowledge vault) | VIOLATED | Scheduled for Stage 8 disaster recovery bundle. |
| **I-13** | Approvals persist through failure | **ENFORCED** | `diff_plans()` computes semantic differences across revisions; any semantic alteration strictly requires human re-approval. |
| **I-14** | Explicit degraded states | PARTIAL | F-01 anti-substitution active; Stage 3 scheduler starvation and Stage 5 multi-stage research pending. |

---

## 5. Changed Files Inventory

### Created Files
- [`app/services/orchestration/action_registry.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/action_registry.py): Authoritative action manifest, capability verification probes, TTL tracking, and capability gaps.
- [`app/services/orchestration/intake.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/intake.py): Lossless requirement ledger, prompt compiler, and plan coverage validator.
- [`tests/test_stage02_planning.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage02_planning.py): 13 dedicated positive and negative Stage 2 tests.
- [`docs/remediation/reports/STAGE_02_PLANNING.md`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/reports/STAGE_02_PLANNING.md): This report.

### Modified Files
- [`app/services/orchestration/task_graph.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/task_graph.py): Removed silent action conversion, cycle edge pruning, and arbitrary deliverable producer reassignment.
- [`app/services/orchestration/planner.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/planner.py): Added dynamic token budgeting, plan diffing, `require_plan_keys` JSON extraction, and lossless requirement prompt formatting.
- [`tests/test_orchestrator_project_isolation.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_orchestrator_project_isolation.py): Updated auto-repair test assertions to verify anti-substitution invariants.
- [`docs/remediation/STATUS.md`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/STATUS.md): Updated active stage, invariant matrix, and test counts.
- [`docs/remediation/FINDING_TRACEABILITY.md`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/FINDING_TRACEABILITY.md): Marked F-01, F-02, F-03, F-04, and F-18 as `RESOLVED`.
- [`docs/remediation/DECISIONS.md`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/DECISIONS.md): Updated ADR-02 status to `IMPLEMENTED_IN_STAGE_2`.
- [`docs/remediation/TEST_MATRIX.md`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/TEST_MATRIX.md): Added module 57 (`test_stage02_planning.py`), updated module 37 to 67/67 passing, updated total to 500 passing.
- [`docs/remediation/CHANGELOG.md`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/CHANGELOG.md): Documented all Stage 2 architectural changes.

---

## 6. Risk Assessment & Safe Staging Bounds

1. **Risk of Model Planning Rejection Rate:**
   - Small models (e.g. 1.5B) may initially propose unknown action names or cyclic dependencies more frequently than large cloud models.
   - *Mitigation:* The authoritative action catalog is rendered directly into the planning prompt, and `build_repair_prompt()` returns specific error diagnostics to guide the bounded repair loop. If capabilities are genuinely unavailable, the engine fails explicitly with `CAPABILITY_GAP` rather than executing hallucinated tasks.
2. **Safe Staging Bounds:**
   - Isolated development only.
   - Zero branch merges to `main`.
   - Zero deployments to production or remote VPS hosts.
   - Zero modifications to user database files in `~/.qwen-collective`.
   - All human approval gates remain active.

---

## 7. Stage 2 Completion Signoff

Stage 2 implementation and verification are complete. All 13 Stage 2 tests are passing, 500 tests pass across the full suite with 0 regressions, and the five targeted findings (F-01, F-02, F-03, F-04, F-18) are resolved.

Awaiting explicit human review and authorization (`CONTINUE STAGE 3`) before initiating Stage 3 (Execution Reliability, Scheduler Fencing, and Semantic Acceptance).
