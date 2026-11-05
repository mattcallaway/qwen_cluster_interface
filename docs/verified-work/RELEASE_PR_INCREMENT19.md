# Release & Integration Review Specification: Increment 19 (`feature/increment19-general-purpose-coordinator`)

**Draft PR**: [PR #3 (`https://github.com/mattcallaway/qwen_cluster_interface/pull/3`)](https://github.com/mattcallaway/qwen_cluster_interface/pull/3)  
**Feature Branch**: `feature/increment19-general-purpose-coordinator`  
**Base Branch**: `main` (`4be7f547f4919380a22abdd2122961d559d2f46f`; stacked on Increment 18 `269dc8ec5017c1b3033d0aa2e3d035162ca4739b` / Draft PR #2 and Increment 17 `070680432dd12fd9bb43c9669d4d86e499623176` / Draft PR #1)  
**Pre-Fix Independent Review Baselines**:
- `301c15ddb6cf487df9d1d73547cbe9f8def7c5bf` (`4 failed` in [`evidence/increment19-pre-fix-failures-20261104.json`](evidence/increment19-pre-fix-failures-20261104.json))
- `d26cc639ac3f3927956de578cafbe503702425de` (Gap 1 & Gap 2 reproduced in [`evidence/increment19-gap-reproductions-20261104.json`](evidence/increment19-gap-reproductions-20261104.json))  
**Tested Implementation Commit**: `745dc27eff9c3931f1f0a24b18f1876c9528a98d` (tree `f2762447c1c1bcef0e039697d19a4a6688898063`)  
**Rehearsed Integration Merge Commit (onto `main`)**: `93e5d2ac8fce3289615e413b2cd294988db1049f` (tree `f2762447c1c1bcef0e039697d19a4a6688898063`; `385 passed` on integration merge in [`evidence/increment19-integration-rehearsal-20261104.json`](evidence/increment19-integration-rehearsal-20261104.json))

---

## 1. Scope & Capability Boundaries (Demonstrated Evidence vs. Remaining Limits)

1. **Independent Public & Holdout Suite Evaluation and Mandatory Containment Across Every Candidate Execution Path (`F-38` / `F-41` / `F-42` — [`app/services/orchestration/protected_repo_verifier.py`](../../app/services/orchestration/protected_repo_verifier.py), [`app/services/orchestration/tool_catalogue.py`](../../app/services/orchestration/tool_catalogue.py))**:
   - **Independent Public & Holdout Suite Execution (`F-41`)**:
     - `apply_and_verify_repository_patch` never derives or manufactures a `public_tests` result from holdout outcomes. It executes `verify_repository_public_suite_protected` and (when `holdout_test_dir` is supplied) `verify_repository_candidate_protected` as separate, independently nonce-bound verification grants (`public_grant_nonce != holdout_grant_nonce`).
     - Maintains separate structured results for `public_tests`, `holdout_tests`, `containment` (including `public_cleanup_confirmed` and `holdout_cleanup_confirmed`), and overall `accepted`.
     - Each suite result records `suite_id`, `suite_role`, `suite_kind`, `suite_sha256`, `candidate_source_sha256`, `status`, `tests_run`, `expected_test_count`, `failures`, `errors`, `grant_nonce`, `grant_sha256`, and `observations`.
     - Unsupported public suites (`pytest`-only functions/fixtures without `unittest.TestCase`, non-`.py`/`.json` files, missing targets, or syntax errors) return `status="not_evaluated"` with a specific `reason` (never `"passed"`), blocking acceptance.
     - Empty public suites (`expected_test_count == 0` / `tests_run == 0`) return `status="failed"` with `reason="zero_public_tests_discovered"`, blocking acceptance.
     - Cleanup failures (`cleanup_confirmed=False`) return `status="cleanup_unverified"` and block acceptance even when test cases pass.
   - **Mandatory Containment Across Every Candidate Execution Path (`F-42`)**:
     - Removed all host-execution fallbacks for candidate generated code. Every candidate execution entry point — `apply_and_verify_repository_patch` (with or without `holdout_test_dir`), `run_repository_test_suite`, `run_bounded_python_verification`, `execute_and_test_code`, direct tool dispatch, public-only diagnostics, and retry/repair paths — requires an active containment backend before candidate code starts, returning `status="held_containment_backend_required"` (or `"held_protected_backend_required"`) when unconfigured.
     - Backend names, caller-supplied flags, or path substrings alone cannot establish `filesystem_and_network_containment=True` or `protected_verification=True`. `verify_receipt_containment_evidence` verifies backend-owned capability descriptors (`_qc_backend_capabilities`), transient unit names (`qc-sandbox-<32hex>.service`), pinned root image digest (`43b057aa6a7133de2bbed57a8caaeecd07aea9228fa7cb6bec1608286b967b61`), and per-case `cleanup_confirmed=True`. Local black-box test doubles (`make_local_blackbox_isolated_transport`) are explicitly marked `is_uncontained_test_double=True` and `filesystem_and_network_containment=False`.
   - **Independent Regression & Live Containment Evidence**:
     - Verified across all 10 unit/containment scenarios + live Linux root-image containment on `qwen-laptop` (`100.101.187.115`) in [`tests/test_increment19_public_holdout_and_containment.py`](../../tests/test_increment19_public_holdout_and_containment.py) (`10 passed in 23.98s`, [`evidence/increment19-public-holdout-containment-20261104.json`](evidence/increment19-public-holdout-containment-20261104.json)) and all 11 adversarial boundary cases in [`tests/test_increment19_protected_boundary_adversarial.py`](../../tests/test_increment19_protected_boundary_adversarial.py) (`2 passed`, [`evidence/increment19-protected-boundary-live-20261104.json`](evidence/increment19-protected-boundary-live-20261104.json)).

2. **Domain-Neutral Repair Prompts & Multi-Variant Repository Repair Transfer (`F-39` — [`app/services/orchestration/deliberate_loop.py`](../../app/services/orchestration/deliberate_loop.py))**:
   - **Removed Embedded Domain Solutions**: Removed all `batch_allocator` solution expressions (`int(b.get('on_hand', 0)) - int(b.get('reserved', 0))` and `reverse=False`) and inline fault-injection instructions (`keeping the existing sort(reverse=True) line unchanged`) from `deliberate_loop.py`.
   - **Domain-Neutral Repair Prompt (`build_repository_repair_prompt`)**: Supplies only the active requirement text, target file source, public unit tests, public failure traceback, and sanitized high-level failed check names (never hidden holdout expected answers or developer-authored code fixes).
   - **Explicit Fault-Injection Attribution**: Test-specific fault injection uses `injected_first_attempt_candidate` and records `failure_origin` (`"natural_model_failure"`, `"injected_test_fixture"`, or `"simulated_fixture"`).
   - **Preserved First-Attempt `batch_allocator` Evidence + 3 Fresh Transfer Variants (Re-Verified with Independent Public + Holdout Linux Containment)**:
     - Original `batch_allocator` cross-domain E2E (`tests/test_increment19_general_coordinator_transfer_e2e.py` — `1 passed in 75.66s`, [`evidence/increment19-general-coordinator-transfer-20261104.json`](evidence/increment19-general-coordinator-transfer-20261104.json)).
     - **3 Fresh Repository Defect Variants** (`tests/test_increment19_fresh_variants_transfer_e2e.py` — `1 passed in 105.76s`, [`evidence/increment19-fresh-variants-transfer-20261104.json`](evidence/increment19-fresh-variants-transfer-20261104.json)):
       1. `variant_a_boundary_handling` (`sla_credit_calculator/credits.py` — inclusive tier boundaries and downtime clamping).
       2. `variant_b_ordering_selection` (`incident_router/router.py` — `on_duty`/skill/capacity filtering and ascending `(active_incidents, engineer_id)` selection).
       3. `variant_c_state_aggregation_and_repair` (`ledger_reconciler/reconcile.py` — credit/debit aggregation and status/currency filtering, with Attempt 1 `injected_test_fixture` rejection followed by Attempt 2 domain-neutral native model repair via `qwen2.5:3b` and live Linux root-image verification of both public and holdout suites).

3. **Honest Action-Selection & Validation Metrics (`F-40` — [`app/services/orchestration/deliberate_loop.py`](../../app/services/orchestration/deliberate_loop.py))**:
   - Renamed `valid_selections / total_proposals` to `proposal_validation_rate`, returning `"not_evaluated"` (never `1.0`) when `total_proposals == 0`.
   - Recorded `action_origin` (`"template"`, `"caller_supplied_contract"`, `"model_proposal"`, `"controller_policy"`) on every task contract and executed step.
   - Reported `model_tool_selection_accuracy = "not_evaluated"` when actions are template-selected (`action_origin_counts["model_proposal"] == 0`).
   - Separated `proposal_validity`, `action_appropriateness`, `execution_outcome`, and `task_outcome` in `evaluate_next_action_appropriateness` and `run_action_selection_benchmark` (10 independently judged benchmark cases across inspection, testing, retrieval, clarification, waiting, stopping, reporting a blocker, and syntactically valid but inappropriate tool proposals).

4. **Remaining Boundaries**:
   - Task contract decomposition in `ensure_deliberate_task_contracts` uses domain templates (`supplier_option_evaluation`, `repository_bugfix`) unless `explicit_contracts` are supplied (`action_origin="template"` vs `"caller_supplied_contract"`). It does not claim open-ended autonomous multi-step DAG planning by the 3B model.
   - All candidate execution entry points default to `status="held_containment_backend_required"` / `"held_protected_backend_required"` unless an explicit non-production verifier transport (`configure_protected_repo_verifier`) is configured.
   - Production deployment, remote runtime installation, and safety-hold removal remain held.

---

## 2. Reproducible Test Commands & Evidence Links

- **Evidence Bundles (Repository-Relative)**:
  - [`docs/verified-work/evidence/increment19-pre-fix-failures-20261104.json`](evidence/increment19-pre-fix-failures-20261104.json)
  - [`docs/verified-work/evidence/increment19-gap-reproductions-20261104.json`](evidence/increment19-gap-reproductions-20261104.json)
  - [`docs/verified-work/evidence/increment19-public-holdout-containment-20261104.json`](evidence/increment19-public-holdout-containment-20261104.json)
  - [`docs/verified-work/evidence/increment19-protected-boundary-live-20261104.json`](evidence/increment19-protected-boundary-live-20261104.json)
  - [`docs/verified-work/evidence/increment19-fresh-variants-transfer-20261104.json`](evidence/increment19-fresh-variants-transfer-20261104.json)
  - [`docs/verified-work/evidence/increment19-general-coordinator-transfer-20261104.json`](evidence/increment19-general-coordinator-transfer-20261104.json)
  - [`docs/verified-work/evidence/increment19-integration-rehearsal-20261104.json`](evidence/increment19-integration-rehearsal-20261104.json)

- **Reproducible Test Commands**:
  ```powershell
  # 1. Independent Public/Holdout Evaluation & Mandatory Containment Suite (10 tests incl. live Linux root-image sandbox)
  python -m pytest tests/test_increment19_public_holdout_and_containment.py -v --tb=short

  # 2. Review Remediation, Adversarial Coordinator Limits & Protected Boundary Suites (16 tests)
  python -m pytest tests/test_increment19_review_remediation.py tests/test_increment19_coordinator_adversarial_limits.py tests/test_increment19_protected_boundary_adversarial.py -v --tb=short

  # 3. 3 Fresh Repository Defect Variants Live E2E via /api/v1/chat/deliberate + VPSWorkerAgent (qwen2.5:3b) + qwen-laptop Linux Verifier
  python -m pytest tests/test_increment19_fresh_variants_transfer_e2e.py -v --tb=short

  # 4. Cross-Domain Coordinator Transfer Live E2E (Supplier + batch_allocator + Park/Restart/Resume + Steering)
  python -m pytest tests/test_increment19_general_coordinator_transfer_e2e.py -v --tb=short

  # 5. Full 23-File Increment 18, Increment 19 & Application Regression Selection (385 tests)
  python -m pytest tests/test_increment19_public_holdout_and_containment.py tests/test_increment19_review_remediation.py tests/test_increment19_coordinator_adversarial_limits.py tests/test_increment18_repair_regressions.py tests/test_working_memory_adversarial.py tests/test_increment18_deliberate_assistant.py tests/test_stage02_planning.py tests/test_inference.py tests/test_inference_and_observatory_widgets.py tests/test_observatory.py tests/test_metric_integrity.py tests/test_web_routes.py tests/test_worker_authorization_enforcement.py tests/test_script_authorization.py tests/test_certification_block_reporting.py tests/test_claim_contracts.py tests/test_financial_reasoning.py tests/test_code_proposals.py tests/test_protected_verification.py tests/test_migrations.py tests/test_migration_compatibility.py tests/test_migration_robustness_and_backup.py tests/test_paged_kv_authority_and_app_behavior.py -q --tb=short
  ```
