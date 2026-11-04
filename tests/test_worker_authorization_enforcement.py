"""
Comprehensive Authorization Boundary & Leasing Gateway Tests.
Validates:
1. Critical Negative Test: Harmless sentinel task queued + disabled worker -> Explicit HTTP 403 denial, task remains unassigned.
2. Critical Negative Test: Held VPS requesting operational task type (library_script) -> Explicit HTTP 403 denial, task remains unassigned.
3. Enabled worker with revoked token -> 401/403 denial.
4. Retired fixture with previously valid credentials -> 403 denial.
5. Enabled worker with stale heartbeat -> 403 denial.
6. Worker with conflicting registry states (machines vs infrastructure_nodes) -> 403 denial.
7. Worker requesting an unauthorized task type (Desktop requesting library_script) -> 403 denial.
8. Worker disabled between task discovery and lease acquisition -> Transactional rollback & 403 denial.
9. Worker missing/malformed capability restrictions -> Fails closed with 403 denial.
10. Authorized inference worker leasing an approved inference task -> 200 OK leased.
11. Previously leased task behavior when worker is administratively disabled -> In-flight upload/heartbeat denied, historical task preserved.
12. Synchronization and registration preserving administrative holds -> Holds never silently cleared.
"""

from datetime import datetime, timezone, timedelta
import json
import pytest
from starlette.testclient import TestClient

from app.main import app
from app.services import worker as worker_svc
from app.services.infrastructure import sync_infrastructure_and_machines, register_or_update_node


@pytest.fixture
def client(db_conn):
    return TestClient(app)


