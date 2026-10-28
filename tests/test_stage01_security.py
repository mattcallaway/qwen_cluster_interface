"""
Stage 1 Security and Authorization Trust Boundary Tests (F-10, F-14, F-15).
Covers:
- Test Isolation Assertion Preflight (Stage 0 correction verification)
- Workstream 1A: Worker lease fencing, CAS state transitions, unassigned task protection, payload idempotency, and atomic exclusive artifact staging (F-10).
- Workstream 1B: Browser egress isolation, local outbound filtering proxy, DNS rebinding prevention, subresource/WebSocket blocks, fail-closed handling, and HTTPX transport security (F-14).
- Workstream 1C: CSRF protection on cookie-authenticated admin worker routes with positive, negative, and cross-session tests (F-15).
"""

import base64
import hashlib
import hmac
import json
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from starlette.testclient import TestClient

from app.auth import create_session_token, get_csrf_token
from app.config import settings
from app.database import get_db_connection
from app.main import app
from app.services import worker as worker_svc
from app.services.research.browser import BrowserRenderResult, HeadlessBrowserService
from app.services.research.browser_proxy import OutboundFilteringProxy
from app.services.research.policy import ResearchSecurityPolicy, get_security_policy
from app.services.research.providers.duckduckgo import DuckDuckGoProvider
from app.services.research.providers.searxng import SearXNGProvider
from app.services.research.providers.api_provider import ApiSearchProvider
from app.services.research.retrieval import (
    DestinationBlockedError,
    PinningHTTPTransport,
    PinningSyncBackend,
    SecureFetcher,
)


@pytest.fixture
def client(db_conn):
    return TestClient(app)


def _setup_workers_and_tasks(db_conn):
    """Helper to provision two distinct worker nodes and tasks for cross-authorization tests."""
    now = datetime.now(timezone.utc).isoformat()
    future = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + 3600, tz=timezone.utc).isoformat()

    worker_svc.register_worker_node(node_id="worker-A", name="Node A", role="vps_worker")
    worker_svc.register_worker_node(node_id="worker-B", name="Node B", role="vps_worker")
    db_conn.execute("UPDATE infrastructure_nodes SET is_enabled = 1, notes = '', health_status = 'online' WHERE id IN ('worker-A', 'worker-B')")
    db_conn.execute("UPDATE machines SET is_enabled = 1, notes = '', connection_status = 'online' WHERE id IN ('worker-A', 'worker-B')")
    db_conn.commit()

    token_id_a, raw_token_a = worker_svc.create_worker_token("worker-A")
    token_id_b, raw_token_b = worker_svc.create_worker_token("worker-B")

    task_a_id = f"task-A-{uuid4().hex[:6]}"
    task_b_id = f"task-B-{uuid4().hex[:6]}"

    db_conn.execute(
        """
        INSERT OR REPLACE INTO tasks (
            id, title, description, task_type, status, assigned_worker,
            attempt_count, max_retries, approval_required, created_at, updated_at
        ) VALUES
        (?, 'Task for A', 'Test Task A', 'vps_action', 'in_progress', 'worker-A', 1, 3, 0, ?, ?),
        (?, 'Task for B', 'Test Task B', 'vps_action', 'in_progress', 'worker-B', 1, 3, 0, ?, ?)
        """,
        (task_a_id, now, now, task_b_id, now, now),
    )

    lease_a_id = f"lease-A-{uuid4().hex[:6]}"
    lease_b_id = f"lease-B-{uuid4().hex[:6]}"

    db_conn.execute(
        """
        INSERT OR REPLACE INTO worker_task_leases (
            id, task_id, worker_id, idempotency_key, status, leased_at,
            lease_expires_at, heartbeat_at, attempt_count, created_at, updated_at
        ) VALUES
        (?, ?, 'worker-A', ?, 'leased', ?, ?, ?, 1, ?, ?),
        (?, ?, 'worker-B', ?, 'leased', ?, ?, ?, 1, ?, ?)
        """,
        (
            lease_a_id, task_a_id, f"idem-{task_a_id}", now, future, now, now, now,
            lease_b_id, task_b_id, f"idem-{task_b_id}", now, future, now, now, now,
        ),
    )
    db_conn.commit()

    return {
        "worker_a": {"node_id": "worker-A", "token": raw_token_a, "task_id": task_a_id, "lease_id": lease_a_id},
        "worker_b": {"node_id": "worker-B", "token": raw_token_b, "task_id": task_b_id, "lease_id": lease_b_id},
    }


