# Qwen Collective — Rollback and Recovery Procedures

## 1. Overview

This document specifies the rollback and recovery procedures for each tier of the Qwen Collective infrastructure:
1. Windows Production Controller Service & Database
2. Linux VPS Worker Code & Virtual Environment
3. Linux Worker Systemd Service Unit
4. Scoped Execution Sandbox & Sudoers Rule
5. Worker Authentication & Enrollment Tokens

---

## 2. Windows Production Controller Rollback

### 2.1 Service Rollback
If the Windows controller exhibits unexpected behavior:
1. Stop the Windows service:
   ```powershell
   Stop-Service -Name QwenCollective
   ```
2. Revert the repository checkout to the approved baseline commit `2e569e1b2a58049e375f5ce2d262aa67a21232d3`:
   ```powershell
   cd "C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective"
   git checkout 2e569e1b2a58049e375f5ce2d262aa67a21232d3
   ```
3. Restart the Windows service:
   ```powershell
   Start-Service -Name QwenCollective
   ```

### 2.2 Database Recovery
If database corruption occurs or schema rollback is required:
1. Stop `QwenCollective` service.
2. Locate the automated timestamped pre-migration backup in `C:\Users\Matt C\.qwen-collective\data\backups\`.
3. Restore the backup:
   ```powershell
   Copy-Item "C:\Users\Matt C\.qwen-collective\data\backups\qwen_collective_schema_v7_*.db" "C:\Users\Matt C\.qwen-collective\data\qwen_collective.db" -Force
   ```
4. Verify database schema version:
   ```powershell
   & "C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective\.venv\Scripts\python.exe" -c "import sqlite3; c=sqlite3.connect('C:/Users/Matt C/.qwen-collective/data/qwen_collective.db'); print(c.execute('PRAGMA user_version;').fetchone()[0])"
   ```
5. Restart the service.

---

## 3. Linux VPS Worker Code Rollback

If updating `/opt/qwen-collective/source` on `qwen-vps` introduces a regression:
1. Connect via SSH to `qwen-vps`.
2. Stop the worker service:
   ```bash
   sudo systemctl stop qwen-worker.service
   ```
3. Check out the previously approved revision (`b15d370e80785009b303d817c65a34c3692f2aa4`):
   ```bash
   cd /opt/qwen-collective/source
   sudo git checkout b15d370e80785009b303d817c65a34c3692f2aa4
   ```
4. Verify file permissions:
   ```bash
   sudo chown -R root:root /opt/qwen-collective/source
   sudo chmod -R 755 /opt/qwen-collective/source
   ```

---

## 4. Linux Systemd Unit Rollback

If `/etc/systemd/system/qwen-worker.service` needs reversion:
1. Restore the prior unit configuration:
   ```bash
   sudo cp /opt/qwen-collective/source/service/qwen-worker.service.backup /etc/systemd/system/qwen-worker.service
   ```
2. Reload systemd daemon:
   ```bash
   sudo systemctl daemon-reload
   ```
3. Verify status:
   ```bash
   systemctl status qwen-worker.service
   ```

---

## 5. Scoped Sandbox Launcher & Sudoers Rollback

### 5.1 Launcher Rollback
If `/usr/local/bin/qwen-sandbox-runner` is damaged:
1. Re-copy the version-controlled script from source:
   ```bash
   sudo cp /opt/qwen-collective/source/scripts/qwen-sandbox-runner.py /usr/local/bin/qwen-sandbox-runner
   sudo chown root:root /usr/local/bin/qwen-sandbox-runner
   sudo chmod 755 /usr/local/bin/qwen-sandbox-runner
   ```
2. Verify execution permissions:
   ```bash
   /usr/local/bin/qwen-sandbox-runner --help
   ```

### 5.2 Sudoers Rollback
If `/etc/sudoers.d/qwen-worker` has a syntax error or requires removal:
1. Remove or restore the sudoers rule:
   ```bash
   sudo rm -f /etc/sudoers.d/qwen-worker
   ```
2. Validate sudoers syntax:
   ```bash
   sudo visudo -c
   ```

---

## 6. Authentication Credential Revocation & Reset

### 6.1 Revoking a Compromised Worker Token
If a worker token is compromised or leaked:
1. On the Windows controller (via web interface or CLI):
   - Access `http://127.0.0.1:8000/infrastructure`.
   - Identify the active worker token and click **Revoke**.
2. Immediately, the controller marks the token revoked in SQLite.
3. The next worker heartbeat or task poll will receive `401 Unauthorized` and halt all polling operations.

### 6.2 Clearing Worker State on VPS
To completely reset worker enrollment credentials on `qwen-vps`:
```bash
sudo rm -f /opt/qwen-collective/data/worker_token.json
sudo rm -f /opt/qwen-collective/data/worker_buffer.db
```
The worker will enter an unauthenticated state and await a new enrollment token.
