# Project Intelligence — Status

**Current milestone: 5 (Historical Learning, Retrospectives & Performance Evaluation). Implemented and verified with 217 tests (100% passing).**
Branch: `feature/project-intelligence`.
* **Milestone 1:** Deployed to production Windows service (port 8000, DB `user_version = 1`, service wrapper active).
* **Milestone 2:** Deployed to production Windows service (port 8000, DB `user_version = 2`, service wrapper active).
* **Milestone 3 & 4:** Deployed to production Windows service (port 8000, DB `user_version = 4`, service wrapper active).
* **Milestone 5:** Completed in development worktree (`scratch\qwen-collective-dev`), fully covered by unit and HTTP route test suites (217 tests passing). Ready for production deployment.

## What works now (verified)

| Capability | Where | Verified by |
| :--- | :--- | :--- |
| **Milestone 1 Foundation:** Projects, Paths, Stages, Knowledge Metadata, Relations, Decisions, Migration 1 | All views | Full M1 test suites |
| **Deliverables Tracking:** Formal deliverables tied to stages/paths, acceptance criteria, due dates, artifact references, verification results | Deliverables tab, `/projects/{pid}/deliverables/...` | `test_milestone2.py`, `test_milestone2_routes.py` |
| **Deliverables Review Workflow:** Planned → Submitted → Changes Requested / Accepted / Rejected with revision history and append-only decisions | Deliverables tab | `test_milestone2.py`, `test_milestone2_routes.py` |
| **Task Dependencies & Graph Cycle Prevention:** Tasks can depend on prerequisite tasks in the same project. Cycles and self-dependencies strictly rejected via BFS/DFS graph traversal | Tasks tab, `/projects/{pid}/tasks/{tid}/dependencies/...` | `test_milestone2.py`, `test_milestone2_routes.py` |
| **Prerequisite Completion Enforcement:** Tasks cannot be dispatched or approved if any declared prerequisite is not in `completed` status | Task dispatch engine | `test_milestone2.py` |
| **Deterministic Stage Completion Gates:** Stage gates require (1) all tasks completed, (2) all deliverables accepted, and (3) verifiable work evidence. Gate status (satisfied vs blockers) displayed on Paths tab | Paths tab | `test_milestone2.py`, `test_milestone2_routes.py` |
| **Automated Stage Advancement:** Advance button transitions stage to `completed`, logs `stage_advancement` decision, and auto-activates the next planned stage in position order | Paths tab, `/projects/{pid}/stages/{sid}/advance` | `test_milestone2.py`, `test_milestone2_routes.py` |
| **Task Operational Events & Knowledge Capture:** Record attempts, outcomes (completed/failed/aborted), actual results, evidence, and reusable lessons. Optionally generates durable Markdown note in project vault with `result_of` relation | Tasks tab, Task detail view | `test_milestone2.py`, `test_milestone2_routes.py` |
| **Path Revisions & Snapshotting:** Strategic revisions archive previous estimates, rationale, and assumptions into `path_revisions` history, logging a `path_revision` decision | Paths tab, `/projects/{pid}/paths/{path_id}/revise` | `test_milestone2.py`, `test_milestone2_routes.py` |
| **Knowledge Promotion to Collective Memory:** Verified or accepted project knowledge can be promoted to collective memory with recorded rationale and decision record | Knowledge tab, `/projects/{pid}/knowledge/{kn_id}/promote` | `test_milestone2.py`, `test_milestone2_routes.py` |
| **Metric Definitions:** Support for counts, percentages, rates, durations, currency, quality, and reliability measures with targets, dates, collection frequencies, and improvement directions | Metrics tab, `/projects/{pid}/metrics/create` | `test_milestone3.py`, `test_milestone3_routes.py` |
| **Immutable Actual Observations:** Timestamped measurements with source, method, verification status, and evidence reference; protected by database-level triggers against updates and deletes | Metrics tab, `/projects/{pid}/metrics/{mid}/observations/create` | `test_milestone3.py`, `test_milestone3_routes.py` |
| **Auditable Observation Corrections:** Corrections explicitly supersede flawed records with mandatory reasons without rewriting or deleting historical data | Metrics tab, `/projects/{pid}/metrics/{mid}/observations/correct` | `test_milestone3.py`, `test_milestone3_routes.py` |
| **Versioned Forecasts:** Historical predictions preserved indefinitely with version auto-increment and mandatory revision reasons; protected by database immutability triggers | Metrics tab, `/projects/{pid}/metrics/{mid}/forecasts/create` | `test_milestone3.py`, `test_milestone3_routes.py` |
| **Transparent Forecasting Algorithms:** Least-squares linear trend extrapolation, rolling averages, and interval rate extrapolation with explicit "insufficient data" states | Metrics engine, `/projects/{pid}/metrics/{mid}/forecasts/generate` | `test_milestone3.py`, `test_milestone3_routes.py` |
| **Forecast Evaluation & Accuracy Tracking:** Compares forecasts against actual observations; calculates directional variance, absolute error, percentage error, and normalized accuracy score | Metrics tab, `/projects/{pid}/metrics/{mid}/forecasts/{fid}/evaluate` | `test_milestone3.py`, `test_milestone3_routes.py` |
| **Status Assessments:** Identifies whether metrics/paths are on target, ahead of forecast, behind forecast, at risk, or awaiting data based on improvement direction | Metrics tab overview cards & badges | `test_milestone3.py`, `test_milestone3_routes.py` |
| **Project Context Packet Builder:** Token-budgeted prompt assembler following 8-tier priority hierarchy with explicit pruning omission notices and trust boundary tagging | Inspector view, `/projects/{pid}/context-packet` | `test_milestone4.py`, `test_milestone4_routes.py` |
| **AI Path Alternative Proposals:** Generates strategic path options complete with planned stages, deliverables, and completion criteria with deterministic fallback | Proposals tab, `/projects/{pid}/proposals/generate-path` | `test_milestone4.py`, `test_milestone4_routes.py` |
| **AI Task Decomposition:** Deconstructs approved stages into bounded, traceable tasks with diagnostic prerequisites and verification gates | Proposals tab, `/projects/{pid}/proposals/generate-tasks` | `test_milestone4.py`, `test_milestone4_routes.py` |
| **Stage Boundary Path Reviews:** Evaluates deliverables and metric health to recommend advance, continue, or reforecast | Proposals tab, `/projects/{pid}/proposals/generate-review` | `test_milestone4.py`, `test_milestone4_routes.py` |
| **Human-in-the-Loop Authorization Workflow:** The model proposes, human authorizes. Authorization creates durable DB records and logs `proposal_acceptance` decisions; rejection records reasons with zero state alteration | Proposals tab, `/projects/{pid}/proposals/{id}/review` | `test_milestone4.py`, `test_milestone4_routes.py` |
| **Model Calibration Analytics:** Quantifies model reliability via proposal acceptance rate, forecast error (MPE, accuracy), method breakdowns, and calibration assessment categories | Learning tab, `app/services/learning.py` | `test_milestone5.py`, `test_milestone5_routes.py` |
| **Deliverable Outcome Evaluations:** Post-delivery empirical audit tracking; connects real-world impacts to deliverables with immutable decision logging | Learning tab, `/projects/{pid}/deliverables/evaluate` | `test_milestone5.py`, `test_milestone5_routes.py` |
| **Automated Retrospective Synthesis:** AI-assisted synthesis of project achievements, blockers, lessons learned, recommendations, and calibration summaries into draft state | Learning tab, `/projects/{pid}/retrospectives/generate` | `test_milestone5.py`, `test_milestone5_routes.py` |
| **Retrospective Vault Publication & Collective Memory:** Operator review publishes retrospectives to project knowledge vaults and optionally promotes key lessons to collective memory | Learning tab, `/projects/{pid}/retrospectives/{id}/publish` | `test_milestone5.py`, `test_milestone5_routes.py` |
| **Cross-Project Transfer Learning:** Feeds empirical lessons and published retrospectives from other projects into new project context packets under Priority 8 | Learning tab, Context Packet Builder | `test_milestone5.py`, `test_milestone5_routes.py` |
| **Migration 5:** Schema version upgraded to 5 with `project_retrospectives`, `deliverable_evaluations`, and expanded decision types | `app/migrations.py` | `test_migrations.py` |

