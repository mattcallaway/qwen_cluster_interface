# Increment 14: Linux containment candidate and production release preparation

Development candidate. No production deployment/migration, service restart, hold removal, remote connection, model inference, generated-code execution or Russian River regeneration occurred.

## Concrete implementation

- `vps_agent/linux_sandbox.py`: fixed Python-profile systemd/cgroup v2 backend candidate, root-image/source validation, strict limits, streamed bounded output, exact manager/cgroup observations, cgroup-wide kill/stop and emptiness checks. A fresh per-launch owner token must match the manager's service description before kill/stop, so a colliding/replaced unit cannot be controlled as if it belonged to this operation.
- Explicit root-owned, expiring non-production host policy binds the actual host identity and permitted image hashes. It is not created by this package; a privileged harness alone is insufficient. No broader polkit/sudo rule is added.
- `CodeProposalReview.prepare_linux_plan()`: fresh native proposal/test/contract validation and an append-only reviewable service plan. Every plan remains non-dispatchable, unverified and unapproved for production. The image hash is a proposed pin, not implicit image approval.
- `scripts/probe_linux_sandbox.py`: a read-only readiness inventory with no service/package/policy changes and no capability certification.
- Integrated Linux reference and production release/rollback gates in the existing README/technical map documentation tree.

The runtime profile requests read-only image/source, separate bounded scratch, dynamic identity, no capabilities/new privileges, network denial and namespace protections. Deadlines belong to the managed service, not just its client. It requests memory/no-swap, tasks and CPU caps. The application cannot mount tests, choose shell commands or turn stdout into manager/verifier authority. Both kill and stop are attempted even if one control operation fails; client exit never proves cgroup emptiness.

## Critical qualification

This is **backend source and modeled control verification, not verified Linux containment**. The candidate is not wired to native worker or generated-code dispatch. All results retain `backend_verified=false`, `accepted=false` and `certification_eligible=false`. The host adapter is an explicit, privileged non-production operator harness and must not be invoked on production as an improvised generated-code path.

Native generated-code authorization, a protected external behavior verifier, approved runtime image/dependencies, real kernel enforcement evidence, a model-generated native canary and a production rollout are still missing. The existing Class C/software certification holds are not lifted by this work.

## Actual environment observations

`linux-environment-check.json` records the limitations. WSL list attempts returned install/help text with exit 1. Docker/Podman CLIs were not discovered. SSH exists, but no readable configured SSH target was discovered. The read-only live controller inventory query failed; current fleet health/hold state was not refreshed or inferred from older reports.

The user has been asked for a non-production Linux SSH host alias/address and username through existing trusted access, without passwords/private keys in chat. No target was guessed, no SSH fingerprint was automatically accepted and no host was contacted. Real Linux acceptance is blocked on that environment/input; code, tests and release preparation continued independently.

## Verification scope and findings

The first 21 Linux candidate checks passed. Additional review tightened unit ownership before destructive control and explicit host/image approval. The final selected suite passed **362 tests**, including **34 new Linux candidate/host-policy cases**; see `increment14-integration-tests.txt`. The earlier 21-test focused log is retained as `increment14-focused-tests.txt`.

The Linux tests inspect generated argv and use modeled service-manager/cgroup responses. Their local subprocesses only provide reviewed fixture pipe data on Windows; they are **not Linux kernel tests**. Policy-parser tests mock host files and do not prove actual root ownership. The existing Windows process tests continue to exercise real Windows jobs separately.

Cases include malformed identities/hashes/limits, source traversal/duplicates, missing Python entrypoint, changed native proposal versions, unknown/mismatched manager identity, collision/replacement protection, failed cleanup, lack of observed cgroup emptiness, timeout independent of client exit, cancellation before start, forged application success flags, unsupported real backend on Windows, and explicit bounded host/image policy.

The **31 focused provenance/containment/negation/Decimal checks pass**. **55 documentation links/entrypoints passed.** This is selected regression testing, not the entire repository suite or production acceptance. Existing TestClient/httpx deprecation and duplicate OpenAPI operation-ID warnings remain (8 warnings in the integration run). The web fixture was unchanged in this increment; prior browser evidence is retained, not represented as a new Linux web-app test.

## Approval and release gates

`docs/verified-work/PRODUCTION_RELEASE.md` defines:

1. Identify and read-only inspect a non-production test host.
2. Review/approve exact isolated image, policy, service/provisioning scope and cleanup operations.
3. Independently verify real kernel enforcement and failure behavior.
4. Implement and accept native generated-code dispatch plus protected verifier authority using a new synthetic project.
5. Review fresh production state, exact build/config/schema/restart scope, backups, canary and rollback before approving live changes.

No current test result waives these gates. The cumulative patch remains a review artifact, not permission to overwrite a dirty production checkout. Linux host provisioning, generated-code execution, VPS services/previews, network changes, deployment/migration/restarts and hold changes still require approval of concrete operations.

Primary implementation references are linked in `docs/verified-work/LINUX_SANDBOX.md`: systemd service/kill/execution documentation and the kernel cgroup v2 documentation. Actual host versions and behavior must be checked; requested properties alone cannot certify enforcement.
