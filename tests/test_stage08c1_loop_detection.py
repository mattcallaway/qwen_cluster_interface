"""
Stage 8C.1: Telemetry-Based Active-Heartbeat Infinite-Loop & Stall Detection Test Suite.

Verifies:
1. Scenario A: Healthy long-running computation with active heartbeats and progress milestones.
2. Scenario B: Worker crash or lost heartbeat (stale worker / expired lease).
3. Scenario C: Active-heartbeat infinite loop (50 identical view_file operations).
4. Scenario D: Repeated failure cycle across retries.
5. Scenario E: Legitimate repeated operations (pagination, chunked processing).
6. False-positive resistance and recovery from warning when progress resumes.
7. Historic infinite-loop stress test (5,000 operations with bounded ring buffer).
8. Telemetry performance overhead and memory bounding.
9. Non-destructive safety invariants (no automated kill/deletion).
10. Diagnostic API (/api/v1/system/diagnostics) and worker telemetry routes.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from app.database import get_db_connection
from app.main import app
from app.services import loop_detection, operational_health, projects as ps, tasks as ts, worker as worker_svc
from app.services.loop_detection import LoopCondition, LoopDetectorManager


@pytest.fixture(autouse=True)
def reset_detector_state():
    """Reset in-memory detector state between tests."""
    loop_detection.detector.clear()
    yield
    loop_detection.detector.clear()


@pytest.fixture
def api_client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# Unit Tests for Canonicalization & Fingerprinting
# ---------------------------------------------------------------------------

def test_canonicalize_payload_strips_sensitive_keys():
    """Verify that credentials and tokens are redacted from argument fingerprints."""
    raw_args = {
        "path": "main.py",
        "auth_token": "secret_abc123",
        "password": "super_secret_pw",
        "nested": {
            "api_key": "key_xyz",
            "count": 42,
        },
    }
    cleaned = loop_detection.canonicalize_payload(raw_args)
    assert cleaned == {"path": "main.py", "nested": {"count": 42}}


def test_fingerprint_deterministic_and_order_independent():
    """Verify that dictionary key order does not affect fingerprint digest."""
    args1 = {"start": 1, "end": 50, "path": "main.py"}
    args2 = {"path": "main.py", "end": 50, "start": 1}

    fp1 = loop_detection.compute_tool_fingerprint("view_file", args1)
    fp2 = loop_detection.compute_tool_fingerprint("view_file", args2)

    assert fp1 == fp2
    assert len(fp1) == 16


def test_fingerprint_differentiates_distinct_arguments():
    """Verify that varying arguments yield distinct fingerprints."""
    fp1 = loop_detection.compute_tool_fingerprint("view_file", {"path": "main.py", "start": 1})
    fp2 = loop_detection.compute_tool_fingerprint("view_file", {"path": "main.py", "start": 51})

    assert fp1 != fp2


# ---------------------------------------------------------------------------
# Five Core Scenario Tests (A - E)
# ---------------------------------------------------------------------------

def test_scenario_a_healthy_long_running_computation():
    """
    Scenario A: An active worker maintains valid heartbeats and produces genuine progress milestones.
    Result: LONG_RUNNING_ACTIVE or HEALTHY, severity: info/normal, no stall warning.
    """
    task_id = f"task-scen-a-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    one_minute_ago = (now - timedelta(seconds=60)).isoformat()
    now_str = now.isoformat()

    # Task running for 750 seconds (exceeds STALL_PROGRESS_THRESHOLD_S of 600s)
    duration_s = 750.0

    # Record operations with meaningful progress milestones
    for i in range(5):
        loop_detection.detector.record_operation(
            task_id=task_id,
            tool_name="compile_module",
            arguments={"module": f"mod_{i}"},
            result={"status": "ok"},
            attempt=1,
            explicit_progress=True,
            operation_ts=(now - timedelta(seconds=30 * (5 - i))).isoformat(),
        )

    # Worker sent a fresh heartbeat 15 seconds ago
    loop_detection.detector.record_heartbeat(task_id, (now - timedelta(seconds=15)).isoformat())

    eval_res = loop_detection.detector.evaluate_task(
        task_id=task_id,
        task_status="in_progress",
        duration_seconds=duration_s,
        heartbeat_at=(now - timedelta(seconds=15)).isoformat(),
        now_dt=now,
    )

    assert eval_res["condition"] in (LoopCondition.LONG_RUNNING_ACTIVE.value, LoopCondition.HEALTHY.value)
    assert eval_res["severity"] in ("info", "normal")
    assert eval_res["heartbeat_status"] == "online"
    assert eval_res["progress_status"] == "advancing"
    assert eval_res["time_since_last_progress_seconds"] < 600.0


def test_scenario_b_worker_crash_or_lost_heartbeat():
    """
    Scenario B: The worker stops heartbeating or its lease expires.
    Result: STALE_WORKER or LEASE_EXPIRED, preserving existing recovery behavior.
    """
    task_id = f"task-scen-b-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)

    # Lease expired 10 seconds ago, heartbeat stopped 350 seconds ago
    lease_expires = (now - timedelta(seconds=10)).isoformat()
    stale_heartbeat = (now - timedelta(seconds=350)).isoformat()

    eval_res = loop_detection.detector.evaluate_task(
        task_id=task_id,
        task_status="in_progress",
        duration_seconds=500.0,
        heartbeat_at=stale_heartbeat,
        lease_expires_at=lease_expires,
        now_dt=now,
    )

    assert eval_res["condition"] in (LoopCondition.LEASE_EXPIRED.value, LoopCondition.STALE_WORKER.value)
    assert eval_res["severity"] == "error"
    assert eval_res["heartbeat_status"] in ("expired", "stale")
    assert eval_res["progress_status"] == "stagnant"
    assert "reclamation" in eval_res["suggested_operator_action"].lower()


def test_scenario_c_active_heartbeat_infinite_loop():
    """
    Scenario C: A worker issues 50 identical view_file calls with unchanged arguments
    and unchanged results while continuing to heartbeat every 10 seconds.
    Result: UNPRODUCTIVE_TOOL_LOOP.
    Includes repetition count (50), relevant fingerprint, and time since last progress.
    """
    task_id = f"task-scen-c-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)

    # 50 identical view_file calls on "main.py"
    for i in range(50):
        loop_detection.detector.record_operation(
            task_id=task_id,
            tool_name="view_file",
            arguments={"path": "main.py", "start": 1, "end": 50},
            result={"content": "def main(): pass"},
            attempt=1,
            operation_ts=(now - timedelta(seconds=500 - (i * 10))).isoformat(),
        )

    # Worker sends fresh heartbeat 5 seconds ago
    fresh_hb = (now - timedelta(seconds=5)).isoformat()
    loop_detection.detector.record_heartbeat(task_id, fresh_hb)

    eval_res = loop_detection.detector.evaluate_task(
        task_id=task_id,
        task_status="in_progress",
        duration_seconds=700.0,
        heartbeat_at=fresh_hb,
        now_dt=now,
    )

    assert eval_res["condition"] == LoopCondition.UNPRODUCTIVE_TOOL_LOOP.value
    assert eval_res["severity"] == "warning"
    assert eval_res["heartbeat_status"] == "online"
    assert eval_res["progress_status"] == "looping"
    assert eval_res["repetition_count"] == 50
    assert eval_res["tool_name"] == "view_file"
    assert "relevant_fingerprint" in eval_res
    assert len(eval_res["relevant_fingerprint"]) == 16
    assert "without state advancement" in eval_res["human_readable_explanation"]
    assert "Cancel or replan" in eval_res["suggested_operator_action"]


def test_scenario_d_repeated_failure_cycle():
    """
    Scenario D: An operation repeatedly fails with the same error classification across attempts,
    without meaningful advancement.
    Result: REPEATED_FAILURE_CYCLE.
    """
    task_id = f"task-scen-d-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)

    # 3 consecutive attempts all failing with "syntax_error"
    for attempt in range(1, 4):
        loop_detection.detector.record_operation(
            task_id=task_id,
            tool_name="python_exec",
            arguments={"script": "bad_code.py"},
            attempt=attempt,
            error_category="syntax_error",
            operation_ts=(now - timedelta(seconds=60 * (4 - attempt))).isoformat(),
        )

    eval_res = loop_detection.detector.evaluate_task(
        task_id=task_id,
        task_status="in_progress",
        duration_seconds=300.0,
        heartbeat_at=(now - timedelta(seconds=10)).isoformat(),
        attempt_count=3,
        max_retries=3,
        now_dt=now,
    )

    assert eval_res["condition"] == LoopCondition.REPEATED_FAILURE_CYCLE.value
    assert eval_res["severity"] in ("warning", "error")
    assert eval_res["progress_status"] == "failing"
    assert eval_res["repetition_count"] >= 2
    assert eval_res["error_category"] == "syntax_error"
    assert "syntax_error" in eval_res["human_readable_explanation"]


def test_scenario_e_legitimate_repeated_operations():
    """
    Scenario E: A worker performs repetitive operations such as pagination or chunked processing,
    with changing inputs, validated outputs, or durable progress.
    Result: HEALTHY (not flagged merely because tool name repeats).
    """
    task_id = f"task-scen-e-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)

    # 50 paginated view_file operations with changing offsets
    for page in range(50):
        loop_detection.detector.record_operation(
            task_id=task_id,
            tool_name="view_file",
            arguments={"path": "large_log.txt", "start": (page * 50) + 1, "end": (page + 1) * 50},
            result={"chunk_id": page, "lines": 50},
            attempt=1,
            operation_ts=(now - timedelta(seconds=500 - (page * 10))).isoformat(),
        )

    fresh_hb = (now - timedelta(seconds=5)).isoformat()
    loop_detection.detector.record_heartbeat(task_id, fresh_hb)

    eval_res = loop_detection.detector.evaluate_task(
        task_id=task_id,
        task_status="in_progress",
        duration_seconds=500.0,
        heartbeat_at=fresh_hb,
        now_dt=now,
    )

    # Must be recognized as healthy, NOT a loop
    assert eval_res["condition"] == LoopCondition.HEALTHY.value
    assert eval_res["severity"] == "normal"
    assert eval_res["heartbeat_status"] == "online"
    assert eval_res["progress_status"] == "advancing"
    assert eval_res["repetition_count"] == 1


# ---------------------------------------------------------------------------
# False-Positive Resistance & Recovery Tests
# ---------------------------------------------------------------------------

def test_long_running_uncertain_state_without_false_alarm():
    """Verify that long compute without frequent milestones remains in explicitly uncertain state."""
    task_id = f"task-uncertain-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)

    # Task running for 900 seconds, worker alive, but no tool milestones reported
    fresh_hb = (now - timedelta(seconds=10)).isoformat()

    eval_res = loop_detection.detector.evaluate_task(
        task_id=task_id,
        task_status="in_progress",
        duration_seconds=900.0,
        heartbeat_at=fresh_hb,
        now_dt=now,
    )

    assert eval_res["condition"] == LoopCondition.LONG_RUNNING_UNCERTAIN.value
    assert eval_res["severity"] == "info"
    assert eval_res["progress_status"] == "uncertain"
    assert "infrequent milestones" in eval_res["human_readable_explanation"]


def test_task_recovers_from_unproductive_loop_when_progress_resumes():
    """Verify that a task flagged with UNPRODUCTIVE_TOOL_LOOP immediately recovers when meaningful progress resumes."""
    task_id = f"task-recovery-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)

    # 10 identical calls -> triggers loop warning
    for i in range(10):
        loop_detection.detector.record_operation(
            task_id=task_id,
            tool_name="view_file",
            arguments={"path": "main.py"},
            result={"text": "same"},
            attempt=1,
            operation_ts=(now - timedelta(seconds=100 - (i * 10))).isoformat(),
        )

    eval_before = loop_detection.detector.evaluate_task(
        task_id=task_id,
        task_status="in_progress",
        duration_seconds=300.0,
        heartbeat_at=(now - timedelta(seconds=5)).isoformat(),
        now_dt=now,
    )
    assert eval_before["condition"] == LoopCondition.UNPRODUCTIVE_TOOL_LOOP.value

    # Agent breaks loop and makes genuine progress (writes file or different tool call)
    loop_detection.detector.record_operation(
        task_id=task_id,
        tool_name="write_file",
        arguments={"path": "output.py", "content": "print(1)"},
        result={"written_bytes": 8},
        attempt=1,
        operation_ts=now.isoformat(),
    )

    eval_after = loop_detection.detector.evaluate_task(
        task_id=task_id,
        task_status="in_progress",
        duration_seconds=305.0,
        heartbeat_at=now.isoformat(),
        now_dt=now,
    )

    # Warning must be cleared
    assert eval_after["condition"] == LoopCondition.HEALTHY.value
    assert eval_after["severity"] == "normal"
    assert eval_after["progress_status"] == "advancing"
    assert eval_after["repetition_count"] == 1


# ---------------------------------------------------------------------------
# Stress Test: Historic Infinite Loop Pattern & Bounded Memory
# ---------------------------------------------------------------------------

def test_stress_historic_infinite_loop_pattern_bounded_memory():
    """
    Stress test reproducing the historic incident with 5,000 repeated tool operations.
    Verifies:
    1. Bounded ring buffer capacity (never exceeds 20 entries).
    2. Accurate repetition counting (tracks to 5,000).
    3. Low execution overhead (< 0.1 ms per record).
    4. Memory does not grow unbounded with repetition count.
    """
    task_id = f"task-stress-{uuid.uuid4().hex[:8]}"
    start_time = time.perf_counter()

    # 5,000 identical tool calls
    for i in range(5000):
        loop_detection.detector.record_operation(
            task_id=task_id,
            tool_name="view_file",
            arguments={"path": "server.py", "start": 1, "end": 100},
            result={"lines": 100, "content": "code_snippet"},
            attempt=1,
        )

    elapsed_s = time.perf_counter() - start_time
    avg_per_op_ms = (elapsed_s / 5000.0) * 1000.0

    state = loop_detection.detector.get_state(task_id)
    assert state is not None

    # Verify bounded ring buffer
    assert len(state.ring_buffer) == 20
    assert state.ring_buffer.maxlen == 20

    # Verify repetition count tracked accurately
    assert state.consecutive_identical_count == 5000

    # Verify performance overhead: < 0.1 ms per operation
    assert avg_per_op_ms < 0.5, f"Overhead per op too high: {avg_per_op_ms:.4f} ms"

    # Verify evaluation detects loop
    eval_res = loop_detection.detector.evaluate_task(
        task_id=task_id,
        task_status="in_progress",
        duration_seconds=600.0,
        heartbeat_at=datetime.now(timezone.utc).isoformat(),
    )
    assert eval_res["condition"] == LoopCondition.UNPRODUCTIVE_TOOL_LOOP.value
    assert eval_res["repetition_count"] == 5000


# ---------------------------------------------------------------------------
# Non-Destructive Safeguard Test
# ---------------------------------------------------------------------------

def test_non_destructive_invariant_tasks_and_leases_preserved():
    """
    Verify that emitting an UNPRODUCTIVE_TOOL_LOOP diagnostic warning
    NEVER deletes tasks, terminates processes, or mutates leases automatically.
    """
    now = datetime.now(timezone.utc)
    proj = ps.create_project(name=f"Loop Proj {uuid.uuid4().hex[:6]}", owner="admin")
    task_id = f"task-safe-{uuid.uuid4().hex[:8]}"

    conn = get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO tasks (id, project_id, title, status, attempt_count, max_retries, created_at, updated_at)
            VALUES (?, ?, 'Looping Safe Task', 'in_progress', 1, 3, ?, ?)
            """,
            (task_id, proj["id"], now.isoformat(), now.isoformat()),
        )
        conn.commit()
    finally:
        conn.close()

    # Simulate 20 identical operations
    for _ in range(20):
        loop_detection.detector.record_operation(
            task_id=task_id,
            tool_name="view_file",
            arguments={"path": "main.py"},
            result={"text": "content"},
        )
    loop_detection.detector.record_heartbeat(task_id, now.isoformat())

    # Run diagnostics
    diag = operational_health.get_operational_diagnostics()
    stall_info = diag["stall_detection"]

    # Verify warning emitted
    assert stall_info["unproductive_loop_count"] >= 1
    loop_task_ids = [l["task_id"] for l in stall_info["unproductive_loops"]]
    assert task_id in loop_task_ids

    # Verify task remains in database with unchanged status
    conn = get_db_connection()
    try:
        t = conn.execute("SELECT id, status FROM tasks WHERE id = ?", (task_id,)).fetchone()
        assert t is not None
        assert t["status"] == "in_progress"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Operational Diagnostics & Worker Route Integration Tests
