# Cluster Optimization Report — 2026-10-15

## Applied (live)
| Change | Where | Why / evidence | Rollback |
|---|---|---|---|
| Ollama drop-in `/etc/systemd/system/ollama.service.d/qc-tuning.conf`: `MAX_LOADED_MODELS=1`, `NUM_PARALLEL=1`, `KEEP_ALIVE=15m`, `CONTEXT_LENGTH=4096` | laptop | Never co-load models on 3.8 GB RAM; one request at a time on 2 cores; avoid 20–38 s cold reloads between jobs. Verified via `systemctl show ollama -p Environment`; both models still listed | `bash /var/backups/qc-audit-20261015T105938Z/ROLLBACK.sh` |
| Sleep/suspend/hibernate targets masked; logind lid/idle = ignore (`/etc/systemd/logind.conf.d/qc-nosuspend.conf`) | laptop | Suspend would silently take the worker offline | same ROLLBACK.sh |
| `worker.env`: remove `QC_ENROLLMENT_TOKEN`; add `QC_ENV=production`, `QC_ALLOW_SUBPROCESS_FALLBACK=0`, `QC_MAX_CONCURRENT_JOBS=1`, `QC_ALLOW_ALT_ISOLATION=0` | laptop | Credential hygiene + explicit no-fallback gate. Worker re-authenticated and ran a task post-change (task-42719add) | `sudo cp -p /var/backups/qc-audit-20261015T105938Z/worker.env.20261015T110607Z.bak /etc/qwen-collective/worker.env && sudo systemctl restart qwen-worker` |
| `inference_backends`: two laptop rows → `error`/inactive | controller DB | Endpoint unreachable; dashboard falsely showed ready | restore `qwen_collective.pre-backendfix-20261015T110008Z.db` (stop service first — needs approval) |

## Evaluated and rejected
- `performance` CPU governor: warm tok/s unchanged. Rejected, restored `powersave`.
- `vm.swappiness=5`: no warm benefit; cold comparison confounded. Rejected, restored 15.
- `num_thread` 3/4: slower than default 2.
- Wi-Fi power-save off: 0 % loss, 3 ms median Tailscale RTT (p50 ≈3 ms, max 65 ms) — no benefit, nonzero lockout risk. Not changed.
- Shrinking/removing 36 GB swap: no harm shown. Not changed.

## Code changes (dev branch, NOT deployed)
`app/services/node_health.py` (new), `infrastructure.py`, `machine_registry.py` (heartbeat-staleness demotion, 60 s), `app/services/worker.py` (RAM constraint fail-closed w/ heartbeat fallback), `vps_agent/executor.py` (loopback-only inference handler), `tests/test_audit_repairs.py`. Full suite: **335 passed**.

## Before/after
| Item | Before | After |
|---|---|---|
| Laptop warm tok/s | 17.3 / 14.3 | unchanged (no regression) |
| Laptop idle MemAvailable | 1.94–2.17 GB | 2.1–2.2 GB |
| Co-loaded models possible | yes (2×1.1–1.5 GB) | no (capped at 1) |
| Controller view of dead worker | `online` indefinitely | still so in prod until deploy; dev: `offline` after 60 s |
| RAM-gated task on 961 MB VPS | leased (bug) | dev: refused; prod: awaiting deploy |

## Baseline metrics
Heartbeat interval ≈10 s (observed hb_age cadence). Queue→lease: 1.2 s (VPS), 4.6 s (laptop), 0.2 s (laptop post-change), 8.4 s (unpinned F4). Sandbox task runtime ≈2.1 s unit time on both nodes (VPS memory peak 1.2 MB). Registration/auth: 0 failures observed. Worker recovery: service start → lease in 3.8 s.

## Addendum - memory/swap tuning on qwen-laptop (2026-10-15 13:35 PDT, requested by user)
| Change | Detail | Rollback |
|---|---|---|
| `vm.swappiness` 15 -> **100** | Edited `/etc/sysctl.d/99-qwen-performance.conf` (it overrode my new drop-in); also `/etc/sysctl.d/99-qc-memory.conf` | `bash /var/backups/qc-audit-20261015T105938Z/ROLLBACK-memory.sh` |
| **zswap** enabled (zstd, 25 % max pool) via `qc-zswap.service`, persistent | compressed RAM cache in front of the 36 GB `/swap.img` | same script |

Measured (`audit_evidence/bench_v3_swap100_zswap.json`, quick mode): no throughput change (Qwen 16.9 / 12.7 tok/s, Llama 14.0-14.4 / 10.6 tok/s warm; all `READY 42`). Idle desktop pages are now pushed out: swap in use ~1.2 GB, of which zswap holds ~0.4 GB of pages in ~0.1 GB of RAM (~3.5:1). Free-for-model memory before the run 612 MB -> 959 MB at start and 2.4 GB after. Llama cold load showed 699 swap-in pages (mild; 27 s vs 25 s earlier, within noise).

**Why not "use all 36 GB":** swap only ever holds *idle* pages. Using more of it means paging out memory the model/desktop still needs; with ~90 MB/s disk that turns inference into minutes per token. The setting above uses swap as aggressively as is useful (idle anon memory goes to compressed RAM first, then disk) without hurting generation. Larger models (e.g. 7B) were **not** pulled: they would exceed RAM and page continuously.

Remaining memory hogs (not changed, user desktop): `gnome-system-monitor` ~350 MB, `snap-store` ~190 MB, `gnome-control-center` ~195 MB, GNOME shell ~140 MB. Booting to `multi-user.target` (headless) would free ~0.7-1 GB; needs a reboot and is not done.

