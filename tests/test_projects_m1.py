"""Milestone 1 project logic: hierarchy, status machines, Path selection, stages, task traceability.

Integration tests against the real (temporary) SQLite database; no mocks.
"""
import sqlite3
import uuid

import pytest

from app.database import get_db_connection
from app.services import projects as ps
from app.services import tasks as ts


def _name(prefix="Proj"):
    return f"{prefix} {uuid.uuid4().hex[:8]}"


@pytest.fixture
def project():
    return ps.create_project(_name(), owner="admin", objective="Build an archive", success_criteria="1000 docs")


@pytest.fixture
def active_setup(project):
    """Active project with a selected active Path and two stages."""
    pid = project["id"]
    path = ps.create_path(pid, "admin", name="Manual collection", rationale="Lowest risk; no scraping")
    ps.change_path_status(pid, path["id"], "approved", "admin", "Reviewed estimates")
    ps.select_path(pid, path["id"], "admin", "Best fit for constraints")
    ps.change_project_status(pid, "active", "admin", "Kick-off")
    s1 = ps.create_stage(pid, path["id"], "admin", name="Inventory sources")
    s2 = ps.create_stage(pid, path["id"], "admin", name="Collect documents")
    return {"project": ps.get_project(pid), "path": ps.get_path(path["id"]), "s1": s1, "s2": s2}


# --- projects ----------------------------------------------------------------

def test_create_project_defaults(project):
    assert project["status"] == "draft"
    assert project["id"].startswith("prj-")
    assert project["knowledge_dir"].startswith("projects/")
    assert project["selected_path_id"] is None


def test_duplicate_project_name_rejected_case_insensitive():
    n = _name("Dup")
    ps.create_project(n, owner="admin")
    with pytest.raises(ps.ValidationError):
        ps.create_project(n.upper(), owner="admin")


def test_project_name_required():
    with pytest.raises(ps.ValidationError):
        ps.create_project("   ", owner="admin")


def test_parent_cycle_and_self_parent_rejected():
    a = ps.create_project(_name("A"), owner="admin")
    b = ps.create_project(_name("B"), owner="admin", parent_id=a["id"])
    with pytest.raises(ps.ValidationError, match="circular"):
        ps.update_project(a["id"], "admin", parent_id=b["id"])
    with pytest.raises(ps.ValidationError, match="own parent"):
        ps.update_project(a["id"], "admin", parent_id=a["id"])
    # DB CHECK also refuses self-parenting written directly
    conn = get_db_connection()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE projects SET parent_id = id WHERE id = ?", (a["id"],))
    conn.close()


def test_nesting_depth_limited():
    a = ps.create_project(_name("L1"), owner="admin")
    b = ps.create_project(_name("L2"), owner="admin", parent_id=a["id"])
    c = ps.create_project(_name("L3"), owner="admin", parent_id=b["id"])
    with pytest.raises(ps.ValidationError, match="levels"):
        ps.create_project(_name("L4"), owner="admin", parent_id=c["id"])
    # Moving a subtree under a deeper node is also bounded
    x = ps.create_project(_name("X"), owner="admin")
    ps.create_project(_name("X-child"), owner="admin", parent_id=x["id"])
    with pytest.raises(ps.ValidationError, match="levels"):
        ps.update_project(x["id"], "admin", parent_id=b["id"])


def test_project_status_transitions_and_decisions(project):
    pid = project["id"]
    with pytest.raises(ps.ValidationError):
        ps.change_project_status(pid, "completed", "admin")  # draft -> completed not allowed
    p = ps.change_project_status(pid, "active", "admin", "Go")
    assert p["status"] == "active" and p["start_date"]
    decisions = ps.list_decisions(pid)
    assert decisions[0]["decision_type"] == "project_status_change"
    assert (decisions[0]["from_value"], decisions[0]["to_value"]) == ("draft", "active")


def test_archived_project_is_read_only(project):
    ps.change_project_status(project["id"], "cancelled", "admin", "Stop")
    ps.change_project_status(project["id"], "archived", "admin", "File away")
    with pytest.raises(ps.ValidationError):
        ps.update_project(project["id"], "admin", name="Renamed")


# --- paths -------------------------------------------------------------------

def test_path_requires_rationale(project):
    with pytest.raises(ps.ValidationError, match="rationale"):
        ps.create_path(project["id"], "admin", name="No why", rationale="  ")


def test_path_numeric_validation(project):
    with pytest.raises(ps.ValidationError):
        ps.create_path(project["id"], "admin", name="P", rationale="r", forecast_confidence="1.5")
    with pytest.raises(ps.ValidationError):
        ps.create_path(project["id"], "admin", name="P", rationale="r", estimated_cost="-3")
    with pytest.raises(ps.ValidationError):
        ps.create_path(project["id"], "admin", name="P", rationale="r", estimated_cost="nan")


