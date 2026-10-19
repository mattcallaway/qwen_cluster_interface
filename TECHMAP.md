# Qwen Collective: Technical Architecture Map (TECHMAP)

## 1. Architectural Philosophy & Mental Model

Qwen Collective is a **sovereign, distributed AI coordination and execution platform**. It is engineered to maximize sovereign hardware efficiency, prevent AI hallucinations about real-world infrastructure, enforce strict human approval boundaries before execution, and ensure every useful output becomes a durable, discoverable, versioned, reusable project asset.

```text
+-----------------------------------------------------------------------------------+
|                        LAYER 1: CENTRAL AUTHORITATIVE CONTROLLER                  |
|                        Windows Host (Local / Tailscale Mesh)                      |
|                                                                                   |
|  - Authoritative SQLite Database (WAL Mode, FTS5 Full-Text Search)                |
|  - Autonomous Project Orchestrator (Intake, Task Graph, Checkpoints, Approvals)   |
|  - Unified Deliverables & Artifact Manager (CAS Storage, Versioning, Lineage)     |
|  - Continuous Web Research Engine (Multi-provider Search, Verification, Citations)|
|  - System Observatory & Telemetry (Real-time Cluster Metrics, Alerts, Snapshots)  |
|  - Infrastructure & Capability Registry (Hardware Inventory, Probe Verification)  |
|  - Durable Knowledge Vault (Obsidian Markdown Notes, Dual-Store Index)            |
|  - WinSW Windows Service Wrapper (Auto-recovery, Rotating Logs)                   |
+-----------------------------------------------------------------------------------+
                                         |
               +-------------------------+-------------------------+
               | Private Tailscale Mesh                            | Private WireGuard / HTTPS
               v                                                   v
+------------------------------------+           +------------------------------------+
|  LAYER 2: LOCAL INFERENCE CLUSTER  |           |   LAYER 3: REMOTE OPERATIONAL NODE |
|  Linux Laptops / Workstations      |           |   Cloud VPS Execution Companion    |
|                                    |           |                                    |
| - Low-Power Compute Nodes (4GB RAM)|           | - Dedicated Python Worker Agent    |
| - Quantized Qwen2.5 (0.5B - 7B)    |           | - Outbound Pull-Based Task Leasing |
| - llama.cpp / Ollama / llama-server|           | - Sandboxed Python/Shell Jobs      |
| - Read-Only Diagnostic Probes (SSH)|           | - Systemd Services & Timers        |
| - Reasoning & Planning Sub-agents  |           | - Offline Disconnect Result Buffer |
+------------------------------------+           +------------------------------------+
                                         ^
                                         | (Ingests Context & Emits Verified Assets)
+-----------------------------------------------------------------------------------+
|                        LAYER 4: PERSISTENT KNOWLEDGE ENGINE                       |
|  - Raw Markdown files in an Obsidian-compatible vault (100% human-readable)       |
|  - Dynamic SQLite FTS5 Full-Text search indexing with BM25 ranking                |
|  - Reusable Deliverable & Knowledge Impact Promotion Engine                       |
+-----------------------------------------------------------------------------------+
```

---

## 2. Complete Database Schema & Migration Progression (M0 – M10)

The authoritative controller database uses **SQLite 3 in WAL (Write-Ahead Logging) mode** with busy timeouts and foreign key constraints enabled. Schema evolution is managed via sequential, transaction-safe migrations in `app/migrations.py`:

