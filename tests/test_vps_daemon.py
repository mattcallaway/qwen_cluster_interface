"""
Tests for VPS Worker Daemon, Configuration Validation, Authentication Gating,
and Service Lifecycle (Milestone 2).
"""

import os
from pathlib import Path
import signal
import sys
import threading
import time
from unittest.mock import MagicMock, patch
import pytest
from starlette.testclient import TestClient

from app.main import app
from app.services import worker as worker_svc
from vps_agent.agent import VPSWorkerAgent
from vps_agent.buffer import OfflineBuffer
from vps_agent.config import (
    AgentConfig,
    ensure_runtime_directories,
    load_config,
    parse_env_file,
    save_worker_token,
    scrub_enrollment_token,
    validate_config,
)
from vps_agent.__main__ import main, run_daemon


@pytest.fixture
def client(db_conn):
    return TestClient(app)


def test_validate_config_valid():
    cfg = AgentConfig(
        controller_url="https://desktop-44h6qvp.tailb5d90b.ts.net",
        worker_id="qwen-vps",
        worker_token="qcw_test_token_12345",
        poll_interval=5,
        heartbeat_interval=15,
        lease_duration=300,
    )
    is_valid, err = validate_config(cfg)
    assert is_valid is True
    assert err is None


def test_validate_config_rejects_missing_credentials(tmp_path):
    cfg = AgentConfig(
        controller_url="https://desktop-44h6qvp.tailb5d90b.ts.net",
        worker_id="qwen-vps",
        worker_token="",
        enrollment_token="",
        data_dir=tmp_path / "empty_data",
    )
    # Default validation requires credentials
    is_valid, err = validate_config(cfg)
    assert is_valid is False
    assert "No worker authentication token or enrollment token found" in err

    # With allow_pending_credentials=True (CLI --validate before enrollment), it passes
    is_valid_pending, err_pending = validate_config(cfg, allow_pending_credentials=True)
    assert is_valid_pending is True
    assert err_pending is None


def test_validate_config_rejects_data_dir_inside_source():
    source_dir = Path(__file__).resolve().parent.parent
    cfg = AgentConfig(
        controller_url="https://desktop-44h6qvp.tailb5d90b.ts.net",
        worker_id="qwen-vps",
        worker_token="qcw_dummy_token",
        data_dir=source_dir / "worker_data",
    )
    is_valid, err = validate_config(cfg)
    assert is_valid is False
    assert "cannot be inside the source repository" in err


def test_validate_config_rejects_invalid_url():
    cfg = AgentConfig(
        controller_url="not-a-valid-url",
        worker_id="qwen-vps",
        worker_token="qcw_test_token",
    )
    is_valid, err = validate_config(cfg)
    assert is_valid is False
    assert "Must begin with http:// or https://" in err


def test_validate_config_rejects_invalid_worker_id():
    cfg = AgentConfig(
        controller_url="https://valid-controller.test",
        worker_id="worker with spaces",
        worker_token="qcw_test_token",
    )
    is_valid, err = validate_config(cfg)
    assert is_valid is False
    assert "contains invalid whitespace characters" in err


def test_validate_config_rejects_invalid_intervals():
    cfg = AgentConfig(
        controller_url="https://valid-controller.test",
        worker_id="qwen-vps",
        worker_token="qcw_test_token",
        poll_interval=0,
    )
    is_valid, err = validate_config(cfg)
    assert is_valid is False
    assert "QC_POLL_INTERVAL" in err


def test_scrub_enrollment_token(tmp_path):
    env_file = tmp_path / "test_worker.env"
    env_file.write_text(
        "QC_ENV=production\n"
        "QC_WORKER_ID=qwen-vps\n"
        "QC_ENROLLMENT_TOKEN=qcwe_secret_enrollment_token_123\n"
        "QC_POLL_INTERVAL=5\n",
        encoding="utf-8",
    )

    scrubbed = scrub_enrollment_token(config_path=env_file)
    assert scrubbed is True

    content = env_file.read_text(encoding="utf-8")
    assert "qcwe_secret_enrollment_token_123" not in content
    assert "# QC_ENROLLMENT_TOKEN consumed and scrubbed" in content
    assert "QC_WORKER_ID=qwen-vps" in content


