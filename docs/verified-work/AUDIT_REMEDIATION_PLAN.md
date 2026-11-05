# Qwen Collective — Prioritized Audit Remediation Plan (November 5, 2026)

- **Repository**: `C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective-integration`
- **Audited Branch**: `feature/increment19-general-purpose-coordinator` (`b89e63ce372726f65fa650fd2ed30aa917b903eb`)
- **Tested Baseline Code Commit**: `745dc27eff9c3931f1f0a24b18f1876c9528a98d` (`main` base `4be7f547f4919380a22abdd2122961d559d2f46f`)
- **Audit Report**: [`docs/verified-work/COMPREHENSIVE_CODE_FUNCTION_AND_THEORY_AUDIT.md`](COMPREHENSIVE_CODE_FUNCTION_AND_THEORY_AUDIT.md)
- **Findings Register**: [`docs/verified-work/AUDIT_FINDINGS_REGISTER.json`](AUDIT_FINDINGS_REGISTER.json)
- **Capability Matrix**: [`docs/verified-work/AUDIT_CAPABILITY_AND_EVIDENCE_MATRIX.md`](AUDIT_CAPABILITY_AND_EVIDENCE_MATRIX.md)

---

## 1. Reconciled Authoritative Repair Sequence & Subfinding Taxonomy

To eliminate any ordering ambiguity between initial summary notes and the remediation plan, the authoritative repair sequence is fixed as **R1 → R2 → R3 → R4**, and compound findings **`AUD-10`** and **`AUD-11`** are split into independently trackable subfindings:

| Finding / Subfinding | Classification | Assigned Increment | Readiness Gate | Summary |
| :--- | :--- | :---: | :--- | :--- |
| **`AUD-03`** | `confirmed_defect` | **R1** | Merge-Review & Production Blocker | `POST /api/v1/chat/deliberate` lacks CSRF check on cookie sessions, allows reading arbitrary host paths via `workspace_path`, exposes fault-injection parameters (`simulate_first_attempt_defect`, `injected_first_attempt_candidate`), and defaults `use_live_worker=False`. |
| **`AUD-05`** | `confirmed_defect` | **R1** | Merge-Review & Production Blocker | Plaintext vs SHA-256 worker bearer token mismatch in `orchestration.py`/`deliverables.py`; ignored `verify_csrf()` return value in `deliverables.py`; unauthenticated routes (`observatory.py`, `knowledge.py`, `inference.py`) and `/health` route shadowing; `user.get` bug in `knowledge.py`; SQLite connection leaks (`chat.py`, `topology.py`). |
| **`AUD-10b`** | `confirmed_defect` | **R1** | Merge-Review & Production Blocker | Execution containment bypass: `code_sandbox.run_isolated_code_evaluation` executes uncontained host `subprocess.run` when `allow_untrusted_subprocess=True` or `internal_harness_test_only=True` is passed, and test doubles must never authorize runtime execution or protected acceptance. |
| **`AUD-11c`** | `confirmed_defect` | **R1** | Merge-Review & Production Blocker | Research fetch SSRF protection (`vps_agent/research_fetch.py`, `app/services/research/fetcher.py`): pre-resolving DNS before `urlopen` does not validate HTTP redirect targets or bind socket connections to the verified public IP (leaving redirect and DNS-rebinding windows). |
| **`AUD-04`** | `confirmed_defect` | **R2** | Merge-Review & Production Blocker | Legacy provenance/acceptance bypasses: `review_deliverable(decision="accept")` promotes deliverables/artifacts to `'accepted'` and extracts `'supported'` knowledge without verification (`engine.py:1457` skips evaluation); `record_task_execution` completes tasks without leases; `verify_worker_task_authorization` synthesizes `direct_{worker_id}` leases; worker self-verifies metrics via `"execution:..."` prefix; UI routes directly set `'completed'`/`'verified'`/`'supported'`. |
| **`AUD-07`** | `confirmed_defect` | **R2** | Merge-Review & Production Blocker | `t3_publish_change_report` copies `t2`'s worker `lease_id` onto a deterministic controller report and fails verification on public-only runs (`holdout_test_dir=None`), without distinguishing `public_diagnostics_passed` from protected acceptance. |
| **`AUD-10c`** | `confirmed_defect` | **R2** | Merge-Review & Production Blocker | Misleading artifact-status behavior: `ReuseStatus.__eq__` (`artifacts/service.py:42-56`) equates `'queued'` with `'reused'` and `'active'`, and `ReuseId.startswith('ar-')` returns `True` on `'reuse-...'` IDs. |
| **`AUD-01`** | `confirmed_defect` | **R3** | Merge-Review & Production Blocker | Binary `detect_project_domain` routes all non-`repository_bugfix` prompts to `supplier_option_evaluation`, and `_load_project_domain_inputs` silently injects `acceptance_harness.SYNTHETIC_SUPPLIER_QUOTES`. |
| **`AUD-02`** | `confirmed_defect` | **R3** | Merge-Review & Production Blocker | `advance_deliberate_project` uses a hardcoded 6-verifier switch ignoring `val_gate["selected_tool"]`; `ToolCatalogue.execute_tool` has unimplemented fallthroughs (`search_and_retrieve_sources`, `create_or_revise_artifact`, `worker_model_inference`); `validate_proposed_action` lacks deep schema/type/key/bound validation. |
| **`AUD-08`** | `confirmed_defect` | **R3** | Merge-Review & Production Blocker | `financial_ledger.reconcile` fails to check target-grant match category eligibility (`c.category in target.match_eligible_categories / target.eligible_categories`), cash/in-kind match caps, currency consistency, and scoped `rule_evidence_id` verification. |
| **`AUD-10a`** | `confirmed_defect` | **R3** | Merge-Review & Production Blocker | Working-memory index consistency: `working_memory.py:1870` omits `project_epistemic_fts` on fence-failure inserts, and FTS5 retrieval does not exclude `stale`/`superseded` items from current decision context by default. |
| **`AUD-12`** | `confirmed_defect` | **R3** | Merge-Review & Production Blocker | Test suite template coupling: production code imports `SYNTHETIC_SUPPLIER_QUOTES` from `acceptance_harness.py` and lacks negative/cross-domain dispatch tests. |
| **`AUD-06`** | `confirmed_defect` | **R4** | Production-Release Blocker | Lifecycle split between `engine.py` (`orchestration_runs`) and `deliberate_loop.py` (`project_task_contracts`) across steer/park/resume/cancel/complete, and `install_proposal_schema` (`code_proposals.py`) missing from `app/migrations.py`. |
| **`AUD-11a`** | `confirmed_defect` | **R4** | Production-Release Blocker | Worker lease-loss cancellation: `vps_agent/agent.py` checks `lease_lost` only after `execute_leased_task` finishes rather than terminating in-flight worker inference/processes within a defined bound. |
| **`AUD-11b`** | `confirmed_defect` | **R4** | Production-Release Blocker | Remote Linux sandbox cleanup (`app/services/orchestration/linux_sandbox.py`): relies on controller-side SSH `finally` block rather than remote systemd transient timer/service lifetime enforcement independent of the SSH connection. |
| **`AUD-09`** | `architectural_limitation` (sidecar reporting defect fixed in **R4**; optional sidecar/multi-host compute deferred) | **R4** | Truthful Capability Reporting Gate | `QC_PAGED_KV_ENABLED=false` by default; `app/services/inference.py` HTTP sidecar cannot inject KV tensors into external Ollama processes (`authority=None`, `matched_tokens=0`); `LocalSplitPipelineEngine` has local parity (`0.0` diff) without multi-host weight execution. |

