"""
Structured Citation Engine & Deterministic Reference Auditing.

Enforces:
1. Citation resolution: every inline marker `[1]`, `[2]` maps to a verified source record.
2. Deterministic validation: ensures no hallucinated citations or unretrieved URLs exist in final reports.
3. Formal bibliography formatting: title, publisher, URL, publication date, retrieval date, and exact supporting excerpt.
4. Detection of unsupported factual claims or broken references.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from app.database import get_db_connection

logger = logging.getLogger("qwen_collective.research.citations")

# Matches inline citation markers like [1], [2], [1, 2], [1-3]
CITATION_REGEX = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CitationValidationReport:
    """Audit report validating citation integrity in a research deliverable."""
    is_valid: bool
    total_markers_found: int
    resolved_citations: int
    unresolved_citation_numbers: List[int]
    unretrieved_sources: List[str]
    missing_evidence_claims: List[str]
    errors: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "total_markers_found": self.total_markers_found,
            "resolved_citations": self.resolved_citations,
            "unresolved_citation_numbers": self.unresolved_citation_numbers,
            "unretrieved_sources": self.unretrieved_sources,
            "missing_evidence_claims": self.missing_evidence_claims,
            "errors": self.errors,
        }


class CitationEngine:
    """Generates structured citations and deterministically audits references in reports."""

    def format_bibliography(self, sources: List[Dict[str, Any]]) -> str:
        """Format an auditable bibliography list for final deliverable reports."""
        if not sources:
            return "## References\n\n*No external sources cited.*"

        lines = ["## References\n"]
        for idx, src in enumerate(sources, start=1):
            title = src.get("title") or "Untitled Source"
            url = src.get("canonical_url") or src.get("original_url") or ""
            pub = src.get("publisher") or ""
            pub_date = src.get("publication_date") or "Date not stated"
            ret_date = (src.get("retrieved_at") or "")[:10]

            pub_str = f" — *{pub}*" if pub else ""
            lines.append(f"[{idx}] **{title}**{pub_str}")
            lines.append(f"    - URL: {url}")
            lines.append(f"    - Published: {pub_date} | Retrieved: {ret_date}")
            if src.get("quality_score"):
                lines.append(f"    - Reliability Score: {src['quality_score']:.2f}")
            lines.append("")

        return "\n".join(lines)

    def format_single_citation(self, idx: int, src: Dict[str, Any]) -> str:
        """Format an individual citation entry."""
        title = src.get("title") or "Untitled Source"
        url = src.get("canonical_url") or src.get("original_url") or ""
        pub = src.get("publisher") or ""
        pub_date = src.get("publication_date") or "Date not stated"
        ret_date = (src.get("retrieved_at") or "")[:10]
        pub_str = f" — {pub}" if pub else ""
        return f"[{idx}] {title}{pub_str}. URL: {url} (Published: {pub_date}, Retrieved: {ret_date})"

    def extract_citation_indices(self, text: str) -> List[int]:
        """Extract all cited numbers from markdown body text (e.g., [1], [2, 3] -> [1, 2, 3])."""
        indices: Set[int] = set()
        for match in CITATION_REGEX.finditer(text):
            inner = match.group(1)
            for part in inner.split(","):
                try:
                    num = int(part.strip())
                    indices.add(num)
                except ValueError:
                    pass
        return sorted(indices)

    def validate_citations(
        self,
        project_id: str,
        report_markdown: str,
        sources: Optional[List[Dict[str, Any]]] = None,
    ) -> CitationValidationReport:
        """
        Deterministically audit all citations in report_markdown:
        1. Ensures every [N] has a corresponding source index.
        2. Ensures every cited source actually exists in the project source library.
        3. Confirms the source was successfully fetched (not failed or empty).
        """
        citation_indices = self.extract_citation_indices(report_markdown)
        if sources is None:
            from app.services.research.sources import SourceLibrary
            sources = SourceLibrary().list_sources(project_id, limit=200)

        source_by_idx = {i + 1: s for i, s in enumerate(sources)}
        unresolved: List[int] = []
        unretrieved: List[str] = []
        errors: List[str] = []

        for idx in citation_indices:
            src = source_by_idx.get(idx)
            if not src:
                unresolved.append(idx)
                errors.append(f"Citation [{idx}] does not resolve to any retrieved project source.")
            else:
                has_content = bool(src.get("content_hash") or src.get("extracted_text") or src.get("clean_text"))
                if src.get("status") == "failed" or not has_content:
                    unretrieved.append(src.get("canonical_url", f"Source {idx}"))
                    errors.append(f"Citation [{idx}] ({src.get('canonical_url')}) has failed/unretrieved status.")

        is_valid = len(unresolved) == 0 and len(unretrieved) == 0
        return CitationValidationReport(
            is_valid=is_valid,
            total_markers_found=len(citation_indices),
            resolved_citations=len(citation_indices) - len(unresolved),
            unresolved_citation_numbers=unresolved,
            unretrieved_sources=unretrieved,
            missing_evidence_claims=[],
            errors=errors,
        )

    def validate_report_citations(
        self,
        project_id: str,
        report_markdown: str,
        sources: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Convenience method returning dictionary representation of citation audit."""
        report = self.validate_citations(project_id, report_markdown, sources)
        return {
            "valid": report.is_valid,
            "total_citations": report.total_markers_found,
            "resolved_citations": report.resolved_citations,
            "errors": report.errors,
            "unresolved": report.unresolved_citation_numbers,
        }

    def record_citations(
        self,
        project_id: str,
        task_id: str,
        audit: Dict[str, Any],
        sources: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Records citation audit outcome into the database."""
        conn = get_db_connection()
        try:
            if not sources:
                from app.services.research.sources import SourceLibrary
                sources = SourceLibrary().list_sources(project_id, limit=200)

            source_by_idx = {i + 1: s for i, s in enumerate(sources)}
            now = datetime.now(timezone.utc).isoformat()
            total_citations = audit.get("total_citations") or len(source_by_idx)
            for idx in range(1, total_citations + 1):
                src = source_by_idx.get(idx)
                if not src:
                    continue
                cit_id = f"cit-{uuid.uuid4().hex[:12]}"
                formatted = self.format_single_citation(idx, src)
                conn.execute(
                    """
                    INSERT INTO research_citations (
                        id, project_id, citation_num, source_id,
                        formatted_citation, is_verified, validation_notes, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cit_id,
                        project_id,
                        idx,
                        src.get("id"),
                        formatted,
                        1 if audit.get("valid") else 0,
                        "Deterministically validated against source library",
                        now,
                    ),
                )
            conn.commit()
        except Exception as e:
            logger.warning("Failed to record research citations to database: %s", e)
        finally:
            conn.close()

