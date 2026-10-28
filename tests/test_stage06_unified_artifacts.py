"""
Stage 6: Unified Artifact and Deliverable Lifecycle Integration Test Suite.

Verifies:
- Test A: Execution run produces a file deliverable, registered with full metadata and checksum.
- Test B: Execution run produces a structured data deliverable (JSON), stored as an artifact and registered.
- Test C: Execution run produces a combined artifact package with metadata linking the components.
- Test D: Re-running a task with modified output creates a new version of the artifact, preserving the previous version with its original checksum.
- Test E: An artifact is sent back into a project for additional work, with the action logged as a reuse.
- Test F: A shared artifact is made available to another project, with an access grant and project link created.
- Test G: An artifact containing active HTML/SVG cannot execute script in a privileged origin.
- Test H: A large artifact exceeding the configured preview limit displays a truncated preview with a full download option.
- Test I: Knowledge extracted from an artifact creates candidate knowledge records with links to the source artifact and version.
- Test J: An artifact cannot be downloaded, previewed, or reused after authorization has been revoked.
"""
from __future__ import annotations

import hashlib
import json
import uuid
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from app.database import get_db_connection
from app.main import app
from app.services.projects import create_project
from app.services.artifacts.service import ArtifactService
from app.services.artifacts.storage import LocalFilesystemStorageBackend
from app.services.orchestration.action_registry import (
    GLOBAL_ACTION_REGISTRY,
)


@pytest.fixture
def test_projects(tmp_path):
    """Create isolated test projects for artifact lifecycle testing."""
    p1 = create_project(
        name=f"Source Project {uuid.uuid4().hex[:6]}",
        objective="Generate models and reports",
        owner="test_engineer",
    )
    p2 = create_project(
        name=f"Consumer Project {uuid.uuid4().hex[:6]}",
        objective="Analyze models and reports",
        owner="test_analyst",
    )
    return p1, p2


@pytest.fixture
def isolated_artifact_service(tmp_path):
    """Artifact service instance backed by a temporary test directory."""
    storage_dir = tmp_path / "artifacts"
    storage_dir.mkdir(parents=True, exist_ok=True)
    return ArtifactService(storage_dir=storage_dir)


def test_a_file_deliverable_registered_with_metadata_and_checksum(test_projects, isolated_artifact_service):
    """Test A: An execution run produces a file deliverable, registered with full metadata and checksum."""
    p1, _ = test_projects
    code_content = b"def calculate_metrics(values):\n    return sum(values) / max(len(values), 1)\n"
    expected_sha256 = hashlib.sha256(code_content).hexdigest()

    # Register deliverable artifact
    art = isolated_artifact_service.register_artifact(
        project_id=p1["id"],
        display_name="metrics_calculator.py",
        artifact_type="code",
        data=code_content,
        created_by="worker-node-1",
        run_id="run-stage06-test-a",
        task_id="task-code-gen-01",
        description="Core metrics calculation utility module",
        tags=["python", "utility", "metrics"],
    )

    assert art is not None
    assert art["id"].startswith("art-")
    assert art["display_name"] == "metrics_calculator.py"
    assert art["artifact_type"] == "code"
    assert art["mime_type"] == "text/x-python"
    assert art["byte_size"] == len(code_content)
    assert art["checksum_sha256"] == expected_sha256
    assert art["current_version_number"] == 1

    # Verify download integrity matches
    dl = isolated_artifact_service.resolve_download(art["id"])
    assert dl["checksum_sha256"] == expected_sha256
    assert Path(dl["file_path"]).read_bytes() == code_content


