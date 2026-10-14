# Project Intelligence — Status

**Current milestone: 2 (Execution and evidence). Implemented and verified with 179 tests (100% passing).**
Branch: `feature/project-intelligence`.
* **Milestone 1:** Deployed to production Windows service (port 8000, DB `user_version = 1`, service wrapper active).
* **Milestone 2:** Completed in development worktree (`scratch\qwen-collective-dev`), fully covered by unit and HTTP route test suites. Awaiting explicit user deployment authorization.

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
| **Migration 2:** Schema version upgraded to 2 with automatic pre-migration backup, non-destructive ALTERs, and clean foreign key/integrity checks | `app/migrations.py` | `test_migrations.py` |

## Test Suite Summary

| Suite | Tests | Status |
| :--- | :--- | :--- |
| Security, Auth, SSRF, & Web Isolation | 63 | Passed |
| Tasks & UI States | 7 | Passed |
| Machines & VPS Diagnostics | 12 | Passed |
| Migrations (Baseline, M1, M2, Idempotency, Upgrades) | 8 | Passed |
| Milestone 1 Project Intelligence & Knowledge | 49 | Passed |
| Milestone 2 Execution, Evidence & Dependencies | 8 | Passed |
| Milestone 2 HTTP Routes (Deliverables, Gates, Events, Revisions) | 6 | Passed |
| Markdown Safety & Knowledge Core | 7 | Passed |
| **Total Test Suite** | **179 passed** | **100% SUCCESS (0 failed)** |
## Production deployment status
* **Milestone 1:** Successfully deployed and verified live in production (`scratch\qwen-collective`, port 8000). Service wrapper WinSW PID 22708, Uvicorn PID 22340 active, pre-migration snapshot created, production DB upgraded to `user_version = 1`.
* **Milestone 2:** Fully implemented and validated in dev worktree (`scratch\qwen-collective-dev`). All 179 tests pass. Awaiting user authorization to deploy.
* **Steps to deploy Milestone 2 to Production (once authorized):**
  1. Take full database & vault backup using `scripts\backup-windows.ps1`.
  2. Fast-forward merge `feature/project-intelligence` into `fix/security-reliability-audit` in `scratch\qwen-collective`.
  3. Push to `origin/fix/security-reliability-audit`.
  4. User restarts service: `Restart-Service QwenCollective` in elevated PowerShell.
  5. On startup, `init_db()` creates snapshot `qwen_collective.pre-migration-v1-to-v2-<UTC>.db`, applies Migration 2, and sets `user_version = 2`.
  6. Verify live routes on port 8000.
