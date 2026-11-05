# Non-executing code proposal protocol

Development primitive only. See the [work register](README.md), [technical map](TECHMAP.md), and [increment report](evidence/INCREMENT_11_CODE_PROPOSALS.md). No production migration, new HTTP route or scheduler action is activated.

## Approved specification

The current approved orchestration plan's deliverable `acceptance_criteria` contains:

```json
{
  "schema": "qc.software_contract.v1",
  "requirements": [{"id": "addition", "description": "Return the exact sum of two integers."}],
  "allowed_files": ["src/main.py", "web/index.html"],
  "test_artifact_id": "ACTUAL_CANONICAL_TEST_ARTIFACT_ID",
  "test_version": 1,
  "test_sha256": "ACTUAL_SHA256_OF_TEST_ARTIFACT_BYTES"
}
```

The identifiers and hash above are illustrative placeholders. They must refer to actual canonical, scoped artifacts. Approving this contract pins test bytes and requirements; it does not establish that the tests are sufficient. Approved revisions are immutable; requirement/test changes need a new approved plan revision and fresh output/review under that revision.

## Proposal and test artifacts

The native inference task emits a canonical UTF-8 JSON artifact:

```json
{"schema":"qc.code_bundle.v1","files":[
  {"path":"src/main.py","content":"def add(a,b):\n    return a+b\n"},
  {"path":"web/index.html","content":"<!doctype html><title>Addition</title>\n"}
]}
```

The separate approved test artifact contains:

```json
{"schema":"qc.independent_tests.v1","requirement_ids":["addition"],"files":[
  {"path":"tests/test_add.py","content":"assert add(2,3)==5\nassert add(-1,1)==0\n"}
]}
```

These tests are illustrative input to a future independent test runner, not a currently runnable test harness or proof of independence. Proposed code and approved tests are never imported or executed by this increment.

## Developer API

`install_proposal_schema(conn)` explicitly installs append-only development audit tables. It is not called at application startup. With a scoped controller connection, construct `CodeProposalReview(conn, project_id, run_id)`, then call `review(deliverable_key, canonical_artifact_id)`. The reviewer reloads the actual approved plan and canonical artifacts rather than accepting a caller's success flags or contract replacement.

The producer must be the exact completed native inference task designated by the deliverable's `produced_by` key (falling back to the deliverable key), in the current plan revision. Lease, worker, execution and artifact records must agree. The returned review ID hashes its complete inputs and diagnostics.

`stage(review_id, existing_developer_root)` revalidates that review, creates a unique directory, writes `proposal/` and `acceptance/` trees, rechecks the inputs and records a hashed manifest. The staging root must be selected by trusted development code, never copied from a model's output. The caller owns database commit/rollback and cleanup of successful staging directories. Failures during materialization clean up only the unique directory created by the call.

Authoritative software admission recognizes this contract and records a fresh proposal review when the development schema is available. It always returns BLOCKED with zero fulfillment score: `stageable=true` means the packaging checks passed, not execution permission, correct behavior, deployment approval or certification. Missing schemas/bindings fail closed.

## Deliberate limits

- There is no automatic repair/dispatch loop yet. A revised proposal or new approved plan needs a new review.
- Limits: 64 files, 250 KB per file and 1 MB combined UTF-8 content per bundle; canonical JSON artifacts are also bounded to 1.5 MB.
- Paths use explicit source/test namespaces, ASCII relative components and supported text extensions. Dependency/configuration hooks and links are rejected. This does not make arbitrary source code safe.
- Test immutability inside this format is not a runtime defense. Hostile code must never share the verifier's process, credentials or authoritative result storage.
- Staging is not a security boundary against concurrent host filesystem manipulation. Execution needs independently verified OS confinement, hard budgets and protected result collection.

## Increment 16 extension

The non-executing APIs above remain unchanged in purpose. `capture_native_response(key)` now captures exact immutable successful native worker code-bundle bytes. The separately gated [protected verifier bridge](PROTECTED_VERIFIER.md) can execute the narrow approved integer case profile. Passing that profile does not change software acceptance or general certification.
