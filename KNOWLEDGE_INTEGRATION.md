# Knowledge Integration (Milestone 1)

How the existing Markdown vault and SQLite FTS5 index serve as the knowledge layer for Projects. This describes current behavior on `feature/project-intelligence`.

## Principles as implemented

| Principle | Implementation |
| :--- | :--- |
| Narrative stays in Markdown | Note bodies are `.md` files in the vault (`<data>/knowledge/`). Obsidian is not required. |
| Structure lives in SQLite | Type, origin, review status, provenance, project link, supersession, and typed relations are stored in `knowledge_metadata` and `knowledge_relations`. |
| The vault is not reorganized | Existing notes are **never moved or modified**. "Link an existing vault note" only adds a metadata row. |
| Knowledge doesn't require a project | `project_id` is optional, and `register_existing_note()` works without one. The global Knowledge page is unchanged apart from a metadata panel. |
| Remembering ≠ knowing | Every entry enters as `captured` (or `proposed`), whatever its origin. Promotion is a recorded human review. |

## Storage

* **New project notes** are written to `projects/<slug>-<id8>/<title_slug>.md` with this frontmatter:
  ```
  ---
  title: …
  kn_id: kn-…
  type: finding
  project: prj-…
  ---
  ```
  * Frontmatter values are reduced to a single line, so a title can't inject extra frontmatter keys.
  * If the metadata insert fails, the file that was just written is deleted, keeping the vault and DB consistent.
  * Writing to an existing filename is refused (no silent overwrite).
* **Indexing.** Vault listing and FTS indexing are now **recursive**, so nested `projects/…` notes are found. Before this branch they were limited to one level.
* **Path safety.** `_safe_note_path()` resolves the path and requires it to stay inside the vault and end in `.md`. Tests cover `../`, `..\`, absolute paths, and non-`.md` targets.

## Metadata (`knowledge_metadata`)

| Field | Values / notes |
| :--- | :--- |
| `knowledge_type` | source, finding, assumption, decision, procedure, task_result, lesson_learned, forecast_rationale, observation, project_brief, general_reference |
| `origin` | human, ai_generated, external_source, vps_extracted, machine_measurement, system |
| `trust_status` | captured, proposed, verified, accepted, disputed, superseded |
| `source_url` | Only `http(s)://` is accepted, so `javascript:` and similar schemes are rejected |
| `source_retrieved_at`, `source_citation` | Provenance |
| `project_id` | Owning project (optional). Never changed by cross-project linking. |
| `author`, `tags`, `created_at`, `updated_at` | |
| `reviewed_by`, `reviewed_at`, `review_note`, `review_count`, `last_verified_at` | Review trail |
| `superseded_by` | Required if and only if the status is `superseded` (CHECK constraint) |

### Provenance rules
* `external_source` and `vps_extracted` entries **must** have a source URL or a citation.
* `ai_generated`, `external_source`, and `vps_extracted` are flagged as **untrusted evidence** everywhere they appear: the project knowledge table, Path evidence lists, HTMX search results, and the note view.
* Note bodies are untrusted content:
  * The Markdown renderer strips raw HTML and allows only `http`, `https`, `mailto`, relative, and `#` URLs.
  * FTS snippets are HTML-escaped before `<mark>` highlighting is re-inserted.
* Nothing in M1 feeds note content to a model. The M4 rule that it must be treated as evidence, never instructions, is specified in [PROJECT_CONTEXT_SPEC.md](PROJECT_CONTEXT_SPEC.md).

### Review workflow (trust transitions)

| From | Allowed to |
| :--- | :--- |
| captured | proposed, verified, disputed |
| proposed | captured, verified, disputed |
| verified | accepted, disputed, superseded |
| accepted | disputed, superseded |
| disputed | proposed, verified, superseded |
| superseded | (terminal) |

* Disputing or superseding requires a review note. Superseding requires a different, non-superseded replacement entry.
* DB triggers enforce two rules: inserts must be `captured` or `proposed`, and promotion to `verified` or `accepted` requires `reviewed_by`, `reviewed_at`, and `review_count` to increase by exactly 1. A plain status flip can't skip the review trail.
* Review status records **workflow, not truth**. The UI says this wherever the status appears.

## Relations (`knowledge_relations`)

* **Targets:** `project`, `path`, `stage`, `task`, `knowledge`.
* **Types:** about, supports, challenges, evidence_for, rationale_for, result_of, procedure_for, derived_from, related_to.
* Duplicates are rejected by a unique index on (knowledge, target_type, target, relation), and self-links are rejected by a CHECK constraint.
* Target existence is validated in the service. On project routes, the **target must belong to that project**.
* **Cross-project reuse.** A note owned by project A can be linked to a target inside project B (through the service, with B's scope). It then appears in B's knowledge list labelled "reused from another project", and its original `project_id` and provenance are kept. The project UI only offers linking for entries already visible in that project.
* **Navigation:**
  * The Paths tab lists each Path's evidence and linked knowledge, with trust and origin labels.
  * The Knowledge tab lists each entry's relations.

## Retrieval

* `list_project_knowledge(pid)` returns entries owned by the project plus entries linked to the project or any of its Paths, stages, or tasks. Results are ordered by review status (accepted → verified → proposed → captured → disputed → superseded), then by recency.
* `search_project_knowledge(pid, q)` runs FTS5 restricted to those notes. The restriction is applied **inside the SQL query, before `LIMIT 30`**, so unrelated vault matches can't push project notes out (covered by a regression test). Each hit carries its trust status, type, origin, and link kind.

## Not in Milestone 1
* Automatic knowledge capture from task results, and the promotion of lessons to collective knowledge (M2)
* Assumption ↔ evidence views (M2/M4)
* Feeding knowledge to Qwen (M4)
* Editing metadata after creation, other than review actions
* Bulk registration of existing notes
