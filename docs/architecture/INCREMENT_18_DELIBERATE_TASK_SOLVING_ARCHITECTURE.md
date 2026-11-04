# Increment 18: Deliberate General-Purpose Assistant & Persisted Task-Solving Architecture

**Branch**: `feature/increment18-deliberate-task-solving-loop`  
**Status**: Implemented (Reversible Development / Review Branch)  
**Target Hardware Profile**: Constrained Local / Distributed Edge Hosts (~3.7 GiB to 4 GiB RAM per node, `qwen2.5:1.5b` and `qwen2.5:3b` via loopback Ollama)

---

## 1. Executive Summary & Capability Goal

Increment 18 transforms Qwen Collective from a set of specialized orchestration, research, and artifact subsystems into a coherent, deliberate, general-purpose assistant capable of:
1. **Understanding requests & preserving user intent** across multi-turn conversations, mid-flight requirement updates, ambiguity resolution, and scope changes.
2. **Researching unfamiliar subjects** using source-separated retrieval, claim-level verification, contradiction detection, and freshness/scope checks.
3. **Reasoning through complex problems** via explicit subproblem decomposition, exact `Decimal` calculation, alternative comparison against hard/soft constraints, and bounded adaptive escalation.
4. **Writing and testing software** against pinned, independent acceptance tests in isolated runners without exposing hidden test oracles to model prompts.
5. **Creating useful, provenance-bound artifacts** and **managing substantial projects over time** across interruptions, restarts, and requirement revisions.

Rather than assuming that small local models (`qwen2.5:1.5b`, `qwen2.5:3b`) behave like frontier models when given longer prompts or more agents, this architecture uses **persisted state**, **deterministic tools**, **strict source/instruction separation**, **bounded context assembly**, **independent verification**, and **measured role routing** to achieve dependable task completion with honest limits.

---

## 2. Current-State vs. Target-State Mapping

### 2.1 Current Request → Planning → Execution → Verification → Delivery Path (Before Increment 18)

| Path Stage | Implemented Module(s) | Connected to UI / API? | Test / Script Only? | Identified Gaps Addressed in Increment 18 |
| :--- | :--- | :--- | :--- | :--- |
| **Conversational Entry (`/chat`)** | `app/routes/chat.py`, `app/services/chat_context.py` | Yes (`/chat`, `/chat/send`) | No | Previously injected passive project markdown into a single chat completion (`max_tokens=2048`). Did not distinguish simple direct questions from durable project assignments, nor expose structured project progress, active step, blockers, or steering controls. |
| **Intake & Requirement Parsing** | `app/services/orchestration/intake.py`, `planner.py:analyze_intake` | Yes (via `/projects/{id}/orchestration/intake`) | No | `RequirementLedger` existed in memory during plan generation/evaluation (`RequirementKind`, `RequirementHardness`), but lacked a durable versioned table tracking user decisions/corrections, explicit assumptions, material missing information, requirement dependencies, verification methods/statuses, and selective artifact invalidation upon mid-flight steering. |
| **Planning & Task Graph** | `app/services/orchestration/planner.py`, `task_graph.py`, `contract.py` | Yes (`/orchestration/plan`) | No | Supported template & AI plans (`PLANNER_MAX_TOKENS=2048`), but lacked adaptive effort escalation, subproblem option matrices, and selective re-planning that preserves unaffected accepted work when constraints change mid-run. |
| **Context Assembly & Project Memory** | `app/services/orchestration/context_manager.py`, `knowledge_store.py` | Yes (used by `engine.py`) | No | **Defect fixed in Increment 18**: `_format_input_for_inference` and `build_inference_prompt` previously defaulted missing `verification_status` to `"supported"` (`c.get("verification_status") or "supported"`). Also lacked persistent structured project memory (`project_memory_state`) for candidate approaches, failed approaches, unresolved contradictions, and downstream invalidation. |
| **Execution & Native Task Leasing** | `app/services/orchestration/engine.py`, `model_router.py`, `vps_agent/executor.py` | Yes (`/api/worker/*`, `/orchestration/*`) | No | Prompt/token caps were uncoordinated across `planner.py` (2,048 tokens), `model_router.py` (8,000 chars), `context_manager.py` (5,200 chars), and `vps_agent/executor.py` (1,024 tokens / 10,000 chars). Role routing defaulted to `1.5b` even when `3b` was required for multi-constraint reasoning or structured JSON/code bundles. |
| **Financial & Claim Verification** | `app/services/orchestration/financial_ledger.py`, `app/services/research/financial_reasoning.py`, `claim_contracts.py` | Partial (used by research ledger) | Mostly exercised in targeted tests | Exact `Decimal` cent-level reconciliation and source-scope claim contracts existed, but were not wired into a general-purpose deliberate reasoning & option-comparison tool catalogue accessible to project runs. |
| **Software Proposal & Protected Verification** | `app/services/orchestration/code_proposals.py`, `protected_verification.py`, `code_sandbox.py` | Held behind explicit dev injection | Exercised in isolated tests | Provided append-only `software_review_items` and isolated test verification, but lacked a general-purpose iterative failure-repair cycle (`proposed -> observed -> verified_for_scope / failed`) with hidden oracle isolation. |

