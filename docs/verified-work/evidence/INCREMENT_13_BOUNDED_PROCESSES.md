# Increment 13: Process-tree deadlines, bounded output and cancellation

Isolated development candidate, 2026-10-27. No production deployment/migration, restart, live VPS access, hold removal, model inference or Russian River regeneration occurred.

## Changes and evidence boundaries

The reviewed-script executor now uses a fixed supervisor and a parent-owned Windows kernel Job Object. The supervisor joins the job before launching target code, closes its local job handle and starts an independent deadline watchdog. Only the parent retains the job handle; parent termination closes that handle. Breakaway flags are not enabled, and a 32-process ceiling is configured. Job accounting and pipe drainage are checked before reporting process cleanup.

Stdout and stderr are drained in 4 KiB reads into one bounded 64 KiB capture budget. Overflow is an execution failure that terminates the job, not successful output silently truncated after unlimited buffering. The worker's display summaries remain independently limited.

Timeout, cancellation, output overflow, live descendants after the main process exits and uncertain cleanup all prevent success. Artifacts are collected only after process cleanup is confirmed. If process cleanup is uncertain, the workspace is retained. A workspace deletion failure also returns an error and suppresses artifacts. Structured stdout cannot override the new trusted process-control metadata.

The worker now cancels reviewed scripts when a lease heartbeat is denied, returns any non-200 status or encounters a network error. The controller's renewal transaction rechecks the script feature flag, explicit node permissions/hold, current native project/run/approved plan and consumed authorization record. An isolated native test changes the controller-side hold during a reviewed job, verifies descendant termination and records an error through the actual submission API.

## What this does not establish

This is Windows process-lifetime control. It does not isolate files, secrets, the network or verifier authority, and it does not enable generated-code execution. No hostile/generated code, model or actual VPS was used in verification.

The Linux backend is deliberately unavailable where no verified process-tree boundary exists; there is no weaker process-group or unrestricted subprocess fallback. Source inspection of the existing VPS launcher found that timing out its `systemd-run` client does not independently prove cgroup-wide termination. That path remains NO-GO pending an actual Linux containment/cgroup implementation and isolated verification. The launcher was not changed or deployed in this increment.

Revocation is detected at a heartbeat, not instantly. Current intervals are 5–15 seconds plus request timeout; the original hard runtime bound remains independent. General filesystem deletion latency, disk quotas, memory/CPU limits and race-resistant hostile-filesystem collection are not certified here. Existing safety holds and software certification blocks remain.

## Development findings

- The first smoke test exposed a newline expectation mismatch in raw Windows pipe output; the assertion now compares logical lines. The deadline test already passed.
- An expanded run initially failed collection because a parameter used pytest's reserved `request` name; it was renamed.
- A subsequent run passed 90 cases and failed the orphan classification assertion. Investigation found the virtual-environment launcher was cleaning up descendants itself. The supervisor and process fixtures now bypass that launcher using the trusted base interpreter, so descendant/orphan tests exercise this implementation rather than incidental launcher cleanup.
- The corrected focused run passed 91 tests (`increment13-focused-tests.txt`).
- The final expanded selected suite passed **328 tests**, including **25 new process/cancellation cases**, with the independent stalled-parent watchdog case passing. See `increment13-integration-tests.txt`.

The process tests inspect actual fixture process identities, including children/grandchildren placed in a new process group, and verify they no longer run after timeout, cancellation or parent crash. Native API tests use fixture-created project/plan/task records, real authenticated TestClient routes, reviewed code and local subprocesses. They do not exercise scheduler planning or live fleet readiness.

The existing **31 focused containment/provenance/negation/Decimal checks passed**. The reviewed web fixture and its **10 browser checks** were rerun successfully. **42 documentation links/entrypoints passed.** TestClient/httpx deprecation and duplicate OpenAPI operation-ID warnings remain (8 warnings in the selected integration run). This is selected regression evidence, not the complete repository suite.

## Documentation, sources and next gates

The integrated README, technical map and execution protocol now link `docs/verified-work/PROCESS_CONTROL.md`. That reference records the implementation, limitations and Microsoft API sources consulted: [Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects), [extended limit structure](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_extended_limit_information), and [job accounting/query](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-queryinformationjobobject). Local process tests, not documentation claims, supply the implementation evidence.

Next: a concrete Linux cgroup/containment backend and protected external verifier/result path, with fixed image/tool inputs and hard resource budgets. Any actual provisioning, live generated-code execution, VPS service/preview starts, network changes, deployment, migration, restart or hold removal still needs explicit approval of that operation. Current production readiness remains NO-GO.
