# Protected code verification protocol

Development only; not deployed. See the [full implementation and test report](evidence/INCREMENT_16_PROTECTED_CODE_VERIFICATION.md), [register](README.md), [technical map](TECHMAP.md) and [release gates](PRODUCTION_RELEASE.md).

## Controller entry point

Explicitly install the existing proposal audit schema in an isolated test database. Configure a trusted transport with `engine.configure_protected_code_verifier(transport)`. This is not a public endpoint, automatic scheduler hook or worker flag. The service API requires `QC_ENV=development`; no transport is configured by default.

Call `engine.verify_native_code_proposal(project_id, run_id, deliverable_key, artifact_id, image_sha256, seconds=5)`. Passing `artifact_id=None` captures exact immutable native worker response bytes as a canonical code bundle. No fence removal, developer rewriting, model calls or code execution happens during capture. Completed task/lease/execution, worker/idempotency, current approved plan and canonical source/test versions must agree. Exact source bytes must match the worker execution response; provenance labels alone are insufficient.

## Initial test profile

The approved `qc.independent_tests.v1` artifact has one file, `tests/cases.json`. Its `qc.integer_cases.v1` document has 2–8 cases with `id`, `requirement_id`, public `input` containing integer `a`/`b`, and integer `expected`. Values are bounded to plus/minus 1,000,000 and all approved requirements need cases. This does not establish independent test authorship or adequacy.

## Boundary and result authority

The controller persists a grant before transport and generates a private per-grant 256-bit HMAC key. Grant/key travel only through explicitly trusted transport. In rehearsal, verified-host SSH stdin launches the reviewed root verifier; no daemon or broad worker privilege is installed. Root consumes a private nonce directory before execution. Each confined service receives source and a read-only `/run/qc-input.json`, never oracle answers, audit storage or key. Resource/process cleanup enforcement remains in the Linux backend.

The controller authenticates the receipt, compares exact ordered answers itself and rechecks current native inputs before recording a scoped result. Forged authority flags, replay, expiration, changed bindings, nonzero exit, unknown cleanup and transport failure cannot pass. There is no automatic retry. `current_result()` revalidates recorded results against current native inputs. HMAC authenticates this trusted controller/verifier transaction against submitted code; it is not public-key proof or protection from a compromised controller/root operator.

`behavior_verified` applies only to the approved cases. `accepted` and `certification_eligible` remain false. The general evaluator remains held. No auto-revision loop, hidden-answer feedback or production activation is added.

## Evidence and milestone status

- **Unit & regression baseline:** 53 focused verifier tests passed; 417 selected regression tests passed (with 6 live opt-in tests skipped in offline run, 7 warnings).
- **Live verifier rehearsal:** All 6 live scenarios passed across 18 actual Linux sandbox executions in 34.07s; 55 external fixture-oracle checks verified.
- **Fresh native leased-model canary:** Successfully executed across real controller → task → lease → worker → model inference path using `qwen2.5:1.5b` on laptop (`100.101.187.115`). Unmodified immutable response bytes captured directly into `qc.code_bundle.v1`. Demonstrated:
  1. Correct behavior passing approved integer cases in confined Linux container.
  2. Incorrect/forged code failing verifier despite exit code 0 and self-proclaimed authority flags.
  3. Bounded revision with fresh task, lease, review, and grant without exposing test oracles to prompt.
  4. Root verifier consume-before-execute nonce replay denial and controller stale binding rejection.
  5. Timeout cancellation with confirmed cgroup and process cleanup.
- **Safety holds:** All administrative holds remain FAIL-CLOSED. No production deployment, service restart, or hold removal authorized. Evidence recorded in `docs/verified-work/evidence/laptop-native-canary-f706a49af04d4182a27a5887d1f9fc85.json`.
