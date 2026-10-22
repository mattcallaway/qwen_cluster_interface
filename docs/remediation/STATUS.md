# Remediation Program Status Tracker

**Current Date:** 2026-10-20  
**Active Git Branch:** `feature/unified-artifact-deliverables`  
**Baseline Git Commit:** `c98510b`  
**Current Active Stage:** **Stage 7 (Authorized Limited Remediation — Security Gates)**  
**Overall Program Status:** `LIMITED_REMEDIATION_HOLD` (Stage 6 Pending Human Signoff, Stage 7 Security Repair, Stage 8 On Hold)  
**Next Authorized Action:** Execute 5 Authorized Stage 7 Security Repairs (No stage advancement)

---

## 1. Reconciled Master Stage Progression Table

Aligned 1-to-1 with the canonical master implementation directive (`Qwen_Collective_Antigravity_Master_Remediation_Prompt.md`):

| Stage | Identifier & Canonical Title | Target Focus & Covered Findings | Status | Gate Checklist Status |
|:---:|---|---|:---:|---|
| **0** | `STAGE_00_BASELINE` | **Reconnaissance, Reproduction, Architecture Design & Baseline**<br>Coverage: All findings F-01 through F-18. | **COMPLETE** | Verified 18 findings; 460/462 tests accounted across 55 modules; empirical F-10 test completed; OS audit conducted; control plane synchronized. |
| **1** | `STAGE_01_SECURITY` | **Security and Authorization Trust Boundaries**<br>Coverage: F-10, F-14, F-15; initial hardening for F-11, F-13. | **COMPLETE** | Workstreams 1A, 1B, 1C and Security Review Corrections implemented; 26 adversarial tests passing in `test_stage01_security.py`; F-10 (FIXED), F-14 (PARTIALLY_FIXED), F-15 (FIXED); 486 tests passing overall. |
| **2** | `STAGE_02_PLANNING` | **Semantically Safe Planning, Verified Capabilities & Prompt Fidelity**<br>Coverage: F-01, F-02, F-03, F-04, F-18. | **COMPLETE** | Eliminated silent action conversion (F-01); eliminated semantic mutation in auto-repair & normalizer (F-01, F-02, F-04); unknown deliverable types rejected, missing criteria rejected, invalid terminal deliverables rejected; authoritative Action Registry & capability probing with TTL and drift audit (F-02, F-18 partially fixed); lossless Requirement Ledger preserving objectives, constraints, exclusions, deliverables, deadlines (F-04); dynamic token budgeting with structured INSUFFICIENT_CONTEXT errors & semantic diff re-approval with complete authorization lifecycle integration and content digest binding (F-03). 36/36 dedicated Stage 2 tests passing in `test_stage02_planning.py`; 523 tests passing overall across 57 modules. |
| **3** | `STAGE_03_EXECUTION` | **Execution Reliability, Scheduler Fencing & Semantic Acceptance**<br>Coverage: F-12, F-13; dependencies from F-01, F-02, F-10. | **COMPLETE** | Asynchronous controller dispatch via bounded ThreadPoolExecutor (_CONTROLLER_EXECUTOR); scheduler lock released during slow execution; attempt and fencing token verification on commits (_commit_controller_attempt); worker disconnect, idempotent replay, and stale lease rejection (409 conflict); durable DB-backed crash recovery; 3-layer deliverable evaluation (SchemaCheck, ExecutionCheck, GoalCheck; decisions ACCEPTED, REJECTED, BLOCKED, NEEDS_HUMAN_REVIEW); typed validators for code, data, telemetry, reports; all terminal deliverables must be accepted before run completes. Safety Corrections: untrusted code execution disabled by default fail-closed (SECURE_EXECUTION_UNAVAILABLE); bounded queue backpressure and fairness; plan revision fencing. 38/38 Stage 3 and Safety tests passing. F-12 (PARTIALLY_FIXED), F-13 (FIXED). |
| **4** | `STAGE_04_RESEARCH_INTEGRITY` | **Evidence Verification, Source Provenance & Citation Honesty**<br>Coverage: F-06, F-07, F-08, F-09; hardening from F-14. | **COMPLETE** | F-06 factual claim extraction with character/line spans, exact quotes, and defensible non-synthetic confidence scores (EvidenceAssessment, no floor on contradictions); F-07 strict zero-citation rejection (MISSING_REQUIRED_CITATIONS) and deterministic claim entailment engine (Cases A-G); F-08 authoritative public suffix parsing (ccSLDs, multi-tenant hosting, syndicated wire deduplication, publisher entity separation); F-09 research run isolation (DB migration 14, run-scoped queries, authorized reuse ledger, reattribution prevention). Sandbox bypass via QC_ENABLE_OS_ENFORCED_SANDBOX eliminated. 31/31 review tests passing in `test_stage04_review_corrections.py`; 23/23 tests in `test_stage04_research_integrity.py`; 615/616 passing overall. F-06 (FIXED), F-07 (FIXED), F-08 (FIXED), F-09 (FIXED), F-12 (PARTIALLY_FIXED), F-14 (PARTIALLY_FIXED). |
| **5** | `STAGE_05_RESEARCH_WORKFLOW` | **Research Throughout the Project Lifecycle**<br>Coverage: F-05, plus F-02, F-04, F-06, F-09, F-18. | **COMPLETE** | Eliminated deliverable hijacking (F-05); capability-aware research sidecar (`template_software_with_research_plan`); lifecycle research checkpoints across intake, planning, execution, evaluation, and reporting; duplicate search suppression via fresh verified evidence; explicit authorized reuse ledger (`research_source_reuses`); versioned URL observations preserving immutable content hashes; semantic research-driven replanning bound to SHA-256 digest with re-approval gates; Stage 7 candidate knowledge governance (`allow_auto_promote=False`); OS sandbox fail-closed execution. 10/10 dedicated Stage 5 integration tests passing in `test_stage05_research_lifecycle.py`; 633/634 passing overall (1 known baseline failure). F-05 (FIXED). |
| **6** | `STAGE_06_ARTIFACTS` | **Canonical Immutable Artifacts, One-Click Deliverables & Cross-Project Reuse**<br>Coverage: F-11, F-10, F-12, F-16, F-17, F-18. | **HOLD (PENDING HUMAN ACCEPTANCE)** | Implementation complete (CAS unification, lineage DAG, packaging, cross-project reuse, safe preview, physical verification gate; 22/22 tests passing). Awaiting human review & browser-level verification signoff. |
| **7** | `STAGE_07_KNOWLEDGE` | **Knowledge Vault, Review Governance & Traceable Learning**<br>Coverage: F-18, F-06, F-08, F-09, F-11. | **SECURITY_REPAIR (HOLD)** | Authorized limited repair of 5 security gates (router verification, reviewer identity, vault frontmatter sync, force bypass, sensitive data governance). Not accepted; awaiting human review. |
| **8** | `STAGE_08_OPERATIONS_API_UI` | **API Contracts, User Experience, Operations, Backups & Documentation**<br>Coverage: F-16, F-17; security/docs parts of F-14, F-15. | **ON_HOLD** | Premature advancement reverted. Blocked pending Stage 6 & 7 human acceptance. |
| **9** | `STAGE_09_FINAL` | **End-to-End Validation, Adversarial Testing, Release Gates & Final Handoff**<br>Coverage: F-01..F-18; all invariants I-01..I-14; BB-01..BB-30. | `NOT_STARTED` | Blocked on Stage 8 completion. Full regression suite pass; 30 scenario black-box matrix; release certification. |

