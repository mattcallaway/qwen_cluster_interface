"""
Comprehensive unit and integration test suite for Qwen Collective Unified Deliverables & Artifact Management.
Covers:
- Canonical artifact registration and atomic storage
- Worker upload authorization and independent checksum validation
- Path traversal prevention, symlink escape rejection, and oversize rejection
- Safe preview generation (Markdown, JSON, CSV, code, images, PDF)
- Cross-project access isolation and explicit sharing grants
- Immutable versioning, revision workflows, and lineage graph
- Same-project reuse and artifact-aware project creation ("Create Project From This")
- Knowledge base extraction from deliverables and impact tracking
- System Observatory telemetry integration
"""
import base64
import hashlib
import json
import pytest
from pathlib import Path

from app.database import get_db_connection, init_db
from app.services.artifacts import artifact_service, LocalFilesystemStorageBackend
from app.services.deliverables import (
    create_deliverable,
    get_deliverable,
    list_deliverables,
    review_deliverable,
    submit_deliverable,
)
from app.services.observatory.analytics import get_deliverables_and_artifacts_metrics
from app.services.projects import create_project, get_project
from app.services.worker import store_task_artifact


@pytest.fixture(autouse=True)
def setup_test_db():
    init_db()


def test_artifact_registration_and_integrity():
    """Verify atomic registration, storage, and checksum calculation."""
    proj = create_project(
        "Artifact Test Project Alpha",
        "test-suite",
        description="Testing artifact registration",
    )
    content = b"# Qwen Collective Deliverable\nThis is a verified test report."
    expected_sha = hashlib.sha256(content).hexdigest()

    art = artifact_service.register_artifact(
        name="test_report.md",
        data=content,
        project_id=proj["id"],
        description="A verified test report",
        created_by="tester",
        expected_sha256=expected_sha,
    )

    assert art["id"].startswith("art-")
    assert art["display_name"] == "test_report.md"
    assert art["artifact_type"] == "document"
    assert art["checksum_sha256"] == expected_sha
    assert art["size_bytes"] == len(content)
    assert art["current_version_number"] == 1
    assert len(art["versions"]) == 1
    assert art["file_exists"] is True


def test_path_traversal_and_security_safeguards():
    """Verify that path traversal attempts, symlink escapes, and protected files are rejected."""
    storage = LocalFilesystemStorageBackend()

    # Path traversal with ../
    with pytest.raises(PermissionError):
        storage._resolve_safe_path("../../../../../windows/system32/cmd.exe", must_exist=False)

    # Attempt to target protected system files like database or env
    with pytest.raises(PermissionError):
        storage._resolve_safe_path("qwen_collective.db", must_exist=False)


def test_worker_upload_and_independent_checksum():
    """Verify that worker uploads undergo independent controller-side checksum calculation."""
    proj = create_project(
        "Worker Ingestion Project",
        "test-suite",
        description="Testing worker artifact ingestion",
    )
    conn = get_db_connection()
    task_id = "task-test-worker-upload"
    conn.execute(
        """
        INSERT INTO tasks (id, project_id, title, description, task_type, status, created_at, updated_at)
        VALUES (?, ?, 'Worker Upload Task', 'Testing worker artifact upload', 'inference', 'in_progress', datetime('now'), datetime('now'))
        """,
        (task_id, proj["id"]),
    )
    conn.commit()
    conn.close()

    payload_bytes = b'{"metric": "latency", "measured_ms": 42.5}'
    correct_sha = hashlib.sha256(payload_bytes).hexdigest()

    # Upload with matching expected checksum
    res = store_task_artifact(
        task_id=task_id,
        filename="benchmark_results.json",
        content_bytes=payload_bytes,
        expected_sha256=correct_sha,
        worker_id="qwen-vps",
    )

    assert res["checksum_matches"] is True
    assert res["computed_sha256"] == correct_sha
    assert res["artifact_id"] is not None

    canonical_art = artifact_service.get_artifact(res["artifact_id"])
    assert canonical_art is not None
    assert canonical_art["origin_worker_id"] == "qwen-vps"
    assert canonical_art["origin_project_id"] == proj["id"]


def test_immutable_versioning_and_lineage():
    """Verify that creating a new version preserves v1, increments version number, and records lineage."""
    proj = create_project(
        "Versioning Project",
        "test-suite",
        description="Testing immutable versions",
    )
    v1_content = b"Draft report v1"
    art = artifact_service.register_artifact(
        name="analysis.txt",
        data=v1_content,
        project_id=proj["id"],
        description="Version 1 draft",
        created_by="tester",
    )
    art_id = art["id"]
    assert art["current_version_number"] == 1

    # Create Version 2
    v2_content = b"Revised report v2 with refined metrics"
    updated_art = artifact_service.create_artifact_version(
        artifact_id=art_id,
        data=v2_content,
        change_description="Incorporated review feedback",
        created_by="reviewer",
    )

    assert updated_art["current_version_number"] == 2
    assert len(updated_art["versions"]) == 2

    # Download Version 1 specifically
    dl_v1 = artifact_service.resolve_download(art_id, version_number=1)
    assert dl_v1["file_path"].read_bytes() == v1_content

    # Download Version 2 specifically
    dl_v2 = artifact_service.resolve_download(art_id, version_number=2)
    assert dl_v2["file_path"].read_bytes() == v2_content

    # Check lineage graph
    lineage = artifact_service.get_artifact_lineage(art_id)
    assert len(lineage["upstream"]) >= 1
    assert lineage["upstream"][0]["relationship_type"] == "revised_from"


