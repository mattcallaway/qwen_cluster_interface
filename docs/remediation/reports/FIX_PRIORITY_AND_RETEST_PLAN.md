# Fix Priority & Retest Plan: Real-World Project Audit

**Date:** 2026-10-22 (UTC) · **Status: PROPOSED. Nothing in this plan has been applied.** No application code, configuration, service state, or node state was changed during the audit.

Related reports: [REAL_WORLD_PROJECT_EXECUTION_AUDIT.md](REAL_WORLD_PROJECT_EXECUTION_AUDIT.md) · [ARTIFACT_PREVIEW_ROOT_CAUSE.md](ARTIFACT_PREVIEW_ROOT_CAUSE.md) · [MODEL_AND_NODE_ROUTING_AUDIT.md](MODEL_AND_NODE_ROUTING_AUDIT.md) · [AUTONOMOUS_RESEARCH_CAPABILITY_AUDIT.md](AUTONOMOUS_RESEARCH_CAPABILITY_AUDIT.md) · [PROJECT_DELIVERABLE_ACCEPTANCE.md](PROJECT_DELIVERABLE_ACCEPTANCE.md)

## Implementation order (summary)

| Order | ID | Severity | Title | Needs operator authorization |
|---|---|---|---|---|
| 1 | F-04 | HIGH (security) | Unauthenticated artifact read APIs | Code change |
| 2 | F-01 | CRITICAL | Live controller runs stale pre-RC2 code → Preview/library/reuse 500 | **Service restart / deploy** |
| 3 | F-06 | HIGH | Planning race + orphaned/timeout inference handling | Code change |
| 4 | F-02 | CRITICAL | Topic-blind research query generation | Code change |
| 5 | F-05 | HIGH | Self-confirming verification; false COMPLETED / auto-accept | Code change |
| 6 | F-09 | MEDIUM | `[Uncited]` id mismatch; citation audit integrity | Code change |
| 7 | F-07 | HIGH | No real node inference-eligible; test fixtures in production registry | **Registry/config change** |
| 8 | F-03 | HIGH | Research template collapses N deliverables to 1, no LLM | Design change |
| 9 | F-08 | MEDIUM | Unreviewed knowledge treated as sufficient prior knowledge | Code change |
| 10 | F-13 | MEDIUM | 0.5B model sole planner candidate | Config/policy |
| 11 | F-14 | MEDIUM | Plan validator accepts placeholder plan with unschedulable task (`valid=True`) | Code change |
| 12 | F-12 | LOW | `deliverable_detail` lacks error handling | Code change |
| 13 | F-10 | MEDIUM | Headless browser missing for LocalSystem | Environment |
| 14 | F-11 | LOW | Duplicate desktop worker processes | Environment |

F-04 is listed before F-01 for a reason. Restarting the service makes the read APIs fully functional again and keeps them exposed to the tailnet. Fix authentication first, or deploy both together.

---

## F-04: Unauthenticated artifact read APIs (CONFIRMED DEFECT, HIGH/security)

- **Components:** `app/api` artifact routes (metadata, `/preview`, `/download`, list). Affects both the live process and the RC2/working-tree code.
- **Repro:** With no cookie or token, `GET /api/v1/artifacts/{id}`, `/api/v1/artifacts/{id}/preview`, `/api/v1/artifacts/{id}/download`, and `/api/v1/artifacts` all return **200 with content**. Verified on the live controller and on the isolated RC2 instance ([isolated_preview_results.json](../evidence/real_world_project_audit/isolated_preview_results.json)).
- **Smallest fix:** Add the existing `Depends(get_current_user)` (or a router-level dependency) to the artifact router. Keep the existing project-scoped permission check.
- **Regression tests:** Unauthenticated requests to each of the four endpoints return 401/303. Authenticated requests still return 200. Cross-project access still returns 403.
- **Risk:** Low. Workers that fetch artifacts must already use a token. Confirm the worker download path uses an authenticated client before merging.

