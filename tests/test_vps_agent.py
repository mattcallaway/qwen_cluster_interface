"""
Tests for VPS Worker Agent & Components (Milestone 2).
"""

import sys
from pathlib import Path
import pytest

from vps_agent.buffer import OfflineBuffer
from vps_agent.capabilities import discover_local_hardware
from vps_agent.config import AgentConfig
from vps_agent.executor import execute_task


def test_discover_local_hardware():
    hw = discover_local_hardware()
    assert hw["cpu_count"] >= 1
    assert hw["ram_total_mb"] > 0
    assert hw["disk_total_gb"] > 0
    assert "os_name" in hw
    assert "installed_runtimes" in hw
    assert any("python" in r.lower() for r in hw["installed_runtimes"])
    assert "python_execution" in hw["declared_capabilities"]


def test_offline_buffer_operations(tmp_path):
    db_file = tmp_path / "test_buffer.db"
    buf = OfflineBuffer(db_file)

    assert buf.count_pending() == 0

    # 1. Add pending submission
    buf.buffer_submission(
        task_id="t-001",
        lease_id="l-001",
        idempotency_key="idem-buf-1",
        submission_payload={"status": "success", "runtime_seconds": 0.42},
    )
    assert buf.count_pending() == 1

    # 2. Retrieve pending
    items = buf.get_pending_submissions()
    assert len(items) == 1
    assert items[0]["task_id"] == "t-001"
    assert items[0]["payload"]["runtime_seconds"] == 0.42

    # 3. Remove submission
    buf.remove_submission(items[0]["id"])
    assert buf.count_pending() == 0


def test_executor_harmless_test_task():
    task = {
        "task_id": "test-task-1",
        "task_type": "test_task",
        "input_data": {"diagnostic": True},
    }
    outcome = execute_task(task, timeout_seconds=10)
    assert outcome["status"] == "success"
    assert outcome["exit_code"] == 0
    assert outcome["runtime_seconds"] >= 0.0
    assert "Harmless" in outcome["stdout_summary"]
    assert outcome["result_payload"]["verified"] is True


def test_executor_python_script_execution():
    task = {
        "task_id": "py-task-1",
        "task_type": "python_execution",
        "input_data": {
            "approved": True,
            "script": "print('Hello from Qwen Worker!'); print(2 + 2)",
        },
    }
    outcome = execute_task(task, timeout_seconds=10)
    assert outcome["status"] == "success"
    assert outcome["exit_code"] == 0
    assert "Hello from Qwen Worker!" in outcome["stdout_summary"]
    assert "4" in outcome["stdout_summary"]


def test_executor_unapproved_script_fails_closed():
    """Unapproved scripts cannot run under Class B execution."""
    task = {
        "task_id": "py-unapproved",
        "task_type": "python_execution",
        "input_data": {
            "script": "print('This should be rejected!')",
        },
    }
    outcome = execute_task(task, timeout_seconds=10)
    assert outcome["status"] == "error"
    assert outcome["exit_code"] == 126
    assert outcome["error_category"] == "unapproved_script"
    assert "unapproved python script" in outcome["stderr_summary"].lower()


def test_executor_checksum_validation_success():
    """Approved script with matching checksum executes successfully."""
    import hashlib
    script = "print('Verified Checksum Execution')"
    checksum = hashlib.sha256(script.encode("utf-8")).hexdigest()
    task = {
        "task_id": "py-checksum-ok",
        "task_type": "python_execution",
        "input_data": {
            "approved": True,
            "script_checksum": checksum,
            "script": script,
        },
    }
    outcome = execute_task(task, timeout_seconds=10)
    assert outcome["status"] == "success"
    assert outcome["exit_code"] == 0
    assert "Verified Checksum Execution" in outcome["stdout_summary"]


def test_executor_checksum_mismatch_fails_closed():
    """Tampered script with checksum mismatch fails closed."""
    task = {
        "task_id": "py-checksum-tampered",
        "task_type": "python_execution",
        "input_data": {
            "approved": True,
            "script_checksum": "0000000000000000000000000000000000000000000000000000000000000000",
            "script": "print('Tampered script')",
        },
    }
    outcome = execute_task(task, timeout_seconds=10)
    assert outcome["status"] == "error"
    assert outcome["exit_code"] == 126
    assert outcome["error_category"] == "checksum_mismatch"
    assert "checksum verification failed" in outcome["stderr_summary"].lower()


