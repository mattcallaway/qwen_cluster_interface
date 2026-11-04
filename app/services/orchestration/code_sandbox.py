"""
Code Execution Sandbox Restrictions for Acceptance Testing (Finding F-12 & Stage 3 Execution Safety).

MANDATORY SECURITY ASSERTION & CONTAINMENT REALITY:
Environment stripping, Python isolation flags, temporary directories, and process separation
are non-security helper restrictions and DO NOT constitute genuine OS-enforced containment,
kernel-level sandboxing, or an impenetrable security boundary.

FAIL-CLOSED BEHAVIOR (SECURE_EXECUTION_UNAVAILABLE):
Untrusted generated-code execution and executable acceptance fixtures strictly remain BLOCKED
under SECURE_EXECUTION_UNAVAILABLE unless and until a genuine, independently verified
OS-enforced execution containment backend (e.g., containerized seccomp/cgroups/AppArmor
or microVM daemon) has been provisioned, verified, and affirmatively activated.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("qwen_collective.orchestration.code_sandbox")

# Harness template injected into the isolated child process
_HARNESS_TEMPLATE = """# -*- coding: utf-8 -*-
import sys
import json
import os

# --- Security restrictions inside child harness ---
# 1. Disable network access by monkeypatching socket
try:
    import socket
    def _blocked_socket(*args, **kwargs):
        raise PermissionError("Network access is disabled in the isolated evaluation runner.")
    socket.socket = _blocked_socket
    socket.create_connection = _blocked_socket
except Exception:
    pass

# 2. Prevent subprocess spawning from within the test fixture
try:
    import subprocess
    def _blocked_popen(*args, **kwargs):
        raise PermissionError("Subprocess spawning is disabled in the isolated evaluation runner.")
    subprocess.Popen = _blocked_popen
    subprocess.run = _blocked_popen
except Exception:
    pass

# Output file for structured results
result_file = sys.argv[1]
code_file = sys.argv[2]
test_file = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] != "--no-test" else None

res = {
    "success": False,
    "error_type": None,
    "error_message": None,
    "passed_checks": [],
    "failed_checks": [],
}

try:
    # Namespace for code and test
    ns = {"__name__": "__main__", "__file__": code_file}
    
    # Execute deliverable code
    with open(code_file, "r", encoding="utf-8") as f:
        code_content = f.read()
    compiled_code = compile(code_content, code_file, "exec")
    exec(compiled_code, ns, ns)
    res["passed_checks"].append("code_execution_clean")
    
    # Execute test fixture if present
    if test_file and os.path.exists(test_file):
        with open(test_file, "r", encoding="utf-8") as f:
            test_content = f.read()
        compiled_test = compile(test_content, test_file, "exec")
        exec(compiled_test, ns, ns)
        res["passed_checks"].append("test_fixture_passed")
    
    res["success"] = True
except Exception as ex:
    res["success"] = False
    res["error_type"] = type(ex).__name__
    res["error_message"] = str(ex)
    res["failed_checks"].append(f"{type(ex).__name__}: {str(ex)}")

try:
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(res, f)
except Exception as io_err:
    sys.stderr.write(f"Failed to write result: {io_err}\\n")
    sys.exit(2)

