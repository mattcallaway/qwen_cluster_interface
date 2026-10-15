# Project Orchestrator REST API Specification

## 1. Authentication and Security Headers

All Project Orchestrator endpoints require authenticated sessions and strict CSRF protection:

1. **Authentication**: All endpoints require a valid administrative session cookie (`qc_session`). Requests without a valid session receive HTTP `401 Unauthorized`.
2. **CSRF Protection**: All mutating HTTP methods (`POST`, `PUT`, `DELETE`) require a valid `X-CSRF-Token` header matching the user's active session. Requests missing this header or providing an invalid token receive HTTP `403 Forbidden`.
3. **Content-Type**: All JSON requests must send `Content-Type: application/json`.
4. **Standard Response Envelope**:
   - Success: `{"ok": true, "result": { ... }}` (HTTP 200)
   - Failure: `{"ok": false, "error": "<detailed message>"}` (HTTP 400, 401, 403, 404, or 409)

---

## 2. API Endpoints

### 2.1 Project Creation & Initialization

#### `POST /api/v1/orchestration/projects`
Creates a new project record, initializes the intake parameters, and provisions an orchestration run in `DRAFT` state.

- **Headers**: `X-CSRF-Token: <token>`
- **Request Body**:
  ```json
  {
    "name": "Sonoma County Housing Study",
    "objective": "Research housing affordability, analyze datasets, and prepare a summary report.",
    "autonomy_level": 1,
    "constraints": ["Run local inference only"],
    "max_runtime_hours": 2
  }
  ```
- **Response** (HTTP 200):
  ```json
  {
    "ok": true,
    "result": {
      "project_id": "proj-9f8a3c",
      "run_id": "run-018e4a",
      "state": "DRAFT"
    }
  }
  ```

#### `POST /api/v1/projects/{project_id}/orchestration/init`
Attaches an orchestration run to an existing legacy project without altering existing project tasks.

- **Headers**: `X-CSRF-Token: <token>`
- **Request Body**:
  ```json
  {
    "objective": "Optional override objective",
    "autonomy_level": 1
  }
  ```
- **Response** (HTTP 200):
  ```json
  {
    "ok": true,
    "result": {
      "run_id": "run-018e4b",
      "state": "DRAFT"
    }
  }
  ```

---

### 2.2 Planning & Revisions

#### `POST /api/v1/projects/{project_id}/orchestration/plan`
Triggers plan generation for the project. Transitions run state from `DRAFT` -> `PLANNING` -> `AWAITING_PLAN_APPROVAL`.

- **Headers**: `X-CSRF-Token: <token>`
- **Request Body**:
  ```json
  {
    "mode": "auto",
    "planner_model": "qwen2.5:1.5b"
  }
  ```
  *(Mode can be `"auto"`, `"template"`, or `"model"`)*
- **Response** (HTTP 200):
  ```json
  {
    "ok": true,
    "result": {
      "revision_id": "rev-018e4c",
      "revision_number": 1,
      "state": "AWAITING_PLAN_APPROVAL",
      "task_count": 4,
      "validation": { "valid": true, "errors": [] }
    }
  }
  ```

#### `GET /api/v1/projects/{project_id}/orchestration/plan`
Retrieves the active plan revision, validation diagnostics, and complete revision history.

- **Response** (HTTP 200):
  ```json
  {
    "ok": true,
    "result": {
      "current": {
        "id": "rev-018e4c",
        "revision_number": 1,
        "status": "pending_approval",
        "source": "template",
        "plan": {
          "title": "Distributed System Health Report",
          "tasks": [
            {
              "id": "collect_vps",
              "title": "Collect VPS Health",
              "action_type": "library_script",
              "script_id": "sys_health_basic",
              "target_node": "qwen-vps",
              "dependencies": []
            }
          ]
        },
        "validation": { "valid": true, "errors": [] }
      },
      "history": [ ... ]
    }
  }
  ```

#### `PUT /api/v1/projects/{project_id}/orchestration/plan`
Allows operator to edit the plan JSON while in `DRAFT` or `AWAITING_PLAN_APPROVAL`. Validates the updated graph and increments `revision_number`.

- **Headers**: `X-CSRF-Token: <token>`
- **Request Body**:
  ```json
  {
    "plan": { ... }
  }
  ```
- **Response** (HTTP 200):
  ```json
  {
    "ok": true,
    "result": {
      "revision_id": "rev-018e4d",
      "revision_number": 2,
      "state": "AWAITING_PLAN_APPROVAL"
    }
  }
  ```

