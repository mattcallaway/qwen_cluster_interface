"""
Tests for Worker Management & Task Leasing Service (Milestone 2).
"""

import json
import sqlite3
import pytest
from datetime import datetime, timezone, timedelta

from app.database import get_db_connection
from app.services import worker as worker_svc


from uuid import uuid4


@pytest.fixture
def clean_node(db_conn):
    node_id = f"vps-node-{uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()
    db_conn.execute(
        """
        INSERT INTO infrastructure_nodes (
            id, name, role, os_name, cpu_info, installed_ram_mb, available_ram_mb,
            storage_total_gb, storage_free_gb, health_status, created_at, updated_at
        ) VALUES (?, 'VPS Test Node', 'vps_worker', 'Linux', '2 CPU cores', 2048, 1024, 40.0, 25.0, 'online', ?, ?)
        """,
        (node_id, now, now),
    )
    db_conn.commit()
    return node_id


def test_worker_token_lifecycle(db_conn, clean_node):
    # 1. Create token
    token_id, raw_token = worker_svc.create_worker_token(clean_node)
    assert token_id.startswith("tok_")
    assert raw_token.startswith("qcw_")

    # 2. Verify token
    verified = worker_svc.verify_worker_token(raw_token)
    assert verified is not None
    assert verified["node_id"] == clean_node
    assert verified["token_id"] == token_id

    # 3. Invalid token returns None
    assert worker_svc.verify_worker_token("invalid_token") is None
    assert worker_svc.verify_worker_token("qcw_nonexistent_hash_token") is None

    # 4. Revoke token
    assert worker_svc.revoke_worker_token(token_id) is True
    assert worker_svc.verify_worker_token(raw_token) is None


def test_worker_registration_and_heartbeat(db_conn):
    node_id = "vps-reg-test"
    res = worker_svc.register_worker_node(
        node_id=node_id,
        name="Registered Worker",
        role="vps_worker",
        cpu_count=4,
        ram_total_mb=4096,
        ram_available_mb=2048,
        declared_capabilities=["python_execution", "scheduled_execution"],
    )
    assert res["status"] == "registered"
    assert res["node_id"] == node_id

    # Verify updated in infrastructure_nodes
    row = db_conn.execute("SELECT * FROM infrastructure_nodes WHERE id = ?", (node_id,)).fetchone()
    assert row is not None
    assert row["name"] == "Registered Worker"
    assert "4 CPU cores" in row["cpu_info"]

    # Send heartbeat
    hb = worker_svc.record_worker_heartbeat(node_id, ram_available_mb=1800, disk_free_gb=30.5)
    assert hb["status"] == "ok"
    assert hb["node_id"] == node_id


def test_durable_task_leasing_and_concurrency(db_conn, clean_node):
    now = datetime.now(timezone.utc).isoformat()
    # Create approved task in tasks table
    task_id = "task-lease-01"
    db_conn.execute(
        """
        INSERT INTO tasks (
            id, title, description, task_type, status, approval_required,
            input_data_json, created_at, updated_at
        ) VALUES (?, 'Test Lease Task', 'Testing atomic lease', 'test_task', 'approved', 1, '{"test": 1}', ?, ?)
        """,
        (task_id, now, now),
    )
    db_conn.commit()

    # 1. Lease task for worker
    lease = worker_svc.lease_task_for_worker(clean_node, supported_categories=["test_task"])
    assert lease is not None
    assert lease["task_id"] == task_id
    assert lease["worker_id"] == clean_node
    assert lease["idempotency_key"].startswith("idem_")

    # 2. Concurrency check: A second worker cannot lease the same task
    worker2 = "vps-other-node"
    db_conn.execute(
        "INSERT OR REPLACE INTO infrastructure_nodes (id, name, role, created_at, updated_at) VALUES (?, 'Other', 'vps_worker', ?, ?)",
        (worker2, now, now),
    )
    db_conn.commit()
    second_lease = worker_svc.lease_task_for_worker(worker2)
    assert second_lease is None, "Second worker should not be able to claim the actively leased task"

    # 3. Renew lease heartbeat
    renewed = worker_svc.renew_task_lease(clean_node, lease["lease_id"], additional_seconds=600)
    assert renewed is True