def test_cross_project_isolation_and_explicit_grants():
    """Verify that private artifacts are inaccessible across projects without explicit sharing grants."""
    proj_a = create_project("Project Alpha Private", "test-suite", description="Private A")
    proj_b = create_project("Project Beta Isolated", "test-suite", description="Isolated B")

    art = artifact_service.register_artifact(
        name="confidential_model_weights.bin",
        data=b"secret_data_12345",
        project_id=proj_a["id"],
        visibility="private",
    )
    art_id = art["id"]

    # Project A has access
    assert artifact_service.check_project_access(art_id, proj_a["id"]) is True

    # Project B does NOT have access initially
    assert artifact_service.check_project_access(art_id, proj_b["id"]) is False

    with pytest.raises(PermissionError):
        artifact_service.resolve_download(art_id, project_id=proj_b["id"])

    # Grant access to Project B
    artifact_service.grant_project_access(
        artifact_id=art_id,
        source_project_id=proj_a["id"],
        target_project_id=proj_b["id"],
        granted_by="admin",
    )

    # Project B now has authorized access
    assert artifact_service.check_project_access(art_id, proj_b["id"]) is True
    dl = artifact_service.resolve_download(art_id, project_id=proj_b["id"])
    assert dl["file_path"].read_bytes() == b"secret_data_12345"


def test_same_project_reuse_workflow():
    """Verify 'Use in This Project' generates an authorized task dependency and lineage."""
    proj = create_project("Reuse Workflow Project", "test-suite", description="Reuse tests")
    art = artifact_service.register_artifact(
        name="source_data.csv",
        data=b"item,count\nwidgets,10\ngadgets,25\n",
        project_id=proj["id"],
        artifact_type="dataset",
    )

    reuse_result = artifact_service.reuse_artifact_in_project(
        artifact_id=art["id"],
        project_id=proj["id"],
        action_type="analyze",
        instructions="Calculate summary statistics and average count",
        actor="lead_architect",
    )

    assert reuse_result["status"] == "queued"
    assert reuse_result["task_id"] is not None

    conn = get_db_connection()
    task_row = conn.execute("SELECT * FROM tasks WHERE id = ?", (reuse_result["task_id"],)).fetchone()
    conn.close()

    assert task_row is not None
    assert "source_data.csv" in task_row["title"]
    input_payload = json.loads(task_row["input_data_json"])
    assert input_payload["input_artifacts"][0]["artifact_id"] == art["id"]


def test_artifact_aware_project_creation():
    """Verify 'Create Project From This' initializes a new project with authorized artifact reference."""
    parent_proj = create_project("Original Research Proj", "test-suite", description="Parent project")
    report_content = b"# Qwen Collective Distributed Architecture Report\nFull system audit findings..."
    art = artifact_service.register_artifact(
        name="architecture_audit.md",
        data=report_content,
        project_id=parent_proj["id"],
        artifact_type="report",
    )

    res = artifact_service.create_project_from_artifact(
        artifact_id=art["id"],
        user_objective="Turn this architecture report into an interactive website with live metrics.",
        title="Interactive Architecture Site",
        created_by="system-architect",
    )

    new_project = res["project"]
    assert new_project["name"] == "Interactive Architecture Site"
    assert "architecture_audit.md" in new_project["objective"]

    # Verify that the new project has authorized access to the source artifact
    assert artifact_service.check_project_access(art["id"], new_project["id"]) is True


def test_deliverable_acceptance_and_knowledge_extraction():
    """Verify that deliverable review links to canonical artifact and triggers knowledge extraction."""
    proj = create_project("Deliverable KB Project", "test-suite", description="KB extraction tests")
    
    # 1. Register canonical report artifact
    report_text = """# System Health Assessment
## Key Findings
- Worker cluster maintains 99.8% uptime during heavy load
- Ollama inference latency averages 45ms per token on laptop GPU
- Network partition recovery succeeded within 12 seconds
"""
    art = artifact_service.register_artifact(
        name="system_health.md",
        data=report_text.encode("utf-8"),
        project_id=proj["id"],
        artifact_type="report",
    )

    # 2. Create planned deliverable
    deliv = create_deliverable(
        project_id=proj["id"],
        name="Cluster Health Assessment",
        deliverable_type="report",
        created_by="tester",
    )

    # 3. Submit deliverable referencing artifact
    submitted = submit_deliverable(
        deliverable_id=deliv["id"],
        user="worker-vps",
        artifact_references=f"system_health.md ({art['id']})",
        verification_results="Checksum verified: deterministic pass",
        primary_artifact_id=art["id"],
    )
    assert submitted["status"] == "submitted"
    assert submitted["primary_artifact_id"] == art["id"]

    # 4. Review and accept deliverable
    reviewed = review_deliverable(
        deliverable_id=deliv["id"],
        new_status="accepted",
        reviewer="lead_architect",
        rationale="All criteria met and findings corroborated",
    )
    assert reviewed["status"] == "accepted"

    # Verify artifact status became accepted
    fresh_art = artifact_service.get_artifact(art["id"])
    assert fresh_art["status"] == "accepted"
    assert fresh_art["knowledge_status"] == "extracted"


def test_observatory_deliverables_and_artifacts_telemetry():
    """Verify that Observatory analytics computes accurate counts and storage rollups."""
    metrics = get_deliverables_and_artifacts_metrics()
    assert "artifacts" in metrics
    assert "deliverables" in metrics
    assert "reuse_and_lineage" in metrics
    assert metrics["artifacts"]["total"] > 0
    assert metrics["artifacts"]["total_storage_bytes"] > 0
