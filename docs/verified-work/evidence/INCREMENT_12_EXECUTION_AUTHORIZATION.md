# Increment 12: Authenticated, one-shot script start authorization

Isolated development candidate. No production deployment/migration, service restart, live VPS connection, hold removal, model inference or Russian River regeneration occurred.

## Implemented

The worker now obtains a fresh online start permit rather than treating a manifest/checksum in task JSON as sufficient execution authority. The controller's signing secret stays on the controller. The authenticated worker route verifies the existing HMAC using stored task bytes and reloads the current worker restrictions, native approved plan, task, assigned worker and active lease.

Only exact reviewed library scripts dispatched under the current approved plan are eligible. Unreviewed/generated scripts are denied even with a valid legacy signature and approval flags. The node must explicitly permit scripts and operations and have no operational hold. Existing named-worker class restrictions still apply.

The controller validates task/lease/worker/idempotency/type, issuance time, expiry, network denial and allowed capabilities. It commits an append-only authorization record before replying, unique by task and lease. Replays, lost responses and crashes do not grant another start. A fresh approved task is needed for recovery.

The agent validates a nonce-bound response over a canonical HTTPS origin (loopback development excepted), refuses redirects and disabled TLS verification, and derives a monotonic five-second start deadline from request start. A local typed permit is consumed once and checked again immediately before launch. Runtime is clamped to at most 30 seconds, the reviewed script's limit and remaining lease time. A JSON success flag cannot become a typed permit.

Scripts now default off locally and are not advertised when disabled. Server authorization defaults held. Explicit development schema installation and separately approved server/worker flags and registry permissions are required. No startup migration or automatic permission change is added.

## Native integration evidence and limits

An isolated test uses fixture-created native project/plan/task records, then exercises the actual authenticated lease API, worker agent, authorization API, a real subprocess running the fixed reviewed web fixture, and result submission into append-only execution history. It does not invoke model inference or exercise scheduler planning. The reviewed subprocess uses an explicit development fallback; it is **not** proof of OS containment, a live VPS run or autonomous software delivery.

Other existing reviewed-code executor tests now supply explicitly labeled synthetic local permits. Runtime code does not import that test helper or enable an environment-based authorization bypass. Those tests verify executor behavior, not controller authentication. The standalone local web rehearsal also records that controller start authorization was not verified; it remains a developer fixture.

Important remaining blockers:

- Authorization establishes lease validity at grant time. Revocation after the reply is not instantaneous, and current heartbeat error handling does not reliably stop an already-running process.
- Streaming output/resource limits, full descendant cleanup, race-resistant artifact reads, launcher ownership and actual OS confinement remain unverified for hostile/generated code.
- The existing server HMAC uses controller configuration; separate protected signing authority/rotation and independent certification authority are not provisioned here.
- The online response relies on the authenticated controller channel and trusted worker process. A compromised worker/controller/administrator is outside this permit object's protection. It is not an offline signature verifier.
- Generated-code Class C execution and all software certification remain held. The new gate is not approval to deploy or to lift fleet holds.

## Build/test protocol

`docs/verified-work/EXECUTION_PROTOCOL.md` specifies the next, still-unimplemented boundary: pinned proposal/tests/image/policy, independently verified OS confinement, fixed build operations, offline locked dependencies, hard resource budgets, protected external behavior verification, immutable result bindings, bounded repair and separate staging/release approval. It explicitly forbids treating tests imported beside hostile code as an independent security boundary.

## Verification record

The initial focused run passed 66 tests (`increment12-focused-tests.txt`). The final expanded selected suite passed **303 tests**, including **39 new authorization cases**, after tightening TLS-verification type handling and package metadata. Its result is recorded in `increment12-integration-tests.txt`.

The checks cover forged signatures and identities, expired/replaced leases, disabled/held/revoked workers, missing schema/feature flags, changed plans, signed invalid capabilities/timestamps, non-library code, delayed/wrong-nonce responses, replay, local permit reuse/mismatch/expiry, missing OS isolation, redirect/TLS denial, disabled local advertisement, and the actual isolated agent flow.

The 31 focused provenance/containment/negation/financial checks also pass. The reviewed local web fixture and its 10 browser checks were rerun successfully. This is selected regression evidence, not the full repository suite. Existing TestClient/httpx and duplicate OpenAPI operation-ID warnings remain.

## Documentation and approval gates

The candidate's integrated README/work register and technical map now describe increment 12 and link the execution protocol. Original documents and earlier evidence are preserved. **34 documentation links/entrypoints passed.** Final packaging checks tracked original-source fingerprints and cumulative patch applicability; the rebuilt standalone worker ZIP remains a development artifact and is not installed.

Next isolated work: enforce process-tree deadlines, streaming output/resource limits and protected result handling; then implement and independently test a concrete containment backend proposal. Actual provisioning, generated-code execution, VPS preview/service starts, production migrations/deployments/restarts, networking changes and hold removal require explicit approval of those operations. Current production readiness remains NO-GO.
