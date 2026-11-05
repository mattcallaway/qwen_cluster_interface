# Audit Finding Traceability Matrix (F-01 through F-18)

**Audit Source:** `Qwen_Collective_Architecture_Code_Audit_2026-10-18.md`  
**Master Implementation Directive:** `Qwen_Collective_Antigravity_Master_Remediation_Prompt.md`  
**Current Status:** All 18 findings verified against active codebase on commit `c98510b`.

---

## 1. Traceability & Stage Mapping Summary

Aligned strictly with the canonical 9-stage roadmap:

| Finding ID | Title / Vulnerability Area | Severity | Classification | Reconciled Stage | Invariants Violated |
|:---:|---|:---:|:---:|:---:|:---:|
| **F-01** | Plan Repair Subsumption & Silent Action Conversion | Critical | `RESOLVED` | Stage 2 | I-01, I-02, I-03, I-14 |
| **F-02** | Library Script Capability Hallucination & Scope Inversion | High | `RESOLVED` | Stage 2 | I-01, I-02 |
| **F-03** | Context Window Starvation & Incomplete Plan Truncation | High | `RESOLVED` | Stage 2 | I-01 |
| **F-04** | Objective Truncation & Parameter Bleed-Through | High | `RESOLVED` | Stage 2 | I-01 |
| **F-05** | Monolithic Research Pipeline Assumption | Medium | `FIXED` | Stage 5 | I-01, I-14 |
| **F-06** | Synthesis Evidence Hallucination & Paragraph Chunking | High | `FIXED` | Stage 4 | I-07, I-11 |
| **F-07** | Unenforced Citation Invariant & Zero-Citation Pass | High | `FIXED` | Stage 4 | I-07, I-08 |
| **F-08** | Naive Domain Parsing & Synthetic Confidence Scoring | Medium | `FIXED` | Stage 4 | I-07 |
| **F-09** | Research Session Scope Leaks Across Run Boundaries | High | `FIXED` | Stage 4 | I-10 |
| **F-10** | Worker Artifact Upload Authorization, Checksum & Overwrite Flaws | Critical | `FIXED` | Stage 1 | I-04, I-09 |
| **F-11** | Artifact Storage Path Collision & Missing CAS in Orchestrator | Critical | `CONFIRMED_STATICALLY` | Stage 6 (CAS) / Stage 1 (quarantine) | I-09, I-10 |
| **F-12** | Deliverable Acceptance Inversion & Shallow Evaluation | Critical | `PARTIALLY_FIXED` | Stage 3 / Stage 4 | I-08, I-14 |
| **F-13** | Scheduler Synchronous Execution & Controller Starvation | High | `FIXED` | Stage 3 | I-05 |
| **F-14** | DNS Rebinding Vulnerability in Crawler/Browser | Critical | `PARTIALLY_FIXED` | Stage 1 / Stage 4 | I-06 |
| **F-15** | Missing CSRF Protection on Worker Admin Routes | High | `FIXED` | Stage 1 | I-04 |
| **F-16** | API Route Drift Between Implementation and Documentation | Medium | `CONFIRMED_STATICALLY` | Stage 8 | (API Contract) |
| **F-17** | Incomplete Backup Strategy & Missing Artifact Vault | High | `CONFIRMED_STATICALLY` | Stage 8 | I-12 |
| **F-18** | Model Router Dynamic Discovery & Untrusted Capabilities | Medium | `FIXED` | Stage 2 (planning) / Stage 7 (governance) | I-02 |

---

## 2. Detailed Finding Analysis, Evidence & Verification States

