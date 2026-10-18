"""Tests verifying deliverable artifact validation across task retries, replans, and re-executions."""
import json
import pytest
from pathlib import Path
from app.services.orchestration import artifact_manager, engine, states
from app.services import projects as proj_svc
from app.services import deliverables as D
from app.database import db_transaction, get_db_connection
from app.services.orchestration.common import new_id, now_iso


def test_artifact_manager_updates_existing_storage_path():
    """When a file already exists at storage_path, write_controller_artifact updates orch_task_id."""
    proj = proj_svc.create_project(
        name="Artifact Project",
        description="Testing artifact updates",
        objective="Verify storage path update",
        owner="admin"
    )
    pid = proj["id"]
    run_id = f"run_{new_id('t')}"
    rev_id = f"rev_{new_id('t')}"
    task1_id = f"ot_{new_id('t')}"
    task2_id = f"ot_{new_id('t')}"

    with db_transaction() as c:
        c.execute(
            """INSERT INTO orchestration_runs (id, project_id, state, autonomy_level, intake_json, analysis_json, created_by, created_at, updated_at)
               VALUES (?, ?, 'RUNNING', 3, '{}', '{}', 'admin', ?, ?)""",
            (run_id, pid, now_iso(), now_iso())
        )
        c.execute(
            """INSERT INTO orchestration_plan_revisions (id, run_id, revision_number, status, source, plan_json, created_by, created_at)
               VALUES (?, ?, 1, 'approved', 'model', '{}', 'admin', ?)""",
            (rev_id, run_id, now_iso())
        )
        c.execute(
            """INSERT INTO orchestration_tasks (id, run_id, revision_id, plan_key, title, agent_role, action_json, state, created_at, updated_at)
               VALUES (?, ?, ?, 't1', 'T1', 'worker', '{}', 'pending', ?, ?)""",
            (task1_id, run_id, rev_id, now_iso(), now_iso())
        )
        c.execute(
            """INSERT INTO orchestration_tasks (id, run_id, revision_id, plan_key, title, agent_role, action_json, state, created_at, updated_at)
               VALUES (?, ?, ?, 't2', 'T2', 'worker', '{}', 'pending', ?, ?)""",
            (task2_id, run_id, rev_id, now_iso(), now_iso())
        )

    run = {"id": run_id, "project_id": pid}

    # First write
    aid1 = artifact_manager.write_controller_artifact(run, task1_id, "report.md", b"# Original Content")
    meta1 = artifact_manager.resolve_for_download(pid, aid1)
    assert meta1["orch_task_id"] == task1_id
    assert meta1["sha256"] != ""

    # Second write by new task on retry/replan
    aid2 = artifact_manager.write_controller_artifact(run, task2_id, "report.md", b"# Updated Content")
    assert aid2 == aid1  # Same artifact ID preserved

    meta2 = artifact_manager.resolve_for_download(pid, aid2)
    assert meta2["orch_task_id"] == task2_id
    assert meta2["sha256"] != meta1["sha256"]


def test_evaluate_deliverables_matches_explicit_artifacts_and_revisions():
    """Deliverable evaluation matches artifacts listed in accepted_result_json even across revisions."""
    proj = proj_svc.create_project(
        name="Test Deliverable Project",
        description="Testing deliverable validation",
        objective="Verify deliverables",
        owner="admin"
    )
    pid = proj["id"]
    run_id = f"run_{new_id('t')}"
    rev1_id = f"rev_{new_id('t')}"
    rev2_id = f"rev_{new_id('t')}"
    ot1_id = f"ot_{new_id('t')}"
    ot2_id = f"ot_{new_id('t')}"

    # Create deliverable: project_id, name, deliverable_type, created_by
    deliv = D.create_deliverable(pid, "Final Report", "report", "admin")
    did = deliv["id"]

    plan = {
        "deliverables": [
            {
                "key": "d1",
                "name": "Final Report",
                "produced_by": "t1",
                "stage": "s1",
                "terminal": True,
                "type": "report"
            }
        ],
        "tasks": [
            {
                "key": "t1",
                "name": "Assemble Report",
                "type": "controller_step"
            }
        ]
    }

    with db_transaction() as c:
        c.execute(
            """INSERT INTO orchestration_runs (id, project_id, state, autonomy_level, intake_json, analysis_json, created_by, created_at, updated_at)
               VALUES (?, ?, 'EVALUATING', 3, '{}', '{}', 'admin', ?, ?)""",
            (run_id, pid, now_iso(), now_iso())
        )
        c.execute(
            """INSERT INTO orchestration_plan_revisions (id, run_id, revision_number, status, source, plan_json, created_by, created_at)
               VALUES (?, ?, 1, 'superseded', 'model', ?, 'admin', ?)""",
            (rev1_id, run_id, json.dumps(plan), now_iso())
        )
        c.execute(
            """INSERT INTO orchestration_plan_revisions (id, run_id, revision_number, status, source, plan_json, created_by, created_at)
               VALUES (?, ?, 2, 'approved', 'model', ?, 'admin', ?)""",
            (rev2_id, run_id, json.dumps(plan), now_iso())
        )
        c.execute(
            "UPDATE orchestration_runs SET analysis_json = ?, current_revision_id = ? WHERE id = ?",
            (json.dumps({"deliverable_map": {"d1": did}}), rev2_id, run_id)
        )
        c.execute(
            """INSERT INTO orchestration_tasks (id, run_id, revision_id, plan_key, title, agent_role, action_json, state, accepted_result_json, created_at, updated_at)
               VALUES (?, ?, ?, 't1', 'Assemble', 'worker', '{}', 'superseded', '{}', ?, ?)""",
            (ot1_id, run_id, rev1_id, now_iso(), now_iso())
        )
        c.execute(
            """INSERT INTO orchestration_tasks (id, run_id, revision_id, plan_key, title, agent_role, action_json, state, accepted_result_json, created_at, updated_at)
               VALUES (?, ?, ?, 't1', 'Assemble', 'worker', '{}', 'pending', '{}', ?, ?)""",
            (ot2_id, run_id, rev2_id, now_iso(), now_iso())
        )

    # Superseded task ot1 wrote artifact
    run_dict = {"id": run_id, "project_id": pid}
    aid = artifact_manager.write_controller_artifact(run_dict, ot1_id, "final_report.md", b"# Report Content")
    artifact_manager.set_validation(aid, "valid", "Passed validation")

    # Active task ot2 accepted with explicit artifact reference
    with db_transaction() as c:
        c.execute(
            """UPDATE orchestration_tasks
               SET state = 'accepted', accepted_result_json = ?
               WHERE id = ?""",
            (json.dumps({"artifacts": [aid], "kind": "controller_generated"}), ot2_id)
        )

    # Run evaluation
    run_row = {"id": run_id, "project_id": pid, "autonomy_level": 3, "analysis_json": json.dumps({"deliverable_map": {"d1": did}})}
    engine._evaluate_deliverables(run_row)

    # Deliverable should now be accepted!
    updated_d = D.get_deliverable(did)
    assert updated_d["status"] == "accepted"