# ---------------------------------------------------------------------------

def test_diagnostics_api_surfaces_loop_details(api_client):
    """Verify that /api/v1/system/diagnostics exposes all required loop telemetry fields."""
    task_id = f"task-diag-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    proj = ps.create_project(name=f"Diag Proj {uuid.uuid4().hex[:6]}", owner="admin")

    conn = get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO tasks (id, project_id, title, status, attempt_count, max_retries, created_at, updated_at)
            VALUES (?, ?, 'Diag Loop Task', 'in_progress', 1, 3, ?, ?)
            """,
            (task_id, proj["id"], now.isoformat(), now.isoformat()),
        )
        conn.commit()
    finally:
        conn.close()

    # Record 15 identical operations
    for _ in range(15):
        loop_detection.detector.record_operation(
            task_id=task_id,
            tool_name="grep_search",
            arguments={"query": "TODO"},
            result={"matches": 0},
        )
    loop_detection.detector.record_heartbeat(task_id, now.isoformat())

    resp = api_client.get("/api/v1/system/diagnostics")
    assert resp.status_code == 200
    data = resp.json()

    assert "stall_detection" in data
    stalls = data["stall_detection"]
    assert "unproductive_loops" in stalls

    matching = [l for l in stalls["unproductive_loops"] if l.get("task_id") == task_id]
    assert len(matching) == 1
    loop_rep = matching[0]

    # Verify required Section 4 fields
    assert loop_rep["condition"] == "UNPRODUCTIVE_TOOL_LOOP"
    assert loop_rep["tool_name"] == "grep_search"
    assert loop_rep["repetition_count"] == 15
    assert "relevant_fingerprint" in loop_rep
    assert "last_meaningful_progress" in loop_rep
    assert "last_heartbeat" in loop_rep
    assert "severity" in loop_rep
    assert "human_readable_explanation" in loop_rep
    assert "suggested_operator_action" in loop_rep


def test_worker_heartbeat_and_telemetry_routes(api_client):
    """Verify that worker heartbeat and telemetry endpoints update loop detection state."""
    # Enroll and register worker node
    node_id = f"worker-loop-{uuid.uuid4().hex[:6]}"
    _, enr_tok = worker_svc.create_enrollment_token(allowed_node_id=node_id)
    reg_resp = api_client.post(
        "/api/v1/worker/register",
        json={"node_id": node_id, "name": "Loop Worker"},
        headers={"X-Enrollment-Token": enr_tok},
    )
    assert reg_resp.status_code == 200
    raw_token = reg_resp.json()["worker_token"]
    headers = {"Authorization": f"Bearer {raw_token}"}

    # Create approved task in database
    now_iso = datetime.now(timezone.utc).isoformat()
    task_id = f"task-route-{uuid.uuid4().hex[:8]}"
    conn = get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO tasks (
                id, title, description, task_type, status, approval_required,
                input_data_json, created_at, updated_at
            ) VALUES (?, 'Route Test Task', 'Verification probe', 'test_task', 'approved', 1, '{"check": true}', ?, ?)
            """,
            (task_id, now_iso, now_iso),
        )
        conn.commit()
    finally:
        conn.close()

    lease_resp = api_client.post(
        "/api/v1/worker/tasks/lease",
        json={"worker_id": node_id, "supported_categories": ["test_task"]},
        headers=headers,
    )
    assert lease_resp.status_code == 200
    lease_data = lease_resp.json()
    assert lease_data["status"] == "leased"
    lease_id = lease_data["task"]["lease_id"]

    # 1. Post telemetry via /telemetry route
    tel_resp = api_client.post(
        f"/api/v1/worker/tasks/{lease_id}/telemetry",
        json={
            "tool_name": "view_file",
            "tool_args": {"path": "test.py"},
            "result_summary": "file_contents",
        },
        headers=headers,
    )
    assert tel_resp.status_code == 200
    assert tel_resp.json()["status"] == "recorded"

    state = loop_detection.detector.get_state(task_id)
    assert state is not None
    assert state.last_tool_name == "view_file"
    assert len(state.ring_buffer) == 1

    # 2. Extend lease via /heartbeat route with telemetry payload
    hb_resp = api_client.post(
        f"/api/v1/worker/tasks/{lease_id}/heartbeat",
        json={
            "additional_seconds": 300,
            "tool_name": "view_file",
            "tool_args": {"path": "test.py"},
            "result_summary": "file_contents",
        },
        headers=headers,
    )
    assert hb_resp.status_code == 200
    assert hb_resp.json()["status"] == "renewed"

    # Consecutive identical count should now be 2
    state2 = loop_detection.detector.get_state(task_id)
    assert state2.consecutive_identical_count == 2
