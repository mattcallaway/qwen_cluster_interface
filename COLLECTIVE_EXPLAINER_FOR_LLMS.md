# Qwen Collective: Deep Technical Specification & Architecture Explainer for LLMs

> **Audience**: Autonomous AI agents, Large Language Models (LLMs), runtime evaluators, and distributed systems engineers.  
> **Purpose**: This document provides an exhaustive, low-level architectural, algorithmic, and programmatic specification of Qwen Collective. Any LLM ingesting this document should understand the exact execution flow, database schemas, API contracts, concurrency models, dependency resolution algorithms, and security boundaries that govern the collective.

---

## 1. High-Level Mental Model & Architectural Invariants

Qwen Collective is a **sovereign, distributed AI orchestration operating system**. It organizes heterogeneous compute nodes into an authoritative coordination topology without reliance on centralized public clouds.

### 1.1 The Sovereign Heterogeneous Computing Hierarchy

```text
+===================================================================================================+
|                                    LAYER 1: CENTRAL CONTROLLER                                    |
|                               (Windows 11 Host, Port 8000/8010)                                   |
|                                                                                                   |
|  - Engine Runtime: Python 3.12+, FastAPI, Uvicorn under WinSW Windows Service Wrapper.            |
|  - Authoritative State: Single-writer SQLite 3 engine in WAL mode with FTS5 search engines.       |
|  - Storage Engine: Cryptographic Content-Addressable Storage (CAS) with SHA-256 deduplication.    |
|  - Authoritative Core Services:                                                                   |
|      * Project Orchestrator Engine (`app/services/orchestration/engine.py`)                       |
|      * Dynamic DAG Compiler & Plan Validator (`app/services/orchestration/task_graph.py`)         |
|      * Bounded Plan Generator & JSON Repair FSM (`app/services/orchestration/planner.py`)         |
|      * Unified Artifact Manager (`app/services/artifacts/manager.py`)                             |
|      * Continuous Web Research & Citation Auditor (`app/services/research/manager.py`)            |
|      * Infrastructure Capability Registry & Probes (`app/services/infrastructure/`)               |
|      * Cluster System Observatory & Telemetry (`app/services/observatory/`)                       |
|      * Obsidian-Compatible Dual-Store Knowledge Vault (`app/services/knowledge_store.py`)         |
+===================================================================================================+
                                         ||                                ||
       Tailscale Private WireGuard Mesh  ||                                || Tailscale Private WireGuard Mesh
         (Outbound Bearer / OpenSSH)     ||                                || (Outbound Worker Pull Protocol)
                                         \/                                \/
+-------------------------------------------------+  +----------------------------------------------+
|        LAYER 2: LOCAL INFERENCE CLUSTER         |  |         LAYER 3: REMOTE OPERATIONAL NODE     |
|          (Linux Laptops / Workstations)         |  |                   (Cloud VPS)                |
|                                                 |  |                                              |
| - Nodes: `qwen-laptop`, `desktop-worker-01`     |  | - Node: `qwen-vps`                           |
| - Engines: `llama-server`, Ollama RPC           |  | - Agent: Standalone Python 3.12 daemon       |
| - Models: Quantized Qwen2.5 (0.5B, 1.5B, 7B)    |  |   managed by systemd (`qwen-vps.service`)    |
| - Capabilities: `ai_inference`, text analysis,  |  | - Capabilities: `python_execution`,          |
|   proposal drafting, and synthetic reasoning.   |  |   `browser_automation`, `scheduled_execution`|
| - Access: Controller initiates scoped inference |  | - Access: VPS agent pulls leases via HTTPS;  |
|   POST requests or executes read-only probes.   |  |   controller exposes ZERO inbound ports.     |
+-------------------------------------------------+  +----------------------------------------------+
```

### 1.2 System Invariants
1. **Single Source of Truth**: The Windows Controller's SQLite database (`qwen_collective.db`) is the only authoritative state store. Worker nodes maintain only transient disconnect buffers.
2. **Inference Independence**: Operational execution (Python pipelines, scheduled tasks, health monitoring, web fetching) never blocks on AI model availability and can run autonomously without active inference.
3. **No Unbounded Autonomy**: Every multi-task project DAG requires human approval (`AWAITING_PLAN_APPROVAL`) before moving into the execution scheduler.
4. **Idempotent At-Least-Once Execution**: Task leasing uses cryptographically distinct idempotency keys with lease timeout reclamation and duplicate submission guards.
5. **Universal Artifact Lineage**: All non-trivial execution outputs are hashed (SHA-256), persisted in Content-Addressable Storage (CAS), and linked to their producing task, stage, project, worker node, and model.