---

## 2. Authoritative Repair Increments, Invariants & Acceptance Criteria

### Increment R1 — Security Boundaries and Containment (`AUD-03`, `AUD-05`, `AUD-10b`, `AUD-11c`)
- **Invariants & Acceptance Criteria**:
  1. **Route Authentication & Hashed Worker Tokens (`AUD-05`)**:
     - `app/routes/orchestration.py` and `app/routes/deliverables.py` verify worker bearer tokens via SHA-256 hash (`hashlib.sha256(raw_token.encode()).hexdigest()`) against `worker_auth_tokens` / `worker_tokens`, enforcing token scope, expiry, and revocation.
     - `app/routes/observatory.py`, `app/routes/knowledge.py`, and `app/routes/inference.py` require authenticated user/operator or authorized worker credentials on all operational/data endpoints while preserving deliberately public minimal `/health` and `/api/v1/health` in `app/main.py` without route shadowing.
  2. **CSRF Enforcement (`AUD-03`, `AUD-05`)**:
     - `POST /api/v1/chat/deliberate` (`app/routes/chat.py`) and state-mutating POST routes in `app/routes/deliverables.py` (`lines 321, 355, 387`) enforce `verify_csrf()` for cookie-authenticated sessions and raise `HTTPException(403)` when `verify_csrf()` returns `False`.
  3. **Workspace Path Confinement, Holdout Isolation & Fault-Injection Removal (`AUD-03`)**:
     - Remove `simulate_first_attempt_defect` and `injected_first_attempt_candidate` from external HTTP request handling in `app/routes/chat.py` (and default `use_live_worker=True`).
     - Confine any user-supplied `workspace_path` to configured authorized workspace roots (`Path.resolve(strict=True)` + `os.path.realpath` + `relative_to` check, rejecting traversal `..`, symlinks, and Windows junctions pointing outside the root).
     - Never accept `holdout_test_dir` from external HTTP request payloads; keep holdout suite paths exclusively under verifier/controller configuration.
  4. **Database Connection Hygiene & `knowledge.py` User Fix (`AUD-05`)**:
     - Close SQLite connections via `try/finally: conn.close()` in `app/routes/chat.py` (`chat_project_status`) and `app/services/pipeline_parallel/topology.py`.
     - Fix `user` string handling in `app/routes/knowledge.py:155`.
  5. **Generated-Code Containment Across Every Entry Point (`AUD-10b`)**:
     - Remove `allow_untrusted_subprocess` and `internal_harness_test_only` host-execution bypasses from `app/services/orchestration/code_sandbox.py`.
     - Ensure every candidate code execution entry point (`apply_and_verify_repository_patch`, `run_repository_test_suite`, `run_bounded_python_verification`, `execute_and_test_code`, `code_sandbox.run_isolated_code_evaluation`) requires an active containment backend before candidate code starts, keeps test doubles confined to explicit test wiring (`filesystem_and_network_containment=False`), preserves independent public and holdout execution, blocks acceptance on `not_evaluated` suites, and prevents candidate stdout/files from forging verification receipts.
  6. **SSRF & DNS-Rebinding Protection on Research Fetching (`AUD-11c`)**:
     - In `vps_agent/research_fetch.py` and `app/services/research/fetcher.py`, validate schemes (`http`/`https`), constrain redirects, timeouts, and response byte budgets, re-validate every redirect target against private/loopback/link-local/reserved IPv4 & IPv6 ranges, and pin the actual socket connection to the verified resolved IP address (with proper `Host` header and TLS `server_hostname` SNI) so DNS changes/rebinding between resolution and socket connect cannot reach private networks.
