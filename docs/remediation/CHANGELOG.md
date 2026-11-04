# Remediation Program Changelog

All notable changes and architectural advancements executed during the Qwen Collective remediation program are documented chronologically in this file.

---

## [Stage 7] — 2026-10-20: Knowledge Vault, Review Governance & Traceable Learning

### Added & Hardened
- **Strict 5-Tier Knowledge Trust Lifecycle (Finding F-18 / Invariant I-11):**
  - Schema Migration 18: Added `trust_tier`, `review_notes`, `reviewed_by`, `reviewed_at`, `vault_note_path`, `sync_status` to `knowledge_records`; created `knowledge_conflicts` table.
  - Implemented `VALID_TRUST_TIERS`: `Captured` $\rightarrow$ `Candidate` $\rightarrow$ `Corroborated` $\rightarrow$ `Reviewed` $\rightarrow$ `Reusable`.
  - Implemented `transition_trust_tier` enforcing directed progression graph. Direct leaps (e.g. Captured to Reusable) are rejected.
  - Contradicted records are strictly blocked from promotion until conflict resolution.
- **Corroboration & Evidence Grounding (Invariant I-07):**
  - Transition to `Corroborated` requires $\ge 2$ independent sources or deterministic verification tests.
- **Privacy & Credential Safeguards:**
  - Enforced `_PRIVATE_INDICATORS` audit blocking private, credential, or confidential data from entering the collective `Reusable` tier.
- **Conflict & Contradiction Ledger:**
  - Implemented `record_knowledge_conflict`, `resolve_knowledge_conflict`, and `list_knowledge_conflicts`.
  - When contradictory findings emerge, both records are marked `contradicted`, reusable records are auto-demoted to `candidate`, and dependent project decisions/tasks are notified via `knowledge_impacts`.
- **Operator Review Queue:**
  - Implemented `get_governance_review_queue` aggregating pending promotions, unreviewed candidates, and unresolved conflicts into a single operator dashboard queue.
- **Bidirectional Markdown Vault Synchronization:**
  - Implemented `sync_record_to_vault`, `sync_vault_to_record`, and `audit_vault_synchronization` providing seamless round-trip between database records and filesystem Markdown notes.
- **Model Router Dynamic Discovery & Persistent Capability Verification (Finding F-18 / Invariant I-02):**
  - Enhanced `eligible_nodes(task, nodes, enforce_verified=True)` in `model_router.py` to check for verified capability probes in `infrastructure_verifications` and `infrastructure_capabilities`.
  - Implemented `verify_node_model_capability` recording persistent, timestamped capability probe results.
- **Dedicated Stage 7 Test Suite (`tests/test_stage07_knowledge_governance.py`):**
  - 10 integration tests (Tests A through J) covering trust tier progression, corroboration requirements, credential filtering, contradiction demotion, conflict resolution, operator review queues, vault synchronization, and persistent model router verification. 10/10 passed (100%).

---

## [Stage 6] — 2026-10-20: Canonical Immutable Artifact Subsystem & One-Click Deliverables

### Added & Hardened
- **Content-Addressed Storage Unification (Finding F-11 / Invariant I-09):**
  - Consolidated storage backend into atomic SHA-256 CAS (`store_cas_blob`, `get_cas_path`) with reference counting and deduplication.
  - Multi-project isolation: identical file contents uploaded by different projects share the underlying blob safely without merging permissions.
- **Artifact Confinement & Anti-Traversal:**
  - Enforced strict storage root boundaries (`evidence_dir`, `artifacts_dir`). Path traversal attempts immediately raise `NotFound` without leaking CAS blobs.
- **Distinct Identifiers for Identical Filenames:**
  - Distinct artifacts uploaded with identical filenames (`script.py`) receive unique primary keys, unique storage locations, and separate version histories.
- **Task Attempt Scoping & Retry Output Preservation:**
  - Task retries write attempt-scoped filenames (`att{N}_{filename}`), preventing retry executions from clobbering attempt 1 diagnostic or failure logs.
  - Stale and superseded task attempt numbers are strictly rejected on commit (Invariant I-05).
