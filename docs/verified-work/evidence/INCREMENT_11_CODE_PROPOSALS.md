# Increment 11: Versioned multi-file software proposals

Development candidate, not deployed. Production execution, service restarts, live VPS access, hold removal and Russian River regeneration were not performed.

## Capability added

`app/services/orchestration/code_proposals.py` introduces a closed, versioned `qc.code_bundle.v1` format and an approved `qc.software_contract.v1` specification. A proposal contains regular UTF-8 text files only; its approved contract pins the exact source inventory, requirement descriptions and separate canonical test artifact ID/version/hash. The test artifact has a separate requirement inventory and test file namespace.

Review reloads the current approved controller plan and canonical artifact versions, rehashes their CAS bytes, and checks the proposal's exact artifact/task/native inference provenance. A valid review records input versions, producer metadata, plan and contract hashes, file hashes and sizes, and approved test bindings. The review remains BLOCKED for overall acceptance, with execution/behavior/certification false.

Staging requires a recorded review and fresh revalidation. It writes an exclusively created directory with separate `proposal/` and `acceptance/` trees and a hashed staging manifest. No shell commands, code imports, build tools, dependency installers or tests are launched. Each staging request creates a separate directory. Callers own database commit/rollback and the explicit staging-root choice.

## Rejection boundaries

- Traversal, absolute paths, drive letters, backslashes, alternate streams, hidden components, Windows reserved device names, non-ASCII aliases and unsupported extensions are rejected.
- Source files are limited to `src/`, `web/`, and `docs/`; tests live under `tests/` inside a separate acceptance tree. Explicit dependency/configuration hooks such as `package.json`, `setup.py`, `conftest.py` and Python startup hooks are rejected.
- Symlink records, modes and extra file fields are not part of the format. Case-colliding/duplicate names, file/directory collisions, omitted/extra files, NUL bytes and oversized content fail review.
- Linked/reparse staging roots are refused. Changed plans, artifacts, tests or native execution records require fresh review before staging.

These are packaging controls, not a determination that arbitrary source content is harmless. Python/JavaScript can contain dangerous logic even in an allowed file. Staging does not confer execution authority or protect against a hostile local user concurrently changing the filesystem. Actual OS confinement, descriptor-based race resistance and process/output resource limits remain separate work.

## Test authority and remaining reasoning limits

Pinning a separately approved test artifact prevents a proposed source bundle from silently replacing those test bytes. Requirement IDs document the intended test scope; they do not prove test adequacy or independent authorship. A test that merely says `assert True` is not sufficient acceptance evidence. No model-authored success flag can waive the software acceptance hold.

This increment provides a callable controller-side review/staging primitive. It does not add an automatic scheduler action, provision a containment backend, execute a model or dispatch a live coding canary. Simulated native-record fixtures in tests must not be described as actual model executions.

The authoritative software admission path recognizes the new contract, reloads its actual approved bindings and records fresh proposal diagnostics when the explicit development schema exists. It always retains BLOCKED status and zero fulfillment score, including for a valid stageable proposal. Missing schemas or invalid bindings fail closed. No production migration is installed automatically.

## Development findings and verification scope

The first focused run was interrupted after fixture setup attempted an update to an approved immutable plan. A single-test diagnostic confirmed the cause. Fixtures now create new approved revisions and use teardown-managed connections, including on setup failure. A subsequent focused run passed 42 cases and failed one fixture attempt to rewrite an append-only execution record; that case now changes task state rather than editing immutable execution history. The diagnostic log is retained as `increment11-focused-tests.txt` and is superseded by the final integration results.

These failures confirmed existing safeguards and were not resolved by disabling database triggers. All model/lease/execution records used here are synthetic test fixtures; no actual inference or live VPS run took place. Existing TestClient/httpx and duplicate OpenAPI operation-ID warnings remain.

Final verification:

- **264 selected integration tests passed**, including 48 new proposal/staging/admission cases. See `increment11-integration-tests.txt`. This is not the entire repository suite.
- **31 focused safety checks passed**, covering containment, native provenance, negation and Decimal finance; `candidate-tests.txt`.
- **27 documentation links/entrypoints passed** after extending the checker to all overlay reference documents; `documentation-checks.json`.
- The proposal tests verify actual materialized file hashes and separate test bytes, unique staging directories, no execution of a marker-writing proposal, unsafe path/shape rejection, stale native/plan/artifact binding rejection and blocked authoritative admission. Final additions check directory case aliases, invalid artifact-version status and inconsistent producer metadata.

The updated `docs/verified-work/CODE_PROPOSALS.md`, work register and map are included in the cumulative review package. Original-repository fingerprints matched the previous manifest, and the cumulative patch passed applicability checking against preserved original files. No deployment readiness is implied by these checks.

## Next steps and approval gates

Next: authenticate execution manifests at the worker with expiry/lease/task/worker/hash binding, define the confined build/test protocol and independent result authority, and test failure/deadline/output handling. Only after actual containment verification should a bounded new synthetic coding project run through the native controller and worker.

Execution, staging services on the VPS, deployment, production migrations/restarts, networking changes and hold removal require explicit approval of the concrete operation. Existing holds remain unchanged.
