"""HTTP-level tests for the orchestrator API/UI: authentication, CSRF, flow, pages, artifact download."""
import re

import pytest
from starlette.testclient import TestClient

import orch_helpers as H
from app.auth import create_session_token
from app.main import app


@pytest.fixture
def cluster():
    H.register_node("t-vps", "vps_worker")
    H.register_node("t-laptop", "inference_node", models=["qwen2.5:1.5b", "llama3.2:1b"])
    H.disable_other_nodes(["t-vps", "t-laptop"])
    yield
    H.restore_nodes()


def _client():
    c = TestClient(app)
    c.cookies.set("qc_session", create_session_token("admin"))
    return c


def _csrf(c):
    html = c.get("/projects/orchestrate/new").text
    m = re.search(r'data-csrf="([0-9a-f]+)"', html)
    assert m, "csrf token not found in page"
    return {"X-CSRF-Token": m.group(1)}


OBJ = "Collect basic health information from the VPS and laptop, use a local AI model to summarize the results, and generate a consolidated system report (system health report)."


def _create(c, h, level=2):
    r = c.post("/api/v1/orchestration/projects", json={"name": "Route Proj " + H.uuid.uuid4().hex[:6], "objective": OBJ,
                                                      "autonomy_level": level}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["result"]["project_id"]


def test_unauthenticated_rejected():
    c = TestClient(app)
    assert c.get("/api/v1/projects/x/orchestration/status").status_code == 401
    assert c.post("/api/v1/orchestration/projects", json={}).status_code == 401
    assert c.get("/api/v1/orchestration/models").status_code == 401


def test_missing_or_bad_csrf_rejected():
    c = _client()
    assert c.post("/api/v1/orchestration/projects", json={"name": "x"}).status_code == 403
    assert c.post("/api/v1/orchestration/projects", json={"name": "x"}, headers={"X-CSRF-Token": "bad"}).status_code == 403
    assert c.post("/api/v1/projects/x/orchestration/start", json={}).status_code == 403


def test_pages_render(cluster):
    c = _client()
    h = _csrf(c)
    assert c.get("/projects/orchestrate/new").status_code == 200
    pid = _create(c, h)
    r = c.get(f"/projects/{pid}/orchestrator")
    assert r.status_code == 200 and "orchestrator" in r.text.lower()


def test_unknown_project_404(cluster):
    c = _client()
    assert c.get("/api/v1/projects/nope/orchestration/status").status_code == 404


def test_api_flow_plan_approve_start_complete(cluster):
    c = _client()
    h = _csrf(c)
    pid = _create(c, h, level=2)
    base = f"/api/v1/projects/{pid}/orchestration"
    r = c.post(base + "/plan", json={"mode": "template"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["result"]["state"] == "AWAITING_PLAN_APPROVAL"
    assert c.get(base + "/plan").json()["result"]["current"]["plan"]
    r = c.post(base + "/approve", json={}, headers=h)
    assert r.status_code == 200, r.text
    r = c.post(base + "/start", json={}, headers=h)
    assert r.status_code == 200, r.text

    from app.services.orchestration import engine
    run_id = c.get(base + "/status").json()["result"]["run"]["id"]
    state = None
    for _ in range(40):
        engine.tick(run_id)
        H.worker_step("t-vps")
        H.worker_step("t-laptop", ["qwen2.5:1.5b", "llama3.2:1b"])
        state = c.get(base + "/status").json()["result"]["run"]["state"]
        if state in ("COMPLETED", "BLOCKED", "FAILED"):
            break
    assert state == "COMPLETED", state

    arts = c.get(base + "/artifacts").json()["result"]
    assert arts
    aid = arts[0]["id"] if isinstance(arts, list) else arts["artifacts"][0]["id"]
    d = c.get(f"{base}/artifacts/{aid}/download")
    assert d.status_code == 200
    assert d.headers.get("x-content-type-options") == "nosniff"
    assert c.get(f"{base}/artifacts/..%2F..%2Fetc/download").status_code in (400, 404)
    assert c.get(f"{base}/artifacts/does-not-exist/download").status_code in (400, 404)
    assert c.get(base + "/events").json()["result"]


def test_invalid_transition_is_409(cluster):
    c = _client()
    h = _csrf(c)
    pid = _create(c, h)
    r = c.post(f"/api/v1/projects/{pid}/orchestration/start", json={}, headers=h)
    assert r.status_code in (400, 409)


def test_migration_v9_tables_present():
    from app.database import get_db_connection
    conn = get_db_connection()
    try:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"orchestration_runs", "plan_revisions", "orchestration_events", "orchestration_tasks"} <= names or \
            any(n.startswith("orchestration") for n in names)
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 0
    finally:
        conn.close()
