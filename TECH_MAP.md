# Qwen Cluster Interface — Technical Architecture & System Map

This document provides an exhaustive technical map of **Qwen Collective / Qwen Cluster Interface**, detailing component boundaries, protocol specifications, data lifecycles, and security controls.

---

## 1. System Architecture Map

```mermaid
graph TB
    subgraph "Layer A: Control Server (Windows / Linux Local Anchor)"
        UI["Web UI (Jinja2 + HTMX + Vanilla CSS)"]
        API["FastAPI Core Router"]
        AUTH["Auth & Session Manager (HMAC-SHA256)"]
        PROBE["SSH Probe & Diagnostic Engine"]
        FTS["SQLite FTS5 Search Engine"]
        DB[("SQLite WAL Database\n(Foreign Keys Enabled)")]
        VAULT["Markdown Knowledge Vault\n(Obsidian Compatible)"]
    end

    subgraph "Secure Access Layer"
        TS["Tailscale Mesh Network\n(Encrypted WireGuard Tunnel)"]
        REMOTE["Remote Admin Devices\n(Phone / Laptop)"]
    end

    subgraph "Layer B: Local Compute Nodes (Aging Laptops / Workstations)"
        subgraph "Node 1: Inference Coordinator (e.g., 4GB RAM Linux)"
            COORD["llama-server / Ollama\n(Port 8080)"]
            RAM1["Local RAM Allocation\n(~2.2 GB for layers 0..N)"]
        end
        subgraph "Node 2: RPC Worker (e.g., 4GB RAM Linux)"
            RPC["rpc-server daemon\n(Port 50052)"]
            RAM2["Remote RAM Allocation\n(~2.2 GB for layers N+1..Total)"]
        end
    end

    subgraph "Layer C: External Companion (Cloud VPS / Remote Worker)"
        VPS_APP["FastAPI Companion Worker\n(Port 8081)"]
        VPS_GATE["HMAC Token Verification Barrier"]
        EXT_NET["External Internet / APIs"]
    end

    %% Connections
    REMOTE -->|Private HTTPS| TS
    TS -->|Proxy to 127.0.0.1:8000| UI
    UI --> API
    API --> AUTH
    AUTH --> DB
    API --> PROBE
    API --> FTS
    FTS --> VAULT
    API --> DB

    PROBE -->|Read-Only SSH (Port 22)\nStrict RejectPolicy| Node 1
    PROBE -->|Read-Only SSH (Port 22)\nStrict RejectPolicy| Node 2

    API -->|HTTP Completions / SSE Stream| COORD
    COORD <-->|GGML RPC Protocol\n(Port 50052)| RPC

    API -->|HMAC Bearer Token\nTask Dispatch & Health| VPS_GATE
    VPS_GATE --> VPS_APP
    VPS_APP --> EXT_NET
```

---

## 2. Layer-by-Layer Technical Specification

### Layer A: The Control Server
* **Purpose**: Coordinates hardware, catalogs knowledge, queues tasks, and surfaces the private control interface.
* **Technology Stack**:
  * **Runtime**: Python 3.11+ / 3.12+ (AsyncIO, FastAPI, Uvicorn).
  * **Templates & UI**: Jinja2 + HTMX + Vanilla CSS (No npm, React, Vue, or Webpack dependencies).
  * **Database**: Embedded SQLite 3.35+ in `WAL` mode (`PRAGMA journal_mode = WAL; PRAGMA foreign_keys = ON;`).
  * **Search Engine**: SQLite `FTS5` virtual table indexing plain Markdown files on demand.
  * **Windows Service**: Windows Service Wrapper (WinSW v2.12.0) with automated 5s/15s/30s crash restarts and 10MB rotating logs.
  * **Linux Service**: Native `systemd` unit with security sandboxing (`ProtectSystem=strict`, `NoNewPrivileges=true`).
* **Port Binding**: Strictly bound to `127.0.0.1:8000`. Does **not** bind to `0.0.0.0` or open public firewall ports.

---