## F-01: Stale live controller (CONFIRMED DEFECT, deployment/process; CRITICAL)

- **Symptom:** Every `/deliverables/{id}` page and `/deliverables` return 500. Reuse form and reuse API return 500.
- **Root cause:** The WinSW service `QwenCollective` (PID 19300, started 2026-10-18 20:25) loaded HEAD-era modules. These call `TemplateResponse("name", {...})` (legacy signature → `TypeError: unhashable type: 'dict'` under Starlette 1.8.0) and `user.get(...)` on a `str`. The working tree and RC2 contain the fixes, but the service was never restarted. See [ARTIFACT_PREVIEW_ROOT_CAUSE.md](ARTIFACT_PREVIEW_ROOT_CAUSE.md).
- **Repro:** Log in, open any deliverable, and click Preview → 500. The traceback in `service/logs/qwen-collective-service.err.log` points to `deliverables.py:142`.
- **Smallest fix:** With operator approval, restart the service, or better, deploy the RC2 package and restart. Commit the working tree so HEAD matches what runs. Add `version` + `git_revision` + `process_started_at` to a `/health` endpoint (currently 404).
- **Regression tests:**
  - Playwright: log in → deliverable detail → Preview shows visible content for md, txt, csv, json, and pdf.
  - Unit: every `TemplateResponse` call uses the `request=` keyword form (grep test).
  - `/health` reports the same revision as `git rev-parse HEAD`.
- **Risk:** The restart drops in-flight planning state (this is how F-06 manifests). Restart when no run is in PLANNING or EXECUTING.

## F-06: Planning race & inference lifecycle (CONFIRMED DEFECT, HIGH)

- **Symptom:** The first "Generate plan" attempt fails with "Planning state has no in-flight request (controller restarted?)" and the run returns to DRAFT. Reproduced 3/3 times, including in the user's original project.
- **Root cause:** `planner.generate_plan` sets PLANNING (L1013), then runs the synchronous intake research checkpoint (1.5–7 s), and only then calls `save_checkpoint`. The engine tick `advance_planning` (L1105–1108) sees PLANNING with no checkpoint and fails the run.
- **Secondary (observed in routing test `run-3ed0a2b98ea94cba`):**
  - The orphaned planning request `inf-89ec965ff229480f` occupied pi-brain for 269 s. Its result was never consumed, and its row is still `submitted`.
  - The replan request `inf-1a8683398fed493f` was queued behind the orphan and timed out at 290 s (`model_timeout`) without ever running.
  - The timeout was then routed into `plan_repair` (a third 0.5B request) instead of a plain retry or a user-visible failure.
  - The "cancelled" task `task-abe9ca3e` later flipped to `completed` (01:23:42Z). Cancellation never reached the worker, and the terminal state was overwritten.
- **Smallest fix:**
  1. Persist the planning checkpoint (or an "in-progress" marker) before running the research checkpoint, or run the research checkpoint before entering PLANNING.
  2. When a run leaves PLANNING, cancel the outstanding inference task.
  3. Start the timeout clock at dispatch, not at enqueue.
  4. Do not treat a timeout as a malformed-plan repair.
- **Regression tests:** A slow research checkpoint (mock 10 s) followed by an engine tick still reaches AWAITING_PLAN_APPROVAL. Failing a run cancels its queued inference tasks. A queued request is not timed out while the node is busy with another request. A task in `cancelled` cannot transition to `completed`.
- **Risk:** Medium. Touches the orchestrator state machine, so the full orchestration suite must be re-run.

## F-02: Topic-blind research queries (CONFIRMED DEFECT, CRITICAL for research)

