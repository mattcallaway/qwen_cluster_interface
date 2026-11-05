# Release & Integration Review Specification: Increment 18 (`feature/increment18-deliberate-task-solving-loop`)

**Draft PR**: [PR #2 (`https://github.com/mattcallaway/qwen_cluster_interface/pull/2`)](https://github.com/mattcallaway/qwen_cluster_interface/pull/2)  
**Feature Branch**: `feature/increment18-deliberate-task-solving-loop`  
**Base Branch**: `main` (`4be7f547f4919380a22abdd2122961d559d2f46f`; stacked on Increment 17 `070680432dd12fd9bb43c9669d4d86e499623176` / Draft PR #1)  
**Pre-Fix Source Review Baseline Commit**: `2cc6dab4e9e4e1c539f024cfe062f55f36732e8b`  
**Tested Implementation Commit**: `ffc771662e31e4665913fe407f8c0e0e934714e1`  
**Disposable Integration Rehearsal Merge Commit**: `0fb2c30a908cbd45191ac24f66aaffff57408776` (`0` merge conflicts; root tree `11bd92eba36ba217806dd2b86d4df58a2a018105`, `app/` tree `764d6fd993b4945ec2269f6f09f184bbcdda6e02`, `tests/` tree `03416c6825003f2b62163264cb05d7f58489a655`)

---

## 1. Scope & Capability Boundaries

1. **Demonstrated Coordinator Scope (Supplier-Specific Pilot)**:
   - On `ffc771662e31e4665913fe407f8c0e0e934714e1`, `advance_deliberate_project` and `ensure_deliberate_task_contracts` in [`app/services/orchestration/deliberate_loop.py`](../../app/services/orchestration/deliberate_loop.py) implement a supplier-specific durable project coordinator decomposing requirements into `t1_reconcile_quotes -> t2_evaluate_constraints -> t3_synthesize_recommendation` with supplier-specific constraint parsing (`extract_constraints_from_ledger`).
   - Generalizing the coordinator beyond supplier-specific branches to arbitrary domains via persisted action contracts and evidence-driven next-action selection is separated into Increment 19 (`feature/increment19-general-purpose-coordinator`).

2. **Actual Controller / Worker Execution Chain**:
   - `POST /api/v1/chat/deliberate` ([`app/routes/chat.py`](../../app/routes/chat.py)) -> `execute_direct_response_turn` (direct questions) or `advance_deliberate_project` (durable projects) -> `execute_native_worker_inference` -> `model_router.select_node` -> `WorkerOllamaProvider` -> `worker.lease_task_for_worker` -> separately running authenticated `VPSWorkerAgent` ([`vps_agent/agent.py`](../../vps_agent/agent.py)) polling `/api/worker/lease` -> local loopback Ollama (`qwen2.5:3b`) -> `/api/worker/tasks/{id}/complete` -> `execute_checkpointed_work_boundary` ([`app/services/orchestration/working_memory.py`](../../app/services/orchestration/working_memory.py)) with controller-signed HMAC-SHA256 `TrustedVerificationReceipt` (`issue_trusted_verification_receipt`).

3. **Distinction Between Component Tests, Developer Reference Fixtures, and Autonomous Outcomes**:
   - **20-Task Acceptance Suite (`accept-01`..`accept-20` in [`app/services/orchestration/acceptance_harness.py`](../../app/services/orchestration/acceptance_harness.py))**: Evaluates **component, deterministic tool, and developer-reference-fixture checks** (`9 unit_or_schema_test`, `6 deterministic_tool_test`, `5 developer_reference_fixture_verification`). `counts_as_autonomous_capability` is `False` for 18 of 20 tasks and `True` only for the 2 native worker inference routing tasks (`accept-10`, `accept-20`). Unmeasured `unsupported_claims` and `unauthorized_side_effects` are recorded as `"not_evaluated"`. **This suite is NOT a claim of 20 autonomous projects completed.**
   - **Autonomous User-Entry-Point Project Outcome (`1` complete live project in [`tests/test_increment18_live_native_project_e2e.py`](../../tests/test_increment18_live_native_project_e2e.py))**: Executes all 10 lifecycle steps (intake, contract decomposition, deterministic tools + live authenticated worker lease, mid-flight steering with atomic revision fence rejection of stale worker commit, cold `uvicorn` controller restart and resume, Attempt 1 verification failure + sanitized 5-step repair + Attempt 2 pass, infeasible constraint `blocked` variant, and authoritative park/cancel cleanup refusal during active lease followed by confirmed cleanup after drain).

4. **Independent Source Review Repairs (`F-31`..`F-36`)**:
   - Pre-fix failures preserved in [`docs/verified-work/evidence/increment18-pre-fix-failures-20261104.json`](evidence/increment18-pre-fix-failures-20261104.json) (`6 failed` on `2cc6dab4e9e4e1c539f024cfe062f55f36732e8b`).
   - Repaired and verified in [`tests/test_increment18_repair_regressions.py`](../../tests/test_increment18_repair_regressions.py) (`10 passed`).

5. **Remaining Limitations on `feature/increment18-deliberate-task-solving-loop`**:
   - Durable-project task branches in `advance_deliberate_project` are supplier-specific prior to Increment 19 generalization.
   - `ToolCatalogue._register_defaults()` registers 9 tools into `GLOBAL_ACTION_REGISTRY` as `ActionKind.CONTROLLER_STEP` without adding them to `task_graph.CONTROLLER_STEPS`, which triggers a drift failure if `tests/test_stage02_planning.py` runs in the same Python process after importing `tool_catalogue` (remediated in Increment 19).
   - Branch `feature/increment18-deliberate-task-solving-loop` was branched from Increment 17 (`070680432dd12fd9bb43c9669d4d86e499623176`), so PR #2 should be merged after PR #1 or reviewed as a stacked branch.
   - Production deployment, remote runtime provisioning, and safety-hold removal remain held.

---

## 2. Reproducible Test Commands & Evidence Links

- **Evidence Bundles (Repository-Relative)**:
  - [`docs/verified-work/evidence/increment18-pre-fix-failures-20261104.json`](evidence/increment18-pre-fix-failures-20261104.json)
  - [`docs/verified-work/evidence/increment18-live-native-project-e2e-20261104.json`](evidence/increment18-live-native-project-e2e-20261104.json)
  - [`docs/verified-work/evidence/increment18-acceptance-and-scaling-20261104.json`](evidence/increment18-acceptance-and-scaling-20261104.json)
  - [`docs/verified-work/evidence/increment18-integration-rehearsal-20261104.json`](evidence/increment18-integration-rehearsal-20261104.json)

- **Reproducible Test Commands**:
  ```powershell
  # 1. Focused Increment 18 Repair Regressions (10 tests)
  python -m pytest tests/test_increment18_repair_regressions.py -v --tb=short

  # 2. Live Native Project E2E via /api/v1/chat/deliberate + VPSWorkerAgent + Ollama qwen2.5:3b (10 steps)
  python -m pytest tests/test_increment18_live_native_project_e2e.py -v --tb=short

  # 3. Working-Memory Adversarial & API Suite (17 tests) + 20-Task Acceptance Suite (8 tests)
  python -m pytest tests/test_working_memory_adversarial.py tests/test_increment18_deliberate_assistant.py -v --tb=short

  # 4. Full 18-File Increment 18 & Broader Application Regression Suite (249 tests)
  python -m pytest tests/test_increment18_repair_regressions.py tests/test_increment18_live_native_project_e2e.py tests/test_working_memory_adversarial.py tests/test_increment18_deliberate_assistant.py tests/test_inference.py tests/test_inference_and_observatory_widgets.py tests/test_observatory.py tests/test_metric_integrity.py tests/test_web_routes.py tests/test_worker_authorization_enforcement.py tests/test_script_authorization.py tests/test_certification_block_reporting.py tests/test_claim_contracts.py tests/test_financial_reasoning.py tests/test_code_proposals.py tests/test_migrations.py tests/test_migration_compatibility.py tests/test_migration_robustness_and_backup.py -q --tb=short
  ```
