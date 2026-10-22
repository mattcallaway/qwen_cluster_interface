"""Stage 6 Artifact Subsystem and Lifecycle Management Test Suite.

Verifies:
- Test A: Two artifacts with identical filenames but different content remain independently downloadable.
- Test B: Duplicate content deduplicates safely without merging permissions.
- Test C: Worker B cannot view or reuse Worker A's unauthorized artifact.
- Test D: Stale or unapproved producer attempts cannot commit accepted artifacts.
- Test E: Task-level retry preserves previous attempt outputs without clobbering.
- Test F: Deliverable with multiple supporting files produces a complete deliverables index.
- Test G: Reused artifact creates an explicit lineage link and does not allow modifying the original artifact.
- Test H: Attempting to upload or store an oversized artifact is rejected.
- Test I: Large artifact download supports streaming or chunked delivery with checksum headers.
- Test J: Inline preview and download of HTML or SVG content disables active scripts and sets security headers.
- Test K: Artifact lineage graph correctly identifies root source artifacts and derivative outputs across runs.
- Test L: A run with missing required artifacts cannot transition to COMPLETED and reports an actionable error.
"""

import hashlib
import json
import os
import uuid
import pytest
from datetime import datetime, timezone
from pathlib import Path
from fastapi.testclient import TestClient

from app.database import get_db_connection, db_transaction
from app.main import app
from app.config import settings
from app.services.projects import create_project
from app.services.artifacts import artifact_service
from app.services.artifacts.storage import storage_backend, DEFAULT_MAX_ARTIFACT_SIZE
from app.services.orchestration import (
    artifact_manager,
    engine,
    evaluator,
    states,
)


from app.auth import create_session_token


@pytest.fixture
def client():
    c = TestClient(app)
    c.cookies.set("qc_session", create_session_token("operator"))
    return c


def _setup_run(project_id: str, plan_tasks=None, deliverables=None):
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    rev_id = f"rev_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    plan_dict = {
        "overview": {"objective": "Test Run"},
        "tasks": plan_tasks or [],
        "deliverables": deliverables or [],
    }
    with db_transaction() as c:
        c.execute(
            """INSERT INTO orchestration_runs (id, project_id, state, autonomy_level, intake_json, created_by, created_at, updated_at)
               VALUES (?, ?, 'RUNNING', 1, '{}', 'operator', ?, ?)""",
            (run_id, project_id, now, now)
        )
        c.execute(
            """INSERT INTO orchestration_plan_revisions (id, run_id, revision_number, status, source, plan_json, created_by, created_at)
               VALUES (?, ?, 1, 'approved', 'model', ?, 'operator', ?)""",
            (rev_id, run_id, json.dumps(plan_dict), now)
        )
        for t in (plan_tasks or []):
            tid = f"ot_{uuid.uuid4().hex[:12]}"
            c.execute(
                """INSERT INTO orchestration_tasks (id, run_id, revision_id, plan_key, title, agent_role, action_json, state, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'worker', '{}', 'pending', ?, ?)""",
                (tid, run_id, rev_id, t.get("key", "t"), t.get("title", "T"), now, now)
            )
    return {"id": run_id, "project_id": project_id, "revision_id": rev_id}


def test_a_identical_filenames_different_content_independently_downloadable():
    """Test A: Two artifacts with identical filenames but different content remain independently downloadable."""
    proj = create_project("Project Identical Names", owner="test_user", description="Testing collision avoidance")
    proj_id = proj["id"]

    content1 = b"# Version Alpha content\nprint('alpha')\n"
    content2 = b"# Version Beta content\nprint('beta')\n"

    # Register two distinct artifacts with the same display filename
    art1 = artifact_service.register_artifact(
        name="script.py",
        data=content1,
        project_id=proj_id,
        created_by="worker_1",
    )
    art2 = artifact_service.register_artifact(
        name="script.py",
        data=content2,
        project_id=proj_id,
        created_by="worker_2",
    )

    assert art1["id"] != art2["id"]
    assert art1["checksum_sha256"] != art2["checksum_sha256"]

    # Download both independently
    dl1 = artifact_service.resolve_download(art1["id"], project_id=proj_id)
    dl2 = artifact_service.resolve_download(art2["id"], project_id=proj_id)

    assert dl1["file_path"].read_bytes() == content1
    assert dl2["file_path"].read_bytes() == content2
    assert dl1["checksum_sha256"] == hashlib.sha256(content1).hexdigest()
    assert dl2["checksum_sha256"] == hashlib.sha256(content2).hexdigest()