- **Worker Isolation & Authorization Enforcement (Finding F-10 / Invariant I-04):**
  - Worker artifact downloads and queries enforce strict worker lease assignment and project ownership checks.
- **Immutable Lineage Graph & Cross-Project Reuse (Invariant I-10):**
  - Created `artifact_lineage` tracking roots, derivatives, and authorized reuse without mutating or clobbering original artifacts.
- **Safe Inline Previews:**
  - Sanitized previews for SVG and HTML files, stripping script tags, javascript URIs, and event handlers.
- **Physical Deliverable Verification Gate (Invariant I-08 / Invariant I-14):**
  - Orchestration engine verifies physical file presence on disk or in CAS for all required deliverable artifacts; missing files trigger `BLOCKED` state, preventing illegitimate completed runs.
- **Dedicated Stage 6 Test Suite (`tests/test_stage06_artifact_subsystem.py`):**
  - 12 integration tests (Tests A through L) passing 100%. Full test suite: 646 collected, 645 passed, 1 known baseline failure (`test_knowledge_page`).

---

## [Stage 5] — 2026-10-20: Research Throughout the Project Lifecycle

### Added & Hardened
- **Elimination of Deliverable Hijacking (Finding F-05):**
  - Updated intake analysis in `planner.py` to differentiate software/database objectives from pure research reports.
  - Implemented `template_software_with_research_plan`: user-requested deliverables (e.g. `.py`, `.sql`) remain terminal deliverables (`terminal: True`, `produced_by: "verify_implementation"`), while research executes as an upstream sidecar feeding technical specifications into downstream implementation.
- **Continuous Lifecycle Research Checkpoints (`research_checkpoint.py`):**
  - Checkpoints integrated at intake analysis, feasibility planning, pre-task gap analysis, task execution, deliverable evaluation, and final reporting.
  - Implemented `evaluate_research_need`: inspects existing verified project context; if fresh verified sources exist, duplicate internet searches are suppressed.
  - Implemented graceful degraded state handling: network or provider exceptions set `status = "degraded"` with recorded evidence gaps, permitting offline execution to proceed safely.
- **Structured Evidence Records & Schema Migration 15 (`migrations.py`, `sources.py`, `verification.py`):**
  - Migration 15: Added `session_id`, `research_run_id` to `research_source_versions`; `session_id` to `research_sources`; `orchestration_run_id`, `research_session_id`, `source_version_id`, `is_heuristic`, `heuristic_label`, `informed_tasks_json`, `informed_requirements_json`, `contradiction_status` to `research_evidence`; and created `research_source_reuses` table with comprehensive lineage indexing.
  - Implemented `get_structured_evidence` returning full 10-field structured evidence records with character/line spans, content hashes, and version IDs.
  - Enforced reattribution prevention in `record_source`: `COALESCE(research_run_id, ?)` ensures original origin run ID cannot be overwritten by subsequent sessions or runs.
  - Re-observation of identical content records authorized source reuse in `research_source_reuses`; content modifications increment version and store new version rows.
- **Research-Driven Semantic Replanning (`planner.py`, `approval_manager.py`):**
  - Implemented `propose_research_driven_replan`: contradictory evidence triggers plan replanning bound to a 64-character SHA-256 canonical plan digest, transitioning run to `AWAITING_PLAN_APPROVAL` requiring human re-approval without converting software into reports.
  - Added `approve_plan_revision` in `approval_manager.py` verifying stored content digest against human approval.
- **Stage 7 Trust-Tier Governance (`knowledge_store.py`):**
  - Updated `propose_promotion` with `allow_auto_promote: bool = False`, strictly preventing candidate findings from auto-promoting to permanently trusted facts.
- **Dedicated Stage 5 Test Suite (`tests/test_stage05_research_lifecycle.py`):**
  - 10 comprehensive integration tests (Tests A through J) covering software deliverable retention, multi-phase checkpoints, duplicate search suppression, versioned observation lineage, contradictory evidence replanning, unsupported capability gap handling, offline resilience, prompt injection resistance, candidate knowledge governance, and OS sandbox fail-closed execution.