- **Root cause:** `app/services/research/planner.py::_generate_queries` keeps only the first 7 non-stopword tokens of the objective ("Conduct a comprehensive evidence-based investigation…"). Three of four subquestions are fixed generic strings. No query contained "Potter Valley", "Sonoma", or "Eel River".
- **Repro:** Call the query generator on the project objective. The output contains none of the named entities ([research_tool_invocations.json](../evidence/real_world_project_audit/research_tool_invocations.json)).
- **Smallest fix:** Extract named entities and noun phrases (capitalised spans, quoted terms, place names) and build per-deliverable and per-subquestion queries from them. Optionally ask an eligible LLM to propose queries, with a deterministic fallback.
- **Regression test:** For the Sonoma objective, at least 80% of queries contain "Potter Valley", "Sonoma", or "Eel". Golden tests for 3 other objectives.
- **Risk:** Low. Isolated function.

## F-05: Verification and evaluator integrity (CONFIRMED DEFECT, HIGH)

- **Root cause:** `engine.py` research_verify extracts claims from source X and verifies them against source X. There is no relevance scoring against the objective, so CAPTCHA and boilerplate pages are accepted. The evaluator auto-accepts the deliverable without checking the requested deliverable count, topical relevance, or citation coverage.
- **Smallest fix:**
  - Verify claims against independent sources (a different domain from the extraction source).
  - Add an objective-relevance threshold and discard CAPTCHA, login, and boilerplate pages.
  - In the evaluator, require (a) every requested deliverable present, (b) a minimum share of cited claims, and (c) at least N relevant sources. Otherwise end in `COMPLETED_WITH_GAPS` / `NEEDS_REVIEW`, not COMPLETED with auto-accept.
- **Regression tests:** A CAPTCHA fixture page yields 0 accepted claims. A report with 0 cited claims is not auto-accepted. A missing deliverable is reported as incomplete.
- **Risk:** Medium. Stricter gates will turn some previously "passing" runs into NEEDS_REVIEW. That outcome is correct.

## F-09: Citation linkage (CONFIRMED / PROBABLE, MEDIUM)

- **Root cause:**
  - `synthesis.py:93` looks up sources by a key that differs from the evidence id key, so every claim renders `[Uncited]` (CONFIRMED).
  - The live process's citation audit counted reference-list entries and returned `valid:true 8/8`. The current on-disk `extract_citation_indices` returns `[]` for the same report. The live validator is older (PROBABLE).
- **Fix:** Unify the id key. The audit must count inline citation markers that resolve to retrieved sources.
- **Regression test:** A synthesised report with 3 evidence items has 3 resolved inline citations, and the audit fails when inline citations are removed.

## F-07: Inference fleet eligibility & registry hygiene (CONFIGURATION ISSUE + PROBABLE DEFECT, HIGH)

- **Observed:**
  - Real workers `desktop-worker-01`, `qwen-laptop`, and `qwen-vps` have `infrastructure_nodes.is_enabled=0`, while the `machines` table shows them enabled.
  - No real node has a passed `model_inference_probe`.
  - The only node with a passed probe is the test fixture `t-laptop` ("passed by test_harness").
  - About 15 test workers (worker-A/B, t-*, phase3-demo-*) remain in the production registry.
  - Why the real nodes were disabled is UNVERIFIED: no audit-log entry exists.
- **Fix (operator decisions):**
  - Remove or quarantine test fixtures.
  - Re-enable the intended real nodes through the UI.
  - Run the built-in inference probe per node/model.
- **Code fixes:**
  - Test harnesses must use an isolated DB (production writes from tests are a defect).
  - Reconcile `machines.enabled` with `infrastructure_nodes.is_enabled`.
  - Write an audit entry for every enable/disable.
- **Regression tests:** The test suite run leaves the production DB untouched (checksum before/after). Node toggles produce audit records.
- **Risk:** Low for code changes. Re-enabling nodes is an operational decision.

## F-03: Research template collapses deliverables (CAPABILITY NOT IMPLEMENTED, HIGH)

- **Root cause:** `planner.py:1048-1052` routes any objective that matches the research regex to `template_research_plan`. That template produces exactly one deliverable and 4 `controller_step` tasks with `required_capabilities=["controller"]`. It does not use an LLM, even when `planner=model` is available.
- **Fix:**
  - Carry intake-extracted deliverables into the template: one synthesis task and one deliverable record per requested deliverable.
  - Allow synthesis steps to request `inference` capability when an eligible model exists. If none exists, report "no eligible model" truthfully.