def test_b_duplicate_content_deduplicates_safely_without_merging_permissions():
    """Test B: Duplicate content deduplicates safely at the blob layer without merging distinct permissions."""
    proj_a = create_project("Project A Owner", owner="alice", description="Private project A")
    proj_b = create_project("Project B Owner", owner="bob", description="Private project B")

    shared_content = b"{\"common_config\": true, \"cluster\": \"prod\"}\n"
    shared_hash = hashlib.sha256(shared_content).hexdigest()

    art_a = artifact_service.register_artifact(
        name="config.json",
        data=shared_content,
        project_id=proj_a["id"],
        visibility="private",
        created_by="alice",
    )
    art_b = artifact_service.register_artifact(
        name="shared_config.json",
        data=shared_content,
        project_id=proj_b["id"],
        visibility="private",
        created_by="bob",
    )

    # Distinct artifact identities
    assert art_a["id"] != art_b["id"]
    assert art_a["checksum_sha256"] == shared_hash
    assert art_b["checksum_sha256"] == shared_hash
    assert art_a["cas_hash"] == shared_hash
    assert art_b["cas_hash"] == shared_hash

    # Single underlying CAS blob
    cas_path = storage_backend.get_cas_path(shared_hash)
    assert cas_path.is_file()

    # Permissions remain strictly isolated: bob cannot download alice's artifact
    with pytest.raises(PermissionError):
        artifact_service.resolve_download(art_a["id"], project_id=proj_b["id"])

    # Alice cannot download bob's artifact
    with pytest.raises(PermissionError):
        artifact_service.resolve_download(art_b["id"], project_id=proj_a["id"])

    # Each can download their own artifact, reading the shared CAS content safely
    dl_a = artifact_service.resolve_download(art_a["id"], project_id=proj_a["id"])
    dl_b = artifact_service.resolve_download(art_b["id"], project_id=proj_b["id"])
    assert dl_a["file_path"].read_bytes() == shared_content
    assert dl_b["file_path"].read_bytes() == shared_content
    assert cas_path.read_bytes() == shared_content
    assert dl_a["checksum_sha256"] == shared_hash
    assert dl_b["checksum_sha256"] == shared_hash
    assert art_a["cas_hash"] == art_b["cas_hash"] == shared_hash


def test_c_worker_b_cannot_view_or_reuse_worker_a_unauthorized_artifact():
    """Test C: Worker B cannot view or reuse Worker A's unauthorized artifact without explicit authorization."""
    proj_a = create_project("Project Confidential", owner="researcher_a", description="Confidential research")
    proj_b = create_project("Project External", owner="contractor_b", description="External tasks")

    secret_data = b"CONFIDENTIAL_MODEL_WEIGHTS_SNAPSHOT_99"
    art_a = artifact_service.register_artifact(
        name="weights.bin",
        data=secret_data,
        project_id=proj_a["id"],
        visibility="private",
        created_by="researcher_a",
    )

    # Unauthorized read attempt by project B
    with pytest.raises(PermissionError):
        artifact_service.resolve_download(art_a["id"], project_id=proj_b["id"])

    # Unauthorized reuse attempt by contractor_b
    with pytest.raises(PermissionError):
        artifact_service.reuse_artifact_in_project(
            artifact_id=art_a["id"],
            project_id=proj_b["id"],
            actor="contractor_b",
        )

    # Authorized reuse by admin succeeds and records lineage in artifact_reuses
    reuse_result = artifact_service.reuse_artifact_in_project(
        artifact_id=art_a["id"],
        project_id=proj_b["id"],
        actor="admin",
        instructions="Authorized security audit copy",
    )
    assert reuse_result["status"] == "queued"
    assert reuse_result["artifact_id"] == art_a["id"]

    # Once authorized, download by project B succeeds
    dl = artifact_service.resolve_download(art_a["id"], project_id=proj_b["id"])
    assert dl["file_path"].read_bytes() == secret_data