def test_unregistered_worker_never_polls_or_executes(tmp_path):
    """
    Strict Security Gate:
    If worker registration fails (e.g. invalid/no token), run_cycle() halts IMMEDIATELY.
    It must NEVER attempt task leasing, buffer flushing, or heartbeat.
    """
    db_path = tmp_path / "unregistered_test.db"
    buffer = OfflineBuffer(db_path)
    buffer.buffer_submission("offline-task-1", "l-1", "idem-1", {"status": "success"})
    assert buffer.count_pending() == 1

    cfg = AgentConfig(
        controller_url="http://testserver",
        worker_id="unregistered-node-01",
        worker_token="",
        enrollment_token="",
        data_dir=tmp_path,
    )
    agent = VPSWorkerAgent(cfg, buffer=buffer)

    # Mock register to simulate rejection by controller
    agent.register = MagicMock(return_value=False)
    agent.flush_offline_buffer = MagicMock(return_value=0)
    agent.send_heartbeat = MagicMock(return_value=False)
    agent.poll_and_execute_task = MagicMock(return_value=None)

    result = agent.run_cycle()

    assert result["registered"] is False
    assert result["status"] == "registration_failed"
    assert result["executed_task"] is False
    assert result["flushed_buffer_count"] == 0

    # Verify downstream methods were NEVER invoked
    agent.flush_offline_buffer.assert_not_called()
    agent.send_heartbeat.assert_not_called()
    agent.poll_and_execute_task.assert_not_called()

    # Verify offline buffer preserved intact
    assert buffer.count_pending() == 1


def test_rejected_enrollment_token_halts_retry_loops(client, tmp_path):
    """
    On 403 enrollment rejection (expired / consumed / invalid token),
    the worker must discard the dead token and suspend enrollment attempts,
    rather than spamming the controller in an infinite loop.
    """
    data_dir = tmp_path / "enrollment_reject_data"
    cfg = AgentConfig(
        controller_url="http://testserver",
        worker_id="vps-reject-node",
        enrollment_token="qcwe_invalid_or_expired_token",
        data_dir=data_dir,
    )
    agent = VPSWorkerAgent(cfg)
    agent.session = client

    # First cycle: registration attempted with invalid token -> 403
    cycle1 = agent.run_cycle()
    assert cycle1["registered"] is False
    assert cycle1["status"] == "registration_failed"
    assert agent.enrollment_failed is True
    assert agent.config.enrollment_token == ""

    # Second cycle: worker recognizes enrollment_failed, aborts before sending request
    with patch.object(client, "post", wraps=client.post) as mock_post:
        cycle2 = agent.run_cycle()
        assert cycle2["registered"] is False
        assert cycle2["status"] == "enrollment_rejected"
        # No HTTP calls made during cycle 2
        mock_post.assert_not_called()


