# Stage 5 — Research Throughout the Project Lifecycle Report

**Stage:** Stage 5 (Continuous Research Workflow, Lifecycle Integration, and Re-planning)  
**Date:** 2026-10-20  
**Status:** **COMPLETE — READY FOR HUMAN REVIEW**  
**Active Branch:** `feature/unified-artifact-deliverables`  
**Baseline Test Evidence:** 634 collected, 633 passed, 1 known preexisting failure (`test_knowledge_page`), 0 skipped, 0 errors  
**Covered Findings:** **F-05** (FIXED), plus **F-02**, **F-04**, **F-06**, **F-09**, **F-18**  
**Enforced Invariants:** **I-01**, **I-02**, **I-07**, **I-08**, **I-10**, **I-11**, **I-14**  

---

## 1. Executive Summary

Stage 5 eliminates the hardcoded research-only workflow vulnerability (**Finding F-05**), where software, database, and application projects were previously hijacked and converted into pure markdown research reports. Under Stage 5:
1. **Preservation of Requested Outcomes (Finding F-05):** User objectives and deliverable specifications requesting software code, scripts, APIs, or database implementations retain their requested deliverable artifacts (e.g., `oauth_service.py`, `jwt_auth_service.py`) as terminal deliverables throughout the entire project lifecycle.
2. **Capability-Aware Research Sidecar:** External research is deployed as an upstream sidecar dependency (`search_spec` $\rightarrow$ `retrieve_spec` $\rightarrow$ `verify_spec`) feeding technical specifications into downstream implementation tasks without mutating or substituting user-requested deliverables.
3. **Continuous Lifecycle Research Checkpoints:** Research checkpoints are integrated across intake analysis, feasibility planning, pre-task gap analysis, task execution, deliverable evaluation, and final reporting. Checkpoints evaluate existing project and collective knowledge first, suppressing duplicate external searches when fresh verified evidence exists.
4. **Degraded State Resilience:** When external internet research or search providers fail (e.g., airgapped environments or API timeouts), checkpoints transition to an explicit `degraded` status with documented evidence gaps, allowing offline tasks to continue execution safely.
5. **Immutable Structured Evidence Records:** Evidence records store character/line spans, content hashes, source version IDs, verification/contradiction status, heuristic strength labels, and explicit links to informed tasks and requirements.
6. **Research-Driven Semantic Replanning:** Contradictory evidence discovered during execution triggers semantic replanning bound to an immutable SHA-256 plan digest, transitioning runs to `AWAITING_PLAN_APPROVAL` to mandate human re-approval without converting executable work into reports.
7. **Stage 7 Trust-Tier Governance:** Newly discovered research findings enter the candidate knowledge pipeline as `proposed` records with `allow_auto_promote=False`, strictly preventing automatic promotion to permanently trusted facts.
8. **Sandbox Execution Containment:** OS sandbox execution restrictions remain fail-closed (`BLOCKED` / `SECURE_EXECUTION_UNAVAILABLE`). Dynamic code verification safely halts if isolation is unavailable.

---

## 2. Key Architecture & Implementation Changes

### 2.1 Elimination of Hardcoded Research Hijacking (`app/services/orchestration/planner.py`)
- **Lossless Intake Classification:** `analyze_intake` inspects objectives and deliverable ledgers using `SOFTWARE_GOALS` and deliverable extensions (`.py`, `.js`, `.ts`, `.go`, `.sql`, `api`, `service`, `app`, `code`). Pure research reports are generated *only* when the user explicitly requests an autonomous research report and has no software deliverable requirements.
- **Sidecar Workflow Generator:** Implemented `template_software_with_research_plan`:
  - Retains user deliverables as primary terminal deliverables (`terminal: True`, `produced_by: "verify_implementation"`).
  - Appends upstream controller-side research tasks (`search_spec`, `retrieve_spec`, `verify_spec`).
  - Implementation tasks (`implement_software`) depend strictly on verified research outputs (`inputs: ["verify_spec"]`), feeding facts into code generation.

