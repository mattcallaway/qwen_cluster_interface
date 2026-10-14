#!/usr/bin/env python3
"""
Qwen Collective - Scoped Privileged Sandbox Runner
Enforces least-privilege transient execution of approved worker tasks.

Security Invariants:
1. Replaces broad polkit rules (e.g. org.freedesktop.systemd1.manage-units).
2. Mandates non-bypassable operating-system sandbox flags:
   - DynamicUser=yes (ephemeral isolated UID/GID)
   - ProtectSystem=strict (read-only /usr, /boot, /etc)
   - ProtectHome=yes (inaccessible /home and /root)
   - PrivateTmp=yes (isolated temporary namespace)
   - IPAddressDeny=any (network disabled by default)
   - ReadWritePaths={workspace} (confined strictly to authorized workspace)
   - Cgroup limits: MemoryMax=512M, TasksMax=64
3. Path validation: workspace must be an absolute path under authorized scratch directories.
4. Prevents arbitrary command execution: executes only python3 against the entrypoint inside the workspace.
"""

import argparse
import os
from pathlib import Path
import subprocess
import sys

ALLOWED_WORKSPACE_PREFIXES = [
    "/tmp/qc_worker_exec_",
    "/var/tmp/qc_worker_exec_",
    "/opt/qwen-collective/workspaces/",
    "/opt/qwen-collective/workspaces",
]


def validate_workspace(workspace_str: str) -> Path:
    raw_path = Path(workspace_str)
    # Reject symlinked workspace paths to prevent directory redirection
    if raw_path.is_symlink():
        sys.stderr.write(f"Error: Workspace cannot be a symlink: {workspace_str}\n")
        sys.exit(126)

    ws = raw_path.resolve()
    if not ws.is_dir():
        sys.stderr.write(f"Error: Workspace directory does not exist: {ws}\n")
        sys.exit(126)

    is_valid = False

    # 1. /opt/qwen-collective/workspaces/<subdir>
    opt_base = Path("/opt/qwen-collective/workspaces").resolve()
    try:
        if ws != opt_base and ws.is_relative_to(opt_base):
            is_valid = True
    except (ValueError, AttributeError):
        pass

    # 2. /tmp/qc_worker_exec_<suffix> or /var/tmp/qc_worker_exec_<suffix>
    tmp_base = Path("/tmp").resolve()
    var_tmp_base = Path("/var/tmp").resolve()
    try:
        if (ws.parent == tmp_base or ws.parent == var_tmp_base) and ws.name.startswith("qc_worker_exec_"):
            is_valid = True
    except Exception:
        pass

    # 3. Allow controlled test scratch paths if specifically configured in test environment
    test_base = os.getenv("QC_ALLOWED_TEST_WORKSPACE")
    if test_base:
        try:
            t_base = Path(test_base).resolve()
            if ws != t_base and ws.is_relative_to(t_base):
                is_valid = True
        except Exception:
            pass

    if not is_valid:
        sys.stderr.write(f"Error: Workspace path '{ws}' is outside authorized prefixes.\n")
        sys.exit(126)

    return ws


def validate_entrypoint(workspace: Path, entrypoint_str: str) -> Path:
    ep_raw = Path(entrypoint_str)
    # Reject symlinks to prevent pointing to host files or sensitive paths
    if ep_raw.is_symlink():
        sys.stderr.write(f"Error: Entrypoint '{entrypoint_str}' cannot be a symlink.\n")
        sys.exit(126)

    ep = ep_raw.resolve()
    if not ep.is_file():
        sys.stderr.write(f"Error: Entrypoint file does not exist: {ep}\n")
        sys.exit(126)

    # Check for hardlinks: if st_nlink > 1, reject
    stat_res = ep.stat()
    if stat_res.st_nlink > 1:
        sys.stderr.write(f"Error: Entrypoint file has hardlinks (nlink={stat_res.st_nlink}).\n")
        sys.exit(126)

    # Entrypoint cannot be world-writable on POSIX
    if os.name != "nt" and (stat_res.st_mode & 0o002):
        sys.stderr.write(f"Error: Entrypoint file cannot be world-writable (mode={oct(stat_res.st_mode)}).\n")
        sys.exit(126)

    # Ensure entrypoint is strictly located inside the validated workspace
    try:
        ep.relative_to(workspace)
    except ValueError:
        sys.stderr.write(f"Error: Entrypoint '{ep}' is not inside workspace '{workspace}'.\n")
        sys.exit(126)

    return ep


def main():
    parser = argparse.ArgumentParser(description="Qwen Collective Scoped Sandbox Runner")
    parser.add_argument("--workspace", required=True, help="Path to isolated task workspace directory")
    parser.add_argument("--entrypoint", required=True, help="Path to entrypoint script inside workspace")
    parser.add_argument("--memory-mb", type=int, default=256, help="Memory limit in megabytes (min 64, max 512)")
    parser.add_argument("--tasks-max", type=int, default=32, help="Process count limit (min 10, max 64)")
    parser.add_argument("--timeout", type=int, default=300, help="Execution timeout in seconds (min 10, max 300)")

    args = parser.parse_args()

    # Ensure runner itself is owned by root and non-world-writable when running as root on Linux/POSIX
    if os.name != "nt" and os.geteuid() == 0:
        self_path = Path(__file__).resolve()
        stat_self = self_path.stat()
        if stat_self.st_uid != 0:
            sys.stderr.write(f"Security Alert: Launcher {self_path} must be owned by root (uid={stat_self.st_uid}).\n")
            sys.exit(126)
        if stat_self.st_mode & 0o022:
            sys.stderr.write(f"Security Alert: Launcher {self_path} must not be group/world writable (mode={oct(stat_self.st_mode)}).\n")
            sys.exit(126)

    # Sanitize root environment before invoking systemd-run
    for var in ["PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE", "LD_PRELOAD", "LD_LIBRARY_PATH"]:
        if var in os.environ:
            del os.environ[var]
    os.environ["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

    # Security validation of inputs
    workspace = validate_workspace(args.workspace)
    entrypoint = validate_entrypoint(workspace, args.entrypoint)

    # Enforce strict immutable upper bounds on resource limits
    mem_mb = min(max(args.memory_mb, 64), 512)
    tasks_max = min(max(args.tasks_max, 10), 64)
    timeout_sec = min(max(args.timeout, 10), 300)

    # Trusted host python binary
    python_bin = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else (sys.executable or "/usr/bin/python3")

    # Construct mandatory systemd-run command
    cmd = [
        "systemd-run",
        "--pipe",
        "--wait",
        "--collect",
        "-p", "DynamicUser=yes",
        "-p", "ProtectSystem=strict",
        "-p", "ProtectHome=yes",
        "-p", "PrivateTmp=yes",
        "-p", "IPAddressDeny=any",
        "-p", f"WorkingDirectory={workspace}",
        "-p", f"MemoryMax={mem_mb}M",
        "-p", f"TasksMax={tasks_max}",
        "-p", f"ReadWritePaths={workspace}",
        python_bin,
        str(entrypoint),
    ]

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(workspace),
            timeout=timeout_sec,
        )
        sys.exit(proc.returncode)
    except subprocess.TimeoutExpired:
        sys.stderr.write(f"Error: Execution timed out after {timeout_sec} seconds.\n")
        sys.exit(124)
    except Exception as e:
        sys.stderr.write(f"Error invoking systemd sandbox: {e}\n")
        sys.exit(126)


if __name__ == "__main__":
    main()
