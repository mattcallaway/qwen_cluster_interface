# Project Data Model (Milestones 1, 2, 3 & 4, schema version 4)

This describes the schema that **exists** on `feature/project-intelligence`. Source of truth:
[`app/migrations.py`](app/migrations.py). Business rules: [`app/services/projects.py`](app/services/projects.py),
[`app/services/tasks.py`](app/services/tasks.py), [`app/services/deliverables.py`](app/services/deliverables.py),
[`app/services/knowledge_meta.py`](app/services/knowledge_meta.py), [`app/services/metrics.py`](app/services/metrics.py),
[`app/services/context_packet.py`](app/services/context_packet.py), [`app/services/proposals.py`](app/services/proposals.py).

## Schema versioning

| Version | Meaning |
| :--- | :--- |
| 0 | Baseline: the tables that existed before this work, created by `create_baseline_schema()` (DDL unchanged). |
| 1 | Migration 1: Project intelligence foundation (projects, paths, stages, knowledge_metadata, knowledge_relations, project_decisions). |
| 2 | Migration 2: Execution and evidence (deliverables, deliverable_revisions, task_dependencies, task_execution_events, path_revisions, collective knowledge). |
| 3 | Migration 3: Measurement and forecasting (project_metrics, metric_observations, path_forecasts, forecast_evaluations). |
| 4 | Migration 4: Project-aware intelligence and human authorization (project_proposals, proposal_acceptance decision type). |

* The version lives in `PRAGMA user_version`.
* `init_db()` runs on every start. It (1) creates the baseline tables if they're missing, (2) takes a `sqlite3.backup()` snapshot when an **existing** DB is behind, named `qwen_collective.pre-migration-v{cur}-to-v{target}-{UTC}.db` and stored next to the DB, then (3) applies pending migrations sequentially.
* Each migration runs in one `BEGIN IMMEDIATE … COMMIT` together with the version bump and a `PRAGMA foreign_key_check`. Any failure rolls the whole migration back.
* Existing tables receive non-destructive ALTER statements or safe table recreations (`project_decisions`).
* Verified on production copy with clean foreign key and integrity checks.

## Entities

```mermaid
erDiagram
    projects ||--o{ projects : "parent_id (optional)"
    projects ||--o{ project_paths : "has alternatives"
    projects |o--o| project_paths : "selected_path_id"
    project_paths ||--o{ path_stages : "ordered by position"
    project_paths ||--o{ path_revisions : "historical snapshots"
    path_stages ||--o{ deliverables : "stage deliverables"
    deliverables ||--o{ deliverable_revisions : "acceptance revisions"
    projects ||--o{ tasks : "project_id (optional)"
    project_paths ||--o{ tasks : "path_id (optional)"
    path_stages ||--o{ tasks : "stage_id (optional)"
    deliverables ||--o{ tasks : "deliverable_id (optional)"
    tasks ||--o{ task_dependencies : "prerequisite graph"
    tasks ||--o{ task_execution_events : "operational logs"
    projects ||--o{ knowledge_metadata : "project_id (optional)"
    knowledge_metadata ||--o{ knowledge_relations : "typed links"
    projects ||--o{ project_decisions : "append-only"
    projects ||--o{ project_metrics : "defines"
    project_paths ||--o{ project_metrics : "path_id (optional)"
    project_metrics ||--o{ metric_observations : "immutable actuals"
    metric_observations ||--o{ metric_observations : "supersedes_id (corrections)"
    project_metrics ||--o{ path_forecasts : "versioned predictions"
    project_paths ||--o{ path_forecasts : "path forecasts"
    path_forecasts ||--o{ forecast_evaluations : "evaluates"
    metric_observations ||--o{ forecast_evaluations : "actual measurement"
```

### `projects`
| Column | Notes |
| :--- | :--- |
| `id` | `prj-<uuid hex>` |
| `name` | 1–200 chars, unique (case-insensitive) |
| `description`, `objective`, `scope`, `constraints_text`, `success_criteria` | Free text, at most 20,000 chars (enforced in the service) |
| `priority` | `low · medium · high · critical` |
| `status` | `draft · planning · active · paused · completed · cancelled · archived` |
| `parent_id` | Optional FK → `projects`. Can't be self (CHECK). Cycles are rejected and depth is capped at 3 (service). |
| `owner` | Username of the creator |
| `selected_path_id` | Optional FK → `project_paths`. A trigger requires the Path to belong to the same project. |
| `knowledge_dir` | Vault folder `projects/<slug>-<last 8 of id>`, unique |
| `start_date`, `target_date`, `completed_date`, `review_date` | ISO dates. `start_date` is set the first time the project becomes active. `completed_date` is set on completion. |
| `created_at`, `updated_at`, `last_activity_at` | UTC ISO timestamps |

