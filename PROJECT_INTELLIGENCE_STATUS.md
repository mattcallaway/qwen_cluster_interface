# Project Intelligence — Status

**Current milestone: 1 (Integrated foundation). Implemented, tested, awaiting your review.**
Branch: `feature/project-intelligence`, based on `fix/security-reliability-audit` @ `6786203`.
**Not deployed.** The production service and database have not been changed (see the last section).

## What works now (verified)

| Capability | Where | Verified by |
| :--- | :--- | :--- |
| Projects: create, edit definition, status transitions with recorded reasons, optional parent (cycle and depth checks), archive filter | `/projects`, Overview tab | `test_projects_m1.py`, `test_projects_routes.py`, browser check |
| Project dashboard: selected Path, current stage, tasks pending approval, Paths awaiting decision, risks, recent decisions and knowledge. **Persisted counts only; no progress percentages.** | Overview tab | Route tests, browser check |
| Path alternatives with side-by-side comparison (status, rationale, cost, effort, confidence, completion, stage count) | Paths tab | Route tests, browser check |
| Path approval and selection. Selection requires a reason, keeps exactly one active Path (DB-enforced), supersedes the previous one, and records a decision | Paths tab | Unit and HTTP tests |
| Ordered stages with ↑/↓ reordering and M1 statuses (planned/active/blocked) | Paths tab | Unit and HTTP tests |
| Project-linked tasks with traceability checks. Project tasks are never queued without approval, and approval re-checks everything | Tasks tab | Unit and HTTP tests |
| Typed project knowledge with provenance, review status, typed relations, cross-project reuse, and Path evidence lists | Knowledge and Paths tabs | `test_knowledge_meta.py`, route tests |
| Project-scoped FTS5 search (HTMX) with trust and untrusted-origin labels | Knowledge tab | Route tests, browser check |
| Knowledge record panel on the global note view | `/knowledge/view` | Route test |
| Versioned, transactional migrations with an automatic pre-migration backup | `app/migrations.py` | `test_migrations.py`, dry run on a production copy |

**M1 acceptance check:** a user can create a project, compare Paths, select one, organize stages, link tasks and evidence, and retrieve the project's knowledge. This full sequence runs over HTTP in `test_full_m1_flow_through_http`. It was also done manually on an isolated dev server (port 8010, throwaway data), with no 500s or template errors in the server log.

## Fixes to pre-existing code made on this branch
1. **Stored XSS in the note viewer.** Raw HTML and `javascript:` links passed through Markdown into `|safe` output. Fixed and tested.
2. **Stored XSS in knowledge search snippets.** Fixed and tested.
3. **Write-lock leak in `create_task`.** A failed INSERT left a connection holding a write lock, which caused later "database is locked" errors. Fixed.
4. Vault listing and indexing were non-recursive, so nested notes were invisible. Now recursive.

## Tests

| Suite | Count |
| :--- | :--- |
| Baseline (before this branch) | 86 |
| New: `test_markdown_safety` 3, `test_migrations` 7, `test_projects_m1` 30, `test_knowledge_meta` 19, `test_projects_routes` 19 | 78 |
| **Full suite now** | **164 passed, 0 failed** |

* All tests are integration tests against a real temporary SQLite DB and the real FastAPI app. **No behavior is mocked in the M1 tests.** One migration test uses `monkeypatch` only to point `settings.data_dir` at a temporary directory.
* Areas covered:
  * Auth redirects and CSRF rejection
  * Cross-project ID tampering on nested routes
  * Path traversal
  * Escaping of user text and of untrusted note content in HTMX responses
  * DB-level guards (single active Path, trace triggers, append-only decisions, review triggers)
  * Migration rollback on failure
  * Preservation of populated v0 data
  * Idempotency

## Known limitations (M1)
* **Single-user authorization.** The app has one admin account and no per-user ownership. Object-level authorization in M1 means three things: authentication, CSRF on every POST, and checks that child IDs (Path, stage, task, knowledge) belong to the project in the URL.
* **No stage completion.** Stages can't be marked completed, skipped, or awaiting_review until M2 gates exist. A Path *can* be marked completed by a human (with a recorded reason), with no gate check.
* **Scope expansion is a human judgment.** Dispatch checks confirm traceability and that the required fields exist. They can't tell whether a task's content exceeds the approved scope.
* **No executor consumes `queued` tasks** in this codebase. A future dispatcher must re-run `validate_project_task_dispatch()` at dispatch time.
* **No Path editing UI.** The service supports editing proposed and under-review Paths, but no form exposes it.
* **DB triggers are not a security boundary** against someone who can write to the SQLite file directly.
* Parent-cycle prevention is enforced in the service only (SQLite triggers can't recurse).
* Knowledge metadata can't be edited after creation, except through review actions.
* No Deliverables or Metrics tabs. They are deferred to M2 and M3 and deliberately not shown as empty placeholders.

## Deferred (not started)
* **M2:** task dependencies, stage completion gates, deliverables and acceptance, artifact references, task-result knowledge capture, Path revision history, knowledge promotion to collective
* **M3:** metrics, observations, versioned forecasts, variance and accuracy
* **M4:** Context Packets (spec only: [PROJECT_CONTEXT_SPEC.md](PROJECT_CONTEXT_SPEC.md)), AI proposals, Path reviews

## Production deployment (requires your approval; not done)
* The live `QwenCollective` service runs from the main checkout at `6786203`. It was confirmed **Running**, `/auth/login` returned 200, and the production DB is still `user_version = 0`.
* Deploying means merging this branch into the service's directory and restarting the service. On the first start, `init_db()` will:
  1. snapshot the DB to `qwen_collective.pre-migration-v0-to-v1-<UTC>.db`
  2. apply migration 1 in a single transaction
* Dry run on a copy of the production DB: counts preserved, `integrity_check` ok, FK check clean, idempotent.
* **Rollback:**
  1. Stop the service.
  2. Restore the pre-migration snapshot (or `backups\qwen_collective_backup_2026-10-14_010900.zip`).
  3. Check out `fix/security-reliability-audit`.
  4. Start the service.