### F-01: Plan Repair Subsumption & Silent Action Conversion
- **Subsystem:** Orchestration / Task Graph
- **Code Locations:** [`app/services/orchestration/task_graph.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/task_graph.py)
- **Status:** `RESOLVED`
- **Remediation Details:** Silent action conversion in `auto_repair_plan` and `normalize_plan` eliminated. Unregistered scripts, unknown controller steps, and disallowed action types are strictly preserved as un-substituted defects and reported as validation errors. Cycle edge deletion removed; Kahn's algorithm cycle detection flags cycles without graph edge mutation. Missing deliverable producers are rejected instead of arbitrarily reassigned.
- **Verification Tests:** Verified by [`tests/test_stage02_planning.py::test_unknown_script_rejected_explicitly_without_substitution`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage02_planning.py), `test_unknown_controller_step_rejected_explicitly_without_substitution`, `test_dependency_cycle_rejected_without_edge_deletion`, and `test_missing_deliverable_producer_rejected_not_reassigned`.
- **Target Stage:** Stage 2 (Workstream 2A).

---

### F-02: Library Script Capability Hallucination & Scope Inversion
- **Subsystem:** Orchestration / Action Registry & Planner
- **Code Locations:** [`app/services/orchestration/action_registry.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/action_registry.py), [`app/services/orchestration/planner.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/planner.py)
- **Status:** `RESOLVED`
- **Remediation Details:** Authoritative `ActionRegistry` established. The dynamic prompt tool catalog exposes only genuine registered actions. Hallucinated script names generate explicit validation errors and structured capability gaps rather than executable tasks.
- **Verification Tests:** Verified by [`tests/test_stage02_planning.py::test_authoritative_action_registry_dynamic_probes`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage02_planning.py) and `test_structured_capability_gaps`.
- **Target Stage:** Stage 2 (Workstream 2B).

---

### F-03: Context Window Starvation & Incomplete Plan Truncation
- **Subsystem:** Orchestration / Planner
- **Code Locations:** [`app/services/orchestration/planner.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/planner.py)
- **Status:** `RESOLVED`
- **Remediation Details:** Implemented dynamic token budgeting (`calculate_planner_token_budget`) reserving safety margin against model context window, preventing context overflow on small 1.5B/3B Qwen models. Enhanced `extract_json_object` to cleanly extract markdown-fenced and prose-wrapped JSON with `require_plan_keys` filtering. Implemented plan semantic diffing (`diff_plans`) to enforce re-approval gates.
- **Verification Tests:** Verified by [`tests/test_stage02_planning.py::test_token_aware_resource_aware_planning`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage02_planning.py), `test_extract_json_object_handles_prose_and_fences`, `test_extract_json_object_require_plan_keys`, and `test_plan_revisions_semantic_diff_computation`.
- **Target Stage:** Stage 2 (Workstream 2D).

---

### F-04: Objective Truncation & Parameter Bleed-Through
- **Subsystem:** Orchestration / Intake & Planner
- **Code Locations:** [`app/services/orchestration/intake.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/intake.py), [`app/services/orchestration/planner.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/planner.py)
- **Status:** `RESOLVED`
- **Remediation Details:** Replaced hard 1200-character slicing with structured `RequirementLedger`. Intake parser captures and preserves user objectives, constraints, scope exclusions, requested deliverables, deadlines, and success criteria, rendering them losslessly into prompt requirements and validating plan coverage.
- **Verification Tests:** Verified by [`tests/test_stage02_planning.py::test_lossless_intake_requirement_ledger`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage02_planning.py).
- **Target Stage:** Stage 2 (Workstream 2C).

---

