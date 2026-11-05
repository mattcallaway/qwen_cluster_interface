# Qwen Collective: REST API & Integration Specification

> **Target Audience**: External clients, web dashboard developers, automation scripts, and LLM tool-calling agents.  
> **Base URL**: `http://127.0.0.1:8000` (Local) or `https://<tailscale-ip>:8000` (Mesh)  
> **Protocol**: HTTP/1.1 with TLS over Tailscale WireGuard  
> **Data Format**: `application/json` (UTF-8 encoded)

---

## 1. Authentication & Security Headers

All requests to mutating endpoints must satisfy authentication and CSRF protections.

| Header | Requirement | Description |
|---|---|---|
| `Authorization` | Required for Worker & API endpoints | Format: `Bearer <token>`. Node tokens are issued during cluster provisioning. |
| `Cookie` | Required for Web UI Sessions | Encrypted session cookie `qc_session` created upon successful administrative login. |
| `X-CSRF-Token` | Required for Mutating Web Requests | PBKDF2 CSRF token embedded in rendered templates or returned via login handshake. |
| `Content-Type` | Required for POST/PUT | Must be explicitly set to `application/json`. |

---

## 2. Autonomous Project Orchestrator APIs

### 2.1 Create Project Orchestration Run
Creates a new project run container in `DRAFT` state.

- **Endpoint**: `POST /api/v1/orchestrator/runs`
- **Request Body**:
```json
{
  "project_id": "prj-a148ae3fcc924b98b88c4d1e432c1337",
  "objective": "Build an automated YouTube channel research pipeline and revenue model",
  "scope": "Local research and automated scripts only",
  "deliverables": "Comprehensive research report, code template, financial projection",
  "success_criteria": "Verifiable sources and clear budget",
  "deadline": "2026-11-01"
}
```
- **Response (201 Created)**:
```json
{
  "run_id": "run-b98a12330639461f",
  "state": "DRAFT",
  "version": 1,
  "created_at": "2026-10-18T20:00:00Z"
}
```

---

### 2.2 Trigger Analysis & Plan Generation
Advances a run from `DRAFT` through `ANALYZING` and submits an asynchronous planning request.

- **Endpoint**: `POST /api/v1/orchestrator/runs/{run_id}/plan`
- **Request Body**:
```json
{
  "mode": "auto",
  "planner_model": "qwen2.5:1.5b"
}
```
- **Response (200 OK)**:
```json
{
  "state": "PLANNING",
  "request_id": "inf-858d5c12f16d4b2a",
  "node": "qwen-laptop",
  "model": "qwen2.5:1.5b"
}
```

---

### 2.3 Approve Generated Plan
Human approval gate: moves run from `AWAITING_PLAN_APPROVAL` to `READY`.

- **Endpoint**: `POST /api/v1/orchestrator/runs/{run_id}/approve`
- **Request Body**:
```json
{
  "revision_id": "rev-51bafb8009594912",
  "actor": "admin"
}
```
- **Response (200 OK)**:
```json
{
  "state": "READY",
  "active_revision": "rev-51bafb8009594912",
  "tasks_enqueued": 4
}
```

---

### 2.4 Trigger Task Execution Tick
Manually forces an orchestrator scheduling tick (normally handled by background timer).

- **Endpoint**: `POST /api/v1/orchestrator/tick`
- **Response (200 OK)**:
```json
{
  "active_runs": 1,
  "tasks_dispatched": 2,
  "completed_runs": []
}
```

---

## 3. Distributed Worker Protocol APIs

These endpoints are used exclusively by background worker agents (e.g. `vps_agent`).

### 3.1 Worker Registration
Registers worker specs, platform details, and capabilities with the controller.