**Project status transitions** (service-enforced; every change is recorded as a decision):

| From | Allowed to |
| :--- | :--- |
| draft | planning, active, cancelled, archived |
| planning | draft, active, paused, cancelled |
| active | paused, completed, cancelled |
| paused | active, planning, cancelled |
| completed / cancelled | archived |
| archived | (none) |

### `project_paths`
Contains strategy fields: `rationale` (**required**), `description`, `assumptions`, `constraints_text`, `dependencies`, `resource_requirements`, `risks`, `estimated_cost` (≥ 0), `estimated_effort_hours` (≥ 0), `expected_outcome`, `expected_completion_date`, `forecast_confidence` (0–1). Path names are unique within a project.

| From | Allowed to |
| :--- | :--- |
| proposed | under_review, approved, rejected |
| under_review | proposed, approved, rejected |
| approved | rejected, superseded, **active (via Select only)** |
| active | paused, completed |
| paused | superseded, **active (via Select only)** |
| superseded / completed / rejected | (terminal) |

* **Only one active Path per project.** Enforced by the partial unique index `ux_one_active_path_per_project`, not just in code.
* `select_path()` is the only way a Path becomes `active`. It requires an approved or paused Path and a reason. It sets the previous active, approved, or paused selection to `superseded`, updates `selected_path_id`, and appends a `path_selection` decision recording from, to, reason, user, and time.
* Every other status change also requires a reason and is recorded as a `path_status_change` decision.
* **Estimates freeze once a Path leaves `proposed`/`under_review`.** Superseded Paths are never deleted, so historical estimates stay visible for comparison.

### `path_stages` (extended in Milestone 2)
`position` is unique per Path. Other fields: `name`, `objective`, `description`, `prerequisites`, `expected_outputs`, `completion_criteria`, `estimated_effort_hours`, `actual_effort_hours`, `status`, and timestamps.

* **Stage completion gates (`evaluate_stage_gate`).** Evaluated deterministically:
  1. All tasks belonging to the stage must be in `completed` status.
  2. All deliverables belonging to the stage must be in `accepted` status.
  3. The stage must have verifiable work (at least one completed task, accepted deliverable, or linked verified evidence).
* **Stage advancement (`advance_stage`).** Transitions stage to `completed` upon satisfying the gate, records a `stage_advancement` decision, and automatically activates the next planned stage in position order on the active Path.

### `deliverables` & `deliverable_revisions` (Milestone 2)
Deliverables represent tangible outputs required by a stage or strategic path. Task completion does not automatically constitute deliverable acceptance.

| Column | Notes |
| :--- | :--- |
| `id` | `del-<uuid hex>` |
| `project_id`, `path_id`, `stage_id` | Foreign keys with project hierarchy consistency |
| `name`, `description` | Deliverable title and narrative description |
| `deliverable_type` | `document · dataset · report · software_artifact · configuration · research_result · published_material · completed_workflow · other` |
| `acceptance_criteria` | Formal standards required for sign-off |
| `status` | `planned · in_progress · submitted · changes_requested · accepted · rejected` |
| `artifact_references` | Documented locations, file paths, commit hashes, or URLs |
| `verification_results` | Automated or manual verification summaries |
| `acceptance_decision` | Recorded reviewer rationale |
| `accepted_by`, `accepted_at` | Formal sign-off identity and timestamp |

* Every submission and review transition snapshots a historical record into `deliverable_revisions`.
* Formal acceptance logs a `deliverable_acceptance` decision into `project_decisions`.

### `task_dependencies` (Milestone 2)
Manages prerequisite relationships (`task_id` depends on `depends_on_task_id`).
* **Cycle Prevention:** Graph traversal verifies that adding an edge does not form a circular dependency. Self-dependencies are rejected.
* **Prerequisite Enforcement:** `validate_project_task_dispatch()` blocks dispatch or approval if any declared prerequisite task is not in `completed` status.