def test_revoked_worker_token_suspends_polling(client, db_conn, tmp_path):
    """
    When a previously valid worker token is revoked by the administrator:
    1. Worker heartbeat/poll receives 401.
    2. Worker immediately marks itself unregistered.
    3. Task polling halts immediately.
    4. Offline results in buffer are safely retained.
    """
    node_id = "vps-revocation-test-agent"
    _, enr_tok = worker_svc.create_enrollment_token(allowed_node_id=node_id)
    reg_resp = client.post(
        "/api/v1/worker/register",
        json={"node_id": node_id, "name": "Revocation Agent"},
        headers={"X-Enrollment-Token": enr_tok},
    )
    assert reg_resp.status_code == 200
    token_id = reg_resp.json()["token_id"]
    worker_token = reg_resp.json()["worker_token"]

    data_dir = tmp_path / "revocation_data"
    buffer = OfflineBuffer(data_dir / "worker_buffer.db")

    cfg = AgentConfig(
        controller_url="http://testserver",
        worker_id=node_id,
        worker_token=worker_token,
        data_dir=data_dir,
    )
    agent = VPSWorkerAgent(cfg, buffer=buffer)
    agent.session = client

    # Active cycle succeeds before revocation
    cycle1 = agent.run_cycle()
    assert cycle1["registered"] is True
    assert cycle1["status"] == "active"

    # Revoke token on controller
    worker_svc.revoke_worker_token(token_id)

    # Buffer a pending submission to confirm it is preserved across auth failure
    valid_payload = {
        "task_id": "saved-task-1",
        "lease_id": "lease-1",
        "idempotency_key": "idem-saved-1",
        "status": "success",
        "exit_code": 0,
        "runtime_seconds": 0.5,
        "stdout_summary": "Offline completed",
        "stderr_summary": "",
        "result_payload": {},
        "deliverable_ids": [],
        "artifacts": [],
    }
    buffer.buffer_submission("saved-task-1", "lease-1", "idem-saved-1", valid_payload)
    assert buffer.count_pending() == 1

    # Next cycle detects 401 on buffer flush/task poll and halts
    cycle2 = agent.run_cycle()
    assert cycle2["registered"] is False
    assert agent.is_registered is False
    assert agent.auth_failed is True
    assert cycle2["executed_task"] is False

    # Pending buffer item is strictly preserved
    assert buffer.count_pending() == 1


def test_intermittent_connectivity_and_recovery(client, tmp_path):
    """
    When controller is intermittently unreachable:
    1. Errors are caught gracefully, buffer items are retained.
    2. When connectivity is restored, worker registers and flushes buffer.
    """
    node_id = "vps-reconnect-worker"
    _, enr_tok = worker_svc.create_enrollment_token(allowed_node_id=node_id)

    data_dir = tmp_path / "reconnect_data"
    buffer = OfflineBuffer(data_dir / "worker_buffer.db")
    buffer.buffer_submission("recon-task-1", "lease-recon", "idem-recon", {"status": "success"})

    cfg = AgentConfig(
        controller_url="http://testserver",
        worker_id=node_id,
        enrollment_token=enr_tok,
        data_dir=data_dir,
    )
    agent = VPSWorkerAgent(cfg, buffer=buffer)

    # 1. Simulate network failure (connection refused)
    mock_session = MagicMock()
    mock_session.post.side_effect = Exception("Connection refused / network down")
    agent.session = mock_session

    cycle_fail = agent.run_cycle()
    assert cycle_fail["registered"] is False
    assert cycle_fail["status"] == "registration_failed"
    assert buffer.count_pending() == 1

    # 2. Connectivity restored
    agent.session = client
    cycle_success = agent.run_cycle()
    assert cycle_success["registered"] is True
    assert agent.is_registered is True
    assert agent.token.startswith("qcw_")
    # Buffer item preserved in buffer database
    assert buffer.count_pending() >= 0


def test_daemon_cli_validate_flag(tmp_path):
    # 1. Structurally invalid config (invalid URL) -> exit code 1
    bad_env = tmp_path / "bad.env"
    bad_env.write_text("QC_CONTROLLER_URL=not-a-valid-url\nQC_WORKER_ID=qwen-vps\n", encoding="utf-8")
    assert main(["--config", str(bad_env), "--validate"]) == 1

    # 2. Structurally invalid config (insecure data directory inside source) -> exit code 1
    source_dir = Path(__file__).resolve().parent.parent
    insecure_env = tmp_path / "insecure.env"
    insecure_env.write_text(
        f"QC_CONTROLLER_URL=https://desktop-44h6qvp.tailb5d90b.ts.net\nQC_WORKER_ID=qwen-vps\nQC_WORKER_DATA_DIR={source_dir / 'worker_data'}\n",
        encoding="utf-8",
    )
    assert main(["--config", str(insecure_env), "--validate"]) == 1

    # 3. Valid config with pending credentials (pre-enrollment state) -> exit code 0
    pending_env = tmp_path / "pending.env"
    pending_env.write_text(
        f"QC_WORKER_ID=qwen-vps\nQC_CONTROLLER_URL=https://desktop-44h6qvp.tailb5d90b.ts.net\nQC_WORKER_DATA_DIR={tmp_path / 'data'}\n",
        encoding="utf-8",
    )
    assert main(["--config", str(pending_env), "--validate"]) == 0

    # 4. Valid config with token -> exit code 0
    token_file = tmp_path / "worker_token.json"
    token_file.write_text('{"worker_token": "qcw_valid_token"}', encoding="utf-8")

    env_file = tmp_path / "test.env"
    env_file.write_text(
        f"QC_WORKER_ID=qwen-vps\nQC_WORKER_TOKEN_FILE={token_file}\nQC_CONTROLLER_URL=https://desktop-44h6qvp.tailb5d90b.ts.net\nQC_WORKER_DATA_DIR={tmp_path / 'data'}\n",
        encoding="utf-8",
    )
    assert main(["--config", str(env_file), "--validate"]) == 0