### F-05: Monolithic Research Pipeline Assumption
- **Subsystem:** Orchestration / Planner & Research Lifecycle
- **Code Locations:** [`app/services/orchestration/planner.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/planner.py), [`app/services/orchestration/research_checkpoint.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/research_checkpoint.py), [`app/services/research/sources.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/sources.py), [`app/services/research/verification.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/verification.py)
- **Status:** `FIXED`
- **Remediation Details:**
  1. *Elimination of Deliverable Hijacking:* Replaced hardcoded pipeline hijacking with capability-aware sidecar planning (`template_software_with_research_plan`). Software, database, and application requests strictly preserve user deliverables (e.g. `.py`, `.sql`) as terminal deliverables (`terminal: True`, `produced_by: "verify_implementation"`), while research tasks execute upstream feeding technical specifications into implementation.
  2. *Continuous Lifecycle Checkpoints:* Checkpoints operate across intake analysis, feasibility planning, pre-task gap analysis, task execution, deliverable evaluation, and final reporting.
  3. *Duplicate Search Suppression & Reuse Lineage:* `evaluate_research_need` checks project sources and collective knowledge; if fresh verified evidence exists, internet searches are suppressed and an authorized reuse entry is written to `research_source_reuses`.
  4. *Degraded State Resilience:* Search provider or network failures transition checkpoint to explicit `degraded` state with evidence gaps recorded, allowing offline tasks to continue safely.
  5. *Immutable Structured Evidence Records:* Evidence records include exact character/line spans, content hashes, source versions, contradiction status, heuristic strength labels, and links to informed tasks. Reattribution is prevented via origin run ID preservation (`COALESCE(research_run_id, ?)`).
  6. *Research-Driven Semantic Replanning:* Contradictory evidence triggers semantic replanning bound to SHA-256 digest, transitioning run to `AWAITING_PLAN_APPROVAL` requiring human re-approval without converting code into reports.
  7. *Trust-Tier Governance:* Discoveries are stored in candidate knowledge with `allow_auto_promote=False`, preventing premature automatic elevation to trusted facts.
- **Verification Evidence:** Verified by [`tests/test_stage05_research_lifecycle.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage05_research_lifecycle.py) (Tests A through J passing 100%).
- **Target Stage:** Stage 5.

---

### F-06: Synthesis Evidence Hallucination & Paragraph Chunking
- **Subsystem:** Research / Synthesis & Extraction
- **Code Locations:** [`app/services/research/extraction.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/extraction.py), [`app/services/research/verification.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/verification.py), [`app/services/orchestration/engine.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/engine.py)
- **Status:** `FIXED`
- **Remediation Details:**
  1. *Factual Claim Span Extraction (`extract_claims_with_spans`):*
     - Replaced naive paragraph and sentence splitting with boundary-preserving extraction returning `FactualClaimSpan` dataclass.
     - Preserves character offsets (`start_char`, `end_char`), 1-indexed line numbers (`start_line`, `end_line`), exact quotes, and surrounding context passages.
     - Filters out short fragments, markdown noise, headers, and bullet lines.
  2. *Defensible Evidence Strength & Confidence Scoring (`EvidenceAssessment`):*
     - Heuristic scoring explicitly structured as `EvidenceAssessment` with `is_heuristic = True` and score limitations.
     - Confidence is grounded structurally and empirically in measurable statistics, definite factual relations, source quality weighting, independent domain counts, and contradiction penalties.
     - Contradicted claims receive 0.0 confidence (no artificial confidence floor).
     - Verified monotonicity (two independent sources yield higher strength than one; verbatim quotes yield higher directness than paraphrases; non-independent sources do not inflate score).
- **Verification Evidence:** Verified by [`tests/test_stage04_research_integrity.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage04_research_integrity.py) and [`tests/test_stage04_review_corrections.py::TestConfidenceSemantics`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage04_review_corrections.py).
- **Target Stage:** Stage 4.

---

### F-07: Unenforced Citation Invariant & Zero-Citation Pass
- **Subsystem:** Research / Citations & Evaluator
- **Code Locations:** [`app/services/research/citations.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/citations.py), [`app/services/research/entailment.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/entailment.py), [`app/services/orchestration/evaluator.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/evaluator.py)
- **Status:** `FIXED`
- **Remediation Details:**
  1. *Strict Zero-Citation Rejection:*
     - In `validate_citations()` and `validate_report_citations()`, reports with 0 citations fail validation with `is_valid = False` and structured error `MISSING_REQUIRED_CITATIONS`.
     - In `evaluate_report()`, deliverables with `requires_citations = True` and 0 citations fail the `citation_coverage` goal check, producing deterministic `decision = "REJECTED"`.
  2. *Deterministic Factual Entailment Engine:*
     - Distinguishes genuine factual support from topical keyword similarity, returning structured `ClaimEntailmentResult` (`SUPPORTED`, `CONTRADICTED`, `INSUFFICIENT_EVIDENCE`, `UNVERIFIED`).
     - Evaluates negation polarity conflicts, directional metric shifts, temporal/modality discrepancies, epistemic debunking, and numeric percentage mismatches.
     - Adversarial cases A through G verified: negation polarity (Test A), directional conflict (Test B), temporal/modal (Test C), debunking (Test D), numeric percentage mismatch (Test E), topic overlap without evidence (Test F), and exact quotes (Test G).
  3. *Deliverable Acceptance Routing:*
     - Contradicted claims cause report acceptance to fail closed with `decision = "REJECTED"`.
     - Unverified claims route reports to `decision = "NEEDS_HUMAN_REVIEW"`.