- **Regression test:** A 5-deliverable objective produces a plan with 5 deliverable records, and the run cannot complete with fewer.

## F-08: Unreviewed knowledge reused as sufficient (CONFIRMED DEFECT, MEDIUM)

- **Observed:** Intake for an unrelated objective reported "Existing verified knowledge (6 fresh records) is sufficient". Those records were status `captured` (unreviewed), were auto-labelled `supported`, and came from an earlier junk run. Research was skipped.
- **Fix:** The sufficiency check counts only reviewed/promoted records whose relevance to the objective exceeds a threshold.
- **Regression test:** 6 captured but irrelevant records → research_required.

## F-13: Planner model selection (EXPECTED given eligibility; quality issue, MEDIUM)

- `_pick_planner` does not require a passed probe and picks the only enabled node with models: pi-brain `qwen2.5:0.5b`. It produced a placeholder plan that echoed the template ("Task 1 Name", `library_script sys_health_basic`).
- **Fix:** Set a minimum parameter size or capability tier for planning, require a probe, and prefer the fastest eligible node.
- **Regression test:** With only a 0.5B model available, the planner falls back to deterministic templates and says so.

## F-14: Placeholder plans pass validation (CONFIRMED DEFECT, MEDIUM)

- **Observed:** pi-brain returned a verbatim copy of the prompt's schema example (rev `rev-1ee096eaf49841e6`): `"Task 1 Name"`, objective `"High-level goal description"`, and a `library_script sys_health_basic` pinned to the disabled `qwen-vps`. Validation recorded "t1 has no compatible node" but returned `valid=True`, and the plan was proposed for approval. The operator did not approve it.
- **Fix:** Treat capability issues (unschedulable tasks) as errors. Reject plans whose titles or objective match the prompt's example literals. Require the plan objective to overlap the user objective.
- **Regression test:** Feeding the schema example back as a model response yields `valid=False` and a repair or deterministic fallback.

## F-12: `deliverable_detail` error handling (CONFIRMED, LOW)

- Missing artifact or permission errors propagate as 500. **Fix:** Map them to 404/403 with a user-facing message. **Test:** Requests for a deleted artifact or a foreign-project deliverable return 404/403.

## F-10: Headless browser for LocalSystem (CONFIGURATION ISSUE, MEDIUM)

- Playwright Chromium is not installed for the LocalSystem profile, so `browser_fetch` fails and research falls back to plain HTTP. **Fix:** Set `PLAYWRIGHT_BROWSERS_PATH` to a shared location and install there. **Test:** The service-context browser smoke check passes.

## F-11: Duplicate desktop workers (CONFIGURATION ISSUE, LOW)

- PIDs 13400 and 3708 both run `desktop-worker.env` as `desktop-worker-01`. **Fix:** Stop one (operator) and add a single-instance lock file to the worker. **Test:** A second launch exits with a clear message.

---

## Retest plan (after fixes are approved and applied)

1. Confirm `/health` revision = deployed commit. Confirm unauthenticated artifact APIs → 401.
2. Playwright: Preview on md/txt/csv/json/pdf shows visible content. HTML/SVG are shown escaped.
3. Create a project → Generate plan once → reaches AWAITING_PLAN_APPROVAL on the first attempt (no race).
4. Re-run the Sonoma objective. Pass requires: 5 deliverable records, topic-bearing queries, at least 5 relevant sources, inline citations resolving to retrieved URLs, evaluator gaps reported honestly.
5. Run the per-node inference probe. A scheduler-mediated inference task runs on a probed real node with model and worker attribution recorded.
6. Reuse a deliverable into a second project. Lineage row has source project, artifact id, and sha256. Download from the target project succeeds.
7. Knowledge: candidates remain `proposed` until reviewed. The intake sufficiency check ignores them.