### 2.2 Lifecycle Research Checkpoints (`app/services/orchestration/research_checkpoint.py`)
- **Deterministic Need Assessment:** `evaluate_research_need` inspects project-scoped knowledge records, approved collective knowledge, and project source libraries.
- **Duplicate Search Suppression:** If fresh, verified evidence items exist, `research_needed` evaluates to `False`, duplicate searches are suppressed, and an authorized reuse record is written into `research_source_reuses`.
- **Checkpoint Persistence:** Checkpoints record durable audit events (`research_check_started`, `knowledge_search_completed`, `source_retrieved`, `knowledge_integrated`) and write structured summaries to the `stage_research_checkpoints` table.
- **Explicit Degraded State:** Catching provider or network exceptions sets `status = "degraded"`, notes `evidence_gaps = "External research unavailable"`, and permits offline execution to proceed without failing the run.

### 2.3 Structured Evidence Records & Reuse Lineage (`app/services/research/`)
- **Migration 15 (`app/migrations.py`):**
  - Added `session_id`, `research_run_id` to `research_source_versions`.
  - Added `session_id` to `research_sources`.
  - Added `orchestration_run_id`, `research_session_id`, `source_version_id`, `is_heuristic`, `heuristic_label`, `informed_tasks_json`, `informed_requirements_json`, `contradiction_status` to `research_evidence`.
  - Created `research_source_reuses` table with comprehensive lineage indexing (`project_id`, `orchestration_run_id`, `source_id`, `reused_at`).
- **Immutable Content Hashes:** `get_structured_evidence` in `verification.py` joins with `research_source_versions` to ensure versioned observations preserve exact immutable content hashes across updates.
- **Reattribution Prevention:** `record_source` in `sources.py` enforces `research_run_id = COALESCE(research_run_id, ?)` to ensure secondary runs or sessions cannot reattribute original source ownership.

### 2.4 Semantic Replanning with Digest Binding (`app/services/orchestration/planner.py`, `approval_manager.py`)
- **Semantic Plan Diffing & Digest Binding:** `propose_research_driven_replan` identifies affected tasks, appends revised research findings, preserves unaffected requirements and exclusions, computes a 64-character SHA-256 canonical digest, and transitions runs to `AWAITING_PLAN_APPROVAL`.
- **Approval Gate Helper:** Added `approve_plan_revision` in `approval_manager.py` with full cryptographic digest verification against human-approved plan content.

### 2.5 Trust-Tier Protection (`app/services/knowledge_store.py`)
- **Conservative Promotion Default:** `propose_promotion` enforces `allow_auto_promote: bool = False` by default. Newly discovered project findings remain in candidate status awaiting Stage 7 governance review.

---

## 3. Mandatory Integration Test Evidence (Tests A through J)

