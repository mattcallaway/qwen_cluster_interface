# Qwen Collective: Technical Architecture Map (TECHMAP)

## 1. Architectural Philosophy & Roles

Qwen Collective is structured as a **hybrid, distributed coordination system** engineered to maximize sovereign hardware efficiency, prevent AI hallucinations about real-world infrastructure, and enforce strict human approval boundaries before execution.

```text
+-----------------------------------------------------------------------------------+
|                        LAYER 1: CENTRAL AUTHORITATIVE CONTROLLER                  |
|                        Windows Host (Local / Tailscale Mesh)                      |
|                                                                                   |
|  - Authoritative Database (SQLite WAL Mode, FTS5 Search)                          |
|  - Project & Execution Governance (Projects, Paths, Tasks, Deliverables, Gates)   |
|  - Infrastructure & Capability Registry (Hardware Inventory, Probe Verification)  |
|  - Durable Knowledge Vault (Obsidian Markdown Notes, Dual-Store Index)            |
|  - Human Approval Workflow & Audit Event Ledger                                   |
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
| - llama.cpp / llama-server         |           | - Sandboxed Python/Shell Jobs      |
| - Read-Only Diagnostic Probes (SSH)|           | - Systemd Services & Timers        |
| - Reasoning & Proposal Generation  |           | - Offline Disconnect Result Buffer |
+------------------------------------+           +------------------------------------+
                                         ^
                                         | (Ingests Context & Emits Findings)
+-----------------------------------------------------------------------------------+
|                        LAYER 4: PERSISTENT KNOWLEDGE ENGINE                       |
|  - Raw Markdown files in an Obsidian-compatible vault (100% human-readable)       |
|  - Dynamic SQLite FTS5 Full-Text search indexing with BM25 ranking                |
|  - Staged inbox for external research before human promotion into system memory   |
+-----------------------------------------------------------------------------------+
```

---

## 2. Database Schema & Migration Progression

The authoritative controller database uses **SQLite 3 in WAL (Write-Ahead Logging) mode** with busy timeouts and foreign key constraints enabled. Schema evolution is managed via sequential, transaction-safe migrations in `app/migrations.py`:

```text
M0 (Baseline)
  ├── users (Administrator accounts, bcrypt password hashes, last login)
  ├── sessions (Signed cookie tokens, expiration timestamps)
  ├── login_attempts (Rate-limiting brute force protection, bounded storage)
  ├── machines (Legacy compute nodes, SSH connection metadata)
  ├── models (Inference models, VRAM/RAM allocations, port bindings)
  ├── tasks (Execution tasks, state, parameters, results)
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
```

---

## 3. Infrastructure Self-Awareness & Capability Architecture

### 3.1 Three-Tier Capability Separation
To prevent AI models from hallucinating capabilities or assuming software availability equals execution authorization, capabilities are separated into three distinct lifecycle tiers:

1. **Declared**: What configuration or administrators state a node *should* support.
2. **Discovered**: Software runtimes, packages, and system utilities verified by read-only inspection.
3. **Verified**: Operational capabilities proven ready through controlled, side-effect-free test probes.

```text
[ Declared ]  --(Read-Only Inspection)-->  [ Discovered ]  --(Side-Effect-Free Probe)-->  [ Verified ]
     |                                           |                                             |
     +-------------------------------------------+---------------------------------------------+
                                                 |
                                     (Architecture Drift Detected)
                                                 v
                                 [ Infrastructure Reconciliation ]
```

### 3.2 15 Structured Capability Categories
Capabilities are strictly categorized to enable deterministic worker eligibility matching:
1. `ai_inference`: Quantized LLM text generation and embeddings.
2. `web_retrieval`: HTTP fetching, documentation extraction, and link crawling.
3. `search`: Web search API queries and indexed search.
4. `browser_automation`: Headless browser execution (Playwright, Chromium).
5. `python_execution`: Controlled execution of approved Python scripts.
6. `approved_shell`: Pre-authorized POSIX or Windows shell commands.
7. `file_processing`: Parsing, transformation, and manipulation of local files.
8. `document_extraction`: PDF, DOCX, and Markdown parsing and indexing.
9. `data_transformation`: Structured JSON, CSV, and tabular data pipelines.
10. `api_integrations`: Outbound communication with external third-party APIs.
11. `scheduled_execution`: Systemd timer or cron-based recurring execution.
12. `persistent_services`: Background daemons managed by systemd or WinSW.
13. `monitoring`: System resource metrics, telemetry, and health reporting.
14. `software_deployment`: Installing, updating, or configuring authorized packages.
15. `reporting`: Markdown, HTML, or PDF deliverable synthesis.