---

## 2. Subsystem Interoperability Matrix

The following matrix documents the exact technical interfaces between core subsystems:

```text
+-----------------------+-----------------------+-----------------------------------------------------------------+
| Calling Subsystem     | Target Subsystem      | Interface & Method of Interaction                               |
+=======================+=======================+=================================================================+
| Project Orchestrator  | Infrastructure Reg.   | `model_router.node_inventory()` queries verified node inventory |
|                       |                       | to filter worker eligibility during DAG compilation.            |
+-----------------------+-----------------------+-----------------------------------------------------------------+
| Project Orchestrator  | Knowledge Vault       | `search_knowledge_records()` extracts historical notes to inject|
|                       |                       | into planning assumptions; records usage impact.                |
+-----------------------+-----------------------+-----------------------------------------------------------------+
| Project Orchestrator  | Inference Cluster     | `ModelRouter.get_provider().submit()` enqueues async planning or|
|                       |                       | task inference requests via HTTP to Ollama/llama-server.        |
+-----------------------+-----------------------+-----------------------------------------------------------------+
| Project Orchestrator  | Remote VPS Agent      | Outbound pull: VPS polls `/api/v1/worker/tasks/lease`, locks    |
|                       |                       | tasks atomically, submits outputs to `/tasks/{id}/submit`.      |
+-----------------------+-----------------------+-----------------------------------------------------------------+
| Project Orchestrator  | Web Research Engine   | Dispatches deterministic `controller_step` actions:             |
|                       |                       | `research_search`, `research_fetch`, `research_verify`, etc.    |
+-----------------------+-----------------------+-----------------------------------------------------------------+
| Project Orchestrator  | Artifact CAS Manager  | `artifact_manager.write_controller_artifact()` writes CAS blobs,|
|                       |                       | registers versions, updates lineage, and triggers FTS5 indexing.|
+-----------------------+-----------------------+-----------------------------------------------------------------+
| Web Research Engine   | Artifact CAS Manager  | Persists search outputs, extracted source bodies, evidence      |
|                       |                       | ledgers, and citation audit reports as project artifacts.       |
+-----------------------+-----------------------+-----------------------------------------------------------------+
| Artifact CAS Manager  | Knowledge Vault       | `promote_artifact_to_knowledge()` writes Markdown files to the  |
|                       |                       | Obsidian vault and creates FTS5 index records.                  |
+-----------------------+-----------------------+-----------------------------------------------------------------+
| All Subsystems        | System Observatory    | Emits telemetry events to `observatory_events` for real-time    |
|                       |                       | metric aggregation, baseline comparisons, and alert triggers.   |
+-----------------------+-----------------------+-----------------------------------------------------------------+
```

---

## 3. Autonomous Project Orchestrator Specification

The orchestrator (`app/services/orchestration/`) is a deterministic, finite state machine and dynamic DAG execution engine.

### 3.1 Orchestration State Machine (`states.py`)

A run is governed by an audited state transition table with optimistic concurrency verification:

```text
                [ DRAFT ] ──(intake submit)──> [ ANALYZING ]
                                                    │
                   ┌────────────────────────────────┴────────────────┐
                   ▼                                                 ▼
        [ AWAITING_CLARIFICATION ]                           [ PLANNING ]
                   │                                                 │
                   └───────────(user clarifies)──────────────────────┤
                                                                     ▼
                                                        [ AWAITING_PLAN_APPROVAL ]
                                                                     │
                                                       (human approves plan)
                                                                     ▼
                                                                 [ READY ]
                                                                     │
                                                           (scheduler dispatches)
                                                                     ▼
                                                                [ RUNNING ] ◄──┐
                                                                     │         │ (next task)
                                                                     ▼         │
                                                               [ EVALUATING ] ─┘
                                                                     │
                                                       (all deliverables verified)
                                                                     ▼
                                                               [ COMPLETED ]
```

