"""Increment R4 Regression Tests — Lifecycle Consistency and Operational Recovery.

Covers:
- AUD-06: Unified authoritative project/run lifecycle across deliberate_loop, working_memory, engine, and orchestration routes; Migration 24 installs software_review_items (`code_proposals`).
- AUD-11a: Bounded lease-loss cancellation on worker (`VPSWorkerAgent` + `execute_task` / `_run_local_inference`) and controller rejection of late/revoked lease submissions.
- AUD-11b: Connection-independent remote Linux sandbox lifetime (`RuntimeMaxSec`, `ExecStopPost` staging cleanup) and remote driver detachment from SSH session.
- AUD-09: Truthful Paged KV (`QC_PAGED_KV_ENABLED=false` default, `telemetry_only_http_sidecar` vs `PyTorchPagedKVAdapter`) and pipeline parallelism capability reporting.
"""
from __future__ import annotations

import os
from pathlib import Path
import threading
import time
from typing import Any, Dict

import pytest

from app.database import create_baseline_schema, get_db_connection
from app.migrations import MIGRATIONS, apply_migrations
from app.services.orchestration import (
    acceptance_harness,
    deliberate_loop,
    engine,
    intake as intake_mod,
    protected_repo_verifier,
    working_memory as wm,
)


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    conn = get_db_connection()
    try:
        create_baseline_schema(conn)
        apply_migrations(conn, MIGRATIONS)
    finally:
        conn.close()
    return tmp_path


def test_aud06_migration24_installs_software_review_items_and_synchronizes_run_lifecycle(isolated_db: Path):
    """AUD-06:
    1. Migration 24 automatically creates `software_review_items` and append-only triggers on `init_db` / `apply_migrations`.
    2. Advancing, parking, resuming, steering, and cancelling a project via `deliberate_loop` / `working_memory`
       synchronizes `orchestration_runs.state` and `engine.status(project_id)` without contradictory states.
    """
    conn = get_db_connection()
    try:
        tbl = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='software_review_items'"
        ).fetchone()
        assert tbl is not None, "Migration 24 must create software_review_items table"
        trig = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name='software_review_no_update'"
        ).fetchone()
        assert trig is not None, "Migration 24 must install append-only triggers on software_review_items"
    finally:
        conn.close()

    # Create a project + run and advance 1 step
    created = engine.create_project_with_run(
        name="Lifecycle Sync Project",
        actor="tester",
        intake={
            "objective": "Evaluate supplier quotes under budget $14,800 and delivery <= 21 days for 10 units.",
        },
        autonomy_level=2,
    )
    pid = created["project"]["id"]
    run_id = created["run"]["id"]
    ledger = intake_mod.parse_intake_requirements({
        "objective": "Evaluate supplier quotes under budget $14,800 and delivery <= 21 days for 10 units.",
        "constraints": [
            "Maximum total budget ceiling: $14,800.00",
            "Maximum delivery lead time: 21 days",
            "Required capabilities: iso27001, telemetry_export",
            "Required dependencies: dep_power_bus, dep_rack_mount, dep_api_v2",
        ],
        "deliverables": [
            "quote_reconciliation_ledger.json",
            "supplier_recommendation.json",
            "supplier_recommendation.md",
        ],
    })
    intake_mod.persist_requirement_ledger(pid, ledger, run_id=run_id, actor="tester")

    # 1. Advance 1 step -> run state must be RUNNING (not stuck in DRAFT)
    step1 = deliberate_loop.advance_deliberate_project(
        pid,
        quotes_data=acceptance_harness.SYNTHETIC_SUPPLIER_QUOTES,
        max_steps=1,
        use_live_worker=False,
    )
    assert step1["final_cycle_phase"] == "execute"
    assert engine.get_run(run_id)["state"] == "RUNNING"

    # 2. Park project -> run state must be PAUSED
    wm.park_project(pid, actor="tester", reason="Pause for review")
    assert engine.get_run(run_id)["state"] == "PAUSED"
    eng_status_parked = engine.status(pid)
    assert eng_status_parked["run"]["state"] == "PAUSED"
    assert eng_status_parked.get("working_memory_cycle_phase") == "parked"

    # 3. Resume project -> run state must be RUNNING
    wm.resume_project(pid, actor="tester")
    assert engine.get_run(run_id)["state"] == "RUNNING"

    # 4. Complete remaining steps -> run state must be COMPLETED
    step_done = deliberate_loop.advance_deliberate_project(
        pid,
        quotes_data=acceptance_harness.SYNTHETIC_SUPPLIER_QUOTES,
        max_steps=5,
        use_live_worker=False,
    )
    assert step_done["final_cycle_phase"] == "completed"
    assert engine.get_run(run_id)["state"] == "COMPLETED"
    eng_status_done = engine.status(pid)
    assert eng_status_done["run"]["state"] == "COMPLETED"
    assert eng_status_done.get("working_memory_cycle_phase") == "completed"

    # 5. Steer a completed project with a new constraint -> invalidates downstream tasks and transitions run out of COMPLETED
    intake_mod.apply_user_steering_update(
        pid,
        run_id=run_id,
        actor="tester",
        action="steer",
        updated_constraints=["Maximum total budget ceiling: $12,000.00"],
        affected_task_keys=["t2_evaluate_constraints"],
        reason="Tighten budget after completion",
    )
    run_after_steer = engine.get_run(run_id)
    assert run_after_steer["state"] != "COMPLETED", (
        "Run must not remain COMPLETED after steering invalidates task contracts"
    )

    # 6. Cancel project -> run state must be CANCELLED
    wm.cancel_task_or_project(pid, actor="tester", reason="Cancel project")
    assert engine.get_run(run_id)["state"] == "CANCELLED"


