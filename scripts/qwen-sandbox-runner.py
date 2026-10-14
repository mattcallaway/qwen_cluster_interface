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
    ws = Path(workspace_str).resolve()
    if not ws.is_dir():
        sys.stderr.write(f"Error: Workspace directory does not exist: {ws}\n")
        sys.exit(126)

    # Ensure path starts with an authorized prefix
    ws_str = str(ws)
    if not any(ws_str.startswith(prefix) for prefix in ALLOWED_WORKSPACE_PREFIXES):
        sys.stderr.write(f"Error: Workspace path '{ws_str}' is outside authorized prefixes.\n")
        sys.exit(126)

    return ws


def validate_entrypoint(workspace: Path, entrypoint_str: str) -> Path:
    ep = Path(entrypoint_str).resolve()
    if not ep.is_file():
        sys.stderr.write(f"Error: Entrypoint file does not exist: {ep}\n")
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
    parser.add_argument("--memory-mb", type=int, default=512, help="Memory limit in megabytes (max 512)")
    parser.add_argument("--tasks-max", type=int, default=64, help="Process count limit (max 64)")
    parser.add_argument("--timeout", type=int, default=600, help="Execution timeout in seconds")
    parser.add_argument("--allow-network", action="store_true", default=False, help="Allow external network access")

    args = parser.parse_args()

    # Security validation of inputs
    workspace = validate_workspace(args.workspace)
    entrypoint = validate_entrypoint(workspace, args.entrypoint)

    # Enforce hard upper bounds on resource limits
    mem_mb = min(max(args.memory_mb, 64), 512)
    tasks_max = min(max(args.tasks_max, 10), 64)

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
        "-p", f"MemoryMax={mem_mb}M",
        "-p", f"TasksMax={tasks_max}",
        "-p", f"ReadWritePaths={workspace}",
    ]

    if not args.allow_network:
        cmd.extend(["-p", "IPAddressDeny=any"])

    cmd.extend([
        sys.executable if sys.executable else "/usr/bin/python3",
        str(entrypoint),
    ])

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(workspace),
            timeout=args.timeout,
        )
        sys.exit(proc.returncode)
    except subprocess.TimeoutExpired:
        sys.stderr.write(f"Error: Execution timed out after {args.timeout} seconds.\n")
        sys.exit(124)
    except Exception as e:
        sys.stderr.write(f"Error invoking systemd sandbox: {e}\n")
        sys.exit(126)


if __name__ == "__main__":
    main()
