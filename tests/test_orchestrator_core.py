import json
import pytest

import orch_helpers as H
from app.services import projects as P
from app.services.orchestration import engine, planner, states, artifact_manager
from app.services.orchestration.common import get_run, list_events, OrchestrationError

OBJ = "Collect basic health information from the VPS and laptop, use a local AI model to summarize the results, and generate a consolidated system report (system health report)."


@pytest.fixture
def cluster():
    H.register_node("t-vps", "vps_worker")
    H.register_node("t-laptop", "inference_node", models=["qwen2.5:1.5b", "llama3.2:1b"])
    H.disable_other_nodes(["t-vps", "t-laptop"])
    yield
    H.restore_nodes()


def make_run(level=2, name="Health Project"):
    out = engine.create_project_with_run(name + " " + H.uuid.uuid4().hex[:6], "tester", {"objective": OBJ}, level)
    return out["project"], out["run"]


def drive(run_id, max_iter=40, level_manual=False):
    for _ in range(max_iter):
        engine.tick(run_id)
        for node, models in (("t-vps", None), ("t-laptop", ["qwen2.5:1.5b", "llama3.2:1b"])):
            H.worker_step(node, models)
        r = get_run(run_id)
        if r["state"] in ("COMPLETED", "BLOCKED", "FAILED", "CANCELLED"):
            return r
    return get_run(run_id)


def test_full_vertical_slice_autonomy2(cluster):
    project, run = make_run(2)
    res = planner.generate_plan(run["id"], "tester", mode="template")
    assert res["state"] == "AWAITING_PLAN_APPROVAL"
    from app.services.orchestration import approval_manager
    approval_manager.approve_plan(run["id"], "tester", "looks good")
    assert get_run(run["id"])["state"] == "READY"
    engine.start_run(run["id"], "tester")
    final = drive(run["id"])
    assert final["state"] == "COMPLETED"
    st = engine.status(project["id"])
    assert st["progress"]["percent"] == 100
    nodes = {t["key"]: t["node"] for t in st["tasks"]}
    assert nodes["collect_t_vps"] == "t-vps" and nodes["collect_t_laptop"] == "t-laptop"
    assert nodes["summarize_health"] == "t-laptop"
    arts = artifact_manager.list_artifacts(project["id"])
    names = {a["filename"] for a in arts}
    assert {"health_report.md", "health_report.json", "deliverables_index.json"} <= names
    prov = {a["filename"]: a["provenance"] for a in arts}
    assert prov["health_report.md"] == "controller_generated"
    assert any(a["provenance"] == "worker_telemetry" and a["validation_state"] == "valid" for a in arts)
    assert any(a["provenance"] == "model_output" for a in arts)
    md = [a for a in arts if a["filename"] == "health_report.md"][0]
    meta = artifact_manager.resolve_for_download(project["id"], md["id"])
    text = meta["path"].read_text("utf-8")
    assert "Measured telemetry" in text and "AI-generated, unverified" in text
    assert P.get_project(project["id"])["status"] == "completed"
    ev = [e["event_type"] for e in list_events(project["id"], limit=500)]
    assert "plan_approved" in ev and "task_dispatched" in ev and "task_accepted" in ev and "completed" in ev
