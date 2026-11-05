# Verified implementation overlay

Supplement to the canonical [TECHMAP](../../TECHMAP.md), its [legacy pointer](../../TECH_MAP.md), and [work register](README.md). Status: development candidate, 2026-11-04; not deployed. Existing diagrams describe intended architecture and must not be read as proof of current operational capability.

## Implementation and evidence boundaries

| Path | Real implementation | Verification boundary |
|---|---|---|
| Objective → plan → task | `app/services/orchestration/task_graph.py`, `engine.py`, `model_router.py` | Native actions are `inference`, fixed `library_script`, `controller_step`, `research_fetch`; no general multi-file coding action |
| Task → lease → worker | `app/services/worker.py`, `vps_agent/agent.py`, `executor.py` | Worker identity, restrictions, assignment/lease fencing; holds remain |
| Native provenance | `app/services/orchestration/certification.py` | Controller DB joins exact output hashes; not authority against a DB administrator |
| Retrieval and recovery | `research/native_retrieval.py`, `retrieval_attempts.py`, `vps_agent/research_fetch.py`, `research_job.py` | Worker/observer receipt, reservation, deadlines, retry limits; retrieval truth is not claim truth |
| Evidence and applicability | `research/feedback.py`, `claim_contracts.py` | Append-only records, scoped assertion cards, exact canonical versions, invalidation; no general entailment |
| Financial reasoning | `research/financial_reasoning.py`, `orchestration/financial_ledger.py` | Exact rules → typed Decimal derivation; no prose interpretation or commitment authentication |
| Model code drafting | Existing inference action | Can produce candidate text; not evidence that code works |
| Code proposal contract/staging | `orchestration/code_proposals.py` | Exact native task/artifact, approved plan/requirements/tests, closed paths, separate staging trees; never executes |
| Code validation | `orchestration/evaluator.py`, `code_sandbox.py` | Static helper remains diagnostic; authoritative software admission is BLOCKED pending independent native acceptance |
| Reviewed web pilot | `orchestration/software_pilot.py`, `script_library.py:web_status_pilot` | Fixed synthetic HTML emitted by reviewed code; no arbitrary code permission or live data |
| Worker output | `vps_agent/executor.py` | Physical files and exit status protected from stdout overwrite; application claims retained under `application_output` |
| Online script authorization | `orchestration/script_authorization.py`, `app/routes/worker.py`, `vps_agent/script_authorization.py`, `agent.py` | Authenticated controller HMAC/lease/plan validation, one-shot durable start gate, short-lived worker permit; reviewed library scripts only |
| Process lifetime/output | `vps_agent/bounded_process.py`, `windows_job.py`, `process_job.py`, `executor.py` | Kernel Windows job, deadline/watchdog, bounded streams, descendant cleanup and cancellation; unsupported backends fail closed |
| Linux candidate | `vps_agent/linux_sandbox.py`, `code_proposals.py:prepare_linux_plan`, `scripts/probe_linux_sandbox.py` | Root-image/cgroup service policy, explicit operator harness, version-bound diagnostic plan; not connected to worker/generated-code dispatch or production |
| Actual Linux evidence | `docs/verified-work/evidence/laptop-linux-*.json`, reviewed rehearsal harnesses | Real laptop fixtures and manager/kernel observations; manual diagnostic evidence, never a substitute for native generated-code provenance |
| VPS launcher | Existing `scripts/qwen-sandbox-runner.py` in original repository | Source inspected; installation/root ownership/actual confinement not reverified on VPS |
| Searchable Paged KV Cache | `app/services/paged_kv/{hasher,page_table,store,manager,identity,model_loader,pytorch_adapter}.py`, `app/services/inference.py` | 16-token page tables, chained SHA-256 hashes, Radix tree prefix matching; live block pinning/generation tracking, COW forking (`copy_on_write_block`), transactional rollback on failed ingest (`F-24`), canonical JSON namespace encoding (`ExecutionNamespace/1.0`), `ModelIdentityManifest` runtime identity binding, `QCKVP2` mandatory HMAC-authenticated envelopes, fail-closed 256-bit storage keys, RAM and disk byte quotas (`max_ram_bytes`, `max_disk_bytes`), 3-tier `IdentityIssuanceAuthority`, `TrustedLocalModelLoader` pinned local artifact SHA-256 verification and live model-instance binding (`F-26`), controlled application modes (`QC_PAGED_KV_ENABLED=false` default), and local GPU `PyTorchPagedKVAdapter` (`DynamicCache` reuse, `F-21`/`F-25`). Closed-binary Ollama KV injection remains unimplemented. |
| Distributed Pipeline Parallelism | `app/services/pipeline_parallel/{activation_protocol,topology,runner,socket_transport,stage1_server,pytorch_split_engine}.py` | WireGuard activation frame transport (`QCACT1`) with strict timestamp freshness, dtype/finite float validation, and per-session `token_index` ordering/replay rejection (`F-22`); bounded TCP server (`max_concurrent_sessions`, `client_timeout_s`, mid-frame truncation detection, `F-23`); in-tree `LocalSplitPipelineEngine` verified on local GTX 1650 SUPER (`Qwen/Qwen2.5-0.5B-Instruct`, 2-stage and 3-stage bit-exact parity); remote split execution with real transformer weights remains unimplemented (`qwen-laptop` lacks PyTorch/CUDA). |
| Orchestrator Verification DAG | `app/services/orchestration/pipeline_kv_diagnostics.py`, `engine.py` | Controller step for live algorithmic and adversarial fault containment diagnostics; certified within synthetic prototype scope |

