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
        "input_data": {"script": "print('Hello from Qwen Worker!'); print(2 + 2)"},
    }
    outcome = execute_task(task, timeout_seconds=10)
    assert outcome["status"] == "success"
    assert outcome["exit_code"] == 0
    assert "Hello from Qwen Worker!" in outcome["stdout_summary"]
    assert "4" in outcome["stdout_summary"]


def test_executor_python_script_error():
    task = {
        "task_id": "py-task-err",
        "task_type": "python_execution",
        "input_data": {"script": "raise ValueError('Intentional worker test error')"},
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
        "input_data": {"script": "import time; time.sleep(5)"},
    }
    # Short 1-second timeout
    outcome = execute_task(task, timeout_seconds=1)
    assert outcome["status"] == "timeout"
    assert outcome["exit_code"] == 124
    assert outcome["error_category"] == "timeout"