```text
M0 (Baseline Security & Auth)
  ├── users (Administrator accounts, bcrypt password hashes, last login)
  ├── sessions (Signed cookie tokens, expiration timestamps)
  ├── login_attempts (Rate-limiting brute force protection, bounded storage)
  ├── machines (Legacy compute nodes, SSH connection metadata)
  ├── models (Inference models, VRAM/RAM allocations, port bindings)
  ├── tasks (Legacy execution tasks, state, parameters, results)
  └── audit_log (Immutable security and operational event ledger)

M1 (Knowledge Vault Integration)
  ├── knowledge_notes (Vault notes metadata, paths, tags, hashes)
  └── knowledge_fts (Virtual FTS5 full-text table for instant keyword search)

M2 (Projects, Paths, Deliverables & Gates)
  ├── projects (Project objectives, priority, status, completion timestamps)
  ├── paths (Sequential execution paths, target outcomes)
  ├── stages (Ordered checkpoints within paths, gating prerequisites)
  ├── project_tasks (Granular work items linked to paths and stages)
  ├── deliverables (Artifact records, SHA-256 checksums, storage paths, status)
  ├── approval_gates (Human-in-the-loop review barriers and approvals)
  └── project_events (Audit event stream tracking project state transitions)

M3 (Metrics, Forecasts & Calibration)
  ├── metric_definitions (Unit definitions, directions, target thresholds)
  ├── metric_readings (Actual observed measurements tied to tasks and deliverables)
  └── metric_forecasts (Predicted performance metrics from AI planning stages)

M4 (Proposals & AI Planning Governance)
  ├── proposals (Proposed plans, changes, or deployments submitted by AI)
  ├── proposal_revisions (Versioned changes to proposed plans)
  └── proposal_reviews (Human reviewer feedback, approvals, and rejections)

M5 (Historical Learning & Retrospectives)
  ├── retrospectives (Project post-mortem reviews, lessons learned, calibrations)
  └── calibration_runs (Forecast accuracy evaluations comparing estimates to actuals)

M6 (Infrastructure Self-Awareness & Capability Registry)
  ├── infrastructure_nodes (Live hardware specs, OS, CPU, RAM, disk, services, runtimes)
  ├── infrastructure_capabilities (15 typed categories, Declared/Discovered/Verified status)
  ├── infrastructure_reconciliations (Architecture drift detection, severity, resolution audit)
  └── infrastructure_verifications (Audit trail for side-effect-free capability probes)

M7 (System Observatory & Real-Time Telemetry)
  ├── observatory_snapshots (Cluster health state snapshots across all nodes)
  ├── observatory_events (Telemetry events: latency, errors, queue depths, memory)
  ├── observatory_alerts (Enforceable alert thresholds, active/resolved statuses)
  └── observatory_baselines (Statistical baseline metrics for anomaly detection)

M8 (Web Research Engine & Evidence Citations)
  ├── research_runs (Research cycles linked to projects and tasks)
  ├── research_queries (Audit trail of external queries, execution time, providers)
  ├── research_search_results (Ranked search engine results from DuckDuckGo/SearXNG/Custom)
  ├── research_sources (Persistent source library, canonical URLs, full extracted text)
  ├── research_claims (Extracted factual claims linked to source passages)
  ├── research_evidence (Corroborated evidence links with confidence scoring)
  ├── research_citations (Deterministic inline citation audits mapping claims to sources)
  ├── research_provider_status (Provider health, consecutive failures, rate-limiting)
  └── research_budgets (Enforceable query, document, token, and browser time limits)

M9 (Autonomous Project Orchestrator)
  ├── orchestration_runs (Run state machine: DRAFT → PLANNING → RUNNING → COMPLETED)
  ├── orchestration_plan_revisions (Immutable plan revisions, validation results, JSON)
  ├── orchestration_tasks (DAG tasks, status, attempts, assigned node, action specs)
  ├── orchestration_task_deps (Explicit topological dependencies between tasks)
  ├── orchestration_attempts (Execution attempt history, runtimes, outputs, error classes)
  ├── orchestration_approvals (Human-in-the-loop review requests: plan, gate, failure)
  ├── orchestration_events (Audit event stream tracking orchestrator transitions)
  ├── orchestration_checkpoints (Resumption checkpoints for multi-phase workflows)
  └── orchestration_scheduler_lock (Concurrency mutex ensuring single-engine scheduler)

M10 (Unified Deliverables & Artifact System)
  ├── artifacts (Universal asset catalog: project, deliverable, source task, mime type)
  ├── artifact_versions (Immutable versions, SHA-256 CAS storage path, byte size, author)
  ├── artifact_lineage (Upstream/downstream DAG lineage tracking asset provenance)
  ├── artifact_task_links (Bidirectional task input/output association)
  ├── artifact_reviews (Human and automated quality validations, status, notes)
  ├── artifact_access_audit (Access, download, preview, and reuse logging)
  └── artifact_search_fts (Virtual FTS5 full-text table for content/metadata search)
```

---

## 3. Subsystem Deep Dives

### 3.1 Autonomous Project Orchestrator Engine (`app/services/orchestration/`)
The orchestrator drives complex, multi-stage projects autonomously while preserving human governance:
- **Intake & Gap Analysis**: Analyzes user project requests, separates discoverable facts from assumptions, and prompts for clarification if critical info is missing.
- **Dynamic Task Graph**: Compiles plans conforming to `qc.plan.v1` schema into a directed acyclic graph (DAG). Enforces cycle prevention, node pinning, library script registration, and input validation.
- **Bounded Model Repair**: Models generate plans with `PLANNER_MAX_TOKENS = 2048`. Truncated JSON streams are automatically repaired via bracket/quote balancing (`try_repair_truncated_json`), and schema violations are healed with bounded repair iterations.
- **Controller-Step vs Worker Execution**: Deterministic workflows (research fetching, browser extraction, evidence synthesis, report assembly) run on the secure controller. Distributed computation (Python scripts, LLM reasoning) is dispatched to verified workers.
- **State Machine**: Strictly audited transitions (`DRAFT` → `ANALYZING` → `PLANNING` → `AWAITING_PLAN_APPROVAL` → `READY` → `RUNNING` → `EVALUATING` → `COMPLETED` / `BLOCKED` / `PAUSED`).