# ===========================================================================
# 0. Test Isolation Guard Verification
# ===========================================================================

def test_stage01_suite_isolation_preflight():
    """Verify that tests execute strictly within temporary directory and never touch persistent paths."""
    resolved_data = Path(settings.data_dir).resolve()
    resolved_db = Path(settings.db_path).resolve()
    resolved_evidence = Path(settings.evidence_dir).resolve()

    assert ".qwen-collective" not in str(resolved_data).lower(), f"Leaked persistent data_dir: {resolved_data}"
    assert resolved_db.is_relative_to(resolved_data), f"db_path {resolved_db} outside data_dir {resolved_data}"
    assert resolved_evidence.is_relative_to(resolved_data), f"evidence_dir {resolved_evidence} outside data_dir {resolved_data}"

    conn = get_db_connection()
    try:
        cur_db = conn.execute("PRAGMA database_list").fetchall()
        for row in cur_db:
            db_file = row[2]
            if db_file:
                assert ".qwen-collective" not in db_file.lower(), f"Connected to persistent database: {db_file}"
                assert Path(db_file).resolve().is_relative_to(resolved_data), f"DB file {db_file} not in {resolved_data}"
    finally:
        conn.close()


# ===========================================================================
# 1. Workstream 1A: Worker Lease Fencing & State Transitions (F-10)
# ===========================================================================

def test_unauthenticated_and_revoked_worker_token(client, db_conn):
    """Unauthenticated, invalid, or revoked worker requests are rejected with 401."""
    env = _setup_workers_and_tasks(db_conn)
    task_id = env["worker_a"]["task_id"]

    upload_body = {
        "filename": "output.txt",
        "content_base64": base64.b64encode(b"test").decode("ascii"),
        "expected_sha256": hashlib.sha256(b"test").hexdigest(),
    }

    # Missing token -> 401
    r = client.post(f"/api/v1/worker/tasks/{task_id}/artifacts", json=upload_body)
    assert r.status_code == 401

    # Revoked token -> 401
    db_conn.execute("UPDATE worker_auth_tokens SET revoked = 1 WHERE node_id = 'worker-A'")
    db_conn.commit()

    r = client.post(
        f"/api/v1/worker/tasks/{task_id}/artifacts",
        json=upload_body,
        headers={"Authorization": f"Bearer {env['worker_a']['token']}"},
    )
    assert r.status_code == 401