def test_b_structured_data_deliverable_registered(test_projects, isolated_artifact_service):
    """Test B: An execution run produces a structured data deliverable (JSON), stored as an artifact and registered."""
    p1, _ = test_projects
    dataset_payload = {
        "benchmark_run": "2026-10-20T12:00:00Z",
        "system": "qwen-collective",
        "metrics": {
            "p50_latency_ms": 42.5,
            "p99_latency_ms": 118.2,
            "throughput_rps": 850,
            "error_rate": 0.0001,
        },
        "sample_points": [10, 20, 30, 40, 50],
    }
    raw_json = json.dumps(dataset_payload, indent=2).encode("utf-8")
    expected_sha256 = hashlib.sha256(raw_json).hexdigest()

    art = isolated_artifact_service.register_artifact(
        project_id=p1["id"],
        display_name="benchmark_results.json",
        artifact_type="dataset",
        data=raw_json,
        created_by="benchmark-worker",
        run_id="run-stage06-test-b",
        task_id="task-bench-02",
        metadata={"record_count": 5, "format": "json"},
    )

    assert art["artifact_type"] == "dataset"
    assert art["mime_type"] == "application/json"
    assert art["checksum_sha256"] == expected_sha256

    preview = isolated_artifact_service.get_preview_data(art["id"])
    assert preview["preview_type"] == "json"
    assert preview["is_truncated"] is False
    assert preview["json_data"]["system"] == "qwen-collective"
    assert preview["json_data"]["metrics"]["throughput_rps"] == 850


def test_c_combined_artifact_package_with_component_links(test_projects, isolated_artifact_service):
    """Test C: An execution run produces a combined artifact package with metadata linking the components."""
    p1, _ = test_projects

    # Create two component artifacts
    doc_art = isolated_artifact_service.register_artifact(
        project_id=p1["id"],
        display_name="architecture_spec.md",
        artifact_type="document",
        data=b"# Architecture Specification\nDetails on distributed coordination.\n",
        created_by="worker-1",
    )
    code_art = isolated_artifact_service.register_artifact(
        project_id=p1["id"],
        display_name="coordinator.py",
        artifact_type="code",
        data=b"class Coordinator:\n    pass\n",
        created_by="worker-1",
    )

    # Package components together
    components = [
        {"artifact_id": doc_art["id"], "role": "specification", "version_number": 1},
        {"artifact_id": code_art["id"], "role": "implementation", "version_number": 1},
    ]

    pkg = isolated_artifact_service.create_artifact_package(
        project_id=p1["id"],
        package_name="distributed-coordinator-release-v1",
        description="Combined release bundle containing spec and code",
        components=components,
        created_by="lead_orchestrator",
        run_id="run-stage06-test-c",
    )

    assert pkg["id"].startswith("pkg-")
    assert pkg["package_name"] == "distributed-coordinator-release-v1"
    assert len(pkg["components"]) == 2
    assert pkg["manifest"]["component_count"] == 2

    # Verify database component relationships
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM artifact_package_components WHERE package_id = ? ORDER BY role",
            (pkg["id"],),
        ).fetchall()
        assert len(rows) == 2
        roles = [r["role"] for r in rows]
        assert "implementation" in roles
        assert "specification" in roles
    finally:
        conn.close()


def test_d_task_rerun_creates_new_version_preserving_previous(test_projects, isolated_artifact_service):
    """Test D: Re-running a task with modified output creates a new version, preserving v1 with its checksum."""
    p1, _ = test_projects
    v1_content = b"print('Hello v1')\n"
    v1_sha = hashlib.sha256(v1_content).hexdigest()

    art = isolated_artifact_service.register_artifact(
        project_id=p1["id"],
        display_name="greeting.py",
        artifact_type="code",
        data=v1_content,
        created_by="worker-1",
    )
    art_id = art["id"]
    assert art["current_version_number"] == 1
    assert art["checksum_sha256"] == v1_sha

    # Rerun or modify task: create version 2
    v2_content = b"print('Hello v2 with enhanced greetings')\n"
    v2_sha = hashlib.sha256(v2_content).hexdigest()

    v2_info = isolated_artifact_service.create_artifact_version(
        artifact_id=art_id,
        data=v2_content,
        change_description="Enhanced greeting message",
        created_by="worker-1",
    )

    assert v2_info["version_number"] == 2
    assert v2_info["checksum_sha256"] == v2_sha

    # Verify both versions exist simultaneously and their contents/checksums are intact
    dl_v1 = isolated_artifact_service.resolve_download(art_id, version_number=1)
    assert dl_v1["checksum_sha256"] == v1_sha
    assert Path(dl_v1["file_path"]).read_bytes() == v1_content

    dl_v2 = isolated_artifact_service.resolve_download(art_id, version_number=2)
    assert dl_v2["checksum_sha256"] == v2_sha
    assert Path(dl_v2["file_path"]).read_bytes() == v2_content


