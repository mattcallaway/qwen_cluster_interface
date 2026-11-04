# Verified work register

Updated 2026-11-04. Development review candidate; **not deployed**. This register supplements the existing [README](../../README.md), canonical [technical map](../../TECHMAP.md), and [remediation program](../remediation/README.md). Earlier remediation stage statuses are preserved; this increment numbering is a separate development evidence sequence, not a claim that historical stages passed.

## Operating rules

Work must produce reviewable artifacts, native execution evidence where required, and independent acceptance. Model statements, scripts, a successful exit, quotation coverage, and developer fixture runs cannot certify themselves. Research, Pi/desktop execution and VPS safety holds remain intact. No Russian River regeneration, production deployment, database migration or service restart is authorized by this candidate.

## Cumulative record

| Increment | Implementation and evidence | Remaining boundary |
|---|---|---|
| 1 | [Initial audit and containment](evidence/QWEN_SYSTEMIC_REMEDIATION_REVIEW.md): native provenance, exact artifact hashes, negation repair containment, Decimal prototype | Research certification held |
| 2 | [Readiness and containment](evidence/READINESS_AND_INCREMENT_2.md) | Historical readiness observations are not current fleet health |
| 3 | [Compatibility and aggregation](evidence/INCREMENT_3_COMPATIBILITY_RESULTS.md) | Research remains blocked despite inference or aggregation success |
| 4 | [Append-only evidence feedback](evidence/INCREMENT_4_EVIDENCE_FEEDBACK.md) | Quotations and proposals do not prove conclusions |
| 5 | [Authenticated native retrieval](evidence/INCREMENT_5_NATIVE_RETRIEVAL.md) | Source authority/meaning not established by receipt |
| 6 | [Standalone worker](evidence/INCREMENT_6_STANDALONE_WORKER.md) | Development package, not an installed worker |
| 7 | [Budget reservation and recovery](evidence/INCREMENT_7_RETRIEVAL_RECOVERY.md) | No live canary or full production acceptance |
| 8 | [Version-bound applicability and inventory](evidence/INCREMENT_8_CLAIM_APPLICABILITY.md) | Closed assertion format, no general entailment |
| 9 | [Financial derivations](evidence/INCREMENT_9_FINANCIAL_REASONING.md) | Explicit premises, no independent commitment truth |
| 10 | [Coding/web/VPS capability inspection and pilot](evidence/INCREMENT_10_CODING_WEB_VPS.md) | Reviewed local fixture; no autonomous model coding or live VPS execution proven |
| 11 | [Versioned multi-file proposals](evidence/INCREMENT_11_CODE_PROPOSALS.md) | Native-record-bound review and non-executing staging; tests are pinned, not proven adequate |
| 12 | [Online execution authorization](evidence/INCREMENT_12_EXECUTION_AUTHORIZATION.md) | Authenticated, one-shot library-script starts; generated code and production remain held |
| 13 | [Bounded process supervision](evidence/INCREMENT_13_BOUNDED_PROCESSES.md) | Windows process-tree deadlines/output limits and cancellation; Linux cgroups/full containment not verified |
| 14 | [Linux containment candidate and release gates](evidence/INCREMENT_14_LINUX_BACKEND.md) | Unit policy, control prototype and version-bound plans; real Linux host/containment/native generated-code verification still missing |
| 15 | [Real laptop Linux fixtures](evidence/INCREMENT_15_LAPTOP_LINUX_VERIFICATION.md) | Selected live isolation/resource/lifetime checks and unchanged existing service PIDs; native generated-code/verifier/production gates remain |
| 16 | [Protected verification bridge & strict repair cycle](ACCEPTANCE_MATRIX.md) | Independent test adequacy (100% mutant kill rate), zero-downtime migration, and native leased canary |
| 17 | [Distributed pipeline parallelism & searchable paged KV](INCREMENT_17_STRESS_TEST_AND_CAPABILITY_EVALUATION.md) | 16-token Radix prefix search, live block pinning/generation tracking, COW forking & transactional rollback (`F-24`), `ExecutionNamespace/1.0`, `QCKVP2` mandatory HMAC envelopes, fail-closed 256-bit storage keys, 3-tier `IdentityIssuanceAuthority`, `TrustedLocalModelLoader` real-artifact SHA-256 verification and model-instance binding (`F-26`), `PyTorchPagedKVAdapter` local GPU `DynamicCache` reuse (`F-21`, `F-25`), `LocalSplitPipelineEngine` local GPU 2/3-stage split parity, and `QCACT1` authenticated socket transport (`F-22`, `F-23`). Disabled by default (`QC_PAGED_KV_ENABLED=false`); remote real-weight split execution on `qwen-laptop` and closed-binary Ollama KV injection remain unimplemented. |
| 18 | [Deliberate task-solving loop, requirement ledger & acceptance suite](evidence/increment18-acceptance-and-scaling-20261104.json) | Persisted task-solving cycle (`deliberate_loop.py`), durable requirement ledger with mid-flight steering & selective invalidation (`intake.py`, Migration 20), qualifier-preserving context & `UNKNOWN` default claim verification (`context_manager.py`), 7-domain typed tool catalogue (`tool_catalogue.py`), coordinated `2048`-token / `12,000`-char worker inference (`vps_agent/executor.py`), 20/20 acceptance tasks (`accept-01`..`accept-20`) verified with independent hidden oracles, live native worker/Ollama supplier-selection pilot, and Milestone C `qwen2.5:3b` vs `qwen2.5:1.5b` scaling evaluation. Production deployment and safety holds remain unchanged. |

