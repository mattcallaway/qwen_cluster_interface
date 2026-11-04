# Qwen Collective: System Interaction Patterns & Visual Execution Flows

> **Target Audience**: Systems architects, distributed systems engineers, and reasoning LLMs that trace multi-agent execution flows.  
> **Purpose**: This document provides end-to-end visual sequence diagrams and architectural flowcharts using Mermaid syntax. It maps out the exact message passing, database state mutations, and error recovery sequences across the heterogeneous cluster.

---

## 1. End-to-End Autonomous Project Lifecycle

The following sequence illustrates the entire journey of a project—from raw user prompt to verified deliverables stored in Content-Addressable Storage (CAS).

```mermaid
sequenceDiagram
    autonumber
    actor User as Human Operator / Client
    participant UI as Web Dashboard / API
    participant Orch as Project Orchestrator
    participant State as State Machine (states.py)
    participant Model as Local Inference Cluster (Qwen)
    participant Repair as Bounded Repair FSM
    participant Sched as Task Scheduler (engine.py)
    participant CAS as Artifact CAS Manager
    participant DB as SQLite Authoritative Store

    User->>UI: Submit Project Goal & Constraints
    UI->>Orch: POST /api/v1/orchestrator/runs (Intake JSON)
    Orch->>State: transition(DRAFT -> ANALYZING)
    Orch->>Orch: Run analyze_intake() (Identify assumptions vs gaps)
    
    alt Critical Missing Information
        Orch->>State: transition(ANALYZING -> AWAITING_CLARIFICATION)
        UI-->>User: Prompt for Required Clarification
        User->>UI: Submit Clarification
    end

    Orch->>State: transition(ANALYZING -> PLANNING)
    Orch->>Model: Submit Planning Prompt (Objective + Hardware Context + Knowledge)
    Model-->>Orch: Raw Plan Stream (qc.plan.v1 JSON)
    
    opt Stream Truncated at Token Limit
        Orch->>Repair: try_repair_truncated_json()
        Repair-->>Orch: Balanced Salvaged JSON Object
    end

    Orch->>Orch: validate_plan(plan, nodes)
    
    opt Schema / Node / Script Violations
        loop Max 2 Repair Cycles
            Orch->>Model: Resubmit with Error List
            Model-->>Orch: Corrected Plan JSON
            Orch->>Orch: validate_plan()
        end
    end

    Orch->>DB: store_revision(status='proposed')
    Orch->>State: transition(PLANNING -> AWAITING_PLAN_APPROVAL)
    UI-->>User: Display Task DAG, Estimated Runtimes, Deliverables
    User->>UI: Click "Approve Plan"
    UI->>State: transition(AWAITING_PLAN_APPROVAL -> READY)
    State->>State: transition(READY -> RUNNING)

    loop Scheduler Engine Tick (Every 2s)
        Sched->>DB: Acquire Mutex in orchestration_scheduler_lock
        Sched->>Sched: Topological Sort (Kahn's Algorithm on Ready Tasks)
        
        alt Task is controller_step
            Sched->>Sched: Execute in Controller Thread Pool
        else Task is worker action
            Sched->>DB: Mark status='ready_for_lease'
        end
    end

    Sched->>CAS: Hash Output (SHA-256) & Write CAS Blob
    CAS->>DB: Insert artifacts, artifact_versions, artifact_lineage, FTS5
    Sched->>State: transition(RUNNING -> EVALUATING -> COMPLETED)
    UI-->>User: Display Project Output with Preview, Download & Lineage
```

---

## 2. Distributed Worker Task Leasing & Disconnect Resiliency

The cloud VPS companion worker operates strictly in an outbound pull model with local buffering to survive network partitions over Tailscale.

```mermaid
sequenceDiagram
    autonumber
    participant Worker as VPS Worker Agent (qwen-vps)
    participant LocalDB as Local Buffer (vps_agent.db)
    participant Net as Tailscale WireGuard Mesh
    participant Controller as Windows Controller API
    participant Sched as Orchestrator Scheduler
    participant CAS as Artifact CAS Manager

    loop Every 10 Seconds
        Worker->>Net: POST /api/v1/worker/heartbeat
        Net->>Controller: Heartbeat Payload (CPU, RAM, Disk)
        Controller-->>Worker: 200 OK (Acknowledge Alive)
    end

    Worker->>Net: POST /api/v1/worker/tasks/lease
    Net->>Controller: Lease Request (Node Capabilities: python_execution)
    Controller->>Controller: BEGIN IMMEDIATE TRANSACTION
    Controller->>Controller: SELECT task WHERE status='ready_for_lease' AND allowed_nodes CONTAINS 'qwen-vps'
    Controller->>Controller: UPDATE status='leased', lease_expires_at=now()+300s, idempotency_key=UUID()
    Controller->>Controller: COMMIT
    Controller-->>Worker: 200 OK (Task Payload, Parameters, Idempotency Key)

    Worker->>Worker: Spawn Subprocess (Sandbox User `qwen-worker`)
    
    loop Every 30 Seconds during Execution
        Worker->>Controller: POST /api/v1/worker/tasks/{id}/heartbeat
        Controller-->>Worker: 200 OK (Lease Extended +300s)
    end

    alt Network Connection Dropped
        Worker->>Worker: Complete Sandboxed Execution
        Worker->>LocalDB: INSERT INTO buffered_results (task_id, status, output, completed_at)
        loop Exponential Backoff Probe
            Worker->>Net: Probe Controller Heartbeat
            Net--xController: Connection Refused / Timeout
        end
        Note over Worker,Controller: Network Connection Restored
        Worker->>Controller: POST /api/v1/worker/heartbeat (Success)
        Worker->>LocalDB: SELECT * FROM buffered_results ORDER BY completed_at ASC
        Worker->>Controller: POST /api/v1/worker/tasks/{id}/submit (Flushed Result)
        LocalDB->>LocalDB: DELETE FROM buffered_results WHERE task_id={id}
    else Normal Execution
        Worker->>Controller: POST /api/v1/worker/tasks/{id}/submit (output_json)
    end

    Controller->>CAS: Store Task Deliverables in CAS
    Controller->>Controller: Mark Task 'completed'
    Controller-->>Worker: 200 OK (Task Closed)
```