def test_aud11a_worker_lease_loss_aborts_inflight_inference_within_bound(monkeypatch: pytest.MonkeyPatch):
    """AUD-11a: When `cancel_event` (`lease_lost`) fires during an in-flight worker inference task,
    `execute_task` / `_run_local_inference` aborts within <= 0.5s rather than blocking until full generation finishes.
    """
    from vps_agent import executor

    cancel_ev = threading.Event()

    class _SlowStreamingResponse:
        def __init__(self, path: str):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

        def read(self, amt: int = -1) -> bytes:
            if self.path.endswith("/api/tags"):
                return b'{"models": [{"name": "qwen2.5:3b"}]}'
            # Simulate a slow 10-second model generation
            for _ in range(100):
                time.sleep(0.05)
            return b'{"model": "qwen2.5:3b", "response": "late output", "eval_count": 5, "eval_duration": 1000000}'

        def close(self) -> None:
            pass

    class _FakeOpener:
        def open(self, req_or_url: Any, timeout: float = 10.0):
            url = req_or_url if isinstance(req_or_url, str) else req_or_url.full_url
            return _SlowStreamingResponse(url)

    monkeypatch.setattr("urllib.request.build_opener", lambda *args, **kwargs: _FakeOpener())

    # Trigger lease cancellation 0.15s after start
    def _trigger_cancel():
        time.sleep(0.15)
        cancel_ev.set()

    t = threading.Thread(target=_trigger_cancel, daemon=True)
    t0 = time.monotonic()
    t.start()
    res = executor.execute_task(
        {
            "task_id": "t_inf_cancel",
            "task_type": "model_inference",
            "input_data": {
                "model": "qwen2.5:3b",
                "prompt": "Write a detailed analysis.",
                "num_predict": 128,
            },
        },
        timeout_seconds=30,
        cancel_event=cancel_ev,
    )
    elapsed = time.monotonic() - t0
    assert elapsed < 0.75, f"Expected in-flight inference to abort promptly on lease loss, took {elapsed:.3f}s"
    assert res["status"] == "error"
    assert res["error_category"] in ("lease_cancelled", "cancelled")


def test_aud11b_linux_sandbox_and_remote_driver_enforce_connection_independent_cleanup():
    """AUD-11b:
    1. `LinuxSpec.command()` includes connection-independent lifetime (`RuntimeMaxSec`) and automatic
       unit/staging cleanup (`ExecStopPost`) so transient staging artifacts are cleaned up by systemd
       even if the SSH client disconnects.
    2. `REMOTE_LINUX_VERIFIER_DRIVER` registers a detached systemd transient cleanup unit (`systemd-run`)
       for `/etc/qwen-sandbox/test-host.json` and `/var/tmp/qc_verify_*` so an abrupt SSH disconnect
       cannot leave test-host authorization or temporary staging behind.
    """
    from vps_agent.linux_sandbox import LinuxSpec

    spec = LinuxSpec(
        "a" * 32,
        "b" * 64,
        "c" * 64,
        (("src/main.py", "d" * 64),),
        seconds=15,
        request_sha256="e" * 64,
    )
    cmd = spec.command()
    assert "--property=RuntimeMaxSec=15" in cmd
    assert any(item.startswith("--property=ExecStopPost=") for item in cmd), (
        "LinuxSpec.command() must include an ExecStopPost property to clean up request/staging state independently of the SSH client"
    )
    assert "systemd-run" in protected_repo_verifier.REMOTE_LINUX_VERIFIER_DRIVER
    assert "qc-verifier-cleanup-" in protected_repo_verifier.REMOTE_LINUX_VERIFIER_DRIVER


def test_aud09_paged_kv_and_pipeline_capability_reporting_is_truthful(isolated_db: Path, monkeypatch: pytest.MonkeyPatch):
    """AUD-09:
    1. `QC_PAGED_KV_ENABLED` is `false` by default.
    2. `get_paged_kv_metrics()` and `get_paged_kv_capabilities()` explicitly report that the FastAPI HTTP
       inference hook (`app/services/inference.py`) is a `telemetry_only_http_sidecar` (`external_http_runtime_kv_reuse_supported=False`),
       while genuine KV tensor reuse is scoped to in-process `PyTorchPagedKVAdapter` with `IssuedReuseAuthority`.
    3. `get_active_pipeline_topologies()` explicitly reports `multi_host_weight_execution_implemented=False`.
    """
    from app.services import inference as inf_mod
    from app.services.pipeline_parallel.topology import get_active_pipeline_topologies

    monkeypatch.delenv("QC_PAGED_KV_ENABLED", raising=False)
    inf_mod.reset_paged_kv_state()
    assert inf_mod.is_paged_kv_enabled() is False

    metrics = inf_mod.get_paged_kv_metrics()
    assert metrics["enabled"] is False
    assert metrics["integration_mode"] == "telemetry_only_http_sidecar"
    assert metrics["external_http_runtime_kv_reuse_supported"] is False
    assert metrics["in_process_pytorch_kv_reuse_supported"] is True

    topologies = get_active_pipeline_topologies()
    assert len(topologies) >= 1
    live_m = topologies[0].get("live_metrics") or {}
    assert live_m.get("multi_host_weight_execution_implemented") is False

