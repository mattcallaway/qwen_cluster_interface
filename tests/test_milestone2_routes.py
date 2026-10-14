"""Milestone 2 HTTP routes tests.

Tests real HTTP endpoints with session cookies and CSRF verification:
1. Deliverable create, submit, review (POST /projects/{pid}/deliverables/...).
2. Task dependencies add, remove (POST /projects/{pid}/tasks/{tid}/dependencies/...).
3. Task execution recording (POST /projects/{pid}/tasks/{tid}/execute).
4. Stage advancement through gate (POST /projects/{pid}/stages/{sid}/advance).
5. Path revision (POST /projects/{pid}/paths/{path_id}/revise).
6. Knowledge promotion (POST /projects/{pid}/knowledge/{kn_id}/promote).
7. Deliverables tab rendering.
"""
import re
import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient

from app.auth import create_session_token
from app.main import app
from app.services import deliverables as ds
from app.services import knowledge_meta as km
from app.services import projects as ps
from app.services import tasks as ts

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
    pid = ps.create_project(f"Route M2 {uuid.uuid4().hex[:8]}", owner="admin", objective="Deliverable routes")["id"]
    path = ps.create_path(pid, "admin", name="Path Route", rationale="Route test rationale")
    ps.change_path_status(pid, path["id"], "approved", "admin", "Approved")
    ps.select_path(pid, path["id"], "admin", "Selected")
    ps.change_project_status(pid, "active", "admin", "Active")
    s1 = ps.create_stage(pid, path["id"], "admin", name="Stage Route 1")
    s2 = ps.create_stage(pid, path["id"], "admin", name="Stage Route 2")
    ps.change_stage_status(pid, s1["id"], "active")
    return {"project_id": pid, "path_id": path["id"], "s1_id": s1["id"], "s2_id": s2["id"]}


def test_deliverable_routes_full_flow(client, active_project_setup):
    pid = active_project_setup["project_id"]
    s1 = active_project_setup["s1_id"]
    token = _csrf(client, f"/projects/{pid}?tab=deliverables")

    # 1. Create deliverable via HTTP
    resp = client.post(
        f"/projects/{pid}/deliverables/create",
        data={
            "csrf_token": token,
            "name": "Audit Report Deliverable",
            "deliverable_type": "report",
            "stage_id": s1,
            "description": "Comprehensive security audit report",
            "acceptance_criteria": "Reviewed with 0 blocker findings",
            "due_date": "2026-11-15"
        },
        follow_redirects=False
    )
    assert resp.status_code == 302
    delivs = ds.list_deliverables(pid)
    assert len(delivs) == 1
    did = delivs[0]["id"]
    assert delivs[0]["name"] == "Audit Report Deliverable"

    # 2. View deliverables tab
    page_resp = client.get(f"/projects/{pid}?tab=deliverables")
    assert page_resp.status_code == 200
    assert "Audit Report Deliverable" in page_resp.text
    assert "Submit for Review" in page_resp.text

    # 3. Submit deliverable via HTTP
    token = _csrf(client, f"/projects/{pid}?tab=deliverables")
    resp2 = client.post(
        f"/projects/{pid}/deliverables/{did}/submit",
        data={
            "csrf_token": token,
            "artifact_references": "reports/audit_2026.pdf",
            "verification_results": "Static check clean"
        },
        follow_redirects=False
    )
    assert resp2.status_code == 302
    d_after_sub = ds.get_deliverable(did)
    assert d_after_sub["status"] == "submitted"

    # 4. Review deliverable via HTTP
    token = _csrf(client, f"/projects/{pid}?tab=deliverables")
    resp3 = client.post(
        f"/projects/{pid}/deliverables/{did}/review",
        data={
            "csrf_token": token,
            "decision": "accept",
            "rationale": "Audit report meets all criteria",
            "verification_results": "Verified by lead reviewer"
        },
        follow_redirects=False
    )
    assert resp3.status_code == 302
    d_after_rev = ds.get_deliverable(did)
    assert d_after_rev["status"] == "accepted"


def test_task_dependency_routes(client, active_project_setup):
    pid = active_project_setup["project_id"]
    s1 = active_project_setup["s1_id"]

    t1 = ts.create_task("Route Prereq Task", project_id=pid, stage_id=s1,
                        purpose="P1", expected_output="O1", completion_criteria="C1")
    t2 = ts.create_task("Route Dependent Task", project_id=pid, stage_id=s1,
                        purpose="P2", expected_output="O2", completion_criteria="C2")

    token = _csrf(client, f"/projects/{pid}?tab=tasks")

    # Add dependency via HTTP
    resp = client.post(
        f"/projects/{pid}/tasks/{t2['id']}/dependencies/add",
        data={"csrf_token": token, "depends_on_task_id": t1["id"]},
        follow_redirects=False
    )
    assert resp.status_code == 302
    assert len(ts.get_task_dependencies(t2["id"])) == 1

    # Remove dependency via HTTP
    token = _csrf(client, f"/projects/{pid}?tab=tasks")
    resp2 = client.post(
        f"/projects/{pid}/tasks/{t2['id']}/dependencies/remove",
        data={"csrf_token": token, "depends_on_task_id": t1["id"]},
        follow_redirects=False
    )
    assert resp2.status_code == 302
    assert len(ts.get_task_dependencies(t2["id"])) == 0