Source paths under `research/` and `orchestration/` are relative to `app/services/`. The worker fixture tests and local browser rehearsal are not a controller-generated model coding run.

## Intended coding loop and missing links

```text
Approved task + immutable input revision
  → native inference → versioned file bundle (review primitive implemented; scheduler action still missing)
  → validate paths, size, approved inventory and bundle hash
  → stage proposal/ and acceptance/ separately (non-executing primitive implemented)
  → verified OS build/test environment (not provisioned by this increment)
  → native execution receipt + physical output versions
  → independent behavior/browser/security checks
  → bounded repair proposal → fresh version + revalidation
  → separately approved staging → separately approved production release
```

## Observed blockers before live execution

- The code acceptance helper can pass static checks; this candidate adds a mandatory hold at authoritative software admission. A new trusted acceptance receipt must be designed, not supplied as a model flag.
- Increment 12 closes the earlier Class B manifest-start gap through fresh online controller verification. Increment 13 adds cancellation on lease/network denial and rechecks current script authority during lease renewal. This is not instant revocation or an offline signature verifier. Full confinement and the Linux cgroup backend remain blockers; do not broaden live permissions yet.
- `code_sandbox.py` reports no independently verified OS backend. Temporary directories, environment scrubbing and development subprocesses are not security boundaries.
- Worker artifact collection rejects symlinks/hardlinks and oversized files and limits count/bytes. Increment 13 collects only after confirmed process cleanup and adds bounded output/process lifetime on Windows. Collection remains top-level; race-resistant reads, disk quotas and a verified Linux backend require follow-up. Hostile-code artifact handling is not certified.
- Increment 15 used approved laptop SSH access and isolated transient test units. Existing worker/model/proxy PIDs were preserved, and the temporary host policy was removed. No VPS deployment, existing-service restart or controller-registry/hold change was performed. Live production controller inventory remains unrefreshed.

## Local pilot acceptance

The reviewed library fixture writes `index.html` and `result.json` in a temporary development workspace. The local rehearsal records `native_execution=false`, `generated_by_collective=false`, `os_containment_verified=false`, and `certification_eligible=false`. A separate Chromium rehearsal checks task inventory/filter/reset, page errors, mobile width and lack of HTTP requests. This demonstrates the fixture and browser-test mechanics, not autonomous software delivery.

## Versioned proposal boundary (increment 11)

`qc.software_contract.v1` pins requirement IDs/descriptions, exact allowed source files and canonical test artifact ID/version/hash. `qc.code_bundle.v1` contains only path/content records. `qc.independent_tests.v1` records the approved requirement inventory and separate test files. These are packaging/traceability schemas, not proof of test adequacy or independent authorship.

`CodeProposalReview` reads the current approved plan and canonical CAS versions, requires the exact native inference output task/lease/worker and producer plan key, and records immutable review/staging entries. Changed inputs require fresh review. `stage()` checks a recorded review twice and writes only a new directory under an explicitly chosen existing root. No imports, installation, tests or service start occur. `install_proposal_schema()` is development-only and is not a production startup migration. No API route or automatic scheduler action is activated.

