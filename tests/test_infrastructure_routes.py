"""HTTP route integration tests for Milestone 1 Infrastructure Dashboard."""
import re
import pytest
from starlette.testclient import TestClient

from app.auth import create_session_token
from app.main import app
from app.services import infrastructure as infra_svc

CSRF_RE = re.compile(r'name="csrf_token" value="([0-9a-f]+)"')


@pytest.fixture
def client():
    c = TestClient(app)
    c.cookies.set("qc_session", create_session_token("admin"))
    return c


def _csrf(client, url="/infrastructure"):
    r = client.get(url)
    assert r.status_code == 200
    m = CSRF_RE.search(r.text)
    assert m, "page should embed a CSRF token"
    return m.group(1)


def test_infrastructure_dashboard_renders_overview(client):
    """Test GET /infrastructure renders overview and architecture topology."""
    res = client.get("/infrastructure?tab=overview")
    assert res.status_code == 200
    assert "Infrastructure & Capability Registry" in res.text
    assert "Component Roles & Architecture Topology" in res.text
    assert "Windows Controller" in res.text
    assert "Active Operational Nodes" in res.text
    assert "id=\"tab-overview\"" in res.text


def test_infrastructure_dashboard_renders_workers(client):
    """Test GET /infrastructure?tab=workers displays worker cards and rediscovery controls."""
    res = client.get("/infrastructure?tab=workers")
    assert res.status_code == 200
    assert "Registered Infrastructure Nodes & Workers" in res.text
    assert "Windows Controller" in res.text
    assert "Read-Only Rediscovery" in res.text
    assert "id=\"tab-workers\"" in res.text


def test_infrastructure_dashboard_renders_capabilities(client):
    """Test GET /infrastructure?tab=capabilities displays capability registry table."""
    res = client.get("/infrastructure?tab=capabilities")
    assert res.status_code == 200
    assert "Infrastructure Capabilities Registry" in res.text
    assert "Test Verify" in res.text
    assert "id=\"tab-capabilities\"" in res.text


def test_infrastructure_dashboard_renders_reconciliation(client):
    """Test GET /infrastructure?tab=reconciliation displays architecture drift monitor."""
    res = client.get("/infrastructure?tab=reconciliation")
    assert res.status_code == 200
    assert "Architecture Drift & Discrepancies" in res.text
    assert "id=\"tab-reconciliation\"" in res.text


def test_infrastructure_context_inspector_route(client):
    """Test GET /infrastructure?tab=architecture renders simulated Architecture Context Packet."""
    res = client.get("/infrastructure?tab=architecture&token_budget=1500&q=Can+we+run+python+scripts%3F")
    assert res.status_code == 200
    assert "Architecture Context Packet Inspector" in res.text
    assert "Rendered Prompt Injected into Model Context" in res.text
    assert "[SYSTEM_ARCHITECTURE_CONTEXT]" in res.text
    assert "[CONTROLLER_AND_BOUNDS]" in res.text
    assert "[VERIFIED_CAPABILITIES]" in res.text
    assert "Can we run python scripts?" in res.text


def test_node_discovery_post_route(client):
    """Test POST /infrastructure/nodes/{id}/discover triggers read-only capability discovery."""
    node = infra_svc.ensure_controller_node()
    token = _csrf(client, "/infrastructure?tab=workers")

    res = client.post(
        f"/infrastructure/nodes/{node['id']}/discover",
        data={"csrf_token": token},
        follow_redirects=False
    )
    assert res.status_code == 302
    assert "/infrastructure?tab=workers" in res.headers["location"]


def test_capability_verify_post_route(client):
    """Test POST /infrastructure/capabilities/{id}/verify executes side-effect-free probe."""
    node = infra_svc.ensure_controller_node()
    caps = infra_svc.list_capabilities(node_id=node["id"])
    assert len(caps) > 0
    cap_id = caps[0]["id"]

    token = _csrf(client, "/infrastructure?tab=capabilities")
    res = client.post(
        f"/infrastructure/capabilities/{cap_id}/verify",
        data={"csrf_token": token},
        follow_redirects=False
    )
    assert res.status_code == 302
    assert "/infrastructure?tab=capabilities" in res.headers["location"]

    # Verify capability is verified
    updated = infra_svc.get_capability(cap_id)
    assert updated["verification_status"] == "verified"


def test_sync_vault_post_route(client):
    """Test POST /infrastructure/sync-vault writes note to vault."""
    token = _csrf(client, "/infrastructure?tab=architecture")
    res = client.post(
        "/infrastructure/sync-vault",
        data={"csrf_token": token},
        follow_redirects=False
    )
    assert res.status_code == 302
    assert "/infrastructure?tab=architecture" in res.headers["location"]


def test_infrastructure_csrf_guards(client):
    """Test that missing or invalid CSRF tokens are rejected."""
    res_disc = client.post(
        "/infrastructure/nodes/controller-local/discover",
        data={"csrf_token": "bad-csrf-token"},
        follow_redirects=False
    )
    assert res_disc.status_code == 302
    assert "err=" in res_disc.headers["location"]

    res_verif = client.post(
        "/infrastructure/capabilities/cap_controller-local_python_runtime/verify",
        data={"csrf_token": "bad-csrf-token"},
        follow_redirects=False
    )
    assert res_verif.status_code == 302
    assert "err=" in res_verif.headers["location"]