### 3.2 Continuous Web Research Engine (`app/services/research/`)
Allows autonomous projects to gather real-world intelligence with strict verifiability:
- **Multi-Provider Search**: Pluggable search backend (DuckDuckGo HTML, SearXNG, Custom Search API) with health tracking and automatic failover.
- **Secure Retrieval & Extraction**: Validates external URLs against SSRF/loopback blocklists, enforces size limits, and sanitizes HTML. Detects Single Page Applications (SPAs) and triggers headless Chromium fallback when needed.
- **Deterministic Evidence Ledger**: Extracts claims, computes passage similarity, assigns confidence scores, and audits citations so every claim in a generated deliverable links to an immutable source record.
- **Resource Budgets**: Hard limits on query count, document count, token usage, and headless browser runtime prevent runaway costs or resource exhaustion.

### 3.3 Unified Deliverables & Artifact System (`app/services/artifacts/`)
Transforms transient outputs into durable, reusable project assets:
- **Content-Addressable Storage (CAS)**: Files are stored at `data/artifacts/{sha256[:2]}/{sha256}` with strict hash verification. Identical files are deduplicated automatically.
- **Dual-Storage Engine**: Metadata, lineage, and lifecycle status are maintained in SQLite; raw content is stored as immutable binary blobs on disk.
- **Full-Text Search (FTS5)**: Extracted text, markdown, and code are indexed in `artifact_search_fts` for instant BM25 search across all project deliverables.
- **Lineage & Cross-Project Reuse**: Deliverables from previous projects or earlier tasks can be transferred as inputs to new projects with complete provenance history.
- **Knowledge Promotion**: High-value deliverables can be promoted directly to the Obsidian-compatible Knowledge Vault with one click, tracking knowledge impact across future plans.

### 3.4 Infrastructure Self-Awareness & Capability Registry (`app/services/infrastructure/`)
Ensures AI agents plan tasks only on verified, capable hardware:
- **Three-Tier Capability Separation**: `Declared` (admin intent) → `Discovered` (read-only inspection) → `Verified` (side-effect-free test probes).
- **15 Typed Categories**: `ai_inference`, `web_retrieval`, `search`, `browser_automation`, `python_execution`, `approved_shell`, `file_processing`, `document_extraction`, `data_transformation`, `api_integrations`, `scheduled_execution`, `persistent_services`, `monitoring`, `software_deployment`, and `reporting`.
- **Token-Budgeted Context Packets**: Dynamically synthesizes live infrastructure facts into token-budgeted prompt packets (500 to 4,000 tokens) with priority pruning so small local models receive exact hardware state without context overflow.

### 3.5 VPS Worker Protocol (`vps_agent/`)
- **Outbound Pull-Based Model**: The cloud VPS worker connects inbound to the controller over private Tailscale WireGuard. No open ports on the controller.
- **Durable Task Leasing**: Atomic lease claims (`status = 'leased'`, `lease_expires_at = now + 300s`) with heartbeat renewals and idempotency keys.
- **Local Disconnect Buffering**: If network connectivity drops, the VPS worker completes active tasks and buffers outputs in a local SQLite database (`vps_agent.db`) until connection is restored.

---

## 4. Security & Zero-Trust Enforcements

| Defense Layer | Implementation Detail |
|---|---|
| **Identity & Authentication** | Per-node Bearer tokens, bcrypt-hashed passwords, CSRF tokens on mutating routes. |
| **Network Isolation** | Controller binds to loopback; remote nodes connect via private Tailscale WireGuard mesh. |
| **Sandbox Execution** | VPS agent executes under unprivileged `qwen-worker` user; no `sudo`; Docker socket forbidden. |
| **SSRF Prevention** | External fetchers block `127.0.0.0/8`, `10.0.0.0/8`, `192.168.0.0/16`, `169.254.0.0/16`, and local hostnames. |
| **Prompt Injection Protection** | Untrusted user data, search results, and extracted documents are wrapped in boundary markers (`<<<OBJECTIVE...OBJECTIVE>>>`) and scanned for privileged instruction injections. |
| **Secret Scrubbing** | Passwords, API tokens, and SSH keys are automatically redacted from prompts, logs, and knowledge notes. |
| **Brute-Force Rate Limiting**| SQLite-backed `login_attempts` tracker locks out client IP and username after 5 failed attempts. |

---

## 5. Technology Stack Summary

- **Backend**: Python 3.12+, FastAPI, Uvicorn, SQLite 3 (WAL + FTS5), Pydantic v2
- **Frontend**: Server-rendered Jinja2 templates, HTMX for reactive interactions, Vanilla CSS (no heavy JS framework overhead)
- **Host Wrapper**: WinSW (Windows Service Wrapper) for Windows background controller service
- **Networking**: Tailscale WireGuard mesh with TLS
- **Inference Runtimes**: Ollama, llama.cpp / llama-server, Quantized Qwen2.5 models