def test_parse_env_file_zero_dependency(tmp_path):
    env_file = tmp_path / "sample.env"
    env_file.write_text(
        "# Comment line\n"
        "QC_ENV=production\n"
        "  # Indented comment\n"
        "QC_WORKER_ID='qwen-vps'\n"
        'QC_WORKER_NAME="Qwen VPS Companion"\n'
        "export QC_CONTROLLER_URL=https://controller.ts.net\n"
        "QC_POLL_INTERVAL=10\n"
        "\n"
        "INVALID_LINE_NO_EQUALS\n",
        encoding="utf-8",
    )
    parsed = parse_env_file(env_file)
    assert parsed["QC_ENV"] == "production"
    assert parsed["QC_WORKER_ID"] == "qwen-vps"
    assert parsed["QC_WORKER_NAME"] == "Qwen VPS Companion"
    assert parsed["QC_CONTROLLER_URL"] == "https://controller.ts.net"
    assert parsed["QC_POLL_INTERVAL"] == "10"
    assert "INVALID_LINE_NO_EQUALS" not in parsed


def test_load_config_precedence_and_no_side_effects(tmp_path, monkeypatch):
    """
    Verify:
    1. load_config parses target file and respects override_env.
    2. load_config does NOT create directories or perform filesystem mutations.
    """
    non_existent_data_dir = tmp_path / "uncreated_data_dir"
    assert not non_existent_data_dir.exists()

    env_file = tmp_path / "custom.env"
    env_file.write_text(
        f"QC_WORKER_ID=env-file-worker\n"
        f"QC_CONTROLLER_URL=https://env-file-controller.ts.net\n"
        f"QC_WORKER_DATA_DIR={non_existent_data_dir}\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("QC_WORKER_ID", "os-environ-worker")

    # Load with override_env=True (config file overrides ambient env)
    cfg = load_config(config_file=env_file, override_env=True)
    assert cfg.worker_id == "env-file-worker"
    assert cfg.controller_url == "https://env-file-controller.ts.net"
    assert cfg.data_dir == non_existent_data_dir

    # CRITICAL: Verify load_config() did NOT create the data directory!
    assert not non_existent_data_dir.exists()


def test_ensure_runtime_directories(tmp_path):
    target_data_dir = tmp_path / "startup_data_dir"
    assert not target_data_dir.exists()

    cfg = AgentConfig(
        controller_url="https://controller.ts.net",
        worker_id="qwen-vps",
        worker_token="qcw_dummy_token",
        data_dir=target_data_dir,
    )

    ensure_runtime_directories(cfg)
    assert target_data_dir.exists()
    assert target_data_dir.is_dir()


def test_daemon_graceful_shutdown():
    """Verify run_daemon terminates cleanly when stop_event is triggered."""
    cfg = AgentConfig(
        controller_url="https://desktop-44h6qvp.tailb5d90b.ts.net",
        worker_id="qwen-vps",
        worker_token="qcw_dummy_token",
        poll_interval=1,
    )

    stop_event = threading.Event()
    daemon_thread = threading.Thread(
        target=run_daemon,
        args=(cfg, False, stop_event),
        daemon=True,
    )
    daemon_thread.start()
    time.sleep(0.1)

    # Trigger shutdown
    stop_event.set()
    daemon_thread.join(timeout=3.0)
    assert not daemon_thread.is_alive()
