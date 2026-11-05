"""
Backup, Recovery, and Data Integrity Subsystem for Qwen Collective.
Provides atomic, logically consistent snapshots of:
1. SQLite database state (via sqlite3.Connection.backup API)
2. Content-Addressed Storage (CAS) artifact blobs and project deliverables
3. Persistent Markdown Knowledge Vault notes
4. Research evidence, run ledgers, and citation records
5. Versioned manifest with SHA-256 hashes and schema compatibility metadata

Security & Safety Guarantees:
- Backup operations are strictly read-only on live data.
- Atomic snapshot creation prevents partial or inconsistent archives.
- Path traversal rejection prevents directory escape vulnerabilities on restore.
- Checksums verified fail-closed: missing, corrupted, or tampered files abort restore.
- Live production data overwrite is blocked unless explicitly authorized by an operator.
- Zero secrets or credentials embedded in archives, manifests, or logs.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple, Union

from app.config import settings

logger = logging.getLogger("qc.backup")

MANIFEST_VERSION = "1.0"
SAFE_FILENAME_RE = re.compile(r"^[a-zA-Z0-9_\-\.]+$")


class BackupError(Exception):
    """Base exception for backup failures."""
    pass


class BackupVerificationError(BackupError):
    """Raised when a backup archive fails integrity or checksum verification."""
    pass


class RestoreError(BackupError):
    """Raised when restore operation fails or is rejected."""
    pass


class PathTraversalSecurityError(RestoreError):
    """Raised when archive contains path traversal attempts."""
    pass


class ArchiveSecurityError(RestoreError):
    """Raised when archive contains dangerous or malicious structures (symlinks, bombs, duplicates)."""
    pass


# Security limits for decompression and extraction
MAX_TOTAL_UNPACKED_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB
MAX_SINGLE_FILE_BYTES = 500 * 1024 * 1024          # 500 MB


class SnapshotCoordination:
    """
    Bounded application write barrier and snapshot coordination mechanism.
    Ensures logically consistent point-in-time snapshots across database and files.
    """
    _lock = threading.RLock()
    _cond = threading.Condition(_lock)
    _snapshot_active = False

    @classmethod
    @contextmanager
    def snapshot_barrier(cls, timeout: float = 15.0) -> Iterator[None]:
        """Context manager acquired during snapshot capture to coordinate concurrent writes/deletions."""
        with cls._cond:
            cls._snapshot_active = True
        try:
            yield
        finally:
            with cls._cond:
                cls._snapshot_active = False
                cls._cond.notify_all()

    @classmethod
    def write_barrier(cls, timeout_sec: float = 10.0) -> bool:
        """
        Bounded wait before mutating or deleting storage during active snapshot.
        Returns True if write may proceed, False if timed out waiting for active snapshot.
        """
        start = time.time()
        with cls._cond:
            while cls._snapshot_active:
                elapsed = time.time() - start
                remaining = timeout_sec - elapsed
                if remaining <= 0:
                    return False
                cls._cond.wait(timeout=min(remaining, 0.2))
            return True

    @classmethod
    def is_snapshot_active(cls) -> bool:
        with cls._lock:
            return cls._snapshot_active


def snapshot_barrier(timeout: float = 15.0):
    """Convenience context manager for snapshot barrier."""
    return SnapshotCoordination.snapshot_barrier(timeout=timeout)


def write_barrier(timeout_sec: float = 10.0) -> bool:
    """Convenience function for write barrier."""
    return SnapshotCoordination.write_barrier(timeout_sec=timeout_sec)


def _sha256_file(filepath: Path) -> Tuple[str, int]:
    """Compute SHA-256 hash and size in bytes of a file."""
    h = hashlib.sha256()
    size = 0
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def _sha256_bytes(data: bytes) -> str:
    """Compute SHA-256 of bytes."""
    return hashlib.sha256(data).hexdigest()


def _validate_tar_member_safety(member: tarfile.TarInfo) -> None:
    """
    Verify member path and type do not attempt path traversal, directory escape, or system abuse:
    - Rejects absolute paths (leading / or \\, drive letters like C:)
    - Rejects paths containing '..'
    - Rejects symlinks, hardlinks, character devices, block devices, and FIFOs
    - Rejects excessive single-file size
    """
    norm = member.name.replace("\\", "/").strip()
    if norm.startswith("/") or norm.startswith("\\"):
        raise PathTraversalSecurityError(f"Security violation: Archive member '{member.name}' is an absolute path.")
    if len(norm) > 1 and norm[1] == ":":
        raise PathTraversalSecurityError(f"Security violation: Archive member '{member.name}' contains a drive letter.")
    parts = Path(norm).parts
    if any(p in ("..", "") for p in parts):
        raise PathTraversalSecurityError(f"Security violation: Archive member '{member.name}' violates path traversal safety rules.")

    # Rejection of symlinks, hardlinks, device files, FIFOs
    if member.issym():
        raise ArchiveSecurityError(f"Security violation: Archive member '{member.name}' is a symbolic link. Symbolic links are forbidden in recovery archives.")
    if member.islnk():
        raise ArchiveSecurityError(f"Security violation: Archive member '{member.name}' is a hard link. Hard links are forbidden in recovery archives.")
    if member.ischr() or member.isdev() or member.isfifo():
        raise ArchiveSecurityError(f"Security violation: Archive member '{member.name}' has an unsupported file type or is a special device file.")
    if not (member.isreg() or member.isdir()):
        raise ArchiveSecurityError(f"Security violation: Archive member '{member.name}' has an unsupported file type.")

    if member.size > MAX_SINGLE_FILE_BYTES:
        raise ArchiveSecurityError(
            f"Security violation: Member '{member.name}' exceeds maximum permitted single-file size "
            f"({member.size} > {MAX_SINGLE_FILE_BYTES} bytes)."
        )


def _is_safe_tar_member_path(member_name: str) -> bool:
    """Verify string path does not attempt path traversal or directory escape."""
    norm = member_name.replace("\\", "/").strip()
    if norm.startswith("/") or norm.startswith("\\"):
        return False
    if len(norm) > 1 and norm[1] == ":":
        return False
    parts = Path(norm).parts
    if any(p in ("..", "") for p in parts):
        return False
    return True


def get_configured_paths(
    data_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
    artifacts_dir: Optional[Path] = None,
    knowledge_dir: Optional[Path] = None,
    evidence_dir: Optional[Path] = None,
) -> Dict[str, Path]:
    """Return verified dictionary of active data storage paths."""
    base_data = (data_dir or settings.data_dir).resolve()
    base_db = (db_path or settings.db_path).resolve()
    base_artifacts = (artifacts_dir or settings.artifacts_dir).resolve()
    base_knowledge = (knowledge_dir or settings.knowledge_dir).resolve()
    base_evidence = (evidence_dir or settings.evidence_dir).resolve()

    return {
        "data_dir": base_data,
        "db_path": base_db,
        "artifacts_dir": base_artifacts,
        "knowledge_dir": base_knowledge,
        "evidence_dir": base_evidence,
    }


def query_db_record_counts(db_path: Path) -> Dict[str, int]:
    """Gather record counts across major tables for manifest metadata."""
    if not db_path.exists() or db_path.stat().st_size == 0:
        return {}

    counts: Dict[str, int] = {}
    candidate_tables = [
        "projects",
        "tasks",
        "artifacts",
        "artifact_versions",
        "deliverables",
        "knowledge_records",
        "knowledge_promotions",
        "research_runs",
        "research_sources",
        "research_claims",
        "research_evidence",
        "orchestration_approvals",
        "orchestration_runs",
        "users",
    ]

    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=10.0)
        try:
            cursor = conn.cursor()
            existing_tables = {
                r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
            }
            for tbl in candidate_tables:
                if tbl in existing_tables:
                    try:
                        cursor.execute(f"SELECT COUNT(*) FROM {tbl};")
                        counts[tbl] = cursor.fetchone()[0]
                    except Exception as e:
                        logger.debug("Failed to count %s: %s", tbl, e)
        finally:
            conn.close()
    except Exception as e:
        logger.warning("Could not read record counts from %s: %s", db_path, e)

    return counts


def create_backup(
    output_dir: Optional[Union[str, Path]] = None,
    data_dir: Optional[Union[str, Path]] = None,
    db_path: Optional[Union[str, Path]] = None,
    artifacts_dir: Optional[Union[str, Path]] = None,
    knowledge_dir: Optional[Union[str, Path]] = None,
    evidence_dir: Optional[Union[str, Path]] = None,
    operator_id: str = "admin",
    comment: str = "",
) -> Dict[str, Any]:
    """
    Create an atomic, complete, isolated backup archive (.tar.gz).
    
    Guarantees:
    - Read-only on live application data.
    - Uses sqlite3.Connection.backup() to produce a consistent SQLite snapshot.
    - Bundles CAS artifacts, Knowledge Vault notes, and research evidence.
    - Creates versioned manifest.json with SHA-256 hashes of all components.
    - Atomically moves completed archive to prevent partial/corrupted archives.
    - Never overwrites an existing snapshot file.
    """
    paths = get_configured_paths(
        data_dir=Path(data_dir) if data_dir else None,
        db_path=Path(db_path) if db_path else None,
        artifacts_dir=Path(artifacts_dir) if artifacts_dir else None,
        knowledge_dir=Path(knowledge_dir) if knowledge_dir else None,
        evidence_dir=Path(evidence_dir) if evidence_dir else None,
    )

    src_db = paths["db_path"]
    if not src_db.exists() or src_db.stat().st_size == 0:
        raise BackupError(f"Cannot backup non-existent or empty database at: {src_db}")

    backup_dest_dir = Path(output_dir or (paths["data_dir"] / "backups")).resolve()
    backup_dest_dir.mkdir(parents=True, exist_ok=True)

    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    snapshot_id = f"snp-{timestamp_str}-{uuid.uuid4().hex[:8]}"
    archive_filename = f"qwen_collective_backup_{snapshot_id}.tar.gz"
    final_archive_path = backup_dest_dir / archive_filename

    if final_archive_path.exists():
        raise BackupError(f"Backup snapshot archive already exists at {final_archive_path}")

    # Stage inside a temporary directory
    stage_dir = Path(tempfile.mkdtemp(prefix=f"qc_backup_stage_{snapshot_id}_"))
    part_archive_path = backup_dest_dir / f"{archive_filename}.part"

    try:
        stage_db_dir = stage_dir / "database"
        stage_db_dir.mkdir(parents=True, exist_ok=True)
        staged_db_path = stage_db_dir / "qwen_collective.db"

        # 1. Atomic SQLite Snapshot via online backup API under snapshot barrier
        with SnapshotCoordination.snapshot_barrier():
            logger.info("Performing online SQLite backup from %s to %s", src_db, staged_db_path)
            src_conn = sqlite3.connect(f"file:{src_db.as_posix()}?mode=ro", uri=True, timeout=30.0)
            try:
                # Check source integrity
                src_cur = src_conn.cursor()
                src_cur.execute("PRAGMA user_version;")
                schema_version = src_cur.fetchone()[0]

                dst_conn = sqlite3.connect(str(staged_db_path))
                try:
                    src_conn.backup(dst_conn)
                finally:
                    dst_conn.close()
            finally:
                src_conn.close()

            # Verify staged database snapshot integrity
            verify_conn = sqlite3.connect(str(staged_db_path))
            try:
                verify_conn.execute("PRAGMA journal_mode = DELETE;")
                v_cur = verify_conn.cursor()
                ic = v_cur.execute("PRAGMA integrity_check;").fetchone()
                if not ic or ic[0].lower() != "ok":
                    raise BackupError(f"Staged database failed integrity check: {ic}")
                fk = v_cur.execute("PRAGMA foreign_key_check;").fetchall()
                if fk:
                    logger.warning("Foreign key check on backup snapshot noted unresolved keys: %s", fk[:5])
            finally:
                verify_conn.close()

            # Clean up any ephemeral SQLite helper files in stage directory
            for ephem in stage_db_dir.glob("*.db-*"):
                try:
                    ephem.unlink()
                except OSError:
                    pass

            # Cross-Component Referential Consistency Verification
            with sqlite3.connect(str(staged_db_path)) as chk_conn:
                chk_conn.row_factory = sqlite3.Row
                chk_cur = chk_conn.cursor()
                existing_tables = {
                    r[0] for r in chk_cur.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
                }

                # Verify all referenced artifact versions exist on disk and match expected checksums
                if "artifact_versions" in existing_tables:
                    art_rows = chk_cur.execute(
                        "SELECT artifact_id, version_number, storage_key, checksum_sha256 FROM artifact_versions;"
                    ).fetchall()
                    for a_row in art_rows:
                        s_key = a_row["storage_key"]
                        exp_sha = a_row["checksum_sha256"]
                        cand_path = (paths["artifacts_dir"] / s_key).resolve()
                        if not cand_path.is_file():
                            raise BackupError(
                                f"Referenced artifact CAS blob missing on disk: '{s_key}' (artifact version '{a_row['artifact_id']}v{a_row['version_number']}'). "
                                f"Snapshot aborted to preserve cross-component referential integrity."
                            )
                        act_sha, _ = _sha256_file(cand_path)
                        if act_sha.lower() != exp_sha.strip().lower():
                            raise BackupError(
                                f"Cross-component inconsistency: Artifact version '{a_row['artifact_id']}v{a_row['version_number']}' "
                                f"checksum mismatch between database ({exp_sha}) and disk ({act_sha})."
                            )

                if "artifacts" in existing_tables:
                    top_art_rows = chk_cur.execute(
                        "SELECT id, storage_key, checksum_sha256 FROM artifacts WHERE storage_key IS NOT NULL AND storage_key != '';"
                    ).fetchall()
                    for a_row in top_art_rows:
                        s_key = a_row["storage_key"]
                        exp_sha = a_row["checksum_sha256"]
                        cand_path = (paths["artifacts_dir"] / s_key).resolve()
                        if not cand_path.is_file():
                            raise BackupError(
                                f"Referenced artifact CAS blob missing on disk: '{s_key}' (artifact '{a_row['id']}'). "
                                f"Snapshot aborted to preserve cross-component referential integrity."
                            )
                        if exp_sha:
                            act_sha, _ = _sha256_file(cand_path)
                            if act_sha.lower() != exp_sha.strip().lower():
                                raise BackupError(
                                    f"Cross-component inconsistency: Artifact '{a_row['id']}' checksum mismatch "
                                    f"between database ({exp_sha}) and disk ({act_sha})."
                                )

                # Verify Knowledge Review Digests
                if "knowledge_records" in existing_tables:
                    kn_rows = chk_cur.execute(
                        "SELECT id, subject, statement, reviewed_by, review_notes FROM knowledge_records;"
                    ).fetchall()
                    for k_row in kn_rows:
                        notes = k_row["review_notes"] or ""
                        if "[digest:" in notes:
                            m = re.search(r"\[digest:([a-fA-F0-9]{16,64})\]", notes)
                            if m:
                                recorded_digest = m.group(1).lower()
                                stmt_bytes = f"{k_row['subject']}:::{k_row['statement']}".encode("utf-8")
                                expected_full = hashlib.sha256(stmt_bytes).hexdigest().lower()
                                if not expected_full.startswith(recorded_digest):
                                    raise BackupError(
                                        f"Cross-component inconsistency: Knowledge record '{k_row['id']}' review digest "
                                        f"is invalid. Content '{k_row['subject']}' was modified without updating review attribution."
                                    )

                # Verify Lineage Referential Integrity
                if "artifact_lineage" in existing_tables and "artifacts" in existing_tables:
                    broken_lineage = chk_cur.execute(
                        """SELECT l.id, l.source_artifact_id
                           FROM artifact_lineage l
                           LEFT JOIN artifacts a ON a.id = l.source_artifact_id
                           WHERE a.id IS NULL;"""
                    ).fetchall()
                    if broken_lineage:
                        raise BackupError(
                            f"Cross-component inconsistency: Broken artifact lineage references detected: "
                            f"{[r['id'] for r in broken_lineage]}"
                        )

            # 2. Gather record counts
            record_counts = query_db_record_counts(staged_db_path)

            # 3. Copy artifacts (CAS blobs and deliverables)
            stage_artifacts_dir = stage_dir / "artifacts"
            stage_artifacts_dir.mkdir(parents=True, exist_ok=True)
            src_artifacts = paths["artifacts_dir"]
            if src_artifacts.exists():
                for root, _, files in os.walk(src_artifacts):
                    for f in files:
                        if f.endswith(("-shm", "-wal", "-journal")):
                            continue
                        src_f = Path(root) / f
                        rel_f = src_f.relative_to(src_artifacts)
                        dst_f = stage_artifacts_dir / rel_f
                        dst_f.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_f, dst_f)

            # 4. Copy Knowledge Vault notes
            stage_knowledge_dir = stage_dir / "knowledge"
            stage_knowledge_dir.mkdir(parents=True, exist_ok=True)
            src_knowledge = paths["knowledge_dir"]
            if src_knowledge.exists():
                for root, _, files in os.walk(src_knowledge):
                    for f in files:
                        if f.endswith(("-shm", "-wal", "-journal")):
                            continue
                        src_f = Path(root) / f
                        rel_f = src_f.relative_to(src_knowledge)
                        dst_f = stage_knowledge_dir / rel_f
                        dst_f.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_f, dst_f)

            # 5. Copy Research Evidence
            stage_evidence_dir = stage_dir / "evidence"
            stage_evidence_dir.mkdir(parents=True, exist_ok=True)
            src_evidence = paths["evidence_dir"]
            if src_evidence.exists():
                for root, _, files in os.walk(src_evidence):
                    for f in files:
                        if f.endswith(("-shm", "-wal", "-journal")):
                            continue
                        src_f = Path(root) / f
                        rel_f = src_f.relative_to(src_evidence)
                        dst_f = stage_evidence_dir / rel_f
                        dst_f.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_f, dst_f)

        # 6. Compute SHA-256 for all staged files
        components: Dict[str, Dict[str, Any]] = {}
        for root, _, files in os.walk(stage_dir):
            for f in files:
                if f.endswith(("-shm", "-wal", "-journal")):
                    continue
                p = Path(root) / f
                rel_path = p.relative_to(stage_dir).as_posix()
                if rel_path == "manifest.json":
                    continue
                sha256_hex, size_bytes = _sha256_file(p)
                c_type = rel_path.split("/")[0]
                components[rel_path] = {
                    "sha256": sha256_hex,
                    "size_bytes": size_bytes,
                    "component_type": c_type,
                }

        # 7. Generate Manifest
        manifest = {
            "manifest_version": MANIFEST_VERSION,
            "snapshot_id": snapshot_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "app_name": settings.app_name,
            "app_version": settings.app_version,
            "schema_version": schema_version,
            "operator_id": operator_id,
            "comment": comment,
            "status": "COMPLETED",
            "source_paths": {
                "db_path": str(src_db),
                "artifacts_dir": str(src_artifacts),
                "knowledge_dir": str(src_knowledge),
                "evidence_dir": str(src_evidence),
            },
            "record_counts": record_counts,
            "components": components,
        }

        manifest_path = stage_dir / "manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as mf:
            json.dump(manifest, mf, indent=2, sort_keys=True)

        # 8. Package into .tar.gz archive atomically
        with tarfile.open(part_archive_path, "w:gz") as tar:
            # Add manifest first
            tar.add(str(manifest_path), arcname="manifest.json")
            # Add all other staged files
            for rel_k in sorted(components.keys()):
                tar.add(str(stage_dir / rel_k), arcname=rel_k)

        # Atomic rename .part to final
        part_archive_path.replace(final_archive_path)
        logger.info("Backup archive successfully written: %s (%d bytes)", final_archive_path, final_archive_path.stat().st_size)

        return {
            "status": "success",
            "snapshot_id": snapshot_id,
            "archive_path": str(final_archive_path),
            "archive_filename": archive_filename,
            "size_bytes": final_archive_path.stat().st_size,
            "schema_version": schema_version,
            "created_at": manifest["created_at"],
            "operator_id": operator_id,
            "component_count": len(components),
            "record_counts": record_counts,
        }

    except Exception as e:
        logger.exception("Backup failed: %s", e)
        if part_archive_path.exists():
            try:
                part_archive_path.unlink()
            except OSError:
                pass
        raise BackupError(f"Backup creation failed: {e}") from e

    finally:
        if stage_dir.exists():
            shutil.rmtree(stage_dir, ignore_errors=True)


def inspect_backup(archive_path: Union[str, Path]) -> Dict[str, Any]:
    """
    Inspect a backup archive and read its manifest without extracting to disk.
    
    Returns parsed manifest and component summary.
    """
    arc = Path(archive_path).resolve()
    if not arc.exists() or not arc.is_file():
        raise BackupError(f"Backup archive does not exist at: {arc}")

    try:
        with tarfile.open(arc, "r:*") as tar:
            try:
                mf_member = tar.getmember("manifest.json")
            except KeyError:
                raise BackupVerificationError(f"Archive missing required 'manifest.json': {arc}")

            f = tar.extractfile(mf_member)
            if not f:
                raise BackupVerificationError(f"Cannot read manifest.json from archive: {arc}")
            manifest = json.loads(f.read().decode("utf-8"))

            members = tar.getnames()
            return {
                "archive_path": str(arc),
                "archive_size_bytes": arc.stat().st_size,
                "snapshot_id": manifest.get("snapshot_id"),
                "created_at": manifest.get("created_at"),
                "app_version": manifest.get("app_version"),
                "schema_version": manifest.get("schema_version"),
                "operator_id": manifest.get("operator_id"),
                "comment": manifest.get("comment"),
                "status": manifest.get("status"),
                "manifest_version": manifest.get("manifest_version"),
                "record_counts": manifest.get("record_counts", {}),
                "component_count": len(manifest.get("components", {})),
                "archive_member_count": len(members),
                "components": manifest.get("components", {}),
            }
    except tarfile.TarError as e:
        raise BackupVerificationError(f"Corrupt or unreadable tar archive: {e}") from e


def verify_backup(archive_path: Union[str, Path]) -> Dict[str, Any]:
    """
    Verify complete integrity of a backup archive:
    1. Validates tar structure and member paths (rejects path traversal).
    2. Validates manifest schema and required fields.
    3. Computes SHA-256 for all included files and validates against manifest.
    4. Confirms no extra unmanifested files and no missing manifested files.
    5. Extracts SQLite snapshot to temp buffer and executes integrity check.
    
    Returns structured verification result with valid=True/False and error list.
    """
    arc = Path(archive_path).resolve()
    errors: List[str] = []
    warnings: List[str] = []

    if not arc.exists():
        return {
            "valid": False,
            "archive_path": str(arc),
            "errors": [f"Archive does not exist: {arc}"],
            "warnings": [],
        }

    try:
        with tarfile.open(arc, "r:*") as tar:
            members = tar.getmembers()
            seen_names: Set[str] = set()
            total_unpacked = 0

            # 1. Structural, path traversal, member type, and size checks on all members
            for m in members:
                try:
                    _validate_tar_member_safety(m)
                except (PathTraversalSecurityError, ArchiveSecurityError) as sec_err:
                    errors.append(str(sec_err))

                norm_name = m.name.replace("\\", "/").strip()
                if norm_name in seen_names:
                    errors.append(f"Security violation: Duplicate archive member '{m.name}' detected.")
                seen_names.add(norm_name)

                total_unpacked += m.size
                if total_unpacked > MAX_TOTAL_UNPACKED_BYTES:
                    errors.append(
                        f"Security violation: Archive total unpacked size exceeds maximum permitted limit "
                        f"({total_unpacked} > {MAX_TOTAL_UNPACKED_BYTES} bytes)."
                    )

            if errors:
                return {
                    "valid": False,
                    "archive_path": str(arc),
                    "errors": errors,
                    "warnings": warnings,
                }

            # 2. Extract and parse manifest.json
            try:
                mf_member = tar.getmember("manifest.json")
            except KeyError:
                return {
                    "valid": False,
                    "archive_path": str(arc),
                    "errors": ["Archive missing required 'manifest.json'."],
                    "warnings": warnings,
                }

            mf_file = tar.extractfile(mf_member)
            if not mf_file:
                return {
                    "valid": False,
                    "archive_path": str(arc),
                    "errors": ["Unable to read 'manifest.json' from archive."],
                    "warnings": warnings,
                }

            try:
                manifest = json.loads(mf_file.read().decode("utf-8"))
            except Exception as e:
                return {
                    "valid": False,
                    "archive_path": str(arc),
                    "errors": [f"Malformed manifest JSON: {e}"],
                    "warnings": warnings,
                }

            # Required manifest fields
            req_fields = ["manifest_version", "snapshot_id", "created_at", "schema_version", "components"]
            for rf in req_fields:
                if rf not in manifest:
                    errors.append(f"Manifest missing required key: '{rf}'")

            if errors:
                return {
                    "valid": False,
                    "archive_path": str(arc),
                    "errors": errors,
                    "warnings": warnings,
                }

            components: Dict[str, Dict[str, Any]] = manifest.get("components", {})
            tar_members_map = {m.name: m for m in members if not m.isdir()}

            # 3. Verify each component in manifest
            for rel_path, comp_info in components.items():
                if rel_path not in tar_members_map:
                    errors.append(f"Missing component: Manifest lists '{rel_path}', but member was not found in archive.")
                    continue

                member = tar_members_map[rel_path]
                f = tar.extractfile(member)
                if not f:
                    errors.append(f"Cannot read archive member '{rel_path}' for verification.")
                    continue

                # Stream and compute sha256
                h = hashlib.sha256()
                byte_count = 0
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    h.update(chunk)
                    byte_count += len(chunk)

                computed_sha256 = h.hexdigest()
                expected_sha256 = comp_info.get("sha256", "").strip().lower()
                expected_size = comp_info.get("size_bytes")

                if computed_sha256 != expected_sha256:
                    errors.append(
                        f"Checksum mismatch on '{rel_path}': expected {expected_sha256}, computed {computed_sha256}."
                    )
                if expected_size is not None and byte_count != expected_size:
                    errors.append(
                        f"Size mismatch on '{rel_path}': expected {expected_size} bytes, computed {byte_count} bytes."
                    )

            # Check for extra files in archive not in manifest
            for mem_name in tar_members_map:
                if mem_name == "manifest.json":
                    continue
                if mem_name not in components:
                    warnings.append(f"Archive contains unmanifested file: '{mem_name}'")

            # 4. Extract SQLite snapshot to verify integrity
            db_member_name = "database/qwen_collective.db"
            if db_member_name in tar_members_map:
                with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
                    tmp_db_path = Path(tmp_db.name)
                try:
                    f = tar.extractfile(tar_members_map[db_member_name])
                    if f:
                        with open(tmp_db_path, "wb") as out_f:
                            shutil.copyfileobj(f, out_f)

                        db_conn = sqlite3.connect(str(tmp_db_path))
                        try:
                            cur = db_conn.cursor()
                            ic = cur.execute("PRAGMA integrity_check;").fetchone()
                            if not ic or ic[0].lower() != "ok":
                                errors.append(f"Restored SQLite snapshot failed PRAGMA integrity_check: {ic}")
                            cur.execute("PRAGMA user_version;")
                            actual_ver = cur.fetchone()[0]
                            if actual_ver != manifest["schema_version"]:
                                errors.append(
                                    f"Schema version mismatch: manifest claims {manifest['schema_version']}, "
                                    f"restored DB reports {actual_ver}."
                                )
                        finally:
                            db_conn.close()
                except Exception as e:
                    errors.append(f"Failed to verify restored database structure: {e}")
                finally:
                    if tmp_db_path.exists():
                        try:
                            tmp_db_path.unlink()
                        except OSError:
                            pass
            else:
                errors.append("Archive missing primary database member 'database/qwen_collective.db'.")

            return {
                "valid": len(errors) == 0,
                "archive_path": str(arc),
                "snapshot_id": manifest.get("snapshot_id"),
                "schema_version": manifest.get("schema_version"),
                "created_at": manifest.get("created_at"),
                "component_count": len(components),
                "errors": errors,
                "warnings": warnings,
            }

    except Exception as e:
        return {
            "valid": False,
            "archive_path": str(arc),
            "errors": [f"Archive verification exception: {e}"],
            "warnings": warnings,
        }


def restore_backup(
    archive_path: Union[str, Path],
    target_data_dir: Union[str, Path],
    dry_run: bool = False,
    allow_production_overwrite: bool = False,
    operator_id: Optional[str] = None,
    session_token: Optional[str] = None,
    operator_authority: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Restore an isolated backup archive into target_data_dir.
    
    Guarantees:
    - Pre-flight verifies archive integrity (fail-closed if any error or mismatch).
    - Prevents overwriting active production data directory without explicit operator authorization
      and independent cryptographic authentication (session token or signed OperatorAuthority).
    - Prevents path traversal directory escape attacks, symlinks, special files, and zip bombs.
    - Post-restore verifies SQLite database and all restored files.
    - Dry-run mode produces full execution plan without touching target directory.
    """
    arc = Path(archive_path).resolve()
    target_dir = Path(target_data_dir).resolve()

    # 1. Verify archive before anything else (Fail-closed)
    verification = verify_backup(arc)
    if not verification["valid"]:
        raise BackupVerificationError(
            f"Restore rejected: Archive failed verification with errors: {'; '.join(verification['errors'])}"
        )

    # 2. Production directory safety guard
    live_production_dir = settings.data_dir.resolve()
    if target_dir == live_production_dir:
        if not allow_production_overwrite:
            raise PermissionError(
                "CRITICAL SAFETY VIOLATION: Restoration into the active production directory is forbidden "
                "without explicit operator authorization (allow_production_overwrite=True)."
            )

        # Authenticate operator independently: unauthenticated string IDs are strictly rejected
        authenticated_operator: Optional[str] = None
        if operator_authority is not None:
            from app.services.knowledge_store import validate_operator_authority
            try:
                op_auth = validate_operator_authority(operator_authority, operation="restore")
                authenticated_operator = op_auth.principal
            except Exception as e:
                raise PermissionError(
                    f"CRITICAL SECURITY VIOLATION: Operator authority validation failed for production restore: {e}"
                ) from e
        elif session_token is not None:
            from app.auth import verify_session_token
            authenticated_operator = verify_session_token(session_token)
            if not authenticated_operator:
                raise PermissionError(
                    "CRITICAL SECURITY VIOLATION: Invalid, forged, or expired session token. "
                    "Production restoration rejected."
                )
            if operator_id and operator_id != authenticated_operator:
                raise PermissionError(
                    f"CRITICAL SECURITY VIOLATION: Session token identity '{authenticated_operator}' "
                    f"does not match requested operator '{operator_id}'."
                )
        else:
            raise PermissionError(
                "CRITICAL SECURITY VIOLATION: Unauthorized production restore attempt. "
                "Restoration into the active production directory requires an independently authenticated "
                "operator session token (session_token) or cryptographically signed OperatorAuthority. "
                "Plain string operator identities are strictly rejected."
            )

        logger.warning(
            "RESTORE TO PRODUCTION AUTHORIZED by independently authenticated operator '%s'. Target: %s",
            authenticated_operator,
            target_dir,
        )

    inspect_data = inspect_backup(arc)
    manifest_components = inspect_data.get("components", {})

    # 3. Dry-run execution
    if dry_run:
        plan_files = []
        for rel_k in sorted(manifest_components.keys()):
            plan_files.append({
                "source_in_archive": rel_k,
                "target_destination": str(target_dir / rel_k),
                "size_bytes": manifest_components[rel_k].get("size_bytes"),
                "sha256": manifest_components[rel_k].get("sha256"),
            })

        return {
            "status": "dry_run_success",
            "dry_run": True,
            "target_data_dir": str(target_dir),
            "snapshot_id": inspect_data.get("snapshot_id"),
            "schema_version": inspect_data.get("schema_version"),
            "created_at": inspect_data.get("created_at"),
            "files_to_restore_count": len(plan_files),
            "files_to_restore": plan_files,
            "allow_production_overwrite": allow_production_overwrite,
            "verification_passed": True,
            "message": "Dry-run validation successful. Archive is valid and ready for restoration.",
        }

    # 4. Actual Restoration
    target_dir.mkdir(parents=True, exist_ok=True)
    temp_stage = Path(tempfile.mkdtemp(prefix=f"qc_restore_stage_{uuid.uuid4().hex[:8]}_"))

    try:
        with tarfile.open(arc, "r:*") as tar:
            seen_members: Set[str] = set()
            total_unpacked_bytes = 0

            # Safe extraction with path traversal and member type prevention
            for member in tar.getmembers():
                _validate_tar_member_safety(member)

                norm_name = member.name.replace("\\", "/").strip()
                if norm_name in seen_members:
                    raise ArchiveSecurityError(f"Security violation: Duplicate archive member '{member.name}' detected.")
                seen_members.add(norm_name)

                total_unpacked_bytes += member.size
                if total_unpacked_bytes > MAX_TOTAL_UNPACKED_BYTES:
                    raise ArchiveSecurityError(
                        f"Security violation: Total uncompressed archive size exceeds maximum permitted limit "
                        f"({total_unpacked_bytes} > {MAX_TOTAL_UNPACKED_BYTES} bytes)."
                    )

                dest_file = temp_stage / member.name
                dest_file_resolved = dest_file.resolve()
                if not dest_file_resolved.is_relative_to(temp_stage.resolve()):
                    raise PathTraversalSecurityError(
                        f"Target destination escapes staging directory: '{member.name}'"
                    )

                if member.isdir():
                    dest_file.mkdir(parents=True, exist_ok=True)
                else:
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    f_src = tar.extractfile(member)
                    if f_src:
                        with open(dest_file, "wb") as f_dst:
                            shutil.copyfileobj(f_src, f_dst)

        # Move restored components into target_dir
        # Database: database/qwen_collective.db -> target_dir / qwen_collective.db
        stage_db = temp_stage / "database" / "qwen_collective.db"
        if not stage_db.exists():
            raise RestoreError("Database file was not found in extracted archive.")

        final_db = target_dir / "qwen_collective.db"
        shutil.copy2(stage_db, final_db)

        # Artifacts
        stage_artifacts = temp_stage / "artifacts"
        final_artifacts = target_dir / "artifacts"
        final_artifacts.mkdir(parents=True, exist_ok=True)
        if stage_artifacts.exists():
            for root, _, files in os.walk(stage_artifacts):
                for f in files:
                    src_f = Path(root) / f
                    rel = src_f.relative_to(stage_artifacts)
                    dst_f = final_artifacts / rel
                    dst_f.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_f, dst_f)

        # Knowledge
        stage_knowledge = temp_stage / "knowledge"
        final_knowledge = target_dir / "knowledge"
        final_knowledge.mkdir(parents=True, exist_ok=True)
        if stage_knowledge.exists():
            for root, _, files in os.walk(stage_knowledge):
                for f in files:
                    src_f = Path(root) / f
                    rel = src_f.relative_to(stage_knowledge)
                    dst_f = final_knowledge / rel
                    dst_f.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_f, dst_f)

        # Evidence
        stage_evidence = temp_stage / "evidence"
        final_evidence = target_dir / "evidence"
        final_evidence.mkdir(parents=True, exist_ok=True)
        if stage_evidence.exists():
            for root, _, files in os.walk(stage_evidence):
                for f in files:
                    src_f = Path(root) / f
                    rel = src_f.relative_to(stage_evidence)
                    dst_f = final_evidence / rel
                    dst_f.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_f, dst_f)

        # Manifest
        stage_manifest = temp_stage / "manifest.json"
        if stage_manifest.exists():
            shutil.copy2(stage_manifest, target_dir / "restored_manifest.json")

        # 5. Post-restore Integrity Verification on Destination
        db_conn = sqlite3.connect(str(final_db))
        try:
            cur = db_conn.cursor()
            ic = cur.execute("PRAGMA integrity_check;").fetchone()
            if not ic or ic[0].lower() != "ok":
                raise RestoreError(f"Restored target database failed PRAGMA integrity_check: {ic}")
            cur.execute("PRAGMA user_version;")
            final_version = cur.fetchone()[0]
        finally:
            db_conn.close()

        # Check all restored files match manifest hashes
        restored_files_count = 1  # qwen_collective.db
        total_restored_bytes = final_db.stat().st_size

        for rel_k, comp in manifest_components.items():
            if rel_k == "database/qwen_collective.db":
                dst_check = final_db
            elif rel_k.startswith("artifacts/"):
                dst_check = target_dir / rel_k
            elif rel_k.startswith("knowledge/"):
                dst_check = target_dir / rel_k
            elif rel_k.startswith("evidence/"):
                dst_check = target_dir / rel_k
            else:
                dst_check = target_dir / rel_k

            if not dst_check.exists():
                raise RestoreError(f"Post-restore check failed: Restored file '{dst_check}' missing.")

            calc_sha, calc_size = _sha256_file(dst_check)
            if calc_sha != comp["sha256"]:
                raise RestoreError(
                    f"Post-restore checksum mismatch on '{dst_check}': "
                    f"expected {comp['sha256']}, got {calc_sha}"
                )
            restored_files_count += 1
            total_restored_bytes += calc_size

        logger.info(
            "Restoration complete: %d files (%d bytes) restored to %s",
            restored_files_count,
            total_restored_bytes,
            target_dir,
        )

        return {
            "status": "restored",
            "target_data_dir": str(target_dir),
            "snapshot_id": inspect_data.get("snapshot_id"),
            "schema_version": final_version,
            "created_at": inspect_data.get("created_at"),
            "restored_files_count": restored_files_count,
            "total_bytes": total_restored_bytes,
            "database_integrity": "ok",
            "operator_id": operator_id or "system",
        }

    finally:
        if temp_stage.exists():
            shutil.rmtree(temp_stage, ignore_errors=True)


