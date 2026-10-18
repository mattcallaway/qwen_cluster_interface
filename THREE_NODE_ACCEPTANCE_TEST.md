# Three-Node Acceptance Test — 2026-10-15

Prefix `QC_ACCEPTANCE_20261015_`. Driver: `audit_evidence/scripts/accept.py`, `run_recovery_g.py`, `evidence.py`. All results below are **live** (real controller DB, real workers); unit tests are not counted. Pre-check: queue empty of foreign work (driver aborts otherwise).

**Deployed controller does not contain the audit fixes** (see Gate section). Tests were run against what is actually deployed.

## Readiness gates (honest)
| Gate | Result |
|---|---|
| Controller source & deployed revision verified | **PARTIAL** — prod tree dirty vs git; running process lacks dev fixes |
| DB integrity / backup verified | PASS (integrity ok, backup restorable-checked by open+integrity) |
| Controller / both workers healthy, recent distinct auth | PASS (tokens used <10 s ago, 2 distinct) |
| Tailscale + HTTPS cert | PASS |
| Sandbox runner integrity both nodes, no unrestricted fallback | PASS (identical SHA; live sandbox tests; laptop fallback now explicitly 0) |
| Ollama models respond locally | PASS |
| Model inventory accurate | PASS (laptop reports `qwen2.5:1.5b`, `llama3.2:1b`; VPS none) |
| Queue clean / test isolation | PASS |
| Regression tests | PASS (335) |
| Critical/high security findings resolved | PASS (none found); Medium findings 1,2,5 **fixed in dev, not deployed** |
| Rollback documented | PASS |

Proceeded with execution/recovery tests (they do not require the fixes). D/E require a deployment → stopped at gate.

## Matrix
| ID | Intent | Method / target | Task | Window (UTC) | Observed | Result |
|---|---|---|---|---|---|---|
| A | Authenticated live identity | DB: `infrastructure_nodes.last_contact_at`, `worker_auth_tokens.last_used_at` | – | 11:06:38 | qwen-vps & qwen-laptop heartbeat age ≤ ~10 s; tokens `tok_855aa0b64e24` / `tok_e94656683def` distinct, last used 11:06:34 / 11:06:33; legacy `node-d35568f5` offline (last contact 10-14 20:38); hardware (RAM 961/3802 MB, 4 cores, models) is worker-reported | **PASS** (liveness derived from token use + heartbeat, not badge) |
| B | Windows→VPS sandboxed exec | `python_execution` pinned `qwen-vps` | `task-bf14b798` | 11:00:30–11:00:34 | leased by qwen-vps after 1.2 s; scoped_runner (DynamicUser uid 64607); sum_squares=333833500; artifact sha256 matched expected; `/etc` & `/root` writes BLOCKED; network BLOCKED (TimeoutError); workspace unit finished | **PASS** |
| C | Windows→laptop exec | pinned `qwen-laptop` | `task-0aa86a8a` (+ `task-42719add` post-hardening) | 11:00:30–11:00:37 | leased by laptop (4.6 s); uid 63053; host_id `e951…` ≠ VPS `6066…`; same result; artifact hash matched; probes blocked; no VPS lease on this task | **PASS** |
| D | Qwen inference via controller->laptop | pinned `inference` task, model `qwen2.5:1.5b`, after worker deploy | `task-3237ad9d` | 20:35:34-20:35:57 | Leased by qwen-laptop in 1.1 s; worker called laptop-local Ollama; `model_returned=qwen2.5:1.5b`, response `READY 42`, 5 tokens, eval 0.322 s (15.55 tok/s), load 20.5 s (cold), total 22.2 s | **PASS** |
| E | Llama inference | pinned, `llama3.2:1b` | `task-97649fef` | 20:35:34-20:36:25 | Leased only after D finished (28.4 s; concurrency 1, no overlapping model loads); `model_returned=llama3.2:1b`, `READY 42`, 4 tokens, 14.27 tok/s, load 20.8 s, total 22.1 s | **PASS** |
| F | Scheduler/capability | unpinned tasks + pinned tasks | F4 `task-477901a3`, F3 `task-ffc96344`, B, C, G | 11:00–11:06 | ✓ VPS task→VPS only, laptop tasks→laptop only; ✓ unpinned `qwen2.5:7b` task never leased by either worker (cancelled after ≥20 s); ✓ worker stopped → no lease (Test G); **✗ F4: `min_ram_mb:1500` unpinned task leased by VPS (961 MB RAM)** — real scheduler defect, fixed in dev + 2 regression tests, not deployed | **FAIL** (defect found; fix pending deployment) |
| G | Interruption/recovery | `systemctl stop/start qwen-worker` on laptop, no lease, Ollama idle | `task-22be55c0` | 11:03:52–11:05:50 | During 112 s pause: laptop-pinned task stayed `queued`, 0 leases (no assignment, no duplicate); heartbeat age grew 24→125 s but controller still showed **`online`** (staleness detection absent in deployed build ✗); after start, leased in 3.8 s, completed, exactly 1 lease; 0 error lines in journal | **PARTIAL** — recovery/no-loss/no-dup PASS; unavailability detection FAIL on deployed build |
| H | Observability/reconciliation | DB cross-check | all | 11:06 | DB state consistent: 7 tasks (6 completed, 1 cancelled by me), leases all completed, no orphans; legacy node preserved offline. **Not performed:** browser cross-check of `/`, `/machines`, `/infrastructure`, `/inference` after the DB corrections (UI needs login; not run in this pass) and worker `tailscale_ip` fields are blank | **PARTIAL** |