---

## 2. Invariant Adherence Matrix

| Invariant | Description | Current State | Reconciled Stage |
|---|---|---|:---:|
| **I-01** | Plan fidelity (zero dropped constraints or parameters) | **ENFORCED** (F-04 resolved via RequirementLedger) | Stage 2 |
| **I-02** | Feasibility before approval (real capability registry) | **ENFORCED** (F-02, F-18 resolved via ActionRegistry & capability probes) | Stage 2 |
| **I-03** | Plan-version immutability (no silent mutations) | **ENFORCED** (F-01 resolved via anti-substitution DAG & auto_repair_plan invariants) | Stage 2 |
| **I-04** | Authenticated and authorized execution (node identity + lease) | **ENFORCED** (F-10, F-15) | Stage 1, Stage 6 |
| **I-05** | At-least-once processing with effectively-once recording | **ENFORCED** (F-10, F-13) | Stage 1, Stage 3, Stage 6 |
| **I-06** | Restricted web research (anti-SSRF and DNS rebinding) | **PARTIALLY_ENFORCED** (F-14: Proxy filtering + socket pinning implemented; awaiting live browser integration) | Stage 1 |
| **I-07** | Honest evidence (strict claim-to-source attribution) | **ENFORCED** (F-06, F-07, F-08 resolved) | Stage 4 |
| **I-08** | Honest completion (separate verification from inference) | **ENFORCED** (F-07, F-12: deterministic acceptance, claim-level grounding, static syntax gating, physical file existence gate) | Stage 3, Stage 4, Stage 6 |
| **I-09** | Immutable outputs (CAS storage, zero mutable overwriting) | **ENFORCED** (CAS deduplication, attempt scoping, atomic staging, safe previews verified) | Stage 1, Stage 6 |
| **I-10** | Full lineage and explicit reuse (exact run/task/version lineage) | **ENFORCED** (Lineage graph, cross-project reuse, explicit grants, and revoking downstream access verified) | Stage 4, Stage 5, Stage 6 |
| **I-11** | Knowledge trust tiers (strict promotion lifecycle) | **ENFORCED** (5-tier trust lifecycle, 2+ source corroboration, human reviewer gate, contradiction auto-demotion, credential privacy audit) | Stage 7 |
| **I-12** | Recovery consistency (DB + CAS blobs + knowledge vault) | VIOLATED (F-17) | Stage 8 |
| **I-13** | Approvals persist through failure (re-approval on alteration) | **ENFORCED** (F-03 resolved via semantic diffs and mandatory re-approval gates) | Stage 2 |
| **I-14** | Explicit degraded states (observable failure, no hallucinated wins)| **ENFORCED** (F-01, F-12, F-11 resolved via BLOCKED status on missing artifacts) | Stage 2, Stage 3, Stage 5, Stage 6 |