- **Endpoint**: `POST /api/v1/worker/register`
- **Headers**: `Authorization: Bearer <worker_token>`
- **Request Body**:
```json
{
  "node_id": "qwen-vps",
  "hostname": "vps-companion-01",
  "ip_address": "100.64.0.5",
  "platform": "Linux 6.8.0-amd64",
  "capabilities": ["python_execution", "browser_automation", "scheduled_execution"],
  "resources": {
    "cpu_cores": 4,
    "ram_total_mb": 8192,
    "disk_free_gb": 45.2
  }
}
```
- **Response (200 OK)**:
```json
{
  "status": "registered",
  "assigned_node_id": "qwen-vps",
  "registered_at": "2026-10-18T20:00:00Z"
}
```

---

### 3.2 Worker Heartbeat
Refreshes live node availability timestamp and system telemetry.

- **Endpoint**: `POST /api/v1/worker/heartbeat`
- **Headers**: `Authorization: Bearer <worker_token>`
- **Request Body**:
```json
{
  "node_id": "qwen-vps",
  "metrics": {
    "cpu_percent": 14.2,
    "ram_used_mb": 3120,
    "active_tasks": 1
  }
}
```
- **Response (200 OK)**:
```json
{
  "status": "acknowledged",
  "server_time": "2026-10-18T20:00:10Z"
}
```

---

### 3.3 Lease Available Task
Atomic pull leasing: retrieves and locks the next topologically ready task.

- **Endpoint**: `POST /api/v1/worker/tasks/lease`
- **Headers**: `Authorization: Bearer <worker_token>`
- **Request Body**:
```json
{
  "node_id": "qwen-vps",
  "supported_capabilities": ["python_execution", "browser_automation"]
}
```
- **Response (200 OK - Task Available)**:
```json
{
  "status": "leased",
  "task": {
    "id": "tsk-4289a0b1c2d3",
    "key": "t2",
    "title": "Automate Video Creation Pipeline",
    "action": {
      "type": "library_script",
      "script_id": "sys_health_basic",
      "params": {}
    },
    "idempotency_key": "idemp-9f8e7d6c5b4a",
    "lease_expires_at": "2026-10-18T20:05:00Z",
    "timeout_seconds": 300
  }
}
```
- **Response (200 OK - No Tasks)**:
```json
{
  "status": "idle",
  "task": null
}
```

---

### 3.4 Submit Task Result
Submits final task execution output and completes the lease.

- **Endpoint**: `POST /api/v1/worker/tasks/{task_id}/submit`
- **Headers**: `Authorization: Bearer <worker_token>`
- **Request Body**:
```json
{
  "node_id": "qwen-vps",
  "idempotency_key": "idemp-9f8e7d6c5b4a",
  "status": "completed",
  "output": {
    "summary": "Execution successful",
    "files": [
      {
        "filename": "pipeline_output.json",
        "mime_type": "application/json",
        "content_base64": "eyJyZXN1bHQiOiAidmFsaWRhdGVkIn0="
      }
    ]
  },
  "execution_metrics": {
    "duration_ms": 1420.5,
    "memory_peak_mb": 42.1
  }
}
```
- **Response (200 OK)**:
```json
{
  "status": "closed",
  "task_id": "tsk-4289a0b1c2d3",
  "artifacts_created": ["art-c1a2b3d4e5f6"]
}
```

---

## 4. Unified Artifacts & Deliverables APIs

### 4.1 List Project Artifacts
Queries all artifacts linked to a specific project.

- **Endpoint**: `GET /api/v1/projects/{project_id}/artifacts`
- **Query Parameters**:
  - `is_deliverable` (optional, boolean): Filter only formal deliverables.
  - `type` (optional, string): Filter by `report`, `code`, `dataset`, `document`.
- **Response (200 OK)**:
```json
[
  {
    "id": "art-c1a2b3d4e5f6",
    "name": "research_report.md",
    "artifact_type": "report",
    "mime_type": "text/markdown",
    "is_deliverable": 1,
    "current_version": {
      "version_number": 1,
      "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "byte_size": 18420,
      "author": "planner-agent",
      "created_at": "2026-10-18T20:00:00Z"
    }
  }
]
```

---

