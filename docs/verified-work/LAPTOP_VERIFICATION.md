# Laptop Linux fixture verification

The user selected the laptop or Pi for real tests. Read-only comparison selected the x86-64 laptop with greater available RAM and installed tooling. It remains an existing inference node; testing used only an isolated temporary sandbox scope and did not change existing worker/model services or holds.

See [increment 15 report](evidence/INCREMENT_15_LAPTOP_LINUX_VERIFICATION.md), [Linux backend reference](LINUX_SANDBOX.md), [work register](README.md), [technical map](TECHMAP.md), and [release gates](PRODUCTION_RELEASE.md).

## New evidence

The image ran reviewed fixtures on actual systemd 255/cgroup v2. Observed checks cover non-root execution, hidden synthetic host data, IP socket denial, read-only source, denied cgroup writes, bounded scratch, kernel memory/OOM and PID caps, output overflow, deadline, orphan cleanup, and service expiry after client loss. Root-owned manager observations and cgroup limit files were recorded; the independent fixed-oracle checker passed 14 checks.

Evidence files are in `evidence/`: `laptop-linux-isolation-tests.json`, `laptop-linux-resource-tests.json`, `laptop-linux-final-state.json`, and `laptop-linux-evidence-checks.json`. Image SHA-256 is `43b057aa6a7133de2bbed57a8caaeecd07aea9228fa7cb6bec1608286b967b61` (12,591,104 bytes). Source/backend hashes are recorded separately. No password or private-key values are part of the records.

All ten test cgroups were empty/absent at final reconciliation. The temporary host policy was removed. Existing worker, Ollama and proxy PIDs remained unchanged. Images, input snapshots and root-only audit reports remain under `/var/lib/qwen-sandbox/` as inactive evidence; there is no newly installed daemon or automatic execution path.

## Corrections driven by Linux results

Image layout must include `/var/tmp`. Completed cgroups require prior observed identity, matching owner and terminal MainPID before an empty manager path can be accepted; actual kernel emptiness remains required. Startup observations are retried only within a bound. Main exit zero with live descendants is an error. These corrections were locally regression-tested and exercised on the laptop.

## Limits

The fixtures were reviewed operator diagnostics, not model-generated code and not the native production controller/worker chain. CPU quota was read back but not stress-benchmarked. Full hostile-code escape and verifier-tamper coverage remain incomplete. Backend results stay ineligible for certification, and generated-code dispatch remains unwired/held. Fresh explicit approval is still needed for production rollout or hold changes.
