"""Unit and integration tests for Milestone 5: Historical Learning, Retrospectives, and Performance Evaluation."""
import uuid
import pytest
from app.services import deliverables as deliv_svc
from app.services import learning as learn_svc
from app.services import metrics as met_svc
from app.services import projects as proj_svc
from app.services import proposals as prop_svc
from app.services import context_packet as cp_svc


def _name(prefix="Test"):
    return f"{prefix} {uuid.uuid4().hex[:8]}"


@pytest.fixture
def active_project():
    proj = proj_svc.create_project(
        name=_name("Autonomous Learning Loop"),
        description="Continuous self-evaluating workflow",
        objective="Validate performance evaluation and retrospectives",
        scope="Calibration, deliverable outcomes, and retrospectives",
        constraints_text="Deterministic synthesis; no external vector DBs",
        owner="admin"
    )
    pid = proj["id"]
    path = proj_svc.create_path(
        pid, "admin",
        name=_name("Adaptive Strategy"),
        rationale="Iterative baseline with empirical verification"
    )
    proj_svc.change_path_status(pid, path["id"], "approved", "admin", "Approved")
    proj_svc.select_path(pid, path["id"], "admin", "Selected")
    proj_svc.change_project_status(pid, "active", "admin", "Active")
    stage = proj_svc.create_stage(
        pid, path["id"], "admin",
        name="Stage 1 - Learning Baseline",
        objective="Capture operational data"
    )
    return {"project": proj_svc.get_project(pid), "path": proj_svc.get_path(path["id"]), "stage": stage}


def test_calculate_model_calibration(active_project):
    """Test model calibration calculation across proposals and forecast evaluations."""
    pid = active_project["project"]["id"]
    path_id = active_project["path"]["id"]

    # 1. Create 2 proposals: 1 accepted, 1 rejected
    p1 = prop_svc.create_proposal(
        project_id=pid,
        proposal_type="path_proposal",
        title="Candidate Strategic Path",
        proposed_data={"name": "Candidate Path", "rationale": "High throughput"},
        created_by="ai_assistant"
    )
    prop_svc.review_proposal(p1["id"], "accept", "admin", "Looks good")

    p2 = prop_svc.create_proposal(
        project_id=pid,
        proposal_type="path_proposal",
        title="Unfeasible Heavyweight Path",
        proposed_data={"name": "Heavy Path", "rationale": "High overhead"},
        created_by="ai_assistant"
    )
    prop_svc.review_proposal(p2["id"], "reject", "admin", "Rejected for overhead")

    # 2. Create metric, observation, forecast, and evaluation
    met = met_svc.define_metric(
        project_id=pid,
        name="Processing Latency",
        unit="ms",
        metric_type="duration",
        path_id=path_id,
        target_value=150.0,
        owner="admin"
    )
    obs = met_svc.record_observation(met["id"], 140.0, created_by="admin")
    fct = met_svc.record_forecast(
        metric_id=met["id"],
        predicted_value=145.0,
        method="linear_trend",
        created_by="ai_assistant",
        path_id=path_id
    )
    met_svc.evaluate_forecast(
        forecast_id=fct["id"],
        actual_observation_id=obs["id"],
        evaluated_by="admin",
        notes="Accurate prediction"
    )

    # 3. Compute calibration
    calib = learn_svc.calculate_model_calibration(pid)
    assert calib["total_proposals"] >= 2
    assert calib["accepted_proposals"] >= 1
    assert calib["rejected_proposals"] >= 1
    assert calib["proposal_acceptance_rate"] == 0.5
    assert calib["total_evaluations"] >= 1
    assert calib["mean_forecast_accuracy"] is not None
    assert calib["mean_forecast_accuracy"] > 0.8
    assert "linear_trend" in calib["method_breakdown"]


def test_deliverable_outcome_evaluation(active_project):
    """Test recording and querying deliverable outcome impact evaluations."""
    pid = active_project["project"]["id"]
    stage_id = active_project["stage"]["id"]

    deliv = deliv_svc.create_deliverable(
        project_id=pid,
        name="Performance Optimization Engine",
        deliverable_type="software_artifact",
        created_by="admin",
        stage_id=stage_id,
        acceptance_criteria="P99 latency under 200ms"
    )
    deliv_svc.submit_deliverable(deliv["id"], user="admin", artifact_references="git:commit-1234", verification_results="All tests pass")
    deliv_svc.review_deliverable(deliv["id"], new_status="accepted", reviewer="admin", rationale="Verified benchmarks and accepted.")

    # Record outcome evaluation
    eval_record = learn_svc.record_deliverable_evaluation(
        deliverable_id=deliv["id"],
        evaluation_type="post_delivery_impact",
        outcome_status="exceeded_expectations",
        impact_summary="Latency dropped by 35% in production telemetry, beating the 20% target.",
        evidence_reference="telemetry-benchmark-log-01",
        lessons_learned="Parallelized token streaming yielded higher gains than batching.",
        evaluated_by="admin",
        project_id=pid
    )

    assert eval_record["id"].startswith("deval_")
    assert eval_record["outcome_status"] == "exceeded_expectations"
    assert "Latency dropped" in eval_record["impact_summary"]

    # Verify query
    evals = learn_svc.list_deliverable_evaluations(pid)
    assert any(e["id"] == eval_record["id"] for e in evals)

    # Verify decision recorded
    decisions = proj_svc.list_decisions(pid)
    assert any(d["decision_type"] == "deliverable_outcome_evaluation" and d["subject_id"] == eval_record["id"] for d in decisions)


