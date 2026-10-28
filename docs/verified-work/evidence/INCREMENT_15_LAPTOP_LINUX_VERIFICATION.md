# Increment 15: Real Linux fixture verification on the laptop

Performed 2026-10-27 Pacific; raw evidence timestamps use UTC. The user authorized using the laptop or Pi for real containment tests. The laptop was selected after read-only inspection. This was an isolated, manually operated fixture scope on an existing inference node, not a declaration that the whole laptop is a dedicated non-production machine.

## Host selection and scope

The laptop has x86-64 Ubuntu 24.04.2, systemd 255, cgroup v2, four logical CPUs, about 3.7 GiB RAM with about 2.8 GiB available at inspection, and substantial free disk. Installed image-building tools and existing passwordless sudo were confirmed. Existing SSH key and known-host verification were used; supplied passwords/private keys were not copied into scripts, images or evidence. The 2 GB Pi was not used for the tests and its services were not changed.

No packages were installed. No existing worker, Ollama or proxy service was restarted or reconfigured. No inference or model-generated code ran. No controller registry, capability flag, production application deployment or existing hold was changed.

The setup created a separate `/var/lib/qwen-sandbox/` test namespace and a short-lived root-owned `/etc/qwen-sandbox/test-host.json` policy. The policy was removed after every execution batch and its absence checked afterward. Read-only images, source snapshots and audit records were retained as inactive review artifacts. Only this run's ten transient test units were reconciled; all ten cgroups were absent or empty at the end.

## Pinned test image

- SHA-256: `43b057aa6a7133de2bbed57a8caaeecd07aea9228fa7cb6bec1608286b967b61`
- Size: 12,591,104 bytes.
- Recipe: installed `/usr/bin/python3.12`, selected standard library, and `ldd`-resolved shared libraries, packaged with the already-installed `mksquashfs`. No host home, application configuration or credentials were copied. The image contains only a synthetic account/NSS configuration.
- The image and each source snapshot were rehashed before launch. Tests used dynamic non-root identities, read-only source/image mounts and the candidate cgroup policy.

## Observed results

| Live fixture | Observation | Cleanup |
|---|---|---|
| Smoke (repeated) | Python ran as a dynamic non-root UID inside the image | Confirmed |
| Isolation probes | Seven checks passed: non-root, host-only synthetic marker hidden, known credential environment variables absent, IP sockets denied, source read-only, cgroup control writes denied, 32 MiB scratch cap reached | Confirmed |
| Deadline | Sleeping fixture stopped at the bounded deadline | Confirmed |
| Output flood | Stopped on `output_limit`; 65,536 bytes retained, overflow detected | Confirmed |
| Orphan child | Main exit 0 did not pass; live descendant produced `orphaned_descendant`, then was stopped | Confirmed |
| Memory pressure | Kernel `memory.max=67108864`, swap max 0; manager reported `oom-kill` on attempted 256 MiB allocation | Confirmed |
| PID pressure | Kernel `pids.max=8`; only seven children spawned before denial | Confirmed |
| Client loss | The running service still expired after its `systemd-run` client was killed; manager result `timeout`, about 2.45 seconds through cleanup | Confirmed |

Kernel `cpu.max=100000 100000` and manager CPU quota were read back. This is configured-quota evidence, not a CPU stress benchmark. Full memory/CPU/filesystem/kernel escape certification is not claimed.

Raw evidence is in `laptop-linux-isolation-tests.json`, `laptop-linux-resource-tests.json` and `laptop-linux-final-state.json`. A separate fixed-oracle checker evaluates the recorded observations: **14/14 evidence checks passed**, including exact image/source consistency, OOM/PID outcomes, cgroup emptiness, policy removal and service preservation. See `laptop-linux-evidence-checks.json`. These hashes/checks are integrity and reviewed-fixture evidence, not a cryptographic native execution attestation.

## Real failures found and corrected

1. Image preparation initially refused standard-library symlinks. Inspection distinguished an internal Python alias from an excluded development directory. The recipe now dereferences only reviewed internal links and omits excluded development content. Partial setup was explicitly reconciled rather than overwritten blindly.
2. The first real service failed with `226/NAMESPACE`: `/var/tmp` was missing from the read-only image. No application code ran. The directory was added and `/var/tmp` received its own requested bounded temporary mount.
3. Systemd startup is asynchronous, and a completed service may clear `ControlGroup`. The backend now waits boundedly for startup and accepts an empty terminal cgroup name only after the exact cgroup was previously observed, the owner still matches, and MainPID is zero. Kernel emptiness must still be checked.
4. A zero main exit with a populated cgroup now produces an orphan failure before cleanup. Unit ownership checks and both kill/stop attempts remain mandatory.

Initial failures are retained in `laptop-linux-canary-initial-failure.json`, `laptop-linux-canary-namespace-failure.json`, and `laptop-service-diagnostic.json`. They were not marked successful or hidden by regenerating acceptance records.

## Existing services preserved

Before and after both successful batches and final reconciliation, these services remained active/running with unchanged MainPID:

- `qwen-worker.service`: 554427
- `ollama.service`: 1432
- `qc-ollama-ts.service`: 519545

This verifies that the existing processes were not restarted by the test work. It is not a claim that tests caused zero transient resource contention. They used bounded scratch/memory/tasks, at most one CPU quota per test service, and one image-builder thread.

## Regression and documentation

The focused Linux suite passed **36 tests**, including new terminal-cgroup cases. The final selected integration run passed **364 tests**; see `increment15-integration-tests.txt`. This is not the complete repository suite. Existing dependency/OpenAPI warnings remain. The unchanged web fixture was not rerun or presented as Linux web-app acceptance.

The **31 focused safety checks** also passed, and **66 documentation links/entrypoints** were verified.

The root README/technical-map overlay, Linux reference, release gates and a new laptop-verification reference record the real observations and remaining blockers. Reviewed harnesses and the evidence checker are preserved for audit without credential values.

## What remains held

All backend/plan results still retain `accepted=false`, `certification_eligible=false`, and no production approval. This was a trusted operator test harness, **not** a native controller → lease → worker model-generated project. It must not substitute for that provenance or certify general application correctness.

Next work is the native generated-code authorization/dispatch path and protected external verifier, followed by a new synthetic project with pinned independent requirements/tests. More adversarial/kernel testing and test-authority separation are required before a broad containment verdict. Production deployment, service restarts, capability/hold changes and rollout still require a concrete approved plan. No production release is authorized by these fixture results.
