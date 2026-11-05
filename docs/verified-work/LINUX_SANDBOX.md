# Linux/VPS containment candidate

Development candidate only. See [work register](README.md), [technical map](TECHMAP.md), [execution protocol](EXECUTION_PROTOCOL.md), [release gates](PRODUCTION_RELEASE.md), and [increment evidence](evidence/INCREMENT_14_LINUX_BACKEND.md).

## Implemented operator harness; native dispatch held

`vps_agent/linux_sandbox.py` defines an explicit operator-harness backend for a systemd-managed cgroup v2 service. It is not connected to `executor.py`, worker startup, Class C dispatch or a public deployment route. The current production worker path still fails closed where no verified backend exists. No environment flag or imported schema silently selects this candidate.

`CodeProposalReview.prepare_linux_plan()` revalidates the recorded native proposal and approved test bindings and produces an append-only reviewable service plan. It includes source/image hashes, exact file inventory, approved tests, requirements, plan revision and a unique job identity. The proposed image still requires independent approval. Every plan has `dispatchable=false`, `backend_verified=false`, `production_approved=false`, `accepted=false`, and `certification_eligible=false`.

The first profile runs only `/usr/bin/python3 -I -B /src/src/main.py` inside the prepared root image. It does not expose a public app server, install dependencies or support arbitrary model-selected commands. Generated web-app previews will need a separately verified private browser/app channel and later release approval.

## Service policy and control

- A content-addressed, root-owned root image is checked against its exact expected SHA-256. The source snapshot is a separate exact, root-owned, non-linked file inventory; every file hash is rechecked. Writable ancestors, links, additional files and changed source bytes block preflight.
- An explicit root-owned `/etc/qwen-sandbox/test-host.json` policy must name `environment=non-production`, match the host machine-ID hash, allow the exact image and expire within seven days. This policy is never created by the candidate and is not production approval. Its preparation belongs to the separate provisioning gate.
- The service requests read-only image mounts, a read-only source mount, temporary bounded scratch, dynamic identity, empty capability sets, no new privileges, namespace/device/kernel protections, private networking, denied IP access and restricted address families. No acceptance/test artifact, controller credential directory or host home is mounted.
- The fixed policy requests a service-managed runtime maximum (at most 30 seconds), a two-second stop timeout, cgroup-wide kill, no restart/notification-based runtime extension, bounded memory/no swap, bounded tasks and CPU quota.
- The controller loop caps combined captured stdout/stderr at 64 KiB, handles cancellation/deadline/failure, and never interprets application stdout as manager or verifier authority.
- Cleanup calls both cgroup-wide kill and stop, even if one fails. A fresh per-launch owner token must match the service-manager description before control; colliding/replaced units are not killed. An observation must identify the exact unit/cgroup before `cgroup.events` can confirm emptiness. Client exit alone is insufficient. Unknown identity, failed control or uncertain emptiness blocks cleanup confirmation.

At increment 14 these were requested policy and modeled control behavior only. Increment 15 adds [actual laptop fixture observations](LAPTOP_VERIFICATION.md) for the pinned image/profile, including memory/PID limits and lifecycle/isolation checks. Broader hostile-code/verifier coverage and other hosts remain unverified. The operator API is not a production execution permission; model-generated dispatch and software certification remain held.

## Read-only readiness probe

`scripts/probe_linux_sandbox.py` reports platform/kernel, cgroup controllers, systemd version and legacy launcher metadata. It does not start/stop services, install packages, change policy, print environment secrets or mark the backend verified. It always retains a NO-GO verdict until separate acceptance evidence exists. Run it only against an identified host through approved access; no host is guessed and SSH fingerprints are not automatically trusted.

The increment 14 Windows environment did not provide a usable local Linux runtime or discovered SSH target. In increment 15 the user supplied the node context, existing laptop SSH access was verified, and isolated real fixtures ran without existing service restarts. Controller inventory/production readiness was not inferred from those laptop observations. See [laptop verification](LAPTOP_VERIFICATION.md) for exact temporary changes, cleanup and remaining scope.

## Acceptance that still must run

On a designated non-production Linux machine, independently check actual namespace/root-image isolation, host-file/credential denial, denied network egress, read-only source/test separation, memory/swap/CPU/task/scratch limits, output floods, daemonized/double-forked descendants, service-manager/client/worker failure, hard expiry and cgroup emptiness. Verify the manager's effective properties and source/image hashes, not just the requested argv.

Then wire a separately authorized native generated-code task to this backend and a protected external verifier. A new synthetic model-generated project must pass independently specified behavior tests; model claims and tests run inside the app process cannot certify success. That native dispatch and verifier authority are not implemented by this increment.

## Primary implementation references

The candidate follows documented systemd [service lifetime controls](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html), [cgroup-wide kill semantics](https://www.freedesktop.org/software/systemd/man/latest/systemd.kill.html), and [root-image/execution settings](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html), plus the kernel's [cgroup v2 populated state](https://docs.kernel.org/admin-guide/cgroup-v2.html). Version-specific availability must be checked on the test host. Documentation and a command policy do not establish real containment.
