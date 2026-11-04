# Increment 16 â€” native proposal capture and protected independent verification

Development/test record: 2026-10-27; documentation finalized 2026-10-28. Development candidate only. No Russian River regeneration, production deployment/restart, service enabling or hold changes.

## Result and scope

The native controller now has an explicit development service API connecting an approved software contract and completed native inference evidence to a protected Linux verifier. It can capture **exact immutable worker-response bytes**, pin approved tests and runtime image, issue a durable one-shot grant, validate a signed receipt, recompute case outcomes, and recheck current native bindings before recording a scoped result.

This is an opt-in service API, not an automatically enabled scheduler branch or new public endpoint. Actual Linux executions passed through this controller API during rehearsal, with synthetic native database fixtures. **A real controller-dispatched, leased worker/model coding canary has not run.** The laptop's existing worker and Ollama services were not used or restarted. General software acceptance and certification remain false, even for passing cases.

## Findings and implemented changes

| Finding | Change | Evidence/limit |
|---|---|---|
| Artifact origin labels alone could lend native-looking provenance to rewritten developer bytes | Compare proposal SHA-256 with the response in the immutable successful worker execution; require one exact completed native lease/execution and current approved plan | Negative test registers altered code against an existing native task; transport must never be called |
| Native inference output was not connected to canonical software proposal capture | `CodeProposalReview.capture_native_response()` captures exact code-bundle JSON from the execution record; validates source inventory and safely reuses matching canonical artifacts | No stripping fences, repairs, substituted source, direct Ollama calls or execution during capture |
| App stdout could claim its own success | Root verifier controls execution; controller authenticates complete receipt and independently compares exact integer answers | Extra pass flags, wrong answers, duplicate JSON, booleans as integers and nonfinite values fail |
| Tests/oracle in the app's filesystem would permit self-grading | Only source and a read-only public input JSON enter the root-image service; expected answers, grant, private audit directory and receipt key stay outside | Actual tamper probe passed with successful arithmetic answers after denied access/write/network attempts |
| Lost/replayed or stale results could appear current | Durable grant before IO; root private nonce directory consumed before execution; no automatic transport retry; exact grant/plan/source/test/image/native bindings; expiry before each case and on controller receipt; current-result revalidation | Replayed root grants denied in all six live scenarios; stale plan/task/lease/artifact tests fail before or after transport |
| Successful process exit did not prove required behavior | Each case requires observed execution, clean process-group termination, completed reason, exact integer exit zero and exact one-field JSON answer | Actual wrong and forged-output programs exited zero but failed every case |

## Trust boundary and execution contract

`engine.verify_native_code_proposal(project_id, run_id, deliverable_key, artifact_id, image_sha256, seconds=...)` is held unless `QC_ENV=development` and an operator explicitly injects a trusted transport through `configure_protected_code_verifier()`. Passing `artifact_id=None` captures the exact immutable native response. The existing proposal schema is installed explicitly in development; no startup migration was added.

The supported initial profile is deliberately narrow: `qc.independent_tests.v1` contains only `tests/cases.json`, whose `qc.integer_cases.v1` suite declares 2â€“8 cases with requirement IDs, integer inputs `a`/`b`, and an expected integer. Each input and expected value is bounded to Â±1,000,000. Every approved requirement must have cases. This is not proof of test adequacy, independent authorship, arbitrary Python correctness, web behavior or dependency safety.

The controller binds the current review, plan revision/hash, contract hash, canonical proposal/test versions/hashes, native worker task/lease/execution/idempotency key, image and requirement inventory. It generates a fresh private 256-bit HMAC key per grant. The root verifier receives the grant/key through authenticated SSH stdin during rehearsal; neither is an app/worker argument, artifact authority flag or app-mounted secret. The key is not persisted in the database, receipt or report. HMAC protects this controller-to-verifier transaction from the submitted app; it is **not public-key proof for an unrelated auditor, nor independence from a compromised controller/root operator**.

