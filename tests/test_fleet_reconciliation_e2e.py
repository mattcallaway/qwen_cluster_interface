import json
import pytest
from app.services.infrastructure import (
    ensure_controller_node,
    sync_from_existing_machines_and_vps,
    list_nodes,
    get_node,
)
from app.services.machine_registry import get_all_machines
from app.services.orchestration.model_router import node_inventory, eligible_nodes
from app.database import get_db_connection, db_transaction


def test_fleet_reconciliation_and_scheduling_invariants(db_conn):
    """Verify all fleet hygiene and scheduler invariants specified in Section 8."""
    # 1. Setup real physical machines and a retired test fixture in DB
    with db_transaction() as conn:
        conn.execute("DELETE FROM infrastructure_nodes")
        conn.execute("DELETE FROM machines")
        conn.execute("DELETE FROM worker_auth_tokens")

        # Insert 4 physical machines into infrastructure_nodes
        real_nodes = [
            ("desktop-worker-01", "desktop-worker-01", "inference_node", "192.168.1.185", "online", 0, '["qwen2.5:3b", "qwen2.5:1.5b"]', 'Active Desktop Worker'),
            ("qwen-laptop", "qwen-laptop", "inference_node", "100.115.92.19", "online", 0, '["llama3.2:1b", "qwen2.5:1.5b"]', 'Linux Laptop'),
            ("pi-brain", "pi-brain", "inference_node", "100.74.195.83", "online", 0, '["qwen2.5:0.5b"]', 'Raspberry Pi'),
            ("qwen-vps", "qwen-vps", "vps_worker", "172.233.170.219", "online", 0, '[]', 'Ubuntu Cloud VPS'),
        ]
        for nid, name, role, ip, health, enabled, models, notes in real_nodes:
            conn.execute("""
                INSERT INTO infrastructure_nodes (
                    id, name, role, hostname, ip_address, tailscale_status,
                    os_name, os_version, architecture, cpu_info,
                    installed_ram_mb, available_ram_mb, storage_total_gb, storage_free_gb,
                    installed_runtimes_json, registered_services_json, available_models_json,
                    health_status, last_contact_at, last_inventory_at,
                    capability_restrictions_json, notes, is_enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'connected', 'Linux', '24.04', 'x86_64', 'CPU', 8000, 4000, 100, 50,
                         '["python3", "ollama"]', '[]', ?, ?, datetime('now'), datetime('now'), '{}', ?, ?, datetime('now'), datetime('now'))
            """, (nid, name, role, nid, ip, models, health, notes, enabled))

        # Insert a retired ghost test fixture
        conn.execute("""
            INSERT INTO infrastructure_nodes (
                id, name, role, hostname, ip_address, tailscale_status,
                os_name, os_version, architecture, cpu_info,
                installed_ram_mb, available_ram_mb, storage_total_gb, storage_free_gb,
                installed_runtimes_json, registered_services_json, available_models_json,
                health_status, last_contact_at, last_inventory_at,
                capability_restrictions_json, notes, is_enabled, created_at, updated_at
            ) VALUES ('worker-A', 'Worker A (Test)', 'inference_node', 'worker-a', '10.0.0.99', 'unknown', 'Linux', '', 'x86_64', '', 4000, 2000, 50, 20,
                     '["python3", "ollama"]', '[]', '["qwen2.5:3b"]', 'offline', datetime('now'), datetime('now'), '{}',
                     '[RETIRED TEST FIXTURE: Quarantined]', 0, datetime('now'), datetime('now'))
        """)

        # Insert active tokens for physical machines only
        for nid in ["desktop-worker-01", "qwen-laptop", "pi-brain", "qwen-vps"]:
            conn.execute("INSERT INTO worker_auth_tokens (token_prefix, token_hash, node_id, revoked, created_at) VALUES (?, ?, ?, 0, '2026-10-23')", (f"tok_{nid[:8]}", f"hash_{nid}", nid))

    # Insert controller node (opens its own transaction)
    ensure_controller_node()

    # Trigger synchronization
    sync_from_existing_machines_and_vps()

    # Invariant 1: Only verified real machines + controller appear in active list_nodes()
    active_nodes = list_nodes(include_retired=False)
    active_ids = {n["id"] for n in active_nodes}
    assert "worker-A" not in active_ids, "Retired test fixture must NOT appear in active list_nodes"
    assert active_ids == {"controller-local", "desktop-worker-01", "qwen-laptop", "pi-brain", "qwen-vps"}

    # Invariant 2: get_all_machines(include_retired=False) excludes retired fixtures and controller
    active_machines = get_all_machines(include_retired=False)
    mach_ids = {m["id"] for m in active_machines}
    assert "worker-A" not in mach_ids
    assert "controller-local" not in mach_ids
    assert len(active_machines) == 4, f"Expected 4 active physical machines, got {len(active_machines)}"

    # Invariant 3: Scheduler / node_inventory() excludes retired fixtures
    inv = node_inventory()
    inv_ids = {n["id"] for n in inv}
    assert "worker-A" not in inv_ids, "Retired fixtures must NOT be present in scheduler node_inventory"
    assert "desktop-worker-01" in inv_ids
    assert "controller-local" in inv_ids

    # Invariant 4: Disabled real machines remain disabled and ineligible for scheduling
    desktop = next(n for n in inv if n["id"] == "desktop-worker-01")
    assert desktop["enabled"] is False

    task = {
        "action": {"type": "inference", "model": "qwen2.5:3b"},
        "preferred_models": ["qwen2.5:3b"],
        "allowed_nodes": []
    }
    elig = eligible_nodes(task, inv)
    assert not any(n["id"] == "desktop-worker-01" for n, m in elig["eligible"]), "Disabled worker must not be eligible"
    assert any("disabled" in r for r in elig["reasons"]), "Disabled reason must be recorded"

    # Invariant 5: Offline real machine is preserved in inventory but not eligible when require_fresh=True
    with db_transaction() as conn:
        conn.execute("UPDATE infrastructure_nodes SET health_status = 'offline', is_enabled = 1 WHERE id = 'qwen-laptop'")
    inv = node_inventory()
    laptop = next(n for n in inv if n["id"] == "qwen-laptop")
    assert laptop["health"] == "offline"
    elig_laptop = eligible_nodes(task, inv, require_fresh=True)
    assert not any(n["id"] == "qwen-laptop" for n, m in elig_laptop["eligible"])

    # Invariant 6: Synchronization loop does not recreate ghost nodes
    sync_from_existing_machines_and_vps()
    all_active_after_sync = list_nodes(include_retired=False)
    assert "worker-A" not in {n["id"] for n in all_active_after_sync}
    assert len(all_active_after_sync) == 5  # 4 physical + 1 controller

    # Invariant 7: Archived view returns the quarantined record
    archived_nodes = [n for n in list_nodes(include_retired=True) if str(n.get("notes", "")).startswith("[RETIRED")]
    assert len(archived_nodes) == 1
    assert archived_nodes[0]["id"] == "worker-A"


