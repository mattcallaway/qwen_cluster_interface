# Verified implementation overlay

Supplement to the canonical [TECHMAP](../../TECHMAP.md), its [legacy pointer](../../TECH_MAP.md), and [work register](README.md). Status: development candidate, 2026-10-27; not deployed. Existing diagrams describe intended architecture and must not be read as proof of current operational capability.

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
| Searchable Paged KV Cache | app/services/paged_kv/{hasher,page_table,store,manager,identity}.py, app/services/inference.py | 16-token page tables, chained SHA-256 hashes, Radix tree prefix matching; live block pinning/generation tracking, canonical JSON namespace encoding (ExecutionNamespace/1.0), ModelIdentityManifest runtime identity binding, QCKVP2 mandatory HMAC-authenticated envelopes (no flag downgrade), fail-closed 256-bit storage keys (no hardcoded fallback), caller identity verification, and RAM cache integrity/byte quotas; repaired and hardened with 3-tier IdentityIssuanceAuthority (ApprovedManifestRegistry, live disk artifact validation against TOCTOU path substitution, IssuedReuseAuthority enforcement at manager ingestion) and controlled application inference modes (QC_PAGED_KV_ENABLED default false, fail_closed vs bypass error policies, secret-free error surfaces, zero-trust conftest) |
| Distributed Pipeline Parallelism | app/services/pipeline_parallel/{activation_protocol,topology,runner}.py | WireGuard activation frame transport (QCACT1), IEEE 754 serialization, HMAC-SHA256 integrity, multi-stage layer partitions; synthetic step transport benchmarked; real layer execution blocked on model engine |
| Orchestrator Verification DAG | app/services/orchestration/pipeline_kv_diagnostics.py, engine.py | Controller step for live algorithmic and adversarial fault containment diagnostics; certified within synthetic prototype scope |

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