def test_path_cannot_be_activated_without_selection(project):
    path = ps.create_path(project["id"], "admin", name="P1", rationale="r")
    with pytest.raises(ps.ValidationError):
        ps.change_path_status(project["id"], path["id"], "active", "admin", "x")
    with pytest.raises(ps.ValidationError, match="approved"):
        ps.select_path(project["id"], path["id"], "admin", "select unapproved")
    with pytest.raises(ps.ValidationError):
        ps.change_path_status(project["id"], path["id"], "superseded", "admin", "invalid jump")


def test_status_change_requires_reason(project):
    path = ps.create_path(project["id"], "admin", name="P1", rationale="r")
    with pytest.raises(ps.ValidationError, match="Reason"):
        ps.change_path_status(project["id"], path["id"], "approved", "admin", "")


def test_path_switch_supersedes_previous_and_records_history(project):
    pid = project["id"]
    a = ps.create_path(pid, "admin", name="Manual", rationale="r1", estimated_cost="100")
    b = ps.create_path(pid, "admin", name="Automated", rationale="r2", estimated_cost="40")
    for p in (a, b):
        ps.change_path_status(pid, p["id"], "approved", "admin", "ok")
    ps.select_path(pid, a["id"], "admin", "Start manual")
    ps.select_path(pid, b["id"], "admin", "Manual too slow")

    assert ps.get_path(a["id"])["status"] == "superseded"
    assert ps.get_path(a["id"])["estimated_cost"] == 100  # historical estimate preserved
    assert ps.get_path(b["id"])["status"] == "active"
    assert ps.get_project(pid)["selected_path_id"] == b["id"]
    selections = [d for d in ps.list_decisions(pid) if d["decision_type"] == "path_selection"]
    assert selections[0]["from_value"] == a["id"] and selections[0]["to_value"] == b["id"]
    assert selections[0]["rationale"] == "Manual too slow" and selections[0]["decided_by"] == "admin"
    # Both Paths remain visible for comparison
    assert {p["id"] for p in ps.list_paths(pid)} >= {a["id"], b["id"]}


def test_single_active_path_enforced_by_database(active_setup):
    pid = active_setup["project"]["id"]
    other = ps.create_path(pid, "admin", name="Second", rationale="r")
    conn = get_db_connection()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE project_paths SET status='active' WHERE id = ?", (other["id"],))
    conn.close()


def test_selected_path_must_belong_to_project(active_setup):
    other_project = ps.create_project(_name("Other"), owner="admin")
    conn = get_db_connection()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE projects SET selected_path_id = ? WHERE id = ?",
                     (active_setup["path"]["id"], other_project["id"]))
    conn.close()


def test_path_details_frozen_after_approval(project):
    path = ps.create_path(project["id"], "admin", name="P", rationale="r", estimated_effort_hours="10")
    ps.update_path(project["id"], path["id"], "admin", estimated_effort_hours="12")
    ps.change_path_status(project["id"], path["id"], "approved", "admin", "ok")
    with pytest.raises(ps.ValidationError, match="frozen"):
        ps.update_path(project["id"], path["id"], "admin", estimated_effort_hours="99")
    assert ps.get_path(path["id"])["estimated_effort_hours"] == 12


def test_decisions_are_append_only(active_setup):
    conn = get_db_connection()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE project_decisions SET rationale = 'rewritten'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM project_decisions")
    conn.close()


def test_cross_project_path_access_rejected(active_setup):
    other = ps.create_project(_name("Other"), owner="admin")
    with pytest.raises(ps.NotFound):
        ps.select_path(other["id"], active_setup["path"]["id"], "admin", "hijack")
    with pytest.raises(ps.NotFound):
        ps.change_path_status(other["id"], active_setup["path"]["id"], "paused", "admin", "hijack")


# --- stages ------------------------------------------------------------------

def test_stage_ordering_and_reorder(active_setup):
    pid, path = active_setup["project"]["id"], active_setup["path"]
    s3 = ps.create_stage(pid, path["id"], "admin", name="Verify")
    assert [s["name"] for s in ps.list_stages(path["id"])] == ["Inventory sources", "Collect documents", "Verify"]
    ps.move_stage(pid, s3["id"], "up")
    assert [s["name"] for s in ps.list_stages(path["id"])] == ["Inventory sources", "Verify", "Collect documents"]
    ps.move_stage(pid, active_setup["s1"]["id"], "up")  # already first: no-op
    assert ps.list_stages(path["id"])[0]["id"] == active_setup["s1"]["id"]
    positions = [s["position"] for s in ps.list_stages(path["id"])]
    assert positions == sorted(set(positions))


def test_stage_cross_project_rejected(active_setup):
    other = ps.create_project(_name("Other"), owner="admin")
    with pytest.raises(ps.NotFound):
        ps.move_stage(other["id"], active_setup["s1"]["id"], "down")


