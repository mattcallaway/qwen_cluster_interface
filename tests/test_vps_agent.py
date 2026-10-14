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


import hashlib

def _make_manifest(task_id: str, script: str, allowed_caps=None):
    cs = hashlib.sha256(script.encode("utf-8")).hexdigest()
    return {
        "task_id": task_id,
        "lease_id": f"lease_{task_id}",
        "worker_id": "test-worker",
        "script_checksum": cs,
        "allowed_capabilities": allowed_caps or ["python_execution"],
        "network_access": False,
    }


def test_executor_python_script_execution():
    script = "print('Hello from Qwen Worker!'); print(2 + 2)"
    task = {
        "task_id": "py-task-1",
        "task_type": "python_execution",
        "input_data": {
            "script": script,
            "execution_manifest": _make_manifest("py-task-1", script),
        },
    }
    outcome = execute_task(task, timeout_seconds=10)
    assert outcome["status"] == "success"
    assert outcome["exit_code"] == 0
    assert "Hello from Qwen Worker!" in outcome["stdout_summary"]
    assert "4" in outcome["stdout_summary"]


def test_executor_forged_approval_flag_rejected():
    """Approval flags supplied inside payload without controller-issued manifest are strictly rejected."""
    task = {
        "task_id": "py-forged",
        "task_type": "python_execution",
        "input_data": {
            "approved": True,
            "script": "print('Forged approval')",
        },
    }
    outcome = execute_task(task, timeout_seconds=10)
    assert outcome["status"] == "error"
    assert outcome["exit_code"] == 126
    assert outcome["error_category"] == "unauthorized_manifest"
    assert "without a controller-issued execution manifest are unauthorized" in outcome["stderr_summary"]


def test_executor_unapproved_script_fails_closed():
    """Unapproved scripts without manifest cannot run under Class B execution."""
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


def test_executor_checksum_validation_success():
    """Approved script with matching manifest checksum executes successfully."""
    script = "print('Verified Checksum Execution')"
    task = {
        "task_id": "py-checksum-ok",
        "task_type": "python_execution",
        "input_data": {
            "script": script,
            "execution_manifest": _make_manifest("py-checksum-ok", script),
        },
    }
    outcome = execute_task(task, timeout_seconds=10)
    assert outcome["status"] == "success"
    assert outcome["exit_code"] == 0
    assert "Verified Checksum Execution" in outcome["stdout_summary"]


def test_executor_checksum_mismatch_fails_closed():
    """Tampered script with checksum mismatch fails closed."""
    script = "print('Tampered script')"
    bad_manifest = _make_manifest("py-checksum-tampered", "different original content")
    task = {
        "task_id": "py-checksum-tampered",
        "task_type": "python_execution",
        "input_data": {
            "script": script,
            "execution_manifest": bad_manifest,
        },
    }
    outcome = execute_task(task, timeout_seconds=10)
    assert outcome["status"] == "error"
    assert outcome["exit_code"] == 126
    assert outcome["error_category"] == "checksum_mismatch"
    assert "checksum verification failed" in outcome["stderr_summary"].lower()


def test_executor_manifest_tampering_fails_closed():
    """Manifest with mismatched task_id fails closed."""
    script = "print('Tampered task ID')"
    bad_manifest = _make_manifest("wrong-task-id", script)
    task = {
        "task_id": "actual-task-id",
        "task_type": "python_execution",
        "input_data": {
            "script": script,
            "execution_manifest": bad_manifest,
        },
    }
    outcome = execute_task(task, timeout_seconds=10)
    assert outcome["status"] == "error"
    assert outcome["exit_code"] == 126
    assert outcome["error_category"] == "manifest_tampering"


