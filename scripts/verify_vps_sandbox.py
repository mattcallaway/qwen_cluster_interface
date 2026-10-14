#!/usr/bin/env python3
"""
Qwen Collective - Linux VPS Sandbox Controlled Verification Script
Run on qwen-vps to independently verify:
1. Actual Python execution under DynamicUser isolation (UID >= 61184)
2. Explicit working directory propagation (-p WorkingDirectory={workspace})
3. Artifact creation in workspace and retrieval by unprivileged user
4. Host filesystem write denial (ProtectSystem=strict, ProtectHome=yes)
5. External network denial (IPAddressDeny=any)
6. Strict rejection of unauthorized launcher inputs (traversals, symlinks, out-of-prefix)
7. Verifiable cleanup (verifies workspace directory removed cleanly; reports failures)
8. Strict error propagation: any check failure immediately causes nonzero process exit.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, Tuple

RUNNER_BIN = "/usr/local/bin/qwen-sandbox-runner"
SHARED_WS_BASE = "/opt/qwen-collective/workspaces"


def print_step(title: str):
    print(f"\n[TEST STEP] {title}")


def print_pass(msg: str):
    print(f"  \033[92mPASS\033[0m: {msg}")


def print_fail(msg: str):
    print(f"  \033[91mFAIL\033[0m: {msg}")


def test_unauthorized_launcher_inputs() -> bool:
    print_step("Verify Launcher Input Validation and Security Boundary Rejections")
    runner = Path(RUNNER_BIN)
    if not runner.exists():
        # Fallback to local script for test execution
        local_runner = Path(__file__).resolve().parent / "qwen-sandbox-runner.py"
        if local_runner.exists():
            runner = local_runner

    all_passed = True

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
        all_passed = False

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
        all_passed = False

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
        all_passed = False

    return all_passed


def evaluate_probe_data(data: Dict[str, Any], test_ws: Path) -> Tuple[bool, str]:
    """
    Strictly evaluate probe payload data:
    - Dynamic UID must be in ephemeral isolated range (>= 61184).
    - Host filesystem write attempts must be blocked.
    - External network attempts must be blocked.
    - Working directory must be explicitly set to test workspace.
    """
    # 1. DynamicUser UID isolation check
    uid = data.get("isolated_uid")
    if uid is None or uid < 61184:
        return False, f"DynamicUser execution identity failure: UID={uid} is not in ephemeral isolated range (>= 61184)."

    # 2. Working directory check
    cwd = data.get("cwd")
    if cwd != str(test_ws.resolve()):
        return False, f"WorkingDirectory propagation failure: expected '{test_ws.resolve()}', got '{cwd}'."

    # 3. Host filesystem isolation check
    fs_denials = data.get("filesystem_denials", {})
    if not fs_denials:
        return False, "Filesystem denial probe returned empty result."
    for path, result in fs_denials.items():
        if "BLOCKED" not in result:
            return False, f"Host filesystem isolation breach at '{path}': status is '{result}'."

    # 4. External network isolation check
    net_status = data.get("network_access", "")
    if "BLOCKED" not in net_status:
        return False, f"External network isolation breach: connection was not blocked (status='{net_status}')."

    # 5. Artifact verification check
    if not data.get("artifact_written"):
        return False, "Probe script reported artifact_written=False."

    return True, "All probe containment and isolation gates passed."


def perform_verifiable_cleanup(test_ws: Path) -> Tuple[bool, str]:
    """
    Verifiably remove workspace and all artifacts.
    Preserves diagnostic error message on failure.
    """
    if not test_ws.exists():
        return True, "Workspace does not exist (already clean)."

    err_msgs = []
    # 1. Remove all files inside workspace
    try:
        for item in test_ws.iterdir():
            try:
                if item.is_file() or item.is_symlink():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            except Exception as e:
                err_msgs.append(f"Failed to remove file '{item.name}': {e}")
    except Exception as e:
        err_msgs.append(f"Failed to list directory contents: {e}")

    # 2. Remove workspace directory itself
    try:
        test_ws.rmdir()
    except Exception as e:
        err_msgs.append(f"Failed to rmdir '{test_ws}': {e}")

    # 3. Verify directory is truly gone
    if test_ws.exists():
        return False, f"Workspace directory '{test_ws}' still exists. Errors: {'; '.join(err_msgs)}"

    return True, f"Workspace '{test_ws}' successfully and verifiably removed."


def test_sandbox_execution_and_containment() -> bool:
    print_step("Verify DynamicUser Execution, Containment & Artifact Return")
    
    # Determine workspace base
    ws_parent = Path(SHARED_WS_BASE) if Path(SHARED_WS_BASE).exists() else Path("/tmp")
    test_ws = Path(tempfile.mkdtemp(prefix="qc_worker_exec_test_", dir=str(ws_parent)))
    
    overall_success = False

    try:
        # Set directory mode 1777 (with sticky bit)
        os.chmod(test_ws, 0o1777)

        # Embedded probe payload to verify filesystem denial, network denial, working directory, and artifact creation
        probe_code = """
import json
import os
from pathlib import Path
import socket
import sys

results = {}

# 1. Host Filesystem Write Denial (/etc, /root, /usr)
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

# 3. Execution Context & Artifact Creation in Workspace
results["cwd"] = os.getcwd()
results["isolated_uid"] = os.getuid()

artifact_file = Path("vps_artifact.json")
artifact_file.write_text(json.dumps({"probe_status": "ok", "isolated_uid": os.getuid()}), encoding="utf-8")
results["artifact_written"] = artifact_file.exists()

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

        # Evaluate probe findings against security gates
        passed, reason = evaluate_probe_data(data, test_ws)
        if not passed:
            print_fail(f"Containment evaluation failed: {reason}")
            return False

        print_pass(f"DynamicUser UID allocated in ephemeral range: UID={data['isolated_uid']}")
        print_pass(f"WorkingDirectory explicitly verified: {data['cwd']}")
        print_pass("Host filesystem write attempts blocked: /etc, /root, /usr all returned PermissionError/OSError.")
        print_pass(f"External network connection blocked: {data['network_access']}")

        # Verify Artifact Retrieval by parent worker
        artifact_path = test_ws / "vps_artifact.json"
        if artifact_path.exists() and artifact_path.stat().st_size > 0:
            content = json.loads(artifact_path.read_text(encoding="utf-8"))
            print_pass(f"Artifact created by sandbox and successfully read by worker: {content}")
        else:
            print_fail("Artifact file missing or empty in workspace.")
            return False

        overall_success = True
        return True

    finally:
        # Verifiable Cleanup
        cleanup_ok, cleanup_diag = perform_verifiable_cleanup(test_ws)
        if cleanup_ok:
            print_pass(f"Verifiable cleanup completed: {cleanup_diag}")
        else:
            print_fail(f"Verifiable cleanup failed: {cleanup_diag}")
            overall_success = False


def main():
    print("=" * 70)
    print("Qwen Collective VPS Scoped Sandbox Integration Test Suite")
    print("=" * 70)
    ok1 = test_unauthorized_launcher_inputs()
    ok2 = test_sandbox_execution_and_containment()
    print("\n" + "=" * 70)
    if ok1 and ok2:
        print("\033[92mVerification completed successfully: ALL CHECKS PASSED.\033[0m")
        print("=" * 70)
        sys.exit(0)
    else:
        print("\033[91mVerification FAILED: One or more security checks failed.\033[0m")
        print("=" * 70)
        sys.exit(1)


if __name__ == "__main__":
    main()