def test_e_artifact_reused_in_project_logged_with_lineage(test_projects, isolated_artifact_service):
    """Test E: An artifact is sent back into a project for additional work, with the action logged as a reuse."""
    p1, _ = test_projects
    art = isolated_artifact_service.register_artifact(
        project_id=p1["id"],
        display_name="dataset_draft.csv",
        artifact_type="dataset",
        data=b"id,val\n1,10\n2,20\n",
        created_by="worker-1",
    )

    # Reuse artifact in same project for refinement
    reuse_res = isolated_artifact_service.reuse_artifact_in_project(
        artifact_id=art["id"],
        project_id=p1["id"],
        action_type="refine",
        instructions="Normalize values and add headers",
        actor="lead_developer",
    )

    assert reuse_res["status"] == "reused"
    assert reuse_res["reuse_id"].startswith("reuse-")
    assert reuse_res["action_type"] == "refine"

    # Verify lineage ledger in database
    conn = get_db_connection()
    try:
        reuses = conn.execute(
            "SELECT * FROM artifact_reuses WHERE artifact_id = ?",
            (art["id"],),
        ).fetchall()
        assert len(reuses) == 1
        assert reuses[0]["target_project_id"] == p1["id"]
        assert reuses[0]["action_type"] == "refine"
        assert reuses[0]["status"] == "active"
    finally:
        conn.close()


def test_f_cross_project_sharing_and_access_grant(test_projects, isolated_artifact_service):
    """Test F: A shared artifact is made available to another project, with an access grant and project link created."""
    p1, p2 = test_projects

    # Originates in p1 as private/shared
    art = isolated_artifact_service.register_artifact(
        project_id=p1["id"],
        display_name="shared_model_weights.bin",
        artifact_type="model",
        data=b"BINARY_WEIGHTS_DATA_V1",
        created_by="p1_researcher",
        visibility="project",
    )

    # Before grant, p2 cannot access
    assert isolated_artifact_service.check_project_access(art["id"], p2["id"]) is False
    with pytest.raises(PermissionError):
        isolated_artifact_service.resolve_download(art["id"], project_id=p2["id"])

    # Grant access to p2
    grant_id = isolated_artifact_service.grant_project_access(
        artifact_id=art["id"],
        source_project_id=p1["id"],
        target_project_id=p2["id"],
        granted_by="admin",
    )
    assert grant_id.startswith("grant-")

    # Now p2 can access
    assert isolated_artifact_service.check_project_access(art["id"], p2["id"]) is True
    dl = isolated_artifact_service.resolve_download(art["id"], project_id=p2["id"])
    assert dl["display_name"] == "shared_model_weights.bin"

    # Verify project link exists
    conn = get_db_connection()
    try:
        link = conn.execute(
            "SELECT * FROM artifact_project_links WHERE artifact_id = ? AND project_id = ?",
            (art["id"], p2["id"]),
        ).fetchone()
        assert link is not None
        assert link["link_type"] == "shared"
    finally:
        conn.close()