- **Full Regression Test Evidence (`run_evidence_suite.py`):**
  - 62 modules, 634 collected, 633 passed, 1 known baseline failure (`test_knowledge_page`), 0 regressions ($634 \equiv 633 + 1 + 0 + 0$).

---

## [Stage 0] — 2026-10-18: Reconnaissance, Verification, Safety Baseline & Architecture Design

### Added
- **Remediation Control Plane:** Established complete governance directory structure in `docs/remediation/`.
  - `README.md`: Program charter, architecture scope, directory layout, and stage progression guidelines.
  - `STATUS.md`: Authoritative stage tracker and invariant adherence matrix.
  - `BASELINE.md`: System specs, Python/SQLite versions, baseline test run results (460 pass, 2 fail), and database analysis.
  - `FINDING_TRACEABILITY.md`: Detailed audit verification matrix mapping findings F-01 through F-18 to exact source files, observed behaviors, and planned fixes.
  - `DECISIONS.md`: Initial Architecture Decision Records (ADR-01 through ADR-08) covering route compatibility, anti-substitution invariants, CAS persistence, and anti-SSRF IP pinning.
  - `RISK_REGISTER.md`: Comprehensive risk inventory (RSK-01 through RSK-12) with severity ratings and mitigation strategies.
  - `TEST_MATRIX.md`: Baseline test catalog, root-cause analysis of existing failures, and planned test cases for all findings.
  - `OPEN_ISSUES.md`: Tracking known gaps, hardware dependencies, and deferred items.
  - `RELEASE_READINESS.md`: Production rollout checklist and go/no-go gates.
  - `reports/STAGE_00_BASELINE.md`: Formal Stage 0 reconnaissance and architecture baseline report.

### Verified
- **Audit Findings F-01 through F-18:** Independently inspected working tree code (`c98510b` on `feature/unified-artifact-deliverables`). Confirmed static presence or reproduced 17 findings; confirmed F-03 is partially addressed (token limit bumped, but dynamic budgeting still required).
- **Test Baseline:** Executed full test suite (`python -m pytest tests -q`). 460 passed, 2 failed due to known preexisting issues (heuristic JSON key filter in `planner.py` and template copy drift in `knowledge/index.html`).

### Deferred / Pending Authorization
- Functional modifications to application source code (Stage 1 through Stage 9) deferred pending human review and explicit authorization.

---

## [Stage 3] — 2026-10-19: Execution Reliability, Scheduler Fencing & Semantic Acceptance

### Added & Hardened
- **Asynchronous Scheduler Thread Pool Offload (`engine.py`):**
  - Integrated bounded `_CONTROLLER_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4)` to decouple controller steps from the scheduler tick.
  - Scheduler releases leader lock during slow controller execution, eliminating starvation and avoiding lease expiration during long operations.
- **Attempt & Fencing Token Verification (`engine.py`):**
  - Implemented `_commit_controller_attempt()` to verify attempt number and dispatched state before committing results. Out-of-order, superseded, or stale commits are rejected and audited under `stale_attempt_fencing`.
  - Stale worker submissions after lease expiration and reassignment rejected with 409 Conflict.
- **Three-Layer Evaluation Architecture (`evaluator.py`):**
  - Separated evaluation into `SchemaCheck` (data shape, AST validity, UTF-8, JSON/CSV), `ExecutionCheck` (worker receipt, exit code 0, non-empty, required sections), and `GoalCheck` (testable acceptance criteria, exported symbols, test fixture execution, telemetry presence, citation grounding).
  - Implemented typed deliverable validators: `evaluate_code_deliverable` (enforces `ast.parse` and test fixture execution; broken code fails even with polished README), `evaluate_data_deliverable` (enforces JSON/CSV parse and schema constraints), `evaluate_report` (enforces section labels, telemetry presence, citation grounding), and `evaluate_inference` (detects prompt injection, labels raw AI content as unverified, routes to `NEEDS_HUMAN_REVIEW`).