def test_executor_python_script_error():
    task = {
        "task_id": "py-task-err",
        "task_type": "python_execution",
        "input_data": {
            "approved": True,
            "script": "raise ValueError('Intentional worker test error')",
        },
    }
    outcome = execute_task(task, timeout_seconds=10)
    assert outcome["status"] == "error"
    assert outcome["exit_code"] != 0
    assert outcome["error_category"] == "runtime_error"
    assert "Intentional worker test error" in outcome["stderr_summary"]


def test_executor_timeout_limit():
    task = {
        "task_id": "py-task-timeout",
        "task_type": "python_execution",
        "input_data": {
            "approved": True,
            "script": "import time; time.sleep(5)",
        },
    }
    # Short 1-second timeout
    outcome = execute_task(task, timeout_seconds=1)
    assert outcome["status"] == "timeout"
    assert outcome["exit_code"] == 124
    assert outcome["error_category"] == "timeout"


def test_executor_unsupported_operation_rejection():
    """Unsupported operations fail explicitly with error_category 'unsupported_operation'."""
    task = {
        "task_id": "bad-task-01",
        "task_type": "arbitrary_custom_binary",
        "input_data": {"command": "do_something_unsafe"},
    }
    outcome = execute_task(task, timeout_seconds=5)
    assert outcome["status"] == "error"
    assert outcome["exit_code"] == 1
    assert outcome["error_category"] == "unsupported_operation"
    assert "Unsupported operation" in outcome["stderr_summary"]


def test_executor_class_c_fails_closed_without_container_runtime(monkeypatch):
    """Class C experimental/untrusted code fails closed if container runtime is absent."""
    from vps_agent import executor
    monkeypatch.setattr(executor, "_check_container_isolation_available", lambda: False)

    task = {
        "task_id": "untrusted-ai-01",
        "task_type": "experimental_code",
        "input_data": {"code": "import os; print('experimental')"},
    }
    outcome = execute_task(task, timeout_seconds=5)
    assert outcome["status"] == "error"
    assert outcome["error_category"] == "isolation_unavailable"
    assert "container isolation" in outcome["stderr_summary"].lower()


def test_executor_environment_sanitization_strips_controller_secrets(monkeypatch):
    """Subprocess does not receive controller secrets or sensitive environment variables."""
    import os
    monkeypatch.setenv("QC_SECRET_KEY", "super_secret_controller_key")
    monkeypatch.setenv("QC_DATABASE_URL", "sqlite:///sensitive.db")
    monkeypatch.setenv("TOKEN_API_SECRET", "super_secret_token")

    task = {
        "task_id": "py-leak-check",
        "task_type": "python_execution",
        "input_data": {
            "approved": True,
            "script": "import os; print('QC_SECRET_KEY=' + os.environ.get('QC_SECRET_KEY', 'NOT_FOUND')); print('TOKEN=' + os.environ.get('TOKEN_API_SECRET', 'NOT_FOUND'))"
        },
    }
    outcome = execute_task(task, timeout_seconds=5)
    assert outcome["status"] == "success"
    assert "QC_SECRET_KEY=NOT_FOUND" in outcome["stdout_summary"]
    assert "TOKEN=NOT_FOUND" in outcome["stdout_summary"]


def test_executor_pythonpath_isolation_blocks_agent_imports():
    """Worker child scripts cannot import vps_agent internal modules due to PYTHONPATH scrubbing."""
    task = {
        "task_id": "py-import-leak",
        "task_type": "python_execution",
        "input_data": {
            "approved": True,
            "script": (
                "try:\n"
                "    import vps_agent\n"
                "    print('LEAKED_VPS_AGENT')\n"
                "except ImportError:\n"
                "    print('BLOCKED_IMPORT_OK')\n"
            ),
        },
    }
    outcome = execute_task(task, timeout_seconds=5)
    assert outcome["status"] == "success"
    assert "BLOCKED_IMPORT_OK" in outcome["stdout_summary"]
    assert "LEAKED_VPS_AGENT" not in outcome["stdout_summary"]