---

## 3. Continuous Web Research, Evidence Extraction & Citation Pipeline

The continuous research engine executes on the controller to discover, verify, and cite real-world evidence without external hallucination.

```mermaid
flowchart TD
    A[Orchestrator Dispatches 'research_search'] --> B[Search Manager]
    B --> C{Select Provider}
    C -->|Priority 1| D[DuckDuckGo HTML Engine]
    C -->|Priority 2| E[SearXNG Meta-Search API]
    C -->|Priority 3| F[Custom Search REST API]
    
    D & E & F --> G[Rank & Filter Top 10 URLs]
    G --> H[Record in research_queries & research_search_results]
    
    H --> I[Orchestrator Dispatches 'research_fetch']
    I --> J[SSRF-Hardened HTTP Fetcher]
    
    J --> K{Evaluate Response}
    K -->|200 OK with Clean HTML| L[Trafilatura / BeautifulSoup4 Text Extraction]
    K -->|403 Barrier / SPA Detected| M[Headless Chromium Sandbox Fallback]
    
    M -->|Render DOM & Execute JS| L
    L --> N[Persist Source into research_sources Table]
    
    N --> O[Orchestrator Dispatches 'research_verify']
    O --> P[Claim Extraction: Split into Atomic Assertions]
    P --> Q[Passage Semantic Matching & TF-IDF Scoring]
    Q --> R[Populate research_claims & research_evidence Ledgers]
    
    R --> S[Orchestrator Dispatches 'research_synthesize']
    S --> T[Generate Markdown Report with Deterministic Citations]
    T --> U[Citation Audit Engine: Verify Every Bracketed Claim [n]]
    U --> V[Emit CAS Artifacts: research_report.md & citation_audit.json]
```

---

## 4. Content-Addressable Storage (CAS) & Cross-Project Artifact Lineage

Every asset output is stored deterministically by its cryptographic hash and can be transferred between projects with full lineage tracking.

```mermaid
graph TD
    subgraph Execution & Ingestion
        T[Task Output Generated] --> H[Compute SHA-256 Hash]
        H --> CAS_CHK{Exists in CAS?}
        CAS_CHK -->|Yes| DEDUP[Deduplicate: Point to Existing CAS Blob]
        CAS_CHK -->|No| WRITE[Atomic Disk Write: data/artifacts/xx/hash]
    end

    subgraph Relational Catalog
        WRITE & DEDUP --> ART[Insert / Update 'artifacts' Record]
        ART --> VER[Insert Immutable 'artifact_versions' Record]
        VER --> FTS[Tokenize & Index into 'artifact_search_fts']
        VER --> LIN[Record Upstream Edge in 'artifact_lineage']
    end

    subgraph Cross-Project Reuse Protocol
        P1[Project A: Deliverable 'system_report.md'] -->|User / Agent Action: transfer_to_project| XFER[transfer_artifact_to_project]
        XFER --> P2_ART[Create New 'artifacts' Record in Project B]
        P2_ART --> P2_VER[Link to Existing Version & CAS Blob]
        P2_VER --> P2_LIN[Insert 'artifact_lineage' with edge_type='transferred_to']
    end

    subgraph Knowledge Promotion
        VER -->|Human Click: Promote to Knowledge| VAULT[promote_artifact_to_knowledge]
        VAULT --> OBS[Write Obsidian Note: knowledge/vault/topic.md]
        OBS --> KFTS[Index in 'knowledge_fts' Table]
    end
```

---

## 5. System Observatory Telemetry & Anomaly Detection Flow

Telemetry events are ingested asynchronously, aggregated into rolling snapshots, and evaluated against statistical baselines.

```mermaid
sequenceDiagram
    autonumber
    participant Subsystems as Controller & Worker Subsystems
    participant ObsAPI as Observatory Telemetry Ingest
    participant EvtTable as DB: observatory_events
    participant SnapEngine as Snapshot & Baseline Engine
    participant SnapTable as DB: observatory_snapshots
    participant AlertEngine as Alert Evaluation Engine
    participant AlertTable as DB: observatory_alerts

    Subsystems->>ObsAPI: emit_event(event_type, metric_name, value, node_id)
    ObsAPI->>EvtTable: INSERT INTO observatory_events (Non-blocking async commit)

    loop Every 60 Seconds
        SnapEngine->>EvtTable: SELECT metrics from last 60s
        SnapEngine->>SnapEngine: Compute Percentiles (p50, p95, p99), Queue Depths, Error Rates
        SnapEngine->>SnapTable: INSERT INTO observatory_snapshots
        
        SnapEngine->>SnapEngine: Update 24h Moving Statistical Baselines
        
        SnapEngine->>AlertEngine: Trigger Alert Evaluation
        AlertEngine->>AlertEngine: Compare Snapshot vs Baselines (Z-score > 3.0 or Hard Limit)
        
        alt Threshold Exceeded
            AlertEngine->>AlertTable: INSERT INTO observatory_alerts (status='active', severity='warning|critical')
        else Metric Returned to Normal
            AlertEngine->>AlertTable: UPDATE observatory_alerts SET status='resolved'
        end
    end
```
