"""
Stage 8A: Backup, Recovery, and Data Integrity Test Suite.

Proves end-to-end recoverable snapshotting, independent restoration into an isolated
disposable environment, checksum verification, trust-tier preservation, human review
digest validity, and fail-closed security defenses (path traversal, corrupted files,
missing CAS blobs, live production protection).
"""
import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any

import pytest

from app.config import settings
from app.database import init_db, get_db_connection, db_transaction
from app.services.backup import (
    create_backup,
    inspect_backup,
    verify_backup,
    restore_backup,
    list_backups,
    BackupError,
    BackupVerificationError,
    RestoreError,
    PathTraversalSecurityError,
    ArchiveSecurityError,
    MAX_TOTAL_UNPACKED_BYTES,
    MAX_SINGLE_FILE_BYTES,
    SnapshotCoordination,
    snapshot_barrier,
    write_barrier,
)
from app.auth import create_session_token
from app.services.artifacts.storage import LocalFilesystemStorageBackend


@pytest.fixture
def disposable_environment(tmp_path):
    """
    Creates an isolated disposable environment with distinct data, artifacts,
    knowledge, and evidence directories to simulate a fully populated instance.
    """
    env_root = tmp_path / "source_env"
    data_dir = env_root / "data"
    db_path = data_dir / "qwen_collective.db"
    artifacts_dir = data_dir / "artifacts"
    knowledge_dir = data_dir / "knowledge"
    evidence_dir = data_dir / "evidence"
    backups_dir = env_root / "backups"

    data_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)

    # Initialize SQLite database schema
    from app.database import create_baseline_schema
    from app.migrations import apply_migrations
    with sqlite3.connect(str(db_path)) as conn:
        create_baseline_schema(conn)
        apply_migrations(conn)

    # Populate rich test dataset
    storage_backend = LocalFilesystemStorageBackend(base_dir=artifacts_dir)
    
    # 1. Projects and Tasks
    p1_id = f"prj-{uuid.uuid4().hex[:8]}"
    p2_id = f"prj-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute(
            """INSERT INTO projects (id, name, description, status, owner, knowledge_dir, created_at, updated_at, last_activity_at)
               VALUES (?, 'Primary Alpha Project', 'Project 1 description', 'active', 'admin', ?, ?, ?, ?)""",
            (p1_id, f"projects/{p1_id}", now, now, now),
        )
        conn.execute(
            """INSERT INTO projects (id, name, description, status, owner, knowledge_dir, created_at, updated_at, last_activity_at)
               VALUES (?, 'Secondary Beta Project', 'Project 2 description', 'active', 'admin', ?, ?, ?, ?)""",
            (p2_id, f"projects/{p2_id}", now, now, now),
        )

        task1_id = f"tsk-{uuid.uuid4().hex[:8]}"
        task2_id = f"tsk-{uuid.uuid4().hex[:8]}"
        conn.execute(
            """INSERT INTO tasks (id, title, task_type, status, project_id, created_at, updated_at)
               VALUES (?, 'Task Alpha 1', 'analysis', 'completed', ?, ?, ?)""",
            (task1_id, p1_id, now, now),
        )
        conn.execute(
            """INSERT INTO tasks (id, title, task_type, status, project_id, created_at, updated_at)
               VALUES (?, 'Task Alpha 2', 'synthesis', 'completed', ?, ?, ?)""",
            (task2_id, p1_id, now, now),
        )
        conn.commit()

    # 2. Immutable CAS Artifacts with Version History & Cross-Project Grants
    # Store blob in CAS
    content_v1 = b"Payload V1: Canonical dataset for model calibration"
    content_v2 = b"Payload V2: Updated dataset with extended evaluation metrics"
    cas_v1 = storage_backend.store_cas_blob(content_v1)
    cas_v2 = storage_backend.store_cas_blob(content_v2)

    art1_id = f"art-{uuid.uuid4().hex[:8]}"
    ver1_id = f"ver-{uuid.uuid4().hex[:8]}"
    ver2_id = f"ver-{uuid.uuid4().hex[:8]}"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute(
            """INSERT INTO artifacts (id, display_name, artifact_type, storage_backend, storage_key,
                                      current_version_number, checksum_sha256, size_bytes, origin_project_id,
                                      created_by, created_at, updated_at)
               VALUES (?, 'calibration_dataset.csv', 'dataset', 'local_filesystem', ?, 1, ?, ?, ?, 'system', ?, ?)""",
            (art1_id, cas_v1["storage_key"], cas_v1["checksum_sha256"], len(content_v1), p1_id, now, now),
        )
        conn.execute(
            """INSERT INTO artifact_versions (id, artifact_id, version_number, storage_backend, storage_key,
                                              checksum_sha256, size_bytes, created_by, created_at)
               VALUES (?, ?, 1, 'local_filesystem', ?, ?, ?, 'engineer_a', ?)""",
            (ver1_id, art1_id, cas_v1["storage_key"], cas_v1["checksum_sha256"], len(content_v1), now),
        )
        conn.execute(
            """INSERT INTO artifact_versions (id, artifact_id, version_number, storage_backend, storage_key,
                                              checksum_sha256, size_bytes, created_by, created_at)
               VALUES (?, ?, 2, 'local_filesystem', ?, ?, ?, 'engineer_b', ?)""",
            (ver2_id, art1_id, cas_v2["storage_key"], cas_v2["checksum_sha256"], len(content_v2), now),
        )
        # Cross project grant: Allow Project Beta to read Art 1
        grant_id = f"grt-{uuid.uuid4().hex[:8]}"
        conn.execute(
            """INSERT INTO artifact_access_grants (id, artifact_id, source_project_id, target_project_id,
                                                   granted_by, granted_at, status)
               VALUES (?, ?, ?, ?, 'system_operator', ?, 'active')""",
            (grant_id, art1_id, p1_id, p2_id, now),
        )
        # Lineage record: Art 1 was derived from an earlier run
        lin_id = f"lin-{uuid.uuid4().hex[:8]}"
        conn.execute(
            """INSERT INTO artifact_lineage (id, source_artifact_id, target_artifact_id, relationship_type, created_at)
               VALUES (?, ?, ?, 'derived_from', ?)""",
            (lin_id, art1_id, art1_id, now),
        )
        conn.commit()

    # 3. Knowledge Records across Multiple Trust Tiers with Human Reviews
    kn1_id = f"kn-{uuid.uuid4().hex[:8]}"
    kn2_id = f"kn-{uuid.uuid4().hex[:8]}"
    kn3_id = f"kn-{uuid.uuid4().hex[:8]}"

    stmt1 = "Polars demonstrates 4.2x faster query throughput than Pandas on parquet benchmarks."
    stmt2 = "Quantized 1.5B models require a minimum of 2GB VRAM on embedded edge devices."
    stmt3 = "Tailscale subnet routes require IP forwarding enabled in Linux sysctl."

    # Compute human review audit digest
    digest1 = hashlib.sha256(f"Polars Execution Speed:::{stmt1}".encode("utf-8")).hexdigest()
    review_notes_1 = f"Benchmarked empirically against local dataset. [digest:{digest1[:16]}][human_reviewer:lead_architect]"

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        # kn1: 'reviewed' tier with verified human review attribution
        conn.execute(
            """INSERT INTO knowledge_records (id, scope, project_id, subject, statement, knowledge_type,
                                              verification_status, trust_tier, reviewed_by, review_notes,
                                              created_at, updated_at)
               VALUES (?, 'project', ?, 'Polars Execution Speed', ?, 'fact', 'supported',
                       'reviewed', 'lead_architect', ?, ?, ?)""",
            (kn1_id, p1_id, stmt1, review_notes_1, now, now),
        )
        # kn2: 'candidate' tier awaiting review
        conn.execute(
            """INSERT INTO knowledge_records (id, scope, project_id, subject, statement, knowledge_type,
                                              verification_status, trust_tier, created_at, updated_at)
               VALUES (?, 'project', ?, 'Edge Model VRAM', ?, 'fact', 'unverified',
                       'candidate', ?, ?)""",
            (kn2_id, p1_id, stmt2, now, now),
        )
        # kn3: 'reusable' collective scope
        digest3 = hashlib.sha256(f"Tailscale Route Config:::{stmt3}".encode("utf-8")).hexdigest()
        review_notes_3 = f"Validated on Linux VPS node. [digest:{digest3[:16]}][human_reviewer:lead_architect]"
        conn.execute(
            """INSERT INTO knowledge_records (id, scope, project_id, subject, statement, knowledge_type,
                                              verification_status, trust_tier, reviewed_by, review_notes,
                                              promoted_by, created_at, updated_at)
               VALUES (?, 'collective', ?, 'Tailscale Route Config', ?, 'procedure', 'corroborated',
                       'reusable', 'lead_architect', ?, 'lead_architect', ?, ?)""",
            (kn3_id, p1_id, stmt3, review_notes_3, now, now),
        )
        conn.commit()

    # 4. Research Sources, Citations & Lineage
    res_run_id = f"run-{uuid.uuid4().hex[:8]}"
    source_id = f"src-{uuid.uuid4().hex[:8]}"
    claim_id = f"clm-{uuid.uuid4().hex[:8]}"
    ev_id = f"evd-{uuid.uuid4().hex[:8]}"

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute(
            """INSERT INTO research_runs (id, project_id, objective, status, created_at, updated_at)
               VALUES (?, ?, 'High-Throughput DataFrame Analysis', 'completed', ?, ?)""",
            (res_run_id, p1_id, now, now),
        )
        content_hash = hashlib.sha256(b"Polars benchmark page content").hexdigest()
        conn.execute(
            """INSERT INTO research_sources (id, project_id, canonical_url, original_url, title,
                                             retrieved_at, content_hash, content_length_bytes,
                                             research_run_id, created_at, updated_at)
               VALUES (?, ?, 'https://pola.rs/benchmarks', 'https://pola.rs/benchmarks',
                       'Polars Official Benchmarks', ?, ?, 32, ?, ?, ?)""",
            (source_id, p1_id, now, content_hash, res_run_id, now, now),
        )
        conn.execute(
            """INSERT INTO research_claims (id, project_id, research_run_id, claim_text, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (claim_id, p1_id, res_run_id, stmt1, now, now),
        )
        conn.execute(
            """INSERT INTO research_evidence (id, project_id, claim_id, source_id, supporting_excerpt, created_at)
               VALUES (?, ?, ?, ?, 'Execution time was 4.2x faster across 10GB parquet query.', ?)""",
            (ev_id, p1_id, claim_id, source_id, now),
        )
        conn.execute(
            """INSERT INTO research_citations (id, project_id, research_run_id, citation_num, source_id, claim_id, created_at)
               VALUES (?, ?, ?, 1, ?, ?, ?)""",
            (f"cit-{uuid.uuid4().hex[:8]}", p1_id, res_run_id, source_id, claim_id, now),
        )
        conn.commit()

    # 5. Persistent Markdown Knowledge Vault Files
    vault_note1 = knowledge_dir / "research" / "polars_benchmarks.md"
    vault_note1.parent.mkdir(parents=True, exist_ok=True)
    vault_note1.write_text(f"# Polars Benchmarks\n\n{stmt1}\n", encoding="utf-8")

    vault_system = knowledge_dir / "system" / "cluster_topology.md"
    vault_system.parent.mkdir(parents=True, exist_ok=True)
    vault_system.write_text("# Cluster Topology\n\nNodes: Windows Controller, VPS Worker.\n", encoding="utf-8")

    # 6. Research Evidence Run Files
    ev_run_dir = evidence_dir / f"orch_{res_run_id}"
    ev_run_dir.mkdir(parents=True, exist_ok=True)
    (ev_run_dir / "citation_audit.json").write_text(
        json.dumps({"run_id": res_run_id, "claims_verified": 1, "status": "VERIFIED"}, indent=2),
        encoding="utf-8"
    )
    (ev_run_dir / "evidence_ledger.json").write_text(
        json.dumps([{"claim_id": claim_id, "evidence_id": ev_id, "source_id": source_id}], indent=2),
        encoding="utf-8"
    )

    return {
        "env_root": env_root,
        "data_dir": data_dir,
        "db_path": db_path,
        "artifacts_dir": artifacts_dir,
        "knowledge_dir": knowledge_dir,
        "evidence_dir": evidence_dir,
        "backups_dir": backups_dir,
        "p1_id": p1_id,
        "p2_id": p2_id,
        "art1_id": art1_id,
        "content_v1": content_v1,
        "content_v2": content_v2,
        "cas_v1_hash": cas_v1["checksum_sha256"],
        "cas_v2_hash": cas_v2["checksum_sha256"],
        "kn1_id": kn1_id,
        "kn2_id": kn2_id,
        "kn3_id": kn3_id,
        "stmt1": stmt1,
        "digest1": digest1,
        "res_run_id": res_run_id,
        "source_id": source_id,
        "claim_id": claim_id,
    }


def test_backup_creation_and_manifest(disposable_environment):
    """Test 1: Create backup and verify manifest contents and structure."""
    env = disposable_environment
    result = create_backup(
        output_dir=env["backups_dir"],
        data_dir=env["data_dir"],
        db_path=env["db_path"],
        artifacts_dir=env["artifacts_dir"],
        knowledge_dir=env["knowledge_dir"],
        evidence_dir=env["evidence_dir"],
        operator_id="test_admin",
        comment="Integration Test Snapshot",
    )

    assert result["status"] == "success"
    assert result["snapshot_id"].startswith("snp-")
    assert result["operator_id"] == "test_admin"
    assert Path(result["archive_path"]).exists()
    assert result["size_bytes"] > 0
    assert result["component_count"] > 0
    assert result["record_counts"].get("projects", 0) >= 2
    assert result["record_counts"].get("tasks", 0) >= 2
    assert result["record_counts"].get("artifacts", 0) >= 1
    assert result["record_counts"].get("knowledge_records", 0) >= 3


def test_backup_inspect_and_list(disposable_environment):
    """Test 2: Test inspection and listing of recovery points without full extraction."""
    env = disposable_environment
    res = create_backup(
        output_dir=env["backups_dir"],
        data_dir=env["data_dir"],
        db_path=env["db_path"],
        artifacts_dir=env["artifacts_dir"],
        knowledge_dir=env["knowledge_dir"],
        evidence_dir=env["evidence_dir"],
        operator_id="operator_beta",
        comment="Inspectable Snapshot",
    )

    backups = list_backups(backup_dir=env["backups_dir"])
    assert len(backups) >= 1
    assert backups[0]["snapshot_id"] == res["snapshot_id"]

    info = inspect_backup(res["archive_path"])
    assert info["snapshot_id"] == res["snapshot_id"]
    assert info["operator_id"] == "operator_beta"
    assert info["comment"] == "Inspectable Snapshot"
    assert "database/qwen_collective.db" in info["components"]
    assert info["schema_version"] >= 15


def test_backup_verification_positive(disposable_environment):
    """Test 3: Verify that a valid archive passes complete integrity verification."""
    env = disposable_environment
    res = create_backup(
        output_dir=env["backups_dir"],
        data_dir=env["data_dir"],
        db_path=env["db_path"],
        artifacts_dir=env["artifacts_dir"],
        knowledge_dir=env["knowledge_dir"],
        evidence_dir=env["evidence_dir"],
    )

    v_res = verify_backup(res["archive_path"])
    assert v_res["valid"] is True
    assert len(v_res["errors"]) == 0
    assert v_res["schema_version"] >= 15
    assert v_res["component_count"] > 0


def test_restore_dry_run_does_not_modify_destination(disposable_environment, tmp_path):
    """Test 4: Verify that a dry-run restore validates everything without touching target."""
    env = disposable_environment
    res = create_backup(
        output_dir=env["backups_dir"],
        data_dir=env["data_dir"],
        db_path=env["db_path"],
        artifacts_dir=env["artifacts_dir"],
        knowledge_dir=env["knowledge_dir"],
        evidence_dir=env["evidence_dir"],
    )

    isolated_target = tmp_path / "dry_run_destination"
    dry_res = restore_backup(
        archive_path=res["archive_path"],
        target_data_dir=isolated_target,
        dry_run=True,
    )

    assert dry_res["status"] == "dry_run_success"
    assert dry_res["dry_run"] is True
    assert dry_res["verification_passed"] is True
    assert dry_res["files_to_restore_count"] > 0
    # Crucial assertion: Destination folder was NOT created/written to
    assert not isolated_target.exists()


def test_restore_live_production_safeguard(disposable_environment):
    """Test 5: Verify that attempting to restore into settings.data_dir without explicit flag fails."""
    env = disposable_environment
    res = create_backup(
        output_dir=env["backups_dir"],
        data_dir=env["data_dir"],
        db_path=env["db_path"],
        artifacts_dir=env["artifacts_dir"],
        knowledge_dir=env["knowledge_dir"],
        evidence_dir=env["evidence_dir"],
    )

    # Attempting to restore into settings.data_dir without allow_production_overwrite MUST fail
    with pytest.raises(PermissionError) as exc_info:
        restore_backup(
            archive_path=res["archive_path"],
            target_data_dir=settings.data_dir,
            allow_production_overwrite=False,
        )
    assert "CRITICAL SAFETY VIOLATION" in str(exc_info.value) or "Refusing to restore" in str(exc_info.value)


def test_restore_into_isolated_environment_end_to_end(disposable_environment, tmp_path):
    """
    Test 6: End-to-end positive restoration into an empty, isolated environment.
    Verifies:
    - Restored database integrity and foreign key validity.
    - All projects and tasks are preserved.
    - Artifact files match original SHA-256 hashes byte-for-byte.
    - Artifact lineage and cross-project grants are intact.
    - Human review attribution and approval digests remain valid.
    - Trust tiers are preserved with zero unauthorized elevation.
    - Research citations and source lineage remain intact.
    - Environment functions without original directory.
    """
    env = disposable_environment
    res = create_backup(
        output_dir=env["backups_dir"],
        data_dir=env["data_dir"],
        db_path=env["db_path"],
        artifacts_dir=env["artifacts_dir"],
        knowledge_dir=env["knowledge_dir"],
        evidence_dir=env["evidence_dir"],
        operator_id="lead_operator",
        comment="End to End Proof Snapshot",
    )

    isolated_dest = tmp_path / "isolated_restored_system"
    restore_res = restore_backup(
        archive_path=res["archive_path"],
        target_data_dir=isolated_dest,
        dry_run=False,
    )

    assert restore_res["status"] == "restored"
    assert restore_res["database_integrity"] == "ok"
    assert restore_res["schema_version"] >= 15

    # 1. Verify Restored SQLite DB
    restored_db_path = isolated_dest / "qwen_collective.db"
    assert restored_db_path.exists()
    assert restored_db_path.stat().st_size > 0

    with sqlite3.connect(str(restored_db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Integrity & Foreign keys
        ic = cur.execute("PRAGMA integrity_check;").fetchone()[0]
        assert ic.lower() == "ok"
        fk = cur.execute("PRAGMA foreign_key_check;").fetchall()
        assert len(fk) == 0

        # Project and task verification
        projects = {r["id"]: dict(r) for r in cur.execute("SELECT * FROM projects").fetchall()}
        assert env["p1_id"] in projects
        assert env["p2_id"] in projects
        assert projects[env["p1_id"]]["name"] == "Primary Alpha Project"

        tasks = {r["id"]: dict(r) for r in cur.execute("SELECT * FROM tasks").fetchall()}
        assert len(tasks) == 2

        # Artifacts and Versions
        art_row = cur.execute("SELECT * FROM artifacts WHERE id = ?", (env["art1_id"],)).fetchone()
        assert art_row is not None
        assert art_row["checksum_sha256"] == env["cas_v1_hash"]

        versions = cur.execute("SELECT * FROM artifact_versions WHERE artifact_id = ? ORDER BY version_number ASC", (env["art1_id"],)).fetchall()
        assert len(versions) == 2
        assert versions[0]["checksum_sha256"] == env["cas_v1_hash"]
        assert versions[1]["checksum_sha256"] == env["cas_v2_hash"]

        # Cross-project grant preserved
        grant = cur.execute("SELECT * FROM artifact_access_grants WHERE artifact_id = ?", (env["art1_id"],)).fetchone()
        assert grant is not None
        assert grant["source_project_id"] == env["p1_id"]
        assert grant["target_project_id"] == env["p2_id"]
        assert grant["status"] == "active"

        # Lineage record preserved
        lineage = cur.execute("SELECT * FROM artifact_lineage WHERE source_artifact_id = ?", (env["art1_id"],)).fetchone()
        assert lineage is not None
        assert lineage["relationship_type"] == "derived_from"

        # Knowledge records and trust tiers
        k_records = {r["id"]: dict(r) for r in cur.execute("SELECT * FROM knowledge_records").fetchall()}
        assert len(k_records) == 3

        # kn1: 'reviewed' tier, human reviewer attribution preserved
        k1 = k_records[env["kn1_id"]]
        assert k1["trust_tier"] == "reviewed"
        assert k1["reviewed_by"] == "lead_architect"
        assert f"[digest:{env['digest1'][:16]}]" in k1["review_notes"]

        # kn2: 'candidate' tier preserved (NO elevation)
        k2 = k_records[env["kn2_id"]]
        assert k2["trust_tier"] == "candidate"
        assert k2["scope"] == "project"

        # kn3: 'reusable' collective scope preserved
        k3 = k_records[env["kn3_id"]]
        assert k3["trust_tier"] == "reusable"
        assert k3["scope"] == "collective"

        # Research citations and evidence lineage
        src_row = cur.execute("SELECT * FROM research_sources WHERE id = ?", (env["source_id"],)).fetchone()
        assert src_row is not None
        assert src_row["canonical_url"] == "https://pola.rs/benchmarks"

        claim_row = cur.execute("SELECT * FROM research_claims WHERE id = ?", (env["claim_id"],)).fetchone()
        assert claim_row is not None
        assert claim_row["claim_text"] == env["stmt1"]

    # 2. Verify Restored Physical Files (CAS blobs)
    restored_cas_v1 = isolated_dest / "artifacts" / "cas" / env["cas_v1_hash"][:2] / env["cas_v1_hash"]
    restored_cas_v2 = isolated_dest / "artifacts" / "cas" / env["cas_v2_hash"][:2] / env["cas_v2_hash"]
    assert restored_cas_v1.exists()
    assert restored_cas_v2.exists()
    assert restored_cas_v1.read_bytes() == env["content_v1"]
    assert restored_cas_v2.read_bytes() == env["content_v2"]

    # 3. Verify Restored Knowledge Notes
    restored_note1 = isolated_dest / "knowledge" / "research" / "polars_benchmarks.md"
    assert restored_note1.exists()
    assert "Polars demonstrates 4.2x faster query throughput" in restored_note1.read_text(encoding="utf-8")

    # 4. Verify Restored Evidence Files
    restored_audit = isolated_dest / "evidence" / f"orch_{env['res_run_id']}" / "citation_audit.json"
    assert restored_audit.exists()
    audit_data = json.loads(restored_audit.read_text(encoding="utf-8"))
    assert audit_data["run_id"] == env["res_run_id"]
    assert audit_data["status"] == "VERIFIED"


def test_corrupted_backup_rejected_fail_closed(disposable_environment, tmp_path):
    """Test 7: Verify that tampering with file content in an archive is detected and rejected fail-closed."""
    env = disposable_environment
    res = create_backup(
        output_dir=env["backups_dir"],
        data_dir=env["data_dir"],
        db_path=env["db_path"],
        artifacts_dir=env["artifacts_dir"],
        knowledge_dir=env["knowledge_dir"],
        evidence_dir=env["evidence_dir"],
    )

    original_archive = Path(res["archive_path"])

    # Unpack archive, tamper with a file, and repack
    tamper_dir = tmp_path / "tamper_stage"
    tamper_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(original_archive, "r:*") as tar:
        tar.extractall(tamper_dir, filter="tar")

    # Tamper with an artifact CAS blob
    cas_files = list((tamper_dir / "artifacts" / "cas").rglob("*"))
    actual_cas_file = [f for f in cas_files if f.is_file()][0]
    actual_cas_file.write_bytes(b"MALICIOUS CORRUPTED BYTES INJECTED INTO CAS BLOB")

    corrupted_archive = env["backups_dir"] / "corrupted_backup.tar.gz"
    with tarfile.open(corrupted_archive, "w:gz") as tar:
        for root, _, files in os.walk(tamper_dir):
            for f in files:
                p = Path(root) / f
                rel = p.relative_to(tamper_dir).as_posix()
                tar.add(str(p), arcname=rel)

    # Verification must fail
    v_res = verify_backup(corrupted_archive)
    assert v_res["valid"] is False
    assert any("Checksum mismatch" in err for err in v_res["errors"])

    # Restore must fail-closed and refuse to execute
    dest_dir = tmp_path / "dest_tamper"
    with pytest.raises(BackupVerificationError) as exc_info:
        restore_backup(
            archive_path=corrupted_archive,
            target_data_dir=dest_dir,
        )
    assert "Restore rejected" in str(exc_info.value)
    # Destination must NOT contain extracted files
    assert not dest_dir.exists() or len(list(dest_dir.iterdir())) == 0


def test_missing_cas_blob_detected_fail_closed(disposable_environment, tmp_path):
    """Test 8: Verify that an archive missing a manifested CAS blob fails verification and restore."""
    env = disposable_environment
    res = create_backup(
        output_dir=env["backups_dir"],
        data_dir=env["data_dir"],
        db_path=env["db_path"],
        artifacts_dir=env["artifacts_dir"],
        knowledge_dir=env["knowledge_dir"],
        evidence_dir=env["evidence_dir"],
    )

    original_archive = Path(res["archive_path"])
    tamper_dir = tmp_path / "missing_blob_stage"
    tamper_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(original_archive, "r:*") as tar:
        tar.extractall(tamper_dir, filter="tar")

    # Delete one CAS file without updating manifest
    cas_files = [f for f in (tamper_dir / "artifacts" / "cas").rglob("*") if f.is_file()]
    cas_files[0].unlink()

    missing_blob_archive = env["backups_dir"] / "missing_blob_backup.tar.gz"
    with tarfile.open(missing_blob_archive, "w:gz") as tar:
        for root, _, files in os.walk(tamper_dir):
            for f in files:
                p = Path(root) / f
                rel = p.relative_to(tamper_dir).as_posix()
                tar.add(str(p), arcname=rel)

    v_res = verify_backup(missing_blob_archive)
    assert v_res["valid"] is False
    assert any("Missing component" in err for err in v_res["errors"])

    dest_dir = tmp_path / "dest_missing"
    with pytest.raises(BackupVerificationError):
        restore_backup(
            archive_path=missing_blob_archive,
            target_data_dir=dest_dir,
        )


def test_path_traversal_rejection_fail_closed(disposable_environment, tmp_path):
    """Test 9: Verify that archives attempting directory escape / path traversal are rejected."""
    env = disposable_environment
    malicious_archive = env["backups_dir"] / "malicious_traversal.tar.gz"

    # Construct malicious archive containing path traversal
    import io
    with tarfile.open(malicious_archive, "w:gz") as tar:
        # Add a benign manifest
        mf_data = json.dumps({
            "manifest_version": "1.0",
            "snapshot_id": "snp-malicious-001",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": 18,
            "components": {}
        }).encode("utf-8")
        ti_mf = tarfile.TarInfo(name="manifest.json")
        ti_mf.size = len(mf_data)
        tar.addfile(ti_mf, fileobj=io.BytesIO(mf_data))

        # Malicious traversal file
        evil_data = b"EVIL ROOT EXPLOIT ATTEMPT"
        ti_evil = tarfile.TarInfo(name="../../outside_target.txt")
        ti_evil.size = len(evil_data)
        tar.addfile(ti_evil, fileobj=io.BytesIO(evil_data))

    v_res = verify_backup(malicious_archive)
    assert v_res["valid"] is False
    assert any("violates path traversal" in err for err in v_res["errors"])

    target_dest = tmp_path / "traversal_victim"
    with pytest.raises(BackupVerificationError):
        restore_backup(
            archive_path=malicious_archive,
            target_data_dir=target_dest,
        )
    # Outside file must not have been created
    assert not (tmp_path / "outside_target.txt").exists()


def test_atomic_backup_under_live_writes(disposable_environment, tmp_path):
    """Test 10: Verify that sqlite3.Connection.backup works reliably while database is in WAL mode."""
    env = disposable_environment
    db_path = env["db_path"]

    # Set WAL mode
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.commit()

    # Perform a write in an active connection
    active_conn = sqlite3.connect(str(db_path))
    active_conn.execute(
        """INSERT INTO tasks (id, title, task_type, status, project_id, created_at, updated_at)
           VALUES ('tsk-live-write', 'Live Task In WAL', 'compute', 'running', ?, ?, ?)""",
        (env["p1_id"], datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
    )
    active_conn.commit()

    # Create backup while active connection exists
    res = create_backup(
        output_dir=env["backups_dir"],
        data_dir=env["data_dir"],
        db_path=env["db_path"],
        artifacts_dir=env["artifacts_dir"],
        knowledge_dir=env["knowledge_dir"],
        evidence_dir=env["evidence_dir"],
        operator_id="live_operator",
        comment="Live WAL backup test",
    )

    active_conn.close()

    # Verify backup
    v_res = verify_backup(res["archive_path"])
    assert v_res["valid"] is True

    # Restore and verify live write was captured
    isolated_dest = tmp_path / "live_wal_restored"
    restore_res = restore_backup(
        archive_path=res["archive_path"],
        target_data_dir=isolated_dest,
    )
    assert restore_res["status"] == "restored"

    with sqlite3.connect(str(isolated_dest / "qwen_collective.db")) as conn:
        cur = conn.cursor()
        task = cur.execute("SELECT * FROM tasks WHERE id = 'tsk-live-write'").fetchone()
        assert task is not None


# ==============================================================================
# STAGE 8A FINAL RECOVERY SAFETY GATE TESTS
# ==============================================================================

def test_unauthenticated_plain_operator_rejected_for_production_restore(disposable_environment):
    """Safety Gate 1: Ensure unauthenticated plain-string operator is rejected for production restore."""
    env = disposable_environment
    res = create_backup(
        output_dir=env["backups_dir"],
        data_dir=env["data_dir"],
        db_path=env["db_path"],
        artifacts_dir=env["artifacts_dir"],
        knowledge_dir=env["knowledge_dir"],
        evidence_dir=env["evidence_dir"],
    )
    arc = res["archive_path"]

    # Attempt to restore to live production data directory with plain string operator and no token
    with pytest.raises(PermissionError) as exc_info:
        restore_backup(
            archive_path=arc,
            target_data_dir=settings.data_dir,
            allow_production_overwrite=True,
            operator_id="admin",
            session_token=None,
        )
    assert "Unauthorized production restore attempt" in str(exc_info.value)
    assert "Plain string operator identities are strictly rejected" in str(exc_info.value)


def test_forged_session_token_rejected_for_production_restore(disposable_environment):
    """Safety Gate 2: Ensure forged or tampered session token is rejected for production restore."""
    env = disposable_environment
    res = create_backup(
        output_dir=env["backups_dir"],
        data_dir=env["data_dir"],
        db_path=env["db_path"],
        artifacts_dir=env["artifacts_dir"],
        knowledge_dir=env["knowledge_dir"],
        evidence_dir=env["evidence_dir"],
    )
    arc = res["archive_path"]

    with pytest.raises(PermissionError) as exc_info:
        restore_backup(
            archive_path=arc,
            target_data_dir=settings.data_dir,
            allow_production_overwrite=True,
            operator_id="admin",
            session_token="admin:1700000000:invalid_forged_hmac_signature",
        )
    assert "Invalid, forged, or expired session token" in str(exc_info.value)


def test_mismatched_session_token_rejected_for_production_restore(disposable_environment):
    """Safety Gate 3: Ensure session token identity mismatch is rejected for production restore."""
    env = disposable_environment
    res = create_backup(
        output_dir=env["backups_dir"],
        data_dir=env["data_dir"],
        db_path=env["db_path"],
        artifacts_dir=env["artifacts_dir"],
        knowledge_dir=env["knowledge_dir"],
        evidence_dir=env["evidence_dir"],
    )
    arc = res["archive_path"]

    # Legitimate token for user "attacker", attempting to claim operator_id="admin"
    attacker_token = create_session_token("attacker")
    with pytest.raises(PermissionError) as exc_info:
        restore_backup(
            archive_path=arc,
            target_data_dir=settings.data_dir,
            allow_production_overwrite=True,
            operator_id="admin",
            session_token=attacker_token,
        )
    assert "does not match requested operator" in str(exc_info.value)


def test_authenticated_operator_authorized_for_production_dry_run(disposable_environment):
    """Safety Gate 4: Ensure authenticated operator with valid token can execute a production dry-run."""
    env = disposable_environment
    res = create_backup(
        output_dir=env["backups_dir"],
        data_dir=env["data_dir"],
        db_path=env["db_path"],
        artifacts_dir=env["artifacts_dir"],
        knowledge_dir=env["knowledge_dir"],
        evidence_dir=env["evidence_dir"],
    )
    arc = res["archive_path"]

    admin_token = create_session_token("admin")
    dry_run_res = restore_backup(
        archive_path=arc,
        target_data_dir=settings.data_dir,
        allow_production_overwrite=True,
        operator_id="admin",
        session_token=admin_token,
        dry_run=True,
    )
    assert dry_run_res["status"] == "dry_run_success"
    assert dry_run_res["dry_run"] is True
    assert dry_run_res["files_to_restore_count"] > 0


def test_archive_safety_rejects_symlink(tmp_path):
    """Safety Gate 5: Confirm archive verification and restore reject symbolic links."""
    import io
    bad_archive = tmp_path / "symlink_attack.tar.gz"

    with tarfile.open(bad_archive, "w:gz") as tar:
        # Valid manifest
        mf_data = json.dumps({
            "manifest_version": "1.0",
            "snapshot_id": "snp-symlink-001",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": 18,
            "components": {}
        }).encode("utf-8")
        ti_mf = tarfile.TarInfo(name="manifest.json")
        ti_mf.size = len(mf_data)
        tar.addfile(ti_mf, fileobj=io.BytesIO(mf_data))

        # Symbolic link entry
        ti_sym = tarfile.TarInfo(name="symlink_link.txt")
        ti_sym.type = tarfile.SYMTYPE
        ti_sym.linkname = "/etc/shadow"
        tar.addfile(ti_sym)

    v_res = verify_backup(bad_archive)
    assert v_res["valid"] is False
    assert any("Symbolic links are forbidden" in err for err in v_res["errors"])

    with pytest.raises(BackupVerificationError):
        restore_backup(archive_path=bad_archive, target_data_dir=tmp_path / "dest")


def test_archive_safety_rejects_hardlink(tmp_path):
    """Safety Gate 6: Confirm archive verification and restore reject hard links."""
    import io
    bad_archive = tmp_path / "hardlink_attack.tar.gz"

    with tarfile.open(bad_archive, "w:gz") as tar:
        mf_data = json.dumps({
            "manifest_version": "1.0",
            "snapshot_id": "snp-hardlink-001",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": 18,
            "components": {}
        }).encode("utf-8")
        ti_mf = tarfile.TarInfo(name="manifest.json")
        ti_mf.size = len(mf_data)
        tar.addfile(ti_mf, fileobj=io.BytesIO(mf_data))

        ti_lnk = tarfile.TarInfo(name="hardlink_link.txt")
        ti_lnk.type = tarfile.LNKTYPE
        ti_lnk.linkname = "database/qwen_collective.db"
        tar.addfile(ti_lnk)

    v_res = verify_backup(bad_archive)
    assert v_res["valid"] is False
    assert any("Hard links are forbidden" in err for err in v_res["errors"])

    with pytest.raises(BackupVerificationError):
        restore_backup(archive_path=bad_archive, target_data_dir=tmp_path / "dest")


def test_archive_safety_rejects_fifo_or_device(tmp_path):
    """Safety Gate 7: Confirm archive verification rejects FIFOs and special device files."""
    import io
    bad_archive = tmp_path / "device_attack.tar.gz"

    with tarfile.open(bad_archive, "w:gz") as tar:
        mf_data = json.dumps({
            "manifest_version": "1.0",
            "snapshot_id": "snp-device-001",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": 18,
            "components": {}
        }).encode("utf-8")
        ti_mf = tarfile.TarInfo(name="manifest.json")
        ti_mf.size = len(mf_data)
        tar.addfile(ti_mf, fileobj=io.BytesIO(mf_data))

        ti_fifo = tarfile.TarInfo(name="evil_fifo")
        ti_fifo.type = tarfile.FIFOTYPE
        tar.addfile(ti_fifo)

    v_res = verify_backup(bad_archive)
    assert v_res["valid"] is False
    assert any("unsupported file type" in err.lower() or "special device" in err.lower() for err in v_res["errors"])


def test_archive_safety_rejects_duplicate_members(tmp_path):
    """Safety Gate 8: Confirm archive verification rejects duplicate archive entries."""
    import io
    bad_archive = tmp_path / "duplicate_attack.tar.gz"

    with tarfile.open(bad_archive, "w:gz") as tar:
        mf_data = json.dumps({
            "manifest_version": "1.0",
            "snapshot_id": "snp-dup-001",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": 18,
            "components": {}
        }).encode("utf-8")
        ti_mf = tarfile.TarInfo(name="manifest.json")
        ti_mf.size = len(mf_data)
        tar.addfile(ti_mf, fileobj=io.BytesIO(mf_data))

        content1 = b"FIRST_VERSION"
        ti1 = tarfile.TarInfo(name="database/qwen_collective.db")
        ti1.size = len(content1)
        tar.addfile(ti1, fileobj=io.BytesIO(content1))

        content2 = b"SECOND_OVERWRITE_ATTEMPT"
        ti2 = tarfile.TarInfo(name="database/qwen_collective.db")
        ti2.size = len(content2)
        tar.addfile(ti2, fileobj=io.BytesIO(content2))

    v_res = verify_backup(bad_archive)
    assert v_res["valid"] is False
    assert any("Duplicate archive member" in err for err in v_res["errors"])


def test_archive_safety_rejects_oversized_file(tmp_path, monkeypatch):
    """Safety Gate 9: Confirm archive verification rejects single files exceeding max threshold."""
    import io
    monkeypatch.setattr("app.services.backup.MAX_SINGLE_FILE_BYTES", 500)
    bad_archive = tmp_path / "bomb_attack.tar.gz"

    with tarfile.open(bad_archive, "w:gz") as tar:
        mf_data = json.dumps({
            "manifest_version": "1.0",
            "snapshot_id": "snp-bomb-001",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": 18,
            "components": {}
        }).encode("utf-8")
        ti_mf = tarfile.TarInfo(name="manifest.json")
        ti_mf.size = len(mf_data)
        tar.addfile(ti_mf, fileobj=io.BytesIO(mf_data))

        # Member with payload exceeding 500 bytes limit
        payload = b"A" * 1000
        ti_bomb = tarfile.TarInfo(name="huge_file.bin")
        ti_bomb.size = len(payload)
        tar.addfile(ti_bomb, fileobj=io.BytesIO(payload))

    v_res = verify_backup(bad_archive)
    assert v_res["valid"] is False
    assert any("exceeds maximum permitted single-file size" in err for err in v_res["errors"])


def test_cross_component_consistency_detects_missing_cas(disposable_environment):
    """Safety Gate 10: Fail-closed verification when DB references a missing CAS artifact."""
    env = disposable_environment
    now = datetime.now(timezone.utc).isoformat()
    missing_key = "non_existent_cas_blob_12345.bin"

    # Insert artifact reference in DB with NO corresponding file in artifacts directory
    with sqlite3.connect(str(env["db_path"])) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        art_id = f"art-{uuid.uuid4().hex[:8]}"
        conn.execute(
            """INSERT INTO artifacts (
                id, display_name, artifact_type, storage_backend, storage_key, checksum_sha256,
                size_bytes, origin_project_id, current_version_number, created_by, created_at, updated_at
            ) VALUES (?, 'Missing CAS Artifact', 'dataset', 'local_filesystem', ?, 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855', 0, ?, 1, 'system', ?, ?)""",
            (art_id, missing_key, env["p1_id"], now, now),
        )
        conn.commit()

    with pytest.raises(BackupError) as exc_info:
        create_backup(
            output_dir=env["backups_dir"],
            data_dir=env["data_dir"],
            db_path=env["db_path"],
            artifacts_dir=env["artifacts_dir"],
            knowledge_dir=env["knowledge_dir"],
            evidence_dir=env["evidence_dir"],
        )
    assert "Referenced artifact CAS blob missing on disk" in str(exc_info.value)


def test_cross_component_consistency_detects_tampered_review_digest(disposable_environment):
    """Safety Gate 11: Fail-closed verification when a markdown note review digest does not match DB."""
    env = disposable_environment
    now = datetime.now(timezone.utc).isoformat()

    # Insert knowledge record with invalid digest
    with sqlite3.connect(str(env["db_path"])) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        rec_id = f"knw-{uuid.uuid4().hex[:8]}"
        conn.execute(
            """INSERT INTO knowledge_records (
                id, scope, project_id, subject, statement, knowledge_type,
                verification_status, trust_tier, reviewed_by, review_notes, created_at, updated_at
            ) VALUES (?, 'project', ?, 'Tampered Architecture', 'Tampered statement', 'fact', 'supported',
                    'reviewed', 'lead_architect', '[digest:badbeef123456789][human_reviewer:lead_architect]', ?, ?)""",
            (rec_id, env["p1_id"], now, now),
        )
        conn.commit()

    with pytest.raises(BackupError) as exc_info:
        create_backup(
            output_dir=env["backups_dir"],
            data_dir=env["data_dir"],
            db_path=env["db_path"],
            artifacts_dir=env["artifacts_dir"],
            knowledge_dir=env["knowledge_dir"],
            evidence_dir=env["evidence_dir"],
        )
    assert "review digest is invalid" in str(exc_info.value)


def test_snapshot_coordination_and_write_barrier():
    """Safety Gate 12: Verify snapshot barrier and write barrier coordination."""
    import threading
    import time

    # Initially no snapshot is active
    assert not SnapshotCoordination.is_snapshot_active()
    assert write_barrier(timeout_sec=0.5) is True

    # Enter snapshot barrier
    with snapshot_barrier():
        assert SnapshotCoordination.is_snapshot_active() is True

        # In concurrent thread, write_barrier should wait
        write_status = []
        def concurrent_writer():
            res = write_barrier(timeout_sec=0.1)
            write_status.append(res)

        t = threading.Thread(target=concurrent_writer)
        t.start()
        t.join()

        # Timed out because snapshot barrier was held
        assert write_status == [False]

    # After exiting barrier, snapshot is no longer active
    assert SnapshotCoordination.is_snapshot_active() is False
    assert write_barrier(timeout_sec=0.5) is True


def test_backup_retention_preserves_sole_recovery_point(tmp_path):
    """Safety Gate 13: Verify PowerShell retention logic never deletes the sole remaining recovery point."""
    import subprocess
    import sys

    backup_test_dir = tmp_path / "retention_test"
    backup_test_dir.mkdir(parents=True, exist_ok=True)

    # 1. Create one older backup file (30 days old)
    old_backup = backup_test_dir / "qwen_collective_backup_20260101_000000.tar.gz"
    old_backup.write_bytes(b"VALID_BACKUP_PAYLOAD")

    # Set file timestamp to 30 days ago
    past_timestamp = time.time() - (30 * 86400)
    os.utime(old_backup, (past_timestamp, past_timestamp))

    # 2. Create partial .part files
    part_file = backup_test_dir / "qwen_collective_backup_partial.tar.gz.part"
    part_file.write_bytes(b"INCOMPLETE_CHUNK")
    os.utime(part_file, (past_timestamp, past_timestamp))

    # Execute retention block in PowerShell
    ps_cmd = f"""
    $BackupDir = '{str(backup_test_dir).replace('\\', '/')}'
    $RetentionDays = 14
    $CutoffDate = (Get-Date).AddDays(-$RetentionDays)

    Get-ChildItem -Path $BackupDir -Filter "*.part" -ErrorAction SilentlyContinue | Where-Object {{ $_.CreationTime -lt (Get-Date).AddDays(-1) -or $_.LastWriteTime -lt (Get-Date).AddDays(-1) }} | Remove-Item -Force

    $ValidBackups = Get-ChildItem -Path $BackupDir -Filter "qwen_collective_backup_*.tar.gz" | Where-Object {{ $_.Extension -eq ".gz" -and -not $_.Name.EndsWith(".part") }} | Sort-Object CreationTime -Descending

    if ($ValidBackups.Count -gt 1) {{
        $EligibleForPrune = $ValidBackups | Select-Object -Skip 1
        $EligibleForPrune | Where-Object {{ $_.CreationTime -lt $CutoffDate -or $_.LastWriteTime -lt $CutoffDate }} | Remove-Item -Force
    }}
    """
    proc = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True)
    assert proc.returncode == 0

    # Old valid backup MUST be preserved
    assert old_backup.exists(), "Last valid recovery point must never be deleted!"
    # Partial file MUST be cleaned
    assert not part_file.exists(), ".part file must be removed by retention cleanup!"
