# Release & Integration Review Specification: Increment 19 (`feature/increment19-general-purpose-coordinator`)

**Draft PR**: [PR #3 (`https://github.com/mattcallaway/qwen_cluster_interface/pull/3`)](https://github.com/mattcallaway/qwen_cluster_interface/pull/3)  
**Feature Branch**: `feature/increment19-general-purpose-coordinator`  
**Base Branch**: `main` (`4be7f547f4919380a22abdd2122961d559d2f46f`; stacked on Increment 18 `269dc8ec5017c1b3033d0aa2e3d035162ca4739b` / Draft PR #2 and Increment 17 `070680432dd12fd9bb43c9669d4d86e499623176` / Draft PR #1)  
**Pre-Fix Independent Review Baseline Commit**: `301c15ddb6cf487df9d1d73547cbe9f8def7c5bf` (`4 failed` in [`evidence/increment19-pre-fix-failures-20261104.json`](evidence/increment19-pre-fix-failures-20261104.json))  
**Tested Implementation Commit**: `267abc1905e3af2218332a7e5e55466de2648a44` (tree `dcdfde52d57356c7c1c1e1579b86dec36763ceb7`)  
**Rehearsed Integration Merge Commit (onto `main`)**: `82a8fd268791098fa913c6903364537f38f8ac3d` (tree `2230317f7900df7f840adf6039d437cef05fac56`; details in [`evidence/increment19-integration-rehearsal-20261104.json`](evidence/increment19-integration-rehearsal-20261104.json))

---

## 1. Scope & Capability Boundaries (Demonstrated Evidence vs. Remaining Limits)

1. **Protected Repository Verification Boundary (`F-38` — [`app/services/orchestration/protected_repo_verifier.py`](../../app/services/orchestration/protected_repo_verifier.py), [`app/services/orchestration/tool_catalogue.py`](../../app/services/orchestration/tool_catalogue.py))**:
   - **Default Held State**: By default (`_ACTIVE_TRANSPORT = None`), `apply_and_verify_repository_patch` holds execution (`status="held_protected_backend_required"`, `accepted=False`) when `holdout_test_dir` is provided and no protected verification backend is configured. Candidate generated code is never executed on the controller host in the default state.
   - **Separation of Containment Categories**:
     - `run_repository_test_suite` is strictly classified as `"public_development_diagnostics_only"` (`containment_category="process_lifetime_and_output_control"`, `filesystem_and_network_containment=False`, `hidden_oracle_separated=False`, `independent_result_authority=False`, `protected_verification=False`). It rejects `extra_test_dir` (`PermissionError`) so holdout tests can never run in the same Python interpreter as candidate code, and uses an out-of-workspace nonce summary file (`_summary_<nonce>.json`) plus static AST test-count verification to block `SUMMARY:` stdout spoofing and early `os._exit(0)`.
     - Protected acceptance uses `protected_repo_verifier.verify_repository_candidate_protected`, which packages the repository candidate into a `qc.code_bundle.v1` bundle and evaluates declarative black-box `qc.json_io_cases.v1` holdouts (`holdout_cases.json`) through `vps_agent.independent_verifier` (`qc.protected_verifier_grant.v1` / `qc.protected_verifier_receipt.v1`).
     - Only the single-case public input (`/run/qc-input.json`) and read-only source (`/src`) enter the candidate environment. Hidden `expected` oracle values, `strict_json_equal` comparison logic, and the 256-bit HMAC receipt signing key remain outside candidate control.
     - `filesystem_and_network_containment` is reported as `True` **only** when executed through the Linux systemd root-image sandbox (`linux_systemd_root_image_sandbox` on `qwen-laptop` `100.101.187.115`, pinned squashfs image `43b057aa6a7133de2bbed57a8caaeecd07aea9228fa7cb6bec1608286b967b61`), and `False` on `local_blackbox_process_isolated_oracle`.
   - **Adversarial Boundary Verification**: Verified against all 10 required hostile candidate behaviors + valid passing candidate in [`tests/test_increment19_protected_boundary_adversarial.py`](../../tests/test_increment19_protected_boundary_adversarial.py) (`2 passed in 31.64s`), with live Linux root-image receipts preserved in [`evidence/increment19-protected-boundary-live-20261104.json`](evidence/increment19-protected-boundary-live-20261104.json).

2. **Domain-Neutral Repair Prompts & Multi-Variant Repository Repair Transfer (`F-39` — [`app/services/orchestration/deliberate_loop.py`](../../app/services/orchestration/deliberate_loop.py))**:
   - **Removed Embedded Domain Solutions**: Removed all `batch_allocator` solution expressions (`int(b.get('on_hand', 0)) - int(b.get('reserved', 0))` and `reverse=False`) and inline fault-injection instructions (`keeping the existing sort(reverse=True) line unchanged`) from `deliberate_loop.py`.
   - **Domain-Neutral Repair Prompt (`build_repository_repair_prompt`)**: Supplies only the active requirement text, target file source, public unit tests, public failure traceback, and sanitized high-level failed check names (never hidden holdout expected answers or developer-authored code fixes).
   - **Explicit Fault-Injection Attribution**: Test-specific fault injection uses `injected_first_attempt_candidate` and records `failure_origin` (`"natural_model_failure"`, `"injected_test_fixture"`, or `"simulated_fixture"`).
   - **Preserved First-Attempt `batch_allocator` Evidence + 3 Fresh Transfer Variants**:
     - Original `batch_allocator` cross-domain E2E (`tests/test_increment19_general_coordinator_transfer_e2e.py` — `1 passed in 58.55s`, [`evidence/increment19-general-coordinator-transfer-20261104.json`](evidence/increment19-general-coordinator-transfer-20261104.json)).
     - **3 Fresh Repository Defect Variants** (`tests/test_increment19_fresh_variants_transfer_e2e.py` — `1 passed in 62.88s`, [`evidence/increment19-fresh-variants-transfer-20261104.json`](evidence/increment19-fresh-variants-transfer-20261104.json)):
       1. `variant_a_boundary_handling` (`sla_credit_calculator/credits.py` — inclusive tier boundaries and downtime clamping).
       2. `variant_b_ordering_selection` (`incident_router/router.py` — `on_duty`/skill/capacity filtering and ascending `(active_incidents, engineer_id)` selection).
       3. `variant_c_state_aggregation_and_repair` (`ledger_reconciler/reconcile.py` — credit/debit aggregation and status/currency filtering, with Attempt 1 `injected_test_fixture` rejection followed by Attempt 2 domain-neutral native model repair via `qwen2.5:3b` and live Linux root-image verification).

3. **Honest Action-Selection & Validation Metrics (`F-40` — [`app/services/orchestration/deliberate_loop.py`](../../app/services/orchestration/deliberate_loop.py))**:
   - Renamed `valid_selections / total_proposals` to `proposal_validation_rate`, returning `"not_evaluated"` (never `1.0`) when `total_proposals == 0`.
   - Recorded `action_origin` (`"template"`, `"caller_supplied_contract"`, `"model_proposal"`, `"controller_policy"`) on every task contract and executed step.
   - Reported `model_tool_selection_accuracy = "not_evaluated"` when actions are template-selected (`action_origin_counts["model_proposal"] == 0`).
   - Separated `proposal_validity`, `action_appropriateness`, `execution_outcome`, and `task_outcome` in `evaluate_next_action_appropriateness` and `run_action_selection_benchmark` (10 independently judged benchmark cases across inspection, testing, retrieval, clarification, waiting, stopping, reporting a blocker, and syntactically valid but inappropriate tool proposals).

4. **Remaining Boundaries**:
   - Task contract decomposition in `ensure_deliberate_task_contracts` uses domain templates (`supplier_option_evaluation`, `repository_bugfix`) unless `explicit_contracts` are supplied (`action_origin="template"` vs `"caller_supplied_contract"`). It does not claim open-ended autonomous multi-step DAG planning by the 3B model.
   - Protected repository verification defaults to `status="held_protected_backend_required"` unless an explicit non-production verifier transport (`configure_protected_repo_verifier`) is configured.
   - Production deployment, remote runtime installation, and safety-hold removal remain held.

---

## 2. Reproducible Test Commands & Evidence Links

- **Evidence Bundles (Repository-Relative)**:
  - [`docs/verified-work/evidence/increment19-pre-fix-failures-20261104.json`](evidence/increment19-pre-fix-failures-20261104.json)
  - [`docs/verified-work/evidence/increment19-protected-boundary-live-20261104.json`](evidence/increment19-protected-boundary-live-20261104.json)
  - [`docs/verified-work/evidence/increment19-fresh-variants-transfer-20261104.json`](evidence/increment19-fresh-variants-transfer-20261104.json)
  - [`docs/verified-work/evidence/increment19-general-coordinator-transfer-20261104.json`](evidence/increment19-general-coordinator-transfer-20261104.json)
  - [`docs/verified-work/evidence/increment19-integration-rehearsal-20261104.json`](evidence/increment19-integration-rehearsal-20261104.json)

- **Reproducible Test Commands**:
  ```powershell
  # 1. Pre-Fix Review Remediation Regressions (4 tests)
  python -m pytest tests/test_increment19_review_remediation.py -v --tb=short

  # 2. Adversarial Protected Boundary Suite — Local + Live Linux Root-Image Sandbox on qwen-laptop (2 tests / 11 live boundary cases)
  python -m pytest tests/test_increment19_protected_boundary_adversarial.py -v --tb=short

  # 3. 3 Fresh Repository Defect Variants Live E2E via /api/v1/chat/deliberate + VPSWorkerAgent (qwen2.5:3b) + qwen-laptop Linux Verifier
  python -m pytest tests/test_increment19_fresh_variants_transfer_e2e.py -v --tb=short

  # 4. Cross-Domain Coordinator Transfer Live E2E (Supplier + batch_allocator + Park/Restart/Resume + Steering)
  python -m pytest tests/test_increment19_general_coordinator_transfer_e2e.py -v --tb=short

  # 5. Full 21-File Increment 18, Increment 19 & Application Regression Selection (352 tests)
  python -m pytest tests/test_increment19_review_remediation.py tests/test_increment19_coordinator_adversarial_limits.py tests/test_increment18_repair_regressions.py tests/test_working_memory_adversarial.py tests/test_increment18_deliberate_assistant.py tests/test_stage02_planning.py tests/test_inference.py tests/test_inference_and_observatory_widgets.py tests/test_observatory.py tests/test_metric_integrity.py tests/test_web_routes.py tests/test_worker_authorization_enforcement.py tests/test_script_authorization.py tests/test_certification_block_reporting.py tests/test_claim_contracts.py tests/test_financial_reasoning.py tests/test_code_proposals.py tests/test_protected_verification.py tests/test_migrations.py tests/test_migration_compatibility.py tests/test_migration_robustness_and_backup.py -q --tb=short
  ```

