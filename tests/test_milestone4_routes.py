"""Milestone 4 HTTP routes tests: Project Context Packet Inspector and AI Proposals Workflow."""
import re
import uuid
import pytest
from starlette.testclient import TestClient

from app.auth import create_session_token
from app.main import app
from app.services import projects as ps
from app.services import proposals as prop_svc

CSRF_RE = re.compile(r'name="csrf_token" value="([0-9a-f]+)"')


@pytest.fixture
def client():
    c = TestClient(app)
    c.cookies.set("qc_session", create_session_token("admin"))
    return c


def _csrf(client, url="/projects"):
    r = client.get(url)
    assert r.status_code == 200
    m = CSRF_RE.search(r.text)
    assert m, "page should embed a CSRF token"
    return m.group(1)


@pytest.fixture
def active_project_setup():
    pid = ps.create_project(f"Route M4 {uuid.uuid4().hex[:8]}", owner="admin", objective="Intelligence testing")["id"]
    path = ps.create_path(pid, "admin", name="Base Path M4", rationale="Base strategy")
    ps.change_path_status(pid, path["id"], "approved", "admin", "Approved")
    ps.select_path(pid, path["id"], "admin", "Selected")
    ps.change_project_status(pid, "active", "admin", "Active")
    stage = ps.create_stage(pid, path["id"], "admin", name="Stage Alpha", objective="Validate routes")
    return {"project_id": pid, "path_id": path["id"], "stage_id": stage["id"]}


def test_context_packet_inspect_route(client, active_project_setup):
    """Test GET /projects/{id}/context-packet renders packet details and rendered prompt."""
    pid = active_project_setup["project_id"]
    res = client.get(f"/projects/{pid}/context-packet")
    assert res.status_code == 200
    assert "AI Project Context Packet Inspector" in res.text
    assert "Token Allocation by Priority Hierarchy" in res.text
    assert "Rendered Model Prompt" in res.text
    assert "[TRUSTED_PROJECT_BOUNDS]" in res.text

    # Custom guidance and budget
    res_custom = client.get(f"/projects/{pid}/context-packet?user_instruction=Custom+Review+Directive&token_budget=1000")
    assert res_custom.status_code == 200
    assert "Custom Review Directive" in res_custom.text


def test_proposals_tab_renders_in_detail(client, active_project_setup):
    """Test GET /projects/{id}?tab=proposals returns 200 and displays AI planning generators."""
    pid = active_project_setup["project_id"]
    res = client.get(f"/projects/{pid}?tab=proposals")
    assert res.status_code == 200
    assert "AI Planning Proposals & Human Authorization" in res.text
    assert "Strategic Path Proposal" in res.text
    assert "Task Decomposition" in res.text
    assert "Stage Boundary Path Review" in res.text
    assert "id=\"tab-proposals\"" in res.text


def test_generate_path_proposal_route(client, active_project_setup):
    """Test POST /projects/{id}/proposals/generate-path generates a draft path proposal."""
    pid = active_project_setup["project_id"]
    token = _csrf(client, f"/projects/{pid}?tab=proposals")

    res = client.post(
        f"/projects/{pid}/proposals/generate-path",
        data={
            "csrf_token": token,
            "user_guidance": "Focus on high observability",
            "token_budget": 4000,
        },
        follow_redirects=True,
    )
    assert res.status_code == 200
    assert "AI Path proposal generated for review" in res.text

    # Verify recorded in DB
    proposals = prop_svc.list_project_proposals(pid)
    assert len(proposals) >= 1
    prop = proposals[0]
    assert prop["proposal_type"] == "path_proposal"
    assert prop["status"] == "proposed"


def test_generate_task_decomposition_route(client, active_project_setup):
    """Test POST /projects/{id}/proposals/generate-tasks decomposes stage into tasks."""
    pid = active_project_setup["project_id"]
    sid = active_project_setup["stage_id"]
    token = _csrf(client, f"/projects/{pid}?tab=proposals")

    res = client.post(
        f"/projects/{pid}/proposals/generate-tasks",
        data={
            "csrf_token": token,
            "stage_id": sid,
            "user_guidance": "Check all prerequisites",
            "token_budget": 4000,
        },
        follow_redirects=True,
    )
    assert res.status_code == 200
    assert "AI Task decomposition generated for review" in res.text

    proposals = prop_svc.list_project_proposals(pid)
    task_prop = next(p for p in proposals if p["proposal_type"] == "task_decomposition")
    assert task_prop["stage_id"] == sid
    assert task_prop["status"] == "proposed"