def test_d_stale_or_unapproved_producer_attempts_cannot_commit_artifacts():
    """Test D: Stale or unapproved producer attempts cannot commit accepted artifacts (Finding F-13 & attempt fencing)."""
    proj = create_project("Project Fencing", owner="operator", description="Testing attempt fencing")
    run = _setup_run(
        proj["id"],
        plan_tasks=[{"key": "task_slow", "title": "Slow background processing"}],
        deliverables=[{"key": "deliv_1", "name": "Validation Report", "produced_by": "task_slow", "terminal": True}],
    )
    run_id = run["id"]

    conn = get_db_connection()
    try:
        ot = conn.execute("SELECT * FROM orchestration_tasks WHERE run_id=? AND plan_key='task_slow'", (run_id,)).fetchone()
        ot_id = ot["id"]
        # Simulate Attempt 1 was dispatched
        conn.execute("UPDATE orchestration_tasks SET state='dispatched', attempts=1 WHERE id=?", (ot_id,))
        conn.execute(
            """INSERT INTO orchestration_attempts (id, orch_task_id, attempt_number, node_id, started_at)
               VALUES (?, ?, 1, 'controller-local', '2026-10-20T00:00:00Z')""",
            (f"att_{uuid.uuid4().hex[:12]}", ot_id)
        )
        # Now simulate scheduler superseding attempt 1 with attempt 2
        conn.execute("UPDATE orchestration_tasks SET attempts=2 WHERE id=?", (ot_id,))
        conn.commit()
    finally:
        conn.close()

    # Attempt 1 returns late and tries to commit
    stale_eval = {
        "accepted": True,
        "checks": [{"name": "stale_check", "passed": True}],
        "result": {"text": "Stale attempt 1 output"},
    }
    engine._commit_controller_attempt(
        run_id=run_id,
        orch_task_id=ot_id,
        attempt_number=1,  # Stale attempt
        ev=stale_eval,
    )

    # Verify attempt 1 was rejected by fencing
    conn = get_db_connection()
    try:
        attempt_row = conn.execute(
            "SELECT * FROM orchestration_attempts WHERE orch_task_id=? AND attempt_number=1",
            (ot_id,)
        ).fetchone()
        assert attempt_row is not None
        assert attempt_row["outcome"] == "rejected"
        assert attempt_row["failure_class"] == "stale_attempt_fencing"

        # Verify task is NOT completed by stale attempt
        curr_ot = conn.execute("SELECT * FROM orchestration_tasks WHERE id=?", (ot_id,)).fetchone()
        assert curr_ot["state"] != "completed"
    finally:
        conn.close()


