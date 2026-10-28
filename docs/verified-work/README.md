# Verified work register

Updated 2026-10-27. Development review candidate; **not deployed**. This register supplements the existing [README](../../README.md), canonical [technical map](../../TECHMAP.md), and [remediation program](../remediation/README.md). Earlier remediation stage statuses are preserved; this increment numbering is a separate development evidence sequence, not a claim that historical stages passed.

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
- Host integrity verified: unchanged service PIDs, temporary test policy removed. Evidence saved in docs/verified-work/evidence/laptop-native-canary-f706a49af04d4182a27a5887d1f9fc85.json.