def test_g_active_markup_cannot_execute_script_in_origin(test_projects, isolated_artifact_service):
    """Test G: An artifact containing active HTML/SVG cannot execute script in a privileged origin."""
    p1, _ = test_projects
    from app.auth import create_session_token
    client = TestClient(app)
    client.cookies.set("qc_session", create_session_token(p1.get("owner") or "admin"))

    malicious_html = b"<!DOCTYPE html><html><body><script>alert(document.cookie)</script><h1>Report</h1></body></html>"
    art = isolated_artifact_service.register_artifact(
        project_id=p1["id"],
        display_name="exploit_report.html",
        artifact_type="document",
        data=malicious_html,
        created_by="untrusted-worker",
    )

    # 1. Download endpoint with inline=1 MUST serve text/plain or attachment with strict CSP
    resp = client.get(f"/api/v1/artifacts/{art['id']}/download?inline=1")
    assert resp.status_code == 200
    # Media type forced away from executable text/html or attachment disposition enforced
    assert resp.headers["content-type"].startswith("text/plain") or "attachment" in resp.headers.get("content-disposition", "")
    assert "sandbox" in resp.headers.get("content-security-policy", "")
    assert resp.headers.get("x-content-type-options") == "nosniff"

    # 2. Sandboxed preview endpoint serves with strict sandbox CSP disabling scripts
    resp_sandboxed = client.get(f"/api/v1/artifacts/{art['id']}/sandboxed-preview")
    assert resp_sandboxed.status_code == 200
    csp = resp_sandboxed.headers.get("content-security-policy", "")
    assert "sandbox" in csp
    assert "allow-scripts" not in csp  # Explicitly forbids scripts


def test_h_large_artifact_preview_truncation(test_projects, isolated_artifact_service):
    """Test H: A large artifact exceeding the configured preview limit displays a truncated preview with full download."""
    p1, _ = test_projects

    # Create artifact exceeding 64KB preview limit (e.g. 100KB)
    large_line = "A" * 1000 + "\n"
    large_data = (large_line * 100).encode("utf-8")  # 100,100 bytes
    art = isolated_artifact_service.register_artifact(
        project_id=p1["id"],
        display_name="large_dataset.txt",
        artifact_type="dataset",
        data=large_data,
        created_by="worker-1",
    )

    preview = isolated_artifact_service.get_preview_data(art["id"])
    assert preview["is_truncated"] is True
    assert preview["truncated_preview"] is not None
    assert len(preview["truncated_preview"].encode("utf-8")) <= 65536
    assert preview["total_bytes"] == len(large_data)

    # Full download remains un-truncated
    dl = isolated_artifact_service.resolve_download(art["id"])
    assert dl["byte_size"] == len(large_data)
    assert len(Path(dl["file_path"]).read_bytes()) == len(large_data)


def test_i_knowledge_extracted_creates_candidate_records_with_links(test_projects, isolated_artifact_service):
    """Test I: Knowledge extracted from an artifact creates candidate knowledge records with links to source and version."""
    p1, _ = test_projects
    analysis_text = (
        "# System Benchmarks\n\n"
        "Benchmark Result: Throughput achieved was 850 requests per second under peak load.\n"
        "Requirement: Service response latency must remain strictly under 100 milliseconds.\n"
        "Design Constraint: Distributed workers require minimum 4GB RAM allocation.\n"
    ).encode("utf-8")

    art = isolated_artifact_service.register_artifact(
        project_id=p1["id"],
        display_name="analysis_summary.md",
        artifact_type="document",
        data=analysis_text,
        created_by="researcher-1",
    )

    # Extract knowledge from artifact
    records = isolated_artifact_service.extract_knowledge_from_artifact(art["id"], actor="evaluator-agent")
    assert len(records) >= 2

    # Verify knowledge links in database
    conn = get_db_connection()
    try:
        links = conn.execute(
            "SELECT * FROM artifact_knowledge_links WHERE artifact_id = ?",
            (art["id"],),
        ).fetchall()
        assert len(links) >= 2
        for link in links:
            assert link["artifact_id"] == art["id"]
            assert link["artifact_version_number"] == 1
            assert link["knowledge_record_id"] in [r["id"] for r in records]

        # Verify knowledge record metadata connects back to source artifact
        for r in records:
            k_row = conn.execute(
                "SELECT * FROM knowledge_records WHERE id = ?",
                (r["id"],),
            ).fetchone()
            assert k_row is not None
            source_ids = json.loads(k_row["source_ids_json"])
            assert art["id"] in source_ids
    finally:
        conn.close()