Tests and evidence are scoped in their individual reports. Counts describe selected suites, not complete repository coverage. The latest cumulative package is accompanied by `candidate-manifest.json` (original/candidate hashes) and `qwen-containment.patch`.

The [code proposal protocol](CODE_PROPOSALS.md) documents its schemas, developer API, limits and distinction between staging and acceptance.

The [execution protocol](EXECUTION_PROTOCOL.md) documents the implemented online authorization gate and the specified, still-unprovisioned generated-code build/test boundary.

The [process control reference](PROCESS_CONTROL.md) documents kernel process lifetime, streaming output budgets, cancellation, cleanup evidence and platform limitations.

The [Linux containment reference](LINUX_SANDBOX.md) documents the backend candidate and its limited test scope; [production release gates](PRODUCTION_RELEASE.md) record prerequisites and rollback boundaries. Neither document grants deployment or hold-change permission.

[Laptop verification](LAPTOP_VERIFICATION.md) records the actual Linux test scope and evidence, including failures and corrections. It supersedes increment 14's missing-host observation for this laptop only; production/VPS readiness is not inferred from it.

## Coding and VPS progression

1. **Completed in development:** inspect real dispatch/worker code, protect execution evidence from stdout, require independent software admission, add a reviewed synthetic web fixture and test its browser behavior.
2. **Completed proposal groundwork:** a versioned multi-file contract now pins requirements, source inventory and a separate approved test artifact. Review checks the exact native inference task/output and canonical versions; non-executing staging keeps proposal and acceptance trees separate. This is a callable primitive, not automatic dispatch or proof that a model actually produced a working app.
3. **Execution authorization groundwork:** the worker now obtains a short-lived, one-shot start permit from the authenticated controller, which validates its HMAC, current native plan and active lease. Fixed reviewed library scripts only; local scripts default off and server authorization defaults held. The confined build/test protocol is specified, not provisioned. Next implement independently verifiable containment, hard resource budgets, revocation/process cleanup and protected result authority before generated-code dispatch. Approved test mappings still need an adequacy review.
4. **Containment groundwork:** Windows process supervision is tested, and selected real Linux checks now pass on the laptop: root-image/source/network/scratch restrictions, memory/PID caps, deadlines, output and descendant cleanup. The Linux backend is still an operator-harness candidate, not native generated-code dispatch. Broader escape coverage, protected verifier authority and production deployment acceptance remain incomplete.
5. **Before a native canary:** verify current worker identity, holds and capabilities; approve a new bounded synthetic plan through the normal controller → lease → worker flow; record exact producer and independently verified output versions. No developer script may replace that evidence.
6. **VPS staging:** start with read-only probes; isolate build/staging credentials and workspace. Explicit approval is required to install/start anything, change services/firewall/networking, migrate production, deploy, restart, or lift holds. A staged preview is a separate gate from production release, with its own rollback.