- **Terminal Deliverable Completion Gate (`engine.py`):**
  - Runs can transition to `COMPLETED` only when all terminal deliverables in an approved plan are verified and accepted. Runs with missing or blocked evidence halt at `BLOCKED` with detailed operator diagnostics.
- **Dedicated Stage 3 Test Suite (`tests/test_stage03_execution.py`):**
  - 14 comprehensive tests covering concurrent fencing, idempotent replay, lease expiration, crash recovery, unverified model outputs, health report telemetry gates, code syntax validation, citation grounding, dataset schema validation, and end-to-end deliverable gates (14/14 passing).
- **Verifiable Test Evidence (`run_evidence_suite.py`):**
  - 58 modules, 538 collected, 537 passed, 1 failed (the documented baseline failure `test_knowledge_page` in `tests/test_web_routes.py`), 0 regressions ($538 \equiv 537 + 1 + 0 + 0$).

---

## [Stage 2 Final Evidence & Semantic Safety Gate] — 2026-10-19: Evidence Integrity, Boundary Safety & Authorization Lifecycle

### Added & Hardened
- **Reproducible Test Evidence Suite (`run_evidence_suite.py`):**
  - Fully automated evidence runner saving raw pytest stdout, stderr, execution metadata, Git status, and JUnit XML to `docs/remediation/evidence/`.
  - Programmatically generated test matrix reconciling 524 collected = 523 passed + 1 failed + 0 skipped across 57 modules ($524 \equiv 523 + 1 + 0 + 0$).
  - Confirmed single failing test is `tests/test_web_routes.py::test_knowledge_page` (Preexisting Failure 2 from Stage 0 baseline).
- **Elimination of Silent Deliverable Mutation (`task_graph.py`):**
  - Removed default fallback to `"document"`. Unknown deliverable types are preserved verbatim and rejected by `validate_plan()` with structured code `UNSUPPORTED_DELIVERABLE_TYPE`.
  - Removed generic acceptance criteria injection (`"Validated output meets project requirements."`). Empty criteria are preserved and rejected with `MISSING_ACCEPTANCE_CRITERIA`.
  - Removed arbitrary marking of last deliverable terminal. Missing terminal deliverables fail with `MISSING_TERMINAL_DELIVERABLE`; intermediate task deliverables falsely marked terminal fail with `INCORRECT_TERMINAL_DELIVERABLE`.
  - Normalization of explicit aliases (`doc` -> `document`, `code` -> `software_artifact`) strictly preserved.
- **Strict Schema Boundary Enforcement (`task_graph.py`):**
  - Stopped truncating task keys, dependency IDs, input IDs, and roles to 40 characters or slicing lists to 20 entries in `normalize_plan()` and `auto_repair_plan()`.
  - Enforced strict schema limits in `validate_plan()`, recording the exact offending string and length in error diagnostics.
- **Cryptographic Human Approval & Digest Binding (`approval_manager.py`, `orchestrator.js`, `orchestration.py`):**
  - Hardened `approve_plan()` to verify `expected_digest` against `curr_digest`. Failing or omitting `expected_digest` when digest binding is required (`require_digest=True`) fails closed with `OMITTED_PLAN_DIGEST`.
  - Updated dashboard UI (`orchestrator.js`) to capture and transmit `window.__orchRevision.plan_digest` and `revision_id` directly from on-screen state.
  - Expanded test suite to 36 tests in `tests/test_stage02_planning.py` (36/36 passing).

---

## [Stage 2] — 2026-10-18: Semantically Safe Planning, Verified Capabilities & Prompt Fidelity

### Added
- **Authoritative Action Registry (`app/services/orchestration/action_registry.py`):**
  - Defines `ActionManifest`, `ActionKind` (`inference`, `controller_step`, `library_script`), and `GLOBAL_ACTION_REGISTRY`.
  - Implements dynamic capability probe tracking with verification status (`DECLARED`, `DISCOVERED`, `VERIFIED`, `STALE`, `FAILED`), timestamps, and configurable TTL.
  - Dynamically computes cluster eligible nodes based on verified capabilities and platform policy.
  - Generates authoritative prompt catalogs containing only genuine, verified cluster actions.
  - Implements structured capability gaps (`create_capability_gap`) for unsatisfied capabilities instead of hallucinating actions.
