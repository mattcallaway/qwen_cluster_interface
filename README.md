# Qwen Cluster Interface (Qwen Collective)
### A Private, Modular, Distributed AI Operating System

[![Tests](https://img.shields.io/badge/Tests-85%20Passed-brightgreen.svg)]()
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-blue.svg)]()
[![License](https://img.shields.io/badge/License-MIT-purple.svg)]()
[![Security](https://img.shields.io/badge/Security-Strict%20Defensive%20Hardened-orange.svg)]()

> **"Use what we have. Understand what we control. Keep what we learn. Expand without rebuilding."**

**Qwen Cluster Interface** coordinates a self-hosted, sovereign AI cluster assembled from everyday computing hardware: a low-power always-on control machine, aging Linux laptops (e.g., 4GB RAM machines running distributed `llama.cpp` RPC), and an isolated external VPS companion worker.

---

## Key Highlights

- **Lightweight & Transparent Control Server**: FastAPI, SQLite WAL mode, FTS5 full-text indexing, Jinja2, HTMX, and Vanilla CSS. Zero Node.js / heavy framework runtime dependencies.
- **Hardware-Aware Model Coordination**: Supports standalone model inference (Ollama, llama-server) and experimental distributed inference (llama.cpp RPC over private LAN or Tailscale). Realistic baselines prioritize small quantized models (e.g., Qwen2.5 0.5B, 1.5B, or 3B Q4_K_M) on 4GB RAM laptops, treating larger models (7B+) as experimental targets evaluated against real RAM, CPU inspection, and actual token-per-second benchmarks. *Security Note: Unauthenticated llama.cpp RPC listeners must never be exposed on public networks.*
- **Defensive Hardware Discovery**: Inspects Linux compute nodes via OpenSSH using strictly whitelisted, read-only diagnostic commands and strict `known_hosts` verification (`paramiko.RejectPolicy`). Never trusts arbitrary remote execution.
- **Persistent Knowledge Base**: Raw Markdown notes stored in an **Obsidian-compatible** vault, indexed dynamically into SQLite FTS5 for instant search. Your notes are 100% human-readable files that outlive any application.
- **Controlled VPS Companion Worker**: Isolates external web/probe tasks onto a cloud VPS behind strong Bearer token authentication, loopback binding by default, and strict task permission boundaries.
- **Always-On Windows Service & Tailscale Remote Access**: Automatic boot startup with WinSW, crash recovery, rotating logs, and secure WireGuard mesh access via `tailscale serve` (zero public port exposure).

---

## System Architecture Map

```text
+-------------------------------------------------------------------------+
| Layer A: Control Server (Windows / Linux Local Anchor)                  |
| - Local Host: 127.0.0.1:8000 only (No public ports)                     |
| - Web UI: FastAPI + Jinja2 + HTMX + Vanilla CSS (No heavy JS frameworks)|
| - Storage: SQLite WAL mode + FTS5 Full-Text Search                      |
| - Machine Registry & Defensive Read-Only Hardware Inspection            |
| - Persistent Task Queue with explicit Human Approval Gates              |
+-------------------------------------------------------------------------+
       |                                                   |
       v (Local LAN / SSH)                                 v (Tailscale / HTTPS)
+------------------------------------+        +---------------------------+
| Layer B: Compute Nodes             |        | Layer C: Online Worker    |
| - Laptops / Workstations           |        | - Internet-connected VPS  |
| - Standalone (Ollama, llama-server)|        | - Isolated companion      |
| - Distributed llama.cpp RPC workers|        | - Untrusted data barrier  |
+------------------------------------+        +---------------------------+
       ^
       | (Feeds context & logs)
+-------------------------------------------------------------------------+
| Layer D: Persistent Knowledge (Durable & Independent)                   |
| - Raw Markdown files in Obsidian-compatible directory tree              |
| - SQLite FTS5 for fast local keyword searching                          |
| - Staged inbox for external research before promotion                   |
+-------------------------------------------------------------------------+
```

For complete protocol details, network boundaries, and distributed memory allocation math, see the **[Technical Map (TECH_MAP.md)](TECH_MAP.md)**.

---

## Quickstart

### 1. Installation on Windows

Run PowerShell as Administrator or standard user:
```powershell
# Clone the repository
git clone https://github.com/mattcallaway/qwen_cluster_interface.git
cd qwen_cluster_interface

# Run the idempotent Windows setup script
powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
```
*Creates the Python virtual environment, installs dependencies, provisions external storage at `%USERPROFILE%\.qwen-collective\data`, generates 256-bit secrets, and validates all 55 tests.*

#### To Run Always-On in the Background:
Double-click **`service\register-service.bat`** (accept UAC elevation) to register and start the Windows Background Service.

#### To Run in Interactive Console:
```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-windows.ps1
```
Navigate to **`http://127.0.0.1:8000`**. Sign in using `admin` and the password saved in `%USERPROFILE%\.qwen-collective\qwen-collective.env`.

---

### 2. Installation on Linux

```bash
git clone https://github.com/mattcallaway/qwen_cluster_interface.git
cd qwen_cluster_interface

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env with your secrets and settings
python -m app.main
```

For production `systemd` configuration, user sandboxing, and cron backup jobs, see the **[Installation Guide (INSTALL.md)](INSTALL.md)**.

---

## Distributed Inference (Dual 4GB RAM Laptops)

Pooled execution of `Qwen 2.5 7B Instruct` across two aging laptops:

1. **On Laptop 2 (Worker)**:
   ```bash
   ./build/bin/rpc-server -H 0.0.0.0 -p 50052
   ```
2. **On Laptop 1 (Coordinator)**:
   ```bash
   ./build/bin/llama-server \
     -m models/qwen2.5-7b-instruct-q4_k_m.gguf \
     -c 4096 \
     --host 0.0.0.0 \
     --port 8080 \
     --rpc <laptop-2-ip>:50052
   ```
3. In the Qwen Collective UI under **Inference & Models**, register `http://<laptop-1-ip>:8080` to route chat interactions across both machines seamlessly.

---

## Secure Remote Access via Tailscale

Keep your Control Server strictly bound to `127.0.0.1:8000` with **zero public ports** and **no router port forwarding**:

```powershell
# Authenticate machine to your tailnet
tailscale up

# Enable secure Tailscale Serve
tailscale serve --bg 8000
```
Access your dashboard from your phone or laptop at: `https://<your-machine>.<tailnet>.ts.net`.

---

## Backup & Disaster Recovery

* **Windows**: Run `powershell -File scripts\backup-windows.ps1` (atomic online SQLite snapshot + compressed Markdown vault with 14-day retention).
* **Linux**: Configured via `/etc/cron.daily/qwen-backup` (documented in [INSTALL.md](INSTALL.md)).

---

## Documentation Index

- **[TECH_MAP.md](TECH_MAP.md)**: Deep dive into architecture, layers, data lifecycle, and hardware whitelist.
- **[INSTALL.md](INSTALL.md)**: Complete production installation and hardening guide for Windows, Linux, and VPS.
- **[WINDOWS_DEPLOYMENT.md](WINDOWS_DEPLOYMENT.md)**: WinSW service operations, log rotation, and OpenSSH permissions on Windows.
- **[STATUS.md](STATUS.md)**: Verified test matrix (55/55 tests passing) and deployment audit.

---

## Repository

* **GitHub**: [https://github.com/mattcallaway/qwen_cluster_interface](https://github.com/mattcallaway/qwen_cluster_interface)
