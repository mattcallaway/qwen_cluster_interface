# Qwen Collective: Operator & Administrator Guide (HOWTO)

This guide provides step-by-step procedures for deploying, administering, and coordinating the Qwen Collective distributed AI operating system.

---

## 📋 Table of Contents
1. [Prerequisites & System Requirements](#1-prerequisites--system-requirements)
2. [Local Setup & Development Running](#2-local-setup--development-running)
3. [Production Deployment as a Windows Service](#3-production-deployment-as-a-windows-service)
4. [Securing Remote Access with Tailscale](#4-securing-remote-access-with-tailscale)
5. [Managing Infrastructure & Capabilities](#5-managing-infrastructure--capabilities)
6. [Onboarding Compute & Execution Workers](#6-onboarding-compute--execution-workers)
7. [Running Projects, Workflows & Approval Gates](#7-running-projects-workflows--approval-gates)
8. [Synchronizing Architecture Knowledge to Obsidian](#8-synchronizing-architecture-knowledge-to-obsidian)
9. [Troubleshooting & Emergency Operations](#9-troubleshooting--emergency-operations)

---

## 1. Prerequisites & System Requirements

### Controller (Windows Host)
- **OS**: Windows 10/11 (64-bit) or Windows Server 2022+
- **Python**: Version 3.12 or newer installed and added to `PATH`
- **Memory**: Minimum 4GB RAM (8GB+ recommended)
- **Disk**: 10GB free space for SQLite database, logs, and Obsidian knowledge vault
- **Networking**: Tailscale installed and authenticated for private mesh networking

### Local Inference Nodes (Linux Laptops / Compute Nodes)
- **OS**: Ubuntu 22.04 LTS, Debian 12, or lightweight Linux distribution
- **RAM**: 4GB+ for quantized Qwen2.5 0.5B / 1.5B / 3B models (`Q4_K_M`)
- **Software**: OpenSSH Server, Python 3.10+, `llama.cpp` (`llama-server`) or `Ollama`

### Remote Execution Companion (Cloud VPS)
- **OS**: Ubuntu 22.04 / 24.04 LTS or Debian 12
- **RAM**: 1GB - 2GB minimum
- **Access**: Outbound connectivity to Tailscale network, non-root user (`qwen-worker`)

---

## 2. Local Setup & Development Running

### Step 2.1: Clone and Create Virtual Environment
```powershell
# Open PowerShell in your projects directory
git clone https://github.com/mattcallaway/qwen_cluster_interface.git
cd qwen_cluster_interface

# Create and activate Python virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install core dependencies
pip install -r requirements.txt
```

### Step 2.2: Configure Development Secrets
Create a `.env` file in the repository root or configure `$HOME\.qwen-collective\qwen-collective.env`:
```ini
QC_APP_NAME=Qwen Collective
QC_ENV=development
QC_HOST=127.0.0.1
QC_PORT=8000
QC_DATA_DIR=C:\Users\<YourUsername>\.qwen-collective\data
QC_SECRET_KEY=dev-secret-key-replace-in-production-must-be-32-chars
QC_ADMIN_PASSWORD=your-secure-admin-password
QC_SESSION_EXPIRE_HOURS=72
QC_LOG_LEVEL=INFO
```

### Step 2.3: Launch the Development Server
```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
Access the application at [http://127.0.0.1:8000](http://127.0.0.1:8000).

---

## 3. Production Deployment as a Windows Service

The Windows Controller is designed to run 24/7 as an automatic Windows Service via **WinSW**.

### Step 3.1: Service Configuration
The WinSW wrapper is configured at `service\qwen-collective-service.xml`. It automatically references:
- Executable: `%BASE%\..\.venv\Scripts\python.exe`
- Arguments: `-m uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level info`
- Environment: `%BASE%\..\.qwen-collective\qwen-collective.env`
- Auto-recovery: Restarts after 5s, 15s, and 30s on failure.
- Rotating Logs: 10MB per log file, 5 archives retained.

### Step 3.2: Installing the Windows Service (Elevated Prompt)
Open PowerShell as **Administrator**:
```powershell
cd "C:\Users\<YourUser>\.gemini\antigravity-ide\scratch\qwen-collective\service"
.\qwen-collective-service.exe install
```

### Step 3.3: Managing the Service
```powershell
# Start the service
Start-Service QwenCollective

# Check service status
Get-Service QwenCollective

# Restart the service (e.g., after software updates or migrations)
Restart-Service QwenCollective

# Stop the service
Stop-Service QwenCollective
```

> **Helper Script**: You can also right-click `scripts\restart-service-as-admin.bat` and select **"Run as administrator"** to gracefully stop and start the service with error verification.

---

## 4. Securing Remote Access with Tailscale

The controller binds strictly to `127.0.0.1` by default to prevent accidental LAN or public internet exposure. To access the dashboard securely from other machines or mobile devices:

### Step 4.1: Ensure Tailscale is Connected
```powershell
tailscale status
```

### Step 4.2: Expose via `tailscale serve`
```powershell
# Forward private Tailscale HTTPS traffic to the local controller port
tailscale serve --bg 8000
```
Your controller is now accessible to authenticated Tailscale devices at `https://<node-name>.<tailnet>.ts.net`.

---

## 5. Managing Infrastructure & Capabilities

Navigate to **Infrastructure** in the sidebar navigation or visit [http://127.0.0.1:8000/infrastructure](http://127.0.0.1:8000/infrastructure).

### Step 5.1: Review Hardware Topology (Overview Tab)
- Inspect total Registered Nodes, Verified Capabilities, and Architecture Drift count.
- Review hardware gauges: CPU core count, Physical and Available RAM, and Storage free space.

### Step 5.2: Run Read-Only Node Discovery (Workers Tab)
1. Select the **Workers & Nodes** tab.
2. Click **Run Discovery** on any registered node card.
3. The controller performs a strictly read-only inspection of installed Python packages, runtimes, and system services without making system modifications.

### Step 5.3: Verify Capabilities (Capability Registry Tab)
1. Select the **Capability Registry** tab.
2. View capabilities across the 15 categories (AI Inference, Python Execution, Browser Automation, File Processing, etc.).
3. Click the **Verify** button next to any capability.
4. The system executes a safe, side-effect-free probe (e.g., in-memory AST compilation for Python, loopback socket binding for network) and records the probe execution time and verified operational status.

### Step 5.4: Resolve Architecture Drift (Architecture Drift Tab)
If software packages are uninstalled or services become unavailable, the reconciliation engine detects the drift.
1. Select the **Architecture Drift** tab.
2. Review the discrepancy type, severity, and detected vs declared state.
3. Click **Mark Resolved** once the underlying system state is aligned.

---

## 6. Onboarding Compute & Execution Workers

### Onboarding a Linux Inference Node (OpenSSH)
1. Ensure the Linux machine has Python and `llama-server` or `Ollama` installed.
2. Add the Linux machine's public SSH key to the controller host's `known_hosts` file (`~/.ssh/known_hosts`).
3. In the Qwen Collective UI, navigate to **Compute Nodes** and register the machine's Tailscale IP and SSH credentials.
4. The controller uses strict `paramiko.RejectPolicy` to reject untrusted host keys.

### Onboarding the VPS Worker Companion (Milestone 2)
1. Log into your VPS via SSH as a non-root user (`qwen-worker`).
2. Clone the worker package or install `vps_agent`:
   ```bash
   git clone https://github.com/mattcallaway/qwen_cluster_interface.git
   cd qwen_cluster_interface/vps_agent
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. Create worker configuration `vps_agent.env`:
   ```ini
   QC_CONTROLLER_URL=https://<controller-tailscale-node>.ts.net
   QC_WORKER_ID=vps-worker-01
   QC_WORKER_TOKEN=<generated-worker-bearer-token>
   QC_POLL_INTERVAL=5
   ```
4. Start the worker daemon as a systemd service:
   ```bash
   sudo systemctl enable --now qwen-worker.service
   ```
5. The worker will connect to the controller, advertise its capabilities, send heartbeats, and pull approved leased tasks.

---

## 7. Running Projects, Workflows & Approval Gates

### Step 7.1: Create a Project
1. Navigate to **Projects** → Click **New Project**.
2. Define the project objective, priority, and target completion criteria.

### Step 7.2: Define Paths & Stages
1. Inside the project, add an execution **Path** (e.g., "Research & Data Ingestion").
2. Break the path into sequential **Stages** (e.g., "Stage 1: Exploration", "Stage 2: Processing").

### Step 7.3: Assign Tasks & Approval Gates
1. Add tasks with explicit capability requirements (e.g., requires `web_retrieval` and `python_execution`).
2. Add an **Approval Gate** before execution. Tasks cannot execute automatically until a human administrator approves the gate.

### Step 7.4: Review Deliverables & Promote to Knowledge
1. When a task completes, inspect the uploaded deliverable and its SHA-256 checksum.
2. Click **Promote to Knowledge** to ingest verified findings directly into the persistent Obsidian vault.

---

## 8. Synchronizing Architecture Knowledge to Obsidian

Live infrastructure facts can be exported into human-readable Markdown notes that integrate directly into your Obsidian vault:

1. Navigate to **Infrastructure** → Click the **📚 Sync to Knowledge Vault** button.
2. The controller generates `knowledge/system/infrastructure_architecture.md` containing:
   - Live hardware specs and memory headroom.
   - Verified operational capabilities.
   - Active services and scheduled timers.
   - Known limitations and security policies.
3. This note is automatically indexed into SQLite FTS5 for instant search by local Qwen models.

---

## 9. Troubleshooting & Emergency Operations

### Issue: "Too many failed login attempts. Please wait X seconds."
- **Cause**: Security lockout triggered by 5 failed login attempts within 5 minutes.
- **Resolution**:
  - Wait for the lockout timer to expire (up to 300 seconds), OR
  - Clear the rate-limiting table via PowerShell:
    ```powershell
    python -c "import sqlite3; conn = sqlite3.connect(r'C:\Users\<User>\.qwen-collective\data\qwen_collective.db'); conn.execute('DELETE FROM login_attempts'); conn.commit()"
    ```

### Issue: Windows Service Fails to Start
- Check the log files at `service\logs\qwen-collective-service.err.log` and `service\logs\qwen-collective-service.out.log`.
- Verify that port 8000 is not already occupied:
  ```powershell
  netstat -ano | findstr 8000
  ```
- Ensure the administrator password in `qwen-collective.env` is at least 12 characters and the secret key is at least 32 characters in production mode.

### Issue: Database Migration Error
- The system automatically creates a pre-migration database backup before applying any schema migration (e.g., `qwen_collective.pre-migration-v5-to-v6-<timestamp>.db`).
- If migration fails, restore the backup file and inspect `app\migrations.py`.