- **Verification Evidence:** Verified by [`tests/test_stage04_research_integrity.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage04_research_integrity.py) and [`tests/test_stage04_review_corrections.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage04_review_corrections.py) (`TestFactualEntailmentAdversarialCases`, `TestCitationGroundingInDeliverables`).
- **Target Stage:** Stage 4.

---

### F-08: Naive Domain Parsing & Synthetic Confidence Scoring
- **Subsystem:** Research / Public Suffix & Verification
- **Code Locations:** [`app/services/research/public_suffix.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/public_suffix.py), [`app/services/research/verification.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/verification.py)
- **Status:** `FIXED`
- **Remediation Details:**
  1. *Authoritative Public Suffix & Multi-Tenant Parsing (`extract_registered_domain`):*
     - RFC/PSL compliant parser handling ccSLD multi-part suffixes (`.co.uk`, `.com.au`, `.co.jp`, `.ac.in`, `.gov.uk`, `.gc.ca`, etc.), parsing `news.bbc.co.uk` as `bbc.co.uk`.
     - Explicit handling for private multi-tenant hosting platforms (`github.io`, `pages.dev`, `vercel.app`, `substack.com`) where tenant subdomains represent independent publisher entities (`alice.github.io` != `bob.github.io`).
  2. *Publisher Identity Separation & Ownership Deduplication:*
     - Disentangles registered domains from underlying corporate publisher entities (e.g., AP wire syndicated across `apnews.com`, `yahoo.com`, `washingtonpost.com`, or conglomerate ownership across `wsj.com` and `nypost.com`).
  3. *Multi-Factor Content Shingling Deduplication:*
     - Implemented 3-gram text shingling (`compute_text_shingle_similarity`) to detect syndicated stories with modified titles, while avoiding false-positive deduplication of unrelated articles with generic titles like "Market Update".
  4. *Contradiction Surfacing & Preservation:*
     - Contradicted claims retain structured status `contradicted` rather than being averaged away or overwritten to supported/corroborated. Confidence drops to 0.0 with no artificial floor.
- **Verification Evidence:** Verified by [`tests/test_stage04_research_integrity.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage04_research_integrity.py) and [`tests/test_stage04_review_corrections.py::TestPublicSuffixAndIndependence`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage04_review_corrections.py).
- **Target Stage:** Stage 4.

---

### F-09: Research Session Scope Leaks Across Run Boundaries
- **Subsystem:** Research / Engine, Sources & Database
- **Code Locations:** [`app/migrations.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/migrations.py), [`app/services/research/sources.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/sources.py), [`app/services/research/verification.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/verification.py), [`app/services/orchestration/engine.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/engine.py)
- **Status:** `FIXED`
- **Remediation Details:**
  1. *Database Migration 14 & Run Isolation:*
     - Added `research_run_id TEXT` column and `idx_research_sources_run` index to `research_sources`.
     - Unique run identifier `rrun-{orch_run_id}` tags all ingested sources, claims, and evidence.
  2. *Verification Across All 8 Audit Scenarios:*
     - Scenarios 1 & 2: Two separate research runs in the same project produce isolated source and claim sets.
     - Scenarios 3 & 4: Ingested sources with missing or NULL `research_run_id` and historical records cannot satisfy citation grounding in a newly executed run without explicit operator reuse.
     - Scenario 5: Citations referencing sources from a different run are detected and rejected.
     - Scenarios 6 & 7: Explicit operator-authorized reuse via `research_source_reuses` table preserves original source provenance and origin run ID without mutably altering `research_sources`.
     - Scenario 8: Historical/NULL run evidence is never implicitly promoted into new runs.
     - Scenario 9: Reattribution prevention: `record_source` enforces immutability on existing URL records and prevents arbitrary run reattribution.
- **Verification Evidence:** Verified by [`tests/test_stage04_research_integrity.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage04_research_integrity.py) and [`tests/test_stage04_review_corrections.py::TestDatabaseMigrationAndRunIsolationEndToEnd`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage04_review_corrections.py).
- **Target Stage:** Stage 4.

