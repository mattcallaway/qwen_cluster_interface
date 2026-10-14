import pytest
from app.services.vps import (
    register_vps_node,
    get_vps_nodes,
    get_vps_node_by_id
)

def test_vps_registration():
    vps_id = register_vps_node(
        name="Hetzner Execution Worker",
        endpoint_url="http://10.0.0.99:8081",
        auth_token="vps-test-secret-token",
        permission_policy="approval_required"
    )
    assert vps_id.startswith("vps-")
    
    node = get_vps_node_by_id(vps_id)
    assert node is not None
    assert node["name"] == "Hetzner Execution Worker"
    assert node["endpoint_url"] == "http://10.0.0.99:8081"
    assert node["permission_policy"] == "approval_required"
    assert node["status"] == "unknown"
    assert "health_check" in node["capabilities"]
