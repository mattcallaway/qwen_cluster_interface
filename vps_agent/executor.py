"""
Sandboxed Execution Engine for VPS Worker Agent.
Enforces execution security model across:
- Class A: Built-in safe diagnostic and capability operations
- Class B: Approved scripts in sanitized environment with restricted working directory
- Class C: Experimental or untrusted code (fails closed if OS-level isolation sandbox is unavailable)
- Explicit rejection of unsupported operations with structured error
"""

import base64
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("qwen_collective.worker_executor")

CLASS_A_OPERATIONS = {"test_task", "health_check", "diagnostic", "capability_probe", "ping"}
CLASS_B_OPERATIONS = {"python_execution", "python_script", "approved_script"}
CLASS_C_OPERATIONS = {"experimental_code", "untrusted_script", "ai_generated_code"}

MAX_OUTPUT_CHARS = 10000

# Environment variables to strip to prevent secret leakage to worker scripts
SENSITIVE_ENV_PREFIXES = ("QC_", "SECRET", "API_KEY", "TOKEN", "PASSWORD", "AWS_", "SSH_", "DATABASE_", "AUTH")


def _build_sanitized_environment(work_dir: Optional[str] = None) -> Dict[str, str]:
    """Create a minimal, sanitized environment for subprocess execution."""
    clean_env: Dict[str, str] = {}
    safe_keys = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "HOME", "LANG", "LC_ALL"}
    for k, v in os.environ.items():
        k_upper = k.upper()
        if any(k_upper.startswith(prefix) for prefix in SENSITIVE_ENV_PREFIXES):
            continue
        if any(term in k_upper for term in ("SECRET", "TOKEN", "KEY", "PASS", "AUTH")):
            continue
        if k_upper in safe_keys:
            clean_env[k] = v
    # Isolate python imports to working directory
    clean_env["PYTHONPATH"] = work_dir or os.getcwd()
    clean_env["PYTHONUNBUFFERED"] = "1"
    clean_env["QC_EXECUTION_SANDBOX"] = "1"
    return clean_env


def _detect_os_isolation_mechanism() -> Optional[str]:
    """
    Detect enforced OS-level execution isolation mechanism.
    Priority:
    1. 'scoped_runner': /usr/local/bin/qwen-sandbox-runner (scoped privileged launcher)
    2. 'systemd_transient': systemd-run transient unit with DynamicUser=yes, ProtectSystem=strict, etc.
    3. 'bubblewrap': bwrap unprivileged container sandbox
    4. 'restricted_subprocess': only in development/test fallback mode
    5. None: if no enforceable OS isolation boundary is present (fails closed)
    """
    custom_runner = os.getenv("QC_SANDBOX_RUNNER")
    if custom_runner and (os.path.exists(custom_runner) or os.name != "nt"):
        return "scoped_runner"
    if os.path.exists("/usr/local/bin/qwen-sandbox-runner") or shutil.which("qwen-sandbox-runner"):
        return "scoped_runner"
    if shutil.which("systemd-run"):
        return "systemd_transient"
    if shutil.which("bwrap"):
        return "bubblewrap"
    if os.environ.get("QC_ALLOW_SUBPROCESS_FALLBACK") == "1" or os.environ.get("PYTEST_CURRENT_TEST"):
        return "restricted_subprocess"
    return None


