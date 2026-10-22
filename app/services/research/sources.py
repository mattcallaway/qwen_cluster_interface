"""
Persistent Research Source Library.

Manages auditable research sources per project:
- Deduplication by canonical URL and content hash
- Versioning when source content updates
- Persistent storage of raw extractions and structured metadata
- Source reliability / quality score assessment
- Strict project isolation
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from app.config import settings
from app.database import get_db_connection
from app.services.research.cache import compute_content_hash, normalize_canonical_url
from app.services.research.extraction import ExtractedDocument

logger = logging.getLogger("qwen_collective.research.sources")

HIGH_QUALITY_DOMAINS = {
    ".gov", ".edu", ".org", "wikipedia.org", "nature.com", "sciencedirect.com",
    "ieee.org", "acm.org", "nih.gov", "arxiv.org", "reuters.com", "apnews.com",
    "bbc.com", "bbc.co.uk", "economist.com", "bloomberg.com", "github.com",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SourceLibrary:
    """Project-isolated repository of research sources and document extractions."""

    def __init__(self, data_dir: Optional[Path] = None, base_storage_dir: Optional[Path] = None):
        self.data_dir = data_dir or base_storage_dir or (settings.data_dir / "research_sources")
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _get_project_dir(self, project_id: str) -> Path:
        p_dir = self.data_dir / project_id
        p_dir.mkdir(parents=True, exist_ok=True)
        return p_dir

    def add_source(
        self,
        project_id: str,
        url: str,
        title: str = "",
        raw_content: bytes = b"",
        extracted_text: str = "",
        canonical_url: str = "",
        content_type: str = "text/html",
        research_task_id: str = "",
        search_query: str = "",
        search_provider: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        research_run_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Convenience method to ingest text directly into the source library."""
        extracted = ExtractedDocument(
            title=title,
            canonical_url=canonical_url or url,
            clean_text=extracted_text,
            content_type=content_type,
            content_hash=compute_content_hash(raw_content or extracted_text.encode("utf-8")),
        )
        return self.record_source(project_id, url, extracted, research_run_id=research_run_id, session_id=session_id)

    def record_source(
        self,
        project_id: str,
        original_url: str,
        extracted: ExtractedDocument,
        retrieval_method: str = "http",
        status: str = "fetched",
        research_run_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Record a newly retrieved and extracted source in the project source library.
        Performs deduplication and updates version history if content changed.
        """
        now = _now_iso()
        canon_url = normalize_canonical_url(original_url)
        content_hash = extracted.content_hash or compute_content_hash(extracted.clean_text)
        quality_score, reliability_assessment = self.assess_source_quality(canon_url, extracted)

        conn = get_db_connection()
        try:
            # Check if source already exists for this project
            existing = conn.execute(
                "SELECT * FROM research_sources WHERE project_id = ? AND canonical_url = ?",
                (project_id, canon_url),
            ).fetchone()

            if existing:
                source_id = existing["id"]
                # Check if content changed
                if existing["content_hash"] != content_hash:
                    # Increment version
                    v_row = conn.execute(
                        "SELECT MAX(version_number) FROM research_source_versions WHERE source_id = ?",
                        (source_id,),
                    ).fetchone()
                    new_version = (v_row[0] or 1) + 1

                    # Save new version file
                    p_dir = self._get_project_dir(project_id)
                    v_file = p_dir / f"{source_id}_v{new_version}.txt"
                    v_file.write_text(extracted.clean_text, encoding="utf-8")

                    conn.execute(
                        """
                        INSERT INTO research_source_versions (
                            id, source_id, version_number, content_hash,
                            content_length_bytes, storage_path, retrieved_at, session_id, research_run_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            f"sver-{uuid.uuid4().hex[:12]}", source_id, new_version, content_hash,
                            len(extracted.clean_text.encode("utf-8")), str(v_file), now, session_id, research_run_id
                        ),
                    )

                    conn.execute(
                        """
                        UPDATE research_sources SET
                            title = COALESCE(NULLIF(?, ''), title),
                            author = COALESCE(?, author),
                            publisher = COALESCE(?, publisher),
                            publication_date = COALESCE(?, publication_date),
                            modified_date = COALESCE(?, modified_date),
                            content_hash = ?,
                            content_length_bytes = ?,
                            storage_path = ?,
                            status = ?,
                            quality_score = ?,
                            reliability_assessment_json = ?,
                            updated_at = ?,
                            session_id = COALESCE(?, session_id),
                            research_run_id = COALESCE(research_run_id, ?)
                        WHERE id = ?
                        """,
                        (
                            extracted.title, extracted.author, extracted.publisher,
                            extracted.publication_date, extracted.modified_date,
                            content_hash, len(extracted.clean_text.encode("utf-8")),
                            str(v_file), status, quality_score, json.dumps(reliability_assessment),
                            now, session_id, research_run_id, source_id,
                        ),
                    )

                    # Update extractions table
                    conn.execute(
                        """
                        INSERT INTO research_extractions (
                            id, project_id, source_id, clean_text, headings_json,
                            tables_json, outbound_urls_json, word_count, extracted_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(source_id) DO UPDATE SET
                            clean_text = excluded.clean_text,
                            headings_json = excluded.headings_json,
                            tables_json = excluded.tables_json,
                            outbound_urls_json = excluded.outbound_urls_json,
                            word_count = excluded.word_count,
                            extracted_at = excluded.extracted_at
                        """,
                        (
                            f"ext-{uuid.uuid4().hex[:12]}", project_id, source_id,
                            extracted.clean_text, json.dumps(extracted.headings),
                            json.dumps(extracted.tables), json.dumps(extracted.outbound_urls),
                            extracted.word_count, now,
                        ),
                    )
                else:
                    # Content is identical: record authorized reuse in subsequent session
                    if session_id:
                        v_row = conn.execute(
                            "SELECT id, version_number FROM research_source_versions WHERE source_id = ? ORDER BY version_number DESC LIMIT 1",
                            (source_id,)
                        ).fetchone()
                        vid = v_row[0] if v_row else None
                        self.record_source_reuse(
                            project_id=project_id,
                            research_session_id=session_id,
                            source_id=source_id,
                            source_version_id=vid,
                            orchestration_run_id=research_run_id,
                            reuse_reason="Identical content verified fresh in subsequent session",
                            verified_fresh=True,
                            conn=conn,
                        )

                conn.commit()
                return self.get_source(project_id, source_id)

            # Insert new source
            source_id = f"src-{uuid.uuid4().hex[:12]}"
            p_dir = self._get_project_dir(project_id)
            storage_file = p_dir / f"{source_id}.txt"
            storage_file.write_text(extracted.clean_text, encoding="utf-8")

            conn.execute(
                """
                INSERT INTO research_sources (
                    id, project_id, research_run_id, session_id, canonical_url, original_url, title, author,
                    publisher, publication_date, modified_date, retrieved_at,
                    content_type, content_hash, content_length_bytes, storage_path,
                    retrieval_method, status, quality_score, reliability_assessment_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id, project_id, research_run_id, session_id, canon_url, original_url, extracted.title,
                    extracted.author, extracted.publisher, extracted.publication_date,
                    extracted.modified_date, now, extracted.content_type, content_hash,
                    len(extracted.clean_text.encode("utf-8")), str(storage_file),
                    retrieval_method, status, quality_score, json.dumps(reliability_assessment),
                    now, now,
                ),
            )

            # Initial version 1
            conn.execute(
                """
                INSERT INTO research_source_versions (
                    id, source_id, version_number, content_hash,
                    content_length_bytes, storage_path, retrieved_at, session_id, research_run_id
                ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"sver-{uuid.uuid4().hex[:12]}", source_id, content_hash,
                    len(extracted.clean_text.encode("utf-8")), str(storage_file), now, session_id, research_run_id
                ),
            )

            # Extractions table
            conn.execute(
                """
                INSERT INTO research_extractions (
                    id, project_id, source_id, clean_text, headings_json,
                    tables_json, outbound_urls_json, word_count, extracted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"ext-{uuid.uuid4().hex[:12]}", project_id, source_id,
                    extracted.clean_text, json.dumps(extracted.headings),
                    json.dumps(extracted.tables), json.dumps(extracted.outbound_urls),
                    extracted.word_count, now,
                ),
            )

            conn.commit()
            return self.get_source(project_id, source_id)
        finally:
            conn.close()

    def get_source(self, project_id: str, source_id: str) -> Optional[Dict[str, Any]]:
        conn = get_db_connection()
        try:
            row = conn.execute(
                "SELECT * FROM research_sources WHERE project_id = ? AND id = ?",
                (project_id, source_id),
            ).fetchone()
            if not row:
                return None
            res = dict(row)
            # Fetch extraction
            ext = conn.execute(
                "SELECT clean_text, headings_json, tables_json, outbound_urls_json, word_count FROM research_extractions WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            if ext:
                res["clean_text"] = ext["clean_text"]
                res["extracted_text"] = ext["clean_text"]
                res["headings"] = json.loads(ext["headings_json"])
                res["tables"] = json.loads(ext["tables_json"])
                res["word_count"] = ext["word_count"]
            else:
                res["extracted_text"] = res.get("clean_text", "")

            res["source_id"] = res["id"]
            # Fetch version count
            v_cnt = conn.execute(
                "SELECT COUNT(*) FROM research_source_versions WHERE source_id = ?",
                (source_id,),
            ).fetchone()[0]
            res["version"] = max(1, v_cnt)
            return res
        finally:
            conn.close()

    def get_source_versions(self, project_id: str, source_id: str) -> List[Dict[str, Any]]:
        """Returns all immutable version records for a given research source."""
        conn = get_db_connection()
        try:
            rows = conn.execute(
                """SELECT * FROM research_source_versions WHERE source_id = ?
                   ORDER BY version_number ASC""",
                (source_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def list_sources(
        self,
        project_id: str,
        limit: int = 100,
        research_run_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        conn = get_db_connection()
        try:
            if research_run_id:
                # Ensure research_source_reuses table exists
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS research_source_reuses (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                        source_id TEXT NOT NULL REFERENCES research_sources(id) ON DELETE CASCADE,
                        origin_run_id TEXT,
                        target_run_id TEXT NOT NULL,
                        authorized_by TEXT NOT NULL,
                        authorization_note TEXT NOT NULL DEFAULT '',
                        authorized_at TEXT NOT NULL,
                        UNIQUE (source_id, target_run_id)
                    )"""
                )
                rows = conn.execute(
                    """SELECT * FROM research_sources
                       WHERE project_id = ?
                         AND (
                             research_run_id = ?
                             OR id IN (SELECT source_id FROM research_source_reuses WHERE project_id = ? AND target_run_id = ?)
                         )
                       ORDER BY quality_score DESC, retrieved_at DESC LIMIT ?""",
                    (project_id, research_run_id, project_id, research_run_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM research_sources WHERE project_id = ? ORDER BY quality_score DESC, retrieved_at DESC LIMIT ?",
                    (project_id, limit),
                ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["source_id"] = d["id"]
                ext = conn.execute("SELECT clean_text FROM research_extractions WHERE source_id = ?", (d["id"],)).fetchone()
                d["extracted_text"] = ext["clean_text"] if ext else ""
                out.append(d)
            return out
        finally:
            conn.close()

    def reuse_source_in_run(
        self,
        project_id: str,
        source_id: str,
        target_run_id: str,
        authorized_by: str = "operator",
        notes: str = "",
    ) -> Dict[str, Any]:
        """
        Explicitly authorizes the reuse of an existing source in a new research run (Finding F-09).
        
        Guarantees:
        1. The original source record and its historical provenance/run-id remain strictly intact.
        2. Authorized reuse is auditable via research_source_reuses.
        3. Prevents implicit promotion or silent reattribution across runs.
        """
        conn = get_db_connection()
        try:
            # Ensure reuse ledger exists
            conn.execute(
                """CREATE TABLE IF NOT EXISTS research_source_reuses (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    source_id TEXT NOT NULL REFERENCES research_sources(id) ON DELETE CASCADE,
                    origin_run_id TEXT,
                    target_run_id TEXT NOT NULL,
                    authorized_by TEXT NOT NULL,
                    authorization_note TEXT NOT NULL DEFAULT '',
                    authorized_at TEXT NOT NULL,
                    UNIQUE (source_id, target_run_id)
                )"""
            )
            src = conn.execute(
                "SELECT * FROM research_sources WHERE project_id = ? AND id = ?",
                (project_id, source_id),
            ).fetchone()
            if not src:
                raise ValueError(f"Source '{source_id}' does not exist under project '{project_id}'.")

            origin_run_id = src["research_run_id"]
            now = _now_iso()
            reuse_id = f"reuse-{uuid.uuid4().hex[:12]}"
            conn.execute(
                """INSERT OR REPLACE INTO research_source_reuses (
                    id, project_id, source_id, origin_run_id, target_run_id,
                    authorized_by, authorization_note, authorized_at, reused_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (reuse_id, project_id, source_id, origin_run_id, target_run_id, authorized_by, notes, now, now),
            )
            conn.commit()
            return {
                "id": reuse_id,
                "project_id": project_id,
                "source_id": source_id,
                "origin_run_id": origin_run_id,
                "target_run_id": target_run_id,
                "authorized_by": authorized_by,
                "authorized_at": now,
                "status": "authorized",
            }
        finally:
            conn.close()

    def assess_source_quality(self, url: str, extracted: ExtractedDocument) -> Tuple[float, Dict[str, Any]]:
        """
        Assess source reliability and quality score (0.0 to 1.0) based on:
        - Domain TLD and reputable publisher signals
        - Publication date freshness
        - Metadata completeness (author, publisher)
        - Content depth (word count, tables, headings)
        """
        score = 0.5
        reasons = []

        host = (urlparse(url).hostname or "").lower()
        if any(host.endswith(tld) or tld in host for tld in HIGH_QUALITY_DOMAINS):
            score += 0.25
            reasons.append("Authoritative / academic / government domain")

        if extracted.author:
            score += 0.1
            reasons.append("Identified author")

        if extracted.publication_date:
            score += 0.1
            reasons.append("Verified publication date")

        if extracted.word_count > 400:
            score += 0.05
            reasons.append("Substantial body text")

        if extracted.tables:
            score += 0.05
            reasons.append("Structured data tables present")

        score = min(1.0, round(score, 2))
        return score, {"score": score, "factors": reasons, "domain": host}

    def record_source_reuse(
        self,
        project_id: str,
        research_session_id: str,
        source_id: str,
        orchestration_run_id: Optional[str] = None,
        source_version_id: Optional[str] = None,
        target_task_id: Optional[str] = None,
        target_stage_phase: Optional[str] = None,
        reuse_reason: str = "Authorized source reuse",
        verified_fresh: bool = True,
        conn: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Record an explicit authorized source reuse in research_source_reuses."""
        reuse_id = f"reu-{uuid.uuid4().hex[:12]}"
        now = _now_iso()
        own_conn = conn is None
        c = get_db_connection() if own_conn else conn
        try:
            c.execute(
                """
                INSERT INTO research_source_reuses (
                    id, project_id, orchestration_run_id, research_session_id,
                    source_id, source_version_id, target_task_id, target_stage_phase,
                    reuse_reason, verified_fresh, reused_at, target_run_id, origin_run_id,
                    authorized_by, authorization_note, authorized_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reuse_id, project_id, orchestration_run_id, research_session_id,
                    source_id, source_version_id, target_task_id, target_stage_phase,
                    reuse_reason, 1 if verified_fresh else 0, now, orchestration_run_id,
                    None, "operator", reuse_reason, now
                ),
            )
            if own_conn:
                c.commit()
            return {
                "id": reuse_id,
                "project_id": project_id,
                "orchestration_run_id": orchestration_run_id,
                "research_session_id": research_session_id,
                "source_id": source_id,
                "source_version_id": source_version_id,
                "target_task_id": target_task_id,
                "target_stage_phase": target_stage_phase,
                "reuse_reason": reuse_reason,
                "verified_fresh": verified_fresh,
                "reused_at": now,
            }
        finally:
            if own_conn:
                c.close()

    def list_source_reuses(
        self,
        project_id: str,
        orchestration_run_id: Optional[str] = None,
        research_session_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List authorized source reuses for a project, optionally filtered by run or session."""
        conn = get_db_connection()
        try:
            sql = "SELECT * FROM research_source_reuses WHERE project_id = ?"
            params: List[Any] = [project_id]
            if orchestration_run_id:
                sql += " AND orchestration_run_id = ?"
                params.append(orchestration_run_id)
            if research_session_id:
                sql += " AND research_session_id = ?"
                params.append(research_session_id)
            sql += " ORDER BY reused_at DESC"
            rows = conn.execute(sql, tuple(params)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


# Module-level convenience functions
def record_source(project_id: str, original_url: str, extracted: Any, **kwargs) -> Dict[str, Any]:
    return SourceLibrary().record_source(project_id, original_url, extracted, **kwargs)

def add_source(project_id: str, url: str, **kwargs) -> Dict[str, Any]:
    return SourceLibrary().add_source(project_id, url, **kwargs)

def record_source_reuse(project_id: str, research_session_id: str, source_id: str, **kwargs) -> Dict[str, Any]:
    return SourceLibrary().record_source_reuse(project_id, research_session_id, source_id, **kwargs)

def list_source_reuses(project_id: str, **kwargs) -> List[Dict[str, Any]]:
    return SourceLibrary().list_source_reuses(project_id, **kwargs)