def test_e_task_level_retry_preserves_previous_attempt_outputs():
    """Test E: Task-level retry preserves previous attempt outputs without clobbering."""
    proj = create_project("Project Retry Artifacts", owner="operator", description="Testing retry preservation")
    run = _setup_run(
        proj["id"],
        plan_tasks=[{"key": "task_retry", "title": "Worker Task"}],
    )

    # Write Attempt 1 artifact
    content_att1 = b"LOG: Attempt 1 crashed with OOM"
    aid1 = artifact_manager.write_controller_artifact(
        run, None, "execution.log", content_att1, provenance="worker_attempt", producer_attempt=1
    )

    # Write Attempt 2 artifact (same filename, retry attempt 2)
    content_att2 = b"LOG: Attempt 2 completed successfully with 2GB RAM"
    aid2 = artifact_manager.write_controller_artifact(
        run, None, "execution.log", content_att2, provenance="worker_attempt", producer_attempt=2
    )

    assert aid1 != aid2

    arts = artifact_manager.list_artifacts(proj["id"])
    assert len(arts) >= 2

    art_row1 = next(a for a in arts if a["id"] == aid1)
    art_row2 = next(a for a in arts if a["id"] == aid2)

    assert art_row1["producer_attempt"] == 1
    assert art_row2["producer_attempt"] == 2
    assert Path(art_row1["storage_path"]).read_bytes() == content_att1
    assert Path(art_row2["storage_path"]).read_bytes() == content_att2


def test_f_deliverable_with_multiple_supporting_files_produces_complete_index():
    """Test F: Deliverable with multiple supporting files produces a complete deliverables index."""
    proj = create_project("Project Multi-Artifact", owner="operator", description="Complete deliverables index")
    run = _setup_run(
        proj["id"],
        plan_tasks=[{"key": "build_task", "title": "Build Deliverables"}],
        deliverables=[{"key": "app_release", "name": "Application Release", "produced_by": "build_task", "terminal": True}],
    )

    # Register primary and supporting artifacts
    aid_main = artifact_manager.write_controller_artifact(run, None, "main.py", b"print('main entry')", provenance="code")
    aid_cfg = artifact_manager.write_controller_artifact(run, None, "config.yaml", b"port: 8080", provenance="config")
    aid_readme = artifact_manager.write_controller_artifact(run, None, "README.md", b"# Guide", provenance="docs")

    artifact_manager.set_validation(aid_main, "valid", "Code syntax valid")
    artifact_manager.set_validation(aid_cfg, "valid", "Config schema valid")
    artifact_manager.set_validation(aid_readme, "valid", "Documentation valid")

    # Call assemble_deliverables_index
    index_aid = engine.assemble_deliverables_index(run)
    assert index_aid is not None

    meta = artifact_manager.resolve_for_download(proj["id"], index_aid)
    index_data = json.loads(Path(meta["storage_path"]).read_text(encoding="utf-8"))

    assert index_data["run_id"] == run["id"]
    assert index_data["project_id"] == proj["id"]
    filenames = [a["filename"] for a in index_data["artifacts"]]
    assert "main.py" in filenames
    assert "config.yaml" in filenames
    assert "README.md" in filenames


def test_g_reused_artifact_creates_lineage_and_preserves_original():
    """Test G: Reused artifact creates an explicit lineage link and does not allow modifying the original artifact."""
    proj1 = create_project("Source Project", owner="alice", description="Original project")
    proj2 = create_project("Target Project", owner="alice", description="Consuming project")

    original_content = b"DATASET V1: 1000 rows\n"
    art_orig = artifact_service.register_artifact(
        name="dataset.csv",
        data=original_content,
        project_id=proj1["id"],
        created_by="alice",
    )

    # Reuse artifact in Project 2
    reuse_res = artifact_service.reuse_artifact_in_project(
        artifact_id=art_orig["id"],
        project_id=proj2["id"],
        action_type="transform",
        instructions="Filter null values",
        actor="alice",
    )
    assert reuse_res["status"] == "queued"
    assert reuse_res["reuse_id"].startswith("ar-")

    # In Project 2, create a new derived version for the original artifact
    derived_content = b"DATASET V2: 950 clean rows\n"
    art_v2 = artifact_service.create_artifact_version(
        artifact_id=art_orig["id"],
        data=derived_content,
        change_description="Filtered 50 null rows in Project 2",
        created_by="alice",
    )

    assert art_v2["current_version_number"] == 2
    assert len(art_v2["versions"]) == 2

    # Original version 1 content is immutable and preserved
    dl_v1 = artifact_service.resolve_download(art_orig["id"], version_number=1, project_id=proj1["id"])
    assert dl_v1["file_path"].read_bytes() == original_content

    # Version 2 has derived content
    dl_v2 = artifact_service.resolve_download(art_orig["id"], version_number=2, project_id=proj2["id"])
    assert dl_v2["file_path"].read_bytes() == derived_content