---

## 4. VPS Worker Communication & Leasing Protocol

The VPS worker operates strictly in an **outbound pull-based model**. The controller never makes unauthenticated inbound calls to the VPS, and the VPS never has administrative access to the controller.

```text
VPS Worker Agent                                         Windows Controller
      |                                                           |
      |--- 1. POST /api/v1/worker/register (Bearer Auth) -------->| (Records node & capabilities)
      |                                                           |
      |--- 2. POST /api/v1/worker/heartbeat (Health & Usage) ---->| (Updates live health state)
      |                                                           |
      |--- 3. POST /api/v1/worker/tasks/lease ------------------->| (Locks pending approved task)
      |<-- 4. Leased Task Payload (Idempotency Key, Limits) ------|
      |                                                           |
      | [Executes in non-root sandbox]                            |
      |                                                           |
      |--- 5. POST /api/v1/worker/tasks/{id}/heartbeat ---------->| (Renews task lease)
      |                                                           |
      |--- 6. POST /api/v1/worker/tasks/{id}/submit ------------->| (Validates & saves deliverables)
      |<-- 7. 200 OK (Task Closed) -------------------------------|
```

### 4.1 Durable Leasing & Concurrency Rules
- **Atomic Lease Claims**: Tasks are leased using an atomic SQLite transaction (`status = 'leased'`, `lease_expires_at = now + 300s`).
- **Idempotency Keys**: Every task execution carries a unique UUID idempotency key to prevent double execution across retries.
- **Heartbeat Renewals**: Long-running jobs send heartbeats every 30 seconds; if 3 consecutive heartbeats are missed, the lease expires and the task is reclaimed.
- **Local Disconnect Buffering**: If the network link to the controller is interrupted, the VPS worker completes active tasks and buffers results in a local SQLite database (`vps_agent.db`) until connection is restored.

---

## 5. Security & Boundary Architecture

| Barrier | Defensive Mechanism |
|---|---|
| **Identity & Authentication** | Scoped Bearer tokens per worker machine; bcrypt-hashed administrator passwords; PBKDF2 CSRF tokens on all mutating HTTP routes. |
| **Network Isolation** | Controller binds to `127.0.0.1` by default; remote access is multiplexed via private Tailscale WireGuard mesh (`tailscale serve`); no open public ports. |
| **Worker Privileges** | VPS agent executes under dedicated unprivileged Linux user (`qwen-worker`); no `sudo` privileges; Docker socket access strictly forbidden. |
| **Prompt Injection Defense** | Retrieved web content, logs, and external deliverables are tagged as untrusted data; secret scrubbers redact keys and credentials before prompts are built. |
| **Brute-Force Lockout** | SQLite-backed `login_attempts` tracker locks out both the client IP and username after 5 failed attempts for 300 seconds. |
| **Side-Effect-Free Probes** | Probes use in-memory AST syntax compilation, local permission checks, and loopback socket tests without executing external code. |

---

## 6. Architecture Context Packet Hierarchy

For local Qwen models with finite context windows, the controller generates token-budgeted prompt packets (500 to 4,000 tokens) using a strict priority hierarchy:

1. **Header & Freshness**: Controller role, local timestamp, and verification cutoff.
2. **Controller Status**: Host OS, CPU, RAM, storage, and service state.
3. **Execution Workers**: List of active compute nodes and operational state.
4. **Verified Capabilities**: Capabilities proven ready via verification probes.
5. **Active Deployments**: Current running services and scheduled timers.
6. **Security Boundaries**: Enforced approval rules, file restrictions, and network egress rules.
7. **Operational Failures**: Recent error classifications and recovery attempts.
8. **Knowledge Excerpts**: Relevant architectural guidelines from the durable vault.

If a token budget is exceeded, lower-tier items are deterministically pruned and replaced with an explicit omission notice informing the model that additional details can be retrieved via typed tools.