## Cleanup performed
Task `task-ffc96344` cancelled (never leased). Sandbox workspaces removed by launcher (verified in earlier sandbox suite on both nodes). Temporary swappiness/governor restored and verified. No production task records deleted.

## Score
Live (updated after worker deploy): **PASS 5** (A, B, C, D, E), **PARTIAL 2** (G, H), **FAIL 1** (F, re-test pending controller restart). Not all-green — by design.

## Deployment plan required for D/E/F/G closure (needs your approval)
1. Controller: stop `QwenCollective` (admin rights), back up `data\` and prod tree, check out audit branch (clean the drifted files), restart; schema unchanged (v8).
2. Laptop (then VPS): rsync `vps_agent/` from branch, `systemctl restart qwen-worker` (no lease held), verify heartbeat.
3. Re-run `accept.py inf`, F4, and `run_recovery_g.py` (expect `offline` within 60 s).
4. Rollback: restore backed-up tree/DB, restart services.


## Post-deployment status (2026-10-15 13:34-13:40 PDT, approved by user)
| Target | Deployed | Rollback |
|---|---|---|
| Laptop worker | branch `9d279fc` `vps_agent/`,`scripts/`,`service/`; service restarted, re-registered | `/var/backups/qc-deploy-20261015T203408Z/source.before.tar.gz` |
| VPS worker | same; restarted, re-registered; runner SHA unchanged `bc7c8fca...` | `/var/backups/qc-deploy-20261015T203429Z/source.before.tar.gz` |
| Controller tree | prod tree now clean at `9d279fc` (was detached `2e569e1` + 10 dirty files); import check OK | backup `C:\Users\Matt C\.qwen-collective\backups\prod-tree-pre-deploy-20261015T203656Z` (files + tracked.patch); DB `qwen_collective.pre-deploy-20261015T203656Z.db` |
| Controller **process** | **NOT restarted** - service runs as LocalSystem and my account is not elevated (`Restart-Service` access denied). Still running the old code in memory, so the heartbeat-staleness and RAM fail-closed fixes are **not yet live**. | n/a |

To finish: in an **elevated** PowerShell run `Restart-Service QwenCollective`. Then re-run `audit_evidence/scripts/run_recovery_g.py` (expect `offline` within ~60 s of stopping the laptop worker) and an unpinned `min_ram_mb:1500` task (expect laptop only).
