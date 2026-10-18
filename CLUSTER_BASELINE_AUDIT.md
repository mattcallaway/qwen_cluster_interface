# Cluster Baseline Audit — 2026-10-15

Branch `audit/three-node-acceptance-20261015` (from `b2d14cb0c04e95a9e49d45a9ab47555a6e634417`, verified equal to remote `feature/laptop-node-and-model-discovery`).
Raw evidence: `audit_evidence/`. "Verified" = observed live in this audit; "Reported" = taken from earlier reports.

## 1. Inventory

| | Controller `controller-local` | VPS `qwen-vps` | Laptop `qwen-laptop` |
|---|---|---|---|
| Host | `desktop-44h6qvp`, Win10 | hostname `localhost`, Ubuntu 24.04 | hostname **`qwen-laptopt`** (typo, verified) |
| Net | LAN .4.69, TS 100.122.228.19 | TS 100.71.194.68, pub 172.233.140.74 | LAN .4.48, TS 100.101.187.115 |
| HW | 6 logical cores, 24.5 GB | 1 vCPU, 961 MB RAM, 495 MB swap | i3-6100U (AVX2) 2C/4T, 3.8 GB RAM, 36 GB swap, HDD-class disk ~85-90 MB/s |
| Code | prod tree HEAD `2e569e1` **+ dirty tree**; dev/remote `b2d14cb` | older full checkout (src hash `0ae351c8…`) | `vps_agent/` subset (hash `b31329ba…`) |
| Service | WinSW `QwenCollective`, PID 41484 started 03:33 | `qwen-worker.service` active | `qwen-worker.service` + `ollama` active |
| DB | SQLite schema v8, WAL | n/a | n/a |

Sandbox runner `/usr/local/bin/qwen-sandbox-runner` SHA-256 `bc7c8fca…d739` — **identical on VPS and laptop** (verified). Sudoers for `qwen-worker` is limited to the runner on both (verified).

Historical node `node-d35568f5` ("Linode"): offline, disabled, IP = VPS public IP. Preserved; no tasks reassigned.

## 2. Controller health (verified)
- `PRAGMA integrity_check` ok; 0 FK violations; no duplicate IPs; queue empty at start.
- Backups (integrity ok): `qwen_collective.pre-audit-20261015T104855Z.db` (sha256 `27b82e6c…d928`) and `…pre-backendfix-20261015T110008Z.db`.
- Unauthenticated worker API → 401 (register → 422); UI → 302 login (local and Tailscale HTTPS); bogus bearer → 401.
- Port 8000 bound to 127.0.0.1 only. TLS: Let's Encrypt, TLS 1.3, valid to Jan 2027.
- Latency (local): p50 ≈16 ms / p95 ≈17-26 ms. Tailscale HTTPS (new conn each request): p50 ≈432 / p95 ≈447 ms.
- Two distinct unrevoked worker tokens (`tok_855aa0b64e24` qwen-vps, `tok_e94656683def` qwen-laptop), both used within seconds of the audit.

## 3. Findings

| # | Sev | Finding | Status |
|---|---|---|---|
| 1 | Medium/High | No heartbeat-staleness logic: "online" is sticky. **Re-demonstrated live in Test G**: still `online` 124 s after worker stopped | Fixed in dev (`node_health.py`); **needs prod deploy** |
| 2 | Medium | Laptop advertises `inference` categories but worker had no handler (`unsupported_operation`) | Handler in dev (loopback-only); **needs worker deploy** |
| 3 | Medium | I had seeded 2 `inference_backends` rows as `ready` at laptop:11434 (unreachable by design) | **Fixed live** (status=error, inactive, reason recorded; backup first) |
| 4 | Medium | Laptop `worker.env` still held consumed `QC_ENROLLMENT_TOKEN`; lacked `QC_ENV=production`, `QC_ALLOW_SUBPROCESS_FALLBACK=0`, `QC_MAX_CONCURRENT_JOBS=1`, `QC_ALLOW_ALT_ISOLATION=0`. (My earlier report wrongly said it was scrubbed.) | **Fixed live**, backup + rollback recorded |
| 5 | Medium | **Scheduler: RAM constraint fail-open.** Unpinned `min_ram_mb:1500` task `task-477901a3` was leased by the 961 MB VPS (older worker doesn't send RAM in lease polls; check skipped when None) | Fixed in dev (fallback to heartbeat RAM, fail closed) + 2 regression tests; **needs controller deploy** |
| 6 | Medium | Production working tree drifts from git (uncommitted synced files) | Documented; reconcile at deploy |
| 7 | Medium | No pause/drain mechanism (`is_enabled` ignored by worker auth; token revoke is destructive) | Open — roadmap |
| 8 | Low | Model match is loose bidirectional substring | Open |
| 9 | Low | Node records show empty `tailscale_ip`/`tailscale_status=unknown` for workers | Open |
| 10 | Info | VPS public 4000/tcp (unrelated Node app), UFW 22/80/443/4000; Windows 0.0.0.0 listeners (135, 445, 8181 unknown…) | Not changed (out of scope) |
| 11 | Info | Laptop battery dead (0%), AC only; hard shutdown on power loss | Noted |

## 4. Revision comparison
Running controller process pre-dates `infrastructure.py` sync and has none of the dev fixes. Production tree ≠ remote. VPS tree ≠ remote (older). Laptop tree = subset of remote. **Nothing running in production contains the audit fixes.**
