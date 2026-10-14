"""Milestone 2 tests: Execution and evidence.

Tests for:
1. Deliverable tracking, verification, artifact references, and acceptance decisions.
2. Task dependencies, cycle prevention, and prerequisite completion enforcement.
3. Deterministic stage completion gates and automated stage advancement.
4. Task execution operational events and durable Markdown knowledge capture.
5. Path revisions with strategic assumption and estimate snapshotting.
6. Knowledge promotion workflow to collective memory.
"""
import uuid
import pytest

from app.database import get_db_connection
from app.services import deliverables as ds
from app.services import knowledge_meta as km
from app.services import projects as ps
from app.services import tasks as ts


def _name(prefix="Test"):
    return f"{prefix} {uuid.uuid4().hex[:8]}"


@pytest.fixture
def active_project():
    """Active project with selected active Path and 2 stages."""
    proj = ps.create_project(_name("Proj M2"), owner="admin", objective="Deliverable system", success_criteria="Gates pass")
    pid = proj["id"]
    path = ps.create_path(pid, "admin", name="Strategy Alpha", rationale="Direct evidence approach")
    ps.change_path_status(pid, path["id"], "approved", "admin", "Approved estimates")
    ps.select_path(pid, path["id"], "admin", "Primary strategy")
    ps.change_project_status(pid, "active", "admin", "Kickoff")
    s1 = ps.create_stage(pid, path["id"], "admin", name="Stage 1 - Analysis")
    s2 = ps.create_stage(pid, path["id"], "admin", name="Stage 2 - Implementation")
    return {"project": ps.get_project(pid), "path": ps.get_path(path["id"]), "s1": s1, "s2": s2}


# --- Deliverables Tests -----------------------------------------------------

def test_deliverable_lifecycle_full(active_project):
    pid = active_project["project"]["id"]
    s1 = active_project["s1"]["id"]

    # 1. Create planned deliverable
    d = ds.create_deliverable(
        project_id=pid,
        name="Security Architecture Document",
        deliverable_type="document",
        created_by="admin",
        stage_id=s1,
        description="Formal security review architecture",
        acceptance_criteria="Passes peer review with 0 open high risks.",
        due_date="2026-12-01"
    )
    assert d["id"].startswith("del-")
    assert d["status"] == "planned"
    assert d["deliverable_type"] == "document"
    assert d["stage_id"] == s1
    assert d["path_id"] == active_project["path"]["id"]

    # 2. Update deliverable
    d_up = ds.update_deliverable(
        d["id"],
        user="admin",
        description="Updated architecture spec",
        acceptance_criteria="Passes peer review and automated lint checks."
    )
    assert d_up["description"] == "Updated architecture spec"
    assert "automated lint" in d_up["acceptance_criteria"]

    # 3. Submit deliverable
    d_sub = ds.submit_deliverable(
        d["id"],
        user="admin",
        artifact_references="docs/sec_arch.md, git:abc1234",
        verification_results="Automated scanner passed with 0 findings."
    )
    assert d_sub["status"] == "submitted"
    assert d_sub["artifact_references"] == "docs/sec_arch.md, git:abc1234"

    # Verify revision history recorded submission
    revs = ds.list_deliverable_revisions(d["id"])
    assert len(revs) == 1
    assert revs[0]["status"] == "submitted"
    assert revs[0]["decided_by"] == "admin"

    # 4. Review deliverable - Request Changes
    d_rev1 = ds.review_deliverable(
        d["id"],
        new_status="changes_requested",
        reviewer="auditor",
        rationale="Needs threat modeling diagram.",
        verification_results="Threat model section incomplete."
    )
    assert d_rev1["status"] == "changes_requested"
    assert len(ds.list_deliverable_revisions(d["id"])) == 2

    # 5. Resubmit with revisions
    ds.submit_deliverable(
        d["id"],
        user="admin",
        artifact_references="docs/sec_arch.md, git:def5678",
        verification_results="Threat model added with STRIDE analysis."
    )

    # 6. Review deliverable - Accept
    d_acc = ds.review_deliverable(
        d["id"],
        new_status="accepted",
        reviewer="lead_auditor",
        rationale="All criteria verified and threat modeling satisfies requirements.",
        verification_results="STRIDE model verified."
    )
    assert d_acc["status"] == "accepted"
    assert d_acc["accepted_by"] == "lead_auditor"
    assert d_acc["accepted_at"] is not None

    # Verify formal decision recorded in project_decisions
    decisions = ps.list_decisions(pid)
    acceptance_decs = [dec for dec in decisions if dec["decision_type"] == "deliverable_acceptance"]
    assert len(acceptance_decs) >= 2
    assert acceptance_decs[0]["to_value"] == "accepted"
    assert acceptance_decs[0]["decided_by"] == "lead_auditor"

    # 7. Cannot edit accepted deliverable
    with pytest.raises(ps.ValidationError, match="Cannot edit deliverable"):
        ds.update_deliverable(d["id"], user="admin", name="New Name")