def test_stage_completion_not_available_in_m1(active_setup):
    pid = active_setup["project"]["id"]
    with pytest.raises(ps.ValidationError, match="gates"):
        ps.change_stage_status(pid, active_setup["s1"]["id"], "completed")
    s = ps.change_stage_status(pid, active_setup["s1"]["id"], "active")
    assert s["status"] == "active" and s["started_at"]
    assert ps.current_stage(active_setup["path"]["id"])["id"] == active_setup["s1"]["id"]


def test_stage_cannot_start_on_non_active_path(project):
    path = ps.create_path(project["id"], "admin", name="P", rationale="r")
    st = ps.create_stage(project["id"], path["id"], "admin", name="S")
    with pytest.raises(ps.ValidationError, match="active Path"):
        ps.change_stage_status(project["id"], st["id"], "active")


# --- tasks -------------------------------------------------------------------

def _traceable_task(setup, **over):
    kw = dict(title="Find county records", project_id=setup["project"]["id"], path_id=setup["path"]["id"],
              stage_id=setup["s1"]["id"], purpose="Identify sources", expected_output="Source list",
              completion_criteria="List reviewed")
    kw.update(over)
    return ts.create_task(**kw)


def test_non_project_tasks_unchanged():
    t = ts.create_task(title="Plain task", approval_required=False)
    assert t["status"] == "queued" and t["project_id"] is None


def test_traceable_project_task_awaits_approval_then_queues(active_setup):
    t = _traceable_task(active_setup)
    assert t["status"] == "awaiting_approval" and t["approval_required"] == 1
    assert ts.approve_task(t["id"]) is True
    assert ts.get_task_by_id(t["id"])["status"] == "queued"


def test_incomplete_project_task_is_draft_and_cannot_be_approved(active_setup):
    t = _traceable_task(active_setup, completion_criteria="")
    assert t["status"] == "draft"
    assert ts.approve_task(t["id"]) is False
    problems = ts.submit_task_for_approval(t["id"])
    assert any("Completion criteria" in p for p in problems)


def test_task_on_unselected_path_is_draft(active_setup):
    pid = active_setup["project"]["id"]
    alt = ps.create_path(pid, "admin", name="Alt", rationale="r")
    st = ps.create_stage(pid, alt["id"], "admin", name="Alt stage")
    t = _traceable_task(active_setup, path_id=alt["id"], stage_id=st["id"])
    assert t["status"] == "draft"
    assert any("not the project's selected Path" in p for p in ts.validate_project_task_dispatch(t))


def test_approval_revalidates_after_path_switch(active_setup):
    pid = active_setup["project"]["id"]
    t = _traceable_task(active_setup)
    assert t["status"] == "awaiting_approval"
    alt = ps.create_path(pid, "admin", name="Alt2", rationale="r")
    ps.change_path_status(pid, alt["id"], "approved", "admin", "ok")
    ps.select_path(pid, alt["id"], "admin", "Switch strategy")
    assert ts.approve_task(t["id"]) is False  # no longer supports the selected Path
    assert ts.get_task_by_id(t["id"])["status"] == "awaiting_approval"


def test_task_in_inactive_project_cannot_queue(active_setup):
    pid = active_setup["project"]["id"]
    ps.change_project_status(pid, "paused", "admin", "hold")
    t = _traceable_task(active_setup)
    assert t["status"] == "draft"


def test_inconsistent_task_references_rejected_by_database(active_setup):
    other = ps.create_project(_name("Other"), owner="admin")
    with pytest.raises(sqlite3.IntegrityError):
        _traceable_task(active_setup, project_id=other["id"])  # path belongs to a different project
    pid = active_setup["project"]["id"]
    alt = ps.create_path(pid, "admin", name="Alt3", rationale="r")
    alt_stage = ps.create_stage(pid, alt["id"], "admin", name="S")
    with pytest.raises(sqlite3.IntegrityError):
        _traceable_task(active_setup, stage_id=alt_stage["id"])  # stage of another Path


def test_draft_submission_moves_to_awaiting(active_setup):
    t = _traceable_task(active_setup, purpose="")
    assert t["status"] == "draft"
    conn = get_db_connection()
    conn.execute("UPDATE tasks SET purpose = 'Now defined' WHERE id = ?", (t["id"],))
    conn.commit()
    conn.close()
    assert ts.submit_task_for_approval(t["id"]) == []
    assert ts.get_task_by_id(t["id"])["status"] == "awaiting_approval"


def test_project_summary_uses_persisted_counts(active_setup):
    _traceable_task(active_setup)
    _traceable_task(active_setup, purpose="")
    summary = ps.project_summary(active_setup["project"])
    assert summary["pending_approvals"] == 1
    assert summary["draft_tasks"] == 1
    assert summary["selected_path"]["id"] == active_setup["path"]["id"]
    assert summary["current_stage"]["id"] == active_setup["s1"]["id"]
    assert "progress" not in summary  # no fabricated percentages
