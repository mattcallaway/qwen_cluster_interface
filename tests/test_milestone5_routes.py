"""Milestone 5 HTTP routes tests: Learning, Retrospectives, and Deliverable Evaluations."""
import re
import uuid
import pytest
from starlette.testclient import TestClient

from app.auth import create_session_token
from app.main import app
from app.services import deliverables as ds
from app.services import learning as learn_svc
from app.services import projects as ps

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
    pid = ps.create_project(f"Route M5 {uuid.uuid4().hex[:8]}", owner="admin", objective="Learning loop testing")["id"]
    path = ps.create_path(pid, "admin", name="Base Path M5", rationale="Base strategy")
    ps.change_path_status(pid, path["id"], "approved", "admin", "Approved")
    ps.select_path(pid, path["id"], "admin", "Selected")
    ps.change_project_status(pid, "active", "admin", "Active")
    stage = ps.create_stage(pid, path["id"], "admin", name="Stage Learning", objective="Validate learning routes")
    return {"project_id": pid, "path_id": path["id"], "stage_id": stage["id"]}


def test_learning_tab_renders_in_detail(client, active_project_setup):
    """Test GET /projects/{id}?tab=learning returns 200 and displays calibration and retrospective sections."""
    pid = active_project_setup["project_id"]
    res = client.get(f"/projects/{pid}?tab=learning")
    assert res.status_code == 200
    assert "Model Proposal Acceptance" in res.text
    assert "Mean Forecast Accuracy" in res.text
    assert "Calibration Assessment" in res.text
    assert "Project Retrospectives & Strategic Lessons" in res.text
    assert "Deliverable Impact & Outcome Evaluations" in res.text
    assert "Cross-Project Transfer Learning" in res.text
    assert "id=\"tab-learning\"" in res.text


def test_generate_retrospective_route(client, active_project_setup):
    """Test POST /projects/{id}/retrospectives/generate generates a draft retrospective."""
    pid = active_project_setup["project_id"]
    token = _csrf(client, f"/projects/{pid}?tab=learning")

    res = client.post(
        f"/projects/{pid}/retrospectives/generate",
        data={
            "csrf_token": token,
            "user_guidance": "Focus on stage completion heuristics",
        },
        follow_redirects=False
    )
    assert res.status_code == 302
    assert f"/projects/{pid}?tab=learning" in res.headers["location"]

    # Verify retrospective generated
    retros = learn_svc.list_retrospectives(pid)
    assert len(retros) >= 1
    assert retros[0]["status"] == "draft"
    assert "Retrospective" in retros[0]["title"]


def test_publish_retrospective_route(client, active_project_setup):
    """Test POST /projects/{id}/retrospectives/{retro_id}/publish publishes to vault and collective memory."""
    pid = active_project_setup["project_id"]
    retro = learn_svc.generate_project_retrospective(pid, created_by="admin")
    retro_id = retro["id"]

    token = _csrf(client, f"/projects/{pid}?tab=learning")
    res = client.post(
        f"/projects/{pid}/retrospectives/{retro_id}/publish",
        data={
            "csrf_token": token,
            "promote_to_collective": "true",
        },
        follow_redirects=False
    )
    assert res.status_code == 302
    assert f"/projects/{pid}?tab=learning" in res.headers["location"]

    updated = learn_svc.get_retrospective(retro_id)
    assert updated["status"] == "published"
    assert updated["reviewed_by"] == "admin"
    assert updated["knowledge_path"] is not None


def test_deliverable_evaluate_route(client, active_project_setup):
    """Test POST /projects/{id}/deliverables/evaluate records an impact evaluation."""
    pid = active_project_setup["project_id"]
    stage_id = active_project_setup["stage_id"]

    deliv = ds.create_deliverable(
        project_id=pid,
        name="Engine Core Binary",
        deliverable_type="software_artifact",
        created_by="admin",
        stage_id=stage_id,
        acceptance_criteria="Passes benchmarks"
    )
    ds.submit_deliverable(deliv["id"], user="admin", artifact_references="bin:core-v1")
    ds.review_deliverable(deliv["id"], new_status="accepted", reviewer="admin", rationale="Meets benchmarks")

    token = _csrf(client, f"/projects/{pid}?tab=learning")
    res = client.post(
        f"/projects/{pid}/deliverables/evaluate",
        data={
            "csrf_token": token,
            "deliverable_id": deliv["id"],
            "evaluation_type": "post_delivery_impact",
            "outcome_status": "exceeded_expectations",
            "impact_summary": "Throughput increased by 50% under production loads.",
            "evidence_reference": "telem:prod-metrics-run4",
            "lessons_learned": "Static memory allocation prevents GC pauses.",
        },
        follow_redirects=False
    )
    assert res.status_code == 302
    assert f"/projects/{pid}?tab=learning" in res.headers["location"]

    evals = learn_svc.list_deliverable_evaluations(pid, deliverable_id=deliv["id"])
    assert len(evals) == 1
    assert evals[0]["outcome_status"] == "exceeded_expectations"
    assert "Throughput increased" in evals[0]["impact_summary"]


def test_learning_csrf_guards(client, active_project_setup):
    """Test that missing or invalid CSRF tokens are rejected."""
    pid = active_project_setup["project_id"]

    res_gen = client.post(
        f"/projects/{pid}/retrospectives/generate",
        data={"csrf_token": "bad-token", "user_guidance": "fail"},
        follow_redirects=False
    )
    assert res_gen.status_code == 302
    assert "err=" in res_gen.headers["location"]

    res_eval = client.post(
        f"/projects/{pid}/deliverables/evaluate",
        data={
            "csrf_token": "bad-token",
            "deliverable_id": "none",
            "evaluation_type": "immediate_acceptance",
            "outcome_status": "met_expectations",
            "impact_summary": "test",
        },
        follow_redirects=False
    )
    assert res_eval.status_code == 302
    assert "err=" in res_eval.headers["location"]
