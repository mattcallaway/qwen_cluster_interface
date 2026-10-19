# Qwen Collective
### A Sovereign, Distributed AI Coordination & Autonomous Execution Platform

[![Tests](https://img.shields.io/badge/Tests-470%2B%20Passed-brightgreen.svg)]()
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20VPS-blue.svg)]()
[![Python](https://img.shields.io/badge/Python-3.12%2B-blue.svg)]()
[![FastAPI](https://img.shields.io/badge/FastAPI-Production%20Hardened-green.svg)]()
[![SQLite](https://img.shields.io/badge/SQLite-WAL%20%2B%20FTS5-orange.svg)]()
[![Security](https://img.shields.io/badge/Security-Zero%20Trust%20Hardened-red.svg)]()
[![Tailscale](https://img.shields.io/badge/Tailscale-Private%20WireGuard%20Mesh-blueviolet.svg)]()

> *"Use what we have. Understand what we control. Keep what we learn. Expand without rebuilding."*

**Qwen Collective** transforms heterogeneous computing hardware—an always-on Windows coordination controller, local low-power Linux compute nodes running quantized open models, and an isolated cloud VPS operational environment—into a sovereign, distributed AI operating system.

It bridges the gap between **autonomous reasoning** and **safe, deterministic real-world execution**:
- The **Windows Controller** provides authoritative coordination, strict human approval gates, continuous web research, content-addressable artifact management, and live infrastructure self-awareness.
- **Local Inference Nodes** provide local intelligence, planning, task decomposition, and code drafting without relying on cloud APIs.
- The **Remote VPS Operational Node** runs approved programs, recurring scheduled jobs, browser automation, and data pipelines independently of AI inference.

---

## 🏛️ System Architecture

```text
                               +--------------------------------------------+
                               |        AUTHORITATIVE COORDINATOR           |
                               |    Windows Controller (Port 8000/8010)     |
                               |  - Autonomous Multi-Stage Project Engine   |
                               |  - Unified Deliverables & CAS Artifacts    |
                               |  - Web Research & Citation Audit Ledger    |
                               |  - Infrastructure & Capability Registry    |
                               |  - System Observatory & Real-time Telemetry|
                               |  - SQLite WAL + Obsidian Markdown Vault    |
                               +--------------------------------------------+
                                       /                             \
                Tailscale / OpenSSH   /                               \  Tailscale HTTPS / Pull Protocol
          (Strict Known Hosts, RO)  /                                 \ (Bearer Auth, Leased Tasks)
                                   v                                   v
+--------------------------------------------+     +--------------------------------------------+
|         LOCAL INFERENCE CLUSTER            |     |        REMOTE EXECUTION ENVIRONMENT        |
|    Linux Compute Nodes / Laptops           |     |             Cloud VPS Companion            |
|  - Quantized Qwen2.5 (0.5B - 7B Q4_K_M)    |     |  - Lightweight Python Worker Agent         |
|  - Ollama & llama.cpp RPC workers          |     |  - Sandboxed Python & Shell Execution      |
|  - Defensive Read-Only Hardware Probes     |     |  - Systemd Services & Timers (Independent) |
|  - Local reasoning & proposal drafting     |     |  - Disconnect-Resilient Result Buffering   |
+--------------------------------------------+     +--------------------------------------------+
```

---

## 🚀 Key Subsystems & Highlights

### 1. Autonomous Project Orchestrator
- **State Machine**: Driven by an audited state machine (`DRAFT` → `PLANNING` → `AWAITING_PLAN_APPROVAL` → `READY` → `RUNNING` → `EVALUATING` → `COMPLETED`).
- **Dynamic Task Graph (DAG)**: Compiles high-level user objectives into topologically sorted stages and tasks conforming to `qc.plan.v1`.
- **Bounded Model Repair**: Models generate plans with `PLANNER_MAX_TOKENS = 2048`. Truncated JSON streams are automatically repaired via bracket/quote balancing (`try_repair_truncated_json`), and schema violations are healed with bounded repair iterations.
- **Hybrid Controller/Worker Execution**: Deterministic controller-steps (research extraction, citation audits, report compilation) execute locally with zero worker overhead; distributed computations lease to verified remote workers.

### 2. Unified Deliverables & Artifact System
- **Every Output is an Asset**: No more disposable outputs. Every generated file, report, dataset, and code artifact is versioned and discoverable.
- **Content-Addressable Storage (CAS)**: SHA-256 deduplicated binary storage (`data/artifacts/{hash[:2]}/{hash}`).
- **Interactive UI**: Preview in-browser, download with 1-click, and inspect comprehensive lineage (which task, stage, worker, and model produced it).
- **Cross-Project Reuse**: Send any deliverable back into another project as an authoritative input with full audit history.
- **Full-Text Search (FTS5)**: Fast keyword and BM25 search across all project deliverables, claims, and notes.

### 3. Continuous Web Research & Citation Audits
- **Multi-Provider Search**: Pluggable backends (DuckDuckGo, SearXNG, Custom Search API) with automatic health tracking and failovers.
- **Secure Retrieval & SPA Fallback**: SSRF-hardened fetcher with automatic headless Chromium fallback for JavaScript-rendered SPAs.
- **Claim & Citation Audit Ledger**: Deterministically extracts claims, scores supporting evidence passages, and audits citations so every fact traces back to an immutable source record.
- **Enforceable Budgets**: Query, document, token, and browser runtime limits prevent runaway resource usage.

### 4. System Observatory & Telemetry
- **Live Cluster Monitoring**: Real-time snapshots of CPU, memory, inference latency, task queues, and network connectivity.
- **Enforceable Alerts & Baselines**: Automated anomaly detection comparing real-time telemetry to statistical baselines.

### 5. Infrastructure Self-Awareness & Capability Registry
- **Three-Tier Capability Separation**: `Declared` (admin configuration) → `Discovered` (read-only inspection) → `Verified` (side-effect-free test probes).
- **15 Structured Categories**: AI Inference, Web Retrieval, Search, Browser Automation, Python Execution, Approved Shell, File Processing, Document Extraction, Data Transformation, API Integrations, Scheduled Execution, Persistent Services, Monitoring, Software Deployment, and Reporting.
- **Token-Budgeted Context Packets**: Dynamically synthesizes hardware facts into token-budgeted prompt packets (500 to 4,000 tokens) with priority pruning for small local models.

### 6. Zero-Trust Security Architecture
- **Dedicated Worker Identities**: Granular Bearer tokens per machine; no shared administrative secrets.
- **Secret Scrubbing**: Automatic redaction of passwords, tokens, and keys from AI prompts, execution logs, and knowledge notes.
- **Prompt Injection Defense**: Untrusted user inputs, search results, and extracted documents are wrapped in boundary markers (`<<<OBJECTIVE...OBJECTIVE>>>`) and scanned for injection attempts.
- **Rate-Limiting Lockout**: SQLite-backed `login_attempts` tracker locks out both the client IP and username after 5 failed attempts for 300 seconds.

---

## 🛠️ Technology Stack

| Component | Technology | Rationale |
|---|---|---|
| **Core Framework** | Python 3.12+, FastAPI, Uvicorn | Async I/O, strict typing, high throughput |
| **Web UI** | Jinja2, HTMX, Vanilla CSS | Server-rendered, reactive, zero JS bundle bloat |
| **Persistence** | SQLite 3 WAL Mode + FTS5 | ACID transactions, concurrent reads, instant full-text search |
| **Storage Engine** | Content-Addressable (CAS, SHA-256)| Deduplication, cryptographic integrity |
| **Knowledge Vault**| Markdown (Obsidian Compatible) | Future-proof, durable, portable human-readable knowledge |
| **Service Host** | WinSW (Windows Service Wrapper) | Robust Windows service management and crash recovery |
| **Networking** | Tailscale (WireGuard Mesh) | Private authenticated mesh with end-to-end encryption |
| **Inference Engines**| llama.cpp, Ollama, llama-server | Sovereign, local quantized open models (Qwen2.5) |

---

## 🚦 Quickstart

### Prerequisites
- Python 3.12+ (64-bit)
- Windows Host (Controller) and optional Linux/VPS nodes

### 1. Clone & Setup Environment
```bash
git clone https://github.com/mattcallaway/qwen_cluster_interface.git
cd qwen_cluster_interface
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Initialize Database & Run Migrations
```bash
python -c "from app.migrations import run_migrations; run_migrations()"
```

### 3. Start the Controller
```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
Navigate to `http://127.0.0.1:8000` to access the web dashboard.

### 4. Run the Full Test Suite
```bash
pytest
```

---

## 📚 Documentation Sitemap

- [Technical Architecture Map (TECHMAP)](file:///TECHMAP.md) — Comprehensive architectural map & M0–M10 schemas
- [LLM System Explainer](file:///COLLECTIVE_EXPLAINER_FOR_LLMS.md) — Deep-dive mental model and operating guide for AI agents
- [System Interaction Patterns](file:///SYSTEM_INTERACTION_PATTERNS.md) — Visual Mermaid sequence diagrams & execution flows
- [API & Integration Specification](file:///API_AND_INTEGRATION_SPECIFICATION.md) — Complete REST endpoints, schemas & worker protocol
- [LLM Prompt & Schema Playbook](file:///LLM_PROMPT_AND_SCHEMA_PLAYBOOK.md) — Context packets, action catalogs & valid vs invalid plan examples
- [Operations & Incident Runbook](file:///OPERATIONS_AND_RUNBOOK.md) — Service controls, backups, CAS audits & troubleshooting trees
- [Artifact System Architecture](file:///ARTIFACT_SYSTEM_ARCHITECTURE.md) — Deliverable & artifact CAS storage design
- [Continuous Web Research Architecture](file:///WEB_RESEARCH_ARCHITECTURE.md) — Search, retrieval, claims, and citations
- [System Observatory Architecture](file:///SYSTEM_OBSERVATORY_ARCHITECTURE.md) — Telemetry, metrics, and alerting
- [VPS Deployment Guide](file:///VPS_DAEMON_DEPLOYMENT_GUIDE.md) — Cloud worker companion setup