The root verifier consumes the nonce in a private root-owned audit directory, writes immutable source/public-input snapshots, invokes the existing bounded systemd root-image backend, and stops further cases if cleanup is unconfirmed. Source and input are read-only; scratch, memory, processes, CPU, network and runtime remain bounded by that backend. No submitted test executable is imported into the verifier. A cleanup or transport exception produces a blocked controller record, never acceptance. Recorded historical results require current-binding revalidation before use.

Receipt authority is configured only inside the one-shot rehearsal. No permanent root daemon, worker privilege rule, broad sudo/polkit permission, configuration activation or package installation was introduced. Trusted root runs the reviewed verifier text supplied over authenticated SSH; no generated code is executed as root.

## Test evidence

- **137 focused local tests passed**: native capture/byte-binding, signed observation validation, replay, stale revisions/leases/artifacts, default and production holds, lost transport, unsafe paths/test shapes, tampered results, process/backend compatibility. See `increment16-focused-tests.txt`.
- **6 live controller-API scenarios passed**, each containing three actual confined executions: correct arithmetic, wrong constant, forged pass flags, boundary tamper probe, infinite loop/deadline and unbounded output. See `increment16-laptop-live-tests.txt` and the final batch's `laptop-verifier-*.json` receipts.
- **55 fixed external oracle checks passed** over the final live batch, without importing the candidate verifier. They recomputed fixture outcomes from raw output and checked case inventories, root replay denial, process cleanup, policy removal and unchanged existing service PIDs/status. These external checks do not themselves authenticate the HMAC; the controller live tests do. See `laptop-verifier-independent-checks.json`.
- The initial opt-in test invocation stopped locally because its imported fixture was not registered; importing that fixture corrected setup before any SSH execution. A later new local test initially counted records from unrelated fixture projects; its assertion was corrected to query the scoped project/run. Neither failure was converted into a pass record.
- The final selected regression suite passed: **417 passed, 6 opt-in live tests skipped, 7 warnings**. See `increment16-integration-tests.txt`. The six live tests were separately run and passed.

The final live batch ran **18 actual confined executions**; an earlier successful pre-controller-entrypoint rehearsal ran another 18, retained as prior evidence rather than counted as additional final scenarios. Every completed root grant was explicitly replayed and rejected without another code execution. All observed test cgroups were empty. Temporary test policies were removed after each scenario. The existing `qwen-worker.service`, `ollama.service` and `qc-ollama-ts.service` kept their original PIDs and active/running status throughout. Current safety holds were not edited; their production policy state was not independently re-audited in this increment.

The one laptop warning concerns pytest's local cache directory permissions; it does not indicate a failed containment check. No live model output, automatically leased generated-code task, real VPS deployment, external dependency installation, complete escape resistance or general certification is claimed.

## Approval gates and next work

1. **No production approval requested by this package.** Review the candidate diff, source baseline, exact configured transport and scoped test profile. Keep automatic generated-code dispatch and certification held.
2. **Next development evidence:** a fresh isolated controller/worker identity should generate the exact code bundle through real native task leasing and an approved local model. Pass its unmodified execution response through capture and this protected verifier. Include a failed case, bounded revision with a fresh grant, and stale/revoked work. Do not enroll or restart the existing production worker to accomplish it.
3. **Before automatic verification/revision:** add explicit execution approval and budget/revocation handling to the native scheduler, failure feedback that omits hidden oracle answers, durable recovery rules, expanded test profiles and independent test-adequacy review. The current synchronous API deliberately does not auto-retry or schedule revisions.
4. **Before production activation or any restart/hold change:** provide exact build hashes, target services, schema/configuration changes, backup/migration rehearsal, operational limits, monitoring, rollback triggers and a concrete approval request. Passing these cases grants none of those permissions.

The implementation and evidence are integrated into the existing README and canonical technical map through `docs/verified-work/`. The original repository and production services remain separate from this candidate.