- **Required Regression Suite**: `tests/test_audit_r1_security_and_containment.py`

---

### Increment R2 — Provenance, Acceptance, and Truthful Status (`AUD-04`, `AUD-07`, `AUD-10c`)
- **Invariants & Acceptance Criteria**:
  1. **No Synthetic Leases or Unleased Task Completion (`AUD-04`)**:
     - Remove `direct_{worker_id}` lease synthesis from `app/services/worker.py:192-214`.
     - Require an active, non-expired, non-revoked `worker_task_leases` row bound to `(task_id, worker_id, project_id, attempt)` for `submit_task_result`.
     - Reject unleased completions in `WorkerOllamaProvider.poll` (`app/services/orchestration/model_router.py:325-336`) and prevent legacy `record_task_execution` (`app/services/tasks.py`) from certifying worker inference or bypassing lease provenance.
  2. **No Unverified Deliverable/Artifact/Metric/Capability/Knowledge Acceptance (`AUD-04`)**:
     - Remove `if dd["status"] == "accepted": continue` in `app/services/orchestration/engine.py:1457-1458`.
     - Prevent `review_deliverable(decision="accept")` (`app/services/deliverables.py:238-308`) and legacy UI routes (`projects.py`, `infrastructure.py`, `knowledge.py`, `deliverables.py`) from promoting deliverables/artifacts to `'accepted'`, metrics/capabilities to `'verified'`, or collective knowledge to `'supported'`/`'corroborated'` without independent verification receipts / `certification.native_output_provenance` / `OperatorAuthority`.
     - In `app/services/worker.py:1666-1669`, never mark worker-submitted metrics `'verified'` merely because an `execution:...` string or execution row exists; record worker-reported metrics as `'observed'` / `'unverified'` until independently verified.
  3. **Truthful Controller Provenance & Public-Only Repository Scope (`AUD-07`)**:
     - Declare `t3_publish_change_report` honestly as a deterministic controller artifact (`selected_tool="create_or_revise_artifact"`, `producing_actor="deterministic_controller"`, `lease_id=None`, keeping `referenced_patch_lease_id` only as an explicit dependency reference).
     - For public-only repository work (`holdout_test_dir=None`), permit an accurately scoped diagnostic summary (`verification_scope="public_diagnostics_passed"`) when policy allows, while never promoting `public_diagnostics_passed` into protected acceptance (`verified_for_scope` / `FULFILLED` protected release).
  4. **Truthful Status Types (`AUD-10c`)**:
     - Remove `ReuseStatus.__eq__` and `ReuseId.startswith` overrides in `app/services/artifacts/service.py:42-56` so `'queued'`, `'active'`, and `'reused'` are distinct states.
- **Required Regression Suite**: `tests/test_audit_r2_provenance_and_status.py`

---