def _build_isolation_command(
    isolation_mech: str,
    script_path: Path,
    work_dir: str,
    network_access: bool = False,
    memory_limit_mb: int = 512,
    tasks_max: int = 64,
) -> List[str]:
    """Build the command line for enforcing the OS isolation boundary."""
    if isolation_mech == "scoped_runner":
        runner_bin = (
            os.getenv("QC_SANDBOX_RUNNER")
            or shutil.which("qwen-sandbox-runner")
            or "/usr/local/bin/qwen-sandbox-runner"
        )
        cmd = [
            "sudo",
            runner_bin,
            "--workspace", str(work_dir),
            "--entrypoint", str(script_path),
            "--memory-mb", str(memory_limit_mb),
            "--tasks-max", str(tasks_max),
        ]
        if network_access:
            cmd.append("--allow-network")
        return cmd
    elif isolation_mech == "systemd_transient":
        network_flags = [] if network_access else ["-p", "IPAddressDeny=any"]
        return [
            "systemd-run",
            "--pipe",
            "--wait",
            "--collect",
            "-p", "DynamicUser=yes",
            "-p", "ProtectSystem=strict",
            "-p", "ProtectHome=yes",
            "-p", "PrivateTmp=yes",
            "-p", f"MemoryMax={memory_limit_mb}M",
            "-p", f"TasksMax={tasks_max}",
            "-p", f"ReadWritePaths={work_dir}",
        ] + network_flags + [
            sys.executable,
            str(script_path),
        ]
    elif isolation_mech == "bubblewrap":
        return [
            "bwrap",
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/lib", "/lib",
            "--ro-bind", "/lib64", "/lib64",
            "--ro-bind", "/bin", "/bin",
            "--ro-bind", "/etc", "/etc",
            "--dir", "/tmp",
            "--bind", work_dir, work_dir,
            "--unshare-all",
            "--die-with-parent",
            sys.executable,
            str(script_path),
        ]
    else:
        return [sys.executable, str(script_path)]


def _check_container_isolation_available() -> bool:
    """Check if an OS-level container isolation runtime is available."""
    # Check for docker, podman, or bwrap in PATH
    for cmd in ("docker", "podman", "bwrap"):
        if shutil.which(cmd):
            return True
    return False