### `task_execution_events` (Milestone 2)
Captures operational execution history for bounded tasks.
* Records `attempt_number`, `outcome` (`completed · failed · aborted`), `attempted`, `actual_result`, `evidence_summary`, `assumptions_feedback`, and `reusable_lessons`.
* Optionally generates a durable Markdown vault note in the project's knowledge directory and creates an explicit `result_of` relation pointing back to the task.

### `path_revisions` (Milestone 2)
When a strategy changes, `revise_path()` archives a snapshot of previous estimates, rationale, risks, and assumptions into `path_revisions` with a sequential `revision_number`, required `revision_reason`, author, and timestamp. A `path_revision` decision is recorded in `project_decisions`.

### `knowledge_metadata` & Collective Promotion (Milestone 2)
Extended with `is_collective` (INTEGER), `promoted_by`, and `promoted_at`.
* Verified or accepted project knowledge can be promoted to **Collective Memory**.
* Promotion logs a `knowledge_promotion` decision in `project_decisions`.

### `project_metrics` (Milestone 3)
Measures actual performance and project outcomes.
* `id`: `met_<uuid>`
* `project_id`: FK → `projects`
* `path_id`: Optional FK → `project_paths`
* `name`: 1–200 chars, unique per project
* `metric_type`: `count · percentage · rate · duration · currency · quality · reliability · other`
* `unit`: string (e.g. `req/s`, `ms`, `USD`, `%`, `points`, `tokens`)
* `measurement_method`: `automated_query · manual_audit · task_telemetry · benchmark · manual`
* `collection_frequency`: `per_task · daily · weekly · stage_gate · ad_hoc`
* `target_value`: Optional numeric target
* `target_date`: Optional ISO target date
* `direction_of_improvement`: `higher_is_better · lower_is_better · target_is_optimal`
* `owner`, `status` (`active · archived`), timestamps.

### `metric_observations` (Milestone 3)
Actual measurements stored as immutable, timestamped records.
* `id`: `obs_<uuid>`
* `metric_id`: FK → `project_metrics`
* `value`: numeric REAL
* `measured_at`: timestamp of actual measurement
* `recorded_at`: timestamp of database entry
* `verification_status`: `unverified · verified · rejected`
* `evidence_reference`: task ID, note path, or external link
* `supersedes_id`: FK → `metric_observations` (for auditable corrections)
* `correction_reason`: mandatory when `supersedes_id` is set
* Database triggers `trg_observations_no_update` and `trg_observations_no_delete` enforce that observation records are never updated or deleted.

### `path_forecasts` (Milestone 3)
Predictions, not targets and not actuals. Versioned and historical predictions are preserved indefinitely.
* `id`: `fct_<uuid>`
* `metric_id`: FK → `project_metrics`
* `path_id`: Optional FK → `project_paths`
* `version`: auto-incrementing integer (v1, v2, ...) per `(metric_id, path_id)`
* `predicted_value`: numeric REAL
* `method`: `linear_trend · rolling_average · rate_extrapolation · manual_estimate · model_estimate`
* `confidence_level`: `low · medium · high`
* `revision_reason`: mandatory for version > 1
* Database triggers `trg_forecasts_no_update` and `trg_forecasts_no_delete` ensure forecasts are versioned and immutable.

### `forecast_evaluations` (Milestone 3)
Records historical accuracy and variance assessments comparing predictions against actual observations.
* `forecast_id`, `metric_id`, `path_id`, `actual_observation_id`
* `target_value`, `predicted_value`, `actual_value`
* `variance`: `actual - predicted`
* `absolute_error`: `abs(actual - predicted)`
* `percentage_error`: `(absolute_error / abs(predicted)) * 100`
* `accuracy_score`: normalized accuracy between 0.0 and 1.0
* `status_assessment`: `on_target · ahead_of_forecast · behind_forecast · at_risk · insufficient_data`

### `project_proposals` (Milestone 4)
Supports AI-assisted planning where the model proposes and a human operator authorizes.