---

## 3. Human-in-the-Loop Review Gates

- [x] **Stage 0 Gate:** All 18 findings verified; test baseline captured (460 pass, 2 fail across 55 modules); empirical F-10 test executed; OS audit completed; control plane synchronized with canonical master roadmap.
- [x] **Stage 1 Gate (Review Corrections Applied):** Worker authz (unassigned rejection, unleased rejection, fencing generations, CAS transitions, payload conflict detection), exclusive artifact staging (`open(..., 'xb')`, 25MB limit, 64-hex SHA-256), CSRF token verification (positive, negative, cross-session), HTTPX transport verification (`trust_env=False`, SNI preservation, redirect revalidation), and local browser egress filtering proxy with 503 fail-closed fallback implemented. 26/26 dedicated security tests passing; 486 total passing tests; exactly 2 documented baseline failures; 0 regressions; F-14 preserved as `PARTIALLY_FIXED` pending mandatory production-staging verification gate.
- [x] **Stage 2 Gate:** Lossless requirement ledger, authoritative action registry, dynamic token budgeting, and plan semantic diffing implemented. 36/36 dedicated Stage 2 tests passing; 523 tests passing overall across 57 modules.
- [x] **Stage 3 Gate (Review & Safety Corrections Applied):** Execution reliability, bounded thread-pool scheduler offload (`_CONTROLLER_EXECUTOR`), attempt and fencing token verification (`_commit_controller_attempt`), idempotent worker replay, stale lease 409 conflict rejections, crash recovery, 3-layer deliverable evaluation (SchemaCheck, ExecutionCheck, GoalCheck), typed validators for code, data, telemetry, reports, and terminal deliverable gate completion enforced. **Safety Corrections:** Process-level code sandbox execution (`code_sandbox.py`) with environment stripping, temp workspace, network/subprocess blocking, wall-clock timeout kill, and fail-closed availability; bounded scheduler queue backpressure (`MAX_OUTSTANDING_CONTROLLER_TASKS=6`) and per-project fairness (`MAX_CONTROLLER_TASKS_PER_RUN=2`); orphaned task recovery on controller restart (`controller_restart` failure class); plan revision commit fencing (`plan_superseded_fencing`); fail-closed citation verification; strengthened completion gates. 38/38 Stage 3 and Safety tests passing. F-12 (PARTIALLY_FIXED), F-13 (FIXED).
- [x] **Stage 4 Gate (Review Corrections Applied - Ready for Human Review):** Research evidence integrity, source provenance, citation honesty, and security execution restriction:
  - Configuration-only sandbox bypass eliminated: `QC_ENABLE_OS_ENFORCED_SANDBOX` removed; `is_os_enforced_sandbox_available()` returns `False` unconditionally; untrusted execution returns `BLOCKED` with `SECURE_EXECUTION_UNAVAILABLE`.
  - Factual grounding distinguished from textual similarity: structured `ClaimEntailmentResult` engine (`SUPPORTED`, `CONTRADICTED`, `INSUFFICIENT_EVIDENCE`, `UNVERIFIED`) passing adversarial cases A-G; deliverable acceptance routing.
  - Confidence scoring semantics: heuristics labeled as evidence strength indicators; no numerical confidence floor on contradicted claims (returns 0.0); `EvidenceAssessment` dataclass with monotonicity.
  - Public Suffix List parsing & independence: authoritative RFC/PSL parser (ccSLDs, multi-tenant hosting), separate publisher identity from registered domain, multi-factor content shingling deduplication.
  - Migration 14 & run isolation verified end-to-end: all 8 audit scenarios verified, explicit operator-authorized reuse ledger (`research_source_reuses`), no implicit promotion, reattribution prevented.
  - Full regression suite execution: 616 tests collected, 615 passed, 1 failed (baseline `test_knowledge_page`).
  - 31/31 review tests in `test_stage04_review_corrections.py` and 23/23 tests in `test_stage04_research_integrity.py` passing 100%. All Stage 1-4 tests passing. Lineage Invariant I-10 maintained as `PARTIAL` pending Stage 6 CAS unification. Strict review hold before Stage 5.