---

## 3. Unified Persisted Task-Solving Cycle

Increment 18 unifies conversational interaction and project orchestration around **one persisted task-solving cycle** without creating a competing agent loop or duplicate database:

```mermaid
flowchart TD
    U["User Request / Turn (/chat or /orchestration)"] --> M{"Mode Classifier"}
    M -->|"Simple Question / Conversational"| D["Direct Response Mode (No Project Machinery)"]
    M -->|"Substantial Assignment / Multi-Step / Artifact"| P1["1. Understand & Update Durable Requirement Ledger"]
    P1 --> P2{"2. Material Gaps or Infeasible Constraints?"}
    P2 -->|"Critical Ambiguity"| C["Request Targeted Clarification (or Disclose Explicit Assumption)"]
    P2 -->|"Clear / Disclosed"| P3["3. Plan & Decompose Subproblems / Options"]
    P3 --> P4["4. Select Next Action & Escalation Step (Steps 1..7)"]
    P4 --> P5["5. Retrieve Bounded Context (Strict Source Separation)"]
    P5 --> P6["6. Execute via Typed Tool / Native Worker Lease"]
    P6 --> P7["7. Inspect & Record Raw Observation"]
    P7 --> P8{"8. Deterministic Verification (7-State Outcome)"}
    P8 -->|"verified_for_scope / supported"| P9["9. Deliver / Advance to Next Subgoal"]
    P8 -->|"partial / failed / contradiction"| R["5-Step Failure Repair or Bounded Replan"]
    R -->|"Budget Remaining"| P4
    R -->|"Budget Exhausted / Hard Conflict"| B["Report Honest Blocker & Preserved Partial Work"]
```

### 3.1 Two Execution Modes
1. **Direct-Response Mode (`mode = "direct_response"`)**:
   - Triggered for self-contained questions, explanations, or lightweight calculations that require no durable deliverables, multi-stage research, or code/test execution.
   - Returns an immediate response without creating unnecessary projects, task graphs, or artifacts.
2. **Durable-Project Mode (`mode = "durable_project"`)**:
   - Triggered for substantial assignments involving multiple deliverables, external/document research, multi-constraint option comparisons, financial/data reconciliation, code generation with tests, or multi-turn project management.
   - Persists state across `orchestration_runs`, `project_requirement_items`, `project_requirement_revisions`, `project_memory_state`, `orchestration_tasks`, and `artifacts`.

---

## 4. Prompt, Context, and Output Budget Alignment

To prevent silent prompt clipping or truncated structured outputs across small local models (`4096`-token context window on 3.7–4.0 GiB RAM hosts), Increment 18 coordinates budgets across all layers:

| Component | Parameter | Previous Value | Coordinated Increment 18 Policy |
| :--- | :--- | :--- | :--- |
| `context_manager.py` | `DEFAULT_CONTEXT_CHARS` / `PER_INPUT_CHARS` | `5200` / `2400` | `5200` chars default (`2400` per input), expandable up to `6800` chars for multi-source synthesis while staying strictly below the `8000`-char router/worker ceiling. |
| `model_router.py` | `MAX_PROMPT_CHARS` | `8000` | `8000` chars hard cap; logs explicit `prompt_truncated` warning if caller exceeds budget instead of silently clipping critical instructions. |
| `planner.py` | `PLANNER_MAX_TOKENS` | `2048` | Coordinated with worker `MAX_NUM_PREDICT` (`2048` tokens max for structured plans/code bundles; `calculate_planner_token_budget` ensures prompt + generation fits within `4096` context window). |
| `vps_agent/executor.py` | `MAX_NUM_PREDICT` / `MAX_OUTPUT_CHARS` | `1024` / `10000` | `MAX_NUM_PREDICT` raised to `2048` (matching `PLANNER_MAX_TOKENS` so planner and multi-file code bundle generations are never silently clamped to 1,024 tokens by the worker); `MAX_OUTPUT_CHARS = 12000`. |
| `evaluator.py` | Truncation Detection | Partial (`output_not_truncated`) | Enforces explicit truncation checks for both prose (incomplete sentence/bullet at token cap) and JSON/code bundles (unbalanced braces/fences or `gen_tokens >= num_predict`). |

---

## 5. Seven-State Verification Model & Five-Step Failure Repair