Authoritative software admission records fresh proposal diagnostics for `qc.software_contract.v1` and retains BLOCKED status. The [protocol reference](CODE_PROPOSALS.md) describes schemas and developer use.

The staging tree protects approved test bytes from replacement within the proposal format. It does not prevent arbitrary code from attacking tests once executed in a shared process; verified confinement and independent result collection are still required. Filesystem concurrency/host-administrator attacks remain outside this staging primitive's protection.

## Reviewed-script authorization boundary (increment 12)

The new worker route is authenticated by the existing per-node bearer identity. `authorize_start()` rereads current policy, active native lease and approved library task in one database transaction. It verifies the controller manifest HMAC and code bytes, rejects capabilities/network/timing mismatches, and commits an append-only start record before responding. `ScriptPermit` checks task/manifest hashes and nonce binding, uses a monotonic deadline, and is consumed once by the executor. No controller signing secret is shipped to the worker or child.

Server authorization and local script advertisement both default off, and schema installation is explicit development work. Existing holds are not modified. The isolated agent test uses the actual lease → authorize → reviewed-code subprocess → submit path against TestClient; the subprocess fallback is a reviewed-fixture test mechanism and does not prove OS isolation, autonomous coding or live VPS readiness. See [execution protocol](EXECUTION_PROTOCOL.md) for exact limits and the future build/test boundary.

## Process supervision boundary (increment 13)

`executor.py` now calls the bounded supervisor rather than collecting unlimited output through `subprocess.run(capture_output=True)`. Windows job accounting and bounded stream drains determine cleanup; timeout, output overflow, cancellation, orphaned descendants and uncertain cleanup are failures. The heartbeat cancellation event propagates into this runner, and the controller rechecks script authority on lease renewal. Workspaces are retained when cleanup cannot be established, and deletion failures cannot produce success/artifacts.

This implementation verifies process lifetime on Windows, not filesystem/network containment. Linux execution is held where no verified process-tree backend exists; the source-inspected VPS launcher still needs cgroup-wide deadline/kill/emptiness verification. See [process control reference](PROCESS_CONTROL.md).

## Linux candidate and production gates (increment 14)

`LinuxSpec` derives a fixed non-shell Python service command with a pinned read-only root image/source, private network, limits and cgroup-wide stop policy. `SystemdHost` checks protected inputs and queries manager/kernel state; `run_linux_candidate` bounds captures and refuses to treat client exit or application output as cleanup/acceptance authority. It is an explicit operator harness, not a runtime-selected worker backend. All returned results retain `backend_verified=false` and `accepted=false`.

`prepare_linux_plan()` reloads a canonical native proposal review and approved test bindings, records service argv/image/source hashes and keeps dispatch/certification/production false. No production startup migration is introduced. At increment 14 the Linux target was unavailable; increment 15 supplies a selected real laptop fixture scope. Production fleet readiness and general hostile-code/verifier acceptance remain unverified. See [Linux reference](LINUX_SANDBOX.md) and [release/rollback gates](PRODUCTION_RELEASE.md).

## Actual laptop fixture scope (increment 15)

The user supplied the existing node access context. SSH key/known-host verification selected the laptop, and a small read-only image was built from installed Python tools without package installation. Real fixtures observed root-image/source/network restrictions, bounded scratch, memory/OOM/PID limits, deadline/output/orphan cleanup and service expiry after client loss. All ten test cgroups were empty/absent at reconciliation; the temporary policy was removed and existing service PIDs were unchanged. No model or native generated-code project ran.

The backend now handles asynchronous startup and a terminal cleared cgroup path only with prior identity/owner evidence; unknown identity remains blocked. See [laptop verification](LAPTOP_VERIFICATION.md). Linux candidate defaults and certification holds remain unchanged; these operator receipts do not satisfy a native execution/certification chain.

## Protected code verification bridge — increment 16

`engine.verify_native_code_proposal()` -> `CodeProposalReview.capture_native_response()` / current approved review -> `ProtectedVerification` -> explicitly injected authenticated transport -> root `vps_agent.independent_verifier` -> bounded `linux_sandbox` service -> signed observations -> controller recomputation and current-input revalidation.

Only public input and read-only source enter the application environment. Hidden expected answers, private audit storage and receipt key stay outside. Native origin labels alone are insufficient: source bytes must match the immutable worker execution response. The transport is unconfigured by default and the API rejects production mode. No automatic scheduler route or certification override was added. See [protocol](PROTECTED_VERIFIER.md) and [test evidence](evidence/INCREMENT_16_PROTECTED_CODE_VERIFICATION.md).

