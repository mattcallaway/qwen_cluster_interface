# Production release gates and rollback plan

Current verdict: **NO-GO**. The user authorized continued backend/deployment preparation, not removal of holds or a concrete live deployment. This document integrates with the [work register](README.md), [technical map](TECHMAP.md), and [Linux candidate](LINUX_SANDBOX.md).

## Gate A: identify and inspect the test host

Required: a designated SSH target, existing authenticated access with trusted host keys, and an identified disposable testing scope. The read-only probe may inventory prerequisites; it does not start units or install a runtime. The laptop test scope is now known; production/VPS readiness remains unverified.

Increment 15 update: the user selected the laptop/Pi; the laptop was inspected and used for a bounded temporary fixture scope. [Laptop verification](LAPTOP_VERIFICATION.md) records actual results and preserved existing services. This completes host discovery for that test scope, not production/VPS readiness or a dedicated isolated-VM approval.

## Gate B: approve concrete isolated provisioning

Prepare and review the exact image source/build recipe and SHA-256, host paths, runtime UID/permissions, manager/kernel versions, limits, unit namespace, test commands, resource cost and cleanup plan. Proposed paths are `/var/lib/qwen-sandbox/images/<sha>.raw` and `/var/lib/qwen-sandbox/inputs/<proposal-sha>/`; these are not created by this package. Root-owned snapshots must contain no production credentials, private keys or acceptance authority. No broad systemd/polkit/sudo permission is granted by this candidate.

The root-owned `/etc/qwen-sandbox/test-host.json` authorization must bind a non-production host identity, approved image hashes and a short expiry. Neither root privilege alone nor a claimed backend success result substitutes for this approval. Creating that policy is part of the concrete provisioning scope, not an automatic installer action.

Explicit approval is needed before image preparation/installation, privileged service creation, package changes or other provisioning on the chosen host. Use a dedicated sandbox host/VM with no production data. If testing on a shared VPS is proposed, first review the added risk and the exact separation; the current plan does not assume that is approved.

## Gate C: independently accept containment

Run the negative/positive kernel suite described in `LINUX_SANDBOX.md`. Preserve image/source/policy hashes, effective manager properties, observed denied operations and kernel cgroup-empty evidence. Test daemonization, parent/worker/client loss, output pressure and resource exhaustion. Any missing feature, unknown cleanup or isolation escape is a failed gate. Mocked transports and generated `backend_verified` flags do not qualify.

## Gate D: accept a native generated-code canary

Implement the missing native dispatch and protected verifier authority. Use a fresh synthetic project with an approved, version-bound requirement contract, pinned independently authored tests and reviewed dependency/image inputs. Record the real controller → task → lease → worker → build/output → external verifier chain. Exercise both correct code and deliberately wrong results, and prove failed checks cause bounded revision rather than certification. No Russian River regeneration.

Only this evidence can justify a proposal to enable a narrow generated-code capability. Existing global certification and Pi/desktop/VPS holds remain until their particular acceptance conditions are met and a concrete change is approved. Passing containment does not approve a public web app or production release.

## Gate E: approve the actual production change

Before approval, provide the exact controller/worker build hashes, source baseline/drift check, configuration and schema changes, target services/hosts, downtime/restart requirements, canary scope and rollback triggers. Inspect fresh node credentials/health and hold state; prior cached inventory is insufficient. Snapshot the current release, configuration and database using a consistent backup method; do not copy a live SQLite main file without its consistency requirements.

Stage the candidate separately, run required regression/migration rehearsals, and preserve the previous binaries and configuration. The current cumulative patch is a review artifact and must not be blindly applied to a dirty production checkout. No production command has been executed by these preparation steps.

## Rollback and failure handling

On a failed canary, isolation denial failure, unknown cleanup, credential exposure, stale binding or regression: stop new admissions; revoke the narrow capability/keep holds; stop only the recorded sandbox unit through verified management; confirm its cgroup is empty; retain failed artifacts/quarantined workspaces for audit. Do not reset append-only evidence or replay a consumed start permit.

For an approved production rollout, restore the previously saved application/configuration through the reviewed service procedure and verify health before reopening admission. Database rollback must follow the separately rehearsed migration plan; never restore an old database over newly accumulated execution evidence. Live service stop/restart, restores and policy changes remain explicit operations requiring authorization unless already included in the approved rollout/rollback plan.

## Present status

Backend source, a pinned test image, Windows-host control tests and selected real laptop Linux observations are available. Full hostile-code containment acceptance, protected generated-code verifier, native generated-code canary and production rollout approval are **not available**. No production application deployment, existing service restart or hold change was performed.

Increment 15 supersedes that increment-14 snapshot for the laptop: a pinned 12.6 MB image and selected real Linux fixtures now have evidence, the temporary host policy was removed, and existing service PIDs were preserved. Inactive image/input/audit files remain under the separate test namespace. The protected verifier, native generated-code canary and production rollout approval are still missing. No existing worker/model service deployment/restart or capability/hold change was performed.

## Increment 16 gate update — 2026-10-28

The protected verifier/controller bridge is implemented and exercised against actual laptop confinement with synthetic native records. Gate D remains open: a real controller-dispatched, leased model-worker coding canary, bounded failure/revision and revocation checks are still required. Gates for production activation, restarts, deployment and hold removal remain separate and require concrete authorization. [Evidence](evidence/INCREMENT_16_PROTECTED_CODE_VERIFICATION.md).
