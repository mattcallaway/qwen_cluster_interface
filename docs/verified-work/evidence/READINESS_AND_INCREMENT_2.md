# Readiness and second implementation increment

October 26, 2026. No production files, holds, credentials, services or historical projects were changed. No restart, inference request or remote Pi connection was made.

## Readiness findings

The read-only database observation at 01:48 Pacific showed:

| Node | Enablement | Readiness evidence | Required next action |
|---|---|---|---|
| Desktop | Enabled in both registries | Heartbeat about 47 minutes old; one active credential record; inventory lists qwen2.5:3b and 1.5b | Diagnose worker heartbeat/connection before attempting native execution. Do not treat the stored online label as current health. |
| Pi | Disabled in both registries | No active credential; heartbeat more than two days old; inventory lists qwen2.5:0.5b and 1,344 MB available RAM; last two recorded executions failed with exit 127 | Verify installed worker/runtime and authentication, then prepare an explicit inference-only enablement and canary plan. Historical RAM is not current capacity. |
| Laptop | Enabled | Heartbeat 14 seconds old; one active credential record | Current registry evidence is fresher; no task was dispatched. |
| VPS | Disabled | Operational and web-fetch holds remain set; no active credential | Preserve existing holds. |

Read-only local model-service GET requests returned version `0.40.11`, installed Qwen 3b and 1.5b models, and no models currently loaded. These requests did not load a model or generate content. The model service's responsiveness does not prove the desktop worker is authenticating or polling correctly. There is no current evidence that restarting Ollama is necessary.

Process inventory found Ollama and Python processes, but protected process metadata did not identify a current Qwen worker/controller command or loaded source revision. A restart cannot yet be targeted confidently. Do not assume Python processes are Qwen or terminate them on that basis.

The worker leasing source enforces a 300-second heartbeat threshold and active credentials for physical workers. Therefore removing Pi enablement holds alone would leave authentication and freshness failures. Desktop administrative enablement is already on.

Evidence: `node-readiness.json`, `local-model-health.json`, `process-readiness.json`.

## Candidate changes since the first package

Project aggregation now requires a nonempty, exact set of unique requested deliverable keys, accepted statuses, nonempty evidence references and native artifact digest verification. A supplied `passed=True` cannot override a rejected status. A configured inference action counts as executed only when a completed matching native lease and successful execution record exist. Database failures cannot imply success. The research hold now also applies at project aggregation and returns BLOCKED with score zero.

These checks reduce additional acceptance paths; they are not a replacement for controller-owned certification records. Evidence digest verification still needs exact deliverable/producer/attempt/contract binding and CAS-byte revalidation. The complete privilege boundary and trusted claim-evidence implementation remain outstanding. Other general artifact acceptance paths still require review. No claim is made that every bypass is closed.

## Validation

- **30 focused tests passed**, including seven new aggregation tests for missing/conflicting summaries, missing native executions, a valid native positive control, database failure and research hold propagation. `candidate-tests.txt` contains the final result.
- **12 existing worker-authorization tests passed** in the isolated copied app with a temporary database. Existing disabled-node, revoked-token, stale-heartbeat, registry-conflict and hold-preservation behavior remains covered. `worker-authorization-tests.txt` contains the result.
- **Objective compatibility: 6 passed, 3 failed.** `objective-compatibility-tests.txt` is intentionally retained. Two fixtures expect technical/substantive success without the required native evidence or despite the research hold. One engine test expects `substantive_evaluation` metadata, but an earlier blocked path does not write it. These require fixture migration and engine failure-reporting integration; they are not waived.

The test runner blocks outbound socket connections, while allowing the local socket pair required by Windows' asynchronous test machinery. Production environment-file loading is mocked. Initial harness setup issues (working directory and overbroad socket blocking) were corrected before the successful authorization test run; they were not product defects.

The patch and original-byte manifest were regenerated against the real repository files. The originals remain unchanged. The review package includes the revised source, tests and evidence. The initial review report's 23-test result describes increment 1; this document and the final logs supersede that test count.

## Next engineering work and approvals

Continue isolated development: migrate compatibility fixtures to real native evidence, make blocked engine paths persist consistent reasons, bind attestations to exact deliverable revisions, and implement trusted claim/source snapshots and typed financial integration. Preserve the research hold until a valid positive end-to-end path exists alongside adversarial rejection tests.

Desktop recovery needs a concrete worker configuration/log diagnosis. Pi recovery additionally needs installed-runtime and authentication verification. No new credentials should be generated and no holds lifted merely to make a readiness check pass.

Explicit approval is still required before production credential/registry changes, modification of a live source checkout, service deployment, or a named process restart. A restart may be appropriate once a worker fault or approved release requires it; none was performed or requested for approval here because the cause and release are not yet ready.

**Release status: NO-GO.** The compatibility failures and incomplete certification boundary remain visible release blockers.