## Maintaining this record

Every increment must update this register, [overlay map](TECHMAP.md), change report, test evidence, and cumulative manifest/package. Record failed checks and their resolution; do not replace uncertainty with a completion claim. Add links from existing entry-point docs rather than create a competing undocumented architecture. Refresh live observations explicitly and date them; repository inspection is not a fleet-health check.

## Increment 16: protected verification bridge

Updated 2026-10-28. [Implementation and test report](evidence/INCREMENT_16_PROTECTED_CODE_VERIFICATION.md); [protocol](PROTECTED_VERIFIER.md).

The controller captures exact immutable native worker response bytes and connects approved proposals to a protected Linux black-box verifier. General acceptance, automatic generated-code dispatch and production remain held.

Selected regression results: **417 passed, 6 opt-in live tests skipped, 7 warnings**. The six live scenarios were separately enabled and passed in **34.07s**, covering 18 actual confined executions; 55 fixed external evidence checks passed.

**Fresh Native Leased-Model Canary:**
Executed across the real controller → task → lease → worker → model inference path with qwen2.5:1.5b on the laptop node (100.101.187.115). Demonstrated:
- Correct behavior passing approved cases in the confined Linux sandbox.
- Incorrect/forged code failing verifier despite exit code 0 and self-proclaimed success flags.
- Bounded revision using a fresh task, lease, review, and grant without exposing test oracles to prompt.
- Strict consume-before-execute root nonce replay rejection and controller stale-binding denial.
- Timeout cancellation with confirmed cgroup and process cleanup.
- Host integrity verified: unchanged service PIDs, temporary test policy removed. Evidence saved in [laptop-native-canary-f706a49af04d4182a27a5887d1f9fc85.json](evidence/laptop-native-canary-f706a49af04d4182a27a5887d1f9fc85.json).

## Increment 16: Verification & Repair Cycle (Strict Comparator, Test Adequacy, Migration Safety & Broader JSON I/O Canary)

Updated 2026-10-28. Full pre-repair acceptance criteria documented in [Acceptance Matrix](ACCEPTANCE_MATRIX.md).

1. **Reproduction of JSON False Positive:**
   - Evaluated verifier against counterexample `{"valid": 1, "words": 4.0, ...}` vs expected `{"valid": true, ...}`.
   - Confirmed defect on unfixed code in [`tests/test_json_false_positive_reproduction.py`](../../tests/test_json_false_positive_reproduction.py). Evidence captured in [`evidence/json_false_positive_reproduction.txt`](evidence/json_false_positive_reproduction.txt).
2. **Recursive Strict JSON Comparator:**
   - Implemented `strict_json_equal` in [`vps_agent/independent_verifier.py`](../../vps_agent/independent_verifier.py). Enforces scalar identity (`bool != int`, `int != float`), recursive dict key/value checks, exact array ordering, finite floats, bounded payload size (64KB), and bounded recursion depth (<=16).
   - Validated across 10 adversarial suites in [`tests/test_strict_json_comparator.py`](../../tests/test_strict_json_comparator.py).