## Distributed Pipeline Parallelism & Paged KV Virtual Memory — Increment 17

### Release Validation & Rehearsal Summary
- **Rehearsed `origin/main` Base SHA:** `4be7f547f4919380a22abdd2122961d559d2f46f`
- **Tested Implementation Commit SHA:** `53576c16ef96f64797478825b38b00d625748b25` (`app/` tree `b36d6357a34412532a3c8c66cdff92db00e7879e`, `tests/` tree `cd1a438eccb5e2c08f688b3f1857077b0b3f8d17`)
- **Tested Disposable Integration Rehearsal Merge SHA:** `1b4bf42d43097546371544e9ae7233ebcf98cbe2` (`279 passed, 0 failed`: 18 focused Increment 17 test files with `136 passed in 83.85s` + 12 broader application regression test files with `143 passed, 1 warning in 59.63s`; tracked in [`evidence/increment17-integration-rehearsal-20261103.json`](evidence/increment17-integration-rehearsal-20261103.json))

### Architecture & Dataflow
1. **Paged KV Cache Virtual Memory Subsystem (`app/services/paged_kv/`)**:
   - Fixed 16-token page virtualization mapping sequence token blocks to physical memory pages.
   - Radix tree prefix lookups keyed by SHA-256 execution prefix hash chains.
   - Cryptographic binary storage envelope `QCKVP2` (186-byte header) enforcing HMAC-SHA256 integrity, per-block key verification, namespace bounds, and bounded decompression limits.
   - Immutable RFC 8785 canonical JSON execution namespaces (`ExecutionNamespace/1.0`) preventing delimiter injection and cross-tenant cache bleeding.
   - Fail-closed 256-bit storage keys (`validate_storage_key()`) without hardcoded or shared fallbacks.
   - Byte-accurate quota enforcement covering RAM and disk, with live-block reference count pinning.
   - Re-entrant thread synchronization (`threading.RLock()`) protecting `PagedKVManager` state mutations across concurrent queries (`F-20`).

2. **Complete Authority Boundary & Real-Artifact Loader Binding (Findings F-17, F-19 & F-26)**:
   - 3-tier identity model: Declared Manifest $\to$ Approved Registry $\to$ Issued Reuse Authority.
   - Enforced non-empty artifact sets covering all 4 core roles (`weights`, `tokenizer`, `chat_template`, `rope_config`).
   - Manager requires an explicitly configured `trusted_issuer` (`IdentityIssuanceAuthority`); unverified authority tokens, unknown issuers, and revoked authorities are rejected fail-closed.
   - Live disk artifact digest verification (`verify_live_artifacts()`) on reuse and sequence restoration from disk, plus [`TrustedLocalModelLoader`](../../app/services/paged_kv/model_loader.py) streaming SHA-256 pre/post-load verification and live in-memory model instance binding (`require_instance_binding=True`) to [`PyTorchPagedKVAdapter`](../../app/services/paged_kv/pytorch_adapter.py).

3. **Distributed Pipeline Parallelism Transport & Multi-Node Execution (`app/services/pipeline_parallel/`)**:
   - Authenticated binary frame streaming protocol (`QCACT1`) over WireGuard tunnels with HMAC-SHA256 frame integrity.
   - Dedicated TCP streaming socket transport ([`socket_transport.py`](../../app/services/pipeline_parallel/socket_transport.py), [`stage1_server.py`](../../app/services/pipeline_parallel/stage1_server.py)) for low-overhead multi-node activation streaming.
   - Live multi-node synthetic transport verification (`Desktop -> qwen-laptop:8990` over Tailscale LAN): `5.17 ms` p50 / `5.76 ms` mean RTT (`173.6` synthetic frame steps/s) with verified HMAC tamper rejection and bounded session concurrency (`F-23`). *(Supersedes earlier phrasing: `qwen-laptop` ran simulated stage callbacks, not real Stage 1 transformer weights or LM head projection.)*

