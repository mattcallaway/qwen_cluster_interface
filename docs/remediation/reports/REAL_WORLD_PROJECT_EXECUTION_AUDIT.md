# Real-World Project Execution Audit: Sonoma County Water Security Research Brief

**Date:** 2026-10-22 (UTC) · **Operator/observer:** Antigravity · **Authorization:** non-destructive disposable test projects on the live controller. No service restarts, config/node changes, code edits, or credential changes were made.

**Companion reports:**
- [MODEL_AND_NODE_ROUTING_AUDIT.md](MODEL_AND_NODE_ROUTING_AUDIT.md)
- [ARTIFACT_PREVIEW_ROOT_CAUSE.md](ARTIFACT_PREVIEW_ROOT_CAUSE.md)
- [AUTONOMOUS_RESEARCH_CAPABILITY_AUDIT.md](AUTONOMOUS_RESEARCH_CAPABILITY_AUDIT.md)
- [PROJECT_DELIVERABLE_ACCEPTANCE.md](PROJECT_DELIVERABLE_ACCEPTANCE.md)
- [FIX_PRIORITY_AND_RETEST_PLAN.md](FIX_PRIORITY_AND_RETEST_PLAN.md)

Evidence bundle: [`docs/remediation/evidence/real_world_project_audit/`](../evidence/real_world_project_audit/)

---

## 1. Executive summary

**Qwen Collective did not complete the research brief.** The run reported `COMPLETED` in about 37 s. It produced **one** generic, off-topic report with zero citations; the other four deliverables were never produced. Preview fails, reuse fails, and the run did not report any of these limitations.

Both user observations are confirmed:
- **One node:** literally true, and worse than it looked. **No model ran at all.** The research template executes four deterministic `controller_step` tasks on `controller-local`, and every real inference worker is currently disabled and unprobed.
- **Preview 500:** confirmed. The live service process (started 2026-10-18) runs pre-RC2 code. That code uses the legacy `TemplateResponse` signature, which raises `TypeError: unhashable type: 'dict'` under Starlette 1.8. The working-tree/RC2 code previews correctly in an isolated instance. The fix exists but was never loaded.

## 2. Environment discovery (Part 1)

| Item | Finding |
|---|---|
| Controller | WinSW service `QwenCollective` (LocalSystem), `uvicorn app.main:app` on 127.0.0.1:8000, PID 19300, **started 2026-10-18 20:25 local** |
| Exposure | `tailscale serve` → `https://desktop-44h6qvp.tailb5d90b.ts.net` (tailnet only) |
| Source | Dev working tree; Git HEAD `c98510b` plus ~40 uncommitted files. The working tree is byte-identical to the RC2 zip. **The running process predates both** |
| Version endpoint | `/health` → 404; no runtime revision reporting |
| DB / storage | `~\.qwen-collective\data\qwen_collective.db` (user_version 18); artifacts under `~\.qwen-collective\data\evidence\orch_run-*` (absolute paths, `cas_hash` NULL) |
| Logs | `service\logs\qwen-collective-service.err.log` / `.out.log` |
| Workers (live heartbeat) | desktop-worker-01 (**2 duplicate processes**), qwen-laptop, qwen-vps, pi-brain |
| Inference reachability (direct) | desktop qwen2.5:1.5b/3b, laptop qwen2.5:1.5b/llama3.2:1b, pi qwen2.5:0.5b all OK; VPS has no Ollama |
| Inference eligibility | **None of the real workers** (`is_enabled=0` on desktop/laptop/VPS; no passed probes). Only pi-brain is enabled |
| Web research | Live DuckDuckGo search + HTTP fetch work. Headless browser fails (no Playwright Chromium for LocalSystem) |
| Delegation | No agent-to-agent delegation observed; research steps are controller-only |
| Background processing | Engine tick runs (~3 s dispatch gap); planning race present |
| Registry hygiene | ~15 test fixtures in the production registry, including `t-laptop` with a passed probe and an active token |