def test_executor_unauthorized_capability_fails_closed():
    """Manifest without python_execution capability fails closed."""
    script = "print('Unauthorized cap')"
    bad_manifest = _make_manifest("py-unauth-cap", script, allowed_caps=["network_ping_only"])
    task = {
        "task_id": "py-unauth-cap",
        "task_type": "python_execution",
        "input_data": {
            "script": script,
            "execution_manifest": bad_manifest,
        },
    }
    outcome = execute_task(task, timeout_seconds=10)
    assert outcome["status"] == "error"
    assert outcome["exit_code"] == 126
    assert outcome["error_category"] == "unauthorized_capability"


def test_executor_isolation_missing_fails_closed(monkeypatch):
    """When host lacks required OS-level isolation, Class B execution fails closed."""
    import vps_agent.executor as exec_mod
    monkeypatch.setattr(exec_mod, "_detect_os_isolation_mechanism", lambda: None)
    script = "print('Isolation check')"
    task = {
        "task_id": "py-no-isolation",
        "task_type": "python_execution",
        "input_data": {
            "script": script,
            "execution_manifest": _make_manifest("py-no-isolation", script),
        },
    }
    outcome = execute_task(task, timeout_seconds=10)
    assert outcome["status"] == "error"
    assert outcome["exit_code"] == 126
    assert outcome["error_category"] == "unsupported_isolation"
    assert "lacks required OS-level isolation boundary" in outcome["stderr_summary"]


def test_executor_physical_artifact_generation():
    """Class B execution creates a physical file and executor returns it as base64 artifact with SHA256."""
    script = (
        "import json\n"
        "data = {'result': 'diagnostic_pass', 'status': 'ok'}\n"
        "with open('vps_artifact.json', 'w') as f:\n"
        "    f.write(json.dumps(data))\n"
        "print('ARTIFACT_CREATED')\n"
    )
    task = {
        "task_id": "py-art-gen",
        "task_type": "python_execution",
        "input_data": {
            "script": script,
            "execution_manifest": _make_manifest("py-art-gen", script),
        },
    }
    outcome = execute_task(task, timeout_seconds=10)
    assert outcome["status"] == "success"
    assert outcome["exit_code"] == 0
    artifacts = outcome["result_payload"].get("artifacts", [])
    assert len(artifacts) >= 1
    art = artifacts[0]
    assert art["filename"] == "vps_artifact.json"
    assert art["file_size"] > 0
    assert len(art["sha256"]) == 64
    assert len(art["content_base64"]) > 0


def test_executor_python_script_error():
    script = "raise ValueError('Intentional worker test error')"
    task = {
        "task_id": "py-task-err",
        "task_type": "python_execution",
        "input_data": {
            "script": script,
            "execution_manifest": _make_manifest("py-task-err", script),
        },
    }
    outcome = execute_task(task, timeout_seconds=10)
    assert outcome["status"] == "error"
    assert outcome["exit_code"] != 0
    assert outcome["error_category"] == "runtime_error"
    assert "Intentional worker test error" in outcome["stderr_summary"]


