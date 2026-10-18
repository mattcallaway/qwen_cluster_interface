# Qwen Collective — Service & Execution Sandbox Hardening Report

## 1. Overview

This report provides the technical audit and verification details for the Linux service architecture, filesystem permission model, sudoers elevation controls, and transient execution sandbox isolating tasks on `qwen-vps`.

---

## 2. Linux Systemd Unit Audit (`qwen-worker.service`)

The systemd service unit template (`service/qwen-worker.service`) was audited against standard security benchmarks:

### 2.1 Directive Analysis

| Directive | Configured Value | Security Justification |
| :--- | :--- | :--- |
| `User` / `Group` | `qwen-worker` / `qwen-worker` | Daemon runs under an unprivileged dedicated service account (UID 999). |
| `WorkingDirectory` | `/opt/qwen-collective/source` | Sets context for reading code files. The directory is owned by `root:root` with mode `0755` (read-only for the worker). |
| `EnvironmentFile` | `/etc/qwen-collective/worker.env` | Config file mode `0600`, owned by `qwen-worker:qwen-worker`. Restricts credential exposure. |
| `ExecStart` | `/opt/qwen-collective/.venv/bin/python -m vps_agent` | Explicit virtual environment Python path; eliminates reliance on ambient `$PATH`. |
| `StartLimitIntervalSec` / `StartLimitBurst` | `300s` / `5` | Placed under `[Unit]` section to throttle rapid crash loops (5 attempts in 5 minutes). |
| `ProtectSystem` | `full` | Mounts `/usr`, `/boot`, and `/etc` read-only for the worker process. |
| `ProtectHome` | `read-only` | Prevents writing to `/home` or `/root`. |
| `ReadWritePaths` | `/opt/qwen-collective/data /opt/qwen-collective/workspaces /tmp` | Explicit allowlist of writable paths. Writing outside these locations is blocked by the kernel. |
| `PrivateTmp` | `true` | Mounts an isolated namespace for `/tmp` and `/var/tmp`. |
| `ProtectControlGroups` | `true` | Read-only access to `/sys/fs/cgroup`. |
| `ProtectKernelModules` | `true` | Denies loading kernel modules. |
| `ProtectKernelTunables` | `true` | Denies modifying `/proc/sys`, `/sys`, etc. |
| `RestrictRealtime` | `true` | Prevents scheduling priority escalation. |
| `LockPersonality` | `true` | Locks execution domain. |
| `PrivateDevices` | `true` | Denies access to physical devices under `/dev` (creates minimalist `/dev` namespace). |
| `NoNewPrivileges` | `no` | **Required exception**: `qwen-worker` must execute `sudo /usr/local/bin/qwen-sandbox-runner`. Sudo setuid requires `NoNewPrivileges=no`. |

---

## 3. Filesystem Permissions & Directory Model

| Path | Owner | Mode | Purpose | Security Controls |
| :--- | :--- | :--- | :--- | :--- |
| `/opt/qwen-collective/source` | `root:root` | `0755` | Repository source checkout | Read-only to `qwen-worker`. Worker cannot alter its own code. |
| `/opt/qwen-collective/data` | `qwen-worker:qwen-worker` | `0700` | SQLite buffer, persistent token | Protected against other local accounts. |
| `/opt/qwen-collective/workspaces` | `qwen-worker:qwen-worker` | `0711` | Per-task execution workspaces | Traversal permitted; per-task directory isolation. |
| `/etc/qwen-collective/worker.env` | `qwen-worker:qwen-worker` | `0600` | Service configuration & tokens | Readable only by `qwen-worker`. |
| `/usr/local/bin/qwen-sandbox-runner` | `root:root` | `0755` | Scoped privileged sandbox launcher | Immutable by `qwen-worker`. |
| `/etc/sudoers.d/qwen-worker` | `root:root` | `0440` | Restricted sudo elevation rule | Immutable by `qwen-worker`. Validated via `visudo -c`. |

---

## 4. Scoped Privileged Launcher (`qwen-sandbox-runner`)

Rather than granting broad polkit permissions (`manage-units`) or unrestricted sudo access, task isolation is delegated to `/usr/local/bin/qwen-sandbox-runner`.

### 4.1 Security Properties Enforced by the Launcher

1. **Path Canonicalization & Workspace Containment**:
   - The workspace path must be an absolute path inside `/opt/qwen-collective/workspaces/`.
   - Symlinks and `..` path traversals are resolved and rejected if they point outside the designated workspace parent.
2. **Network Isolation**:
   - The launcher unconditionally specifies `-p IPAddressDeny=any` to systemd.
   - The execution unit has no access to loopback, tailnet, LAN, or public internet.
3. **Dynamic User Isolation**:
   - Enforces `-p DynamicUser=yes`.
   - The command executes under an ephemeral UID/GID allocated by systemd from the reserved range `61184-65519`.
   - The dynamic UID has no access to `qwen-worker` files, data directory, or system files.
4. **Filesystem Protection**:
   - `-p ProtectSystem=strict`: Root filesystem mounted completely read-only.
   - `-p ProtectHome=yes`: `/root` and `/home` inaccessible.
   - `-p ReadWritePaths={workspace}`: Only the specific workspace directory is writable.
5. **Resource Limits & Bounded Execution**:
   - Memory ceiling: Max 1024 MB (`MemoryMax=1024M`).
   - Process limit: Max 128 tasks (`TasksMax=128`).
   - Timeout: Enforced via `TimeoutStopSec` and Python process watchdog.
6. **Transient Unit Lifecycle**:
   - Executes via `systemd-run --scope` or transient `.service` unit with `--wait --collect`.
   - Unit is automatically cleaned up and garbage collected upon termination.

---

## 5. Sudoers Rule Verification (`/etc/sudoers.d/qwen-worker`)

The installed sudoers file contains:

```sudoers
qwen-worker ALL=(ALL) NOPASSWD: /usr/local/bin/qwen-sandbox-runner
```

- **Verification**:
  - File permissions: Mode `0440`, owned by `root:root`.
  - Syntax check: `visudo -cf /etc/sudoers.d/qwen-worker` exits 0.
  - Least privilege: `qwen-worker` cannot execute any command other than `/usr/local/bin/qwen-sandbox-runner`.
  - Cannot execute `bash`, `sh`, `systemctl`, `systemd-run`, or arbitrary binaries via sudo.
