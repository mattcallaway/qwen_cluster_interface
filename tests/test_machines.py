import pytest
from app.services.machine_registry import (
    register_machine,
    get_all_machines,
    get_machine_by_id,
    update_machine_role,
    update_machine_specs,
    toggle_machine_status,
    generate_setup_instructions,
    get_machine_audits
)

def test_machine_registration_and_retrieval():
    node = register_machine(
        display_name="ThinkPad T430",
        ip_address="192.168.1.110",
        hostname="thinkpad-t430",
        role="distributed_worker",
        ram_total_mb=3892,
        cpu_info="Intel Core i5-3320M"
    )
    assert node["id"].startswith("node-")
    assert node["display_name"] == "ThinkPad T430"
    assert node["connection_status"] == "unknown"
    assert node["role"] == "distributed_worker"
    
    fetched = get_machine_by_id(node["id"])
    assert fetched is not None
    assert fetched["ip_address"] == "192.168.1.110"

def test_prevent_duplicate_ip():
    with pytest.raises(ValueError) as exc:
        register_machine(
            display_name="Duplicate Node",
            ip_address="192.168.1.110" # Duplicate
        )
    assert "already registered" in str(exc.value)

def test_machine_role_update_and_audit():
    node = register_machine(
        display_name="Dell Inspiron",
        ip_address="192.168.1.120",
        role="unassigned"
    )
    update_machine_role(node["id"], "standalone_server", notes="Upgraded RAM to 8GB")
    
    updated = get_machine_by_id(node["id"])
    assert updated["role"] == "standalone_server"
    assert updated["notes"] == "Upgraded RAM to 8GB"
    
    audits = get_machine_audits(node["id"])
    assert len(audits) >= 2
    actions = [a["action"] for a in audits]
    assert "REGISTERED" in actions
    assert "ROLE_CHANGED" in actions

def test_toggle_machine_enabled():
    node = register_machine(
        display_name="Old Acer",
        ip_address="192.168.1.130"
    )
    assert node["is_enabled"] == 1
    
    toggle_machine_status(node["id"], False)
    assert get_machine_by_id(node["id"])["is_enabled"] == 0
    
    toggle_machine_status(node["id"], True)
    assert get_machine_by_id(node["id"])["is_enabled"] == 1

def test_setup_instructions_generation():
    worker_node = {
        "display_name": "Laptop 1",
        "ip_address": "192.168.1.101",
        "role": "distributed_worker"
    }
    worker_inst = generate_setup_instructions(worker_node)
    assert "rpc-server" in worker_inst["commands"]
    assert "50052" in worker_inst["commands"]
    
    coord_node = {
        "display_name": "Coordinator",
        "ip_address": "192.168.1.100",
        "role": "inference_coordinator"
    }
    coord_inst = generate_setup_instructions(coord_node)
    assert "llama-server" in coord_inst["commands"]
    assert "--rpc" in coord_inst["commands"]