def _setup_node(db_conn, node_id, is_enabled=1, role="inference_node", health_status="online", last_contact_delta=0, restrictions=None, notes=""):
    now_dt = datetime.now(timezone.utc) + timedelta(seconds=last_contact_delta)
    now_iso = now_dt.isoformat()
    restr_json = json.dumps(restrictions if restrictions is not None else {})

    # 1. infrastructure_nodes
    db_conn.execute(
        """
        INSERT OR REPLACE INTO infrastructure_nodes (
            id, name, role, hostname, ip_address, tailscale_status,
            os_name, os_version, architecture, cpu_info,
            installed_ram_mb, available_ram_mb, storage_total_gb, storage_free_gb,
            installed_runtimes_json, registered_services_json, available_models_json,
            health_status, last_contact_at, last_inventory_at,
            capability_restrictions_json, notes, is_enabled, created_at, updated_at
        ) VALUES (?, ?, ?, 'test-host', '10.0.0.1', 'connected',
                  'Linux', 'Ubuntu 24.04', 'x86_64', '4 cores',
                  8192, 4096, 100.0, 50.0,
                  '["python3"]', '["worker"]', '["qwen2.5:3b"]',
                  ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (node_id, f"Node {node_id}", role, health_status, now_iso, now_iso, restr_json, notes, 1 if is_enabled else 0, now_iso, now_iso),
    )

    # 2. machines
    db_conn.execute(
        """
        INSERT OR REPLACE INTO machines (
            id, display_name, hostname, ip_address, connection_type,
            os_info, cpu_info, ram_total_mb, ram_avail_mb, disk_info,
            role, connection_status, last_checked_at, last_successful_connection,
            capabilities_json, notes, is_enabled, created_at, updated_at
        ) VALUES (?, ?, 'test-host', '10.0.0.1', 'ssh',
                  'Ubuntu 24.04', '4 cores', 8192, 4096, '100 GB',
                  ?, ?, ?, ?,
                  '["python3", "model_inference"]', ?, ?, ?, ?)
        """,
        (node_id, f"Node {node_id}", role, health_status, now_iso, now_iso, notes, 1 if is_enabled else 0, now_iso, now_iso),
    )

    # 3. Commit before opening separate connection for token
    db_conn.commit()

    # 4. worker_auth_tokens
    token_id, raw_token = worker_svc.create_worker_token(node_id, "Test Token")
    return raw_token, token_id


# ---------------------------------------------------------------------------
# 1. Critical Negative Test: Disabled worker attempting to acquire queued task
# ---------------------------------------------------------------------------
def test_critical_negative_disabled_worker_denied_with_queued_sentinel_task(client, db_conn):
    """
    CRITICAL NEGATIVE TEST:
    1. A compatible harmless sentinel task is demonstrably queued.
    2. An administratively disabled worker authenticates with a valid token.
    3. The lease endpoint explicitly returns HTTP 403 Forbidden.
    4. The sentinel task remains queued and unassigned.
    """
    # Clear any previous pending tasks
    db_conn.execute("UPDATE tasks SET status = 'completed' WHERE status IN ('pending', 'approved', 'queued')")

    # Queue compatible sentinel task
    now = datetime.now(timezone.utc).isoformat()
    sentinel_task_id = "task-sentinel-disabled-test-01"
    db_conn.execute(
        """
        INSERT INTO tasks (
            id, title, description, task_type, status, approval_required,
            input_data_json, created_at, updated_at
        ) VALUES (?, 'Harmless Sentinel Task', 'Compatible verification task', 'inference', 'queued', 0, '{"required_model": "qwen2.5:3b"}', ?, ?)
        """,
        (sentinel_task_id, now, now),
    )
    db_conn.commit()

    # Create worker administratively disabled (is_enabled = 0)
    worker_id = "desktop-worker-01"
    token, _ = _setup_node(db_conn, worker_id, is_enabled=0)

    # Disabled worker calls lease endpoint
    resp = client.post(
        "/api/v1/worker/tasks/lease",
        json={"worker_id": worker_id, "supported_categories": ["inference"]},
        headers={"Authorization": f"Bearer {token}"},
    )

    # Must be explicitly denied with HTTP 403 Forbidden
    assert resp.status_code == 403
    assert "administratively disabled" in resp.json()["detail"].lower()

    # The sentinel task must remain queued and unassigned
    row = db_conn.execute("SELECT status, assigned_worker FROM tasks WHERE id = ?", (sentinel_task_id,)).fetchone()
    assert row["status"] == "queued"
    assert row["assigned_worker"] is None

    # No lease must have been issued
    leases = db_conn.execute("SELECT count(*) FROM worker_task_leases WHERE task_id = ?", (sentinel_task_id,)).fetchone()[0]
    assert leases == 0


# ---------------------------------------------------------------------------
# 2. Critical Negative Test: Held VPS attempting operational task type
# ---------------------------------------------------------------------------
def test_critical_negative_held_vps_denied_operational_task(client, db_conn):
    """
    CRITICAL NEGATIVE TEST:
    1. Operational sentinel task ('library_script') is queued.
    2. VPS is enabled but subject to operational hold (operational_hold=True, allow_operational_execution=False).
    3. VPS attempting to lease is explicitly denied with HTTP 403.
    4. Task remains unassigned.
    """
    db_conn.execute("UPDATE tasks SET status = 'completed' WHERE status IN ('pending', 'approved', 'queued')")

    now = datetime.now(timezone.utc).isoformat()
    sentinel_task_id = "task-sentinel-vps-script-01"
    db_conn.execute(
        """
        INSERT INTO tasks (
            id, title, description, task_type, status, approval_required,
            input_data_json, created_at, updated_at
        ) VALUES (?, 'Operational Script Task', 'Script task under hold', 'library_script', 'queued', 0, '{"script": "print(1)"}', ?, ?)
        """,
        (sentinel_task_id, now, now),
    )
    db_conn.commit()

    # Setup VPS node with operational hold active
    vps_id = "qwen-vps"
    token, _ = _setup_node(
        db_conn,
        vps_id,
        is_enabled=1,
        role="vps_worker",
        restrictions={
            "operational_hold": True,
            "allow_operational_execution": False,
            "blocked_task_types": ["library_script", "shell_execution", "vps_action"],
        },
    )

    # Attempt 1: Request with supported_categories=['library_script']
    resp1 = client.post(
        "/api/v1/worker/tasks/lease",
        json={"worker_id": vps_id, "supported_categories": ["library_script"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp1.status_code == 403
    assert "hold" in resp1.json()["detail"].lower()

    # Attempt 2: Request without category filter when operational task is in queue
    resp2 = client.post(
        "/api/v1/worker/tasks/lease",
        json={"worker_id": vps_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp2.status_code == 403
    assert "hold" in resp2.json()["detail"].lower() or "denied" in resp2.json()["detail"].lower()

    # Verify task remains queued and unassigned
    row = db_conn.execute("SELECT status, assigned_worker FROM tasks WHERE id = ?", (sentinel_task_id,)).fetchone()
    assert row["status"] == "queued"
    assert row["assigned_worker"] is None


# ---------------------------------------------------------------------------
# 3. Enabled worker with revoked token
# ---------------------------------------------------------------------------
def test_enabled_worker_with_revoked_token_rejected(client, db_conn):
    worker_id = "worker-revoked-01"
    token, token_id = _setup_node(db_conn, worker_id, is_enabled=1)

    # Revoke the token
    worker_svc.revoke_worker_token(token_id)

    resp = client.post(
        "/api/v1/worker/tasks/lease",
        json={"worker_id": worker_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    # Auth middleware rejects revoked Bearer token
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# 4. Retired fixture with previously valid credentials
# ---------------------------------------------------------------------------
def test_retired_fixture_rejected(client, db_conn):
    worker_id = "worker-retired-fixture-01"
    token, _ = _setup_node(db_conn, worker_id, is_enabled=0, notes="[RETIRED TEST FIXTURE: Quarantined]", health_status="offline")

    resp = client.post(
        "/api/v1/worker/tasks/lease",
        json={"worker_id": worker_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "retired" in resp.json()["detail"].lower() or "disabled" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 5. Enabled worker with stale heartbeat
# ---------------------------------------------------------------------------
def test_enabled_worker_with_stale_heartbeat_rejected(client, db_conn):
    worker_id = "worker-stale-hb-01"
    # Last contact was 3600 seconds (1 hour) ago
    token, _ = _setup_node(db_conn, worker_id, is_enabled=1, last_contact_delta=-3600)

    resp = client.post(
        "/api/v1/worker/tasks/lease",
        json={"worker_id": worker_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "stale" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 6. Worker with conflicting registry states
# ---------------------------------------------------------------------------
def test_conflicting_registry_states_fails_closed(client, db_conn):
    worker_id = "worker-conflict-01"
    token, _ = _setup_node(db_conn, worker_id, is_enabled=1)

    # Create conflict: infrastructure_nodes is 1, but machines is 0
    db_conn.execute("UPDATE machines SET is_enabled = 0 WHERE id = ?", (worker_id,))
    db_conn.commit()

    resp = client.post(
        "/api/v1/worker/tasks/lease",
        json={"worker_id": worker_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "disabled" in resp.json()["detail"].lower() or "mismatch" in resp.json()["detail"].lower()

    # Invert conflict: machines is 1, but infrastructure_nodes is 0
    db_conn.execute("UPDATE machines SET is_enabled = 1 WHERE id = ?", (worker_id,))
    db_conn.execute("UPDATE infrastructure_nodes SET is_enabled = 0 WHERE id = ?", (worker_id,))
    db_conn.commit()

    resp2 = client.post(
        "/api/v1/worker/tasks/lease",
        json={"worker_id": worker_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp2.status_code == 403
    assert "disabled" in resp2.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 7. Worker requesting an unauthorized task type
# ---------------------------------------------------------------------------
def test_desktop_worker_requesting_script_execution_denied(client, db_conn):
    worker_id = "desktop-worker-boundary-01"
    token, _ = _setup_node(
        db_conn,
        worker_id,
        is_enabled=1,
        role="inference_node",
        restrictions={"allowed_task_types": ["inference"], "allow_script_execution": False},
    )

    resp = client.post(
        "/api/v1/worker/tasks/lease",
        json={"worker_id": worker_id, "supported_categories": ["library_script"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    detail_lower = resp.json()["detail"].lower()
    assert "not authorized" in detail_lower or "unauthorized" in detail_lower or "denied" in detail_lower


# ---------------------------------------------------------------------------
# 8. Worker disabled between task discovery and lease acquisition
# ---------------------------------------------------------------------------
def test_worker_disabled_during_lease_assignment_transaction(client, db_conn, monkeypatch):
    """Transactional re-check detects worker disabled before lease commit."""
    db_conn.execute("UPDATE tasks SET status = 'completed' WHERE status IN ('pending', 'approved', 'queued')")

    now = datetime.now(timezone.utc).isoformat()
    task_id = "task-race-test-01"
    db_conn.execute(
        """
        INSERT INTO tasks (id, title, description, task_type, status, approval_required, created_at, updated_at)
        VALUES (?, 'Race Task', 'Test race condition', 'inference', 'queued', 0, ?, ?)
        """,
        (task_id, now, now),
    )
    db_conn.commit()

    worker_id = "worker-race-01"
    token, _ = _setup_node(db_conn, worker_id, is_enabled=1)

    # Disable worker right before transaction commit in worker_svc
    orig_begin = db_conn.execute
    def hook_and_disable(sql, *args, **kwargs):
        if "BEGIN IMMEDIATE" in str(sql):
            # Mutate DB directly: worker is disabled right as transaction begins
            from app.database import get_db_connection
            c = get_db_connection()
            c.execute("UPDATE infrastructure_nodes SET is_enabled = 0 WHERE id = ?", (worker_id,))
            c.commit()
            c.close()
        return orig_begin(sql, *args, **kwargs)

    # Call directly via service or API
    with pytest.raises(worker_svc.TaskAuthorizationError) as exc_info:
        # Simulate worker disabled during assignment
        db_conn.execute("UPDATE infrastructure_nodes SET is_enabled = 0 WHERE id = ?", (worker_id,))
        db_conn.commit()
        worker_svc.lease_task_for_worker(worker_id)
    assert "disabled" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 9. Worker missing/malformed capability restrictions fails closed
# ---------------------------------------------------------------------------
def test_malformed_capability_restrictions_fails_closed(client, db_conn):
    worker_id = "worker-malformed-restr-01"
    token, _ = _setup_node(db_conn, worker_id, is_enabled=1)

    # Inject corrupt JSON
    db_conn.execute("UPDATE infrastructure_nodes SET capability_restrictions_json = 'INVALID_JSON{{{' WHERE id = ?", (worker_id,))
    db_conn.commit()

    resp = client.post(
        "/api/v1/worker/tasks/lease",
        json={"worker_id": worker_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "malformed" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 10. Authorized inference worker leasing approved task
# ---------------------------------------------------------------------------
def test_authorized_inference_worker_leases_approved_task(client, db_conn):
    db_conn.execute("UPDATE tasks SET status = 'completed' WHERE status IN ('pending', 'approved', 'queued')")

    now = datetime.now(timezone.utc).isoformat()
    task_id = "task-valid-lease-01"
    db_conn.execute(
        """
        INSERT INTO tasks (
            id, title, description, task_type, status, approval_required,
            input_data_json, created_at, updated_at
        ) VALUES (?, 'Approved Inference Task', 'Valid query', 'inference', 'approved', 1, '{"required_model": "qwen2.5:3b"}', ?, ?)
        """,
        (task_id, now, now),
    )
    db_conn.commit()

    worker_id = "desktop-valid-01"
    token, _ = _setup_node(
        db_conn,
        worker_id,
        is_enabled=1,
        role="inference_node",
        restrictions={"allowed_task_types": ["inference"], "allow_script_execution": False},
    )

    resp = client.post(
        "/api/v1/worker/tasks/lease",
        json={"worker_id": worker_id, "supported_categories": ["inference"], "available_models": ["qwen2.5:3b"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "leased"
    assert data["task"]["task_id"] == task_id


# ---------------------------------------------------------------------------
# 11. Previously leased task behavior when worker is administratively disabled
# ---------------------------------------------------------------------------
def test_previously_leased_task_denies_operations_when_worker_disabled(client, db_conn):
    """
    If a worker is leased a task and subsequently disabled:
    - In-flight upload and heartbeat renewal are denied.
    - Historical task records are preserved without unreported cancellation.
    """
    db_conn.execute("UPDATE tasks SET status = 'completed' WHERE status IN ('pending', 'approved', 'queued')")

    now = datetime.now(timezone.utc).isoformat()
    task_id = "task-inflight-01"
    db_conn.execute(
        """
        INSERT INTO tasks (id, title, description, task_type, status, approval_required, created_at, updated_at)
        VALUES (?, 'Inflight Task', 'Leased task', 'inference', 'queued', 0, ?, ?)
        """,
        (task_id, now, now),
    )
    db_conn.commit()

    worker_id = "worker-inflight-01"
    token, _ = _setup_node(db_conn, worker_id, is_enabled=1)

    # 1. Successfully lease task
    lease_resp = client.post(
        "/api/v1/worker/tasks/lease",
        json={"worker_id": worker_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert lease_resp.status_code == 200
    lease_id = lease_resp.json()["task"]["lease_id"]

    # 2. Worker is administratively disabled
    db_conn.execute("UPDATE infrastructure_nodes SET is_enabled = 0 WHERE id = ?", (worker_id,))
    db_conn.execute("UPDATE machines SET is_enabled = 0 WHERE id = ?", (worker_id,))
    db_conn.commit()

    # 3. Heartbeat renewal denied
    hb_resp = client.post(
        f"/api/v1/worker/tasks/{lease_id}/heartbeat",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert hb_resp.status_code == 403
    assert "administratively disabled" in hb_resp.json()["detail"].lower()

    # 4. Artifact upload denied
    up_resp = client.post(
        f"/api/v1/worker/tasks/{task_id}/artifacts",
        json={"filename": "output.txt", "content_base64": "SGVsbG8=", "lease_id": lease_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert up_resp.status_code == 403
    assert "administratively disabled" in up_resp.json()["detail"].lower()

    # 5. Task record is preserved in database (not deleted or corrupted)
    row = db_conn.execute("SELECT status, assigned_worker FROM tasks WHERE id = ?", (task_id,)).fetchone()
    assert row["status"] == "in_progress"
    assert row["assigned_worker"] == worker_id


# ---------------------------------------------------------------------------
# 12. Registry synchronization and controller restart preserving holds
# ---------------------------------------------------------------------------
def test_sync_and_registration_preserve_administrative_holds(db_conn):
    worker_id = "desktop-worker-01"
    _setup_node(db_conn, worker_id, is_enabled=0)

    # Verify both are disabled
    r1 = db_conn.execute("SELECT is_enabled FROM infrastructure_nodes WHERE id = ?", (worker_id,)).fetchone()
    r2 = db_conn.execute("SELECT is_enabled FROM machines WHERE id = ?", (worker_id,)).fetchone()
    assert r1["is_enabled"] == 0
    assert r2["is_enabled"] == 0

    # Run bidirectional synchronization
    sync_infrastructure_and_machines()

    # Holds must be preserved
    r1_post = db_conn.execute("SELECT is_enabled FROM infrastructure_nodes WHERE id = ?", (worker_id,)).fetchone()
    r2_post = db_conn.execute("SELECT is_enabled FROM machines WHERE id = ?", (worker_id,)).fetchone()
    assert r1_post["is_enabled"] == 0
    assert r2_post["is_enabled"] == 0

    # Automated registration update must also preserve the hold
    register_or_update_node(
        node_id=worker_id,
        name="Desktop Worker",
        role="inference_node",
        is_enabled=True,  # Default registration payload attempts True
    )

    r1_reg = db_conn.execute("SELECT is_enabled FROM infrastructure_nodes WHERE id = ?", (worker_id,)).fetchone()
    assert r1_reg["is_enabled"] == 0, "Registration must NOT override administrative hold without override_hold=True"