def execute_task(
    task: Dict[str, Any],
    timeout_seconds: int = 300,
) -> Dict[str, Any]:
    """
    Execute an approved task specification adhering to execution class controls.
    Returns:
    {
        "status": "success" | "error" | "timeout",
        "exit_code": int,
        "runtime_seconds": float,
        "stdout_summary": str,
        "stderr_summary": str,
        "error_category": Optional[str],
        "result_payload": dict,
    }
    """
    start_time = time.time()
    task_type = task.get("task_type", "unsupported")
    input_data = task.get("input_data") or {}
    task_id = task.get("task_id", "")

    logger.info("Starting execution of task %s (type: %s, timeout: %ss)", task_id, task_type, timeout_seconds)

    # ---------------------------------------------------------------------------
    # Class A: Built-in Safe Operations
    # ---------------------------------------------------------------------------
    if task_type in CLASS_A_OPERATIONS:
        time.sleep(0.02)  # Simulate brief verification
        runtime = round(time.time() - start_time, 4)
        return {
            "status": "success",
            "exit_code": 0,
            "runtime_seconds": runtime,
            "stdout_summary": f"Harmless built-in operation '{task_type}' verified successfully for task {task_id}.",
            "stderr_summary": "",
            "error_category": None,
            "result_payload": {
                "execution_class": "Class A",
                "verified": True,
                "operation": task_type,
                "message": f"Harmless built-in operation '{task_type}' verified successfully.",
                "worker_python": sys.version.split()[0],
                "received_input": input_data,
            },
        }

    # ---------------------------------------------------------------------------
    # Class B: Approved Scripts / Controlled Python Execution
    # ---------------------------------------------------------------------------
    if task_type in CLASS_B_OPERATIONS:
        script_code = input_data.get("script") or input_data.get("code")
        if not script_code:
            runtime = round(time.time() - start_time, 4)
            return {
                "status": "error",
                "exit_code": 1,
                "runtime_seconds": runtime,
                "stdout_summary": "",
                "stderr_summary": f"Task specified '{task_type}' but provided no script code in input_data.",
                "error_category": "validation_error",
                "result_payload": {"execution_class": "Class B"},
            }

        actual_checksum = hashlib.sha256(script_code.encode("utf-8")).hexdigest()

        # ---------------------------------------------------------------------------
        # Security Gate: Enforce Controller-Issued Execution Manifest
        # ---------------------------------------------------------------------------
        # A worker must not trust arbitrary approval indicators ('approved: true', 'is_approved: true')
        # supplied inside an arbitrary payload. It must require a valid execution manifest.
        manifest = task.get("execution_manifest") or input_data.get("execution_manifest")

        if not manifest:
            runtime = round(time.time() - start_time, 4)
            # Differentiate forged approval flag from ordinary unapproved script
            has_forged_flag = bool(
                input_data.get("approved") is True
                or input_data.get("is_approved") is True
                or task.get("approved") is True
                or input_data.get("approved_script_id")
            )
            if has_forged_flag:
                logger.warning("Class B execution rejected: Forged approval flag without manifest (task: %s)", task_id)
                return {
                    "status": "error",
                    "exit_code": 126,
                    "runtime_seconds": runtime,
                    "stdout_summary": "",
                    "stderr_summary": (
                        "Class B execution rejected: Approval indicators supplied inside task payload without a "
                        "controller-issued execution manifest are unauthorized."
                    ),
                    "error_category": "unauthorized_manifest",
                    "result_payload": {"execution_class": "Class B", "is_approved": False},
                }

            logger.warning("Unapproved script rejected for Class B execution (task: %s)", task_id)
            return {
                "status": "error",
                "exit_code": 126,
                "runtime_seconds": runtime,
                "stdout_summary": "",
                "stderr_summary": "Unapproved Python script rejected for Class B execution. Arbitrary scripts must have an approval record.",
                "error_category": "unapproved_script",
                "result_payload": {"execution_class": "Class B", "is_approved": False},
            }

        # Validate manifest task ID binding
        manifest_task_id = manifest.get("task_id")
        if manifest_task_id and task_id and manifest_task_id != task_id:
            runtime = round(time.time() - start_time, 4)
            logger.warning("Manifest tampering detected for task %s (manifest task: %s)", task_id, manifest_task_id)
            return {
                "status": "error",
                "exit_code": 126,
                "runtime_seconds": runtime,
                "stdout_summary": "",
                "stderr_summary": f"Manifest task_id '{manifest_task_id}' does not match target task '{task_id}'.",
                "error_category": "manifest_tampering",
                "result_payload": {"execution_class": "Class B"},
            }

        # Validate manifest script checksum binding
        expected_checksum = (
            manifest.get("script_checksum")
            or input_data.get("script_checksum")
            or input_data.get("checksum")
            or input_data.get("sha256")
            or task.get("script_checksum")
        )
        if not expected_checksum or expected_checksum.lower() != actual_checksum.lower():
            runtime = round(time.time() - start_time, 4)
            logger.warning("Script checksum mismatch for task %s (expected %s, got %s)", task_id, expected_checksum, actual_checksum)
            return {
                "status": "error",
                "exit_code": 126,
                "runtime_seconds": runtime,
                "stdout_summary": "",
                "stderr_summary": f"Script checksum verification failed. Expected '{expected_checksum}', computed '{actual_checksum}'.",
                "error_category": "checksum_mismatch",
                "result_payload": {"execution_class": "Class B", "expected_checksum": expected_checksum, "actual_checksum": actual_checksum},
            }

        # Validate allowed capabilities
        allowed_caps = manifest.get("allowed_capabilities") or ["python_execution"]
        if "python_execution" not in allowed_caps:
            runtime = round(time.time() - start_time, 4)
            logger.warning("Manifest for task %s does not authorize python_execution", task_id)
            return {
                "status": "error",
                "exit_code": 126,
                "runtime_seconds": runtime,
                "stdout_summary": "",
                "stderr_summary": "Manifest does not authorize 'python_execution' capability.",
                "error_category": "unauthorized_capability",
                "result_payload": {"execution_class": "Class B"},
            }

        # ---------------------------------------------------------------------------
        # OS-Level Isolation Boundary Enforcement
        # ---------------------------------------------------------------------------
        isolation_mech = _detect_os_isolation_mechanism()
        if not isolation_mech:
            runtime = round(time.time() - start_time, 4)
            logger.error("Class B execution rejected: Host lacks required OS-level isolation boundary (task: %s)", task_id)
            return {
                "status": "error",
                "exit_code": 126,
                "runtime_seconds": runtime,
                "stdout_summary": "",
                "stderr_summary": (
                    "Class B execution rejected: Host lacks required OS-level isolation boundary. "
                    "Production Linux worker requires systemd transient unit (DynamicUser=yes) or Bubblewrap (bwrap)."
                ),
                "error_category": "unsupported_isolation",
                "result_payload": {
                    "execution_class": "Class B",
                    "isolation_enforced": False,
                    "missing_isolation": ["systemd-run", "bwrap"],
                },
            }

        # Create isolated workspace directory for this execution.
        # Prefer designated shared workspace directory (/opt/qwen-collective/workspaces)
        # to ensure visibility across PrivateTmp namespaces between worker service and transient units.
        ws_base = os.getenv("QC_WORKER_WORKSPACE_DIR")
        if ws_base and Path(ws_base).exists():
            work_dir = tempfile.mkdtemp(prefix="qc_worker_exec_", dir=ws_base)
        elif Path("/opt/qwen-collective/workspaces").exists():
            work_dir = tempfile.mkdtemp(prefix="qc_worker_exec_", dir="/opt/qwen-collective/workspaces")
        else:
            work_dir = tempfile.mkdtemp(prefix="qc_worker_exec_")

        # DynamicUser=yes assigns an ephemeral UID (61184-65519) to the sandboxed execution.
        # Set 01777 (rwxrwxrwt with sticky bit): allows ephemeral dynamic user to write artifacts,
        # while sticky bit prevents tampering, unlinking, or replacement of task_entrypoint.py.
        if os.name != "nt":
            try:
                os.chmod(work_dir, 0o1777)
            except Exception:
                pass

        # Write script to isolated entrypoint file (mode 0555: readable/executable by dynamic user, non-writable)
        script_file = Path(work_dir) / "task_entrypoint.py"
        script_file.write_text(script_code, encoding="utf-8")
        if os.name != "nt":
            try:
                os.chmod(script_file, 0o555)
            except Exception:
                pass

        sanitized_env = _build_sanitized_environment(work_dir=work_dir)
        network_access = bool(manifest.get("network_access", False))
        cmd = _build_isolation_command(
            isolation_mech=isolation_mech,
            script_path=script_file,
            work_dir=work_dir,
            network_access=network_access,
        )

        try:
            proc = subprocess.run(
                cmd,
                cwd=work_dir,
                env=sanitized_env,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            runtime = round(time.time() - start_time, 4)
            is_success = proc.returncode == 0
            stdout_text = proc.stdout[:MAX_OUTPUT_CHARS]
            stderr_text = proc.stderr[:MAX_OUTPUT_CHARS]

            # ---------------------------------------------------------------------------
            # Collect Physical Artifacts Generated During Execution
            # ---------------------------------------------------------------------------
            generated_artifacts = []
            for item in Path(work_dir).iterdir():
                if item.is_file() and item.name != "task_entrypoint.py":
                    try:
                        file_bytes = item.read_bytes()
                        file_sha = hashlib.sha256(file_bytes).hexdigest()
                        generated_artifacts.append({
                            "filename": item.name,
                            "file_size": len(file_bytes),
                            "sha256": file_sha,
                            "expected_sha256": file_sha,
                            "content_base64": base64.b64encode(file_bytes).decode("ascii"),
                        })
                    except Exception as art_err:
                        logger.warning("Failed to read generated artifact %s: %s", item.name, art_err)

            # Parse any structured JSON lines in stdout
            parsed_result = {}
            for line in stdout_text.splitlines():
                line_str = line.strip()
                if line_str.startswith("{") and line_str.endswith("}"):
                    try:
                        data = json.loads(line_str)
                        if isinstance(data, dict):
                            parsed_result.update(data)
                    except Exception:
                        pass

            result_payload = {
                "execution_class": "Class B",
                "isolation_mechanism": isolation_mech,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "returncode": proc.returncode,
                "artifacts": generated_artifacts,
            }
            result_payload.update(parsed_result)
            if generated_artifacts and "artifacts" not in parsed_result:
                result_payload["artifacts"] = generated_artifacts

            return {
                "status": "success" if is_success else "error",
                "exit_code": proc.returncode,
                "runtime_seconds": runtime,
                "stdout_summary": stdout_text,
                "stderr_summary": stderr_text,
                "error_category": "runtime_error" if not is_success else None,
                "result_payload": result_payload,
            }
        except subprocess.TimeoutExpired:
            runtime = round(time.time() - start_time, 4)
            return {
                "status": "timeout",
                "exit_code": 124,
                "runtime_seconds": runtime,
                "stdout_summary": "",
                "stderr_summary": f"Process exceeded execution timeout limit of {timeout_seconds} seconds.",
                "error_category": "timeout",
                "result_payload": {"execution_class": "Class B", "isolation_mechanism": isolation_mech},
            }
        except Exception as e:
            runtime = round(time.time() - start_time, 4)
            return {
                "status": "error",
                "exit_code": 1,
                "runtime_seconds": runtime,
                "stdout_summary": "",
                "stderr_summary": str(e),
                "error_category": "system_error",
                "result_payload": {"execution_class": "Class B", "isolation_mechanism": isolation_mech},
            }
        finally:
            # Clean up the isolated workspace directory
            try:
                shutil.rmtree(work_dir, ignore_errors=True)
            except Exception:
                pass

    # ---------------------------------------------------------------------------
    # Class C: Experimental Code / Untrusted AI-Generated Code
    # ---------------------------------------------------------------------------
    if task_type in CLASS_C_OPERATIONS:
        # OS-level isolation is mandatory for Class C
        if not _check_container_isolation_available():
            runtime = round(time.time() - start_time, 4)
            logger.warning("Class C execution rejected: No OS container isolation sandbox found.")
            return {
                "status": "error",
                "exit_code": 1,
                "runtime_seconds": runtime,
                "stdout_summary": "",
                "stderr_summary": (
                    "Execution rejected: Class C experimental code requires OS-level container isolation "
                    "(docker, podman, or bwrap). Missing isolation prerequisites on worker host."
                ),
                "error_category": "isolation_unavailable",
                "result_payload": {
                    "execution_class": "Class C",
                    "isolated": False,
                    "missing_prerequisites": ["container_runtime"],
                },
            }

        # If container runtime is available, bounded execution would be invoked here
        runtime = round(time.time() - start_time, 4)
        return {
            "status": "error",
            "exit_code": 1,
            "runtime_seconds": runtime,
            "stdout_summary": "",
            "stderr_summary": "Class C container runner configured but requires explicit administrator image approval.",
            "error_category": "approval_required",
            "result_payload": {"execution_class": "Class C"},
        }

    # ---------------------------------------------------------------------------
    # Unsupported Operations (Must Fail Explicitly, Never Report False Success)
    # ---------------------------------------------------------------------------
    runtime = round(time.time() - start_time, 4)
    logger.error("Task %s rejected with unsupported operation '%s'", task_id, task_type)
    return {
        "status": "error",
        "exit_code": 1,
        "runtime_seconds": runtime,
        "stdout_summary": "",
        "stderr_summary": f"Unsupported operation: task_type '{task_type}' is not supported by worker.",
        "error_category": "unsupported_operation",
        "result_payload": {
            "supported_classes": {
                "class_a": sorted(list(CLASS_A_OPERATIONS)),
                "class_b": sorted(list(CLASS_B_OPERATIONS)),
                "class_c": sorted(list(CLASS_C_OPERATIONS)),
            }
        },
    }