### Layer B: Compute Nodes & Distributed Inference
* **Purpose**: Executes model weights across consumer hardware and aging machines without high-end GPUs.
* **Inference Topologies**:
  1. **Standalone Node**:
     * Single machine running Ollama (`http://node-ip:11434`) or `llama-server` (`http://node-ip:8080`).
     * Runs models scaled to available RAM (e.g., Qwen 2.5 1.5B/3B Q4_K_M on 4GB machines).
  2. **Distributed RPC Group (Dual 4GB Laptops)**:
     * **Worker Node**: Runs `rpc-server -H 0.0.0.0 -p 50052` (compiled with `-DGGML_RPC=ON`).
     * **Coordinator Node**: Runs `llama-server -m <model.gguf> -c 4096 --host 0.0.0.0 --port 8080 --rpc <worker-ip>:50052`.
     * **Memory Distribution Math**:
       * Model: `Qwen 2.5 7B Instruct Q4_K_M` (~4.4 GB total tensor weights).
       * Split: ~2.2 GB allocated into Worker RAM via GGML RPC; ~2.2 GB + context buffer retained in Coordinator RAM.
       * Result: Enables 7B parameter reasoning across two 4GB laptops that individually cannot load the model.

---

### Layer C: Online VPS Companion Worker
* **Purpose**: Isolated companion running on a publicly connected VPS to handle external research, web fetching, or network tasks without exposing the local network.
* **Security Barrier**:
  * The Control Server interacts with the VPS worker solely through predefined API endpoints (`GET /health`, `POST /tasks/execute`).
  * Every request requires a shared secret token passed via `Authorization: Bearer <QC_AUTH_TOKEN>`.
  * The VPS worker cannot issue commands back to the Control Server (one-way dispatch).
  * Untrusted external outputs are routed to the staged Knowledge `inbox/` for human review prior to promotion.

---

### Layer D: Durable Knowledge Vault
* **Purpose**: Long-term memory and operational documentation that outlives any single piece of software.
* **Storage Standard**:
  * 100% human-readable Markdown files organized in an **Obsidian-compatible** directory hierarchy:
    * `system/`: Machine profiles, cluster topologies, network maps.
    * `projects/`: Specific research or construction initiatives.
    * `research/`: Synthesized findings and topic analyses.
    * `sources/`: External reference summaries and URLs.
    * `decisions/`: Architecture Decision Records (ADRs).
    * `journal/`: Daily activity and operational changelogs.
    * `inbox/`: Staged notes from external tasks awaiting human review.
  * SQLite `knowledge_fts` virtual table maintains a full-text search index, updated automatically on write and rebuildable at any time from disk.

---

## 3. Network & Security Architecture Map

| Protocol / Vector | Endpoint | Direction | Security Controls Enforced |
| :--- | :--- | :--- | :--- |
| **Local Web Interface** | `127.0.0.1:8000` | Inbound | Session cookie authentication, CSRF tokens on all POST routes, localhost binding. |
| **Remote Access** | `*.ts.net:443` | Inbound | Tailscale encrypted WireGuard mesh, mutual TLS, identity verification. No public ports or Funnel. |
| **Hardware Probing** | Node `:22` | Outbound | Paramiko SSH with strict `RejectPolicy`, `%USERPROFILE%\.ssh\known_hosts` checking. Whitelisted read-only commands. |
| **Inference Completions**| Node `:8080` | Outbound | HTTP/1.1 REST client with SSRF filter (prohibits cloud metadata `169.254.169.254`). |
| **Distributed RPC** | Node `:50052`| Cross-Node | Raw GGML RPC binary protocol over local subnet. |
| **VPS Task Dispatch** | VPS `:8081` | Outbound | HTTPS / Tailscale with constant-time HMAC token comparison. |

---

## 4. Hardware Inspection Whitelist

To eliminate remote code execution vulnerabilities, the automated hardware discovery engine strictly rejects arbitrary commands and executes only the following read-only POSIX diagnostic commands:

```text
1. Kernel & Arch:      uname -srm
2. CPU Architecture:   lscpu || cat /proc/cpuinfo
3. Memory Allocation:  free -m
4. Storage & Disks:    lsblk -b --output NAME,SIZE,TYPE,MOUNTPOINT || df -h
5. Network Interfaces: ip -brief addr || ifconfig
```

Host key checking is strictly enforced using `paramiko.RejectPolicy()`. Unknown hosts are never automatically trusted.