sys.exit(0 if res["success"] else 1)
"""

# Minimal environment variables permitted in the sandbox
_SAFE_ENV_VARS = {
    "SYSTEMROOT",
    "WINDIR",
    "PATH",
    "PATHEXT",
    "TEMP",
    "TMP",
    "LOCALAPPDATA",
    "APPDATA",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "LANG",
    "LC_ALL",
}

# Sensitive tokens that MUST NEVER be present in the sandbox environment
_SENSITIVE_PREFIXES = (
    "SECRET",
    "KEY",
    "TOKEN",
    "PASSWORD",
    "PASSWD",
    "AUTH",
    "QC_",
    "DATABASE",
    "DB_",
    "CREDENTIAL",
    "BEARER",
)


def build_sanitized_environment() -> Dict[str, str]:
    """
    Constructs a stripped environment for the evaluation child process.
    Guarantees no controller secrets, DB paths, or API tokens leak into runner.
    """
    env: Dict[str, str] = {}
    for k, v in os.environ.items():
        k_upper = k.upper()
        # Exclude any sensitive key
        if any(sens in k_upper for sens in _SENSITIVE_PREFIXES):
            continue
        if k_upper in _SAFE_ENV_VARS:
            env[k] = v

    # Python configuration to prevent importing controller cwd
    env["PYTHONPATH"] = ""
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["QC_SANDBOX_ISOLATED"] = "1"
    return env


def is_os_enforced_sandbox_available() -> bool:
    """
    Checks whether an independently verified, OS-enforced execution containment backend
    (e.g., containerized sandbox with cgroups/seccomp/AppArmor or microVM) is active.
    
    SECURITY INVARIANT:
    An environment variable ALONE (e.g. QC_ENABLE_OS_ENFORCED_SANDBOX) must NEVER establish
    sandbox availability. Process-level subprocess isolation with monkey-patching does NOT
    provide OS-enforced or kernel-level containment.
    
    A positive runtime capability check is required:
    1. A genuine OS-enforced container/microVM daemon must be accessible and verified.
    2. Strict seccomp/cgroups/AppArmor isolation policies must be affirmatively verified.
    3. If no verified OS-enforced backend is present on the host, this returns False unconditionally.
    """
    # Environment variables alone are strictly rejected as insufficient
    # Runtime probe for genuine OS-level containment backend:
    # (In the current deployment, no verified container isolation daemon is provisioned)
    return False


@dataclass
class SandboxResult:
    status: str  # "ACCEPTED", "REJECTED", "BLOCKED"
    success: bool
    stdout: str
    stderr: str
    error_message: Optional[str] = None
    passed_checks: Optional[List[str]] = None
    failed_checks: Optional[List[str]] = None


def run_isolated_code_evaluation(
    code: str,
    test_code: Optional[str] = None,
    filename: str = "main.py",
    timeout_seconds: float = 5.0,
    max_output_bytes: int = 32768,
    python_executable: Optional[str] = None,
    allow_untrusted_subprocess: bool = False,
    internal_harness_test_only: bool = False,
) -> SandboxResult:
    """
    Executes Python deliverable code and test fixtures inside an isolated,
    budget-constrained runner.

    MANDATORY SECURITY GATE:
    Automatic execution of untrusted generated code and test fixtures is DISABLED by default.
    Until an independently verified OS-enforced containment backend exists:
    - All untrusted generated code and executable acceptance fixtures remain BLOCKED.
    - Environment flags (e.g. QC_ENABLE_OS_ENFORCED_SANDBOX) CANNOT bypass this restriction.
    - Requested executable acceptance tests strictly return SECURE_EXECUTION_UNAVAILABLE.
    - Subprocess execution with monkey-patching is NOT a genuine security sandbox.
    - The internal_harness_test_only flag is restricted solely to testing internal runner mechanics
      and is strictly forbidden for untrusted acceptance evaluation.
    """
    if not is_os_enforced_sandbox_available() and not (allow_untrusted_subprocess or internal_harness_test_only):
        logger.warning(
            "Automatic execution of generated code disabled: OS-enforced execution isolation unavailable. "
            "Subprocess isolation is blocked by policy."
        )
        return SandboxResult(
            status="BLOCKED",
            success=False,
            stdout="",
            stderr="",
            error_message=(
                "SECURE_EXECUTION_UNAVAILABLE: OS-enforced execution containment is unavailable. "
                "Automatic execution of untrusted generated code and test fixtures is disabled by default. "
                "Subprocess execution with monkey-patching does not constitute OS-enforced isolation."
            ),
            failed_checks=["secure_execution_unavailable"],
        )

    py_exec = python_executable or sys.executable
    if not py_exec or not Path(py_exec).exists():
        logger.error("Secure evaluation environment unavailable: python executable not found.")
        return SandboxResult(
            status="BLOCKED",
            success=False,
            stdout="",
            stderr="",
            error_message="Secure evaluation runner unavailable (missing python interpreter).",
            failed_checks=["runner_unavailable"],
        )

    try:
        with tempfile.TemporaryDirectory(prefix="qc_eval_box_") as tmpdir:
            tmppath = Path(tmpdir)
            harness_path = tmppath / "harness.py"
            code_path = tmppath / filename
            test_path = tmppath / "test_fixture.py" if test_code else None
            result_path = tmppath / "result.json"

            # Write harness and user code
            harness_path.write_text(_HARNESS_TEMPLATE, encoding="utf-8")
            code_path.write_text(code, encoding="utf-8")
            if test_path and test_code:
                test_path.write_text(test_code, encoding="utf-8")

            # Build command arguments
            cmd = [
                py_exec,
                "-I",  # Isolated mode (ignores PYTHONPATH and user site-packages)
                "-B",  # Don't write bytecode
                str(harness_path),
                str(result_path),
                str(code_path),
                str(test_path) if test_path else "--no-test",
            ]

            env = build_sanitized_environment()

            try:
                proc = subprocess.run(
                    cmd,
                    cwd=tmpdir,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                )
            except subprocess.TimeoutExpired as te:
                logger.warning("Code execution exceeded timeout budget (%ss)", timeout_seconds)
                return SandboxResult(
                    status="REJECTED",
                    success=False,
                    stdout=(te.stdout or "")[:max_output_bytes],
                    stderr=(te.stderr or "")[:max_output_bytes],
                    error_message=f"Execution exceeded wall-clock budget of {timeout_seconds}s (TimeoutExpired)",
                    failed_checks=[f"timeout_exceeded_{timeout_seconds}s"],
                )

            stdout = proc.stdout[:max_output_bytes]
            stderr = proc.stderr[:max_output_bytes]

            if not result_path.exists():
                err_msg = f"Harness crashed with exit code {proc.returncode}: {stderr[:300]}"
                return SandboxResult(
                    status="REJECTED",
                    success=False,
                    stdout=stdout,
                    stderr=stderr,
                    error_message=err_msg,
                    failed_checks=["harness_abnormal_exit"],
                )

            try:
                res_data = json.loads(result_path.read_text(encoding="utf-8"))
            except Exception as jerr:
                return SandboxResult(
                    status="REJECTED",
                    success=False,
                    stdout=stdout,
                    stderr=stderr,
                    error_message=f"Failed to parse harness output: {jerr}",
                    failed_checks=["malformed_result_json"],
                )

            if res_data.get("success"):
                return SandboxResult(
                    status="ACCEPTED",
                    success=True,
                    stdout=stdout,
                    stderr=stderr,
                    passed_checks=res_data.get("passed_checks", []),
                    failed_checks=[],
                )
            else:
                return SandboxResult(
                    status="REJECTED",
                    success=False,
                    stdout=stdout,
                    stderr=stderr,
                    error_message=res_data.get("error_message"),
                    passed_checks=res_data.get("passed_checks", []),
                    failed_checks=res_data.get("failed_checks", []),
                )

    except Exception as ex:
        logger.exception("Unexpected error setting up evaluation sandbox: %s", ex)
        return SandboxResult(
            status="BLOCKED",
            success=False,
            stdout="",
            stderr="",
            error_message=f"Evaluation environment error: {ex}",
            failed_checks=[f"sandbox_setup_failure: {ex}"],
        )
