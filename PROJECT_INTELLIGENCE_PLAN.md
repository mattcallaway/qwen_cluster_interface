# Project Intelligence — Implementation Plan

Status: **Milestone 1 implemented and tested; awaiting review.** Branch `feature/project-intelligence`. Not deployed. See `PROJECT_INTELLIGENCE_STATUS.md`.
Base commit: `6786203` (`fix/security-reliability-audit`)

---

## 1. Inspection findings (2026-10-14, verified against source)

| Area | Finding |
| :--- | :--- |
| Branch / commit | `fix/security-reliability-audit` @ `6786203`, clean tree. 86 tests pass. |
| Live service | `QwenCollective` (WinSW, LocalSystem) is **Running** from the main working directory `scratch/qwen-collective`. Process tree: services.exe → WinSW → `.venv\Scripts\python.exe` → Uvicorn, owning `127.0.0.1:8000`. |
| Code the service runs | It runs whatever is on disk in the main working directory when it starts, which is `6786203` today. |
| Schema management | **No migration system.** `init_db()` runs on every startup and only issues `CREATE TABLE IF NOT EXISTS`. Production `PRAGMA user_version = 0`. |
| Production data | 1 user, 1 knowledge note, 0 tasks, 0 machines. `integrity_check = ok`, `foreign_key_check` clean. |
| Tasks | `tasks` table with string IDs (`task-xxxxxxxx`), statuses `draft/queued/running/awaiting_approval/completed/failed/cancelled`, an `approval_required` flag, `related_knowledge_path`. Nothing links tasks to projects. |
| Knowledge | Markdown vault at `<data>/knowledge/<category>/*.md`. FTS5 table `knowledge_fts(path, title, category, content)`. Listing and indexing are **non-recursive** (`glob("*.md")`). No metadata, types, trust, or relations. |
| Auth | Single admin account. HMAC-signed session cookie, CSRF token derived from the session, PBKDF2 passwords, persistent login rate limiting. **No per-user object ownership exists.** |
| Tests | Isolated: `conftest.py` sets `QC_DATA_DIR` to a temp directory before importing config. `load_dotenv` doesn't override variables that are already set, so tests never touch production data. |

### Pre-existing defects found during inspection

1. **Stored XSS in the knowledge viewer.** `knowledge/note.html` renders `rendered_html|safe`. Python-Markdown passes raw HTML through and accepts `javascript:` links.
2. **Stored XSS in knowledge search.** `knowledge/index.html` renders the FTS `snippet|safe`. The snippet is raw note text with `<mark>` tags inserted, so any HTML in a note runs when it appears in search results.

Both become reachable by untrusted input as soon as external or VPS-extracted knowledge enters the vault, which is exactly what this feature enables. They are fixed in Milestone 1, with behavioral tests.

---

## 2. Compatibility risks and mitigations

| Risk | Mitigation |
| :--- | :--- |
| **The live service would load feature code on its next restart.** Migrations run in `init_db()`, so a crash, a reboot, or SCM recovery would migrate the production DB with unreviewed code. | Development happens in a separate **git worktree** at `scratch/qwen-collective-dev`. The service's directory stays on `fix/security-reliability-audit`. Nothing is deployed without approval. |
| An ALTER on `tasks` could lose data. | `ALTER TABLE ADD COLUMN` only (nullable columns, no table rebuild). A test migrates a populated v0 DB and checks the rows are preserved. |
| A partial migration leaves the schema inconsistent. | Each migration runs inside an explicit `BEGIN IMMEDIATE … COMMIT`, with `PRAGMA user_version` bumped in the same transaction. A test injects a failing migration and checks for a full rollback. |
| Production DB loss during migration. | `init_db()` writes a `sqlite3.backup()` snapshot (`qwen_collective.pre-migration-v{cur}-to-v{target}-{UTC stamp}.db`, next to the DB) before applying migrations to a DB that already existed. |
| Making vault indexing recursive surfaces notes that weren't indexed before. | Production has one note. The change is additive, and `read_note` already handles nested paths. |
| Polymorphic knowledge relations can't use FKs. | Target existence is validated in the service layer, and the target type is restricted by a `CHECK` constraint. Documented in `PROJECT_DATA_MODEL.md`. |

---

## 3. Milestone 1 scope (this branch)

**Build:**
- Versioned, transactional migrations (`app/migrations.py`, `PRAGMA user_version`)
- Projects: CRUD, explicit status transitions, optional parent with cycle and depth checks
- Paths: alternatives per project, explicit status machine, DB-enforced single active Path, selection recorded as an append-only decision
- Stages: ordered per Path, reordering, limited M1 statuses
- Tasks: optional `project_id/path_id/stage_id/purpose/expected_output/completion_criteria`. Project tasks can't be approved or queued unless traceable to the selected Path and a valid stage.
- Knowledge metadata: type, origin, trust status, provenance, project association, supersession
- Knowledge relations: typed links to project, path, stage, task, or knowledge
- UI: a Projects sidebar entry, a project dashboard, and a project detail page with Overview, Paths, Tasks, and Knowledge views
- Security: the XSS fixes above, CSRF on every POST, and parent/child ID consistency checks on every nested route

**Explicitly deferred:**
- M2: task dependencies, stage completion gates, deliverables, artifact references, task-result capture, Path revision history, knowledge promotion workflow
- M3: metrics, observations, forecasts
- M4: Context Packets, AI proposals, Path reviews

The Deliverables and Metrics tabs are **not shown** until their milestones exist.

**No new dependencies.** No new framework, vector DB, broker, or agent framework.

---

## 4. Rollback

- **Code:** don't merge `feature/project-intelligence`. The service directory never leaves `fix/security-reliability-audit`.
- **Data (after any future approved deployment):** stop the service, then restore either `qwen_collective.pre-migration-v0-to-v1-*.db` or the latest `backups\qwen_collective_backup_*.zip`.
- The pre-development production backup is `backups\qwen_collective_backup_2026-10-14_010900.zip`. It has been verified: it extracts, `integrity_check = ok`, and the user row and vault note are present.