- **Lossless Intake Requirement Ledger (`app/services/orchestration/intake.py`):**
  - Defines `RequirementLedger`, `RequirementItem`, and `RequirementKind` (`OBJECTIVE`, `CONSTRAINT`, `DELIVERABLE`, `EXCLUSION`, `SUCCESS_CRITERION`).
  - Completely preserves user objectives, constraints, scope exclusions, requested deliverables, deadlines, and success criteria without hard character slicing.
  - Machine-checkable plan coverage validation (`validate_plan_coverage`) verifying that all hard requirements and exclusions are respected in candidate plans.
- **Dedicated Stage 2 Test Suite (13 Tests in `tests/test_stage02_planning.py`):**
  - Rejection of unknown library scripts and controller steps without silent mutation (F-01, F-02).
  - Rejection of disallowed action types (e.g. `bash_exec`).
  - Rejection of cyclic task graphs without deleting DAG edges (F-02, Kahn's algorithm preservation).
  - Rejection of deliverables with non-existent task producers (no arbitrary reassignment).
  - Preservation of safe deterministic repairs (self-reference stripping, deliverable type aliasing).
  - Authoritative Action Registry lookup, dynamic probe evaluation, and TTL expiration (F-18).
  - Structured capability gap generation for unverified or missing capabilities.
  - Lossless intake requirement ledger preservation and formatting (F-04).
  - Token-aware, resource-aware dynamic planning budget calculations for small Qwen models.
  - Robust JSON extraction handling code fences, prose wrappers, and schema key filtering.
  - Plan revision semantic diff computation (`diff_plans`) and mandatory re-approval enforcement (F-03).

### Changed
- **Anti-Substitution Task Graph Invariants (`app/services/orchestration/task_graph.py`):**
  - `normalize_plan()`: Removed keyword-based action type guessing ("health", "collect", "report", "assemble").
  - `auto_repair_plan()`: Strictly eliminated silent action conversions (`library_script -> inference`, unknown `controller_step -> assemble_report`, unknown `type -> inference`).
  - `auto_repair_plan()`: Removed dependency cycle edge pruning; cycles are preserved intact in the DAG for Kahn's algorithm to reject with `Dependency cycle among tasks`.
  - `auto_repair_plan()`: Removed silent reassignment of deliverable `produced_by` references to `task_keys[-1]`.
- **Dynamic Token Budgeting & Robust JSON Extraction (`app/services/orchestration/planner.py`):**
  - `build_planning_prompt()`: Replaced 1200-char truncation with `RequirementLedger.format_prompt_summary()` and dynamic action catalog from `GLOBAL_ACTION_REGISTRY.build_prompt_catalog(nodes)`.
  - `calculate_planner_token_budget()`: Added dynamic context window calculation reserving headroom and safety margins for small Qwen/Llama models.
  - `extract_json_object()`: Added `require_plan_keys` parameter (default `False`), enabling robust extraction of generic JSON objects while supporting schema-filtered extraction in planning engine ticks.
  - `diff_plans()`: Computes semantic diffs (added/removed/modified tasks and deliverables) between plan revisions.
  - `store_revision()`: Records semantic diffs in `model_info_json`, ensuring any semantic alterations require explicit re-approval.
- **Test Baseline Alignment:**
  - Updated `tests/test_orchestrator_project_isolation.py:279` to assert anti-substitution invariants.
  - Resolved preexisting failure in `test_orchestrator_units.py::test_extract_json_object_handles_prose_and_fences` (67/67 passing).

---

## [Stage 1] — 2026-10-18: Security and Authorization Trust Boundaries (Including Review Corrections)

### Added
- **Dedicated Security Test Suite (26 Tests):** Expanded `tests/test_stage01_security.py` from 10 to 26 adversarial and verification tests covering:
  - Worker lease fencing, generation tracking, CAS state transitions, unassigned upload rejections, and direct execution rules.
  - Exclusive atomic artifact staging (`open(..., 'xb')`), 25MB cap, malformed base64 checks, and 64-hex checksum validation.
  - Browser egress filtering proxy, DNS rebinding prevention, redirect revalidation, and fail-closed 503 fallback.
  - Double-submit cookie CSRF verification (positive, negative, and cross-session tests).
  - Pre-suite isolation preflight check.
- **Local Browser Egress Filtering Proxy (`OutboundFilteringProxy`):** Implemented in `app/services/research/browser_proxy.py`:
  - Loopback-bound forward proxy supporting CONNECT tunneling and GET/POST proxying.
  - Per-connection DNS resolution and destination IP pinning.
  - Blocks loopback, private RFC1918, link-local, carrier-grade NAT, and cloud metadata targets (HTTP 403 Forbidden).
  - Blocks WebSocket upgrade handshakes and browser subresources targeting restricted IP spaces.
- **Fail-Closed Structured Browser Fallback:** Configured Playwright with `--proxy-server` and `--proxy-bypass-list=<-loopback>`. If proxy fails or Playwright is not installed, returns structured HTTP 503 / `BrowserRenderResult(error=...)` with zero unproxied egress.
- **Stage 0 Test Isolation Pre-Suite Guard:** Added pre-flight assertions in `tests/conftest.py` ensuring `data_dir`, `db_path`, and `evidence_dir` reside strictly within pytest temporary directories and abort if pointing to `.qwen-collective`.

### Changed
- **Hardened HTTPX Pinned Transport:** Enforced `trust_env=False` and `keepalive_expiry=0.0` on `PinningHTTPTransport` to prevent OS proxy bypass and socket reuse rebinding; wired into DuckDuckGo, SearXNG, and API research providers.
- **DuckDuckGo & Extraction Fallbacks:** Hardened DuckDuckGo provider to use `lite.duckduckgo.com` POST endpoint with regex parsing; added standard library HTML stripping fallback in `extraction.py` for environments lacking `bs4`.
- **Worker Lease Authorization Gate:**
  - Blocked uploads to unassigned tasks (HTTP 403 Forbidden).
  - Blocked workers from claiming authority solely due to absence of lease rows (HTTP 409 Conflict if task has lease history).
  - Restricted direct execution to legacy direct assignment (`assigned_worker == worker_id` with 0 lease history rows).
  - Enforced fencing generations (`attempt_count` / `lease_id`) rejecting stale upload/submit attempts.
  - Enforced CAS state transitions and idempotency conflict detection in `submit_task_result()`.
- **Atomic Artifact Staging:**
  - Implemented `open(dest_file, 'xb')` (`O_CREAT | O_EXCL`) for exclusive file creation.
  - Safely falls back to UUID-stamped names only on `FileExistsError` collision, preserving evaluator-expected names where possible.
  - Validates 64-hex SHA-256 formatting and 25MB file size limit.
- **Governance Reconciliation:** Updated ADR-06 (`PENDING_HUMAN_APPROVAL`, `IMPLEMENTED`) and ADR-09 (`PENDING_HUMAN_APPROVAL`, `IMPLEMENTED`) with explicit note on separating implementation status from human approval.
- **Finding Classification:** Classified F-10 as `FIXED`, F-14 as `PARTIALLY_FIXED` (mandated until live browser proxy test runs in containerized production staging), and F-15 as `FIXED`.

---

## [Stage 2 Review Corrections] — 2026-10-19: Elimination of Semantic Mutation, Authorization Lifecycle Integration & Boundary Hardening

### Added
- **Complete Authorization Lifecycle Integration (`app/services/orchestration/approval_manager.py`, `engine.py`, `planner.py`):**
  - Full end-to-end integration test demonstrating: plan creation $\rightarrow$ revision N approval $\rightarrow$ modification in revision N+1 $\rightarrow$ revision N authorization revocation $\rightarrow$ scheduler refusal to execute revision N+1 $\rightarrow$ explicit revision N+1 reapproval $\rightarrow$ execution resumption $\rightarrow$ concurrent edit conflict and stale approval replay rejection.
  - Plan content digest (`plan_digest = sha256(canonical_json)`) binding in revisions, approvals, and events.
  - `edit_plan(..., base_revision_id)` concurrency guard rejecting stale concurrent plan edits with `OrchestrationError`.
  - Comprehensive semantic-diff detection (`diff_plans`) inspecting user objectives, hard constraints, exclusions, deadlines, success criteria, action identities, and deliverable acceptance criteria. A change to the requirement ledger invalidates approval even when the generated task graph is structurally identical.
- **Conservative Token Budgeting & Structured Error Handling (`app/services/orchestration/planner.py`):**
  - Defined `PlanContextExceededError(OrchestrationError)` with structured error code `INSUFFICIENT_CONTEXT`.
  - Implemented conservative prompt token budgeting (`math.ceil(len(prompt_text) / 3.0) + 120`).
  - Raises structured `PlanContextExceededError` whenever generation headroom drops below `min_tokens` or when model context window is missing/stale/unsupported, strictly eliminating silent truncation of `MUST` requirements.
- **Action Registry & Dispatcher Drift Audit (`app/services/orchestration/action_registry.py`):**
  - Automated bidirectional drift audit asserting all registered library scripts exist in `script_library.LIBRARY` and all controller steps exist in `task_graph.CONTROLLER_STEPS`.
  - Concrete probe evidence verification (software runtime, exit codes, verified features, TTL).
  - Stale and failed capability probes verified to block node eligibility when `enforce_verified=True`.
- **End-to-End Planner Integration Scenarios (`tests/test_stage02_planning.py`):**
  - Scenario A: Unknown executable action requested through genuine entrypoints rejected by validation gate, scheduler never dispatches, and structured `CAPABILITY_GAP` diagnostic reported.
  - Scenario B: Multi-requirement engineering project preserving explicit scope, exclusions, constraints, deadline, and executable deliverables through intake, planning, validation, persistence, and retrieval.
- **Expanded Stage 2 Test Suite (26 Tests in `tests/test_stage02_planning.py`):**
  - Separate negative tests for self-dependency cycles in `depends_on` and in `inputs`.
  - Separate negative tests for dangling dependencies on nonexistent tasks.
  - Separate negative tests for missing task inputs without declared dependencies.
  - Separate negative tests for role ambiguity (pipe-delimited role strings like `execution|analysis` rejected explicitly).
  - Rejection of unregistered action identifiers (`generate_natural_response`).
  - Verification that rejected graphs are never scheduled and rejected edges remain available for debugging.

### Changed
- **Elimination of Semantic Mutation in Normalizer & Auto-Repair (`app/services/orchestration/task_graph.py`):**
  - `normalize_plan()`: Strictly removed stripping of self-references from `inputs` and `depends_on`, auto-injection of `inputs` into `depends_on`, and arbitrary role token selection from pipe-delimited role strings.
  - `auto_repair_plan()`: Strictly removed deletion of self-references, pruning of missing dependencies, input binding mutation, and role reassignments.
  - Confined deterministic repair strictly to provably safe syntactic normalization: deliverable type aliasing (`doc` $\rightarrow$ `document`), defaulting empty acceptance criteria, and ensuring terminal deliverable marker.
- **Finding Classification:**
  - Preserved F-14 as `PARTIALLY_FIXED` (browser proxy isolation awaiting production-staging container verification).
  - Classified F-18 as `PARTIALLY_FIXED` (action registry & capability verification completed for Stage 2; fleet model-router governance scheduled for Stage 7).
  - F-01, F-02, F-03, F-04 verified as `RESOLVED`.
- **Test Suite Reconciled:** 514 collected, 513 passed, 1 failed (`test_web_routes.py::test_get_runs_endpoint`, documented preexisting failure) across 57 modules in 55.77s. Zero regressions.