4. **Real Capability Classification Boundary**:
   - **Local Simulated Stages**: Fully operational (deterministic mathematical layer simulation).
   - **Remote Transport of Synthetic Activations**: Fully operational (`396.5` steps/s loopback, `368.3–374.2` steps/s across 4–16 sessions, `173.6` synthetic frame steps/s over live Tailscale LAN; synthetic frame steps/s are never model generation tokens/s).
   - **Local Split Execution Using Real Weights**: Fully operational on NVIDIA GeForce GTX 1650 SUPER (`Qwen/Qwen2.5-0.5B-Instruct` via [`LocalSplitPipelineEngine`](../../app/services/pipeline_parallel/pytorch_split_engine.py)), bit-exact numerical parity verified (`max_logit_diff = 0.000000`, 100% greedy token agreement across 2-stage and 3-stage partitions; local partition equivalence does not establish distributed speedup, and split decode recomputes the prefix with `use_cache=False`).
   - **Remote Split Execution with Real Weights**: Not implemented / held (`qwen-laptop` lacks PyTorch/CUDA; runtime installation prohibited).
   - **Local Genuine KV-Tensor Reuse**: Fully operational locally via [`TrustedLocalModelLoader`](../../app/services/paged_kv/model_loader.py) and [`PyTorchPagedKVAdapter`](../../app/services/paged_kv/pytorch_adapter.py) (`F-21`, `F-25`, `F-26`) on NVIDIA GeForce GTX 1650 SUPER (`Qwen/Qwen2.5-0.5B-Instruct` revision `7ae557604adf67be50417f59c2c2f167def9a775`). Maps 16-token page blocks to Hugging Face `DynamicCache`; demonstrated `3.41x–4.77x` warmed RAM prefill speedup (`1.14x–1.20x` end-to-end 16-token generation speedup; `0.003906`–`0.051` FP16 SDPA max logit diff within tolerance, `0.000031` in FP32, 100% greedy token agreement; warm-prefill gains do not establish equivalent end-to-end request gains).
   - **Production Inference Integration**: Disabled by default (`QC_PAGED_KV_ENABLED=false`); sidecar token-block accounting only when enabled. Closed-binary Ollama/llama.cpp HTTP servers do not expose internal KV tensor buffers for external injection; production activation remains held.

## Deliberate General-Purpose Assistant & Task-Solving Loop — Increment 18

### Architecture & Implementation Boundary
1. **Persisted Task-Solving Cycle ([`deliberate_loop.py`](../../app/services/orchestration/deliberate_loop.py))**:
   - Implements the single persisted cycle `Understand -> clarify material gaps -> plan -> select next action -> retrieve context -> execute -> inspect results -> verify -> revise or deliver`.
   - Classifies incoming requests (`classify_request_mode`) into **Direct-Response Mode** (for single-turn factual/explanatory questions, creating zero project rows or artifacts) or **Durable-Project Mode** (for multi-constraint, multi-deliverable assignments via `/api/v1/chat/deliberate`).
   - Implements the 7-step adaptive escalation ladder (`ESCALATION_LADDER`), role-based model routing (`select_model_for_role` across `qwen2.5:3b` and `qwen2.5:1.5b`), truncation detection (`detect_generation_truncation`), 5-step failure repair with hidden oracle redaction (`build_sanitized_repair_feedback`), and scoped procedure learning with revocation (`record_procedure_outcome`, `query_verified_procedures`, `revoke_procedure_outcome`).
2. **Durable Requirement Ledger & Steering ([`intake.py`](../../app/services/orchestration/intake.py), Migration 20)**:
   - Stores structured requirements (`project_requirement_items`) and immutable revisions (`project_requirement_revisions`) tracking `objective`, `deliverable`, `acceptance_criterion`, `hard_constraint`, `soft_preference`, `exclusion`, `user_decision`, `assumption`, and `missing_info`.
   - Detects mutually exclusive constraints and material missing parameters before execution (`detect_conflicts_and_ambiguities`).
   - Distinguishes mid-flight user steering (`apply_user_steering_update(action="steer")`) from cancellation (`action="cancel"`), selectively invalidating only affected downstream tasks/artifacts while preserving unaffected verified artifacts.
   - Enforces source/instruction separation (`inspect_untrusted_source_for_directive_injection`), stripping prompt-injection directives from untrusted sources and rejecting requirement mutations originating from `untrusted_document`.
3. **Durable Project Memory & Context Fidelity ([`context_manager.py`](../../app/services/orchestration/context_manager.py))**:
   - Persists `project_memory_state` across restarts and invalidates downstream decisions when upstream inputs change (`invalidate_downstream_memory_on_input_change`).
   - Fixes missing claim `verification_status` so unverified claims default to `"UNKNOWN"` / `"unknown"` (never `"SUPPORTED"`).
   - Preserves negations, dates, units, and uncertainty qualifiers during prompt budget truncation (`_smart_truncate_preserving_qualifiers`).