Every requirement item and artifact version carries an explicit verification method and one of seven verification statuses:
1. `proposed`: Model or planner generated a candidate output or plan not yet checked.
2. `observed`: Raw tool or worker execution receipt recorded, but domain/acceptance checks not yet run.
3. `supported`: Factual claim or recommendation is backed by verified source snapshots and/or exact calculation derivations.
4. `verified_for_scope`: Deliverable passed all deterministic schema, execution, and goal checks for its declared scope.
5. `partial`: Some requirements or sub-deliverables passed and are preserved, while others remain incomplete or failed.
6. `blocked`: Execution cannot proceed due to an infeasible constraint conflict, missing external authority/resource, or exhausted budget.
7. `failed`: Output violated hard constraints, failed independent tests, or contained unsupported/contradicted claims.

When a step produces `failed` or `partial`, the deliberate loop executes a **5-step failure repair cycle** under fresh native task/lease execution:
1. **Classify Failure**: Distinguish syntax error, test failure, schema mismatch, missing evidence, arithmetic mismatch, constraint violation, or truncation.
2. **Isolate Root Cause**: Extract exact error diagnostics (e.g., failing test case name/assertion or violated constraint ID) **without exposing hidden test source code or private oracle answers** in the repair prompt.
3. **Preserve Unaffected Work**: Keep validated upstream artifacts, source snapshots, and unrelated completed deliverables intact.
4. **Formulate Targeted Repair Action**: Select the appropriate escalation step (e.g., tighten output schema, run `Decimal` calculator, switch from `qwen2.5:1.5b` to `qwen2.5:3b`, or narrow context).
5. **Re-Verify Independently**: Execute the repaired proposal through a fresh native task/lease and re-run independent verification checks.

---

## 6. Migration Path

1. **Schema Migration 20 (`app/migrations.py`)**: Adds `project_requirement_items`, `project_requirement_revisions`, `project_memory_state`, and `procedure_outcome_registry` without altering existing Increment 1–19 tables.
2. **Schema Migration 21 (`app/migrations.py`)**: Adds live database-backed working-memory tables: `project_task_contracts`, `project_epistemic_items`, `project_epistemic_fts` (FTS5 virtual table), `project_working_checkpoints` (protected by append-only SQLite triggers `project_working_checkpoints_no_update` and `project_working_checkpoints_no_delete`), and `project_artifact_publication_journal`.
3. **Backward Compatibility**: All existing routes (`/projects`, `/tasks`, `/orchestration`, `/research`, `/artifacts`) and Increment 17 regression suites remain 100% compatible.
4. **Incremental Rollout**: The deliberate task-solving loop and working-memory subsystem are implemented in `app/services/orchestration/deliberate_loop.py` and `app/services/orchestration/working_memory.py`, integrated with `/chat` (`app/routes/chat.py`) and `/api/v1/projects/{id}/orchestration` (`app/routes/orchestration.py`), and verified against the 20 acceptance tasks in `COLLECTIVE_ACCEPTANCE_TASKS.json` plus 17 working-memory adversarial & API tests in `tests/test_working_memory_adversarial.py`.

---

## 7. Live Database-Backed Working Memory (`app/services/orchestration/working_memory.py`)

To enable Qwen Collective to break substantial projects into manageable tasks without losing the whole project's requirements, constraints, evidence, or progress, Increment 18 implements **live database-backed working memory** across three coordinated levels:

### 7.1 Three Coordinated Memory Levels
- **Level A — Resident Working Context (`WorkingSetCache` & `assemble_scoped_work_packet`)**:
  - Bounded in-RAM working-set cache (`max_cached_records=128`, `max_cache_bytes=524,288`) and scoped task packet (`max_total_chars=5,200`, `pinned_budget_chars=1,800`, `reserved_output_chars=1,200`).
  - Essential project intent (current requirement revision, objective, hard constraints, exclusions, acceptance criteria, and active task contract) is **pinned** and never silently truncated. If pinned intent exceeds the configured budget, `assemble_scoped_work_packet` raises `ContextBudgetExceededError` recommending task division rather than truncating constraints.
  - Note: This is logical paging of structured project knowledge into task context; it does not replace model attention/KV memory or claim distributed inference capability.