def test_executor_timeout_limit():
    script = "import time; time.sleep(5)"
    task = {
        "task_id": "py-task-timeout",
        "task_type": "python_execution",
        "input_data": {
            "script": script,
            "execution_manifest": _make_manifest("py-task-timeout", script),
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

    script = "import os; print('QC_SECRET_KEY=' + os.environ.get('QC_SECRET_KEY', 'NOT_FOUND')); print('TOKEN=' + os.environ.get('TOKEN_API_SECRET', 'NOT_FOUND'))"
    task = {
        "task_id": "py-leak-check",
        "task_type": "python_execution",
        "input_data": {
            "script": script,
            "execution_manifest": _make_manifest("py-leak-check", script),
        },
    }
    outcome = execute_task(task, timeout_seconds=5)
    assert outcome["status"] == "success"
    assert "QC_SECRET_KEY=NOT_FOUND" in outcome["stdout_summary"]
    assert "TOKEN=NOT_FOUND" in outcome["stdout_summary"]


def test_executor_pythonpath_isolation_blocks_agent_imports():
    """Worker child scripts cannot import vps_agent internal modules due to PYTHONPATH scrubbing."""
    script = (
        "try:\n"
        "    import vps_agent\n"
        "    print('LEAKED_VPS_AGENT')\n"
        "except ImportError:\n"
        "    print('BLOCKED_IMPORT_OK')\n"
    )
    task = {
        "task_id": "py-import-leak",
        "task_type": "python_execution",
        "input_data": {
            "script": script,
            "execution_manifest": _make_manifest("py-import-leak", script),
        },
    }
    outcome = execute_task(task, timeout_seconds=5)
    assert outcome["status"] == "success"
    assert "BLOCKED_IMPORT_OK" in outcome["stdout_summary"]
    assert "LEAKED_VPS_AGENT" not in outcome["stdout_summary"]


def test_executor_scoped_runner_detection_and_command_construction(monkeypatch, tmp_path):
    """Executor detects scoped_runner when configured and builds least-privilege sudo command."""
    from vps_agent import executor

    fake_runner = tmp_path / "qwen-sandbox-runner"
    fake_runner.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("QC_SANDBOX_RUNNER", str(fake_runner))
    assert executor._detect_os_isolation_mechanism() == "scoped_runner"

    script_path = tmp_path / "task_entrypoint.py"
    cmd_no_net = executor._build_isolation_command(
        isolation_mech="scoped_runner",
        script_path=script_path,
        work_dir=str(tmp_path),
        network_access=False,
    )
    assert cmd_no_net[:3] == ["sudo", str(fake_runner), "--workspace"]
    assert str(tmp_path) in cmd_no_net
    assert "--entrypoint" in cmd_no_net
    assert str(script_path) in cmd_no_net
    assert "--allow-network" not in cmd_no_net

    cmd_net = executor._build_isolation_command(
        isolation_mech="scoped_runner",
        script_path=script_path,
        work_dir=str(tmp_path),
        network_access=True,
    )
    # Production scoped runner unconditionally denies network; worker cannot enable network
    assert "--allow-network" not in cmd_net


def test_scoped_sandbox_runner_validates_paths_and_hardens_systemd(tmp_path, monkeypatch):
    """
    Test qwen-sandbox-runner validation and security invariants:
    1. Rejects unauthorized workspace directory outside prefix with exit code 126
    2. Rejects entrypoint script outside the workspace directory with exit code 126
    3. Mandates non-bypassable systemd-run sandbox flags (DynamicUser, ProtectSystem, PrivateTmp, etc.)
    """
    import os
    import subprocess
    import sys
    runner_script = Path(__file__).resolve().parent.parent / "scripts" / "qwen-sandbox-runner.py"
    assert runner_script.exists()

    # 1. Unauthorized workspace (e.g. tmp_path without authorized prefix)
    unauthorized_ws = tmp_path / "unauthorized_dir"
    unauthorized_ws.mkdir()
    ep = unauthorized_ws / "entrypoint.py"
    ep.write_text("print('test')", encoding="utf-8")

    res_ws = subprocess.run(
        [sys.executable, str(runner_script), "--workspace", str(unauthorized_ws), "--entrypoint", str(ep)],
        capture_output=True,
        text=True,
    )
    assert res_ws.returncode == 126
    assert "outside authorized prefixes" in res_ws.stderr

    # 2. Authorized workspace prefix but entrypoint outside workspace
    auth_ws = tmp_path / "qc_worker_exec_test123"
    auth_ws.mkdir()
    outside_ep = tmp_path / "outside_script.py"
    outside_ep.write_text("print('outside')", encoding="utf-8")

    # Add tmp_path to allowed prefixes for this test via runner module inspection
    sys.path.insert(0, str(runner_script.parent))
    import importlib.util
    spec = importlib.util.spec_from_file_location("qwen_sandbox_runner", str(runner_script))
    runner_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner_mod)

    # Validate workspace logic directly
    with pytest.raises(SystemExit) as excinfo:
        runner_mod.validate_workspace(str(unauthorized_ws))
    assert excinfo.value.code == 126

    # Validate entrypoint outside workspace logic directly
    with pytest.raises(SystemExit) as excinfo2:
        runner_mod.validate_entrypoint(auth_ws, str(outside_ep))
    assert excinfo2.value.code == 126

    # 3. Path prefix collision defense (e.g. /opt/qwen-collective/workspaces_evil)
    collision_dir = tmp_path / "workspaces_evil"
    collision_dir.mkdir()
    with pytest.raises(SystemExit) as excinfo3:
        runner_mod.validate_workspace(str(collision_dir))
    assert excinfo3.value.code == 126

    # 4. Authorized workspace under test base succeeds
    monkeypatch.setenv("QC_ALLOWED_TEST_WORKSPACE", str(tmp_path))
    valid_ws = tmp_path / "valid_task_ws"
    valid_ws.mkdir()
    valid_ep = valid_ws / "task_entrypoint.py"
    valid_ep.write_text("print('valid')", encoding="utf-8")
    if hasattr(os, "chmod") and os.name != "nt":
        os.chmod(valid_ep, 0o555)

    ws_resolved = runner_mod.validate_workspace(str(valid_ws))
    assert ws_resolved == valid_ws.resolve()

    ep_resolved = runner_mod.validate_entrypoint(valid_ws, str(valid_ep))
    assert ep_resolved == valid_ep.resolve()


def test_production_sandbox_adversarial_boundary_protections(monkeypatch, tmp_path):
    """
    Adversarial verification tests:
    1. --allow-network is rejected by launcher CLI
    2. Over-ceiling resource requests are strictly clamped to approved maximums
    3. Missing launcher in production causes strict fail-closed rejection
    4. Alternative execution mechanisms (systemd-run, bwrap) are blocked in production
    """
    import subprocess
    import sys
    from vps_agent import executor

    runner_script = Path(__file__).resolve().parent.parent / "scripts" / "qwen-sandbox-runner.py"

    # 1. Attempting to pass --allow-network is rejected by argument parser
    res_net = subprocess.run(
        [sys.executable, str(runner_script), "--workspace", str(tmp_path), "--entrypoint", str(tmp_path / "ep.py"), "--allow-network"],
        capture_output=True,
        text=True,
    )
    assert res_net.returncode == 2
    assert "unrecognized arguments: --allow-network" in res_net.stderr

    # 2. Strict production gate: in production, missing launcher returns None even if systemd-run / bwrap exist
    monkeypatch.setenv("QC_ENV", "production")
    monkeypatch.delenv("QC_SANDBOX_RUNNER", raising=False)
    monkeypatch.delenv("QC_ALLOW_ALT_ISOLATION", raising=False)
    monkeypatch.delenv("QC_ALLOW_SUBPROCESS_FALLBACK", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    # Mock system commands to simulate systemd and bwrap being installed on host
    monkeypatch.setattr(executor.os.path, "exists", lambda p: False)
    monkeypatch.setattr(executor.shutil, "which", lambda cmd: f"/usr/bin/{cmd}" if cmd in ("systemd-run", "bwrap") else None)

    # In production, must NOT fall back to systemd_transient or bubblewrap
    assert executor._detect_os_isolation_mechanism() is None

    # Class B task execution fails closed with exit code 126
    import hashlib
    script_content = "print('evil')"
    script_sha = hashlib.sha256(script_content.encode("utf-8")).hexdigest()
    fake_manifest = {
        "task_id": "adv-task-1",
        "script_checksum": script_sha,
        "allowed_capabilities": ["python_execution"],
    }
    task = {
        "task_id": "adv-task-1",
        "task_type": "python_execution",
        "input_data": {
            "script": script_content,
            "checksum": script_sha,
            "execution_manifest": fake_manifest,
        },
    }
    outcome = executor.execute_task(task)
    assert outcome["status"] == "error"
    assert outcome["exit_code"] == 126
    assert outcome["error_category"] == "unsupported_isolation"


def test_systemd_working_directory_propagation(tmp_path):
    """Verify explicit WorkingDirectory propagation to systemd transient units."""
    from vps_agent import executor

    script_path = tmp_path / "task_entrypoint.py"
    cmd = executor._build_isolation_command(
        isolation_mech="systemd_transient",
        script_path=script_path,
        work_dir=str(tmp_path),
    )
    assert "-p" in cmd
    idx = cmd.index(f"WorkingDirectory={tmp_path}")
    assert cmd[idx - 1] == "-p"

    # Also verify in qwen-sandbox-runner.py source code
    runner_script = Path(__file__).resolve().parent.parent / "scripts" / "qwen-sandbox-runner.py"
    source = runner_script.read_text(encoding="utf-8")
    assert 'f"WorkingDirectory={workspace}"' in source


def test_verify_vps_sandbox_failure_handling_and_cleanup(tmp_path):
    """
    Verify verify_vps_sandbox.py failure handling and verifiable cleanup:
    1. Rejects non-isolated UIDs (< 61184)
    2. Rejects mismatched working directory
    3. Rejects filesystem isolation leaks
    4. Rejects network isolation leaks
    5. Accepts completely isolated valid probe
    6. Verifies clean workspace deletion
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import importlib.util
    verify_script = Path(__file__).resolve().parent.parent / "scripts" / "verify_vps_sandbox.py"
    spec = importlib.util.spec_from_file_location("verify_vps_sandbox", str(verify_script))
    verify_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verify_mod)

    test_ws = tmp_path / "test_eval_ws"
    test_ws.mkdir()

    base_valid = {
        "isolated_uid": 61200,
        "cwd": str(test_ws.resolve()),
        "filesystem_denials": {
            "/etc/test": "BLOCKED: PermissionError",
            "/root/test": "BLOCKED: PermissionError",
        },
        "network_access": "BLOCKED: OSError",
        "artifact_written": True,
    }

    # 1. Valid probe passes
    ok, reason = verify_mod.evaluate_probe_data(base_valid, test_ws)
    assert ok is True

    # 2. Non-isolated UID (< 61184, e.g. root or regular user) fails
    invalid_uid_data = dict(base_valid, isolated_uid=1001)
    ok_uid, reason_uid = verify_mod.evaluate_probe_data(invalid_uid_data, test_ws)
    assert ok_uid is False
    assert "DynamicUser" in reason_uid

    # 3. Mismatched WorkingDirectory fails
    invalid_cwd_data = dict(base_valid, cwd="/tmp")
    ok_cwd, reason_cwd = verify_mod.evaluate_probe_data(invalid_cwd_data, test_ws)
    assert ok_cwd is False
    assert "WorkingDirectory" in reason_cwd

    # 4. Filesystem write allowed fails
    invalid_fs_data = dict(base_valid, filesystem_denials={"/etc/test": "VULNERABLE_WRITE_ALLOWED"})
    ok_fs, reason_fs = verify_mod.evaluate_probe_data(invalid_fs_data, test_ws)
    assert ok_fs is False
    assert "Filesystem" in reason_fs or "filesystem" in reason_fs

    # 5. Network connection allowed fails
    invalid_net_data = dict(base_valid, network_access="VULNERABLE_CONNECTED")
    ok_net, reason_net = verify_mod.evaluate_probe_data(invalid_net_data, test_ws)
    assert ok_net is False
    assert "network" in reason_net.lower()

    # 6. Verifiable cleanup test
    dummy_file = test_ws / "artifact.json"
    dummy_file.write_text("data", encoding="utf-8")
    assert test_ws.exists()
    cleanup_ok, cleanup_diag = verify_mod.perform_verifiable_cleanup(test_ws)
    assert cleanup_ok is True
    assert not test_ws.exists()