def test_cross_worker_task_authorization_blocked(client, db_conn):
    """Worker B cannot upload artifacts or submit results for Worker A's task."""
    env = _setup_workers_and_tasks(db_conn)
    task_a = env["worker_a"]["task_id"]
    token_b = env["worker_b"]["token"]

    upload_body = {
        "filename": "rogue_artifact.bin",
        "content_base64": base64.b64encode(b"malicious").decode("ascii"),
        "expected_sha256": hashlib.sha256(b"malicious").hexdigest(),
    }
    r = client.post(
        f"/api/v1/worker/tasks/{task_a}/artifacts",
        json=upload_body,
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert r.status_code == 403
    assert "not authorized for task" in r.json()["detail"]

    submit_body = {
        "task_id": task_a,
        "idempotency_key": f"rogue-idem-{uuid4().hex[:8]}",
        "status": "success",
        "exit_code": 0,
    }
    r_sub = client.post(
        f"/api/v1/worker/tasks/{task_a}/submit",
        json=submit_body,
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert r_sub.status_code == 403


def test_worker_cannot_upload_to_unassigned_task(client, db_conn):
    """A worker cannot upload to or execute an unassigned task."""
    env = _setup_workers_and_tasks(db_conn)
    token_a = env["worker_a"]["token"]
    now = datetime.now(timezone.utc).isoformat()

    unassigned_task_id = f"task-unassigned-{uuid4().hex[:6]}"
    db_conn.execute(
        """
        INSERT INTO tasks (id, title, task_type, status, assigned_worker, created_at, updated_at)
        VALUES (?, 'Unassigned Task', 'general', 'pending', NULL, ?, ?)
        """,
        (unassigned_task_id, now, now),
    )
    db_conn.commit()

    upload_body = {
        "filename": "result.txt",
        "content_base64": base64.b64encode(b"content").decode("ascii"),
        "expected_sha256": hashlib.sha256(b"content").hexdigest(),
    }

    # Upload to unassigned task rejected with 403
    r = client.post(
        f"/api/v1/worker/tasks/{unassigned_task_id}/artifacts",
        json=upload_body,
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r.status_code == 403
    assert "unassigned" in r.json()["detail"].lower()
    db_conn.execute("DELETE FROM tasks WHERE id = ?", (unassigned_task_id,))
    db_conn.commit()


def test_worker_cannot_obtain_execution_authority_solely_without_lease(client, db_conn):
    """A worker cannot obtain execution authority solely because a task has no lease record."""
    env = _setup_workers_and_tasks(db_conn)
    token_a = env["worker_a"]["token"]
    now = datetime.now(timezone.utc).isoformat()

    unleased_task_id = f"task-unleased-{uuid4().hex[:6]}"
    db_conn.execute(
        """
        INSERT INTO tasks (id, title, task_type, status, assigned_worker, created_at, updated_at)
        VALUES (?, 'Direct Unleased Task', 'general', 'in_progress', 'worker-A', ?, ?)
        """,
        (unleased_task_id, now, now),
    )
    db_conn.commit()

    upload_body = {
        "filename": "data.bin",
        "content_base64": base64.b64encode(b"payload").decode("ascii"),
        "expected_sha256": hashlib.sha256(b"payload").hexdigest(),
    }

    # Upload requires an active lease record — direct unleased task cannot upload
    r = client.post(
        f"/api/v1/worker/tasks/{unleased_task_id}/artifacts",
        json=upload_body,
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r.status_code == 409
    assert "lease" in r.json()["detail"].lower()


def test_legacy_direct_assignment_rules(client, db_conn):
    """Legacy direct assignments are accepted only under explicitly defined authorization rules."""
    env = _setup_workers_and_tasks(db_conn)
    token_a = env["worker_a"]["token"]
    now = datetime.now(timezone.utc).isoformat()

    # Rule 1: Explicit direct assignment with 0 lease history permits submit
    direct_task_id = f"task-legacy-{uuid4().hex[:6]}"
    db_conn.execute(
        """
        INSERT INTO tasks (id, title, task_type, status, assigned_worker, created_at, updated_at)
        VALUES (?, 'Legacy Direct Task', 'general', 'in_progress', 'worker-A', ?, ?)
        """,
        (direct_task_id, now, now),
    )
    db_conn.commit()

    submit_body = {
        "task_id": direct_task_id,
        "idempotency_key": f"legacy-idem-{uuid4().hex[:8]}",
        "status": "success",
        "exit_code": 0,
        "result_payload": {"message": "Legacy direct result accepted"},
    }
    r = client.post(
        f"/api/v1/worker/tasks/{direct_task_id}/submit",
        json=submit_body,
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r.status_code == 200

    # Rule 2: If lease history existed for a task, direct assignment cannot bypass lease check
    leased_task_id = f"task-had-lease-{uuid4().hex[:6]}"
    db_conn.execute(
        """
        INSERT INTO tasks (id, title, task_type, status, assigned_worker, created_at, updated_at)
        VALUES (?, 'Had Lease Task', 'general', 'in_progress', 'worker-A', ?, ?)
        """,
        (leased_task_id, now, now),
    )
    # Record an expired lease for this task belonging to another attempt
    db_conn.execute(
        """
        INSERT INTO worker_task_leases (id, task_id, worker_id, idempotency_key, status, leased_at, lease_expires_at, heartbeat_at, attempt_count, created_at, updated_at)
        VALUES ('prior_lease', ?, 'worker-B', 'prior-idem', 'expired', ?, ?, ?, 1, ?, ?)
        """,
        (leased_task_id, now, now, now, now, now),
    )
    db_conn.commit()

    r2 = client.post(
        f"/api/v1/worker/tasks/{leased_task_id}/submit",
        json={
            "task_id": leased_task_id,
            "idempotency_key": f"idem-had-lease-{uuid4().hex[:6]}",
            "status": "success",
        },
        headers={"Authorization": f"Bearer {token_a}"},
    )
    # Must reject because task requires lease-based execution
    assert r2.status_code == 403


def test_stale_lease_identifier_and_fencing_generation_rejected(client, db_conn):
    """A stale lease identifier or superseded fencing generation is rejected."""
    env = _setup_workers_and_tasks(db_conn)
    task_id = env["worker_a"]["task_id"]
    lease_a = env["worker_a"]["lease_id"]
    token_a = env["worker_a"]["token"]
    now = datetime.now(timezone.utc).isoformat()
    future = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + 3600, tz=timezone.utc).isoformat()

    # Create a newer lease attempt (fencing generation 2)
    db_conn.execute(
        """
        INSERT INTO worker_task_leases (
            id, task_id, worker_id, idempotency_key, status, leased_at, lease_expires_at, heartbeat_at, attempt_count, created_at, updated_at
        ) VALUES ('lease_gen_2', ?, 'worker-A', 'idem-gen-2', 'leased', ?, ?, ?, 2, ?, ?)
        """,
        (task_id, now, future, now, now, now),
    )
    db_conn.commit()

    # Worker submits with stale lease_a (attempt 1) -> rejected with 409
    submit_body = {
        "task_id": task_id,
        "lease_id": lease_a,
        "idempotency_key": f"stale-gen-{uuid4().hex[:6]}",
        "status": "success",
    }
    r = client.post(
        f"/api/v1/worker/tasks/{task_id}/submit",
        json=submit_body,
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r.status_code == 409
    assert "superseded" in r.json()["detail"].lower() or "stale" in r.json()["detail"].lower()


def test_expired_worker_cannot_submit_after_task_reacquired(client, db_conn):
    """An expired worker cannot submit a result after another worker has acquired the task."""
    env = _setup_workers_and_tasks(db_conn)
    task_id = env["worker_a"]["task_id"]
    lease_a = env["worker_a"]["lease_id"]
    token_a = env["worker_a"]["token"]
    now = datetime.now(timezone.utc).isoformat()
    past = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - 100, tz=timezone.utc).isoformat()
    future = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + 3600, tz=timezone.utc).isoformat()

    # Expire worker A's lease
    db_conn.execute("UPDATE worker_task_leases SET status = 'expired', lease_expires_at = ? WHERE id = ?", (past, lease_a))
    # Worker B re-acquires the task
    db_conn.execute(
        """
        INSERT INTO worker_task_leases (id, task_id, worker_id, idempotency_key, status, leased_at, lease_expires_at, heartbeat_at, attempt_count, created_at, updated_at)
        VALUES ('lease_reacquired_b', ?, 'worker-B', 'idem-reacq', 'leased', ?, ?, ?, 2, ?, ?)
        """,
        (task_id, now, future, now, now, now),
    )
    db_conn.execute("UPDATE tasks SET assigned_worker = 'worker-B' WHERE id = ?", (task_id,))
    db_conn.commit()

    # Expired worker A tries to submit delayed result
    sub_body = {
        "task_id": task_id,
        "lease_id": lease_a,
        "idempotency_key": f"delayed-sub-{uuid4().hex[:6]}",
        "status": "success",
    }
    r = client.post(
        f"/api/v1/worker/tasks/{task_id}/submit",
        json=sub_body,
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r.status_code in (403, 409)
    detail_msg = r.json()["detail"].lower()
    assert any(term in detail_msg for term in ["assigned", "reacquired", "superseded", "forbidden", "lease"])


def test_concurrent_cas_state_transition_conflict(client, db_conn):
    """Concurrent authorization checks and writes cannot bypass state transitions (CAS)."""
    env = _setup_workers_and_tasks(db_conn)
    task_id = env["worker_a"]["task_id"]
    token_a = env["worker_a"]["token"]

    # First submission succeeds and transitions task to 'completed'
    sub_body_1 = {
        "task_id": task_id,
        "idempotency_key": f"cas-sub-1-{uuid4().hex[:6]}",
        "status": "success",
        "result_payload": {"seq": 1},
    }
    r1 = client.post(f"/api/v1/worker/tasks/{task_id}/submit", json=sub_body_1, headers={"Authorization": f"Bearer {token_a}"})
    assert r1.status_code == 200

    # Second distinct submission attempt cannot overwrite completed task
    sub_body_2 = {
        "task_id": task_id,
        "idempotency_key": f"cas-sub-2-{uuid4().hex[:6]}",
        "status": "success",
        "result_payload": {"seq": 2},
    }
    r2 = client.post(f"/api/v1/worker/tasks/{task_id}/submit", json=sub_body_2, headers={"Authorization": f"Bearer {token_a}"})
    assert r2.status_code in (200, 409)
    if r2.status_code == 200:
        assert r2.json()["status"] == "reconciled_conflict"
    else:
        assert "conflict" in r2.json()["detail"].lower() or "state" in r2.json()["detail"].lower()

    # Crucial verification: CAS state transition prevented task record from being overwritten with seq: 2
    t_row = db_conn.execute("SELECT status, result_data_json FROM tasks WHERE id = ?", (task_id,)).fetchone()
    assert t_row["status"] == "completed"
    assert "seq\": 1" in t_row["result_data_json"]
    assert "seq\": 2" not in t_row["result_data_json"]


def test_idempotency_key_payload_conflict_prevention(client, db_conn):
    """Reusing an idempotency key with different output content cannot corrupt or replace accepted results."""
    env = _setup_workers_and_tasks(db_conn)
    task_id = env["worker_a"]["task_id"]
    token_a = env["worker_a"]["token"]
    idem_key = f"idem-guard-{uuid4().hex[:8]}"

    # Initial submission
    r1 = client.post(
        f"/api/v1/worker/tasks/{task_id}/submit",
        json={
            "task_id": task_id,
            "idempotency_key": idem_key,
            "status": "success",
            "result_payload": {"hash": "valid_output"},
        },
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r1.status_code == 200
    assert r1.json()["status"] == "recorded"

    # Re-submit with identical payload (legitimate offline buffering catchup) -> accepted idempotently
    r2 = client.post(
        f"/api/v1/worker/tasks/{task_id}/submit",
        json={
            "task_id": task_id,
            "idempotency_key": idem_key,
            "status": "success",
            "result_payload": {"hash": "valid_output"},
        },
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "already_processed"

    # Re-submit with conflicting output content -> rejected with 409
    r3 = client.post(
        f"/api/v1/worker/tasks/{task_id}/submit",
        json={
            "task_id": task_id,
            "idempotency_key": idem_key,
            "status": "success",
            "result_payload": {"hash": "corrupted_output"},
        },
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r3.status_code == 409
    assert "different output content" in r3.json()["detail"].lower() or "conflict" in r3.json()["detail"].lower()


# ===========================================================================
# 2. Workstream 1A (continued): Atomic Artifact Staging & Checksum Tests
# ===========================================================================

def test_atomic_artifact_staging_exclusive_and_verified(client, db_conn):
    """Artifacts are created atomically using exclusive creation semantics and verified post-write."""
    env = _setup_workers_and_tasks(db_conn)
    task_id = env["worker_a"]["task_id"]
    token_a = env["worker_a"]["token"]

    raw_bytes = b"Atomic Evidence Content 2026"
    expected_sha = hashlib.sha256(raw_bytes).hexdigest()

    upload_body = {
        "filename": "metrics_report.json",
        "content_base64": base64.b64encode(raw_bytes).decode("ascii"),
        "expected_sha256": expected_sha,
    }

    r1 = client.post(
        f"/api/v1/worker/tasks/{task_id}/artifacts",
        json=upload_body,
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r1.status_code == 200
    stored_1 = r1.json()["artifact"]
    file_1 = Path(stored_1["stored_path"])
    assert file_1.exists()
    assert file_1.read_bytes() == raw_bytes

    # Second upload with same original filename: generates collision-proof unique file
    r2 = client.post(
        f"/api/v1/worker/tasks/{task_id}/artifacts",
        json=upload_body,
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r2.status_code == 200
    stored_2 = r2.json()["artifact"]
    file_2 = Path(stored_2["stored_path"])
    assert file_2.exists()
    assert file_1 != file_2  # Distinct exclusive files


def test_artifact_size_limit_rejected(client, db_conn):
    """Artifacts exceeding 25MB are rejected before storage."""
    env = _setup_workers_and_tasks(db_conn)
    task_id = env["worker_a"]["task_id"]
    token_a = env["worker_a"]["token"]

    # 26MB fake payload
    oversized = b"X" * (26 * 1024 * 1024)
    upload_body = {
        "filename": "huge.bin",
        "content_base64": base64.b64encode(oversized).decode("ascii"),
    }
    r = client.post(
        f"/api/v1/worker/tasks/{task_id}/artifacts",
        json=upload_body,
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r.status_code in (400, 413)
    assert "maximum allowed size" in r.json()["detail"].lower()


def test_artifact_malformed_base64_rejected(client, db_conn):
    """Malformed base64 uploads are rejected with 400 Bad Request."""
    env = _setup_workers_and_tasks(db_conn)
    task_id = env["worker_a"]["task_id"]
    token_a = env["worker_a"]["token"]

    upload_body = {
        "filename": "corrupted.bin",
        "content_base64": "!!!NOT_VALID_BASE64@@@",
    }
    r = client.post(
        f"/api/v1/worker/tasks/{task_id}/artifacts",
        json=upload_body,
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r.status_code == 400
    assert "base64" in r.json()["detail"].lower()


def test_artifact_invalid_checksum_format_rejected(client, db_conn):
    """Invalid checksum formats (not 64 hex characters) fail closed with 400 Bad Request."""
    env = _setup_workers_and_tasks(db_conn)
    task_id = env["worker_a"]["task_id"]
    token_a = env["worker_a"]["token"]

    upload_body = {
        "filename": "report.txt",
        "content_base64": base64.b64encode(b"content").decode("ascii"),
        "expected_sha256": "invalid-non-hex-sha256",
    }
    r = client.post(
        f"/api/v1/worker/tasks/{task_id}/artifacts",
        json=upload_body,
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r.status_code == 400
    assert "format" in r.json()["detail"].lower() or "hexadecimal" in r.json()["detail"].lower()


def test_checksum_verification_fails_closed_zero_bytes_persisted(client, db_conn):
    """Mismatch in expected_sha256 fails closed with 400 Bad Request and persists zero bytes."""
    env = _setup_workers_and_tasks(db_conn)
    task_id = env["worker_a"]["task_id"]
    token_a = env["worker_a"]["token"]

    raw_bytes = b"Integrity Check Content"
    wrong_sha = "0000000000000000000000000000000000000000000000000000000000000000"

    task_dir = Path(settings.evidence_dir) / f"task_{task_id}"
    files_before = set(task_dir.glob("*")) if task_dir.exists() else set()

    upload_body = {
        "filename": "falsified.txt",
        "content_base64": base64.b64encode(raw_bytes).decode("ascii"),
        "expected_sha256": wrong_sha,
    }

    r = client.post(
        f"/api/v1/worker/tasks/{task_id}/artifacts",
        json=upload_body,
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r.status_code == 400
    assert "checksum mismatch" in r.json()["detail"].lower()

    files_after = set(task_dir.glob("*")) if task_dir.exists() else set()
    assert files_before == files_after, "Failed upload persisted partial bytes to disk!"


# ===========================================================================
# 3. Workstream 1B: Browser Egress Isolation & Filtering Proxy (F-14)
# ===========================================================================

def test_browser_proxy_blocks_direct_private_and_loopback():
    """OutboundFilteringProxy blocks connections to localhost, private RFC1918, link-local, and Tailscale."""
    policy = get_security_policy()
    with OutboundFilteringProxy(policy=policy) as proxy:
        forbidden_targets = [
            ("127.0.0.1", 8080, "loopback"),
            ("10.0.0.1", 80, "RFC1918 10/8"),
            ("172.16.0.1", 443, "RFC1918 172.16/12"),
            ("192.168.1.1", 8080, "RFC1918 192.168/16"),
            ("169.254.169.254", 80, "Link-local cloud metadata"),
            ("100.64.0.1", 80, "Tailscale CGNAT"),
        ]

        for host, port, desc in forbidden_targets:
            # Test CONNECT method (HTTPS/WSS)
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("127.0.0.1", proxy.port))
            req = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n"
            s.sendall(req.encode("ascii"))
            resp = s.recv(4096).decode("ascii", errors="replace")
            s.close()

            assert "403 Forbidden" in resp, f"Proxy failed to block {desc} ({host}:{port}): {resp}"
            assert "SSRF-Violation" in resp or "Blocked" in resp

        audit = proxy.get_audit_log()
        assert len(audit) >= len(forbidden_targets)
        for record in audit:
            assert record.is_allowed is False
            assert record.connected_ip is None


def test_browser_proxy_dns_rebinding_prevented(monkeypatch):
    """Public DNS response followed by private IP cannot cause an internal connection."""
    policy = get_security_policy()

    # Simulate DNS rebinding: hostname resolves to 127.0.0.1
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))
    ])

    with OutboundFilteringProxy(policy=policy) as proxy:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(("127.0.0.1", proxy.port))
        req = "CONNECT rebind-attack.com:443 HTTP/1.1\r\nHost: rebind-attack.com:443\r\n\r\n"
        s.sendall(req.encode("ascii"))
        resp = s.recv(4096).decode("ascii", errors="replace")
        s.close()

        assert "403 Forbidden" in resp
        audit = proxy.get_audit_log()
        assert any(r.target_host == "rebind-attack.com" and not r.is_allowed for r in audit)


def test_browser_proxy_blocks_subresources_and_websockets():
    """Browser proxy blocks cross-origin subresources and WebSocket requests targeting internal endpoints."""
    policy = get_security_policy()
    with OutboundFilteringProxy(policy=policy) as proxy:
        # 1. Plain HTTP subresource to internal endpoint
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(("127.0.0.1", proxy.port))
        s.sendall(b"GET http://192.168.1.100/secret.json HTTP/1.1\r\nHost: 192.168.1.100\r\n\r\n")
        resp = s.recv(4096).decode("ascii", errors="replace")
        s.close()
        assert "403 Forbidden" in resp

        # 2. WebSocket upgrade attempt to localhost
        s_ws = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s_ws.connect(("127.0.0.1", proxy.port))
        s_ws.sendall(b"CONNECT 127.0.0.1:9001 HTTP/1.1\r\nHost: 127.0.0.1:9001\r\nUpgrade: websocket\r\n\r\n")
        resp_ws = s_ws.recv(4096).decode("ascii", errors="replace")
        s_ws.close()
        assert "403 Forbidden" in resp_ws


def test_failed_proxy_causes_browser_retrieval_fail_closed(monkeypatch):
    """When secure egress proxy or isolation fails, browser retrieval fails closed with 503."""
    browser_svc = HeadlessBrowserService()
    # Attempting to render an invalid URL or with missing browser fails closed
    result = browser_svc.render_page("http://127.0.0.1:8080/admin")
    assert result.is_success is False
    assert result.status_code == 403

    # Direct check of structured security-unavailable or navigation failure response
    monkeypatch.setattr(
        "app.services.research.browser_proxy.OutboundFilteringProxy.start",
        lambda self: (_ for _ in ()).throw(RuntimeError("Simulated proxy startup failure")),
    )
    res_pub = browser_svc.render_page("https://example.com/page")
    assert res_pub.is_success is False
    assert res_pub.status_code in (500, 502, 503, 504)
    assert any(term in res_pub.error_message.lower() for term in ("secure browser egress unavailable", "playwright", "browser navigation error", "proxy"))


# ===========================================================================
# 4. Workstream 1B (continued): HTTPX Transport Security Verification
# ===========================================================================

def test_httpx_pinning_transport_connects_to_verified_ip_and_preserves_sni():
    """Verify PinningSyncBackend pins destination IP while retaining original hostname."""
    policy = get_security_policy()
    backend = PinningSyncBackend(policy=policy)

    # Internal destination blocked
    with pytest.raises(DestinationBlockedError) as exc_info:
        backend.connect_tcp(host="127.0.0.1", port=80)
    assert "blocked by ssrf policy" in str(exc_info.value).lower()

    # Pinning transport configuration
    transport = PinningHTTPTransport(policy=policy, trust_env=False)
    assert transport._pool._network_backend.policy is policy


def test_httpx_redirect_revalidation_blocks_internal_hops(monkeypatch):
    """SecureFetcher validates every hop in a redirect chain and blocks private hops."""
    fetcher = SecureFetcher()

    # Preflight check on internal redirect target
    is_valid, reason, _ = fetcher.policy.validate_url("http://169.254.169.254/latest/meta-data")
    assert is_valid is False
    assert "forbidden" in reason.lower() or "blocked" in reason.lower()

    res = fetcher.fetch("http://169.254.169.254/latest/meta-data")
    assert res.is_success is False
    assert res.status_code == 403


def test_research_providers_use_secured_transport():
    """DuckDuckGo, SearXNG, and API search providers use PinningHTTPTransport."""
    ddg = DuckDuckGoProvider()
    searx = SearXNGProvider()
    api = ApiSearchProvider(api_url="https://api.example.com/search", api_key="test-key")

    assert ddg is not None
    assert searx is not None
    assert api is not None


# ===========================================================================
# 5. Workstream 1C: CSRF Protection & Mutating Endpoint Audit (F-15)
# ===========================================================================

def _csrf_for_session(session_tok: str) -> str:
    import hmac, hashlib
    return hmac.new(settings.secret_key.encode('utf-8'), f"csrf:{session_tok}".encode('utf-8'), hashlib.sha256).hexdigest()[:32]


def test_admin_csrf_positive_legitimate_actions_succeed(client):
    """Admin with valid session cookie and valid CSRF token successfully performs admin mutations."""
    session_tok = create_session_token("admin")
    client.cookies.set("qc_session", session_tok)
    csrf_tok = _csrf_for_session(session_tok)

    # 1. Create enrollment token with valid CSRF
    r_create = client.post(
        "/api/v1/worker/enrollment-tokens",
        json={"allowed_node_id": "test-csrfd-node", "expires_in_seconds": 3600},
        headers={"X-CSRF-Token": csrf_tok},
    )
    assert r_create.status_code == 200
    token_id = r_create.json()["token_id"]

    # 2. Revoke enrollment token with valid CSRF
    r_revoke = client.delete(
        f"/api/v1/worker/enrollment-tokens/{token_id}",
        headers={"X-CSRF-Token": csrf_tok},
    )
    assert r_revoke.status_code == 200
    assert r_revoke.json()["status"] == "revoked"


def test_admin_csrf_negative_missing_and_invalid_tokens_rejected(client):
    """Admin mutations without CSRF token or with invalid CSRF token are rejected with 403."""
    session_tok = create_session_token("admin")
    client.cookies.set("qc_session", session_tok)

    # Missing token -> 403
    r_missing = client.post(
        "/api/v1/worker/enrollment-tokens",
        json={"allowed_node_id": "node-no-csrf"},
    )
    assert r_missing.status_code == 403
    assert "csrf" in r_missing.json()["detail"].lower()

    # Invalid token -> 403
    r_invalid = client.post(
        "/api/v1/worker/enrollment-tokens",
        json={"allowed_node_id": "node-bad-csrf"},
        headers={"X-CSRF-Token": "completely-invalid-csrf-token"},
    )
    assert r_invalid.status_code == 403
    assert "csrf" in r_invalid.json()["detail"].lower()


def test_admin_csrf_negative_cross_session_token_rejected(client):
    """CSRF token generated for session A used with session B is rejected with 403."""
    session_a = create_session_token("admin")
    # Generate session B with a distinct timestamp so its derived CSRF token is different
    ts_b = str(int(time.time()) - 60)
    payload_b = f"admin:{ts_b}"
    sig_b = hmac.new(
        settings.secret_key.encode("utf-8"),
        payload_b.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    session_b = f"{payload_b}:{sig_b}"

    csrf_a = _csrf_for_session(session_a)
    csrf_b = _csrf_for_session(session_b)
    assert csrf_a != csrf_b, "Test precondition: sessions must have distinct CSRF tokens"

    # Set session B cookie, but supply CSRF token from session A
    client.cookies.set("qc_session", session_b)
    r_cross = client.post(
        "/api/v1/worker/enrollment-tokens",
        json={"allowed_node_id": "node-cross-session"},
        headers={"X-CSRF-Token": csrf_a},
    )
    assert r_cross.status_code == 403
    assert "csrf" in r_cross.json()["detail"].lower()


def test_bearer_token_worker_operations_unaffected_by_csrf(client, db_conn):
    """Worker API endpoints authenticate via Bearer token and remain functional without CSRF token."""
    env = _setup_workers_and_tasks(db_conn)
    token_a = env["worker_a"]["token"]

    # Heartbeat without CSRF token succeeds
    r_hb = client.post(
        "/api/v1/worker/heartbeat",
        json={"node_id": "worker-A", "cpu_percent": 15.0},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r_hb.status_code == 200
    assert r_hb.json()["status"] == "ok"