def list_backups(backup_dir: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
    """List and inspect all recovery points available in backup directory."""
    b_dir = Path(backup_dir or (settings.data_dir / "backups")).resolve()
    if not b_dir.exists():
        return []

    results = []
    for arc_file in sorted(b_dir.glob("qwen_collective_backup_*.tar.gz"), reverse=True):
        try:
            info = inspect_backup(arc_file)
            results.append(info)
        except Exception as e:
            results.append({
                "archive_path": str(arc_file),
                "status": "UNREADABLE",
                "error": str(e),
            })
    return results


def main() -> None:
    """CLI Entrypoint for Backup and Disaster Recovery Operations."""
    parser = argparse.ArgumentParser(description="Qwen Collective Backup & Disaster Recovery Utility")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # create
    create_p = subparsers.add_parser("create", help="Create a complete recoverable snapshot")
    create_p.add_argument("--output-dir", help="Destination directory for backup archive", default=None)
    create_p.add_argument("--data-dir", help="Source application data directory override", default=None)
    create_p.add_argument("--operator-id", help="Operator username or identifier", default="operator")
    create_p.add_argument("--comment", help="Optional comment for snapshot manifest", default="")

    # list
    list_p = subparsers.add_parser("list", help="List available recovery points")
    list_p.add_argument("--backup-dir", help="Backup directory to inspect", default=None)

    # inspect
    inspect_p = subparsers.add_parser("inspect", help="Inspect a backup archive manifest")
    inspect_p.add_argument("archive", help="Path to backup archive (.tar.gz)")

    # verify
    verify_p = subparsers.add_parser("verify", help="Verify integrity and checksums of backup archive")
    verify_p.add_argument("archive", help="Path to backup archive (.tar.gz)")

    # restore
    restore_p = subparsers.add_parser("restore", help="Restore backup into target destination")
    restore_p.add_argument("archive", help="Path to backup archive (.tar.gz)")
    restore_p.add_argument("--target-dir", required=True, help="Destination directory to restore data into")
    restore_p.add_argument("--dry-run", action="store_true", help="Simulate restore and print execution plan")
    restore_p.add_argument("--allow-production-overwrite", action="store_true", help="Explicitly authorize overwriting live production data directory")
    restore_p.add_argument("--operator-id", help="Operator identifier authorizing restoration", default="operator")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    try:
        if args.subcommand == "create":
            res = create_backup(
                output_dir=args.output_dir,
                data_dir=args.data_dir,
                operator_id=args.operator_id,
                comment=args.comment,
            )
            print(json.dumps(res, indent=2))

        elif args.subcommand == "list":
            backups = list_backups(backup_dir=args.backup_dir)
            print(json.dumps(backups, indent=2))

        elif args.subcommand == "inspect":
            info = inspect_backup(args.archive)
            print(json.dumps(info, indent=2))

        elif args.subcommand == "verify":
            v_res = verify_backup(args.archive)
            print(json.dumps(v_res, indent=2))
            if not v_res["valid"]:
                sys.exit(1)

        elif args.subcommand == "restore":
            r_res = restore_backup(
                archive_path=args.archive,
                target_data_dir=args.target_dir,
                dry_run=args.dry_run,
                allow_production_overwrite=args.allow_production_overwrite,
                operator_id=args.operator_id,
            )
            print(json.dumps(r_res, indent=2))

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
