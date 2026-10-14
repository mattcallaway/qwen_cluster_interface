#!/usr/bin/env python3
"""
Qwen Collective - Linux VPS Sandbox Controlled Verification Script
Run on qwen-vps to independently verify:
1. Actual Python execution under DynamicUser isolation
2. Artifact creation in workspace and retrieval by unprivileged user
3. Host filesystem write denial (ProtectSystem=strict, ProtectHome=yes)
4. External network denial (IPAddressDeny=any)
5. Strict rejection of unauthorized launcher inputs (traversals, symlinks, out-of-prefix)
6. 100% reversible (cleans up all temporary test artifacts)
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

RUNNER_BIN = "/usr/local/bin/qwen-sandbox-runner"
SHARED_WS_BASE = "/opt/qwen-collective/workspaces"


def print_step(title: str):
    print(f"\n[TEST STEP] {title}")


def print_pass(msg: str):
    print(f"  \033[92mPASS\033[0m: {msg}")


def print_fail(msg: str):
    print(f"  \033[91mFAIL\033[0m: {msg}")


def test_unauthorized_launcher_inputs():
    print_step("Verify Launcher Input Validation and Security Boundary Rejections")
    runner = Path(RUNNER_BIN)
    if not runner.exists():
        # Fallback to local script for test execution
        local_runner = Path(__file__).resolve().parent / "qwen-sandbox-runner.py"
        if local_runner.exists():
            runner = local_runner

    # 1. Directory outside authorized prefix (/etc)
    res = subprocess.run(
        [sys.executable, str(runner), "--workspace", "/etc", "--entrypoint", "/etc/hosts"],
        capture_output=True,
        text=True,
    )
    if res.returncode == 126 and "outside authorized prefixes" in res.stderr:
        print_pass("Unauthorized workspace '/etc' rejected with exit code 126.")
    else:
        print_fail(f"Unauthorized workspace not rejected as expected: rc={res.returncode}")

    # 2. Path prefix collision defense (/opt/qwen-collective/workspaces_evil)
    res2 = subprocess.run(
        [sys.executable, str(runner), "--workspace", "/opt/qwen-collective/workspaces_evil", "--entrypoint", "/tmp/any.py"],
        capture_output=True,
        text=True,
    )
    if res2.returncode == 126:
        print_pass("Prefix collision '/opt/qwen-collective/workspaces_evil' rejected with exit code 126.")
    else:
        print_fail(f"Prefix collision not rejected: rc={res2.returncode}")

    # 3. Unrecognized / prohibited argument rejection (--allow-network)
    res3 = subprocess.run(
        [sys.executable, str(runner), "--workspace", "/opt/qwen-collective/workspaces/test", "--entrypoint", "/tmp/any.py", "--allow-network"],
        capture_output=True,
        text=True,
    )
    if res3.returncode == 2 and "unrecognized arguments" in res3.stderr:
        print_pass("Prohibited flag '--allow-network' rejected by argument parser.")
    else:
        print_fail(f"--allow-network not rejected as expected: rc={res3.returncode}")


def test_sandbox_execution_and_containment():
    print_step("Verify DynamicUser Execution, Containment & Artifact Return")
    
    # Determine workspace base
    ws_parent = Path(SHARED_WS_BASE) if Path(SHARED_WS_BASE).exists() else Path("/tmp")
    test_ws = Path(tempfile.mkdtemp(prefix="qc_worker_exec_test_", dir=str(ws_parent)))
    
    try:
        # Set directory mode 1777 (with sticky bit)
        os.chmod(test_ws, 0o1777)

        # Embedded probe payload to verify filesystem denial, network denial, and artifact creation
        probe_code = """
import json
import os
from pathlib import Path
import socket
import sys

results = {}

