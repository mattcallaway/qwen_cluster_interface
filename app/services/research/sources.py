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
    ) -> Dict[str, Any]:
        """Convenience method to ingest text directly into the source library."""
        extracted = ExtractedDocument(
            title=title,
            canonical_url=canonical_url or url,
            clean_text=extracted_text,
            content_type=content_type,
            content_hash=compute_content_hash(raw_content or extracted_text.encode("utf-8")),
        )
        return self.record_source(project_id, url, extracted)

    def record_source(
        self,
        project_id: str,
        original_url: str,
        extracted: ExtractedDocument,
        retrieval_method: str = "http",
        status: str = "fetched",
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
                            content_length_bytes, storage_path, retrieved_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            f"sver-{uuid.uuid4().hex[:12]}", source_id, new_version, content_hash,
                            len(extracted.clean_text.encode("utf-8")), str(v_file), now,
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
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            extracted.title, extracted.author, extracted.publisher,
                            extracted.publication_date, extracted.modified_date,
                            content_hash, len(extracted.clean_text.encode("utf-8")),
                            str(v_file), status, quality_score, json.dumps(reliability_assessment),
                            now, source_id,
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
                    id, project_id, canonical_url, original_url, title, author,
                    publisher, publication_date, modified_date, retrieved_at,
                    content_type, content_hash, content_length_bytes, storage_path,
                    retrieval_method, status, quality_score, reliability_assessment_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id, project_id, canon_url, original_url, extracted.title,
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
                    content_length_bytes, storage_path, retrieved_at
                ) VALUES (?, ?, 1, ?, ?, ?, ?)
                """,
                (
                    f"sver-{uuid.uuid4().hex[:12]}", source_id, content_hash,
                    len(extracted.clean_text.encode("utf-8")), str(storage_file), now,
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

    def list_sources(self, project_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        conn = get_db_connection()
        try:
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