| Column | Notes |
| :--- | :--- |
| `id` | `prop_<12 hex>` |
| `project_id` | FK → `projects` (ON DELETE CASCADE) |
| `path_id` | Optional FK → `project_paths` |
| `stage_id` | Optional FK → `path_stages` |
| `proposal_type` | `path_proposal · task_decomposition · metric_suggestion · path_review` |
| `title` | 1–200 characters |
| `summary` | Natural-language summary of what is proposed |
| `proposed_data_json` | Valid JSON object with structured proposed parameters (e.g. stages, tasks, metrics) |
| `status` | `proposed · under_review · accepted · rejected · cancelled` |
| `ai_model` | Inference model identifier (e.g. `qwen`, `fallback_heuristic`) |
| `prompt_tokens`, `completion_tokens` | Token usage tracking |
| `context_packet_summary` | Record of context packet size and sections used during generation |
| `review_notes` | Required reason on rejection; optional notes on acceptance |
| `reviewed_by`, `reviewed_at` | Operator identity and review timestamp |
| `created_by`, `created_at`, `updated_at` | Audit timestamps |

**Human Approval Workflow Rules:**
* The model proposes; the human decides.
* The model cannot silently alter project goals, activate paths, delete decisions, alter measurements, or approve its own deliverables.
* Accepting a `path_proposal` creates durable `project_paths` and `path_stages` records and logs an append-only `proposal_acceptance` decision.
* Accepting a `task_decomposition` creates draft tasks linked to the stage.
* Accepting a `metric_suggestion` creates active `project_metrics`.
* Rejecting a proposal updates status to `rejected` with recorded reason and creates zero application records.

### `project_decisions` (append-only)
Expanded CHECK constraint allows:
`decision_type ∈ {path_selection, path_status_change, project_status_change, project_parent_change, stage_advancement, stage_status_change, deliverable_acceptance, path_revision, knowledge_promotion, proposal_acceptance}`.
Each row records `subject_id`, `from_value`, `to_value`, `rationale` (required), `decided_by`, `decided_at`, and optional `evidence_knowledge_id`. The triggers `trg_decisions_no_update` and `trg_decisions_no_delete` abort any UPDATE or DELETE.

## Project Context Packet Hierarchy

Prompt retrieval adheres to an explicit priority ordering under a strict token budget:

1. **User Instruction / Directive:** Exact current user prompt or operational request.
2. **Project Definition & Bounds (Required):** Goal, scope, constraints, and success criteria.
3. **Strategic Path & Stage Context:** Active strategy, objectives, prerequisites, and completion criteria.
4. **Key Decisions & Deliverables:** Past decisions, accepted deliverables, and current gate status.
5. **Active Work & Task State:** Work in progress, dependencies, and execution history.
6. **Metrics & Forecast Tracking:** Targets, current observations, variance, and forecast models.
7. **Project Supporting Knowledge:** Verified and captured notes in the project knowledge dir.
8. **Cross-Project Lessons & Shared Notes:** Collective wisdom and promoted insights.

When token budget limits are reached, sections are pruned from lowest priority (8 and 7 first) with explicit omission notices injected into the rendered prompt. Security separation labels (`[TRUSTED_PROJECT_BOUNDS]`, `[VERIFIED_RECORDS]`, `[EXTERNAL_UNTRUSTED]`) clearly separate verified project data from untrusted user content.

## Integrity guarantees and their limits

| Guarantee | Enforced by |
| :--- | :--- |
| Enum values, numeric ranges, non-empty names and rationales | CHECK constraints |
| Single active Path | Partial unique index |
| Selected Path belongs to the project | Trigger |
| Task trace consistency | Triggers (insert and update) |
| Task dependency cycles | Service graph traversal |
| Prerequisite task completion before dispatch | Dispatch validation |
| Stage completion gate requirements | Deterministic gate service |
| Knowledge enters only as captured or proposed; promotion needs review | Triggers & service rules |
| Decisions immutable | Triggers (abort on update/delete) |
| Deliverables immutable once accepted/rejected | Service transition rules |
| Observations immutable | Triggers (abort on update/delete; corrections supersede) |
| Forecasts versioned and immutable | Triggers (abort on update/delete) |
| AI proposals require human approval | Human-in-the-loop review service & status check |
| Unique metric names per project | UNIQUE index |
| Relation targets exist and belong to the scoped project | Service polymorphic validation |

> [!WARNING]
> Triggers and constraints protect against application bugs and naive writes. They are **not a security boundary** against anyone with write access to the SQLite file.