---

### F-10: Worker Artifact Upload Authorization, Checksum & Overwrite Flaws
- **Subsystem:** Worker API / Security & Integrity
- **Code Locations:** [`app/routes/worker.py:485-550`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/routes/worker.py#L485-L550), [`app/services/worker.py:75-160, 740-840, 1250-1360`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/worker.py#L75-L160)
- **Status:** `FIXED`
- **Remediation Details:**
  1. *Authoritative Authorization Gate (`verify_worker_task_authorization`):*
     - Rejects unassigned tasks with HTTP 403 Forbidden.
     - Workers cannot claim execution authority merely because no lease row exists; tasks with lease history require an active lease (HTTP 409 Conflict).
     - Direct unleased execution strictly restricted to legacy direct assignment (`assigned_worker == worker_id` with 0 lease history rows).
     - Fencing generation (`attempt_count` / `lease_id`) rejection: stale lease IDs and expired generations cannot mutate tasks (HTTP 409 Conflict).
     - Cross-worker task access strictly blocked (HTTP 403 Forbidden).
     - Non-terminal guard enforced on artifact uploads (HTTP 409 Conflict).
  2. *Exclusive Atomic Artifact Staging (`store_task_artifact`):*
     - Validates SHA-256 against strict 64-hex regex (`^[a-fA-F0-9]{64}$`).
     - Enforces 25MB artifact payload cap.
     - Validates Base64 encoding strictly (`validate=True`).
     - Pre-computes and matches SHA-256 in memory before opening file handles; fail-closed on mismatch (HTTP 400 Bad Request) with zero bytes persisted.
     - Sanitizes filenames against path traversal (`..`), directory separators, and Windows reserved device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1..9`, `LPT1..9`).
     - Atomic exclusive creation semantics: opens with `open(..., 'xb')` (`O_CREAT | O_EXCL`). On collision (`FileExistsError`), safely resolves to a UUID-stamped filename.
     - Verifies persisted bytes and size on disk post-write.
  3. *CAS State Transitions & Idempotency Conflict Prevention (`submit_task_result`):*
     - Atomic CAS updates task status (`UPDATE tasks SET status = ? WHERE id = ? AND status = ?`).
     - Compares submitted payload against existing execution record; compatible subsets are accepted idempotently, while conflicting values or mismatched outputs return HTTP 409 Conflict.
- **Verification Evidence:** 14 automated tests in [`tests/test_stage01_security.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage01_security.py) (including `test_worker_cannot_upload_to_unassigned_task`, `test_worker_cannot_obtain_execution_authority_solely_without_lease`, `test_legacy_direct_assignment_rules`, `test_stale_lease_identifier_and_fencing_generation_rejected`, `test_expired_worker_cannot_submit_after_task_reacquired`, `test_concurrent_cas_state_transition_conflict`, `test_idempotency_key_payload_conflict_prevention`, `test_atomic_artifact_staging_exclusive_and_verified`, `test_artifact_size_limit_rejected`, `test_artifact_malformed_base64_rejected`, `test_artifact_invalid_checksum_format_rejected`, `test_checksum_verification_fails_closed_zero_bytes_persisted`).
- **Target Stage:** Stage 1 (Workstream 1A).

---