All 10 mandatory integration test scenarios specified in Stage 5 directives were authored in [`tests/test_stage05_research_lifecycle.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage05_research_lifecycle.py) and executed against the live test database:

| Test ID | Method Name | Requirement / Invariant Verified | Result |
|:---:|---|---|:---:|
| **Test A** | `test_a_software_project_retains_deliverable_with_research` | **Finding F-05 / Invariant I-01:** Software projects requesting `.py` deliverables retain terminal code deliverables throughout execution; research executes as an upstream sidecar. | **PASSED** |
| **Test B** | `test_b_research_at_planning_execution_evaluation_stages` | **Lifecycle Checkpoints:** Research checkpoints successfully execute and persist at intake, planning, execution, evaluation, and final reporting phases. | **PASSED** |
| **Test C** | `test_c_reuse_fresh_evidence_suppresses_duplicate_search` | **Duplicate Search Suppression / Invariant I-10:** Fresh verified project sources suppress duplicate web searches and record authorized reuse in `research_source_reuses`. | **PASSED** |
| **Test D** | `test_d_source_versioning_and_observation_lineage` | **Versioned Observations / Invariant I-09:** Repeated URL observations across sessions increment version numbers, preserve prior versions, and record version-specific content hashes without overwriting. | **PASSED** |
| **Test E** | `test_e_contradictory_evidence_triggers_replan` | **Semantic Replanning / Invariants I-03, I-13:** Contradictory evidence triggers semantic replanning bound to SHA-256 digest, transitioning to `AWAITING_PLAN_APPROVAL` and requiring human approval. | **PASSED** |
| **Test F** | `test_f_unsupported_capability_produces_gap_not_report` | **Action Registry / Invariant I-02:** Unsupported capabilities produce structured capability gaps or blocked states rather than substituting fallback research reports. | **PASSED** |
| **Test G** | `test_g_web_unavailable_offline_tasks_continue` | **Degraded State / Invariant I-14:** External research failure transitions checkpoint to `degraded` status with documented evidence gaps while offline tasks proceed. | **PASSED** |
| **Test H** | `test_h_prompt_injection_in_retrieved_web_content_fails` | **Security / Invariant I-06:** Malicious prompt injection embedded in retrieved web pages fails to alter orchestrator state, bypass approvals, or hijack deliverables. | **PASSED** |
| **Test I** | `test_i_candidate_knowledge_no_automatic_promotion` | **Trust-Tier Protection / Invariant I-11:** Newly discovered research findings enter candidate knowledge queue as `proposed` records with `allow_auto_promote=False`. | **PASSED** |
| **Test J** | `test_j_software_test_blocked_without_secure_sandbox` | **Sandbox Containment / Invariant I-08:** Untrusted code execution without a verified OS sandbox fails closed, returning `BLOCKED` with `SECURE_EXECUTION_UNAVAILABLE`. | **PASSED** |

---

## 4. Full Regression Test Evidence

The automated regression suite runner (`run_evidence_suite.py`) was executed across all 62 test modules in the repository:

- **Total Collected Tests:** 634
- **Passed:** 633
- **Failed:** 1 (`tests.test_web_routes.test_knowledge_page` — the known preexisting baseline failure)
- **Skipped:** 0
- **Errors:** 0
- **Regression Count:** 0
- **Execution Time:** 197.2 seconds
- **JUnit Report Artifact:** [`docs/remediation/evidence/junit_report.xml`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/junit_report.xml)
- **Test Inventory Artifact:** [`docs/remediation/evidence/test_ids_inventory.md`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/test_ids_inventory.md)

---

## 5. Traceability to Invariants and Audit Findings

| Finding / Invariant | Title | Resolution Description | Verification Reference |
|---|---|---|---|
| **F-05** | Autonomous Research Workflow Hijacking | Fixed. Pure research is strictly segregated from software projects. Software projects retain terminal software deliverables and attach research as an upstream sidecar. | `tests/test_stage05_research_lifecycle.py::test_a_software_project_retains_deliverable_with_research` |
| **I-01** | Plan Fidelity | Enforced. User-specified deliverables, constraints, and success criteria are preserved without dropping or altering deliverables. | `tests/test_stage05_research_lifecycle.py::test_a_software_project_retains_deliverable_with_research` |
| **I-02** | Feasibility Before Approval | Enforced. Missing capabilities produce structured capability gaps, never substitute reports. | `tests/test_stage05_research_lifecycle.py::test_f_unsupported_capability_produces_gap_not_report` |
| **I-07** | Honest Evidence | Enforced. Evidence records store exact quotes, spans, verification status, and heuristic strength labels. | `tests/test_stage05_research_lifecycle.py::test_c_reuse_fresh_evidence_suppresses_duplicate_search` |
| **I-08** | Honest Completion | Enforced. Software execution testing remains fail-closed without an OS sandbox. | `tests/test_stage05_research_lifecycle.py::test_j_software_test_blocked_without_secure_sandbox` |
| **I-09** | Immutable Outputs | Enforced. Versioned observations preserve exact immutable content hashes without overwriting. | `tests/test_stage05_research_lifecycle.py::test_d_source_versioning_and_observation_lineage` |
| **I-10** | Full Lineage and Explicit Reuse | Enforced. Cross-session and cross-run source reuse is recorded in `research_source_reuses` with origin run ID and timestamps. | `tests/test_stage05_research_lifecycle.py::test_c_reuse_fresh_evidence_suppresses_duplicate_search` |
| **I-11** | Knowledge Trust Tiers | Enforced. Research findings enter candidate pool with `allow_auto_promote=False`. | `tests/test_stage05_research_lifecycle.py::test_i_candidate_knowledge_no_automatic_promotion` |
| **I-14** | Explicit Degraded States | Enforced. Research failures record explicit degraded checkpoint states with evidence gaps. | `tests/test_stage05_research_lifecycle.py::test_g_web_unavailable_offline_tasks_continue` |

---

## 6. Review Gates and Next Steps

With Stage 5 implementation, 10 integration tests, and full regression test suite complete:
- Stage 5 is marked **COMPLETE** and submitted for human review.
- The project is ready to proceed to **Stage 6 (Canonical Immutable Artifacts, One-Click Deliverables & Cross-Project Reuse)** upon human authorization.