def test_deliverable_cross_stage_validation(active_project):
    pid = active_project["project"]["id"]
    other_proj = ps.create_project(_name("Other"), owner="admin")
    other_path = ps.create_path(other_proj["id"], "admin", name="Other Path", rationale="R")
    other_stage = ps.create_stage(other_proj["id"], other_path["id"], "admin", name="Foreign Stage")

    # Linking stage from another project must fail
    with pytest.raises(ps.ValidationError):
        ds.create_deliverable(pid, "Cross Deliverable", "dataset", "admin", stage_id=other_stage["id"])


# --- Task Dependencies & Cycle Prevention -----------------------------------

def test_task_dependency_and_cycle_prevention(active_project):
    pid = active_project["project"]["id"]
    s1 = active_project["s1"]["id"]

    t1 = ts.create_task("Task 1 - Schema design", project_id=pid, stage_id=s1,
                        purpose="Purpose 1", expected_output="Output 1", completion_criteria="Criteria 1")
    t2 = ts.create_task("Task 2 - Backend implementation", project_id=pid, stage_id=s1,
                        purpose="Purpose 2", expected_output="Output 2", completion_criteria="Criteria 2")
    t3 = ts.create_task("Task 3 - Integration tests", project_id=pid, stage_id=s1,
                        purpose="Purpose 3", expected_output="Output 3", completion_criteria="Criteria 3")

    # T2 depends on T1
    ts.add_task_dependency(t2["id"], t1["id"])
    deps = ts.get_task_dependencies(t2["id"])
    assert len(deps) == 1
    assert deps[0]["id"] == t1["id"]

    # T3 depends on T2 (T3 -> T2 -> T1)
    ts.add_task_dependency(t3["id"], t2["id"])
    assert len(ts.get_task_dependencies(t3["id"])) == 1

    # Cycle detection: T1 depending on T3 must be rejected (T1 -> T3 -> T2 -> T1)
    with pytest.raises(ps.ValidationError, match="Circular task dependency"):
        ts.add_task_dependency(t1["id"], t3["id"])

    # Self-dependency rejected
    with pytest.raises(ps.ValidationError, match="cannot depend on itself"):
        ts.add_task_dependency(t1["id"], t1["id"])

    # Remove dependency
    ts.remove_task_dependency(t3["id"], t2["id"])
    assert len(ts.get_task_dependencies(t3["id"])) == 0


def test_prerequisite_task_completion_enforcement(active_project):
    pid = active_project["project"]["id"]
    s1 = active_project["s1"]["id"]

    # Start stage 1
    ps.change_stage_status(pid, s1, "active")

    t1 = ts.create_task("Task Prereq", project_id=pid, stage_id=s1,
                        purpose="P1", expected_output="O1", completion_criteria="C1")
    t2 = ts.create_task("Task Dependent", project_id=pid, stage_id=s1,
                        purpose="P2", expected_output="O2", completion_criteria="C2")

    ts.add_task_dependency(t2["id"], t1["id"])

    # T1 is currently in awaiting_approval (not completed)
    # T2 validation must report that T1 is not completed
    t2_loaded = ts.get_task_by_id(t2["id"])
    problems = ts.validate_project_task_dispatch(t2_loaded)
    assert any("prerequisite" in p.lower() for p in problems)

    # Approving T2 while T1 is incomplete must fail
    assert ts.approve_task(t2["id"]) is False

    # Complete T1
    ts.approve_task(t1["id"])
    ts.record_task_execution(
        task_id=t1["id"],
        outcome="completed",
        user="worker1",
        attempted="Completed prereq work",
        actual_result="Output generated cleanly"
    )

    t1_done = ts.get_task_by_id(t1["id"])
    assert t1_done["status"] == "completed"

    # Now T2 prerequisites are satisfied
    t2_ready = ts.get_task_by_id(t2["id"])
    assert ts.validate_project_task_dispatch(t2_ready) == []
    assert ts.approve_task(t2["id"]) is True
    assert ts.get_task_by_id(t2["id"])["status"] == "queued"


