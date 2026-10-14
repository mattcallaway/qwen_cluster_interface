# Qwen Collective: Linux Control Machine Deployment Checklist

This checklist guides installing and hardening Qwen Collective on the permanent, always-on Linux control server (e.g., mini-PC, thin client, or low-power desktop).

---

## 1. System Prerequisites

- [ ] **Base OS**: Debian 12 / Ubuntu 22.04+ / Raspberry Pi OS 64-bit.
- [ ] **Core Packages**:
  ```bash
  sudo apt update && sudo apt install -y python3 python3-venv python3-pip sqlite3 git curl
  ```
- [ ] **Dedicated System User**: Run the service under an unprivileged user without sudo rights:
  ```bash
  sudo useradd -r -s /bin/bash -m -d /opt/qwen-collective qwen
  ```

---

## 2. Directory Layout & Repository Setup

- [ ] **Install Application Tree**:
  ```bash
  sudo -u qwen git clone <repo-url> /opt/qwen-collective/app
  cd /opt/qwen-collective/app
  ```
- [ ] **Create Virtual Environment & Install Dependencies**:
  ```bash
  sudo -u qwen python3 -m venv /opt/qwen-collective/venv
  sudo -u qwen /opt/qwen-collective/venv/bin/pip install --upgrade pip
  sudo -u qwen /opt/qwen-collective/venv/bin/pip install -r requirements.txt
  ```
- [ ] **Create Dedicated Data Directory**:
  ```bash
  sudo mkdir -p /var/lib/qwen-collective/data
  sudo chown -R qwen:qwen /var/lib/qwen-collective
  sudo chmod 700 /var/lib/qwen-collective
  ```

---

## 3. Production Configuration (`.env`)

- [ ] **Generate Random Session Secret**:
  ```bash
  python3 -c "import secrets; print(secrets.token_urlsafe(32))"
  ```
- [ ] **Write Production Environment Configuration** to `/opt/qwen-collective/.env`:
  ```bash
  sudo -u qwen tee /opt/qwen-collective/.env << 'EOF'
  QC_APP_NAME="Qwen Collective"
  QC_ENV="production"
  QC_HOST="127.0.0.1"
  QC_PORT="8000"
  QC_DATA_DIR="/var/lib/qwen-collective/data"
  QC_SECRET_KEY="<paste-generated-32+-char-secret>"
  QC_ADMIN_PASSWORD="<unique-strong-password-min-12-chars>"
  QC_SESSION_EXPIRE_HOURS="72"
  QC_LOG_LEVEL="INFO"
  EOF
  sudo chmod 600 /opt/qwen-collective/.env
  ```
  *(Note: The application will abort startup if default passwords or insecure secrets are detected in `QC_ENV=production`).*

---

## 4. SSH Probe Configuration & Known Hosts

- [ ] **Create Dedicated Probe Key**:
  ```bash
  sudo -u qwen ssh-keygen -t ed25519 -f /opt/qwen-collective/.ssh/id_probe -N "" -C "qwen-collective-probe"
  ```
- [ ] **Distribute Public Key to Compute Nodes**:
  Install `/opt/qwen-collective/.ssh/id_probe.pub` into `~/.ssh/authorized_keys` on target Linux laptops.
- [ ] **Pre-seed `known_hosts`** (Strict host key checking is enforced; unknown keys are rejected):
  ```bash
  sudo -u qwen ssh-keyscan -H <laptop-1-ip> >> /opt/qwen-collective/.ssh/known_hosts
  sudo -u qwen ssh-keyscan -H <laptop-2-ip> >> /opt/qwen-collective/.ssh/known_hosts
  ```

---

## 5. Systemd Service Setup

- [ ] **Install Service Unit** to `/etc/systemd/system/qwen-collective.service`:
  ```ini
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

  # Hardening
  ProtectSystem=strict
  ProtectHome=true
  ReadWritePaths=/var/lib/qwen-collective/data
  PrivateTmp=true
  NoNewPrivileges=true

  [Install]
  WantedBy=multi-user.target
  ```
- [ ] **Enable & Start Service**:
  ```bash
  sudo systemctl daemon-reload
  sudo systemctl enable --now qwen-collective
  sudo systemctl status qwen-collective
  ```

---

## 6. Remote Access via Tailscale (No Public Ports)

- [ ] **Do NOT open public ports in firewall**.
- [ ] **Enable Tailscale Serve** for authenticated private access:
  ```bash
  tailscale serve --bg 8000
  ```
  Access the web interface securely over your Tailscale tailnet: `https://<control-node>.<tailnet>.ts.net`.

---

## 7. Automated Daily Backup Job

- [ ] **Install Daily Cron Backup** (`/etc/cron.daily/qwen-backup`):
  ```bash
  sudo tee /etc/cron.daily/qwen-backup << 'EOF'
  #!/bin/bash
  set -euo pipefail
  BACKUP_DIR="/var/backups/qwen-collective"
  DATE=$(date +%Y-%m-%d)
  mkdir -p "$BACKUP_DIR"

  # Online atomic SQLite snapshot
  sqlite3 /var/lib/qwen-collective/data/qwen_collective.db ".backup /tmp/qc_backup.db"

  # Archive knowledge notes and database
  tar -czf "$BACKUP_DIR/qwen_backup_$DATE.tar.gz" \
    -C /var/lib/qwen-collective/data knowledge \
    -C /tmp qc_backup.db

  rm -f /tmp/qc_backup.db
  # Keep 14 days of backups
  find "$BACKUP_DIR" -name "qwen_backup_*.tar.gz" -mtime +14 -delete
  EOF
  sudo chmod +x /etc/cron.daily/qwen-backup
  ```