### F-11: Artifact Storage Path Collision & Missing CAS in Orchestrator
- **Subsystem:** Orchestration / Artifact Manager & Canonical Artifact Service
- **Code Locations:** [`app/services/orchestration/artifact_manager.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/artifact_manager.py), [`app/services/artifacts/storage.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/artifacts/storage.py), [`app/services/artifacts/service.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/artifacts/service.py)
- **Status:** `PARTIALLY_VERIFIED` (Consolidated CAS architecture implemented; Stage 6 acceptance verification in progress)
- **Remediation Details:** Consolidated artifact persistence into a unified Content-Addressed Storage (CAS) backend with SHA-256 deduplication and reference counting. Every artifact record maintains unique primary keys and paths even for duplicate filenames. Task retry attempts are scoped by attempt number (`att{N}_{filename}`), preventing retry overwrites of prior attempt logs. Cross-worker and cross-project access are strictly forbidden. Lineage graph records source/derivative nodes and authorized cross-project reuse without mutating original files. Deliverable evaluation verifies physical disk/CAS presence before allowing run completion.
- **Verification Tests:** Verified by [`tests/test_stage06_artifact_subsystem.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage06_artifact_subsystem.py) (12 initial tests); final acceptance tests A-J and UI browser workflow verification in progress.
- **Target Stage:** Stage 6.

---

### F-12: Deliverable Acceptance Inversion & Shallow Evaluation
- **Subsystem:** Orchestration / Evaluator & Engine
- **Code Locations:** [`app/services/orchestration/evaluator.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/evaluator.py), [`app/services/orchestration/code_sandbox.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/code_sandbox.py)
- **Status:** `PARTIALLY_FIXED`
- **Remediation Details:** Replaced shallow evaluation with authoritative 3-layer architecture (`SchemaCheck`, `ExecutionCheck`, `GoalCheck`) returning deterministic decisions (`ACCEPTED`, `REJECTED`, `BLOCKED`, `NEEDS_HUMAN_REVIEW`). Implemented typed validators for data, telemetry, and reports with citation grounding.
  - *Execution Safety Restriction (Stage 3 Correction / Pre-Stage 4):* Subprocess execution with monkey-patching was reclassified as `HIGH` risk and does not constitute an OS-enforced security sandbox. Dynamic code execution of untrusted generated code and test fixtures is disabled by default (`is_os_enforced_sandbox_available() -> False`), returning `BLOCKED` with structured reason `SECURE_EXECUTION_UNAVAILABLE`. Static AST syntax checking continues with explicit disclaimer (`code_syntax_valid`). Maintained as `PARTIALLY_FIXED` pending OS-enforced container backend integration.
- **Verification Tests:** Verified by [`tests/test_stage03_execution.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage03_execution.py) and [`tests/test_stage03_execution_safety.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage03_execution_safety.py) (38 tests).
- **Target Stage:** Stage 3 / Stage 4 / Stage 8.

---

### F-13: Scheduler Synchronous Execution & Controller Starvation
- **Subsystem:** Orchestration / Engine Scheduler
- **Code Locations:** [`app/services/orchestration/engine.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/engine.py)
- **Status:** `FIXED`
- **Remediation Details:** Decoupled controller step execution from the scheduler tick loop via bounded thread pool `_CONTROLLER_EXECUTOR = ThreadPoolExecutor(max_workers=4)`. Dispatches run asynchronously without holding the scheduler lock. Implemented attempt and fencing token verification via `_commit_controller_attempt()`, preventing stale/superseded attempts from committing out-of-order results. Worker disconnects and retries are accepted idempotently (`already_processed`); stale worker attempts after lease expiration and task reassignment are rejected with 409 Conflict.
- **Verification Tests:** Verified by [`tests/test_stage03_execution.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage03_execution.py) (`test_controller_step_async_offload_does_not_hold_scheduler_lock`, `test_stale_attempt_fencing_rejects_outdated_controller_commit`, `test_worker_idempotent_result_replay`, `test_stale_worker_submission_after_lease_expiry_rejected`, `test_crash_recovery_resumes_durable_state_without_illegal_leaps`).
- **Target Stage:** Stage 3 (Workstream 3A).

---