Details: [worker_fleet_snapshot.json](../evidence/real_world_project_audit/worker_fleet_snapshot.json).

## 3. Reproduction of the user's failures (Part 2)

The user's project `prj-c3e69205a8e0466ab54b609801694796` / `run-08de085e97e84130` still exists (6 artifacts, all checksums match on disk).

Real Chromium (Playwright) reproduction:

| Action | Result |
|---|---|
| Open `/deliverables/{id}` (Preview button target) | **HTTP 500** "Internal Server Error" |
| Open `/deliverables` library | **HTTP 500** |
| `GET /api/v1/artifacts/{id}/preview` (JSON) | 200 with content |
| Download | 200, bytes match stored SHA-256 |

- Server traceback: `deliverables.py:142 deliverable_detail` / `:80 deliverables_library` → `TypeError: unhashable type: 'dict'`. The error count went from 4 to 14 during the audit.
- **Root cause:** the stale process loaded HEAD-era code with the legacy `TemplateResponse(name, ctx)` call. The working tree is fixed, but the service was never restarted. This one exception explains every Preview failure, for all content types.
- Isolated RC2 instance:
  - md, txt, csv, json, and pdf render visibly.
  - HTML and SVG are shown escaped (no script execution).
- **New security defect found during this test:** artifact metadata, preview, download, and list endpoints return 200 **without authentication**. This applies to both the live and RC2 code (F-04).

Full analysis: [ARTIFACT_PREVIEW_ROOT_CAUSE.md](ARTIFACT_PREVIEW_ROOT_CAUSE.md).

## 4. Fresh project execution through the live UI (Parts 3–4)

Disposable project `prj-c74dd685c2b145e5b32ab347253dbc77` · run `run-ee1166ebb5eb4558` · autonomy 2 · planner auto. The full Sonoma objective and all five deliverables were entered in the UI.

| UTC | Phase | Observation |
|---|---|---|
| 01:10:33 | Project created (UI) | OK |
| 01:10:39 | Planning | **planning_failed → DRAFT** ("no in-flight request (controller restarted?)"). Planning race, reproduced 3 of 3 times |
| 01:10:45 | Replan (UI) | → AWAITING_PLAN_APPROVAL. Plan: `template_research_plan`, 4 low-risk `controller_step` tasks, **1 deliverable** (the 5 requested were collapsed) |
| 01:12:08 | Approval (UI) | Operator approved; non-destructive, no privileged actions |
| 01:12:11 | Execution start | 6.58 s approval → start |
| 01:12:12–01:12:27 | search_sources | controller-local, no model; **7 topic-blind queries** (no "Potter Valley"/"Sonoma"/"Eel") |
| 01:12:30–01:12:39 | retrieve_content | HTTP fetch (browser unavailable); irrelevant pages incl. a CAPTCHA page |
| 01:12:42–01:12:43 | verify_claims | Claims verified against their own source pages (self-confirming) |
| 01:12:46–01:12:47 | synthesize_report | `research_report.md` (art-ea7a176ffa6f46c7, 6658 B); all claims `[Uncited]` |
| 01:12:48 | Evaluation | **COMPLETED; deliverable auto-accepted** |
| post-run | Preview (UI click) | **HTTP 500** |
| post-run | Reuse into `prj-58797337cbc94f3bac9fd67834fdbcaf` | UI form 500; API `POST /api/v1/artifacts/{id}/reuse` 500 (`AttributeError: 'str' object has no attribute 'get'`); no lineage |

Observed outcomes by lifecycle phase:
- Intake extraction did capture the 5 deliverables, but planning dropped 4 of them.
- Worker/model capability discovery was not consulted for any research step.
- Knowledge: 11 `captured` candidates per run. Promotions stayed `proposed` (boundary held). In the next project, however, these unreviewed records were treated as "sufficient verified knowledge" and research was skipped (F-08).

Ledger: [task_execution_ledger.json](../evidence/real_world_project_audit/task_execution_ledger.json), [project_run_log.json](../evidence/real_world_project_audit/project_run_log.json), screenshots in `screens_project_run/`.