#### `POST /api/v1/projects/{project_id}/orchestration/approve`
Approves the current plan revision and materializes tasks into `orchestration_tasks`. Transitions `AWAITING_PLAN_APPROVAL` -> `READY`.

- **Headers**: `X-CSRF-Token: <token>`
- **Response** (HTTP 200):
  ```json
  {
    "ok": true,
    "result": {
      "state": "READY",
      "materialized_tasks": 4
    }
  }
  ```

#### `POST /api/v1/projects/{project_id}/orchestration/reject`
Rejects the proposed plan. Transitions `AWAITING_PLAN_APPROVAL` -> `DRAFT`.

- **Headers**: `X-CSRF-Token: <token>`
- **Request Body**: `{"reason": "Need fewer tasks"}`

#### `POST /api/v1/projects/{project_id}/orchestration/clarify`
Submits feedback or clarification for a plan, triggering a replan iteration.

- **Headers**: `X-CSRF-Token: <token>`
- **Request Body**: `{"notes": "Include swap memory analysis"}`

---

### 2.3 Execution Controls

All execution control endpoints require `X-CSRF-Token`.

| Endpoint | Method | Allowed From States | To State | Description |
| :--- | :---: | :--- | :--- | :--- |
| `.../start` | POST | `READY` | `RUNNING` | Begins autonomous task scheduling. |
| `.../pause` | POST | `RUNNING` | `PAUSED` | Suspends new task dispatches; running tasks complete. |
| `.../resume` | POST | `PAUSED` | `RUNNING` | Resumes automated scheduling. |
| `.../cancel` | POST | Any non-terminal | `CANCELLED` | Aborts execution and marks run cancelled. |
| `.../replan` | POST | `RUNNING`, `FAILED`, `BLOCKED` | `PLANNING` | Generates a new plan accommodating current state. |
| `.../step` | POST | `RUNNING`, `PAUSED` | *(Current)* | Executes exactly one ready task (Autonomy Level 0). |

#### `POST /api/v1/projects/{project_id}/orchestration/autonomy`
Updates the autonomy level dynamically.
- **Request Body**: `{"level": 2}`

#### `POST /api/v1/projects/{project_id}/orchestration/tasks/{task_id}/retry`
Manually resets a failed task attempt to `pending` and increments the allowed retry count.

#### `POST /api/v1/projects/{project_id}/orchestration/approvals/{approval_id}`
Decides a pending approval request.
- **Request Body**:
  ```json
  {
    "decision": "approved",
    "notes": "Approved disk diagnostic script execution."
  }
  ```
  *(Decision must be `"approved"` or `"rejected"`)*

---

### 2.4 Status, Observability & Artifacts

#### `GET /api/v1/projects/{project_id}/orchestration/status`
Returns a unified snapshot of the orchestrator state, current tasks, active approvals, and resource usage.

- **Response** (HTTP 200):
  ```json
  {
    "ok": true,
    "result": {
      "run": {
        "id": "run-018e4a",
        "state": "RUNNING",
        "autonomy_level": 2,
        "current_revision_id": "rev-018e4c",
        "created_at": "2026-10-15T16:00:00Z"
      },
      "tasks": [
        {
          "id": "orch_task_1",
          "task_key": "collect_vps",
          "title": "Collect VPS Health",
          "action_type": "library_script",
          "status": "completed",
          "assigned_node": "qwen-vps"
        }
      ],
      "approvals": [],
      "telemetry": {
        "tasks_completed": 3,
        "inference_tokens": 1420,
        "runtime_seconds": 24.5
      }
    }
  }
  ```

#### `GET /api/v1/projects/{project_id}/orchestration/events`
Returns the append-only event stream for audit tracking.
- **Query Parameters**: `after_id=0&limit=100`

#### `GET /api/v1/projects/{project_id}/orchestration/artifacts`
Returns all artifacts produced by tasks or the controller.

#### `GET /api/v1/projects/{project_id}/orchestration/artifacts/{artifact_id}/download`
Downloads the requested artifact file.
- **Security**: Verifies that the resolved path is strictly within the allowed evidence directory. Directory traversal (`../`) is blocked with HTTP 400.
- **Headers**:
  - `X-Content-Type-Options: nosniff`
  - `X-Artifact-SHA256: <hex_sha256>`
  - `Content-Disposition: attachment; filename="<filename>"`

#### `GET /api/v1/orchestration/models`
Returns available Ollama models across all online worker nodes.

---

### 2.5 UI Routes

- `GET /projects/orchestrate/new`: Displays the new project intake form with model selector and autonomy settings.
- `GET /projects/{project_id}/orchestrator`: Displays the full live execution dashboard with real-time state visualization.
