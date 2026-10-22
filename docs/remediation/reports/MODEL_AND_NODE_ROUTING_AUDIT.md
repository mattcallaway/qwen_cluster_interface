# Model & Node Routing Audit

**Date:** 2026-10-22 (UTC) · **Mode:** read-only SQL, direct Ollama probes (labelled), and one scheduler-mediated UI test project. No nodes were enabled, disabled, restarted, or reconfigured.

Evidence: [worker_fleet_snapshot.json](../evidence/real_world_project_audit/worker_fleet_snapshot.json) · [model_routing_decisions.json](../evidence/real_world_project_audit/model_routing_decisions.json) · [direct_model_reachability.json](../evidence/real_world_project_audit/direct_model_reachability.json) · [routing_test_log.json](../evidence/real_world_project_audit/routing_test_log.json) · [routing_probe_model_plan.txt](../evidence/real_world_project_audit/routing_probe_model_plan.txt) · [task_execution_ledger.json](../evidence/real_world_project_audit/task_execution_ledger.json)

## 1. Bottom line

- **The user's observation understates the problem.** In the Sonoma project, **zero models ran inference**. The only "node" was `controller-local`, which executed four deterministic `controller_step` tasks (search, retrieve, verify, synthesize) with `dispatched_model = null`.
- Real research requests never use the fleet. Any objective matching the research regex goes to `template_research_plan` (`app/services/orchestrator/planner.py:1048-1052`). That template requires only the `controller` capability and uses no LLM.
- Even if a research plan requested inference, **no real worker is currently inference-eligible**:
  - `desktop-worker-01`, `qwen-laptop`, and `qwen-vps` are `is_enabled = 0`.
  - No real node has a passed `model_inference_probe`.
- The only enabled node with a model is **pi-brain (`qwen2.5:0.5b`)**. It is therefore the sole planner candidate. When used, it returned a verbatim copy of the prompt's schema example rather than a plan.
- Classification:
  - Template routing: **CAPABILITY NOT IMPLEMENTED / design**.
  - Disabled nodes and missing probes: **CONFIGURATION ISSUE** (reason for disablement UNVERIFIED).
  - Orphaned/timeout handling and placeholder-plan acceptance: **CONFIRMED DEFECTS**.

## 2. Node-by-node accounting

Heartbeats were observed live at about 01:25Z on 2026-10-22.

| Node | Role / OS | Address (Tailscale) | Registered | Heartbeat | Reachable (direct) | `is_enabled` | Inference probe | Inference-eligible | Selected in audit | Actually executed |
|---|---|---|---|---|---|---|---|---|---|---|
| `controller-local` | controller / Windows 10 Home | desktop-44h6qvp, 100.122.228.19 | yes | stale (07:14Z) | n/a | 0 | none | No (not a worker) | **Yes**: all research `controller_step` tasks | **Yes** (no LLM) |
| `desktop-worker-01` | compute_worker / Windows | same host | yes | **live** | local Ollama OK (`qwen2.5:1.5b`, `qwen2.5:3b`) | **0** | none | **No** | No | No |
| `qwen-laptop` | inference_node / Linux | 100.101.187.115 | yes | **live** | OK (`qwen2.5:1.5b`, `llama3.2:1b`) | **0** | none | **No** | No | No |
| `qwen-vps` | vps_worker / Linux | 100.71.194.68 | yes | **live** | no Ollama installed | **0** | n/a | No (no models, 573 MB RAM) | No | No |
| `pi-brain` | inference_node / Linux | 100.111.178.12 | yes | **live** | OK (`qwen2.5:0.5b`) | 1 | none | Not for task inference (no probe); **yes for planning** (`_pick_planner` skips the probe) | **Yes**: planner in routing test | **Yes**: 2 completed generations |
| `t-laptop` | inference_node (test fixture) | none | yes | stale (07:38Z) | not a real host | 1 | **"passed by test_harness"** | Would pass gates except health freshness | No | No |
| ~15 others (`worker-A/B`, `t-*`, `phase3-demo-*`, `worker-xxxx`, `worker-rc1-staging`, `node-d35568f5`) | test fixtures | none | yes | stale | no | mixed | mixed | No | No | No |

The `machines` table reports the real nodes as `enabled = 1`, which contradicts `infrastructure_nodes.is_enabled = 0`. The only node-toggle audit record concerns `node-d35568f5`, so the cause of the disablement is **UNVERIFIED**.

Two `desktop-worker-01` processes (PIDs 13400 and 3708) run with the same config (F-11).

## 3. Router gates (code)

`model_router.eligible_nodes` (task inference) requires all of the following:
1. Worker role.
2. `is_enabled = 1`.
3. An active worker token.
4. Health `online` with a fresh contact.
5. The requested model is present.
6. Available RAM ≥ 350 MB.
7. A **passed `model_inference_probe`** for that node/model.

`_pick_planner` applies gates 1–6 but **not** gate 7. That is why pi-brain is chosen for planning even though it would be refused for task inference.

## 4. Direct reachability (DIRECT Ollama calls, NOT scheduler-mediated)

| Endpoint | Model | Load | Gen rate | Result |
|---|---|---|---|---|
| desktop (local) | qwen2.5:1.5b | 12 s | 66.7 tok/s | OK |
| desktop (local) | qwen2.5:3b | 12 s | 81.3 tok/s | OK |
| qwen-laptop | qwen2.5:1.5b | 45 s | 17.8 tok/s | OK |
| qwen-laptop | llama3.2:1b | 40 s | 21.8 tok/s | OK |
| pi-brain | qwen2.5:0.5b | 7 s | 11.3 tok/s | OK |
| qwen-vps | n/a | n/a | n/a | No Ollama |

