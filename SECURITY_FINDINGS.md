# Qwen Collective — Security Findings & Risk Analysis

## 1. Finding Overview & Severity Matrix

| ID | Title | Severity | Status | Scope |
| :--- | :--- | :--- | :--- | :--- |
| **SEC-01** | Public Port 22 SSH Exposure with Root Password Authentication | **HIGH** | Open (Action Plan Ready) | VPS Host OS |
| **SEC-02** | Configuration Loading Defect Causing Fallback to Insecure Source Path | **MEDIUM** | **REPAIRED** (`8dee5a2`) | Worker Daemon |
| **SEC-03** | Systemd Restart Rate Limiting Directives Ignored in `[Service]` | **LOW** | **REPAIRED** (`8dee5a2`) | Service Unit |
| **SEC-04** | Execution Sandbox Launcher & Sudoers Least-Privilege Architecture | **INFORMATIONAL** | **VERIFIED HARDENED** | Sandbox Jail |
| **SEC-05** | Worker Enrollment & Single-Use Token Cryptographic Binding | **INFORMATIONAL** | **VERIFIED HARDENED** | Controller API |

---

## 2. Detailed Findings

---

### SEC-01: Public Port 22 SSH Exposure with Root Password Authentication

- **Severity**: **HIGH**
- **Affected Host**: Linux VPS (`172.233.140.74` / `100.71.194.68`)
- **Evidence**:
  - Live probe against `172.233.140.74:22`:
    ```text
    root@172.233.140.74: Permission denied (publickey,password).
    ```
  - Port 22 is reachable from the public internet without firewall restrictions.
  - SSH daemon advertises `password` as an accepted authentication method for `root`.
- **Root Cause**: Default Ubuntu VPS provisioning configured SSH listening on `0.0.0.0:22` with `PermitRootLogin yes` (or `prohibit-password` overridden) and `PasswordAuthentication yes`.
- **Security Impact**: Vulnerable to brute-force attacks and credential compromise from automated internet scanners.
- **Recommended Correction**:
  1. Provision a dedicated administrative account with sudo access (e.g. `qwen-admin`).
  2. Install an Ed25519 public key for the administrative user.
  3. Verify successful public-key authentication in a separate terminal session before making any service changes.
  4. In `/etc/ssh/sshd_config.d/50-cloud-init.conf` or `/etc/ssh/sshd_config`:
     ```ini
     PermitRootLogin no
     PasswordAuthentication no
     KbdInteractiveAuthentication no
     ```
  5. Bind SSH to listen on the Tailscale interface (`100.71.194.68:22`) or configure UFW to allow port 22 only from authorized administrative IPs / Tailnet.
- **Precautionary Gate**: **Do not disable root SSH or password authentication until an alternative verified administrative session is established and explicitly authorized by the user.**

---

### SEC-02: Configuration Loading Defect Causing Fallback to Insecure Source Path

- **Severity**: **MEDIUM**
- **Affected Component**: `vps_agent/config.py`
- **Evidence**:
  - Running `python -m vps_agent --config /etc/qwen-collective/worker.env --validate` resulted in `Permission denied: '/opt/qwen-collective/source/worker_data'`.
- **Root Cause**:
  1. Silent failure importing `python-dotenv` due to missing requirement in `requirements.txt`.
  2. Unconditional `mkdir()` executed inside `load_config()`.
  3. Fallback path defaulted to relative `./worker_data`, resolving to the root-owned source checkout.
- **Security Impact**:
  - Worker could attempt to create or write files inside the code checkout rather than dedicated isolated storage.
  - Failure to validate configuration prior to credential provisioning.
- **Remediation**:
  - Built zero-dependency parser `parse_env_file()`.
  - Removed all filesystem mutations from `load_config()`.
  - Added source tree boundary detection in `validate_config()` to reject any data directory residing inside the git repository.
  - Updated default data directory to `/opt/qwen-collective/data` on Linux.
  - Added support for `--validate` with pending credentials.
- **Files Changed**:
  - `vps_agent/config.py`
  - `vps_agent/__main__.py`
  - `requirements.txt`
  - `tests/test_vps_daemon.py`
- **Tests Added**: 6 regression unit tests in `tests/test_vps_daemon.py` (all passing).

---

### SEC-03: Systemd Restart Rate Limiting Directives Ignored in `[Service]`

- **Severity**: **LOW**
- **Affected Component**: `service/qwen-worker.service`
- **Evidence**:
  - Lines in template:
    ```ini
    [Service]
    StartLimitIntervalSec=300s
    StartLimitBurst=5
    ```
- **Root Cause**: In modern versions of systemd (>=230), `StartLimitIntervalSec` and `StartLimitBurst` belong in the `[Unit]` section. When placed under `[Service]`, systemd logs a warning or ignores the directives, leaving services vulnerable to rapid restart loops.
- **Security Impact**: Service could fail to throttle restarts upon continuous crashing, generating excess journal logs or CPU load.
- **Remediation**: Moved `StartLimitIntervalSec=300s` and `StartLimitBurst=5` under `[Unit]` in `service/qwen-worker.service`.
- **Files Changed**: `service/qwen-worker.service`.

---

### SEC-04: Execution Sandbox Launcher & Sudoers Least-Privilege Architecture

- **Severity**: **INFORMATIONAL / VERIFIED HARDENED**
- **Affected Component**: `scripts/qwen-sandbox-runner.py` & `/etc/sudoers.d/qwen-worker`
- **Audit Findings**:
  - Root-owned launcher at `/usr/local/bin/qwen-sandbox-runner` (mode 0755).
  - Sudoers rule strictly permits:
    ```sudoers
    qwen-worker ALL=(ALL) NOPASSWD: /usr/local/bin/qwen-sandbox-runner
    ```
  - No arguments wildcards permitted.
  - The launcher enforces:
    - Mandatory `DynamicUser=yes` (transient unprivileged UID between 61184-65519).
    - `ProtectSystem=strict` (read-only filesystem).
    - `ProtectHome=yes` (no access to `/root` or `/home`).
    - `PrivateTmp=yes` (isolated `/tmp`).
    - `IPAddressDeny=any` (complete outbound and inbound network block).
    - `WorkingDirectory={workspace}`.
    - Rigid resource ceilings (memory max 1024MB, tasks max 128, timeout max 1200s).
- **Status**: Production Linux sandbox verification test previously passed with exit code 0.

---

### SEC-05: Worker Enrollment & Single-Use Token Cryptographic Binding

- **Severity**: **INFORMATIONAL / VERIFIED HARDENED**
- **Affected Component**: `/api/v1/worker/register` & `app/services/worker.py`
- **Audit Findings**:
  - Single-use enrollment tokens: Tokens are verified and immediately consumed; replay attempts return `403 Forbidden`.
  - Authorization before mutation: Zero database records are created or altered unless the enrollment token or admin session is validated first.
  - Secret protection: Tokens are stored as SHA-256 hashes in SQLite; raw tokens are never logged or stored in plain text.
  - Credential persistence: Worker saves received `worker_token` to `/opt/qwen-collective/data/worker_token.json` with mode `0600`.
  - Scrubbing: `QC_ENROLLMENT_TOKEN` is scrubbed from the environment file immediately upon successful registration.
- **Status**: Verified by 10 dedicated automated integration tests.