## 5. Model & node participation (Part 5)

Summary of [MODEL_AND_NODE_ROUTING_AUDIT.md](MODEL_AND_NODE_ROUTING_AUDIT.md):

- **Research runs: 0 model calls; only `controller-local`.** This is by design of `template_research_plan` (`planner.py:1048-1052`).
- **Real workers:** heartbeating and directly reachable, but `is_enabled=0` and unprobed, so the router excludes them. Why they were disabled is UNVERIFIED; there is no audit record.
- **Scheduler-mediated test** (`prj-bc2bca9d593744c88306d05734c02b5a`, planner=model):
  - The scheduler dispatched planning to **pi-brain qwen2.5:0.5b**, the only enabled node with a model.
  - That node then received an orphaned request, a request that timed out while queued, and a plan-repair request.
  - The model returned the prompt's schema example verbatim ("Task 1 Name", `library_script` on the disabled VPS). The validator marked it `valid=True`.
  - The operator did not approve it.
  - A cancelled task's status was later overwritten to `completed`.
- Multi-node inference **did work historically** (laptop and desktop through 2026-10-19). Scheduler-mediated task inference on other nodes is **BLOCKED** today without config changes.

## 6. Research & evidence quality (Part 6)

From [AUTONOMOUS_RESEARCH_CAPABILITY_AUDIT.md](AUTONOMOUS_RESEARCH_CAPABILITY_AUDIT.md):
- Live search did occur, but none of the queries was on topic.
- No URLs were invented.
- Zero relevant sources were found.
- Verification is self-referential.
- Every claim is `[Uncited]`.
- `citation_audit.json` nevertheless reports `valid:true 8/8`.
- No uncertainty was reported.

Antigravity's **external validation** (clearly labelled in that report) confirms the topic has abundant current authoritative sources: the PG&E license surrender at FERC, Scott/Cape Horn dam removal, and the regional diversion-facility proposal. A working pipeline could have found them.

## 7. Deliverables (Part 7)

**0 of 5 accepted.** See [PROJECT_DELIVERABLE_ACCEPTANCE.md](PROJECT_DELIVERABLE_ACCEPTANCE.md).
- The only report produced is off-topic and uncited.
- Download works and hashes match.
- Preview fails on the live controller.
- Artifacts have checksums but no CAS hash.
- Reuse failed.

## 8. Error handling & truthfulness (Part 8)

| Failure | Detected? | Classified correctly? | User told? | Status truthful? |
|---|---|---|---|---|
| Preview 500 | Logged only | No (generic 500) | "Internal Server Error" | n/a |
| Planning race | Yes | **No**: message blames a controller restart | Run drops to DRAFT | Misleading |
| Planning inference queued behind orphan | Timeout recorded | **No**: queue wait counted; routed to plan_repair | Not surfaced | Partially |
| Cancelled task executed anyway | No | No | No | **False** (`cancelled` → `completed`) |
| Off-topic sources / CAPTCHA | No | No | No | **False** (claims "supported") |
| Uncited report | No | No | No | **False** (`citation_audit valid:true`) |
| 4 missing deliverables | No | No | No | **False** (COMPLETED + auto-accept) |
| Headless browser missing | Logged | Silent fallback to HTTP | No | Partially |
| Unschedulable task in model plan | Recorded as capability issue | Still `valid=True` | Shown in plan review | Partially |
| Reuse failure | 500 | No | Generic error | n/a |
| Approval boundary | n/a | n/a | n/a | **Held** (no execution before approval; promotions stayed `proposed`) |

`SECURE_EXECUTION_UNAVAILABLE` and the approval policies were left untouched.

## 9. Performance (Part 9)

All values are measured unless noted. See [measured_telemetry.json](../evidence/real_world_project_audit/measured_telemetry.json).

