"""
Tests for Increment 17 Web Dashboard & Observatory UI Exposure.
Validates:
1. REST API endpoints for Paged KV metrics and Pipeline Stages.
2. Full page rendering for /inference with embedded topology visualizer and paged KV widgets.
3. HTMX live partial endpoints for polling updates.
4. /observatory page rendering of Distributed Pipeline Parallelism and Paged KV card.
"""
import pytest
from starlette.testclient import TestClient
from app.main import app
from app.auth import create_session_token


@pytest.fixture
def auth_client():
    client = TestClient(app)
    token = create_session_token("admin")
    client.cookies.set("qc_session", token)
    return client


def test_api_paged_kv_metrics(auth_client):
    res = auth_client.get("/api/v1/inference/paged-kv/metrics")
    assert res.status_code == 200
    data = res.json()
    assert "total_queries" in data
    assert "prefix_hit_rate_percent" in data
    assert "current_ram_pages" in data
    assert "max_ram_blocks" in data
    assert "free_physical_blocks" in data


def test_api_pipeline_stages(auth_client):
    res = auth_client.get("/api/v1/inference/pipeline/stages")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    topo = data[0]
    assert "pipeline_name" in topo
    assert "model_name" in topo
    assert "stages" in topo
    assert len(topo["stages"]) >= 2
    assert topo["stages"][0]["is_first_stage"] is True
    assert topo["stages"][-1]["is_last_stage"] is True


def test_inference_page_renders_widgets(auth_client):
    res = auth_client.get("/inference")
    assert res.status_code == 200
    html = res.text
    assert "Distributed Execution &amp; Virtual Memory" in html or "Distributed Execution & Virtual Memory" in html
    assert "Searchable Paged KV Cache Virtual Memory" in html
    assert "Distributed Pipeline Parallelism Topology Visualizer" in html
    assert "Tailscale WireGuard" in html
    assert "STAGE 0 &bull; INGRESS" in html or "STAGE 0 • INGRESS" in html
    assert "STAGE 1 &bull; EGRESS" in html or "STAGE 1 • EGRESS" in html


def test_htmx_paged_kv_widget_partial(auth_client):
    res = auth_client.get("/inference/paged-kv/widget")
    assert res.status_code == 200
    html = res.text
    assert "Searchable Paged KV Cache Virtual Memory" in html
    assert "Prefix Cache Hit Rate" in html
    assert "Tier 1 RAM Working Set" in html


def test_htmx_pipeline_widget_partial(auth_client):
    res = auth_client.get("/inference/pipeline/widget")
    assert res.status_code == 200
    html = res.text
    assert "Distributed Pipeline Parallelism Topology Visualizer" in html
    assert "Tailscale Activation Bridge" in html
    assert "QCACT1 Authenticated" in html


def test_observatory_renders_pipeline_card(auth_client):
    res = auth_client.get("/observatory")
    assert res.status_code == 200
    html = res.text
    assert "Distributed Pipeline Parallelism &amp; Searchable Paged KV Cache" in html or "Distributed Pipeline Parallelism & Searchable Paged KV Cache" in html
    assert "Tailscale WireGuard RTT" in html
    assert "Edge RAM Preservation" in html
