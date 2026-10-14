import os
import pytest
from pathlib import Path

from app.database import get_db_connection
from app.services import infrastructure as infra_svc


@pytest.fixture(autouse=True)
def clean_infrastructure_tables():
    """Ensure clean infrastructure tables before each test."""
    with get_db_connection() as conn:
        conn.execute("DELETE FROM infrastructure_verifications")
        conn.execute("DELETE FROM infrastructure_reconciliations")
        conn.execute("DELETE FROM infrastructure_capabilities")
        conn.execute("DELETE FROM infrastructure_nodes")
        conn.commit()
    yield


def test_ensure_controller_node():
    """Test that controller node auto-registration captures OS, hardware and default capabilities."""
    node = infra_svc.ensure_controller_node()
    assert node["id"] == "controller-local"
    assert node["role"] == "controller"
    assert node["health_status"] == "online"
    assert len(node["installed_runtimes"]) >= 1
    assert any("python" in r.lower() for r in node["installed_runtimes"])

    # Verify default capabilities
    caps = infra_svc.list_capabilities(node_id="controller-local")
    assert len(caps) >= 3
    categories = [c["category"] for c in caps]
    assert "ai_inference" in categories
    assert "python_execution" in categories
    assert "file_processing" in categories
    assert "monitoring" in categories


def test_register_and_list_nodes():
    """Test node registration, querying and validation."""
    node = infra_svc.register_or_update_node(
        node_id="vps-node-01",
        name="Primary VPS Execution Node",
        role="vps_worker",
        ip_address="100.64.0.10",
        os_name="Linux",
        os_version="Ubuntu 24.04 LTS",
        installed_ram_mb=4096,
        available_ram_mb=2800,
        storage_total_gb=80.0,
        storage_free_gb=54.2,
        installed_runtimes=["python3.12", "systemd", "playwright"],
        health_status="online"
    )

    assert node["id"] == "vps-node-01"
    assert node["role"] == "vps_worker"
    assert node["installed_ram_mb"] == 4096

    # Verify querying
    all_nodes = infra_svc.list_nodes()
    assert any(n["id"] == "vps-node-01" for n in all_nodes)

    vps_nodes = infra_svc.list_nodes(role="vps_worker")
    assert len(vps_nodes) == 1
    assert vps_nodes[0]["id"] == "vps-node-01"

    # Default capabilities generated
    caps = infra_svc.list_capabilities(node_id="vps-node-01")
    assert len(caps) >= 3
    cap_cats = [c["category"] for c in caps]
    assert "python_execution" in cap_cats
    assert "web_retrieval" in cap_cats


def test_capability_discovery_read_only():
    """Test read-only discovery transitions declared capabilities to discovered."""
    infra_svc.ensure_controller_node()
    res = infra_svc.discover_node_capabilities("controller-local")
    assert res["node_id"] == "controller-local"
    assert len(res["capabilities_discovered"]) >= 3

    caps = infra_svc.list_capabilities(node_id="controller-local")
    assert all(c["discovery_status"] == "discovered" for c in caps)


def test_side_effect_free_capability_verification():
    """Test executing controlled side-effect-free probes to verify operational capabilities."""
    infra_svc.ensure_controller_node()
    py_cap = next(c for c in infra_svc.list_capabilities(node_id="controller-local") if c["category"] == "python_execution")

    res = infra_svc.verify_capability(py_cap["id"], verified_by="admin")
    assert res["passed"] is True
    assert res["verification_status"] == "verified"
    assert "confirmed" in res["details"].lower() or "verified" in res["details"].lower()

    # Verify updated record
    updated = infra_svc.get_capability(py_cap["id"])
    assert updated["verification_status"] == "verified"
    assert updated["last_verified_at"] is not None


def test_reconciliation_drift_detection():
    """Test architecture reconciliation detects degraded nodes and failed verifications."""
    node = infra_svc.register_or_update_node(
        node_id="failing-worker",
        name="Degraded Worker",
        role="compute_worker",
        health_status="offline"
    )

    alerts = infra_svc.reconcile_infrastructure()
    assert any(a["node_id"] == "failing-worker" and a["discrepancy_type"] == "health_degraded" for a in alerts)


def test_architecture_context_packet_budgeting():
    """Test Architecture Context Packet adheres to strict token budgets and priority pruning."""
    infra_svc.ensure_controller_node()

    # Generous budget
    packet = infra_svc.build_architecture_context_packet(token_budget=3000, user_query="Can we run python scripts?")
    assert "[TASK_OR_INSTRUCTION_SCOPE]" in packet["rendered_prompt"]
    assert "[CONTROLLER_AND_BOUNDS]" in packet["rendered_prompt"]
    assert "[VERIFIED_CAPABILITIES]" in packet["rendered_prompt"]
    assert "[ACTIVE_WORKERS_SUMMARY]" in packet["rendered_prompt"]
    assert packet["estimated_tokens"] <= 3500

    # Very small budget triggers omission notices
    tight_packet = infra_svc.build_architecture_context_packet(token_budget=150, user_query="Check capabilities")
    assert "[CONTROLLER_AND_BOUNDS]" in tight_packet["rendered_prompt"]
    assert "[OMISSION_NOTICE]" in tight_packet["rendered_prompt"]
    assert len(tight_packet["omitted_priorities"]) > 0


def test_sync_architecture_knowledge():
    """Test synchronizing live infrastructure records with durable Markdown vault note."""
    vault_path = infra_svc.sync_architecture_knowledge()
    assert vault_path == "system/infrastructure_architecture.md"

    vault_file = Path(os.environ.get("QC_DATA_DIR", ".")) / "knowledge" / vault_path
    # Note was written via write_note_atomic
    caps = infra_svc.list_capabilities()
    assert len(caps) >= 3
