"""Unit and integration tests for Milestone 4: Project-Aware Intelligence."""
import uuid
import pytest
from app.database import get_db_connection
from app.services import context_packet as cp_svc
from app.services import proposals as prop_svc
from app.services import projects as proj_svc
from app.services import tasks as task_svc
from app.services import metrics as met_svc


def _name(prefix="Test"):
    return f"{prefix} {uuid.uuid4().hex[:8]}"


@pytest.fixture
def active_project():
    proj = proj_svc.create_project(
        name=_name("Intelligence Pipeline"),
        description="Build cognitive reasoning pipeline",
        objective="Deliver bounded planning assistance",
        scope="Inference coordination and context packets",
        constraints_text="Deterministic fallback; low compute hardware",
        success_criteria="Token-budgeted packets assemble cleanly",
        owner="admin"
    )
    pid = proj["id"]
    path = proj_svc.create_path(
        pid, "admin",
        name=_name("Heuristic First Path"),
        rationale="Guarantees operation on old hardware without GPU"
    )
    proj_svc.change_path_status(pid, path["id"], "approved", "admin", "Approved")
    proj_svc.select_path(pid, path["id"], "admin", "Selected")
    proj_svc.change_project_status(pid, "active", "admin", "Active")
    s1 = proj_svc.create_stage(
        pid, path["id"], "admin",
        name="Stage 1 - Prototype",
        objective="Build packet builder",
        expected_outputs="Context packet service"
    )
    s2 = proj_svc.create_stage(
        pid, path["id"], "admin",
        name="Stage 2 - Integration",
        objective="Proposal workflow",
        expected_outputs="Approved proposals"
    )
    return {"project": proj_svc.get_project(pid), "path": proj_svc.get_path(path["id"]), "s1": s1, "s2": s2}


def test_context_packet_assembly_and_priority(active_project):
    """Test full context packet assembly includes all prioritized sections."""
    proj = active_project["project"]
    pid = proj["id"]

    # Add a metric and an observation
    metric = met_svc.define_metric(
        project_id=pid,
        name="Response Latency",
        unit="ms",
        metric_type="duration",
        target_value=250.0,
        owner="admin"
    )
    met_svc.record_observation(metric["id"], 220.0, created_by="admin")

    # Add a task
    task_svc.create_task(
        title="Prototype tokenizer",
        task_type="general",
        project_id=pid,
        stage_id=active_project["s1"]["id"],
        purpose="Token estimation",
    )

    packet = cp_svc.build_project_context_packet(
        project_id=pid,
        user_instruction="Propose next development steps",
        token_budget=4000
    )

    assert packet["project_id"] == pid
    assert packet["project_name"] == proj["name"]
    assert packet["token_budget"] == 4000
    assert packet["estimated_tokens"] > 0
    assert "User Request / Directive" in packet["rendered_prompt"]
    assert "Propose next development steps" in packet["rendered_prompt"]
    assert "Project: " in packet["rendered_prompt"]
    assert "Strategic Path & Stage Context" in packet["rendered_prompt"]
    assert "Response Latency" in packet["rendered_prompt"]
    assert "Prototype tokenizer" in packet["rendered_prompt"]
    assert "Security Separation: [TRUSTED_PROJECT_BOUNDS]" in packet["rendered_prompt"]


def test_context_packet_token_budget_pruning(active_project):
    """Test that setting a tight token budget prunes lower-priority sections with explicit omission notices."""
    pid = active_project["project"]["id"]

    # Assemble with very tight budget (e.g. 200 tokens)
    packet = cp_svc.build_project_context_packet(
        project_id=pid,
        user_instruction="Quick summary",
        token_budget=200
    )

    # Low priority sections should be marked omitted
    assert len(packet["omitted_info"]) > 0
    omitted_names = [o["section"] for o in packet["omitted_info"]]
    # Cross-project lessons (priority 8) and supporting knowledge (priority 7) are pruned first
    assert "Cross-Project Lessons" in omitted_names
    assert "Supporting Knowledge" in omitted_names
    assert "Notice of Omitted Context" in packet["rendered_prompt"]
    # Required core sections must still be present
    assert "Project Definition" in [s["name"] for s in packet["sections"] if s.get("required")]


