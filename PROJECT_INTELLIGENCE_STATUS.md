# Project Intelligence — Status

**Current milestone: 3 (Measurement and forecasting). Implemented and verified with 194 tests (100% passing).**
Branch: `feature/project-intelligence`.
* **Milestone 1:** Deployed to production Windows service (port 8000, DB `user_version = 1`, service wrapper active).
* **Milestone 2:** Deployed to production codebase (`scratch\qwen-collective`), merged into `fix/security-reliability-audit` (`22a66bc`), pending elevated service restart `Restart-Service QwenCollective` to trigger automatic Migration 2 upgrade.
* **Milestone 3:** Completed in development worktree (`scratch\qwen-collective-dev`), fully covered by unit and HTTP route test suites (194 tests passing). Ready for review.

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
| **Migration 3:** Schema version upgraded to 3 with automatic pre-migration backup and clean foreign key/integrity checks | `app/migrations.py` | `test_migrations.py` |

## Test Suite Summary

| Suite | Tests | Status |
| :--- | :--- | :--- |
| Security, Auth, SSRF, & Web Isolation | 63 | Passed |
| Tasks & UI States | 7 | Passed |
| Machines & VPS Diagnostics | 12 | Passed |
| Migrations (Baseline, M1, M2, M3, Idempotency, Upgrades) | 8 | Passed |
| Milestone 1 Project Intelligence & Knowledge | 49 | Passed |
| Milestone 2 Execution, Evidence & Dependencies | 8 | Passed |
| Milestone 2 HTTP Routes (Deliverables, Gates, Events, Revisions) | 6 | Passed |
| Milestone 3 Metrics, Forecasts, Observations & Accuracy | 9 | Passed |
| Milestone 3 HTTP Routes (Metrics, Forecasts, Calculations) | 6 | Passed |
| Markdown Safety & Knowledge Core | 7 | Passed |
| **Total Test Suite** | **194 passed** | **100% SUCCESS (0 failed)** |

## Production deployment status
* **Milestone 1:** Successfully deployed and verified live in production (`scratch\qwen-collective`, port 8000).
* **Milestone 2:** Codebase merged and pushed to `origin/fix/security-reliability-audit`. To activate on Windows service:
  ```powershell
  Restart-Service QwenCollective
  ```
  On restart, `init_db()` will automatically snapshot `qwen_collective.pre-migration-v1-to-v2-<UTC>.db`, apply Migration 2, and bump DB version to 2.
* **Milestone 3:** Ready in development worktree (`scratch\qwen-collective-dev`). All 194 tests pass cleanly. Ready for deployment upon authorization.