def test_generate_and_publish_project_retrospective(active_project):
    """Test generating, reviewing, and publishing a project retrospective with Markdown artifacts."""
    pid = active_project["project"]["id"]
    stage_id = active_project["stage"]["id"]

    # Set up completed deliverable
    deliv = deliv_svc.create_deliverable(
        project_id=pid,
        name="Production Release Bundle",
        deliverable_type="document",
        created_by="admin",
        stage_id=stage_id,
        acceptance_criteria="All specs complete"
    )
    deliv_svc.submit_deliverable(deliv["id"], user="admin", artifact_references="doc:release-notes-v1")
    deliv_svc.review_deliverable(deliv["id"], new_status="accepted", reviewer="admin", rationale="Accepted release docs.")

    # 1. Synthesize draft retrospective
    retro = learn_svc.generate_project_retrospective(
        project_id=pid,
        user_guidance="Highlight lessons on deterministic gates",
        created_by="ai_assistant"
    )

    assert retro["status"] == "draft"
    assert retro["project_id"] == pid
    assert "Retrospective" in retro["title"]
    assert len(retro["lessons_learned"]) > 0
    assert len(retro["recommendations"]) > 0

    # 2. Publish retrospective
    published = learn_svc.publish_project_retrospective(
        retrospective_id=retro["id"],
        reviewed_by="admin",
        promote_to_collective=True
    )

    assert published["status"] == "published"
    assert published["reviewed_by"] == "admin"
    assert published["knowledge_path"] is not None
    assert published["knowledge_path"].endswith("retrospective.md")

    # Verify decision logged
    decisions = proj_svc.list_decisions(pid)
    assert any(d["decision_type"] == "retrospective_publication" and d["subject_id"] == retro["id"] for d in decisions)


def test_search_cross_project_lessons(active_project):
    """Test cross-project lesson retrieval transfers knowledge to new projects while filtering self-references."""
    proj_a = active_project["project"]
    pid_a = proj_a["id"]

    # Publish a retrospective for Project A
    retro_a = learn_svc.generate_project_retrospective(project_id=pid_a)
    learn_svc.publish_project_retrospective(retro_a["id"], reviewed_by="admin", promote_to_collective=True)

    # Create Project B
    proj_b = proj_svc.create_project(
        name=_name("Next Generation Work"),
        description="Subsequent project",
        owner="admin"
    )
    pid_b = proj_b["id"]

    # Search lessons from Project B perspective
    lessons_for_b = learn_svc.search_cross_project_lessons(current_project_id=pid_b)
    assert len(lessons_for_b) > 0
    # Must include lessons from Project A
    assert any(l["project_id"] == pid_a or l["project_name"] == proj_a["name"] for l in lessons_for_b)
    # Must NOT include lessons from Project B itself
    assert not any(l["project_id"] == pid_b for l in lessons_for_b)


def test_context_packet_priority_8_incorporates_published_retrospectives(active_project):
    """Test that Project Context Packets automatically incorporate historical retrospectives under Priority 8."""
    proj_a = active_project["project"]
    pid_a = proj_a["id"]

    retro_a = learn_svc.generate_project_retrospective(project_id=pid_a)
    learn_svc.publish_project_retrospective(retro_a["id"], reviewed_by="admin", promote_to_collective=True)

    # Project B context packet
    proj_b = proj_svc.create_project(
        name=_name("Target Analysis Pipeline"),
        description="Target pipeline benefiting from prior history",
        owner="admin"
    )
    packet_b = cp_svc.build_project_context_packet(
        project_id=proj_b["id"],
        user_instruction="Analyze previous cycle outcomes",
        token_budget=4000
    )

    prompt = packet_b["rendered_prompt"]
    assert "Cross-Project Reusable Lessons & Collective Memory" in prompt
    assert proj_a["name"] in prompt or "Published Retrospectives" in prompt