| Metric | Value |
|---|---|
| Approval → execution start | 6.58 s |
| Dispatch gap between tasks | ~3.2 s (derived from timestamps) |
| search_sources / retrieve / verify / synthesize | 15.5 s / 8.7 s / 0.86 s / 0.25 s |
| Total execution (audit / user run) | 36.6 s / 35.0 s |
| Model calls in research runs | **0** |
| Web searches per run | 7 |
| Relevant sources retrieved | **0** |
| Planning race occurrences | 3 of 3 first attempts |
| pi-brain planning inference | 268.9 s (orphan), 131.7 s (repair), 1282 / 509 tokens |
| Replan → plan proposed (routing probe) | 484 s |
| Worker utilisation | controller-local 8 tasks; pi-brain 3 planning calls; desktop / laptop / VPS **0** |
| Token usage for research runs | none (no model) |
| Lease acquisition time | not instrumented (missing telemetry) |

## 10. Findings register (Part 10)

| ID | Classification | Severity | Summary |
|---|---|---|---|
| F-01 | CONFIRMED DEFECT (deployment) | CRITICAL | Stale live process (pre-RC2) → Preview/library/reuse 500; no revision reporting |
| F-02 | CONFIRMED DEFECT | CRITICAL | Topic-blind research queries |
| F-03 | CAPABILITY NOT IMPLEMENTED | HIGH | Research template: 1 deliverable, controller-only, no LLM |
| F-04 | CONFIRMED DEFECT (security) | HIGH | Unauthenticated artifact read APIs (live and RC2) |
| F-05 | CONFIRMED DEFECT | HIGH | Self-confirming verification; false COMPLETED / auto-accept |
| F-06 | CONFIRMED DEFECT | HIGH | Planning race; orphaned inference; queue-inclusive timeout; cancelled → completed |
| F-07 | CONFIGURATION + PROBABLE DEFECT | HIGH | No real node inference-eligible; test fixtures in production registry |
| F-08 | CONFIRMED DEFECT | MEDIUM | Unreviewed knowledge treated as sufficient |
| F-09 | CONFIRMED / PROBABLE | MEDIUM | `[Uncited]` key mismatch; live citation audit counts references |
| F-10 | CONFIGURATION ISSUE | MEDIUM | No headless browser for LocalSystem |
| F-11 | CONFIGURATION ISSUE | LOW | Duplicate desktop worker |
| F-12 | CONFIRMED DEFECT | LOW | No error mapping in `deliverable_detail` |
| F-13 | EXPECTED (poor) | MEDIUM | 0.5B model sole planner |
| F-14 | CONFIRMED DEFECT | MEDIUM | Placeholder / unschedulable model plan passes validation |

Reproduction steps, fixes, tests, and implementation order are in [FIX_PRIORITY_AND_RETEST_PLAN.md](FIX_PRIORITY_AND_RETEST_PLAN.md).

## 11. Previously certified features that failed in real use

| Certified | Real-world result | Why certification missed it |
|---|---|---|
| Browser acceptance / deliverable preview | 500 on live | Certified against fresh RC2 processes; production was never redeployed or restarted |
| Artifact reuse with lineage | 500, no lineage | Same stale-process cause |
| Citation audit integrity | `valid:true` for an uncited report | Fixtures had well-formed citations; no relevance or inline check |
| Research verification | Self-confirming; CAPTCHA accepted | Static fixtures; no off-topic or adversarial pages |
| Distributed inference | 0 models in research; real nodes disabled | Certified with test workers (`t-laptop`, harness-passed probe) in the same DB |
| Artifact authorization | Read APIs unauthenticated | Browser tests always logged in; no negative-auth API test |

## 12. Disposable records created

| Project | Purpose | Final state |
|---|---|---|
| `prj-c74dd685c2b145e5b32ab347253dbc77` | Sonoma re-run | COMPLETED |
| `prj-58797337cbc94f3bac9fd67834fdbcaf` | Reuse target | Empty |
| `prj-bc2bca9d593744c88306d05734c02b5a` | Routing probe | Left at AWAITING_PLAN_APPROVAL, intentionally not approved |

All three can be archived by the operator. The user's original project was not modified.