# --- Task Execution Operational Events & Durable Knowledge ------------------

def test_task_execution_event_and_durable_knowledge(active_project):
    pid = active_project["project"]["id"]
    s1 = active_project["s1"]["id"]
    ps.change_stage_status(pid, s1, "active")

    task = ts.create_task("Model Benchmark Task", project_id=pid, stage_id=s1,
                          purpose="Evaluate throughput", expected_output="Tokens/sec report", completion_criteria="Recorded")
    ts.approve_task(task["id"])

    # Record execution with durable knowledge generation
    updated_task = ts.record_task_execution(
        task_id=task["id"],
        outcome="completed",
        user="benchmarker",
        attempted="Ran 500 prompt evaluations on local GPU",
        actual_result="Achieved 42.5 tokens/sec average throughput",
        evidence_summary="Log captured in benchmarks/gpu_20261014.json",
        assumptions_feedback="Confirms assumption of >30 t/s on 8-bit quantized weights.",
        reusable_lessons="Context size 8k requires 12GB VRAM headroom.",
        create_knowledge_note=True,
        knowledge_title="GPU Benchmark Evidence Note"
    )
    assert updated_task["status"] == "completed"
    assert updated_task["attempt_count"] == 1  # 1 recorded execution event

    # Check execution event record
    events = ts.list_task_execution_events(task["id"])
    assert len(events) == 1
    ev = events[0]
    assert ev["outcome"] == "completed"
    assert ev["attempted"] == "Ran 500 prompt evaluations on local GPU"
    assert ev["resulting_knowledge_id"] is not None

    # Check durable knowledge note
    kn = km.get_metadata(ev["resulting_knowledge_id"])
    assert kn is not None
    assert kn["knowledge_type"] == "task_result"
    assert kn["project_id"] == pid

    # Check relation 'result_of' pointing back to task
    rels = km.list_relations_for_target("task", task["id"])
    assert any(r["relation_type"] == "result_of" and r["knowledge_id"] == kn["id"] for r in rels)


# --- Deterministic Stage Completion Gates ------------------------------------

def test_stage_completion_gate_and_advancement(active_project):
    pid = active_project["project"]["id"]
    s1 = active_project["s1"]["id"]
    s2 = active_project["s2"]["id"]

    ps.change_stage_status(pid, s1, "active")

    # Empty stage gate is NOT satisfied (requires work and deliverables)
    gate = ps.evaluate_stage_gate(pid, s1)
    assert gate["is_satisfied"] is False
    assert any("no associated tasks" in b.lower() for b in gate["blocking_reasons"])

    # Add task and deliverable to S1
    t = ts.create_task("S1 Task", project_id=pid, stage_id=s1,
                       purpose="Analysis", expected_output="Doc", completion_criteria="Done")
    d = ds.create_deliverable(pid, "S1 Deliverable", "document", "admin", stage_id=s1,
                             acceptance_criteria="Signoff")

    # Gate blocked by incomplete task and unaccepted deliverable
    gate2 = ps.evaluate_stage_gate(pid, s1)
    assert gate2["is_satisfied"] is False
    assert any("not completed" in b.lower() for b in gate2["blocking_reasons"])
    assert any("not accepted" in b.lower() for b in gate2["blocking_reasons"])

    # Advance must fail
    with pytest.raises(ps.ValidationError, match="completion gate blocked"):
        ps.advance_stage(pid, s1, user="admin")

    # Complete task
    ts.approve_task(t["id"])
    ts.record_task_execution(t["id"], "completed", "admin", "Attempt", "Result", create_knowledge_note=True)

    # Deliverable submitted but not accepted
    ds.submit_deliverable(d["id"], "admin", "artifact:spec.pdf", "verified clean")
    gate3 = ps.evaluate_stage_gate(pid, s1)
    assert gate3["is_satisfied"] is False

    # Accept deliverable
    ds.review_deliverable(d["id"], "accepted", "auditor", "Meets all criteria")

    # Now stage gate is satisfied!
    gate4 = ps.evaluate_stage_gate(pid, s1)
    assert gate4["is_satisfied"] is True
    assert len(gate4["blocking_reasons"]) == 0

    # Advance stage
    advanced_s1 = ps.advance_stage(pid, s1, user="admin", rationale="All deliverables accepted and gate passed.")
    assert advanced_s1["status"] == "completed"
    assert advanced_s1["completed_at"] is not None

    # Automated advancement: next stage S2 should now be automatically activated!
    s2_updated = ps.get_stage(s2)
    assert s2_updated["status"] == "active"
    assert s2_updated["started_at"] is not None

    # Decision records check
    decisions = ps.list_decisions(pid)
    types = [dec["decision_type"] for dec in decisions]
    assert "stage_advancement" in types


