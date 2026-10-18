# Qwen Collective — Web Research API Specification

## 1. Overview & Authentication

The Autonomous Web Research & Browsing Engine exposes authenticated REST endpoints under the `/api/v1/projects/{project_id}/research/` routing prefix.

- **Authentication**: Bearer token via `Authorization: Bearer <token>` or active session cookie (`qwen_session`).
- **Authorization**: Enforces existing project ownership and membership permissions via `require_project_permission()`.
- **CSRF Defense**: State-modifying requests (`POST`, `PUT`, `DELETE`) require a valid `X-CSRF-Token` header.

---

## 2. API Endpoints

### 2.1. Generate Research Strategy
**`POST /api/v1/projects/{id}/research/plan`**
Analyzes the project's objective and constructs a multi-step research strategy DAG with query definitions, target domains, and resource budgets.

**Request Body (Optional)**:
```json
{
  "focus_areas": ["battery energy density", "grid-scale LFP economics"],
  "depth": "comprehensive",
  "budget_overrides": {
    "max_queries": 20,
    "max_docs": 40
  }
}
```

**Response (200 OK)**:
```json
{
  "status": "planned",
  "project_id": "prj-3b88a0c5f114",
  "strategy": {
    "primary_question": "What are the latest developments in renewable energy storage?",
    "subquestions": [
      "Commercial grid-scale battery chemistries deployed in 2025-2026",
      "Cost per MWh comparisons between LFP, sodium-ion, and flow batteries"
    ],
    "initial_queries": [
      "grid scale renewable energy storage developments 2025 2026",
      "LFP vs sodium ion battery cost per MWh utility storage"
    ],
    "target_domains": ["energy.gov", "irena.org", "nrel.gov"],
    "budget": {
      "max_queries": 20,
      "max_docs": 40,
      "max_browsing_seconds": 180.0
    }
  }
}
```

---

### 2.2. Trigger Autonomous Research Cycle
**`POST /api/v1/projects/{id}/research/start`**
Dispatches or initiates the research execution workflow.

**Request Body (Optional)**:
```json
{
  "topic": "Renewable Energy Storage Review",
  "max_queries": 10
}
```

**Response (200 OK)**:
```json
{
  "status": "completed",
  "project_id": "prj-3b88a0c5f114",
  "plan": { ... },
  "search": { "results_count": 8 },
  "retrieval": { "sources_count": 4 },
  "verification": { "claims_count": 3 },
  "report": {
    "status": "success",
    "citations_count": 4,
    "markdown_length": 4210
  }
}
```

---

### 2.3. Query Execution & History
**`GET /api/v1/projects/{id}/research/queries`**
Returns all search queries formulated and executed for this project, including provider used, duration, and status.

**Response (200 OK)**:
```json
{
  "project_id": "prj-3b88a0c5f114",
  "count": 2,
  "queries": [
    {
      "id": "qry-a901ffc29b",
      "query_text": "grid scale renewable energy storage developments",
      "provider": "duckduckgo",
      "status": "completed",
      "results_count": 5,
      "duration_ms": 782.4,
      "created_at": "2026-10-18T10:14:00Z"
    }
  ]
}
```

---

### 2.4. Project Source Library
**`GET /api/v1/projects/{id}/research/sources`**
Lists all persistent, project-isolated sources ingested into the source library.

**Response (200 OK)**:
```json
{
  "project_id": "prj-3b88a0c5f114",
  "count": 3,
  "sources": [
    {
      "id": "src-71ba0b3f88d2",
      "canonical_url": "https://www.nrel.gov/storage-report-2025.html",
      "title": "Grid-Scale Battery Storage Cost Trajectory",
      "publisher": "National Renewable Energy Laboratory",
      "publication_date": "2025-11-12",
      "quality_score": 0.95,
      "retrieval_method": "http",
      "content_length_bytes": 48210,
      "status": "extracted"
    }
  ]
}
```

---

### 2.5. Evidence Ledger & Claims
**`GET /api/v1/projects/{id}/research/claims`**
Returns all material factual claims tracked for the project along with attached supporting passages, contradiction flags, and corroboration statuses.

**Response (200 OK)**:
```json
{
  "project_id": "prj-3b88a0c5f114",
  "count": 2,
  "claims": [
    {
      "id": "clm-44b209c118",
      "claim_text": "LFP utility battery pack prices declined by 18% year-over-year in 2025.",
      "category": "fact",
      "verification_status": "CORROBORATED",
      "evidence_count": 2,
      "contradiction_details": null,
      "created_at": "2026-10-18T10:15:30Z"
    }
  ]
}
```

---

### 2.6. Citation Resolution & Audit Report
**`GET /api/v1/projects/{id}/research/citations`**
Returns the deterministic citation audit for the project's generated research deliverable.

**Response (200 OK)**:
```json
{
  "project_id": "prj-3b88a0c5f114",
  "total_citations": 4,
  "citations": [
    {
      "id": "cit-884bf92a10",
      "citation_num": 1,
      "source_id": "src-71ba0b3f88d2",
      "formatted_citation": "[1] Grid-Scale Battery Storage Cost Trajectory — National Renewable Energy Laboratory. URL: https://www.nrel.gov/storage-report-2025.html (Accessed 2026-10-18)",
      "is_verified": 1,
      "validation_notes": "Deterministically validated against source library"
    }
  ]
}
```

---

### 2.7. Export Citations
**`GET /api/v1/projects/{id}/research/export?format=markdown`**
Exports all project citations in standard formats (`markdown`, `bibtex`, or `json`).