### F-14: DNS Rebinding Vulnerability in Crawler/Browser
- **Subsystem:** Research / Retrieval & Browser Egress
- **Code Locations:** [`app/services/research/retrieval.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/retrieval.py), [`app/services/research/browser_proxy.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/browser_proxy.py), [`app/services/research/browser.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/browser.py), [`app/services/research/policy.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/policy.py)
- **Status:** `PARTIALLY_FIXED`
- **Remediation Details:**
  1. *Socket IP Pinning (`PinningSyncBackend` & `PinningHTTPTransport`):* Pre-resolves DNS, validates all destination IPs against `ResearchSecurityPolicy`, and connects TCP sockets directly to `(pinned_ip, port)`. TLS handshake uses `server_hostname=host` for SNI and certificate validation. Set `trust_env=False` and `keepalive_expiry=0.0` to eliminate proxy bypass and socket reuse rebinding. All HTTP retrieval (DuckDuckGo, SearXNG, API providers) utilizes this pinned transport. Redirects re-validate destination IPs on every hop.
  2. *Browser Egress Isolation (`OutboundFilteringProxy`):*
     - Local forward filtering proxy listening strictly on loopback (`127.0.0.1:auto`).
     - Supports both HTTP CONNECT tunneling and standard HTTP GET/POST forward proxying.
     - Resolves destination hostnames on every connection attempt, verifies every resolved IP against `ResearchSecurityPolicy`, and pins outbound upstream sockets directly to the verified destination IP.
     - Intercepts and denies HTTP/HTTPS connections to loopback (`127.0.0.1`), private RFC1918 subnets (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), link-local, carrier-grade NAT, and cloud metadata endpoints (`169.254.169.254`, `metadata.google.internal`), returning HTTP 403 Forbidden with `X-Blocked-By: OutboundFilteringProxy`.
     - Blocks WebSocket upgrade attempts (`Upgrade: websocket`) with HTTP 403 Forbidden.
     - Blocks browser subresource requests to restricted IP spaces.
     - Configured in Playwright via `--proxy-server` with `--proxy-bypass-list=<-loopback>`.
  3. *Fail-Closed Structured Fallback:* If proxy startup fails or Playwright is not installed, browser rendering fails closed, returning structured HTTP 503 Service Unavailable / `BrowserRenderResult(error="Browser automation unavailable...")` with zero unproxied network egress.
  4. *Governance Classification:* Classified as `PARTIALLY_FIXED` until full live browser execution runs in production staging.
- **Verification Evidence:** 7 automated tests in [`tests/test_stage01_security.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage01_security.py) (`test_browser_proxy_blocks_direct_private_and_loopback`, `test_browser_proxy_dns_rebinding_prevented`, `test_browser_proxy_blocks_subresources_and_websockets`, `test_failed_proxy_causes_browser_retrieval_fail_closed`, `test_httpx_pinning_transport_connects_to_verified_ip_and_preserves_sni`, `test_httpx_redirect_revalidation_blocks_internal_hops`, `test_research_providers_use_secured_transport`).
- **Target Stage:** Stage 1 (Workstream 1B).

---

### F-15: Missing CSRF Protection on Worker Admin Routes
- **Subsystem:** Worker API / Admin Routes
- **Code Locations:** [`app/routes/worker.py:260-310`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/routes/worker.py#L260-L310), [`app/auth.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/auth.py)
- **Status:** `FIXED`
- **Remediation Details:**
  1. Full audit of all mutating routes: distinguished cookie-authenticated web administration endpoints from bearer-token authenticated worker node routes.
  2. Enforced double-submit cookie / `X-CSRF-Token` header verification (`verify_csrf`) on cookie-authenticated administrative endpoints:
     - `POST /api/v1/worker/enrollment-tokens`
     - `DELETE /api/v1/worker/enrollment-tokens/{token_id}`
     - `DELETE /api/v1/worker/tokens/{token_id}`
     - `POST /api/v1/worker/register` (when authenticated via admin session cookie)
  3. Positive tests verify that requests presenting matching session cookies and `X-CSRF-Token` headers succeed (HTTP 200/201).
  4. Negative tests verify that requests missing tokens or with invalid tokens receive HTTP 403 Forbidden.
  5. Cross-session tests verify that a token valid for Session A is rejected when used with Session B (HTTP 403 Forbidden).
  6. Bearer token worker routes (`/tasks/{id}/artifacts`, `/tasks/{id}/submit`, `/tasks/{id}/heartbeat`) remain exempt and operational.