def test_idempotent_task_result_submission(db_conn, clean_node):
    now = datetime.now(timezone.utc).isoformat()
    task_id = "task-sub-01"
    db_conn.execute(
        """
        INSERT INTO tasks (
            id, title, description, task_type, status, approval_required, created_at, updated_at
        ) VALUES (?, 'Submission Task', 'Testing submit', 'test_task', 'approved', 1, ?, ?)
        """,
        (task_id, now, now),
    )
    db_conn.commit()

    lease = worker_svc.lease_task_for_worker(clean_node)
    assert lease is not None
    lease_id = lease["lease_id"]
    idempotency_key = lease["idempotency_key"]

    # 1. First submission
    sub1 = worker_svc.submit_task_result(
        worker_id=clean_node,
        task_id=task_id,
        lease_id=lease_id,
        idempotency_key=idempotency_key,
        status="success",
        exit_code=0,
        runtime_seconds=1.25,
        stdout_summary="Test passed successfully.",
    )
    assert sub1["status"] == "recorded"
    assert sub1["task_status"] == "completed"

    # Verify task updated in tasks table
    task_row = db_conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
    assert task_row["status"] == "completed"

    # 2. Duplicate submission with same idempotency_key returns cached response safely
    sub2 = worker_svc.submit_task_result(
        worker_id=clean_node,
        task_id=task_id,
        lease_id=lease_id,
        idempotency_key=idempotency_key,
        status="success",
        exit_code=0,
    )
    assert sub2["status"] == "already_processed"
    assert sub2["execution_id"] == sub1["execution_id"]


def test_lease_expiration_and_recovery(db_conn, clean_node):
    now_dt = datetime.now(timezone.utc)
    expired_dt = now_dt - timedelta(minutes=10)
    task_id = "task-expired-01"

    db_conn.execute(
        """
        INSERT INTO tasks (
            id, title, description, task_type, status, approval_required, created_at, updated_at
        ) VALUES (?, 'Expired Lease Task', 'Testing recovery', 'test_task', 'in_progress', 0, ?, ?)
        """,
        (task_id, now_dt.isoformat(), now_dt.isoformat()),
    )
    # Insert expired lease
    db_conn.execute(
        """
        INSERT INTO worker_task_leases (
            id, task_id, worker_id, idempotency_key, status,
            leased_at, lease_expires_at, heartbeat_at, created_at, updated_at
        ) VALUES ('lease-exp-1', ?, ?, 'idem-exp-1', 'leased', ?, ?, ?, ?, ?)
        """,
        (task_id, clean_node, expired_dt.isoformat(), expired_dt.isoformat(), expired_dt.isoformat(), now_dt.isoformat(), now_dt.isoformat()),
    )
    db_conn.commit()

    # Run reconciliation
    recovered = worker_svc.reconcile_expired_leases()
    assert task_id in recovered

    # Verify task was reset to pending
    task_row = db_conn.execute("SELECT status, assigned_worker FROM tasks WHERE id = ?", (task_id,)).fetchone()
    assert task_row["status"] == "pending"
    assert task_row["assigned_worker"] is None


def test_execution_history_immutability(db_conn, clean_node):
    now = datetime.now(timezone.utc).isoformat()
    exec_id = "exec_immutable_test"
    db_conn.execute(
        """
        INSERT INTO worker_executions (
            id, task_id, worker_id, idempotency_key, status, exit_code,
            started_at, completed_at, runtime_seconds, created_at
        ) VALUES (?, 'task-dummy', ?, 'idem-immut', 'success', 0, ?, ?, 0.5, ?)
        """,
        (exec_id, clean_node, now, now, now),
    )
    db_conn.commit()

    # Attempt to modify: trigger must abort
    with pytest.raises(sqlite3.IntegrityError, match="worker_executions is append-only"):
        db_conn.execute("UPDATE worker_executions SET status = 'error' WHERE id = ?", (exec_id,))

    # Attempt to delete: trigger must abort
    with pytest.raises(sqlite3.IntegrityError, match="worker_executions is append-only"):
        db_conn.execute("DELETE FROM worker_executions WHERE id = ?", (exec_id,))
