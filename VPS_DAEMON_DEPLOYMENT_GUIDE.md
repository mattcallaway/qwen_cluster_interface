# Qwen Collective — VPS Worker Daemon & Systemd Service Deployment Guide

**Target Host**: `qwen-vps` (Ubuntu 24.04 LTS, Tailscale IP `100.71.194.68`)  
**Worker Account**: `qwen-worker` (UID 999, GID 999)  
**Controller Endpoint**: `https://desktop-44h6qvp.tailb5d90b.ts.net` (Tailscale HTTPS)  
**Daemon Module**: `python -m vps_agent`  

---

## 1. Architecture & Security Model

The VPS worker operates as an outbound pull-based agent:
1. **Unprivileged Execution**: Runs under dedicated service account `qwen-worker` (`/opt/qwen-collective`).
2. **Strict Class B Sandboxing**:
   - Tasks execute via the root-owned, scoped privileged runner `/usr/local/bin/qwen-sandbox-runner`.
   - DynamicUser UID isolation, read-only rootfs (`ProtectSystem=strict`), private tmp, network denial (`IPAddressDeny=any`), working directory propagation (`-p WorkingDirectory={workspace}`), and CPU/memory ceilings.
   - Restricted sudoers permits strictly the scoped runner (`qwen-worker ALL=(ALL) NOPASSWD: /usr/local/bin/qwen-sandbox-runner *`).
   - Production fails closed if the approved runner is absent or tampered with.
3. **Authentication Gating**:
   - `run_cycle()` enforces authentication before any task polling, heartbeat, or buffered result flush.
   - Single-use enrollment token provisions a persistent worker Bearer token (`/opt/qwen-collective/data/worker_token.json`, mode 0600).
   - Once enrolled, the enrollment credential is scrubbed from memory and configuration files.
   - On 401/403 rejection, task polling halts immediately and follows bounded exponential backoff (up to 60s) without tight restart loops.
   - Pending offline buffer records are strictly preserved during outages or credential revocation.

---

## 2. Pre-Installation Host Verification

On `qwen-vps`, verify existing directory layout, permissions, and sandbox runner:

```bash
# 1. Verify directory layout and permissions
ls -ld /opt/qwen-collective
ls -ld /opt/qwen-collective/data          # Expected: drwx------ (0700) qwen-worker:qwen-worker
ls -ld /opt/qwen-collective/workspaces    # Expected: drwx--x--x (0711) qwen-worker:qwen-worker

# 2. Verify scoped sandbox runner
ls -la /usr/local/bin/qwen-sandbox-runner # Expected: -rwxr-xr-x (0755) root:root
sudo -l -U qwen-worker                    # Expected: (ALL) NOPASSWD: /usr/local/bin/qwen-sandbox-runner *

# 3. Test virtual environment and Python entrypoint
sudo -u qwen-worker /opt/qwen-collective/.venv/bin/python -m vps_agent --help
```

---

## 3. Step-by-Step Installation Procedure

### Step 3.1: Create Configuration Directory & Environment File

```bash
# 1. Create protected configuration directory
sudo mkdir -p /etc/qwen-collective
sudo chmod 0700 /etc/qwen-collective
sudo chown qwen-worker:qwen-worker /etc/qwen-collective

# 2. Copy the environment template
sudo cp /opt/qwen-collective/source/service/worker.env.example /etc/qwen-collective/worker.env
sudo chown qwen-worker:qwen-worker /etc/qwen-collective/worker.env
sudo chmod 0600 /etc/qwen-collective/worker.env
```

### Step 3.2: Install and Verify Systemd Service Unit

```bash
# 1. Copy unit file from source repository
sudo cp /opt/qwen-collective/source/service/qwen-worker.service /etc/systemd/system/qwen-worker.service
sudo chmod 0644 /etc/systemd/system/qwen-worker.service
sudo chown root:root /etc/systemd/system/qwen-worker.service

# 2. Verify unit file syntax
systemd-analyze verify /etc/systemd/system/qwen-worker.service

# 3. Reload systemd manager configuration
sudo systemctl daemon-reload
```

---

## 4. Worker Enrollment & First Launch

### Step 4.1: Generate Single-Use Enrollment Token on Controller
On the Windows controller (via web UI `/infrastructure` or authenticated admin API):
- Create a single-use enrollment token restricted to node ID `qwen-vps` with 30-minute validity.

### Step 4.2: Supply Credential Securely to VPS
Without leaving the secret in shell history, configure `QC_ENROLLMENT_TOKEN`:

```bash
# Use read -s to input token without bash history or console echo:
read -s -p "Enter enrollment token: " ENR_TOKEN
echo ""
sudo -u qwen-worker sed -i "s|^# QC_ENROLLMENT_TOKEN=.*|QC_ENROLLMENT_TOKEN=${ENR_TOKEN}|" /etc/qwen-collective/worker.env
unset ENR_TOKEN
```

### Step 4.3: Dry-Run Single Cycle Validation
Verify enrollment, token persistence, and config scrubbing:

```bash
sudo -u qwen-worker /opt/qwen-collective/.venv/bin/python -m vps_agent \
    --config /etc/qwen-collective/worker.env \
    --once
```

Expected output:
- `Provisioned and securely persisted new worker token for node qwen-vps`
- `Single-cycle execution finished with status: active`

Verify persisted token file:
```bash
ls -l /opt/qwen-collective/data/worker_token.json  # Mode: 0600 (-rw-------), owner qwen-worker
grep -i "QC_ENROLLMENT_TOKEN" /etc/qwen-collective/worker.env  # Should show: # QC_ENROLLMENT_TOKEN consumed and scrubbed
```

---

## 5. Service Operations & Monitoring

### Start and Enable Service
```bash
sudo systemctl enable --now qwen-worker.service
```

### Inspect Status & Health
```bash
# Service status
sudo systemctl status qwen-worker.service

# Real-time journal logs
sudo journalctl -u qwen-worker.service -f -o cat
```

### Stop / Restart Service
```bash
sudo systemctl restart qwen-worker.service
sudo systemctl stop qwen-worker.service
```

---

## 6. Token Revocation & Controlled Re-Enrollment

If credentials are compromised or re-provisioning is required:
1. **Controller Revocation**:
   Administrator clicks **Revoke** on `qwen-vps` or calls `DELETE /api/v1/worker/tokens/{token_id}`.
2. **Daemon Reaction**:
   Next cycle receives HTTP 401, immediately pauses task leasing, and backs off.
3. **Re-Enrollment on VPS**:
   ```bash
   # Stop worker service
   sudo systemctl stop qwen-worker.service

   # Remove stale persisted token
   sudo -u qwen-worker rm -f /opt/qwen-collective/data/worker_token.json

   # Supply fresh enrollment token into /etc/qwen-collective/worker.env
   read -s -p "Enter fresh enrollment token: " ENR_TOKEN
   echo ""
   sudo -u qwen-worker sed -i "s|.*QC_ENROLLMENT_TOKEN.*|QC_ENROLLMENT_TOKEN=${ENR_TOKEN}|" /etc/qwen-collective/worker.env
   unset ENR_TOKEN

   # Start worker service
   sudo systemctl start qwen-worker.service
   ```

---

## 7. Service Safe Removal / Rollback

To cleanly disable and remove the service:

```bash
sudo systemctl stop qwen-worker.service
sudo systemctl disable qwen-worker.service
sudo rm -f /etc/systemd/system/qwen-worker.service
sudo systemctl daemon-reload
sudo systemctl reset-failed
```