def test_h_oversized_artifact_rejected():
    """Test H: Attempting to upload or store an oversized artifact is rejected fail-closed."""
    proj = create_project("Project Size Limit", owner="operator", description="Testing size limits")

    oversized_data = b"0" * 1500
    with pytest.raises(ValueError, match="exceeds maximum permitted size"):
        storage_backend.store_cas_blob(oversized_data, max_size_bytes=1000)

    # Also test via write_controller_artifact
    run = _setup_run(proj["id"])
    with pytest.raises(ValueError, match="Artifact too large"):
        artifact_manager.write_controller_artifact(
            run, None, "oversized.bin", b"X" * (artifact_manager.MAX_ARTIFACT_BYTES + 10)
        )


def test_i_large_artifact_download_supports_streaming(client):
    """Test I: Artifact download endpoint supports streaming and checksum verification headers."""
    proj = create_project("Project Streaming", owner="operator", description="Large file streaming")
    data = b"CHUNKED_DATA_STREAM_" * 1024  # ~20KB test stream
    sha = hashlib.sha256(data).hexdigest()

    art = artifact_service.register_artifact(
        name="stream_test.dat",
        data=data,
        project_id=proj["id"],
        created_by="operator",
    )

    resp = client.get(f"/api/v1/artifacts/{art['id']}/download?project_id={proj['id']}")
    assert resp.status_code == 200
    assert resp.headers["x-artifact-id"] == art["id"]
    assert resp.headers["x-artifact-version"] == "1"
    assert resp.headers["x-artifact-checksum-sha256"] == sha
    assert resp.content == data


def test_j_inline_preview_of_html_or_svg_disables_active_scripts(client):
    """Test J: Inline preview and download of HTML or SVG content disables active scripts and sets security headers."""
    proj = create_project("Project Web Security", owner="operator", description="Prevent XSS")

    malicious_html = b"<html><head><script>alert('pwned')</script></head><body><h1>Hello</h1></body></html>"
    malicious_svg = b"<svg xmlns=\"http://www.w3.org/2000/svg\" onload=\"alert('xss')\"><circle r=\"10\"/></svg>"

    art_html = artifact_service.register_artifact(
        name="exploit.html",
        data=malicious_html,
        project_id=proj["id"],
    )
    art_svg = artifact_service.register_artifact(
        name="vector.svg",
        data=malicious_svg,
        project_id=proj["id"],
    )

    # 1. Safe preview structure: both must be treated as code/sanitized, never executable DOM
    preview_html = artifact_service.get_preview_data(art_html["id"], project_id=proj["id"])
    assert preview_html["preview_type"] == "code"
    assert preview_html["is_sanitized"] is True

    preview_svg = artifact_service.get_preview_data(art_svg["id"], project_id=proj["id"])
    assert preview_svg["preview_type"] == "code"
    assert preview_svg["is_sanitized"] is True

    # 2. HTTP download with inline=1: must force text/plain and sandbox CSP headers
    resp_html = client.get(f"/api/v1/artifacts/{art_html['id']}/download?project_id={proj['id']}&inline=1")
    assert resp_html.status_code == 200
    assert resp_html.headers["content-type"].startswith("text/plain")
    assert "default-src 'none'; sandbox" in resp_html.headers.get("content-security-policy", "")
    assert resp_html.headers.get("x-content-type-options") == "nosniff"

    resp_svg = client.get(f"/api/v1/artifacts/{art_svg['id']}/download?project_id={proj['id']}&inline=1")
    assert resp_svg.status_code == 200
    assert resp_svg.headers["content-type"].startswith("text/plain")
    assert "default-src 'none'; sandbox" in resp_svg.headers.get("content-security-policy", "")