## Test Suite Summary

| Suite | Tests | Status |
| :--- | :--- | :--- |
| Security, Auth, SSRF, & Web Isolation | 63 | Passed |
| Tasks & UI States | 7 | Passed |
| Machines & VPS Diagnostics | 12 | Passed |
| Migrations (Baseline, M1, M2, M3, M4, M5, Idempotency, Upgrades) | 8 | Passed |
| Milestone 1 Project Intelligence & Knowledge | 49 | Passed |
| Milestone 2 Execution, Evidence & Dependencies | 8 | Passed |
| Milestone 2 HTTP Routes (Deliverables, Gates, Events, Revisions) | 6 | Passed |
| Milestone 3 Metrics, Forecasts, Observations & Accuracy | 9 | Passed |
| Milestone 3 HTTP Routes (Metrics, Forecasts, Calculations) | 6 | Passed |
| Milestone 4 Project-Aware Intelligence & Context Packets | 6 | Passed |
| Milestone 4 HTTP Routes (Proposals, Reviews, Context Inspector) | 7 | Passed |
| Milestone 5 Learning, Retrospectives & Calibration | 5 | Passed |
| Milestone 5 HTTP Routes (Retrospectives, Evaluations, Tabs) | 5 | Passed |
| Markdown Safety & Knowledge Core | 7 | Passed |
| **Total Test Suite** | **217 passed** | **100% SUCCESS (0 failed)** |

## Production deployment status
* **Milestone 1:** Successfully deployed and verified live in production (`scratch\qwen-collective`, port 8000).
* **Milestone 2:** Live in production (`Restart-Service QwenCollective` completed, DB `user_version = 2`).
* **Milestone 3 & 4:** Live in production (`Restart-Service QwenCollective` completed, DB `user_version = 4`).
* **Milestone 5:** Completed in development worktree (`scratch\qwen-collective-dev`). All 217 tests pass cleanly. Ready for deployment upon authorization.
