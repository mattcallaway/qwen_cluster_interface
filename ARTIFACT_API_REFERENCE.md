# Qwen Collective — Artifact & Deliverable API Reference

## 1. Overview

The Unified Artifact & Deliverables system exposes both HTML server-rendered user endpoints and RESTful JSON APIs for distributed workers, frontend AJAX/HTMX components, and programmatic orchestrator services.

---

## 2. Web UI Routes

| Method | Route | Description |
|---|---|---|
| `GET` | `/deliverables` | **Global Deliverables Hub**: Cross-project library with full-text search, type filtering, sorting, and artifact cards. |
| `GET` | `/deliverables/{artifact_id}` | **Artifact Detail & Preview View**: Full preview, metadata, version history, lineage graph, and one-click actions. |
| `POST` | `/deliverables/{artifact_id}/reuse` | **Use in This Project**: Dispatches a reuse workflow (analyze, revise, expand, summarize, etc.). |
| `POST` | `/deliverables/{artifact_id}/create-project` | **Create Project From This**: Initializes a new project with authorized source artifact context. |
| `POST` | `/deliverables/{artifact_id}/knowledge` | **Add to Knowledge**: Extracts findings and concept assertions into the knowledge store. |
| `GET` | `/projects/{project_id}` | Project detail view with **Deliverables Tab** displaying deliverables and managed artifacts with one-click Open/Download. |

---

## 3. REST API Endpoints

### 3.1 Artifacts Management (`/api/v1/artifacts`)

#### `GET /api/v1/artifacts`
Search and list artifacts across the system (subject to user permissions).
- **Query Parameters**:
  - `q` (string, optional): Full-text search query across filenames, descriptions, and extracted content.
  - `project_id` (string, optional): Filter by associated project.
  - `artifact_type` (string, optional): Filter by type (`document`, `dataset`, `report`, `code`, `config`, `image`, etc.).
  - `status` (string, optional): Filter by lifecycle status (`available`, `accepted`, `needs_review`, etc.).
  - `limit` (integer, default 50): Number of records.
  - `offset` (integer, default 0): Paging offset.
- **Response**:
```json
{
  "total": 1,
  "artifacts": [
    {
      "id": "art-1b2c3d4e5f6a",
      "display_name": "quarterly_telemetry.json",
      "artifact_type": "dataset",
      "mime_type": "application/json",
      "size_bytes": 10482,
      "checksum_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "current_version_number": 2,
      "status": "accepted",
      "knowledge_status": "extracted",
      "origin_project_id": "proj-98765",
      "created_at": "2026-10-18T14:30:00Z"
    }
  ]
}
```

#### `GET /api/v1/artifacts/{id}`
Retrieve complete metadata, version list, and direct links for a specific artifact.

#### `GET /api/v1/artifacts/{id}/download`
Download the exact authorized physical file.
- **Query Parameters**:
  - `v` (integer, optional): Specific version number (defaults to current active version).
  - `project_id` (string, optional): Requesting project ID for authorization validation.
- **Headers**:
  - `Content-Disposition: attachment; filename="<sanitized_name>"`
  - `Content-Type: <mime_type>`
  - `ETag: "<checksum_sha256>"`

#### `GET /api/v1/artifacts/{id}/preview`
Retrieve safe rendered preview HTML or JSON structure.
- **Query Parameters**:
  - `v` (integer, optional): Version number.

#### `GET /api/v1/artifacts/{id}/versions`
Retrieve all historical versions for an artifact, including change descriptions, timestamps, and committers.

#### `GET /api/v1/artifacts/{id}/lineage`
Retrieve the complete provenance DAG:
```json
{
  "artifact_id": "art-1b2c3d4e5f6a",
  "upstream": [
    {
      "source_artifact_id": "art-0a1b2c3d4e5f",
      "relationship_type": "derived_from",
      "source_version": 1,
      "target_version": 2,
      "context_description": "Cleaned raw telemetry dataset"
    }
  ],
  "downstream": [
    {
      "target_artifact_id": "art-9f8e7d6c5b4a",
      "relationship_type": "analyzes",
      "context_description": "Generated latency regression report"
    }
  ]
}
```

#### `POST /api/v1/artifacts/{id}/reuse`
Create a reuse task in a specified project.
- **Request Body**:
```json
{
  "project_id": "proj-98765",
  "action_type": "analyze",
  "instructions": "Generate summary statistical distributions and charts.",
  "actor": "admin"
}
```

#### `POST /api/v1/artifacts/{id}/create-project`
Create a new project pre-populated with this deliverable as its foundational context.
- **Request Body**:
```json
{
  "user_objective": "Build a public interactive dashboard from this benchmark report.",
  "title": "Interactive Benchmark Site",
  "created_by": "lead_architect"
}
```

#### `POST /api/v1/artifacts/{id}/knowledge`
Extract findings and assertions from the artifact into the project knowledge store.

---

### 3.2 Worker Task Artifact Endpoints (`/api/v1/worker`)

#### `POST /api/v1/worker/tasks/{task_id}/artifacts`
Authenticated upload of a completed task output file.
- **Authentication**: Worker Bearer Token (`Authorization: Bearer <worker_token>`).
- **Form Data**:
  - `file`: Multipart file stream.
  - `filename`: Target display name.
  - `artifact_type`: Content category (`code`, `dataset`, `report`, `output`).
  - `expected_sha256`: Worker's calculated SHA-256 for verification.
- **Response**:
```json
{
  "status": "stored",
  "artifact_id": "art-1b2c3d4e5f6a",
  "version": 1,
  "checksum_sha256": "e3b0c442...",
  "checksum_matches": true,
  "size_bytes": 45102
}
```

#### `GET /api/v1/worker/tasks/{task_id}/artifacts/{artifact_id}`
Materialize an input artifact inside an authorized worker sandbox.
- **Verification**: Verifies that the task is leased to the calling worker and that `artifact_id` is declared in the task's `input_artifacts`.
- **Response**: File stream with `ETag` matching the verified checksum.