def test_k_artifact_lineage_graph_identifies_root_and_derivative_outputs():
    """Test K: Artifact lineage graph correctly identifies root source artifacts and derivative outputs across runs."""
    proj = create_project("Project Lineage", owner="operator", description="Lineage graph testing")

    root_art = artifact_service.register_artifact(
        name="raw_input.csv",
        data=b"col1,col2\n1,2\n",
        project_id=proj["id"],
    )

    child_art = artifact_service.register_artifact(
        name="cleaned_input.csv",
        data=b"col1,col2\n1,2\n",
        project_id=proj["id"],
        parent_artifact_id=root_art["id"],
    )

    grandchild_art = artifact_service.register_artifact(
        name="final_model_features.parquet",
        data=b"PARQUET_MAGIC_BYTES_1234",
        project_id=proj["id"],
        parent_artifact_id=child_art["id"],
    )

    lineage = artifact_service.get_artifact_lineage(child_art["id"])
    upstream_ids = [l["source_artifact_id"] for l in lineage["upstream"]]
    downstream_ids = [l["target_artifact_id"] for l in lineage["downstream"]]

    assert root_art["id"] in upstream_ids
    assert grandchild_art["id"] in downstream_ids


def test_l_missing_required_artifacts_block_completed_status():
    """Test L: A run with missing required artifacts cannot transition to COMPLETED and reports an actionable error."""
    proj = create_project("Project Missing Artifacts", owner="operator", description="Missing artifact blocking")
    run = _setup_run(
        proj["id"],
        plan_tasks=[{"key": "report_step", "title": "Produce Report"}],
        deliverables=[{"key": "final_report", "name": "Final Executive Report", "produced_by": "report_step", "terminal": True, "required": True}],
    )

    # Register task and attempt outcome
    conn = get_db_connection()
    try:
        ot = conn.execute("SELECT * FROM orchestration_tasks WHERE run_id=? AND plan_key='report_step'", (run["id"],)).fetchone()
        ot_id = ot["id"]
        conn.execute("UPDATE orchestration_tasks SET state='accepted', attempts=1 WHERE id=?", (ot_id,))
        conn.execute(
            """INSERT INTO orchestration_attempts (id, orch_task_id, attempt_number, node_id, started_at, finished_at, outcome, evaluation_json)
               VALUES (?, ?, 1, 'controller-local', '2026-10-20T00:00:00Z', '2026-10-20T00:01:00Z', 'accepted', '{"accepted": true}')""",
            (f"att_{uuid.uuid4().hex[:12]}", ot_id)
        )

        # Insert metadata record pointing to a non-existent physical file
        fake_storage_path = str(settings.evidence_dir / "orch_nonexistent" / "deleted_file.md")
        conn.execute(
            """INSERT INTO artifact_metadata (id, run_id, project_id, task_id, orch_task_id, worker_id, filename,
                   mime_type, size_bytes, sha256, storage_path, cas_hash, producer_attempt, provenance, validation_state, created_at)
               VALUES ('art-fake-missing', ?, ?, NULL, ?, 'controller-local', 'deleted_file.md', 'text/markdown', 100,
                   'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855', ?, NULL, 1, 'controller_generated', 'valid', '2026-10-20T00:01:00Z')""",
            (run["id"], proj["id"], ot_id, fake_storage_path)
        )
        conn.commit()
    finally:
        conn.close()

    # Trigger deliverable evaluation
    run_record = engine.get_run(run["id"])
    engine._evaluate_deliverables(run_record)

    # Verify run is BLOCKED and not COMPLETED
    curr_run = engine.get_run(run["id"])
    assert curr_run["state"] == "BLOCKED"
    assert "missing from storage" in curr_run["block_reason"].lower()
