# ARCHITECTURE.md: Architectural Contract

## Core Philosophy
> **"Use what we have. Understand what we control. Keep what we learn. Expand without rebuilding."**

The system coordinates replaceable resources. No individual model, machine, or VPS provider is fundamental to its identity.

## Four-Layer Design

```
+-------------------------------------------------------------------------+
| Layer A: Control Server (Always-on local anchor)                        |
| - Web UI (FastAPI + Jinja2 + HTMX)                                      |
| - SQLite (State, Machines, Tasks, Approvals, Inference Backends)        |
| - Machine Registry & Defensive SSH Probing                              |
| - Task Queue with bounded execution and manual approval gates           |
+-------------------------------------------------------------------------+
       |                                                   |
       v (HTTP / Local Network)                            v (Tailscale / HTTPS)
+------------------------------------+        +---------------------------+
| Layer B: Compute Nodes             |        | Layer C: Online Worker    |
| - Laptops / Workstations           |        | - Internet-connected VPS  |
| - Standalone (Ollama, llama-server)|        | - Isolated companion      |
| - Distributed llama.cpp RPC workers|        | - Untrusted input barrier |
+------------------------------------+        +---------------------------+
       ^
       | (Feeds context & logs)
+-------------------------------------------------------------------------+
| Layer D: Persistent Knowledge (Durable & Independent)                   |
| - Markdown files in standard Obsidian-compatible directory tree         |
| - SQLite FTS5 for fast local keyword searching                           |
| - Staged inbox for external research before manual promotion            |
+-------------------------------------------------------------------------+
```

## Permanent Architectural Rules

1. **Local Ownership & Continuity**:
   The Control Server stores all data locally in `data/`. If all compute nodes and the VPS go offline, the UI, knowledge repository, task history, and machine registry remain 100% accessible and responsive.

2. **No Invented State**:
   Never display simulated hardware metrics, fake RAM totals, or synthetic benchmark values. If a machine has not reported its specs or is unreachable, the UI clearly displays "Unknown" or "Offline".

3. **Compute Realism**:
   Adding computers does not magically pool RAM. Standalone inference and distributed inference (llama.cpp RPC) are explicitly configured, tested, and tracked. A distributed group status reflects genuine probe tests, not theoretical availability.

4. **Security Boundaries for Untrusted Workers**:
   The internet-facing VPS is an execution worker, never an authority. Data from the VPS is treated as untrusted. The VPS does not have database, root, or unrestricted filesystem access to the Control Server.

5. **Approval Gates Before Consequential Action**:
   Actions that affect external systems, publish data, or alter server configurations require explicit human approval via the web UI.

6. **Conservative & Durable Stack**:
   - Python 3.11+
   - FastAPI + Pydantic v2
   - SQLite + FTS5 (WAL mode)
   - Jinja2 + HTMX + clean custom CSS
   - Standard SSH for administration (read-only probes, keys only, no plaintext storage)
   - Standard HTTP APIs for inference and worker communications

7. **Extensibility Without Rewrites**:
   Inference engines, compute nodes, and workers are connected through small, explicit adapters. Upgrading models or changing nodes requires zero core database migrations or architectural rewrites.