### Increment R3 — Grounded Coordination, Tools, Financial Rules, and Memory (`AUD-01`, `AUD-02`, `AUD-08`, `AUD-10a`, `AUD-12`)
- **Invariants & Acceptance Criteria**:
  1. **Grounded Project Inputs & Honest Domain Support (`AUD-01`, `AUD-12`)**:
     - Remove all production imports and fallback uses of `SYNTHETIC_SUPPLIER_QUOTES` in `app/services/orchestration/deliberate_loop.py`.
     - Support domains where real planning, tools, evidence, and verification exist (`supplier_option_evaluation`, `repository_bugfix`, `financial_reconciliation`, `structured_data_analysis`, `research_synthesis`, `web_app_verification`).
     - Require real user-supplied project inputs (or explicit missing-information blockers that transition the project to `awaiting_clarification`), and return `awaiting_clarification` / `unsupported_request` for unsupported or ambiguous requests rather than falling back to supplier evaluation.
     - Validate explicit caller-supplied contracts through the same authorization and capability gates.
  2. **Unified Controlled Tool Dispatch & Deep Argument Validation (`AUD-02`)**:
     - Validate argument types, required fields, nested structures, allowed keys, bounds, and permitted effects in `validate_proposed_action`.
     - Implement safe, working handlers in `ToolCatalogue.execute_tool` for `search_and_retrieve_sources` and `create_or_revise_artifact` (and keep `worker_model_inference` routed through authenticated worker leasing), or mark unavailable tools non-selectable.
     - Dispatch validated tools through one controlled dispatch boundary in `advance_deliberate_project` while keeping verifier selection trusted on the controller.
  3. **Source-Backed Financial & Grant Match Reconciliation (`AUD-08`)**:
     - In `app/services/orchestration/financial_ledger.py`, enforce target-grant match category eligibility (`target.match_eligible_categories` / `target.eligible_categories`), distinguishing direct-expenditure eligibility from match eligibility where rules differ.
     - Enforce cash vs in-kind match share limits (`max_inkind_match_share` / `min_cash_match_share`), currency consistency (`currency`), and reject unknown/unsupported rule fields rather than inventing permissive defaults.
     - Resolve `rule_evidence_id` references against scoped project/input evidence records before claiming `source_backed_compliance=True`.
  4. **Working-Memory FTS5 Consistency & Stale Filtering (`AUD-10a`)**:
     - Synchronize `project_epistemic_fts` across insertion (including fence-failure inserts at `working_memory.py:1870`), invalidation, supersession, and crash recovery.
     - Exclude `stale` and `superseded` epistemic items from default decision/retrieval queries while retaining explicit `include_historical=True` access.
- **Required Regression Suite**: `tests/test_audit_r3_grounding_tools_finance_memory.py`

---

### Increment R4 — Lifecycle Consistency and Operational Recovery (`AUD-06`, `AUD-11a`, `AUD-11b`, `AUD-09`)
- **Invariants & Acceptance Criteria**:
  1. **Unified Authoritative Project Lifecycle & Migrations (`AUD-06`)**:
     - Synchronize project/run lifecycle states across `deliberate_loop.py`, `working_memory.py`, `engine.py`, and `/api/v1/orchestration/*` so steering, pause/park, resume, cancellation, failure, and completion share one authoritative state without competing truths.
     - Integrate `code_proposals` schema installation into `app/migrations.py` (Migration 24) and verify both fresh database creation and migration of representative existing disposable databases.
  2. **Bounded Lease-Loss Cancellation on Worker (`AUD-11a`)**:
     - Propagate `lease_lost` into `vps_agent/agent.py` and `vps_agent/executor.py` so in-flight model HTTP requests and subprocesses terminate within a defined bound when a lease is lost or cancelled, late results are rejected by the controller, and cleanup status is reported accurately.
  3. **Connection-Independent Remote Sandbox Lifetime & Cleanup (`AUD-11b`)**:
     - Enforce remote execution lifetime (`RuntimeMaxSec`) and automatic staging-directory cleanup on `qwen-laptop` independently of the controller SSH connection (using systemd unit `ExecStopPost` / transient cleanup hooks rather than relying solely on a controller-side `finally` or shell trap), and verify cleanup across timeout, partial startup, and simulated SSH disconnect.
  4. **Truthful Paged KV & Pipeline Parallelism Capability Reporting (`AUD-09`)**:
     - Keep `QC_PAGED_KV_ENABLED=false` by default.
     - Update `app/services/inference.py` and `/api/v1/inference/*` capability reporting so telemetry-only/inactive HTTP-sidecar state is explicitly distinguished from in-process `PyTorchPagedKVAdapter` KV reuse, local split-model parity (`LocalSplitPipelineEngine`), and unimplemented multi-host inference.
- **Required Regression Suite**: `tests/test_audit_r4_lifecycle_and_recovery.py`