- **Level B — Database-Backed Working State (SQLite WAL, Migrations 20 & 21)**:
  - `project_requirement_items` & `project_requirement_revisions`: Versioned requirement ledger with revision lineage and supersession tracking.
  - `project_task_contracts`: Explicit per-task contracts binding `task_key` to `bound_objective`, `bound_requirement_revision`, `bound_requirement_ids_json`, `depends_on_task_keys_json`, `owned_fields_json`, `resource_budget_json`, `acceptance_checks_json`, and `output_summary_json`.
  - `project_epistemic_items` & `project_epistemic_fts`: Fine-grained operational working-memory items strictly separated across 8 epistemic categories (`user_instruction_or_decision`, `source_observation`, `supported_claim`, `derived_result`, `hypothesis_or_estimate`, `model_proposal`, `verified_artifact`, `verification_failure`), indexed in SQLite FTS5 for project-scoped retrieval.
  - `project_working_checkpoints`: Append-only, immutable checkpoints recorded at `task_boundary`, `park`, `resume`, `steer`, `cancel`, and `manual` boundaries.
  - `project_artifact_publication_journal`: Two-phase crash recovery journal (`intent_logged -> artifact_written -> state_committed | rolled_back | reconciled_after_crash`) coordinating filesystem/CAS artifact writes with SQLite state commits.
- **Level C — Detailed Artifacts in Content-Addressable Storage (`artifact_manager.py` / `artifacts/storage.py`)**:
  - Full reports, raw source snapshots, datasets, code bundles, and test logs remain in CAS/filesystem storage and are referenced by exact `artifact_id`, `version`, and `sha256` digest rather than stuffed into model prompts.

### 7.2 Authoritative Project State (`derive_authoritative_project_state`)
Derived directly from SQLite tables (never from an unverified model summary), answering all 8 operational questions:
1. **What did the user ask for?** (`what_user_asked_for`: objective, deliverables, acceptance criteria, hard constraints, soft preferences, exclusions)
2. **Which requirement revision is current?** (`current_requirement_revision`: revision number, change type, reason, timestamp)
3. **What is complete and independently checked?** (`completed_and_independently_checked`: verified tasks, verified CAS artifacts, verified epistemic results)
4. **What is proposed or unverified?** (`proposed_or_unverified`: model proposals, hypotheses/estimates, `unknown`-status items, unverified artifacts, stale tasks)
5. **What is blocked, contradictory, or missing?** (`blocked_contradictory_or_missing`: blocked tasks, verification failures, requirement/evidence contradictions, material missing info)
6. **Which task is active?** (`active_task`)
7. **What should happen next?** (`next_action`)
8. **What changed since the last checkpoint?** (`last_checkpoint` & `delta_since_last_checkpoint`)

### 7.3 Transactional Work Boundary, Revision Fencing, Dependency-Closure Invalidation & Retrieval
- **7-Step Checkpointed Work Boundary (`execute_checkpointed_work_boundary`)**:
  1. Reads current requirement and state revisions without holding a database transaction open across inference or tool execution.
  2. Assembles a bounded, version-checked scoped work packet (`assemble_scoped_work_packet`).
  3. Executes one native model/tool action into a bounded intermediate buffer (`MAX_INTERMEDIATE_BUFFER_BYTES = 256 KiB`).
  4. Verifies optimistic concurrency fencing (`StaleWorkerResultError` if `requirement_revision` changed mid-flight).
  5. Coordinates two-phase artifact publication via `project_artifact_publication_journal` (`reconcile_crash_publication_journal` recovers or rolls back interrupted writes on restart).
  6. Records qualifier-preserving epistemic items (`record_epistemic_item`, keeping missing status as `'unknown'` and stripping prompt injections from untrusted documents) and updates `project_task_contracts`.
  7. Commits an immutable row in `project_working_checkpoints`.
- **Dependency-Closure Invalidation (`invalidate_dependency_closure`)**:
  - Computes the transitive closure over `bound_requirement_ids`, `input_artifact_versions`, and `depends_on_task_keys`, marking only affected downstream tasks/artifacts/claims `'stale'` while preserving unaffected completed work.
- **Active Working-Memory Retrieval (`WorkingMemoryRetriever`)**:
  - Executes SQL-filtered (`WHERE project_id = ?`), paginated (`LIMIT ? OFFSET ?`) queries supporting all 7 operational query types (`needed_for_task`, `why_approach_selected`, `depends_on_requirement`, `evidence_for_conclusion`, `changed_since_pass`, `proceed_while_blocked`, `remaining_for_completion`) plus project-isolated FTS5 search (`fts_search`) and index rebuild (`rebuild_epistemic_fts_index`).
- **Park, Resume, Cancellation & Parallel Merge Rules**:
  - `park_project` and `cancel_task_or_project` fail closed with `ParkOrCancelCleanupError` when `active_process_cleanup_confirmed=False` (`cancel_pending_cleanup_unknown`).
  - `resume_project` reconciles crash journals, checks for requirement revisions since park, preserves verified upstream tasks, and resumes at the next pending/stale task without replaying the conversation.
  - `commit_parallel_task_update` enforces per-task `owned_fields` and raises `ParallelConflictError` on unowned or conflicting writes.


