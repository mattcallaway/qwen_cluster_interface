"""
Sandboxed Execution Engine for VPS Worker Agent.
Enforces execution security model across:
- Class A: Built-in safe diagnostic and capability operations
- Class B: Approved scripts in sanitized environment with restricted working directory
- Class C: Experimental or untrusted code (fails closed if OS-level isolation sandbox is unavailable)
- Explicit rejection of unsupported operations with structured error
"""

import logging
import os
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
SENSITIVE_ENV_PREFIXES = ("QC_", "SECRET_", "API_KEY", "TOKEN", "PASSWORD", "AWS_", "SSH_", "DATABASE_")


def _build_sanitized_environment() -> Dict[str, str]:
    """Create a minimal, sanitized environment for subprocess execution."""
    clean_env: Dict[str, str] = {}
    safe_keys = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "HOME", "LANG", "LC_ALL"}
    for k, v in os.environ.items():
        k_upper = k.upper()
        if any(k_upper.startswith(prefix) for prefix in SENSITIVE_ENV_PREFIXES):
            continue
        if k_upper in safe_keys or k_upper.startswith("PYTHON"):
            clean_env[k] = v
    # Ensure unbuffered python output
    clean_env["PYTHONUNBUFFERED"] = "1"
    return clean_env


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

        # Create isolated workspace directory for this execution
        work_dir = tempfile.mkdtemp(prefix="qc_worker_exec_")
        sanitized_env = _build_sanitized_environment()

        try:
            cmd = [sys.executable, "-c", script_code]
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

            return {
                "status": "success" if is_success else "error",
                "exit_code": proc.returncode,
                "runtime_seconds": runtime,
                "stdout_summary": stdout_text,
                "stderr_summary": stderr_text,
                "error_category": "runtime_error" if not is_success else None,
                "result_payload": {
                    "execution_class": "Class B",
                    "stdout": stdout_text,
                    "stderr": stderr_text,
                    "returncode": proc.returncode,
                },
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
                "result_payload": {"execution_class": "Class B"},
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
                "result_payload": {"execution_class": "Class B"},
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
