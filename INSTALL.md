# Qwen Cluster Interface — Installation & Hardening Guide

This guide covers installing, configuring, and hardening the **Qwen Cluster Interface (Qwen Collective)** across Windows control servers, Linux compute nodes, and external VPS workers.

---

## 1. Prerequisites

- **Python 3.11+ or 3.12+** (with virtual environment support)
- **SQLite 3.35+** (built into standard Python distributions)
- **OpenSSH Client** (for automated hardware diagnostics)
- **Git** (for code management)
- **Tailscale** (optional, recommended for encrypted remote access)

---

## 2. Windows Control Server Installation

### Quick Automated Setup (Recommended)

1. Open PowerShell and navigate to the project directory:
   ```powershell
   cd "C:\path\to\qwen_cluster_interface"
   ```

2. Run the automated installer:
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
   ```
   *The installer automatically provisions a dedicated `.venv`, installs requirements, creates the external data vault at `%USERPROFILE%\.qwen-collective\data`, generates 256-bit cryptographic secrets, hardens `%USERPROFILE%\.ssh` permissions, configures WinSW, and validates all 55 tests.*

3. Your production credentials are saved in:
   `%USERPROFILE%\.qwen-collective\qwen-collective.env`

---

### Running the Control Server on Windows

#### Option A: One-Click Background Windows Service (Always-On)
To register Qwen Collective as an automatic system service that starts on boot and auto-restarts upon crash:
1. Double-click **`service\register-service.bat`**.
2. Accept the Windows UAC elevation prompt.
3. The script registers `QwenCollective` via WinSW and starts it in the background.

*To uninstall the service, double-click `service\unregister-service.bat`.*

#### Option B: Administrator PowerShell Service Commands
```powershell
cd service
.\qwen-collective-service.exe install
.\qwen-collective-service.exe start
.\qwen-collective-service.exe status
```

#### Option C: Interactive Terminal Execution (Testing / Dev)
```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-windows.ps1
```
Open **`http://127.0.0.1:8000`** in your browser.

---

## 3. Linux Control Server Installation

For Debian / Ubuntu / Raspberry Pi OS 64-bit:

```bash
# 1. System dependencies
sudo apt update && sudo apt install -y python3 python3-venv python3-pip sqlite3 git

# 2. Dedicated service user & data directory
sudo useradd -r -s /bin/bash -m -d /opt/qwen-collective qwen
sudo mkdir -p /var/lib/qwen-collective/data
sudo chown -R qwen:qwen /var/lib/qwen-collective
sudo chmod 700 /var/lib/qwen-collective

# 3. Clone and setup venv
sudo -u qwen git clone https://github.com/mattcallaway/qwen_cluster_interface.git /opt/qwen-collective/app
cd /opt/qwen-collective/app
sudo -u qwen python3 -m venv /opt/qwen-collective/venv
sudo -u qwen /opt/qwen-collective/venv/bin/pip install -r requirements.txt

# 4. Configure production environment
sudo -u qwen tee /opt/qwen-collective/.env << 'EOF'
QC_APP_NAME="Qwen Collective"
QC_ENV="production"
QC_HOST="127.0.0.1"
QC_PORT="8000"
QC_DATA_DIR="/var/lib/qwen-collective/data"
QC_SECRET_KEY="<paste-random-32+-character-secret>"
QC_ADMIN_PASSWORD="<unique-strong-password-min-12-chars>"
QC_SESSION_EXPIRE_HOURS="72"
QC_LOG_LEVEL="INFO"
EOF
sudo chmod 600 /opt/qwen-collective/.env

# 5. Systemd service registration
sudo tee /etc/systemd/system/qwen-collective.service << 'EOF'
[Unit]
Description=Qwen Collective Control Server
After=network.target

[Service]
Type=simple
User=qwen
Group=qwen
WorkingDirectory=/opt/qwen-collective/app
EnvironmentFile=/opt/qwen-collective/.env
ExecStart=/opt/qwen-collective/venv/bin/python -m app.main
Restart=on-failure
RestartSec=5s
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/qwen-collective/data
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now qwen-collective
```

---

## 4. Compute Node Setup & Distributed Inference

### 1. SSH Pre-seeding (Required for Hardware Discovery)
To allow the Control Server to inspect hardware without disabling host-key checking:
```bash
# Append node host keys into known_hosts
ssh-keyscan -H <node-1-ip> >> ~/.ssh/known_hosts
ssh-keyscan -H <node-2-ip> >> ~/.ssh/known_hosts
```

### 2. Standalone Inference (Ollama or llama-server)
On your compute node:
```bash
ollama run qwen2.5:3b
# Or:
./llama-server -m models/qwen2.5-3b-instruct-q4_k_m.gguf --port 8080 --host 0.0.0.0
```

### 3. Distributed RPC Inference (Dual 4GB RAM Linux Laptops)
To run a larger 7B model split across two 4GB laptops:

**On Laptop 2 (Worker)**:
```bash
# Build llama.cpp with RPC support:
cmake -B build -DGGML_RPC=ON && cmake --build build --config Release
# Start RPC server:
./build/bin/rpc-server -H 0.0.0.0 -p 50052
```

**On Laptop 1 (Coordinator)**:
```bash
./build/bin/llama-server \
  -m models/qwen2.5-7b-instruct-q4_k_m.gguf \
  -c 4096 \
  --host 0.0.0.0 \
  --port 8080 \
  --rpc <laptop-2-ip>:50052
```

In the Qwen Collective web UI:
1. Navigate to **Inference & Models** &rarr; **Add Inference Endpoint**.
2. Set Endpoint URL to `http://<laptop-1-ip>:8080`.
3. Verify connectivity and start chatting!

---

## 5. Tailscale Remote Access Setup (Zero Public Exposure)

To access your Control Server securely from your mobile phone or outside laptop without port forwarding or exposing port 8000 to the public:

1. **Install and authenticate Tailscale**:
   ```powershell
   tailscale up
   ```
2. **Enable Tailscale Serve**:
   ```powershell
   tailscale serve --bg 8000
   ```
3. Your control interface is now accessible over HTTPS at:
   `https://<node-name>.<tailnet-name>.ts.net`

> **Security Note**: Never enable `tailscale funnel`. `tailscale serve` keeps access strictly confined to authenticated devices on your encrypted private tailnet.

---

## 6. Atomic Backups & Disaster Recovery

### Windows
Run manual backup or schedule in Task Scheduler:
```powershell
powershell -ExecutionPolicy Bypass -File scripts\backup-windows.ps1
```
*Creates timestamped ZIP archives containing an online atomic SQLite snapshot and the complete Markdown knowledge vault, automatically pruning archives older than 14 days.*

### Linux
Run daily cron backup:
```bash
sudo tee /etc/cron.daily/qwen-backup << 'EOF'
#!/bin/bash
set -euo pipefail
BACKUP_DIR="/var/backups/qwen-collective"
DATE=$(date +%Y-%m-%d)
mkdir -p "$BACKUP_DIR"
sqlite3 /var/lib/qwen-collective/data/qwen_collective.db ".backup /tmp/qc_backup.db"
tar -czf "$BACKUP_DIR/qwen_backup_$DATE.tar.gz" -C /var/lib/qwen-collective/data knowledge -C /tmp qc_backup.db
rm -f /tmp/qc_backup.db
find "$BACKUP_DIR" -name "qwen_backup_*.tar.gz" -mtime +14 -delete
EOF
sudo chmod +x /etc/cron.daily/qwen-backup
```