- **Verification Evidence:** 4 automated tests in [`tests/test_stage01_security.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage01_security.py) (`test_admin_csrf_positive_legitimate_actions_succeed`, `test_admin_csrf_negative_missing_and_invalid_tokens_rejected`, `test_admin_csrf_negative_cross_session_token_rejected`, `test_bearer_token_worker_operations_unaffected_by_csrf`).
- **Target Stage:** Stage 1 (Workstream 1C).

---

### F-16: API Route Drift Between Implementation and Documentation
- **Subsystem:** API Specification & Routing
- **Code Locations:** [`app/routes/orchestration.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/routes/orchestration.py) vs `API_AND_INTEGRATION_SPECIFICATION.md`
- **Status:** `CONFIRMED_STATICALLY`
- **Static Verification:** Documented routes (`/api/v1/orchestrator/runs`, `/runs/{id}/plan`) diverge from canonical routes (`/api/v1/orchestration/projects/{id}/runs`).
- **Unverified Aspects:** Impact on external third-party SDK callers.
- **Target Stage:** Stage 8 (Workstream 8A).

---

### F-17: Incomplete Backup Strategy & Missing Artifact Vault
- **Subsystem:** Operations & Runbooks
- **Code Locations:** [`OPERATIONS_AND_RUNBOOK.md:3.2`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/OPERATIONS_AND_RUNBOOK.md#L3)
- **Status:** `CONFIRMED_STATICALLY`
- **Static Verification:** Runbook only instructs `sqlite3 .backup data/qwen_collective.db`; omits CAS blobs (`data/artifacts/`), vault notes, and manifests.
- **Unverified Aspects:** Rehydration time and storage footprint for multi-gigabyte CAS stores.
- **Target Stage:** Stage 8 (Workstream 8C).

---

### F-18: Model Router Dynamic Discovery & Untrusted Capabilities
- **Subsystem:** Orchestration / Action Registry & Model Router
- **Code Locations:** [`app/services/orchestration/action_registry.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/action_registry.py), [`app/services/orchestration/model_router.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/orchestration/model_router.py)
- **Status:** `FIXED`
- **Remediation Details:**
  1. *Authoritative Action Registry (Stage 2):* Implemented authoritative `ActionRegistry` with explicit `CapabilityProbeRecord` tracking verification states (`DECLARED`, `DISCOVERED`, `VERIFIED`, `STALE`, `FAILED`) with configurable TTL and timestamping. Dynamic capability checks verify evidence and expiration rather than trusting arbitrary strings. Manifest-to-dispatcher drift protection enforced.
  2. *Persistent Capability Verification & Auditing (Stage 7):* Added `verify_node_model_capability` recording verifiable probe results into `infrastructure_capabilities` and `infrastructure_verifications`.
  3. *Enforced Verification in Model Routing (Stage 7):* Enhanced `eligible_nodes(task, nodes, enforce_verified=True)` to reject unverified or failed node model capabilities.
- **Verification Tests:**
  - Stage 2: Verified by [`tests/test_stage02_planning.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage02_planning.py) (`test_action_registry_and_dispatcher_bidirectional_drift_audit`, `test_capability_probe_concrete_evidence_verification`, `test_stale_or_failed_capability_probe_blocks_execution`, `test_nonexistent_script_generate_natural_response_produces_capability_gap`).
  - Stage 7: Verified by [`tests/test_stage07_knowledge_governance.py::test_i_model_router_persistent_capability_verification_f18`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage07_knowledge_governance.py) and `test_j_f18_knowledge_trust_tiers_e2e`.
- **Target Stage:** Stage 2 (action registry & capability verification) / Stage 7 (model-router governance & backend verification).