def test_j_revoked_artifact_cannot_be_downloaded_previewed_or_reused(test_projects, isolated_artifact_service):
    """Test J: An artifact cannot be downloaded, previewed, or reused after authorization has been revoked."""
    p1, p2 = test_projects
    secret_data = b"CONFIDENTIAL_PROPRIETARY_CODE_V1"

    art = isolated_artifact_service.register_artifact(
        project_id=p1["id"],
        display_name="proprietary_algorithm.py",
        artifact_type="code",
        data=secret_data,
        created_by="worker-1",
    )
    art_id = art["id"]

    # Verify initially accessible
    assert isolated_artifact_service.check_project_access(art_id, p1["id"]) is True
    assert isolated_artifact_service.resolve_download(art_id) is not None

    # Revoke artifact
    revocation_ok = isolated_artifact_service.revoke_artifact(
        artifact_id=art_id,
        revoked_by="security_admin",
        reason="Compliance audit revocation",
    )
    assert revocation_ok is True

    # 1. Access check fails
    assert isolated_artifact_service.check_project_access(art_id, p1["id"]) is False

    # 2. Download fails with PermissionError
    with pytest.raises(PermissionError) as exc_dl:
        isolated_artifact_service.resolve_download(art_id)
    assert "revoked" in str(exc_dl.value).lower()

    # 3. Preview fails with PermissionError
    with pytest.raises(PermissionError) as exc_prev:
        isolated_artifact_service.get_preview_data(art_id)
    assert "revoked" in str(exc_prev.value).lower()

    # 4. Reuse in project fails with PermissionError
    with pytest.raises(PermissionError) as exc_reuse:
        isolated_artifact_service.reuse_artifact_in_project(
            artifact_id=art_id,
            project_id=p1["id"],
            action_type="audit",
        )
    assert "revoked" in str(exc_reuse.value).lower()


def test_k_deliverables_routes_render_ok_and_deny_unauthenticated(test_projects, isolated_artifact_service):
    """Test K (Regression for FIX 1): Deliverables routes return HTTP 200 with content for authenticated users, denying unauthenticated."""
    from app.auth import create_session_token
    from app.services.artifacts import artifact_service

    p1, _ = test_projects
    art = isolated_artifact_service.register_artifact(
        project_id=p1["id"],
        display_name="regression_test_report.md",
        artifact_type="document",
        data=b"# Regression Test Report\n\nAll components functioning normally.",
        created_by="tester",
    )
    art_id = art["id"]

    # Also register with global service if needed for route lookup
    try:
        artifact_service.register_artifact(
            project_id=p1["id"],
            display_name="regression_test_report.md",
            artifact_type="document",
            data=b"# Regression Test Report\n\nAll components functioning normally.",
            created_by="tester",
        )
    except Exception:
        pass

    anon = TestClient(app)
    # Unauthenticated requests are denied / redirected to login
    r_anon_hub = anon.get("/deliverables", follow_redirects=False)
    assert r_anon_hub.status_code in (302, 401)
    assert "/auth/login" in r_anon_hub.headers.get("location", "")

    r_anon_detail = anon.get(f"/deliverables/{art_id}", follow_redirects=False)
    assert r_anon_detail.status_code in (302, 401)
    assert "/auth/login" in r_anon_detail.headers.get("location", "")

    # Authenticated requests succeed with HTTP 200
    auth_client = TestClient(app)
    auth_client.cookies.set("qc_session", create_session_token("admin"))

    r_hub = auth_client.get("/deliverables")
    assert r_hub.status_code == 200
    assert "Deliverables" in r_hub.text

    r_detail = auth_client.get(f"/deliverables/{art_id}")
    assert r_detail.status_code == 200
    assert "Safe File Preview" in r_detail.text
    assert "regression_test_report.md" in r_detail.text