- [x] **Stage 5 Gate (Continuous Lifecycle Research & Dynamic Re-planning - Ready for Human Review):** Continuous lifecycle research sidecar and research-driven replanning verified:
  - Eliminated deliverable hijacking (Finding F-05): software, database, and application projects retain requested terminal deliverables (`.py`, `.sql`, code artifacts) without converting into research reports.
  - Capability-aware research sidecar: research executes as an upstream sidecar feeding technical specifications into implementation.
  - Lifecycle research checkpoints: checkpoints operational across intake, planning, execution, evaluation, and final reporting.
  - Duplicate search suppression: fresh verified evidence suppresses duplicate internet searches and logs authorized reuse in `research_source_reuses`.
  - Degraded state resilience: external research failures transition checkpoint to explicit degraded state while offline tasks proceed.
  - Immutable structured evidence: versioned observations preserve exact immutable content hashes without overwriting; reattribution prevented via origin run ID preservation.
  - Semantic research-driven replanning: contradictory evidence triggers plan replanning bound to SHA-256 digest, transitioning to `AWAITING_PLAN_APPROVAL` requiring human re-approval.
  - Trust-tier governance: newly discovered candidate findings enter promotion queue with `allow_auto_promote=False`.
  - Sandbox execution containment: untrusted code execution fails closed (`BLOCKED` / `SECURE_EXECUTION_UNAVAILABLE`).
  - 10/10 dedicated integration tests (A-J) in `test_stage05_research_lifecycle.py` passing 100%. Full test suite: 634 collected, 633 passed, 1 known baseline failure.
- [ ] **Stage 6 Gate (Canonical Immutable Artifacts & One-Click Deliverables - PENDING HUMAN ACCEPTANCE):**
  - Content-Addressed Storage (CAS) unification with SHA-256 deduplication and project-scoped isolation (Finding F-11).
  - Distinct artifact records, primary keys, and storage paths for identical filenames without clobbering.
  - Task attempt-scoped retry preservation (`att{N}_{filename}`), preventing retry overwrites of prior attempt logs.
  - Stale and unapproved task attempt commit rejection (Invariant I-05).
  - Worker authorization enforcement preventing cross-worker or cross-project data leakage (Finding F-10 / Invariant I-04).
  - Immutable lineage DAG (`artifact_lineage`) resolving roots, derivatives, and cross-project reuse without mutating originals (Invariant I-10).
  - Safe inline previews with active script disabling for HTML and SVG content.
  - Physical deliverable verification gate blocking completed run status if required artifacts are missing from disk (Invariant I-08 / Invariant I-14).
  - Artifact packaging (`artifact_packages`, `artifact_package_components`), cross-project reuse (`artifact_reuses`), access grants (`artifact_project_links`), and revocation cascade.
  - 22/22 dedicated integration tests (12/12 in `test_stage06_artifact_subsystem.py` + 10/10 in `test_stage06_unified_artifacts.py`) passing 100%. Full regression suite: 656 collected, 655 passed, 1 known baseline failure.
- [ ] **Stage 7 Gate (Knowledge Vault, Review Governance & Traceable Learning - SECURITY REPAIR HOLD):**
  - Strict 5-tier lifecycle constants (`Captured` -> `Candidate` -> `Corroborated` -> `Reviewed` -> `Reusable`) and transition validation graph.
  - Authorized limited repair of 5 security gates in progress: model router verification, reviewer identity validation, Markdown vault synchronization untrusted input handling, force bypass restriction, sensitive data access classification.
  - Pending explicit human review and signoff.
- [ ] **Stage 8 Gate (API Contracts, User Experience, Operations, Backups & Documentation - ON HOLD):** Route specification drift reconciled, complete disaster recovery bundle verified.
- [ ] **Stage 9 Gate:** Comprehensive black-box matrix (BB-01..BB-30) clean, 0 test failures, final release signoff.