# --- Path Revisions ---------------------------------------------------------

def test_path_revision_snapshotting(active_project):
    pid = active_project["project"]["id"]
    path_id = active_project["path"]["id"]

    orig_path = ps.get_path(path_id)
    assert orig_path["name"] == "Strategy Alpha"

    # Revise path with new estimates and assumptions
    revised = ps.revise_path(
        project_id=pid,
        path_id=path_id,
        user="strategist",
        revision_reason="Adjusted for higher token throughput and lower cost",
        name="Strategy Alpha (Revised)",
        rationale="Upgraded to newer quant model with lower latency",
        assumptions="GPU vRAM allows batch size 4",
        estimated_cost="150.00",
        estimated_effort_hours="40.0",
        forecast_confidence="0.85"
    )

    assert revised["name"] == "Strategy Alpha (Revised)"
    assert revised["estimated_cost"] == 150.0
    assert revised["forecast_confidence"] == 0.85

    # Check revision history snapshots original values
    revs = ps.list_path_revisions(path_id)
    assert len(revs) == 1
    assert revs[0]["revision_number"] == 1
    assert revs[0]["name"] == "Strategy Alpha"  # original snapshot
    assert revs[0]["revision_reason"] == "Adjusted for higher token throughput and lower cost"
    assert revs[0]["revised_by"] == "strategist"

    # Check decision log recorded path revision
    decs = ps.list_decisions(pid)
    assert any(d["decision_type"] == "path_revision" and d["subject_id"] == path_id for d in decs)


# --- Knowledge Promotion to Collective Memory -------------------------------

def test_knowledge_promotion_workflow(active_project):
    pid = active_project["project"]["id"]

    # 1. Create a captured project note
    kn = km.create_project_note(
        project_id=pid,
        title="Key Architectural Discovery",
        knowledge_type="finding",
        origin="human",
        body="Found that SQLite WAL mode with mmap achieves sub-millisecond query latency.",
        author="engineer"
    )
    assert kn["trust_status"] == "captured"

    # Captured knowledge CANNOT be promoted
    with pytest.raises(ps.ValidationError, match="Only verified or accepted"):
        km.promote_to_collective(kn["id"], user="lead")

    # Review to 'verified'
    km.review_knowledge(kn["id"], "verified", reviewer="auditor", note="Verified through benchmark runs.")

    # Now promotion succeeds
    promoted = km.promote_to_collective(
        kn["id"],
        user="lead",
        rationale="Generalizable performance pattern for all collective nodes."
    )
    assert promoted["is_collective"] == 1
    assert promoted["promoted_by"] == "lead"
    assert promoted["promoted_at"] is not None

    # Check collective knowledge list
    collective_items = km.list_collective_knowledge()
    assert any(item["id"] == kn["id"] for item in collective_items)

    # Check decision record
    decs = ps.list_decisions(pid)
    promo_decs = [d for d in decs if d["decision_type"] == "knowledge_promotion"]
    assert len(promo_decs) == 1
    assert promo_decs[0]["subject_id"] == kn["id"]
    assert promo_decs[0]["to_value"] == "collective"
