# Project Orchestrator Implementation Guide

## 1. Overview and Codebase Structure

The Project Orchestrator is integrated directly into the Qwen Collective codebase without disrupting legacy task or machine endpoints. All core orchestrator logic is housed within the `app/services/orchestration/` package, with routing in `app/routes/orchestration.py`, database schema migrations in `app/migrations.py`, and client-side UI in `app/templates/projects/` and `app/static/`.

```
app/
├── config.py                                 # QC_ORCHESTRATOR_ENABLED setting
├── main.py                                   # Lifespan background engine loop
├── migrations.py                             # Schema migration v9 (_M9)
├── routes/
│   └── orchestration.py                      # REST API & HTML UI routes
├── static/
│   ├── css/orchestrator.css                  # Modern dark-mode styling & glassmorphism
│   └── js/
│       ├── orchestrator.js                   # Live dashboard polling & reactive DOM
│       └── orchestrator_new.js               # New project intake & model picker
├── templates/projects/
│   ├── index.html                            # "Orchestrate with AI" button added
│   ├── detail.html                           # "Orchestrator" view button added
│   ├── orchestrator.html                     # Live project execution dashboard
│   └── orchestrator_new.html                 # New orchestrated project creation view
└── services/
    └── orchestration/
        ├── __init__.py                       # Package exports
        ├── common.py                         # Event logging, DB helpers, custom errors
        ├── states.py                         # State machine, transitions, status sync
        ├── script_library.py                 # Allowlisted script library (sys_health_basic)
        ├── task_graph.py                     # DAG cycle checks, injection filters, action validation
        ├── model_router.py                   # Capability matching, inventory, Ollama provider
        ├── recovery.py                       # Failure classification, exponential backoff
        ├── telemetry.py                      # Resource budget tracking & breach checks
        ├── artifact_manager.py               # Registration, path traversal guard, downloads
        ├── evaluator.py                      # Deterministic schema evaluation (qc.health.v1)
        ├── planner.py                        # Template & LLM planner, JSON repair loops, revisions
        ├── approval_manager.py               # Human gatekeeper for plans, tasks, budgets, deliverables
        ├── context_manager.py                # Context aggregation across multi-step tasks
        ├── reporting.py                      # Markdown report compilation
        └── engine.py                         # Distributed scheduler lock, background tick loop
```

---

## 2. Database Migration v9 (`_M9`)

Migration `_M9` in `app/migrations.py` adds 12 dedicated tables strictly through additive DDL (`CREATE TABLE IF NOT EXISTS`), ensuring backward compatibility with existing project and task records:

1. `orchestration_runs`: Primary lifecycle record tracking `project_id`, `state`, `autonomy_level`, `current_revision_id`, `intake_json`, `plan_json`, `state_version`, `created_at`, `updated_at`.
2. `plan_revisions`: Immutable history of generated and edited plans, storing `revision_number`, `source` (`template`, `model`, `human`), `plan_json`, `validation_json`, `status`, and `approved_by`.
3. `orchestration_events`: Append-only audit ledger recording all transitions, task dispatches, worker heartbeats, and approval decisions.
4. `orchestration_approvals`: Gatekeeper records for human decisions (`plan`, `task`, `budget`, `deliverable`), with `status` (`pending`, `approved`, `rejected`), notes, and approver identity.
5. `orchestration_tasks`: Normalized task definitions derived from approved plans, tracking `task_key`, `action_type`, `required_role`, `status` (`pending`, `queued`, `running`, `completed`, `failed`), and retries.
6. `orchestration_task_deps`: Directed task dependency edges (`parent_task_id` -> `child_task_id`).
7. `orchestration_task_attempts`: Individual execution attempts of an orchestrated task, mapping to physical worker tasks via `worker_task_id`, tracking lease IDs, timestamps, and error classifications.
8. `orchestration_artifact_metadata`: Metadata for all project deliverables, tracking SHA-256 hashes, byte sizes, relative paths, MIME types, and producing tasks.
9. `orchestration_resource_budgets`: Guardrail limits per project (`max_tasks`, `max_inference_tokens`, `max_runtime_seconds`, `max_retries_per_task`, `max_wall_clock_hours`).
10. `orchestration_model_inference_requests`: Detailed telemetry for all LLM calls (planner and task inference), recording prompt lengths, generation tokens, latencies, and models.
11. `orchestration_scheduler_lock`: Single-row distributed lock (`lock_id='primary'`) with an expiration timestamp (`expires_at`) to ensure only one engine tick executes concurrently.
12. `orchestration_checkpoints`: Serialized execution state snapshots for disaster recovery.