#### Transition Validations (`states.py:TRANSITIONS`)
```python
TRANSITIONS: Dict[str, Set[str]] = {
    "DRAFT": {"ANALYZING", "PLANNING", "CANCELLED"},
    "ANALYZING": {"AWAITING_CLARIFICATION", "PLANNING", "DRAFT", "CANCELLED"},
    "AWAITING_CLARIFICATION": {"ANALYZING", "PLANNING", "DRAFT", "CANCELLED"},
    "PLANNING": {"AWAITING_PLAN_APPROVAL", "DRAFT", "FAILED", "CANCELLED"},
    "AWAITING_PLAN_APPROVAL": {"READY", "PLANNING", "DRAFT", "CANCELLED"},
    "READY": {"RUNNING", "PLANNING", "PAUSED", "CANCELLED"},
    "RUNNING": {"EVALUATING", "PAUSED", "BLOCKED", "REPLANNING", "FAILED", "CANCELLED"},
    "EVALUATING": {"COMPLETED", "RUNNING", "BLOCKED", "REPLANNING", "FAILED", "CANCELLED"},
    "PAUSED": {"RUNNING", "READY", "CANCELLED"},
    "BLOCKED": {"RUNNING", "REPLANNING", "FAILED", "CANCELLED"},
    "REPLANNING": {"AWAITING_PLAN_APPROVAL", "RUNNING", "BLOCKED", "FAILED", "CANCELLED"},
    "FAILED": {"REPLANNING", "PLANNING", "CANCELLED"},
    "COMPLETED": set(),
    "CANCELLED": set(),
}
```

### 3.2 Dynamic Plan Schema (`qc.plan.v1`)

Plans generated by LLMs or deterministic templates must conform to the following JSON schema:

```json
{
  "schema": "qc.plan.v1",
  "overview": {
    "objective": "High-level goal description",
    "scope": "In-scope and out-of-scope boundaries",
    "assumptions": ["List of validated assumptions"],
    "constraints": ["Security, execution, or hardware constraints"],
    "expected_outcome": "Measurable end-state description"
  },
  "paths": [
    {
      "key": "main",
      "name": "Main Execution Path",
      "rationale": "Direct execution sequence",
      "stages": [
        {"key": "s1", "name": "Stage 1", "objective": "Stage 1 goal"}
      ]
    }
  ],
  "tasks": [
    {
      "key": "t1",
      "title": "Task 1 Name",
      "description": "Granular execution instructions",
      "stage": "s1",
      "agent_role": "execution | analysis | integration | research",
      "depends_on": [],
      "allowed_nodes": ["qwen-vps", "qwen-laptop"],
      "action": {
        "type": "library_script | inference | controller_step",
        "script_id": "sys_health_basic",
        "instruction": "Prompt text for inference tasks",
        "step": "research_search | research_fetch | research_verify | assemble_report",
        "inputs": ["t0"],
        "max_tokens": 1000
      }
    }
  ],
  "deliverables": [
    {
      "key": "d1",
      "name": "Deliverable Name",
      "type": "report | code | dataset | document",
      "acceptance_criteria": "Deterministic acceptance rules",
      "produced_by": "t1",
      "stage": "s1",
      "terminal": true
    }
  ],
  "approval_gates": [
    {"action": "Execute plan", "reason": "Initial plan execution requires approval"}
  ],
  "risks": []
}
```

### 3.3 The Bounded Repair & Truncated JSON Parser

Local quantized models (such as `qwen2.5:1.5b`) can occasionally truncate responses when hitting token ceilings or output slight syntax imperfections. The planner implements a **two-tier recovery algorithm**:

#### Tier 1: Truncation Repair Finite State Machine (`planner.py:try_repair_truncated_json`)
1. Strips markdown fences (` ```json `).
2. Locates the initial root brace `{`.
3. If the outer brace does not balance, runs a reverse trim loop (up to 800 characters) removing trailing incomplete keys, unclosed quotes, and dangling colons (`:\s*"?$ -> : ""`).
4. Tracks string escape state (`\`) and counts unclosed container tokens (`{`, `[`).
5. Appends the exact sequence of balanced closing brackets (`]`, `}`).
6. Attempts `json.loads()`. If the recovered object contains top-level keys (`schema`, `paths`, `tasks`, `overview`), the plan is salvaged intact.

#### Tier 2: Bounded Schema Repair (`planner.py:_resubmit`)
1. If the extracted JSON fails `task_graph.validate_plan(norm, nodes)`, the exact validation error list (e.g. `Deliverable 'd4' produced by unknown task ''`) is formatted into a repair prompt.
2. The repair prompt is submitted with `repairs + 1`.
3. Bounded to `MAX_REPAIRS = 2`. If repair attempts are exhausted, the run cleanly enters `DRAFT` with an explicit diagnostic message.
4. `PLANNER_MAX_TOKENS = 2048` guarantees sufficient token budget for 4+ stages and deliverables.

### 3.4 Topological Task Scheduling Algorithm (`engine.py`)

The engine tick (`engine.tick()`) executes on a 2-second background timer. It prevents race conditions using an atomic SQLite mutex table (`orchestration_scheduler_lock`).

```text
[Engine Tick]
      │
      ▼
Acquire Mutex in `orchestration_scheduler_lock`
      │
      ▼
Fetch all runs in `RUNNING` state
      │
      ▼
For each run:
  1. Build Task DAG from `orchestration_tasks` and `orchestration_task_deps`.
  2. Compute topological order using Kahn's algorithm:
     - In-degree 0: Tasks with status='pending' and all dependencies in status='completed'.
  3. Filter eligible tasks:
     - If action['type'] == 'controller_step':
         -> Execute immediately on controller in a thread pool worker.
         -> Evaluate output with `evaluator.evaluate_research_step()`.
         -> Write CAS artifact via `artifact_manager.write_controller_artifact()`.
         -> Mark task 'completed' and advance DAG.
     - If action['type'] == 'library_script' or 'inference':
         -> Mark task 'ready_for_lease'.
         -> Available for worker pull leasing via `/api/v1/worker/tasks/lease`.
  4. If all tasks completed:
     -> Transition run: `RUNNING` -> `EVALUATING` -> `COMPLETED`.
```

---

## 4. Unified Deliverables & Artifact System Specification

The Artifact System (`app/services/artifacts/`) establishes a permanent, immutable record for every meaningful piece of computation.

### 4.1 Content-Addressable Storage (CAS) Architecture

```text
Generated Content (Bytes)
           │
           ▼
Compute SHA-256 Checksum: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
           │
           ▼
Check if File Exists: `data/artifacts/e3/e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
           │
     ┌─────┴─────────────────────────────────┐
     ▼ (File already exists)                 ▼ (New content)
Deduplicate automatically;              Atomically write bytes to temp file;
reuse existing storage path.            Rename to destination CAS path (atomic on POSIX/Win32).
```

### 4.2 Relational Metadata & Lineage Schema (M10 Migration)

Every artifact is tracked through relational tables ensuring full provenance:

```sql
-- 1. Master Artifact Record
CREATE TABLE artifacts (
    id TEXT PRIMARY KEY,                       -- art-xxxxxxxxxxxxxxxx
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    deliverable_id TEXT REFERENCES deliverables(id) ON DELETE SET NULL,
    current_version_id TEXT,                   -- points to active artifact_versions row
    name TEXT NOT NULL,                        -- e.g. "research_report.md"
    artifact_type TEXT NOT NULL,               -- report | code | dataset | document
    mime_type TEXT NOT NULL,                   -- text/markdown, application/json, etc.
    storage_kind TEXT NOT NULL DEFAULT 'cas',  -- cas | db_blob | inline
    is_deliverable INTEGER NOT NULL DEFAULT 0, -- 1 if formal project deliverable
    created_at TEXT NOT NULL
);

-- 2. Immutable Version Records
CREATE TABLE artifact_versions (
    id TEXT PRIMARY KEY,                       -- arv-xxxxxxxxxxxxxxxx
    artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,           -- 1, 2, 3...
    sha256 TEXT NOT NULL,                      -- Cryptographic content hash
    storage_path TEXT NOT NULL,                -- data/artifacts/xx/xxxxxxxx...
    byte_size INTEGER NOT NULL,                -- Exact byte length
    source_task_id TEXT,                       -- Producing orchestration_tasks ID
    source_run_id TEXT,                        -- Producing orchestration_runs ID
    node_id TEXT,                              -- Worker node that produced the asset
    model_name TEXT,                           -- Model that authored the output
    created_at TEXT NOT NULL
);

-- 3. DAG Lineage Association
CREATE TABLE artifact_lineage (
    id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    version_id TEXT NOT NULL REFERENCES artifact_versions(id) ON DELETE CASCADE,
    parent_artifact_id TEXT REFERENCES artifacts(id) ON DELETE CASCADE,
    parent_version_id TEXT REFERENCES artifact_versions(id) ON DELETE CASCADE,
    edge_type TEXT NOT NULL DEFAULT 'derived_from', -- derived_from | input_to | compiled_into
    created_at TEXT NOT NULL
);

-- 4. Full-Text Search Engine
CREATE VIRTUAL TABLE artifact_search_fts USING fts5(
    artifact_id UNINDEXED,
    version_id UNINDEXED,
    name,
    content_text,
    summary,
    tokenize = 'porter unicode61'
);
```

### 4.3 Deliverable Cross-Project Reuse Protocol

When a user or agent transfers a deliverable to a new project:
1. `artifact_manager.transfer_artifact_to_project(artifact_id, target_project_id, actor)` is invoked.
2. The CAS storage path (`data/artifacts/xx/...`) is referenced without copying the underlying disk file.
3. A new `artifacts` row is registered in the target project pointing to the same CAS version hash.
4. An entry in `artifact_lineage` is created with `edge_type = 'transferred_to'` linking the original project's artifact version to the new project artifact.
5. An audit event is logged in `artifact_access_audit` recording the actor and timestamp.

---

## 5. Web Research & Citation Audit Engine Specification

The Research Engine (`app/services/research/`) gives the collective external sensory perception with strict verification boundaries.

```text
[Research Step Execution]
         │
         ▼
1. Query Generation & Search Dispatch
   - Multi-provider queries (DuckDuckGo, SearXNG, Custom API).
   - Logged in `research_queries` and ranked in `research_search_results`.
         │
         ▼
2. SSRF-Hardened Document Fetcher
   - Resolves DNS; checks IP against `SSRF_BLACKLIST` (`127.0.0.0/8`, `10.0.0.0/8`, `192.168.0.0/16`, `169.254.0.0/16`).
   - Fetches with strict 10MB streaming limit and 15s timeout.
   - Extracts structured text and metadata via BeautifulSoup4 / Trafilatura.
         │
         ▼
3. Headless Browser Fallback (Playwright / Chromium)
   - Triggered if HTTP fetch returns 403, Cloudflare challenge, or `<div id="app"></div>` SPA placeholder.
   - Renders page in headless sandbox; charges duration to `research_budgets.browser_seconds_used`.
         │
         ▼
4. Claim-Level Fact Extraction & Verification
   - Splits document into paragraphs and sentences.
   - Extracts atomic claims: `research_claims`.
   - Links supporting passages with similarity scoring: `research_evidence`.
         │
         ▼
5. Deterministic Synthesis & Citation Audit
   - Assembles Markdown deliverable: `research_report.md`.
   - Audits inline citations: `[1]`, `[2]` against registered sources in `research_sources`.
   - Writes `retrieved_sources.json`, `evidence_ledger.json`, and `citation_audit.json` as CAS artifacts.
```

---

## 6. Remote Worker Execution Protocol (VPS Agent)

The remote execution worker runs on the cloud VPS (`vps_agent/`) as a systemd service (`qwen-vps.service`).

### 6.1 The Outbound Pull & Leasing Protocol

The controller does **not** connect inbound to the VPS. The VPS agent maintains an outbound HTTPS connection over Tailscale:

```text
VPS Worker Agent                                         Windows Controller
      │                                                          │
      │── 1. POST /api/v1/worker/register (Bearer Token) ───────>│ (Validates token, updates `infrastructure_nodes`)
      │                                                          │
      │── 2. POST /api/v1/worker/heartbeat ─────────────────────>│ (Refreshes node health timestamp in DB)
      │                                                          │
      │── 3. POST /api/v1/worker/tasks/lease ───────────────────>│ (Atomic SQLite transaction:
      │                                                          │  SELECT task WHERE status='ready_for_lease'
      │                                                          │  AND allowed_nodes IN ('qwen-vps', '')
      │                                                          │  UPDATE status='leased', lease_expires=now+300s)
      │<─ 4. 200 OK: Task Payload + Action Spec + Limits ────────│
      │                                                          │
  [Executes under unprivileged Linux user `qwen-worker`]         │
      │                                                          │
      │── 5. POST /api/v1/worker/tasks/{id}/heartbeat ──────────>│ (Extends lease expiration by 300s)
      │                                                          │
      │── 6. POST /api/v1/worker/tasks/{id}/submit ─────────────>│ (Stores output, writes CAS artifacts,
      │      {status: "completed", output: {...}}                │  marks task 'completed', advances DAG)
      │<─ 7. 200 OK (Task Closed) ───────────────────────────────│
```

### 6.2 Offline Disconnect Buffering
If network connectivity between the VPS and Controller drops:
1. The VPS agent continues executing the leased task locally.
2. Upon completion, the task result is persisted to a local SQLite database (`vps_agent.db`).
3. An exponential backoff background thread continuously probes the controller's `/api/v1/worker/heartbeat`.
4. Once connection is restored, all buffered results are flushed in chronological order with their original execution timestamps.

---

## 7. Security Hardening & Zero-Trust Architecture

Qwen Collective enforces strict defense-in-depth across the entire execution surface:

| Vector | Defensive Implementation | File Reference |
|---|---|---|
| **Prompt Injection** | Untrusted inputs (user prompts, web documents, external search snippets) are wrapped in `<<<OBJECTIVE...OBJECTIVE>>>` boundaries. AST syntax compilation scans input text for privileged command override markers. | `app/services/orchestration/task_graph.py` |
| **Network Egress (SSRF)** | External fetcher validates destination IP before establishing sockets; drops loopback (`127.0.0.1`), private RFC1918 subnets, and AWS/GCP cloud metadata addresses (`169.254.169.254`). | `app/services/research/fetcher.py` |
| **Worker Sandboxing** | VPS background agent runs under the dedicated, non-root system account `qwen-worker`. Sudo access is disabled, and socket connection to `/var/run/docker.sock` is blocked. | `vps_agent/agent.py` |
| **Credential Scrubbing** | Central secret scrubber scans all prompts, log messages, and generated artifact text; automatically masks API keys, bearer tokens, SSH private keys, and bcrypt hashes. | `app/services/orchestration/common.py` |
| **Brute-Force Protection**| SQLite-backed `login_attempts` table enforces dual IP and username lockout for 300 seconds after 5 failed authentication attempts. | `app/services/auth.py` |
| **Mutating Route Security**| All mutating HTTP routes require cryptographic CSRF validation tokens linked to authenticated user sessions. | `app/middleware.py` |

---

## 8. Prescriptive Rules for LLMs Operating within the Collective

When an LLM is paired with the collective as a **Planner**, **Worker Agent**, or **Evaluator**, the following behavioral contracts are mandatory:

1. **Strictly Structural JSON Responses**: When invoked for task decomposition or planning, output **only** a valid JSON object matching `qc.plan.v1`. Do not preface with conversational text (`"Here is the plan:"`) and do not append markdown prose.
2. **Never Hallucinate Worker Nodes or Capabilities**: You must only assign tasks to worker node IDs explicitly present in the provided Live Architecture Context (`qwen-vps`, `qwen-laptop`, `controller-local`). If no worker supports a capability, pin to `controller_step` or request approval.
3. **Prefer Deterministic Actions Over Inference**: When compiling data, checking citations, or aggregating metrics, specify `action: {"type": "controller_step", "step": "assemble_report"}` rather than open-ended LLM inference. Deterministic controller steps guarantee zero hallucination, instantaneous execution, and cryptographic verifiability.
4. **Always Define Explicit Dependencies**: Every task $t_n$ that relies on the output of $t_a$ must declare `depends_on: ["ta"]` and list `ta` in `action.inputs: ["ta"]`.
5. **Ensure Deliverables Have Producers**: Every entry in the `deliverables` array must specify a valid `produced_by: "<task_key>"` corresponding to an existing task in the `tasks` array.
