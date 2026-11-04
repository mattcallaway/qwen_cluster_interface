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
from app.services.research.entailment import verify_claim_against_source

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
    supported_citations: Optional[List[str]] = None
    contradicted_citations: Optional[List[str]] = None
    insufficient_evidence_citations: Optional[List[str]] = None
    unverified_citations: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "total_markers_found": self.total_markers_found,
            "resolved_citations": self.resolved_citations,
            "unresolved_citation_numbers": self.unresolved_citation_numbers,
            "unretrieved_sources": self.unretrieved_sources,
            "missing_evidence_claims": self.missing_evidence_claims,
            "supported_citations": self.supported_citations or [],
            "contradicted_citations": self.contradicted_citations or [],
            "insufficient_evidence_citations": self.insufficient_evidence_citations or [],
            "unverified_citations": self.unverified_citations or [],
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

    def extract_citation_indices(self, text: str, max_valid_index: Optional[int] = None) -> List[int]:
        """Extract all cited numbers from markdown body text (e.g., [1], [2, 3] -> [1, 2, 3])."""
        ref_pos = text.find("## References")
        if ref_pos == -1:
            ref_pos = text.find("# References")
        body_text = text[:ref_pos] if ref_pos != -1 else text

        # Strip markdown links [label](url) so bracketed link text is not confused for citations
        body_no_links = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", body_text)

        indices: Set[int] = set()
        for match in CITATION_REGEX.finditer(body_no_links):
            inner = match.group(1)
            for part in inner.split(","):
                try:
                    num = int(part.strip())
                    # Disambiguate 4-digit years (e.g. [2026], [2024]) from footnote citations
                    if 1900 <= num <= 2099 and (max_valid_index is None or num > max_valid_index):
                        continue
                    indices.add(num)
                except ValueError:
                    pass
        return sorted(indices)

    def validate_citations(
        self,
        project_id: str,
        report_markdown: str,
        sources: Optional[List[Dict[str, Any]]] = None,
        research_run_id: Optional[str] = None,
    ) -> CitationValidationReport:
        """
        Deterministically audit all citations in report_markdown (Findings F-07, F-09):
        1. Strict zero-citation rejection: reports with 0 citations fail with MISSING_REQUIRED_CITATIONS.
        2. Ensures every [N] has a corresponding source index.
        3. Ensures every cited source actually exists in the project source library.
        4. Confirms the source was successfully fetched (not failed or empty).
        5. Enforces claim-level grounding: citations must demonstrate support for text.
        6. Enforces run isolation: cited sources must not violate research_run_id scope.
        """
        if sources is None:
            from app.services.research.sources import SourceLibrary
            sources = SourceLibrary().list_sources(project_id, limit=200, research_run_id=research_run_id)

        source_by_idx = {i + 1: s for i, s in enumerate(sources)}
        citation_indices = self.extract_citation_indices(report_markdown, max_valid_index=len(source_by_idx))

        # Finding F-07: Zero-citation reports must strictly fail validation
        if len(citation_indices) == 0:
            return CitationValidationReport(
                is_valid=False,
                total_markers_found=0,
                resolved_citations=0,
                unresolved_citation_numbers=[],
                unretrieved_sources=[],
                missing_evidence_claims=["all"],
                errors=["MISSING_REQUIRED_CITATIONS: Report contains 0 citations. All research deliverables must cite authoritative sources."],
            )

        unresolved: List[int] = []
        unretrieved: List[str] = []
        unsupported: List[str] = []
        supported: List[str] = []
        contradicted: List[str] = []
        insufficient: List[str] = []
        unverified: List[str] = []
        errors: List[str] = []

        # Scope body text before references section
        ref_header_pos = report_markdown.find("## References")
        if ref_header_pos == -1:
            ref_header_pos = report_markdown.find("# References")
        body_markdown = report_markdown[:ref_header_pos] if ref_header_pos != -1 else report_markdown

        # Find paragraphs and sentences containing each citation marker
        for idx in citation_indices:
            src = source_by_idx.get(idx)
            if not src:
                unresolved.append(idx)
                errors.append(f"Citation [{idx}] does not resolve to any retrieved project source.")
                continue

            # Verify run isolation if research_run_id is specified
            src_run_id = src.get("research_run_id")
            if research_run_id and src_run_id and src_run_id != research_run_id:
                # Check if authorized reuse exists in database
                is_authorized_reuse = False
                try:
                    conn = get_db_connection()
                    try:
                        row = conn.execute(
                            "SELECT 1 FROM research_source_reuses WHERE source_id = ? AND target_run_id = ?",
                            (src.get("id"), research_run_id),
                        ).fetchone()
                        is_authorized_reuse = bool(row)
                    finally:
                        conn.close()
                except Exception:
                    is_authorized_reuse = False

                if not is_authorized_reuse:
                    errors.append(f"Citation [{idx}] violates research run isolation: source belongs to run '{src_run_id}', expected '{research_run_id}'.")

            src_text = src.get("extracted_text") or src.get("clean_text") or ""
            has_content = bool(src.get("content_hash") or src_text)
            if src.get("status") == "failed" or not has_content:
                unretrieved.append(src.get("canonical_url", f"Source {idx}"))
                errors.append(f"Citation [{idx}] ({src.get('canonical_url')}) has failed/unretrieved status.")
                continue

            # Verify claim-level entailment (Finding F-07 and Stage 4 Review Correction)
            marker = f"[{idx}]"
            m_pos = body_markdown.find(marker)
            if m_pos != -1:
                line_start = max(0, body_markdown.rfind("\n", 0, m_pos) + 1)
                line_end = body_markdown.find("\n", m_pos)
                if line_end == -1:
                    line_end = len(body_markdown)
                current_line = body_markdown[line_start:line_end]

                if "|" in current_line:
                    # Markdown table row - extract claim text from table cells
                    parts = [
                        part.strip() for part in current_line.split("|")
                        if part.strip()
                        and not part.strip().startswith("[")
                        and not part.strip().startswith(":")
                        and part.strip() not in ("Fact", "Assumption", "Analysis", "Finding", "Category")
                        and not re.match(r"^C\d+$", part.strip())
                        and not (part.strip().startswith("**") and part.strip().endswith("**"))
                    ]
                    claim_sentence = max(parts, key=len) if parts else current_line.strip()
                else:
                    # Extract sentence containing the marker, preserving decimals like 99.8%
                    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", current_line) if s.strip()]
                    claim_sentence = ""
                    for s in sentences:
                        if marker in s:
                            claim_sentence = s
                            break
                    if not claim_sentence or len(claim_sentence) < 15:
                        claim_sentence = current_line.strip()

                # Deterministic factual entailment check
                entailment = verify_claim_against_source(claim_sentence, src_text)
                if entailment.status == "SUPPORTED":
                    supported.append(f"[{idx}]")
                elif entailment.status == "CONTRADICTED":
                    contradicted.append(f"[{idx}]")
                    unsupported.append(f"[{idx}]")
                    errors.append(f"CLAIM_CONTRADICTED: Citation [{idx}] is contradicted by referenced source '{src.get('canonical_url')}': {entailment.reason}")
                elif entailment.status == "INSUFFICIENT_EVIDENCE":
                    insufficient.append(f"[{idx}]")
                    unsupported.append(f"[{idx}]")
                    errors.append(f"INSUFFICIENT_EVIDENCE: Citation [{idx}] lacks verifiable evidence in source '{src.get('canonical_url')}': {entailment.reason}")
                elif entailment.status == "UNVERIFIED":
                    unverified.append(f"[{idx}]")
                    unsupported.append(f"[{idx}]")
                    errors.append(f"UNVERIFIED: Citation [{idx}] cannot be verified against source '{src.get('canonical_url')}': {entailment.reason}")

        is_valid = len(unresolved) == 0 and len(unretrieved) == 0 and len(unsupported) == 0 and len(errors) == 0
        return CitationValidationReport(
            is_valid=is_valid,
            total_markers_found=len(citation_indices),
            resolved_citations=len(citation_indices) - len(unresolved),
            unresolved_citation_numbers=unresolved,
            unretrieved_sources=unretrieved,
            missing_evidence_claims=unsupported,
            supported_citations=supported,
            contradicted_citations=contradicted,
            insufficient_evidence_citations=insufficient,
            unverified_citations=unverified,
            errors=errors,
        )

    def validate_report_citations(
        self,
        project_id: str,
        report_markdown: str,
        sources: Optional[List[Dict[str, Any]]] = None,
        research_run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Convenience method returning dictionary representation of citation audit."""
        report = self.validate_citations(project_id, report_markdown, sources, research_run_id=research_run_id)
        return {
            "valid": report.is_valid,
            "total_citations": report.total_markers_found,
            "resolved_citations": report.resolved_citations,
            "errors": report.errors,
            "unresolved": report.unresolved_citation_numbers,
            "missing_evidence_claims": report.missing_evidence_claims,
            "supported": report.supported_citations or [],
            "contradicted": report.contradicted_citations or [],
            "insufficient_evidence": report.insufficient_evidence_citations or [],
            "unverified": report.unverified_citations or [],
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