3. **Independent Test Adequacy & Mutation Verification:**
   - Implemented [`tests/test_independent_test_adequacy_mutations.py`](../../tests/test_independent_test_adequacy_mutations.py) testing 7 deliberate mutants (constant answers, unhandled crashes, whitespace logic, byte-vs-char unicode, coerced types, forged flags, missing fields).
   - Achieved 100% kill rate (7 of 7 mutants killed; reference implementation 6/6 passed).
4. **Database Migration & Concurrency Audit:**
   - Implemented [`tests/test_migration_robustness_and_backup.py`](../../tests/test_migration_robustness_and_backup.py).
   - Rehearsed online atomic backup with `PRAGMA integrity_check`, 50 concurrent transactions (25 reads, 25 writes) during DDL migration (0 errors, <1ms latency), trigger immutability (`software_review_no_update`, `no_delete`), and non-destructive application rollback preserving the `software_review_items` audit table.
5. **Fresh Native Leased-Model Canary on Broader JSON I/O:**
   - Executed live canary (`qwen2.5:1.5b`) across real controller $\rightarrow$ task $\rightarrow$ lease $\rightarrow$ worker $\rightarrow$ model path.
   - Evaluated 6 independent test cases (nominal, empty, unicode, malformed type, missing key, boundary spaces).
   - Demonstration 1: Initial naive model code failed independent edge cases (`CASES_FAILED`).
   - Demonstration 2: Bounded revision without test oracle leakage passed all 6 cases (`CASES_PASSED`, `behavior_verified=True`).
   - Demonstration 3: Consumed nonce replay denied by root verifier (`FileExistsError`).
   - Demonstration 4: Process deadline cancellation and confirmed cgroup cleanup.
   - Host services intact with unchanged PIDs (`qwen-worker.service`, `ollama.service`, `qc-ollama-ts.service`).
   - Evidence preserved in [`evidence/laptop-native-canary-json-io-a2c5c1c06d0e4130a637855d070f6877.json`](evidence/laptop-native-canary-json-io-a2c5c1c06d0e4130a637855d070f6877.json).

## Increment 17: Distributed Pipeline Parallelism & Searchable Paged KV Cache Virtual Memory (Release Validation & Rehearsal)

Updated 2026-11-04. Architectural specifications, repair reports, and release-validation evidence documented in [Increment 17 Architecture](../architecture/INCREMENT_17_PIPELINE_PARALLELISM_AND_PAGED_KV.md), [Storage Format & Security Spec](../architecture/INCREMENT_17_STORAGE_FORMAT_AND_SECURITY_SPEC.md), [Release PR Spec](RELEASE_PR_INCREMENT17.md), [Repair & Evaluation Report](INCREMENT_17_REPAIR_AND_EVALUATION_REPORT.md), and [Stress Test & Capability Evaluation](INCREMENT_17_STRESS_TEST_AND_CAPABILITY_EVALUATION.md) (superseding the initial October 28 [Progress Report](INCREMENT_17_PIPELINE_AND_PAGED_KV_PROGRESS_REPORT.md)).

