> **2026-10-28 development review:** [Verified-work package and Antigravity handoff](docs/verified-work-review/increment16/README.md). Candidate evidence only; runtime integration and production activation remain gated.

# Qwen Collective
### A Sovereign, Distributed AI Coordination & Execution Platform

[![Tests](https://img.shields.io/badge/Tests-233%20Passed-brightgreen.svg)]()
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20VPS-blue.svg)]()
[![Python](https://img.shields.io/badge/Python-3.12%2B-blue.svg)]()
[![FastAPI](https://img.shields.io/badge/FastAPI-Production%20Hardened-green.svg)]()
[![SQLite](https://img.shields.io/badge/SQLite-WAL%20%2B%20FTS5-orange.svg)]()
[![Security](https://img.shields.io/badge/Security-Zero%20Trust%20Hardened-red.svg)]()
[![Tailscale](https://img.shields.io/badge/Tailscale-Private%20WireGuard%20Mesh-blueviolet.svg)]()

> *"Use what we have. Understand what we control. Keep what we learn. Expand without rebuilding."*

**Qwen Collective** transforms heterogeneous computing hardware—an always-on Windows coordination controller, local low-power Linux compute nodes running quantized Qwen inference models, and an isolated cloud VPS operational environment—into a sovereign, distributed AI operating system.

It bridges the gap between **autonomous reasoning** and **safe, deterministic real-world execution**:
- The **Windows Controller** provides authoritative coordination, strict human approval gates, persistent memory, and live infrastructure self-awareness.
- **Local Qwen Nodes** provide local intelligence, planning, task decomposition, and code drafting without relying on cloud APIs.
- The **Remote VPS Operational Node** runs approved programs, recurring scheduled jobs, browser automation, and data pipelines independently of AI inference.

---

## 🏛️ System Architecture

```text
                               +--------------------------------------------+
                               |        AUTHORITATIVE COORDINATOR           |
                               |    Windows Controller (Port 8000/8010)     |
                               |  - Projects, Paths, Stages, Deliverables   |
                               |  - Infrastructure & Capability Registry    |
                               |  - Human Approval Gates & Audit Logs       |
                               |  - SQLite WAL + Obsidian Markdown Vault    |
                               |  - Token-Budgeted Architecture Context     |
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

## 🚀 Key Highlights & Capabilities

### 1. Infrastructure Self-Awareness & Capability Registry
- **Persistent Infrastructure Topology**: Live inventory of CPU cores, exact physical and available RAM (via Windows `GlobalMemoryStatusEx`), storage free space, active system services, and installed Python runtimes.
- **Three-Tier Capability Separation**:
  - *Declared*: What configuration or admins propose a node should support.
  - *Discovered*: Software, packages, and runtimes actually detected via read-only inspection.
  - *Verified*: Operational functions proven through side-effect-free test probes.
- **15 Structured Capability Categories**: AI Inference, Web Retrieval, Search, Browser Automation, Python Execution, Approved Shell, File Processing, Document Extraction, Data Transformation, API Integrations, Scheduled Execution, Persistent Services, Monitoring, Software Deployment, and Reporting.
- **Side-Effect-Free Probes**: Verifies capability readiness (in-memory AST compilation, local permission checks, loopback socket binding) without modifying system state or executing external scripts.
- **Token-Budgeted Architecture Context Packets**: Dynamically synthesizes live infrastructure facts into token-budgeted prompt packets (500 to 4,000 tokens) tailored for local Qwen models with priority pruning and omission notices.
- **Durable Obsidian Vault Synchronization**: Synchronizes live infrastructure facts to durable Markdown notes in `knowledge/system/infrastructure_architecture.md`.

### 2. Comprehensive Project Intelligence (Milestones 1–5)
- **Hierarchical Decomposition**: `Project` → `Path` → `Stage` → `Task` → `Deliverable`.
- **Evidence & Governance**: Strict approval gates, deliverable promotion workflows, cryptographic checksums, and immutable event logs.
- **Metrics & Accuracy Tracking**: Versioned forecasts vs actual observations, task throughput, accuracy tracking, and automated calibration.
- **Historical Learning & Retrospectives**: Captures project retrospectives, operational failure modes, and reusable procedural lessons directly into the persistent knowledge vault.

### 3. Secure VPS Execution Companion (Milestone 2)
- **Outbound Pull-Based Protocol**: The VPS connects inbound to the controller over private Tailscale WireGuard. No public controller ports are exposed.
- **Durable Task Leasing**: Atomic lease claims, configurable lease timeouts, heartbeat extensions, and duplicate submission guards.
- **Local Disconnect Buffering**: If network connectivity drops, the VPS agent safely executes authorized tasks and buffers results in a local SQLite store until reconnection.
- **Inference-Free Independence**: Approved recurring scheduled jobs and systemd services execute continuously on the VPS without requiring active Qwen model sessions.

### 4. Zero-Trust Security Architecture
- **Dedicated Worker Identities**: Granular Bearer tokens per machine; no shared administrative secrets.
- **Secret Scrubbing**: Automatic redaction of passwords, tokens, and keys from AI prompts, execution logs, and knowledge notes.
- **Defensive Hardware Probes**: Uses `paramiko.RejectPolicy` with strict `known_hosts` checks for SSH nodes.
- **Web Security**: CSRF token validation on all mutating endpoints, HttpOnly/SameSite session cookies, bounded SQLite login rate-limiting lockout against brute-force attacks, and SSRF loopback protections.
- **Windows Service Reliability**: Packaged with WinSW for automatic boot startup, crash recovery policies, and rotating logs.

---

## 🛠️ Technology Stack

| Component | Technology | Rationale |
|---|---|---|
| **Core Framework** | Python 3.12+, FastAPI | High performance, async I/O, native typing |
| **Web UI** | Jinja2, HTMX, Vanilla CSS | Server-rendered, reactive, zero JavaScript bundle bloat |
| **Persistence** | SQLite WAL Mode + FTS5 | ACID transactions, concurrent reads, instant full-text search |
| **Knowledge Vault** | Markdown (Obsidian Compatible) | Future-proof, durable, portable human-readable knowledge |
| **Service Host** | WinSW (Windows Service Wrapper) | Robust Windows service management and crash recovery |
| **Networking** | Tailscale (WireGuard) | Private authenticated mesh with end-to-end encryption |
| **Inference Engines**| llama.cpp, Ollama | Sovereign, local quantized open models (Qwen2.5) |

---

## 🚦 Quickstart

### Prerequisites
- Windows 10/11 or Windows Server (Controller)
- Python 3.12 or newer
- Git

### 1. Clone & Set Up Virtual Environment
```powershell
git clone https://github.com/mattcallaway/qwen_cluster_interface.git
cd qwen_cluster_interface
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Configure Environment Secrets
Create a secure configuration file at `$HOME\.qwen-collective\qwen-collective.env`:
```ini
QC_APP_NAME=Qwen Collective
QC_ENV=development
QC_HOST=127.0.0.1
QC_PORT=8000
QC_DATA_DIR=C:\Users\<YourUser>\.qwen-collective\data
QC_SECRET_KEY=your-secure-random-secret-key-min-32-chars
QC_ADMIN_PASSWORD=your-secure-admin-password
QC_SESSION_EXPIRE_HOURS=72
QC_LOG_LEVEL=INFO
```

### 3. Run the Development Server
```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
Open [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser and sign in with `admin` and your configured password.

### 4. Run the Test Suite
```powershell
pytest -v
```
*Current test suite: 233 automated unit, integration, and security tests passing.*

---

## 📖 Documentation Index

- **[Technical Architecture Map (TECHMAP.md)](TECHMAP.md)**: Deep dive into the database schema (Migrations 1–6), component communication mesh, leasing protocols, and security boundaries.
- **[Operator & Administrator Guide (HOWTO.md)](HOWTO.md)**: Step-by-step instructions for running the Windows service, connecting Linux inference nodes, setting up the VPS agent, managing capabilities, and running projects.
- **[Capability Registry Specification (CAPABILITY_REGISTRY_SPEC.md)](CAPABILITY_REGISTRY_SPEC.md)**: Typed schema, lifecycle stages, and verification probes for all 15 capability categories.
- **[VPS Agent Protocol Specification (VPS_AGENT_PROTOCOL.md)](VPS_AGENT_PROTOCOL.md)**: Detailed message formats, leasing state machine, idempotency keys, and buffering rules.
- **[Deployment Lifecycle (DEPLOYMENT_LIFECYCLE.md)](DEPLOYMENT_LIFECYCLE.md)**: The 8-stage deployment lifecycle from proposal to retirement.
- **[Execution Security Guide (EXECUTION_SECURITY.md)](EXECUTION_SECURITY.md)**: Least privilege execution, sandbox containment, network guards, and secret isolation.
- **[Infrastructure Context Specification (INFRASTRUCTURE_CONTEXT_SPEC.md)](INFRASTRUCTURE_CONTEXT_SPEC.md)**: Token-budgeted prompt packet synthesis and priority pruning rules.
- **[Operations Runbook (VPS_OPERATIONS_RUNBOOK.md)](VPS_OPERATIONS_RUNBOOK.md)**: Troubleshooting, health check routines, and emergency disaster recovery.

---

## 🔒 Security Policy

Qwen Collective enforces strict defensive controls:
1. No unauthenticated public endpoints.
2. The VPS execution environment runs as a non-privileged user and never receives root permissions.
3. Arbitrary shell execution is forbidden; only approved, declared, and verified operations may run.
4. AI-generated proposals require explicit human review and approval before execution.

---

## 📄 License
MIT License. Created by Matt Callaway and the Qwen Collective Contributors.