def test_path_proposal_and_human_acceptance(active_project):
    """Test AI proposes a Path alternative, and human accepts it, creating real DB records."""
    pid = active_project["project"]["id"]

    proposal = prop_svc.create_proposal(
        project_id=pid,
        proposal_type="path_proposal",
        title="Distributed Inference Strategy",
        proposed_data={
            "name": "Distributed Inference Strategy",
            "rationale": "Leverage multiple small nodes in parallel.",
            "description": "Cluster coordination path.",
            "estimated_effort_hours": 60.0,
            "stages": [
                {
                    "name": "Node Discovery Stage",
                    "objective": "Scan local subnet for RPC worker machines.",
                    "expected_outputs": "Active machines registry."
                },
                {
                    "name": "Cluster Dispatch Stage",
                    "objective": "Distribute model weights across verified nodes.",
                    "expected_outputs": "Working distributed inference group."
                }
            ]
        },
        summary="Proposal for distributed model parallelism.",
        created_by="ai_assistant"
    )

    assert proposal["status"] == "proposed"
    assert proposal["title"] == "Distributed Inference Strategy"

    # Human reviews and accepts proposal
    reviewed = prop_svc.review_proposal(
        proposal_id=proposal["id"],
        action="accept",
        reviewed_by="admin",
        review_notes="Approved for exploration in next cycle."
    )
    assert reviewed["status"] == "accepted"
    assert reviewed["reviewed_by"] == "admin"

    # Verify durable path and stages were created in DB
    paths = proj_svc.list_paths(pid)
    created_path = next((p for p in paths if p["name"] == "Distributed Inference Strategy"), None)
    assert created_path is not None
    assert created_path["rationale"] == "Leverage multiple small nodes in parallel."

    stages = proj_svc.list_stages(created_path["id"])
    assert len(stages) == 2
    assert stages[0]["name"] == "Node Discovery Stage"
    assert stages[1]["name"] == "Cluster Dispatch Stage"

    # Verify append-only decision was logged
    decisions = proj_svc.list_decisions(pid)
    assert any(d["decision_type"] == "proposal_acceptance" and d["subject_id"] == proposal["id"] for d in decisions)


def test_task_decomposition_proposal_and_acceptance(active_project):
    """Test AI decomposes a stage into bounded tasks, which are created as draft tasks on human acceptance."""
    pid = active_project["project"]["id"]
    s1_id = active_project["s1"]["id"]

    proposal = prop_svc.create_proposal(
        project_id=pid,
        path_id=active_project["path"]["id"],
        stage_id=s1_id,
        proposal_type="task_decomposition",
        title=f"Task Breakdown for {active_project['s1']['name']}",
        proposed_data={
            "tasks": [
                {
                    "title": "Verify local llama.cpp binary",
                    "task_type": "diagnostic",
                    "purpose": "Confirm executable presence",
                    "expected_output": "llama-server binary path",
                    "completion_criteria": "Binary executes with --version"
                },
                {
                    "title": "Benchmark prompt processing",
                    "task_type": "general",
                    "purpose": "Record tokens/sec baseline",
                    "expected_output": "Benchmark row",
                    "completion_criteria": "Latency under 200ms"
                }
            ]
        },
        created_by="ai_assistant"
    )

    # Human reviews and accepts
    reviewed = prop_svc.review_proposal(proposal["id"], "accept", "admin", "Tasks look good")
    assert reviewed["status"] == "accepted"

    # Verify tasks created in DB under this stage
    tasks = task_svc.get_project_tasks(pid)
    s1_tasks = [t for t in tasks if t.get("stage_id") == s1_id]
    titles = [t["title"] for t in s1_tasks]
    assert "Verify local llama.cpp binary" in titles
    assert "Benchmark prompt processing" in titles
    # All created tasks must enter in draft/safe status (no autonomous online dispatch)
    for t in s1_tasks:
        assert t["status"] in ("draft", "awaiting_approval")


def test_proposal_rejection(active_project):
    """Test rejecting an AI proposal does NOT create any application records."""
    pid = active_project["project"]["id"]

    proposal = prop_svc.create_proposal(
        project_id=pid,
        proposal_type="path_proposal",
        title="Flawed Heavyweight Strategy",
        proposed_data={"name": "Flawed Heavyweight Strategy", "rationale": "Rewrite in Rust"},
        created_by="ai_assistant"
    )

    reviewed = prop_svc.review_proposal(
        proposal["id"], "reject", "admin", "Violates core constraint to preserve Python stack."
    )
    assert reviewed["status"] == "rejected"
    assert reviewed["review_notes"] == "Violates core constraint to preserve Python stack."

    # Verify no path created
    paths = proj_svc.list_paths(pid)
    assert not any(p["name"] == "Flawed Heavyweight Strategy" for p in paths)


def test_deterministic_ai_generators(active_project):
    """Test AI proposal generators with deterministic fallback."""
    pid = active_project["project"]["id"]
    s1_id = active_project["s1"]["id"]

    # 1. Path Proposal Generation
    path_prop = prop_svc.generate_path_proposals(pid, user_guidance="Focus on verification")
    assert path_prop["proposal_type"] == "path_proposal"
    assert path_prop["status"] == "proposed"
    assert "stages" in path_prop["proposed_data"]
    assert len(path_prop["proposed_data"]["stages"]) >= 2

    # 2. Task Decomposition Generation
    task_prop = prop_svc.generate_task_decomposition(pid, s1_id, user_guidance="Audit prerequisites")
    assert task_prop["proposal_type"] == "task_decomposition"
    assert task_prop["stage_id"] == s1_id
    assert len(task_prop["proposed_data"]["tasks"]) == 3

    # 3. Path Review Generation
    review_prop = prop_svc.generate_path_review(pid, path_id=active_project["path"]["id"])
    assert review_prop["proposal_type"] == "path_review"
    assert "recommendation" in review_prop["proposed_data"]
    assert review_prop["proposed_data"]["recommendation"] in ("continue", "reforecast", "advance", "pause")