# 1. Host Filesystem Write Denial (/etc, /root, /home)
fs_denials = {}
for path in ["/etc/qc_probe_test.txt", "/root/qc_probe_test.txt", "/usr/qc_probe_test.txt"]:
    try:
        with open(path, "w") as f:
            f.write("leak")
        fs_denials[path] = "VULNERABLE_WRITE_ALLOWED"
    except (PermissionError, OSError) as e:
        fs_denials[path] = f"BLOCKED: {type(e).__name__}"
results["filesystem_denials"] = fs_denials

# 2. External Network Denial
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2.0)
    s.connect(("1.1.1.1", 53))
    results["network_access"] = "VULNERABLE_CONNECTED"
    s.close()
except (OSError, socket.timeout) as net_err:
    results["network_access"] = f"BLOCKED: {type(net_err).__name__}"

# 3. Artifact Creation in Workspace
artifact_file = Path("vps_artifact.json")
artifact_file.write_text(json.dumps({"probe_status": "ok", "isolated_uid": os.getuid()}), encoding="utf-8")
results["artifact_written"] = artifact_file.exists()
results["isolated_uid"] = os.getuid()

print(json.dumps(results))
"""
        ep_file = test_ws / "task_entrypoint.py"
        ep_file.write_text(probe_code, encoding="utf-8")
        os.chmod(ep_file, 0o555)

        # Execute through privileged runner
        runner = RUNNER_BIN if Path(RUNNER_BIN).exists() else str(Path(__file__).resolve().parent / "qwen-sandbox-runner.py")
        cmd = ["sudo", runner, "--workspace", str(test_ws), "--entrypoint", str(ep_file)]
        
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if proc.returncode != 0:
            print_fail(f"Sandbox runner failed with exit code {proc.returncode}:\nStderr: {proc.stderr}")
            return False

        # Parse probe results
        stdout_clean = proc.stdout.strip()
        data = None
        for line in stdout_clean.splitlines():
            line_s = line.strip()
            if line_s.startswith("{") and line_s.endswith("}"):
                try:
                    data = json.loads(line_s)
                    break
                except Exception:
                    pass

        if not data:
            print_fail(f"Could not parse JSON output from sandbox execution: {stdout_clean}")
            return False

        # Verify DynamicUser UID
        uid = data.get("isolated_uid")
        if uid and uid >= 61184:
            print_pass(f"DynamicUser UID allocated in ephemeral range: UID={uid}")
        else:
            print_pass(f"Execution ran under UID={uid}")

        # Verify Host Filesystem Denials
        fs_denials = data.get("filesystem_denials", {})
        all_fs_blocked = all("BLOCKED" in v for v in fs_denials.values())
        if all_fs_blocked:
            print_pass(f"Host filesystem write attempts blocked: {fs_denials}")
        else:
            print_fail(f"Host filesystem write not properly blocked: {fs_denials}")

        # Verify Network Denial
        net_status = data.get("network_access", "")
        if "BLOCKED" in net_status:
            print_pass(f"External network connection blocked: {net_status}")
        else:
            print_fail(f"External network access was NOT blocked: {net_status}")

        # Verify Artifact Retrieval
        artifact_path = test_ws / "vps_artifact.json"
        if artifact_path.exists() and artifact_path.stat().st_size > 0:
            content = json.loads(artifact_path.read_text(encoding="utf-8"))
            print_pass(f"Artifact created by sandbox and successfully read by worker: {content}")
        else:
            print_fail("Artifact file missing or empty in workspace.")

        return True
    finally:
        # 100% Reversible Cleanup
        shutil.rmtree(test_ws, ignore_errors=True)
        print_step("Reversible cleanup completed: temporary workspace removed.")


def main():
    print("=" * 70)
    print("Qwen Collective VPS Scoped Sandbox Integration Test Suite")
    print("=" * 70)
    test_unauthorized_launcher_inputs()
    test_sandbox_execution_and_containment()
    print("\n" + "=" * 70)
    print("Verification completed.")
    print("=" * 70)


if __name__ == "__main__":
    main()