def test_ui_views_fleet_reconciliation():
    from starlette.testclient import TestClient
    from app.main import app
    from app.auth import create_session_token

    client = TestClient(app)
    client.cookies.set("qc_session", create_session_token("admin"))

    # Test /machines?view=active
    res = client.get("/machines?view=active")
    assert res.status_code == 200
    assert "Physical Fleet Registry" in res.text
    assert "desktop-worker-01" in res.text
    assert "INTERNAL CONTROLLER IDENTITY" in res.text
    assert "worker-A" not in res.text, "Retired worker-A must not appear in active machines view"

    # Test /machines?view=archived
    res_archived = client.get("/machines?view=archived")
    assert res_archived.status_code == 200
    assert "Archived & Quarantined" in res_archived.text
    assert "worker-A" in res_archived.text
    assert "Quarantine Active: Zero Scheduling Impact" in res_archived.text

    # Test /infrastructure?tab=workers
    res_workers = client.get("/infrastructure?tab=workers")
    assert res_workers.status_code == 200
    assert "worker-A" not in res_workers.text

    # Test /infrastructure?tab=archived
    res_infra_archived = client.get("/infrastructure?tab=archived")
    assert res_infra_archived.status_code == 200
    assert "Archived & Quarantined Test Records" in res_infra_archived.text
    assert "worker-A" in res_infra_archived.text