**Four models on three hosts are reachable and functional but unused.** The scheduler is not broken in the sense of "ignoring healthy eligible nodes". The nodes are excluded by registry state (`is_enabled = 0`, no probe).

## 5. Scheduler-mediated routing test

Test project `prj-bc2bca9d593744c88306d05734c02b5a` · run `run-3ed0a2b98ea94cba` · planner mode `model`, autonomy 2 · non-research objective ("short plain-language explanation of how drought affects reservoirs").

| Time (UTC) | Event | Evidence |
|---|---|---|
| 01:16:48 | Run created → PLANNING (mode=model); intake research checkpoint starts | events 553–556 |
| 01:16:50 | **planning_failed → DRAFT** ("no in-flight request"). Race (F-06) | event 561 |
| 01:16:56 | Planning inference `inf-89ec965ff229480f` dispatched anyway to **pi-brain qwen2.5:0.5b** (task-c258fba1). **Orphaned** | model_inference_requests |
| 01:17:54 | Operator clicks Replan (model) in the UI; intake now says "6 fresh verified records sufficient" (F-08) | events 565–570 |
| 01:17:55 | Second planning inference `inf-1a8683398fed493f` (task-abe9ca3e) queued for pi-brain behind the orphan | |
| 01:21:25 | Orphan finished on pi-brain: 1282 prompt / 509 gen tokens, 268.9 s. Result never consumed; row still `submitted` | task-c258fba1 |
| 01:22:45 | Second request marked **`model_timeout`** (290 s; the clock included queue time). Task set to `cancelled` | inf-1a86…, task-abe9ca3e |
| 01:22:45 | Timeout routed to **plan_repair** (attempt 1/2): third request `inf-13ca27d8efdb414e` to pi-brain | event 571 |
| 01:23:42 | **The "cancelled" task-abe9ca3e flipped to `completed`.** pi-brain ran it anyway; cancellation never reached the worker | tasks table |
| 01:25:59 | Repair completed (latency 131.7 s, 1282 / 509 tokens). Plan revision 1 `valid=True` → AWAITING_PLAN_APPROVAL | events, rev-1ee096eaf49841e6 |

**What the model produced:** a verbatim copy of the schema example in its prompt ([routing_probe_model_plan.txt](../evidence/real_world_project_audit/routing_probe_model_plan.txt)):
- objective `"High-level goal description"`;
- tasks `"Task 1 Name"` / `"Task 2 Name"`;
- a `library_script sys_health_basic` pinned to `qwen-vps`.

The plan validator recorded a capability issue ("t1 has no compatible node … qwen-vps: disabled") but still marked the revision **valid** and proposed it for approval.

**Operator decision:** not approved. The plan is unrelated to the objective and includes a `library_script` task. The run remains in AWAITING_PLAN_APPROVAL. Nothing executed beyond planning.

**Scheduler-mediated conclusions:**
- **Verified:** the scheduler delivers planning requests to a remote Linux node (pi-brain) and records model, node, and token telemetry.
- **Verified defects:**
  - Orphaned inference is never cancelled or consumed.
  - The timeout clock includes queue wait.
  - A timeout is treated as a plan-repair case.
  - A cancelled task's status is overwritten to `completed`.
  - A placeholder plan with an unschedulable task passes validation.
- **Not verifiable without config changes (BLOCKED):** scheduler-mediated *task* inference on `qwen-laptop` or `desktop-worker-01`. Both are disabled and unprobed, and enabling them is outside this audit's authorization.

## 6. Historical model usage (all projects)

| Node | Model | Purpose | Status | Count | Last |
|---|---|---|---|---|---|
| pi-brain | qwen2.5:0.5b | planning / plan_repair | 1 completed, 1 timeout, 1 orphaned (`submitted`) | 3 | 2026-10-22 (this audit) |
| qwen-laptop | qwen2.5:1.5b | planning / plan_repair | completed | 64 | 2026-10-19 02:53Z |
| qwen-laptop | qwen2.5:1.5b | plan_repair | timeout | 8 | 2026-10-18 |
| desktop-worker-01 | qwen2.5:1.5b | task | completed | 9 | 2026-10-18 |
| qwen-laptop | llama3.2:1b | task | completed | 5 | 2026-10-18 |
| both | qwen2.5:1.5b | evaluation | completed | 2 | 2026-10-16 |

Multi-node inference **did work historically** (through 2026-10-19). Since then no real worker has executed inference, consistent with the nodes becoming disabled after that date. `qwen2.5:3b` and the VPS have never executed inference.

## 7. Why only one model/node appeared

1. **Research objectives bypass the fleet by design.** The research template uses 4 controller-only steps and no LLM. This alone explains the user's observation.
2. **Real inference workers are disabled and unprobed.** Even the general model planner can only reach pi-brain's 0.5B model.
3. **Registry pollution.** Test fixtures (including `t-laptop` with a passed probe and an active token) make the fleet look larger than it is and could be selected if their heartbeats were fresh.
4. **The UI does not say so.** Runs complete without any warning that no model was used.

## 8. Recommendations

Details are in [FIX_PRIORITY_AND_RETEST_PLAN.md](FIX_PRIORITY_AND_RETEST_PLAN.md) (F-03, F-06, F-07, F-13).
- **Operator:**
  - Quarantine test fixtures.
  - Re-enable the intended real nodes.
  - Run per-model inference probes.
  - Stop the duplicate desktop worker.
- **Code:**
  - Research template requests `inference` for synthesis.
  - Planner requires a probe and a minimum model tier.
  - Cancel orphaned inference on run failure.
  - Start the timeout at dispatch.
  - A cancelled task cannot transition to `completed`.
  - The validator rejects plans with unschedulable tasks or echoed placeholder text.
  - Run summary shows "models used: none" explicitly.