def test_task_record_execution_route(client, active_project_setup):
    pid = active_project_setup["project_id"]
    s1 = active_project_setup["s1_id"]

    task = ts.create_task("Route Exec Task", project_id=pid, stage_id=s1,
                          purpose="Exec", expected_output="Out", completion_criteria="Crit")
    ts.approve_task(task["id"])

    token = _csrf(client, f"/projects/{pid}?tab=tasks")
    resp = client.post(
        f"/projects/{pid}/tasks/{task['id']}/execute",
        data={
            "csrf_token": token,
            "outcome": "completed",
            "attempted": "HTTP attempt",
            "actual_result": "HTTP success",
            "evidence_summary": "HTTP log evidence",
            "assumptions_feedback": "Confirmed",
            "reusable_lessons": "Always verify HTTP headers",
            "create_knowledge_note": "1"
        },
        follow_redirects=False
    )
    assert resp.status_code == 302
    events = ts.list_task_execution_events(task["id"])
    assert len(events) == 1
    assert events[0]["outcome"] == "completed"
    assert events[0]["resulting_knowledge_id"] is not None


def test_path_revision_route(client, active_project_setup):
    pid = active_project_setup["project_id"]
    path_id = active_project_setup["path_id"]

    token = _csrf(client, f"/projects/{pid}?tab=paths")
    resp = client.post(
        f"/projects/{pid}/paths/{path_id}/revise",
        data={
            "csrf_token": token,
            "revision_reason": "Updated estimates via UI",
            "name": "Path Route (Revised)",
            "rationale": "Updated strategic rationale",
            "estimated_cost": "250.00",
            "estimated_effort_hours": "50.0",
            "forecast_confidence": "0.9"
        },
        follow_redirects=False
    )
    assert resp.status_code == 302
    path = ps.get_path(path_id)
    assert path["name"] == "Path Route (Revised)"
    assert len(ps.list_path_revisions(path_id)) == 1


def test_knowledge_promotion_route(client, active_project_setup):
    pid = active_project_setup["project_id"]

    kn = km.create_project_note(
        project_id=pid,
        title="Promotable Strategy Note",
        knowledge_type="finding",
        origin="human",
        body="Note content for collective",
        author="admin"
    )
    km.review_knowledge(kn["id"], "verified", reviewer="auditor", note="Checked")

    token = _csrf(client, f"/projects/{pid}?tab=knowledge")
    resp = client.post(
        f"/projects/{pid}/knowledge/{kn['id']}/promote",
        data={"csrf_token": token, "rationale": "Proven utility for all nodes"},
        follow_redirects=False
    )
    assert resp.status_code == 302
    meta = km.get_metadata(kn["id"])
    assert meta["is_collective"] == 1
    assert meta["promoted_by"] == "admin"


def test_stage_advance_route(client, active_project_setup):
    pid = active_project_setup["project_id"]
    s1 = active_project_setup["s1_id"]
    s2 = active_project_setup["s2_id"]

    # Fails if gate not satisfied
    token = _csrf(client, f"/projects/{pid}?tab=paths")
    resp = client.post(
        f"/projects/{pid}/stages/{s1}/advance",
        data={"csrf_token": token, "rationale": "Premature attempt"},
        follow_redirects=False
    )
    assert resp.status_code == 302
    # Verify redirected with error parameter
    q = parse_qs(urlparse(resp.headers["location"]).query)
    assert "err" in q

    # Complete gate requirements: 1 completed task + 1 accepted deliverable
    t = ts.create_task("Gate Task", project_id=pid, stage_id=s1,
                       purpose="P", expected_output="O", completion_criteria="C")
    ts.approve_task(t["id"])
    ts.record_task_execution(t["id"], "completed", "admin", "Attempted", "Done")

    d = ds.create_deliverable(pid, "Gate Deliv", "document", "admin", stage_id=s1)
    ds.submit_deliverable(d["id"], "admin", "art:1", "verified")
    ds.review_deliverable(d["id"], "accepted", "admin", "Accepted")

    # Now advance via route succeeds!
    token = _csrf(client, f"/projects/{pid}?tab=paths")
    resp_ok = client.post(
        f"/projects/{pid}/stages/{s1}/advance",
        data={"csrf_token": token, "rationale": "Stage 1 finished successfully"},
        follow_redirects=False
    )
    assert resp_ok.status_code == 302
    assert ps.get_stage(s1)["status"] == "completed"
    assert ps.get_stage(s2)["status"] == "active"