def test_generate_path_review_route(client, active_project_setup):
    """Test POST /projects/{id}/proposals/generate-review generates stage boundary review."""
    pid = active_project_setup["project_id"]
    token = _csrf(client, f"/projects/{pid}?tab=proposals")

    res = client.post(
        f"/projects/{pid}/proposals/generate-review",
        data={
            "csrf_token": token,
            "path_id": active_project_setup["path_id"],
            "token_budget": 4000,
        },
        follow_redirects=True,
    )
    assert res.status_code == 200
    assert "AI Path review proposal generated" in res.text

    proposals = prop_svc.list_project_proposals(pid)
    review_prop = next(p for p in proposals if p["proposal_type"] == "path_review")
    assert review_prop["status"] == "proposed"


def test_review_proposal_accept_and_reject_routes(client, active_project_setup):
    """Test human acceptance and rejection workflow via HTTP routes."""
    pid = active_project_setup["project_id"]
    token = _csrf(client, f"/projects/{pid}?tab=proposals")

    # 1. Generate proposal directly via service
    prop = prop_svc.create_proposal(
        project_id=pid,
        proposal_type="path_proposal",
        title="Candidate Strategic Alternative",
        proposed_data={
            "name": "Candidate Strategic Alternative",
            "rationale": "High throughput routing",
            "stages": [
                {"name": "Stage Setup", "objective": "Initialize network", "expected_outputs": "Configs"}
            ]
        },
        created_by="ai_assistant"
    )

    # 2. Accept proposal via route
    res = client.post(
        f"/projects/{pid}/proposals/{prop['id']}/review",
        data={
            "csrf_token": token,
            "action": "accept",
            "review_notes": "Operator authorizes this strategy",
        },
        follow_redirects=True,
    )
    assert res.status_code == 200
    assert "Proposal successfully accepted" in res.text

    # Verify durable path created
    paths = ps.list_paths(pid)
    assert any(p["name"] == "Candidate Strategic Alternative" for p in paths)

    # 3. Create another proposal and reject it
    prop2 = prop_svc.create_proposal(
        project_id=pid,
        proposal_type="path_proposal",
        title="Unwanted Strategy",
        proposed_data={"name": "Unwanted Strategy", "rationale": "Risky"},
        created_by="ai_assistant"
    )

    res_reject = client.post(
        f"/projects/{pid}/proposals/{prop2['id']}/review",
        data={
            "csrf_token": token,
            "action": "reject",
            "review_notes": "Does not align with constraints",
        },
        follow_redirects=True,
    )
    assert res_reject.status_code == 200
    assert "Proposal successfully rejected" in res_reject.text

    # Verify proposal status updated and path was not created
    p2_updated = prop_svc.get_proposal(prop2["id"])
    assert p2_updated["status"] == "rejected"
    assert p2_updated["reviewed_by"] == "admin"
    paths_after = ps.list_paths(pid)
    assert not any(p["name"] == "Unwanted Strategy" for p in paths_after)


def test_unauthenticated_and_csrf_protection(client, active_project_setup):
    """Test security boundaries on Milestone 4 routes."""
    pid = active_project_setup["project_id"]

    # 1. Unauthenticated client
    anon = TestClient(app)
    res_anon = anon.get(f"/projects/{pid}/context-packet", follow_redirects=False)
    assert res_anon.status_code == 302
    assert "/auth/login" in res_anon.headers.get("location", "")

    # 2. Invalid CSRF token
    res_csrf = client.post(
        f"/projects/{pid}/proposals/generate-path",
        data={"csrf_token": "bad_token_123"},
        follow_redirects=True,
    )
    assert "Session expired or invalid form token" in res_csrf.text