### 4.2 Download Raw Artifact Content
Streams raw binary or text content directly from Content-Addressable Storage (CAS).

- **Endpoint**: `GET /api/v1/artifacts/{artifact_id}/download`
- **Headers Sent**: `Accept: */*`
- **Response (200 OK)**:
  - `Content-Type`: `text/markdown` (matches artifact MIME type)
  - `Content-Disposition`: `attachment; filename="research_report.md"`
  - `ETag`: `"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"`
  - Body: Binary stream of content.

---

### 4.3 Transfer Artifact Across Projects
Transfers an artifact into a target project with complete upstream lineage.

- **Endpoint**: `POST /api/v1/artifacts/{artifact_id}/transfer`
- **Request Body**:
```json
{
  "target_project_id": "prj-f2f7445c985749dea02955c3e1a9df96"
}
```
- **Response (201 Created)**:
```json
{
  "new_artifact_id": "art-89f7a6b5c4d3",
  "target_project_id": "prj-f2f7445c985749dea02955c3e1a9df96",
  "lineage_edge": "transferred_to"
}
```

---

### 4.4 Promote Deliverable to Obsidian Vault
Exports an approved deliverable to durable Markdown storage in the Obsidian Knowledge Vault.

- **Endpoint**: `POST /api/v1/artifacts/{artifact_id}/promote`
- **Request Body**:
```json
{
  "vault_path": "research/automated_youtube_architecture.md",
  "tags": ["automation", "architecture", "research"]
}
```
- **Response (200 OK)**:
```json
{
  "status": "promoted",
  "note_id": "not-1a2b3c4d5e6f",
  "indexed_fts": true
}
```

---

## 5. Web Research & Citation APIs

### 5.1 Execute Multi-Provider Search
Runs a federated query across configured search engines.

- **Endpoint**: `POST /api/v1/research/search`
- **Request Body**:
```json
{
  "project_id": "prj-a148ae3fcc924b98b88c4d1e432c1337",
  "queries": ["headless youtube channel automation tools 2026", "youtube api video upload limits"],
  "provider": "duckduckgo"
}
```
- **Response (200 OK)**:
```json
{
  "status": "completed",
  "results_count": 10,
  "sources": [
    {
      "url": "https://example.com/automation-guide",
      "title": "Headless Automation Guide",
      "snippet": "Building an automated pipeline using Python...",
      "rank": 1
    }
  ]
}
```

---

### 5.2 Citation Audit Report
Verifies claim-level evidence mapping for an assembled report.

- **Endpoint**: `GET /api/v1/research/projects/{project_id}/citation-audit`
- **Response (200 OK)**:
```json
{
  "total_claims": 8,
  "verified_claims": 8,
  "unsupported_claims": 0,
  "sources_cited": 4,
  "citations": [
    {
      "claim": "YouTube API v3 enforces a default limit of 10,000 units per day.",
      "citation_tag": "[1]",
      "source_url": "https://developers.google.com/youtube/v3/getting-started",
      "confidence": 0.95
    }
  ]
}
```

---

## 6. System Observatory Telemetry APIs

### 6.1 Cluster Real-Time Health Snapshot
Retrieves the latest cluster-wide telemetry roll-up.

- **Endpoint**: `GET /api/v1/observatory/snapshots/latest`
- **Response (200 OK)**:
```json
{
  "timestamp": "2026-10-18T20:00:00Z",
  "cluster_status": "healthy",
  "nodes_online": 3,
  "nodes_offline": 0,
  "active_tasks": 1,
  "inference_latency_p95_ms": 1420.0,
  "nodes": [
    {"node_id": "controller-local", "status": "online", "cpu_percent": 12.0, "ram_free_mb": 12400},
    {"node_id": "qwen-laptop", "status": "online", "cpu_percent": 28.5, "ram_free_mb": 1850},
    {"node_id": "qwen-vps", "status": "online", "cpu_percent": 8.2, "ram_free_mb": 5120}
  ]
}
```
