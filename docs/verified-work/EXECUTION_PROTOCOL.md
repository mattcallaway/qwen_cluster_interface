# Execution authorization and confined build/test protocol

Development specification, 2026-10-27. See [work register](README.md), [technical map](TECHMAP.md), [proposal protocol](CODE_PROPOSALS.md), and [increment report](evidence/INCREMENT_12_EXECUTION_AUTHORIZATION.md). Production is not provisioned or enabled by this document.

## Implemented: reviewed library-script start gate

1. The worker must explicitly allow scripts locally. The default is off; it does not advertise script execution when off. Server node policy, existing holds, worker identity and token authority remain separate mandatory checks.
2. After a native lease, the agent requests `POST /api/v1/worker/tasks/{task_id}/authorize-script` with the leased manifest, idempotency key and a new random nonce. The agent requires a canonical HTTPS controller origin with certificate verification, refuses redirects, and permits HTTP only for explicit loopback development. It never receives the controller signing secret.
3. The controller reloads current worker policy, task, active lease, approved native plan, designated dispatched worker, and approved fixed library action. It verifies the existing controller HMAC against stored script bytes and checks manifest timing, identity, capabilities and network denial. A valid legacy signature is insufficient to authorize arbitrary/non-library code.
4. Authorization requires an active project/run and the current approved revision, exact fixed library code, explicit operational/script permissions, no hold, and no earlier execution history. It accepts only `python_execution`; generated-code Class C jobs remain held.
5. The controller records an append-only one-shot authorization before replying. Task and lease identities are unique in that ledger. Response loss, process failure or a repeated request cannot grant another start for the same task. Recovery requires a new approved task, not deletion of the record or silent replay.
6. The response binds task/lease/worker/idempotency/type, script hash, complete manifest hash and nonce. The worker checks those against its actual leased task. The start window is at most five seconds measured from the worker's request-start monotonic time; a delayed response loses that window.
7. A process-local permit can be consumed once and cannot be supplied as task JSON. The executor requires it before creating the workspace and checks the start deadline again immediately before launching. Effective runtime is clamped to the grant, reviewed script limit and caller limit: at most 30 seconds, with remaining lease time reserved. Actual OS isolation is still independently required.

This is online controller verification, not offline worker signature verification. Its authority depends on the authenticated controller channel and trusted agent code. It does not defend against a compromised controller/worker process, malicious administrator or trusted-code permit fabrication. Synthetic permits in reviewed executor fixtures are explicitly test-only; runtime code never imports their helper or activates a test authorization fallback.

The feature requires explicit development schema installation through `install_script_authorization_schema()`, the server flag `QC_SCRIPT_AUTHORIZATION_ENABLED`, the local worker flag `QC_WORKER_ALLOW_SCRIPT_EXECUTION`, and separately approved registry permissions. Both flags default off. No production migration, flag change or permission change occurred here. Existing named-worker class restrictions may deny execution even if flags are set; flags do not override holds.

## Remaining execution limits

Authorization is not instantaneous revocation after the reply. Increment 13 adds cancellation on heartbeat denial/disconnection and current-authority checks during renewal, plus bounded output and kernel descendant cleanup on Windows. The [process control reference](PROCESS_CONTROL.md) records evidence and limits. The Linux cgroup path, race-resistant output reads, full resource controls and verified launcher ownership/filesystem/network confinement remain prerequisites for production acceptance. A permit never waives them or certifies results.

## Specified next: generated-code build/test boundary (not implemented)

This section describes the required next boundary, not an available backend or permission to execute generated code.

| Stage | Required inputs and checks | Failure behavior |
|---|---|---|
| Prepare | Fresh version-bound proposal review, current approved requirement contract, pinned source and independent test artifacts | Stale or incomplete inputs remain BLOCKED |
| Select environment | Administrator-reviewed immutable image digest and independently verified OS containment profile; no floating images or model-selected host tools | No backend means no dispatch |
| Materialize | Read-only source snapshot; test authority and result storage outside generated code's writable environment; bounded scratch space | Path/link/hash/policy failure prevents start |
| Build | Fixed reviewed tool operation with explicit argv, dependency lock/artifact hashes, offline prepared dependencies, hard wall/CPU/memory/process/output limits | No shell interpolation, installation hooks, internet downloads or unrestricted fallback |
| Exercise behavior | Independent verifier uses controlled input/output channels to the confined app. A browser preview requires a separately tested private channel/bridge with denied external egress; no general host/network access | Application assertions or stdout cannot become verifier results |
| Record | Verifier-owned observation record binds exact source/tests/image/policy, native task/lease/worker, deadlines, exit status and observed behavior | Missing or mismatched evidence remains BLOCKED |
| Repair | A failed test creates a bounded investigation/revision proposal. New bytes require a new version, review and authorized task | No editing pinned tests or reusing old success receipts |
| Release | Separate staging approval, independent acceptance, then separate production deployment approval and rollback | No automatic service, network, credential or production changes |

The first generated-code canary must be a new synthetic project. Verify denial of network escape, host filesystem/secret access, test/result tampering, runaway output, process leaks and timeout evasion. Code may not share the independent verifier's Python process or authoritative storage. Merely importing tests beside generated code is not an independent security boundary.

Provisioning the backend, installing images or launchers, changing live worker permissions, running generated code, starting previews/services on the VPS, deployment and production restarts require explicit approval of concrete operations. No current result certifies that capability.