4. **Typed Tool Catalogue ([`tool_catalogue.py`](../../app/services/orchestration/tool_catalogue.py))**:
   - Registers 10 typed tools across 7 capability domains (`document_and_repo_inspection`, `search_and_source_retrieval`, `calculation_and_data_analysis`, `code_generation_and_testing`, `browser_and_webapp_inspection`, `artifact_creation_and_revision`, `operational_and_system_inspection`), including exact `Decimal` multi-revision quote resolution (`resolve_quote_revisions`), total cost breakdown (`calculate_option_total_cost`), hard/soft constraint evaluation (`compare_options_against_constraints`), WindowsJob-bounded Python verification (`run_bounded_python_verification`), web application DOM/interaction verification (`verify_web_application_behavior`), and structured dataset outlier analysis (`analyze_structured_dataset`).
5. **Live Database-Backed Working Memory & Independent Review Hardening ([`working_memory.py`](../../app/services/orchestration/working_memory.py), Migrations 21–22)**:
   - Implements the 3-level coordinated memory architecture: **Level A** (`WorkingSetCache` & `assemble_scoped_work_packet` with pinned project intent/constraints that are never silently truncated), **Level B** (SQLite WAL tables: `project_task_contracts`, `project_epistemic_items`, `project_epistemic_fts`, append-only `project_working_checkpoints`, and two-phase `project_artifact_publication_journal`, extended in Migration 22 with `cleanup_lifecycle_state`, `dispatch_stopped`, `contract_revision`, `bound_requirements_snapshot_json`, `input_artifacts_snapshot_json`, `verification_receipt_json`, and `attempt_id`), and **Level C** (CAS artifacts referenced by exact `artifact_id`, `version`, and `sha256`).
   - Derives the authoritative project state (`derive_authoritative_project_state`) directly from SQLite tables across all 8 required questions and all 8 epistemic categories.
   - Enforces explicit task contracts (`register_task_contract`, `verify_project_task_integration`), 7-step checkpointed work boundaries (`execute_checkpointed_work_boundary`) with controller-signed HMAC verification receipts (`issue_trusted_verification_receipt`, `validate_trusted_verification_receipt`, `F-34`), atomic `BEGIN IMMEDIATE` compare-and-swap revision fencing (`StaleWorkerResultError`, `F-35`), crash journal reconciliation (`reconcile_crash_publication_journal`), dependency-closure invalidation (`invalidate_dependency_closure`), 7 operational working-memory queries + project-isolated FTS5 search (`WorkingMemoryRetriever`), authoritative park/resume/cancel lifecycle (`inspect_authoritative_execution_cleanup`, `ParkOrCancelCleanupError`, `F-33`), and parallel field ownership rules (`commit_parallel_task_update`).