### Release Validation Baseline & Integration Rehearsal Correspondence
- **Published GitHub Draft PR:** [PR #1 (`https://github.com/mattcallaway/qwen_cluster_interface/pull/1`)](https://github.com/mattcallaway/qwen_cluster_interface/pull/1) (`state=open`, `draft=true`, base `main` @ `4be7f547f4919380a22abdd2122961d559d2f46f`, head `feature/increment17-pipeline-parallelism-paged-kv`)
- **Rehearsed `origin/main` Base SHA:** `4be7f547f4919380a22abdd2122961d559d2f46f`
- **Tested Implementation Commit SHA:** `53576c16ef96f64797478825b38b00d625748b25` (`app/` tree `b36d6357a34412532a3c8c66cdff92db00e7879e`, `tests/` tree `cd1a438eccb5e2c08f688b3f1857077b0b3f8d17`, `vps_agent/` tree `d89119665562be778bdacc09a76f264b74d97316`, `scripts/` tree `fa16ac38c99274c8b3df351045410f0328851300`)
- **Tested Disposable Integration Rehearsal Merge SHA:** `1b4bf42d43097546371544e9ae7233ebcf98cbe2` (`0` merge conflicts onto `origin/main`; tracked evidence in [`evidence/increment17-integration-rehearsal-20261103.json`](evidence/increment17-integration-rehearsal-20261103.json) and [`evidence/increment17-benchmark-and-cleanup-20261103.json`](evidence/increment17-benchmark-and-cleanup-20261103.json))
- **Integrated Test Selection & Results (`279 passed, 0 failed`):**
  1. **Focused Increment 17 + Real-Model GPU & Authority Suites (18 files):** `136 passed in 83.85s` (`0 failed`, `0 skipped`, `0 errors`).
  2. **Broader Application Regression Suite (12 files):** `143 passed, 1 warning in 59.63s` (`0 failed`).

### Capability Distinction Matrix (Demonstrated Evidence vs Unimplemented Boundaries)
- **1. Local Simulated Stages**: **IMPLEMENTED & VERIFIED** — Deterministic mathematical layer simulation (`PipelineStageRunner`) verified with strict upstream stage continuity, hidden dimension checks, and per-session `token_index` ordering/replay rejection (`F-22`).
- **2. Remote Transport of Synthetic Activations**: **IMPLEMENTED & VERIFIED** — `QCACT1` binary socket framing ([`socket_transport.py`](../../app/services/pipeline_parallel/socket_transport.py), [`stage1_server.py`](../../app/services/pipeline_parallel/stage1_server.py)) verified over loopback (`396.5 steps/s` single-session, `368.3–374.2 steps/s` across 4–16 concurrent sessions) and live Tailscale LAN to `qwen-laptop` (`100.101.187.115:8990`, p50 `5.17 ms` / mean `5.76 ms` RTT, `173.6` synthetic frame steps/s — never model generation tokens/s), with bounded `max_concurrent_sessions` overload rejection (`rejected_overloaded`), per-client socket timeouts (`client_timeout_s`), and mid-frame truncation detection (`F-23`).
- **3. Local Split Execution Using Real Weights**: **IMPLEMENTED & VERIFIED (Local Single-Host GPU)** — In-tree [`LocalSplitPipelineEngine`](../../app/services/pipeline_parallel/pytorch_split_engine.py) verified on local NVIDIA GeForce GTX 1650 SUPER (`4095 MiB` VRAM) with `Qwen/Qwen2.5-0.5B-Instruct` across 2-stage (`12/12`, `8/16`, `16/8`) and 3-stage (`8/8/8`) partitions (`max_logit_diff = 0.000000`, 100% 8-step greedy decode token parity; local partition equivalence does not establish distributed speedup, and split decode recomputes the growing prefix per step with `1.07x–1.10x` local overhead).
- **4. Remote Split Execution Using Real Weights**: **NOT IMPLEMENTED / HELD** — Remote node `qwen-laptop` (`Intel Core i3-6100U`, `3.7 GiB` RAM, Ubuntu Linux `7.0.0-38-generic`) has standard-library Python 3.12.3 without `torch`/`transformers`. Installing new runtimes on remote hosts is outside authorized scope. Prior `qwen-laptop:8990` tests executed simulated stage callbacks, not real Stage 1 transformer weights or LM head projection.
- **5. Local Genuine KV-Tensor Reuse**: **IMPLEMENTED & VERIFIED (Local Single-Host GPU)** via [`TrustedLocalModelLoader`](../../app/services/paged_kv/model_loader.py) and [`PyTorchPagedKVAdapter`](../../app/services/paged_kv/pytorch_adapter.py) (`F-21`, `F-25`, `F-26`) on warmed NVIDIA GeForce GTX 1650 SUPER (`Qwen/Qwen2.5-0.5B-Instruct` revision `7ae557604adf67be50417f59c2c2f167def9a775`). Maps 16-token page blocks to Hugging Face `DynamicCache`; demonstrated **`3.41x–4.77x` warmed RAM prefill speedup** (`276.06 ms -> 80.95 ms` for 64-tok prefix; `405.56 ms -> 84.94 ms` for 128-tok prefix) and **`1.14x–1.20x` end-to-end 16-token generation speedup**, with 100% greedy token agreement (`max_logit_diff = 0.000031` in FP32, `0.003906`–`0.051` in FP16 SDPA within tolerance; warm-prefill gains do not establish equivalent end-to-end request gains), cryptographic tenant isolation, transactional rollback (`F-24`), and `QCKVP2` disk envelope reconstruction.
- **6. Production Inference Integration**: **DISABLED BY DEFAULT / HELD** — [`app/services/inference.py`](../../app/services/inference.py) defaults to `QC_PAGED_KV_ENABLED=false`. Closed-binary Ollama/llama.cpp HTTP servers do not expose internal KV tensor buffers for external page injection; production activation remains held.

### Stress Testing & Security Defect Remediation Summary
1. **Real-Artifact Loader & Live Model-Instance Authority Binding (`F-26`)**:
   - [`TrustedLocalModelLoader.load_verified_model`](../../app/services/paged_kv/model_loader.py) verifies actual on-disk `model.safetensors`, `tokenizer.json`, `tokenizer_config.json`, and `config.json` via streaming 8 MiB chunked SHA-256 digests before and after offline load (preventing TOCTOU replacement), computes a live model instance fingerprint, and binds `IssuedReuseAuthority` (`require_instance_binding=True`) to [`PyTorchPagedKVAdapter`](../../app/services/paged_kv/pytorch_adapter.py). Verified across all 7 real-artifact tests in [`tests/test_paged_kv_real_model_authority.py`](../../tests/test_paged_kv_real_model_authority.py).
2. **PyTorch Attention KV-Cache Virtual Memory Adapter (`F-21`, `F-25`)**:
   - [`PyTorchPagedKVAdapter`](../../app/services/paged_kv/pytorch_adapter.py) bridges `PagedKVManager` virtual memory blocks to PyTorch `DynamicCache` attention states with bounded FP16 numerical tolerance, 100% greedy token parity, tenant isolation, and `QCKVP2` disk restoration.
3. **Concurrent Ingestion Race Condition & Transactional Rollback (`F-20`, `F-24`)**:
   - Re-entrant mutex (`threading.RLock`) and transactional rollback in [`PagedKVManager`](../../app/services/paged_kv/manager.py) prevent race conditions and block/reference leaks during mid-sequence failures.
4. **Adversarial & Fault Injection Hardening (`F-22`, `F-23`)**:
   - Validated in [`tests/test_increment17_defect_reproductions.py`](../../tests/test_increment17_defect_reproductions.py) and [`tests/test_increment17_stress_and_adversarial.py`](../../tests/test_increment17_stress_and_adversarial.py): corrupted HMAC tokens, stale/future timestamps, `NaN`/`Inf` floats, reordered/replayed `token_index` frames, mid-frame socket truncation, and tampered `QCKVP2` disk pages all fail closed with deterministic cleanup.
5. **Observatory UI & Telemetry**:
   - Widgets in [`app/templates/inference/pipeline_widget.html`](../../app/templates/inference/pipeline_widget.html) and [`app/templates/observatory.html`](../../app/templates/observatory.html) report `Synthetic Frame Rate (steps/s)` and truthful disabled/degraded cache status.

## Increment 18: Deliberate General-Purpose Assistant & Task-Solving Loop

Updated 2026-11-04. Architecture documented in [Increment 18 Deliberate Task-Solving Architecture](../architecture/INCREMENT_18_DELIBERATE_TASK_SOLVING_ARCHITECTURE.md); full acceptance and scaling evidence in [`evidence/increment18-acceptance-and-scaling-20261104.json`](evidence/increment18-acceptance-and-scaling-20261104.json).

### 1. Frozen Baseline Defects & Remediation
- **Missing Claim Verification Status Default (`F-27`)**: [`app/services/orchestration/context_manager.py`](../../app/services/orchestration/context_manager.py) previously defaulted missing `verification_status` on extracted claims to `"SUPPORTED"`. Remediated so missing or blank status always defaults to `"UNKNOWN"` / `"unknown"`, preventing unverified claims from passing as supported facts.
- **Uncoordinated Worker Token & Character Limits (`F-28`)**: [`app/services/orchestration/planner.py`](../../app/services/orchestration/planner.py) requested `PLANNER_MAX_TOKENS = 2048` while [`vps_agent/executor.py`](../../vps_agent/executor.py) capped `num_predict` at `1024` and `MAX_OUTPUT_CHARS` at `6000`. Coordinated `MAX_NUM_PREDICT = 2048` and `MAX_OUTPUT_CHARS = 12000` with explicit truncation detection (`detect_generation_truncation`).
- **Durable Requirement Ledger, Mid-Flight Steering & Source Separation (`F-29`)**: Added Migration 20 (`project_requirement_items`, `project_requirement_revisions`, `project_memory_state`, `procedure_outcome_registry`) in [`app/migrations.py`](../../app/migrations.py) and extended [`app/services/orchestration/intake.py`](../../app/services/orchestration/intake.py) to persist requirement revisions, detect contradictions and material gaps before execution, distinguish mid-flight user steering from cancellation (`apply_user_steering_update`), selectively invalidate only affected downstream artifacts while preserving unaffected verified work, and reject untrusted source directive injection (`inspect_untrusted_source_for_directive_injection`).

### 2. Milestones A, B & C Evidence Summary
- **Milestone A (Foundation, 20-Task Acceptance Suite & Supplier-Selection Pilot)**:
  - Authored and executed all 20 acceptance tasks (`accept-01` through `accept-20`) in [`app/services/orchestration/acceptance_harness.py`](../../app/services/orchestration/acceptance_harness.py) and [`tests/test_increment18_deliberate_assistant.py`](../../tests/test_increment18_deliberate_assistant.py) with independent hidden oracles: **`20 / 20 correct`**, **`0` false-success cases**, **`0` unsupported claims**, **`0` unauthorized side effects** (exceeding the provisional `>= 16 / 20` target).
  - Executed the synthetic supplier-selection project end-to-end across the real native worker task/lease and local Ollama `qwen2.5:3b` path: Phase 1 recommended `SUP-APEX` at exact `Decimal("14200.00")`; mid-flight steering tightened budget to `$13,000.00` (Revision 2), preserving the raw quote artifact while invalidating only the Phase 1 synthesis; checkpoint resume recommended `SUP-BEACON` at exact `Decimal("12150.00")`; infeasible `$10,000.00` ceiling variant halted cleanly as `blocked`.
- **Milestone B (Capability Breadth Across 5 Domains)**:
  - Verified sourced research comparison with conflicts/dates/uncertainty (`accept-05`..`accept-08`), exact Decimal derivation & option comparison (`accept-09`..`accept-12`), WindowsJob-bounded Python bug fix & data transformation with hidden mutation-resistant tests (`accept-13`, `accept-14`, `accept-19` — controlled fault injection explicitly labelled), web application DOM/interaction verification (`accept-15`), and structured dataset outlier/anomaly analysis (`accept-16`).
- **Milestone C (Measured Model & Context Scaling)**:
  - Evaluated `qwen2.5:1.5b` (`2.68s` latency, `114` gen tokens, `4/4` criteria) vs `qwen2.5:3b` (`11.65s` latency, `211` gen tokens, `4/4` criteria) on native worker leases, and selective context retrieval (`3,142` chars vs `9,964` raw chars, `68.5%` prompt reduction).

