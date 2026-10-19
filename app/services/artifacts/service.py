"""
Canonical Artifact Service for Qwen Collective (Unified Deliverables & Artifacts).
Manages:
- Universal artifact identity and immutable versions
- Physical storage via storage abstraction
- Project ownership, cross-project sharing grants, and access isolation
- Safe preview generation (text, markdown, CSV, JSON, code, images, PDF)
- One-click downloads with integrity verification
- Lineage tracking (derived_from, revised_from, summarizes, transforms, etc.)
- Reuse workflows (same project, another project, Create Project From This)
- Knowledge base extraction and impact tracking
"""
from __future__ import annotations

import csv
import io
import json
import logging
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.database import db_transaction, get_db_connection
from app.services.artifacts.storage import (
    DEFAULT_MAX_ARTIFACT_SIZE,
    LocalFilesystemStorageBackend,
    sanitize_filename,
    storage_backend,
)

logger = logging.getLogger("qwen_collective.artifacts.service")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _detect_artifact_type(filename: str, mime_type: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in (".md", ".markdown", ".txt", ".rst"):
        return "document"
    elif ext in (".csv", ".tsv", ".parquet", ".arrow"):
        return "dataset"
    elif ext in (".json", ".jsonl"):
        return "json"
    elif ext in (".py", ".sh", ".bash", ".js", ".ts", ".html", ".css", ".sql", ".rs", ".go", ".c", ".cpp"):
        return "code"
    elif ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
        return "image"
    elif ext in (".pdf", ".docx", ".epub"):
        return "report"
    elif ext in (".zip", ".tar", ".gz", ".7z"):
        return "archive"
    elif ext in (".log",):
        return "log"
    return "other"


def _guess_mime(filename: str) -> str:
    mime, _ = mimetypes.guess_type(filename)
    if mime:
        return mime
    ext = Path(filename).suffix.lower()
    custom = {
        ".md": "text/markdown",
        ".json": "application/json",
        ".csv": "text/csv",
        ".log": "text/plain",
        ".sh": "text/x-shellscript",
        ".py": "text/x-python",
    }
    return custom.get(ext, "application/octet-stream")


class ArtifactService:
    """Authoritative service for managing artifacts and deliverables."""

    def __init__(self, backend=None):
        self.storage = backend or storage_backend

    # -----------------------------------------------------------------------
    # Registration & Versioning
    # -----------------------------------------------------------------------

    def register_artifact(
        self,
        name: str,
        data: bytes,
        project_id: Optional[str] = None,
        description: str = "",
        artifact_type: Optional[str] = None,
        task_id: Optional[str] = None,
        orch_task_id: Optional[str] = None,
        worker_id: Optional[str] = None,
        model_id: Optional[str] = None,
        parent_artifact_id: Optional[str] = None,
        created_by: str = "system",
        expected_sha256: Optional[str] = None,
        visibility: str = "private",
        status: str = "available",
        validation_status: str = "valid",
        validation_detail: str = "",
        db_conn: Optional[sqlite3.Connection] = None,
    ) -> Dict[str, Any]:
        """
        Atomically register a new canonical artifact and its version 1.
        """
        import uuid
        art_id = f"art-{uuid.uuid4().hex[:16]}"
        safe_name = sanitize_filename(name)
        mime_type = _guess_mime(safe_name)
        atype = artifact_type or _detect_artifact_type(safe_name, mime_type)
        ext = Path(safe_name).suffix.lower()
        now = _now_iso()

        # Storage key layout: {project_id or 'global'}/{artifact_id}/v1_{safe_name}
        proj_prefix = project_id or "global"
        storage_key = f"{proj_prefix}/{art_id}/v1_{safe_name}"

        # Physically store bytes
        store_result = self.storage.store_bytes(
            storage_key=storage_key,
            data=data,
            expected_sha256=expected_sha256,
        )
        sha256 = store_result["checksum_sha256"]
        size_bytes = store_result["size_bytes"]

        ver_id = f"artver-{uuid.uuid4().hex[:16]}"

        def _execute_inserts(conn_handle):
            # 1. Insert Artifact record
            conn_handle.execute(
                """
                INSERT INTO artifacts (
                    id, display_name, description, artifact_type, mime_type, file_extension,
                    size_bytes, checksum_sha256, storage_backend, storage_key, current_version_number,
                    status, validation_status, validation_detail, knowledge_status, visibility,
                    retention_policy, origin_project_id, origin_task_id, origin_worker_id, origin_model_id,
                    parent_artifact_id, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, 'not_processed', ?, 'standard', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    art_id, safe_name, description, atype, mime_type, ext,
                    size_bytes, sha256, "local_filesystem", store_result["storage_key"],
                    status, validation_status, validation_detail, visibility,
                    project_id, task_id, worker_id, model_id, parent_artifact_id,
                    created_by, now, now,
                ),
            )

            # 2. Insert Artifact Version 1
            conn_handle.execute(
                """
                INSERT INTO artifact_versions (
                    id, artifact_id, version_number, storage_backend, storage_key,
                    size_bytes, checksum_sha256, change_description, producing_task_id,
                    producing_worker_id, producing_model_id, validation_status, created_by, created_at
                ) VALUES (?, ?, 1, ?, ?, ?, ?, 'Initial creation', ?, ?, ?, ?, ?, ?)
                """,
                (
                    ver_id, art_id, "local_filesystem", store_result["storage_key"],
                    size_bytes, sha256, task_id, worker_id, model_id, validation_status,
                    created_by, now,
                ),
            )

            # 3. Associate with project if project_id is given
            if project_id:
                link_id = f"apl-{uuid.uuid4().hex[:12]}"
                conn_handle.execute(
                    """
                    INSERT OR IGNORE INTO artifact_project_links (id, artifact_id, project_id, role, added_at, added_by)
                    VALUES (?, ?, ?, 'output', ?, ?)
                    """,
                    (link_id, art_id, project_id, now, created_by),
                )

            # 4. Associate with task if task_id is given
            if task_id:
                tlink_id = f"atl-{uuid.uuid4().hex[:12]}"
                conn_handle.execute(
                    """
                    INSERT INTO artifact_task_links (id, artifact_id, version_number, task_id, orch_task_id, direction, role, created_at)
                    VALUES (?, ?, 1, ?, ?, 'output', 'primary', ?)
                    """,
                    (tlink_id, art_id, task_id, orch_task_id, now),
                )

            # 5. Lineage from parent artifact if specified
            if parent_artifact_id:
                lin_id = f"lin-{uuid.uuid4().hex[:12]}"
                conn_handle.execute(
                    """
                    INSERT INTO artifact_lineage (
                        id, source_artifact_id, target_artifact_id, relationship_type,
                        source_version_number, target_version_number, context_description, created_at
                    ) VALUES (?, ?, ?, 'derived_from', 1, 1, 'Derived from parent artifact during registration', ?)
                    """,
                    (lin_id, parent_artifact_id, art_id, now),
                )

        if db_conn is not None:
            _execute_inserts(db_conn)
        else:
            with db_transaction() as conn:
                _execute_inserts(conn)

        logger.info("Registered canonical artifact %s (%s, %d bytes)", art_id, safe_name, size_bytes)
        return self.get_artifact(art_id, conn=db_conn)

    def create_artifact_version(
        self,
        artifact_id: str,
        data: bytes,
        change_description: str = "",
        task_id: Optional[str] = None,
        worker_id: Optional[str] = None,
        model_id: Optional[str] = None,
        created_by: str = "system",
        expected_sha256: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a new immutable version of an existing artifact.
        Preserves earlier versions and creates lineage link.
        """
        import uuid
        art = self.get_artifact(artifact_id)
        if not art:
            raise ValueError(f"Artifact '{artifact_id}' does not exist.")

        next_ver = art["current_version_number"] + 1
        safe_name = sanitize_filename(art["display_name"])
        proj_prefix = art["origin_project_id"] or "global"
        storage_key = f"{proj_prefix}/{artifact_id}/v{next_ver}_{safe_name}"

        store_result = self.storage.store_bytes(
            storage_key=storage_key,
            data=data,
            expected_sha256=expected_sha256,
        )
        sha256 = store_result["checksum_sha256"]
        size_bytes = store_result["size_bytes"]
        now = _now_iso()

        ver_id = f"artver-{uuid.uuid4().hex[:16]}"
        lin_id = f"lin-{uuid.uuid4().hex[:12]}"

        with db_transaction() as conn:
            # 1. Insert new version
            conn.execute(
                """
                INSERT INTO artifact_versions (
                    id, artifact_id, version_number, storage_backend, storage_key,
                    size_bytes, checksum_sha256, change_description, producing_task_id,
                    producing_worker_id, producing_model_id, validation_status, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'valid', ?, ?)
                """,
                (
                    ver_id, artifact_id, next_ver, "local_filesystem", store_result["storage_key"],
                    size_bytes, sha256, change_description or f"Version {next_ver}",
                    task_id, worker_id, model_id, created_by, now,
                ),
            )

            # 2. Update parent artifact current pointer
            conn.execute(
                """
                UPDATE artifacts
                SET current_version_number = ?,
                    size_bytes = ?,
                    checksum_sha256 = ?,
                    storage_key = ?,
                    status = 'available',
                    updated_at = ?
                WHERE id = ?
                """,
                (next_ver, size_bytes, sha256, store_result["storage_key"], now, artifact_id),
            )

            # 3. Record lineage relationship
            conn.execute(
                """
                INSERT INTO artifact_lineage (
                    id, source_artifact_id, target_artifact_id, relationship_type,
                    source_version_number, target_version_number, context_description, created_at
                ) VALUES (?, ?, ?, 'revised_from', ?, ?, ?, ?)
                """,
                (lin_id, artifact_id, artifact_id, art["current_version_number"], next_ver, change_description, now),
            )

            # 4. If produced by task, record task link
            if task_id:
                tlink_id = f"atl-{uuid.uuid4().hex[:12]}"
                conn.execute(
                    """
                    INSERT INTO artifact_task_links (id, artifact_id, version_number, task_id, orch_task_id, direction, role, created_at)
                    VALUES (?, ?, ?, ?, NULL, 'output', 'revision', ?)
                    """,
                    (tlink_id, artifact_id, next_ver, task_id, now),
                )

        logger.info("Created version %d for artifact %s (%d bytes)", next_ver, artifact_id, size_bytes)
        return self.get_artifact(artifact_id)

    # -----------------------------------------------------------------------
    # Retrieval & Inspection
    # -----------------------------------------------------------------------

    def get_artifact(self, artifact_id: str, conn: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
        own_conn = conn is None
        c = get_db_connection() if own_conn else conn
        try:
            row = c.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
            if not row:
                return None
            art = dict(row)

            # Fetch versions
            vers = c.execute(
                "SELECT * FROM artifact_versions WHERE artifact_id = ? ORDER BY version_number ASC",
                (artifact_id,),
            ).fetchall()
            art["versions"] = [dict(v) for v in vers]

            # Fetch linked projects
            projs = c.execute(
                """
                SELECT p.id, p.name, apl.role, apl.added_at
                FROM artifact_project_links apl
                JOIN projects p ON p.id = apl.project_id
                WHERE apl.artifact_id = ?
                ORDER BY apl.added_at ASC
                """,
                (artifact_id,),
            ).fetchall()
            art["projects"] = [dict(p) for p in projs]

            # Check if linked as primary artifact to a deliverable
            deliv = c.execute(
                "SELECT id, name, status, deliverable_type, project_id FROM deliverables WHERE primary_artifact_id = ?",
                (artifact_id,),
            ).fetchone()
            art["deliverable"] = dict(deliv) if deliv else None

            # Calculate physical file presence
            art["file_exists"] = self.storage.exists(art["storage_key"])

            return art
        finally:
            if own_conn:
                c.close()

    def get_artifact_version(self, artifact_id: str, version_number: int) -> Optional[Dict[str, Any]]:
        conn = get_db_connection()
        try:
            row = conn.execute(
                "SELECT * FROM artifact_versions WHERE artifact_id = ? AND version_number = ?",
                (artifact_id, version_number),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list_artifacts(
        self,
        project_id: Optional[str] = None,
        artifact_type: Optional[str] = None,
        worker_id: Optional[str] = None,
        status: Optional[str] = None,
        validation_status: Optional[str] = None,
        knowledge_status: Optional[str] = None,
        visibility: Optional[str] = None,
        search_query: Optional[str] = None,
        sort_by: str = "recent",
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        List artifacts with multi-criteria filtering and FTS5 search support.
        """
        conn = get_db_connection()
        try:
            conditions = []
            params = []

            if project_id:
                # Artifact is accessible if it was produced in project OR linked to project
                conditions.append(
                    """(
                        artifacts.origin_project_id = ?
                        OR artifacts.id IN (SELECT artifact_id FROM artifact_project_links WHERE project_id = ?)
                        OR artifacts.id IN (SELECT artifact_id FROM artifact_access_grants WHERE target_project_id = ? AND status = 'active')
                    )"""
                )
                params.extend([project_id, project_id, project_id])

            if artifact_type:
                conditions.append("artifacts.artifact_type = ?")
                params.append(artifact_type)

            if worker_id:
                conditions.append("artifacts.origin_worker_id = ?")
                params.append(worker_id)

            if status:
                conditions.append("artifacts.status = ?")
                params.append(status)

            if validation_status:
                conditions.append("artifacts.validation_status = ?")
                params.append(validation_status)

            if knowledge_status:
                conditions.append("artifacts.knowledge_status = ?")
                params.append(knowledge_status)

            if visibility:
                conditions.append("artifacts.visibility = ?")
                params.append(visibility)

            if search_query and search_query.strip():
                # Query FTS5
                clean_terms = [t for t in search_query.strip().split() if len(t) > 1]
                if clean_terms:
                    fts_clause = " OR ".join(f"{t}*" for t in clean_terms)
                    conditions.append(
                        "artifacts.id IN (SELECT artifact_id FROM artifacts_fts WHERE artifacts_fts MATCH ?)"
                    )
                    params.append(fts_clause)

            where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""

            order_sql = "ORDER BY artifacts.created_at DESC"
            if sort_by == "name":
                order_sql = "ORDER BY artifacts.display_name ASC"
            elif sort_by == "size":
                order_sql = "ORDER BY artifacts.size_bytes DESC"
            elif sort_by == "oldest":
                order_sql = "ORDER BY artifacts.created_at ASC"

            sql = f"""
                SELECT artifacts.*,
                       (SELECT count(*) FROM artifact_lineage WHERE source_artifact_id = artifacts.id) AS derivative_count,
                       (SELECT count(*) FROM artifact_project_links WHERE artifact_id = artifacts.id) AS project_link_count,
                       (SELECT name FROM projects WHERE id = artifacts.origin_project_id) AS origin_project_name
                FROM artifacts
                {where_sql}
                {order_sql}
                LIMIT ? OFFSET ?
            """
            params.extend([limit, offset])

            rows = conn.execute(sql, params).fetchall()
            result = []
            for r in rows:
                item = dict(r)
                item["file_exists"] = self.storage.exists(item["storage_key"])
                result.append(item)
            return result
        finally:
            conn.close()

    # -----------------------------------------------------------------------
    # Download & Safe Preview Generation
    # -----------------------------------------------------------------------

    def resolve_download(
        self,
        artifact_id: str,
        version_number: Optional[int] = None,
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Verify access, locate physical file, verify integrity checksum, and return download parameters.
        """
        art = self.get_artifact(artifact_id)
        if not art:
            raise FileNotFoundError(f"Artifact '{artifact_id}' not found.")

        # Check project access if project_id is given
        if project_id and not self.check_project_access(artifact_id, project_id):
            raise PermissionError(f"Project '{project_id}' is not authorized to access artifact '{artifact_id}'.")

        target_key = art["storage_key"]
        expected_sha = art["checksum_sha256"]
        ver_num = art["current_version_number"]

        if version_number and version_number != art["current_version_number"]:
            ver_record = self.get_artifact_version(artifact_id, version_number)
            if not ver_record:
                raise FileNotFoundError(f"Version {version_number} of artifact '{artifact_id}' not found.")
            target_key = ver_record["storage_key"]
            expected_sha = ver_record["checksum_sha256"]
            ver_num = version_number

        real_path = self.storage.get_real_path(target_key)
        matches, computed = self.storage.verify_integrity(target_key, expected_sha)
        if not matches:
            logger.error("Integrity check failed for artifact %s: expected %s, got %s", artifact_id, expected_sha, computed)
            raise ValueError(f"Integrity check failed: file checksum ({computed}) does not match recorded checksum ({expected_sha}).")

        return {
            "artifact_id": artifact_id,
            "version_number": ver_num,
            "display_name": art["display_name"],
            "mime_type": art["mime_type"],
            "size_bytes": real_path.stat().st_size,
            "checksum_sha256": expected_sha,
            "file_path": real_path,
        }

    def get_preview_data(
        self,
        artifact_id: str,
        version_number: Optional[int] = None,
        project_id: Optional[str] = None,
        max_bytes: int = 150000,
    ) -> Dict[str, Any]:
        """
        Generate a safe preview structure according to MIME / artifact type.
        Never executes scripts, code, or active HTML.
        """
        dl = self.resolve_download(artifact_id, version_number=version_number, project_id=project_id)
        path = dl["file_path"]
        mime = dl["mime_type"]
        ext = path.suffix.lower()

        preview: Dict[str, Any] = {
            "artifact_id": artifact_id,
            "version_number": dl["version_number"],
            "display_name": dl["display_name"],
            "mime_type": mime,
            "size_bytes": dl["size_bytes"],
            "preview_type": "binary",
            "content": None,
            "truncated": False,
        }

        # 1. Plain text / Markdown / JSON / Code / Logs
        is_textual = (
            mime.startswith("text/")
            or mime in ("application/json", "application/javascript", "application/xml")
            or ext in (".md", ".json", ".csv", ".txt", ".py", ".sh", ".log", ".yaml", ".yml", ".sql")
        )

        if is_textual:
            raw = path.read_bytes()
            if len(raw) > max_bytes:
                raw = raw[:max_bytes]
                preview["truncated"] = True

            text = raw.decode("utf-8", errors="replace")

            if mime == "application/json" or ext == ".json":
                preview["preview_type"] = "json"
                try:
                    parsed = json.loads(text)
                    preview["content"] = json.dumps(parsed, indent=2)
                except Exception:
                    preview["content"] = text
            elif ext == ".csv":
                preview["preview_type"] = "csv"
                try:
                    reader = csv.reader(io.StringIO(text))
                    rows = [row for i, row in enumerate(reader) if i < 30]
                    preview["content"] = {"rows": rows, "total_sample_rows": len(rows)}
                except Exception:
                    preview["preview_type"] = "code"
                    preview["content"] = text
            elif ext in (".md", ".markdown"):
                preview["preview_type"] = "markdown"
                preview["content"] = text
            else:
                preview["preview_type"] = "code"
                preview["content"] = text

        # 2. Image types
        elif mime.startswith("image/"):
            import base64
            raw = path.read_bytes()
            if len(raw) <= 5 * 1024 * 1024:  # under 5MB for inline display
                b64 = base64.b64encode(raw).decode("utf-8")
                preview["preview_type"] = "image"
                preview["content"] = f"data:{mime};base64,{b64}"
            else:
                preview["preview_type"] = "binary"
                preview["content"] = "Image exceeds 5MB inline preview limit; use direct download."

        # 3. PDF
        elif mime == "application/pdf" or ext == ".pdf":
            preview["preview_type"] = "pdf"
            preview["content"] = f"/api/v1/artifacts/{artifact_id}/download?inline=1"

        else:
            preview["preview_type"] = "binary"
            preview["content"] = f"Binary file ({mime}) ready for direct download."

        return preview

    # -----------------------------------------------------------------------
    # Lineage Graph
    # -----------------------------------------------------------------------

    def record_lineage(
        self,
        source_artifact_id: str,
        target_artifact_id: str,
        relationship_type: str,
        source_version: Optional[int] = None,
        target_version: Optional[int] = None,
        context_description: str = "",
    ) -> str:
        """Record a lineage relationship between two artifacts."""
        import uuid
        lin_id = f"lin-{uuid.uuid4().hex[:12]}"
        now = _now_iso()
        with db_transaction() as conn:
            conn.execute(
                """
                INSERT INTO artifact_lineage (
                    id, source_artifact_id, target_artifact_id, relationship_type,
                    source_version_number, target_version_number, context_description, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lin_id, source_artifact_id, target_artifact_id, relationship_type,
                    source_version or 1, target_version or 1, context_description, now,
                ),
            )
        return lin_id

    def get_artifact_lineage(self, artifact_id: str) -> Dict[str, Any]:
        """Query upstream sources and downstream derivatives for an artifact."""
        conn = get_db_connection()
        try:
            upstream = conn.execute(
                """
                SELECT al.*, a.display_name AS source_name, a.artifact_type, a.origin_project_id
                FROM artifact_lineage al
                JOIN artifacts a ON a.id = al.source_artifact_id
                WHERE al.target_artifact_id = ?
                ORDER BY al.created_at DESC
                """,
                (artifact_id,),
            ).fetchall()

            downstream = conn.execute(
                """
                SELECT al.*, a.display_name AS target_name, a.artifact_type, a.origin_project_id
                FROM artifact_lineage al
                JOIN artifacts a ON a.id = al.target_artifact_id
                WHERE al.source_artifact_id = ?
                ORDER BY al.created_at DESC
                """,
                (artifact_id,),
            ).fetchall()

            return {
                "artifact_id": artifact_id,
                "upstream": [dict(r) for r in upstream],
                "downstream": [dict(r) for r in downstream],
            }
        finally:
            conn.close()

    # -----------------------------------------------------------------------
    # Cross-Project Sharing & Authorization
    # -----------------------------------------------------------------------

    def grant_project_access(
        self,
        artifact_id: str,
        source_project_id: str,
        target_project_id: str,
        granted_by: str = "admin",
    ) -> str:
        """Explicitly share an artifact with another project."""
        import uuid
        grant_id = f"aag-{uuid.uuid4().hex[:12]}"
        now = _now_iso()
        with db_transaction() as conn:
            conn.execute(
                """
                INSERT INTO artifact_access_grants (id, artifact_id, source_project_id, target_project_id, granted_by, granted_at, status)
                VALUES (?, ?, ?, ?, ?, ?, 'active')
                ON CONFLICT(artifact_id, target_project_id) DO UPDATE SET status = 'active', granted_at = ?
                """,
                (grant_id, artifact_id, source_project_id, target_project_id, granted_by, now, now),
            )
            # Add project link as shared_reference
            conn.execute(
                """
                INSERT OR IGNORE INTO artifact_project_links (id, artifact_id, project_id, role, added_at, added_by)
                VALUES (?, ?, ?, 'shared_reference', ?, ?)
                """,
                (f"apl-{uuid.uuid4().hex[:12]}", artifact_id, target_project_id, now, granted_by),
            )
        logger.info("Granted project %s access to artifact %s from %s", target_project_id, artifact_id, source_project_id)
        return grant_id

    def check_project_access(self, artifact_id: str, project_id: str) -> bool:
        """Verify whether a project is authorized to read an artifact."""
        conn = get_db_connection()
        try:
            row = conn.execute(
                """
                SELECT 1 FROM artifacts
                WHERE id = ? AND (
                    origin_project_id = ?
                    OR visibility IN ('shared', 'collective')
                    OR id IN (SELECT artifact_id FROM artifact_project_links WHERE project_id = ?)
                    OR id IN (SELECT artifact_id FROM artifact_access_grants WHERE target_project_id = ? AND status = 'active')
                )
                """,
                (artifact_id, project_id, project_id, project_id),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    # -----------------------------------------------------------------------
    # Knowledge Extraction & Integration
    # -----------------------------------------------------------------------

    def extract_knowledge_from_artifact(
        self,
        artifact_id: str,
        version_number: Optional[int] = None,
        auto_promote: bool = False,
        actor: str = "system",
    ) -> List[Dict[str, Any]]:
        """
        Extract meaningful factual claims, summaries, or structured insights from an artifact
        into the project knowledge base, with strict provenance linking.
        """
        import uuid
        art = self.get_artifact(artifact_id)
        if not art:
            raise ValueError(f"Artifact '{artifact_id}' not found.")

        target_version = version_number or art["current_version_number"]
        dl = self.resolve_download(artifact_id, version_number=target_version)
        path = dl["file_path"]

        # Parse text content
        raw_text = ""
        try:
            raw_text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            raw_text = ""

        if not raw_text.strip():
            logger.warning("Artifact %s has no extractable text content", artifact_id)
            return []

        # Simple structured extractor:
        # 1. Look for markdown headers / bullet summaries / or key-value JSON claims
        extracted_claims: List[Dict[str, Any]] = []
        now = _now_iso()

        if dl["mime_type"] == "application/json" or path.suffix.lower() == ".json":
            try:
                js_data = json.loads(raw_text)
                if isinstance(js_data, dict):
                    for k, v in list(js_data.items())[:10]:
                        if isinstance(v, (str, int, float, bool)):
                            extracted_claims.append({
                                "subject": f"{art['display_name']}::{k}",
                                "statement": f"{k}: {v}",
                                "knowledge_type": "source_observation",
                                "confidence": "high",
                            })
            except Exception:
                pass

        # Text/markdown lines or sections
        if not extracted_claims:
            lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
            claims_count = 0
            for l in lines:
                if (l.startswith("#") or l.startswith("- ") or l.startswith("* ") or ":" in l) and len(l) > 15:
                    clean_stmt = l.lstrip("#-* ").strip()
                    extracted_claims.append({
                        "subject": f"{art['display_name']} Finding",
                        "statement": clean_stmt[:500],
                        "knowledge_type": "source_observation",
                        "confidence": "moderate",
                    })
                    claims_count += 1
                    if claims_count >= 5:
                        break

        # Fallback claim
        if not extracted_claims:
            extracted_claims.append({
                "subject": f"{art['display_name']} Deliverable",
                "statement": f"Artifact {art['display_name']} produced with checksum {dl['checksum_sha256'][:16]}...",
                "knowledge_type": "source_observation",
                "confidence": "high",
            })

        from app.services.knowledge_store import record_knowledge_impact, search_knowledge_records

        created_records = []
        with db_transaction() as conn:
            for item in extracted_claims:
                kid = f"know-{uuid.uuid4().hex[:16]}"
                source_meta = {
                    "source_artifact_id": artifact_id,
                    "artifact_version": target_version,
                    "artifact_name": art["display_name"],
                    "sha256": dl["checksum_sha256"],
                    "extracted_at": now,
                }

                conn.execute(
                    """
                    INSERT INTO knowledge_records (
                        id, scope, project_id, source_project_id, subject, knowledge_type,
                        statement, summary, source_ids_json, evidence_ids_json,
                        verification_status, verification_method, confidence_assessment,
                        created_at, updated_at
                    ) VALUES (?, 'project', ?, ?, ?, ?, ?, ?, ?, ?, 'supported', 'artifact_extraction', ?, ?, ?)
                    """,
                    (
                        kid,
                        art["origin_project_id"],
                        art["origin_project_id"],
                        item["subject"][:150],
                        item["knowledge_type"],
                        item["statement"],
                        f"Extracted from {art['display_name']} v{target_version}",
                        json.dumps([artifact_id]),
                        json.dumps([source_meta]),
                        item["confidence"],
                        now,
                        now,
                    ),
                )

                # Link artifact to knowledge record
                conn.execute(
                    """
                    INSERT INTO artifact_knowledge_links (
                        id, artifact_id, artifact_version_number, knowledge_record_id,
                        extraction_method, extracted_claim, created_at
                    ) VALUES (?, ?, ?, ?, 'automated_nlp', ?, ?)
                    """,
                    (f"akl-{uuid.uuid4().hex[:12]}", artifact_id, target_version, kid, item["statement"][:500], now),
                )

                created_records.append({"knowledge_id": kid, "subject": item["subject"], "statement": item["statement"]})

            # Update artifact knowledge status
            conn.execute(
                "UPDATE artifacts SET knowledge_status = 'extracted', updated_at = ? WHERE id = ?",
                (now, artifact_id),
            )

        # Record knowledge impact outside the transaction to prevent nested lock deadlocks
        if art["origin_project_id"]:
            for r in created_records:
                try:
                    record_knowledge_impact(
                        knowledge_id=r["knowledge_id"],
                        project_id=art["origin_project_id"],
                        usage_type="deliverable_claim",
                        impact_summary=f"Extracted from artifact {art['display_name']}",
                    )
                except Exception as e_imp:
                    logger.debug("Failed recording knowledge impact: %s", e_imp)

        logger.info("Extracted %d knowledge records from artifact %s", len(created_records), artifact_id)
        return created_records

    # -----------------------------------------------------------------------
    # Reuse Workflows & Project Creation
    # -----------------------------------------------------------------------

    def reuse_artifact_in_project(
        self,
        artifact_id: str,
        project_id: str,
        action_type: str = "analyze",
        instructions: str = "",
        actor: str = "admin",
    ) -> Dict[str, Any]:
        """
        Send an artifact back into a project (same or authorized target) for additional work.
        Creates an authorized input link, records lineage, and creates a queued follow-up task.
        """
        import uuid
        art = self.get_artifact(artifact_id)
        if not art:
            raise ValueError(f"Artifact '{artifact_id}' does not exist.")

        # Ensure project access
        if not self.check_project_access(artifact_id, project_id):
            if art["origin_project_id"] and art["origin_project_id"] != project_id:
                # Grant access explicitly
                self.grant_project_access(artifact_id, art["origin_project_id"], project_id, granted_by=actor)

        now = _now_iso()
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        task_title = f"{action_type.capitalize()} artifact: {art['display_name']}"
        action_desc = instructions or f"Perform '{action_type}' on artifact {art['display_name']} (ID: {artifact_id})."

        task_input_data = {
            "action_type": action_type,
            "instructions": action_desc,
            "input_artifacts": [
                {
                    "artifact_id": artifact_id,
                    "version": art["current_version_number"],
                    "display_name": art["display_name"],
                    "role": "source_document",
                    "storage_key": art["storage_key"],
                }
            ],
        }

        with db_transaction() as conn:
            # 1. Link artifact to target project as 'input'
            conn.execute(
                """
                INSERT OR IGNORE INTO artifact_project_links (id, artifact_id, project_id, role, added_at, added_by)
                VALUES (?, ?, ?, 'input', ?, ?)
                """,
                (f"apl-{uuid.uuid4().hex[:12]}", artifact_id, project_id, now, actor),
            )

            # 2. Insert queued task in target project
            conn.execute(
                """
                INSERT INTO tasks (
                    id, project_id, title, description, task_type,
                    status, input_data_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'artifact_reuse', 'queued', ?, ?, ?)
                """,
                (task_id, project_id, task_title, action_desc, json.dumps(task_input_data), now, now),
            )

            # 3. Insert task link
            conn.execute(
                """
                INSERT INTO artifact_task_links (id, artifact_id, version_number, task_id, orch_task_id, direction, role, created_at)
                VALUES (?, ?, ?, ?, NULL, 'input', 'source_document', ?)
                """,
                (f"atl-{uuid.uuid4().hex[:12]}", artifact_id, art["current_version_number"], task_id, now),
            )

            # 4. Touch project
            conn.execute("UPDATE projects SET last_activity_at = ? WHERE id = ?", (now, project_id))

        logger.info("Reused artifact %s in project %s via task %s", artifact_id, project_id, task_id)
        return {
            "artifact_id": artifact_id,
            "project_id": project_id,
            "task_id": task_id,
            "action_type": action_type,
            "status": "queued",
        }

    def create_project_from_artifact(
        self,
        artifact_id: str,
        user_objective: str,
        title: Optional[str] = None,
        created_by: str = "admin",
    ) -> Dict[str, Any]:
        """
        Artifact-Aware Project Creation ('Create Project From This').
        Initializes a new project with authorized artifact reference, retrieves supporting research,
        links lineage, and pre-populates intake.
        """
        import uuid
        art = self.get_artifact(artifact_id)
        if not art:
            raise ValueError(f"Artifact '{artifact_id}' does not exist.")

        from app.services.projects import create_project

        proj_title = title or f"Project: {art['display_name']} Expansion"
        full_objective = (
            f"{user_objective.strip()}\n\n"
            f"[Source Deliverable Context: Based on artifact '{art['display_name']}' (ID: {artifact_id}, "
            f"Version {art['current_version_number']}, SHA256: {art['checksum_sha256'][:16]}...)]"
        )

        # Create new project
        new_proj = create_project(
            proj_title,
            created_by,
            description=f"Artifact-aware project initialized from deliverable {art['display_name']}",
            objective=full_objective,
        )
        new_proj_id = new_proj["id"]

        now = _now_iso()

        with db_transaction() as conn:
            # Grant access to artifact
            if art["origin_project_id"]:
                conn.execute(
                    """
                    INSERT INTO artifact_access_grants (id, artifact_id, source_project_id, target_project_id, granted_by, granted_at, status)
                    VALUES (?, ?, ?, ?, ?, ?, 'active')
                    """,
                    (f"aag-{uuid.uuid4().hex[:12]}", artifact_id, art["origin_project_id"], new_proj_id, created_by, now),
                )

            # Associate as imported input
            conn.execute(
                """
                INSERT OR IGNORE INTO artifact_project_links (id, artifact_id, project_id, role, added_at, added_by)
                VALUES (?, ?, ?, 'imported', ?, ?)
                """,
                (f"apl-{uuid.uuid4().hex[:12]}", artifact_id, new_proj_id, now, created_by),
            )

        logger.info("Created artifact-aware project %s from artifact %s", new_proj_id, artifact_id)
        return {
            "project": new_proj,
            "source_artifact": art,
        }


# Default singleton instance
artifact_service = ArtifactService()