6. **Release Validation, Integration Rehearsal & Scope Boundaries**:
   - **Tested Implementation Commit:** `ffc771662e31e4665913fe407f8c0e0e934714e1` (pre-fix baseline `2cc6dab4e9e4e1c539f024cfe062f55f36732e8b`; rehearsed integration merge `0fb2c30a908cbd45191ac24f66aaffff57408776` onto `origin/main` `4be7f547f4919380a22abdd2122961d559d2f46f` with `0` conflicts; Draft [PR #2](https://github.com/mattcallaway/qwen_cluster_interface/pull/2)).
   - **Supplier-Specific Coordinator Scope:** In Increment 18 (`ffc771662e31e4665913fe407f8c0e0e934714e1`), `advance_deliberate_project` and `ensure_deliberate_task_contracts` in `deliberate_loop.py` implement a supplier-specific task graph (`t1_reconcile_quotes -> t2_evaluate_constraints -> t3_synthesize_recommendation`); generalizing the coordinator to arbitrary domains via persisted action contracts is separated into Increment 19.
   - **Component/Fixture Suite vs. Live Autonomous Project:** The 20-task acceptance suite (`accept-01`..`accept-20`) evaluates component, deterministic tool, and developer-reference-fixture checks (`9 unit_or_schema_test`, `6 deterministic_tool_test`, `5 developer_reference_fixture_verification`; `counts_as_autonomous_capability=False` for 18 of 20 tasks — not 20 autonomous projects). Autonomous user-entry-point execution is demonstrated by the 1 complete 10-step live native supplier project E2E (`tests/test_increment18_live_native_project_e2e.py`).
   - **Evidence & Rehearsal Bundles:** Tracked in [`RELEASE_PR_INCREMENT18.md`](RELEASE_PR_INCREMENT18.md), [`evidence/increment18-acceptance-and-scaling-20261104.json`](evidence/increment18-acceptance-and-scaling-20261104.json), [`evidence/increment18-pre-fix-failures-20261104.json`](evidence/increment18-pre-fix-failures-20261104.json), [`evidence/increment18-live-native-project-e2e-20261104.json`](evidence/increment18-live-native-project-e2e-20261104.json), and [`evidence/increment18-integration-rehearsal-20261104.json`](evidence/increment18-integration-rehearsal-20261104.json) (`249 passed, 0 failed, 2 warnings in 123.48s` across the 18-file regression suite).

## Generalized Task-Solving Coordinator & Cross-Domain Transfer — Increment 19

### Architecture & Implementation Boundary
1. **Persisted Action Contracts & Schema Migration 23 ([`migrations.py`](../../app/migrations.py), [`working_memory.py`](../../app/services/orchestration/working_memory.py))**:
   - Extends `project_task_contracts` (`_M23`) with `selected_tool`, `validated_arguments_json`, `expected_observation`, `verification_method`, `verifier_id`, `authority_scope`, `permitted_effects_json`, and `checkpoint_refs_json`.
   - Appends every committed working-memory checkpoint ID to `checkpoint_refs_json` on the corresponding task contract and ignores per-task execution metadata keys (`"decision_summary"`, `"lease_id"`, `"producing_actor"`) during `verify_project_task_integration`.
2. **Evidence-Driven Coordinator & Registered Verifiers ([`deliberate_loop.py`](../../app/services/orchestration/deliberate_loop.py), [`tool_catalogue.py`](../../app/services/orchestration/tool_catalogue.py))**:
   - Replaces supplier-only task branches with domain-agnostic contract orchestration (`ensure_deliberate_task_contracts`, `advance_deliberate_project`) supporting `supplier_option_evaluation`, `repository_bugfix`, and `explicit_contracts`.
   - Validates proposed actions (`validate_proposed_action`, `parse_and_validate_model_action_proposal`) against `GLOBAL_TOOL_CATALOGUE`, `REGISTERED_CONTROLLER_VERIFIERS` (`verifier:quote_reconciliation_v1`, `verifier:option_constraint_comparison_v1`, `verifier:grounded_recommendation_memo_v1`, `verifier:repo_inspection_and_reproduction_v1`, `verifier:protected_repo_bugfix_v1`, `verifier:repo_change_summary_v1`), `authority_scope`, and `permitted_effects` before dispatch.
   - Strips private `<think>` reasoning (`strip_private_chain_of_thought`), records concise operational decision summaries (`summarize_operational_decision`), and stops unproductive retry loops (`MAX_UNPRODUCTIVE_ATTEMPTS_PER_CONTRACT = 2`).
   - Adds workspace-confined repository tools (`inspect_repository_workspace`, `run_repository_test_suite`, `apply_and_verify_repository_patch`) with `_resolve_safe_workspace_path` and Python-commented directive sanitization (`# [UNTRUSTED_DIRECTIVE_STRIPPED ...]`).
3. **Cross-Domain Transfer & Adversarial Verification**:
   - **Live Cross-Domain E2E ([`tests/test_increment19_general_coordinator_transfer_e2e.py`](../../tests/test_increment19_general_coordinator_transfer_e2e.py))**: `1 passed in 55.38s` across both `batch_allocator` repository bug-fix (`lease_53491a5290a7`, fault recovery `lease_81fc249ecddc -> lease_0a72c31774dd`, mid-flight steering `lease_854d75b916ba`) and supplier evaluation regression (`lease_3f6f47de285d`), tracked in [`evidence/increment19-general-coordinator-transfer-20261104.json`](evidence/increment19-general-coordinator-transfer-20261104.json).
   - **Adversarial Coordinator Limits ([`tests/test_increment19_coordinator_adversarial_limits.py`](../../tests/test_increment19_coordinator_adversarial_limits.py))**: `10 passed in 4.01s` covering all 10 required failure modes.