---

## 3. Module Walkthrough

### 3.1 `common.py`
Provides core database access primitives, structured JSON serialization helpers, error definitions (`OrchestrationError`, `InvalidTransition`, `NotFound`, `StaleState`), and `log_event()` for append-only audit tracking.

### 3.2 `states.py`
Enforces the lifecycle state machine. The `transition()` function accepts `(run_id, from_state, to_state, actor, reason, details)`. It atomically verifies the existing state, applies the update, bumps `state_version`, writes an audit row to `orchestration_events`, and synchronizes the parent project status in `projects` using `sync_project_status()`.

### 3.3 `script_library.py`
Maintains pre-vetted, sandboxed diagnostic scripts. The primary script `sys_health_basic` gathers kernel version, CPU cores, load averages, memory/swap totals, uptime, and disk usage without invoking shell commands or elevated privileges. Outputs strictly adhere to the `qc.health.v1` schema.

### 3.4 `task_graph.py`
Validates plan structures using Kahn's algorithm for topological sorting. It ensures all dependency IDs exist, detects circular dependencies, verifies that action types belong to the allowlist, and scans input fields for prompt-injection markers using strict regex patterns.

### 3.5 `model_router.py`
Inspects candidate infrastructure nodes from `infrastructure_nodes` and `worker_auth_tokens`.
- Filters nodes by health (`online`) and recency (`last_contact_at` < 60s ago).
- Matches required capabilities (e.g. `python-3.*` runtime or `ollama` with the requested model).
- Implements `WorkerOllamaProvider` which delegates inference to worker nodes via the standard worker lease queue, supporting local inference without exposing Ollama ports directly to external networks.

### 3.6 `recovery.py`
Classifies execution errors into operational categories (`timeout`, `oom`, `network_disconnect`, `script_exit`, `format_error`). Calculates exponential backoff intervals with jitter and enforces the per-task retry budget.

### 3.7 `telemetry.py`
Maintains real-time counters of consumed resources (tasks executed, LLM tokens generated, total worker execution seconds). When usage exceeds configured limits, the engine transitions the run to `BLOCKED` and creates a `budget` approval record.

### 3.8 `artifact_manager.py`
Handles artifact registration, SHA-256 calculation, and delivery. The `resolve_for_download()` function verifies that requested files exist within approved evidence directories, strictly preventing directory traversal (`../`) attacks.

### 3.9 `evaluator.py` and `reporting.py`
Validates task results against deterministic criteria. Once all tasks are complete, `reporting.py` compiles raw worker telemetry and model summaries into a markdown report, distinguishing between factual system data and AI narrative interpretation.

### 3.10 `planner.py`
Implements two planning modes:
1. `template`: Instant, deterministic plan generation for standard workflows (such as cluster health diagnostics).
2. `model`: Invokes local LLMs (e.g. `qwen2.5:1.5b` or `llama3.2:1b`) with few-shot JSON formatting prompts. If the model returns malformed JSON, `repair_plan_json()` executes up to two automated correction prompts.

### 3.11 `approval_manager.py`
Coordinates human sign-offs. Supports approving or rejecting plan revisions, authorizing high-risk tasks, extending resource budgets, and signing off on final project deliverables.

### 3.12 `engine.py`
The central heartbeat. Implements `acquire_scheduler_lock()` to ensure single-leader execution. The `tick()` method executes every 2 seconds:
- Cleans up expired leases and disconnected workers.
- Gathers completed worker tasks and updates attempt records.
- Dispatches eligible tasks to available nodes.
- When all tasks finish, triggers report compilation and deliverable evaluation.

---

## 4. Integration with Existing System

### 4.1 Worker Agent Updates (`vps_agent/executor.py`)
- Raised `MAX_NUM_PREDICT` from 256 to 1024 tokens to support comprehensive system summaries.
- Added support for an optional `temperature` parameter (default 0.0 for deterministic outputs).

### 4.2 Application Lifespan (`app/main.py`)
The orchestrator engine is integrated into FastAPI's lifespan context. When `QC_ORCHESTRATOR_ENABLED=1`, a background asyncio task runs `engine.background_loop()` on a 2-second interval, cleanly shutting down on application exit.

### 4.3 SQLite Concurrency Handling
All database modifications use short-lived `with db_transaction() as cursor:` blocks. Nested transactions are strictly avoided to prevent SQLite database locks. All cross-module status synchronizations are performed immediately after transactions commit.
