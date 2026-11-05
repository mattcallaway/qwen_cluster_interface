"""
Evidence-Based Claim & Citation Repair Workflow.

Implements bounded, deterministic repair for research deliverables:
1. Identifies unsupported or contradicted claims and their citation markers.
2. Locates the exact supporting or contradicting passage from the cited source.
3. Determines whether the source establishes a verifiable replacement.
4. Replaces only the affected statement or table row using source evidence.
5. Re-runs claim and citation verification to ensure 100% strict entailment.
6. Preserves the full repair history and diagnostic trace.
7. If repair cannot be verified, marks deliverable incomplete or requiring human review.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.services.research.citations import CitationEngine, CitationValidationReport
from app.services.research.entailment import verify_claim_against_source

logger = logging.getLogger("qwen_collective.research.repair")


@dataclass
class ClaimRepairRecord:
    """Record of a single claim repair attempt."""
    citation_index: int
    original_statement: str
    failure_type: str  # "CONTRADICTED", "INSUFFICIENT_EVIDENCE", "UNVERIFIED", "UNRESOLVED"
    failure_reason: str
    source_url: str
    source_passage: Optional[str]
    repaired_statement: Optional[str]
    success: bool
    status: str  # "REPAIRED", "UNREPAIRABLE_REQUIRES_HUMAN_REVIEW"


@dataclass
class DeliverableRepairResult:
    """Result of running evidence-based repair on a deliverable."""
    is_valid: bool
    repaired: bool
    original_markdown: str
    repaired_markdown: str
    repair_count: int
    repair_history: List[ClaimRepairRecord] = field(default_factory=list)
    final_audit: Optional[Dict[str, Any]] = None


class EvidenceRepairEngine:
    """Repairs ungrounded or contradictory claims using authentic source excerpts."""

    def __init__(self, citation_engine: Optional[CitationEngine] = None):
        self.citation_engine = citation_engine or CitationEngine()

    def repair_deliverable(
        self,
        project_id: str,
        report_markdown: str,
        sources: List[Dict[str, Any]],
        research_run_id: Optional[str] = None,
        max_repair_passes: int = 2,
        contract: Optional[Any] = None,
        critic_report: Optional[Dict[str, Any]] = None,
    ) -> DeliverableRepairResult:
        """
        Executes bounded evidence-based repair on report_markdown.
        Strictly preserves citation gates: never introduces fuzzy tolerance.
        """
        current_md = report_markdown
        repair_history: List[ClaimRepairRecord] = []
        source_by_idx = {i + 1: s for i, s in enumerate(sources)}

        for pass_num in range(max_repair_passes):
            pass_repairs = 0

            # Quality-First negative constraint repair (Milestone 3/4)
            if contract:
                cd = contract.to_dict() if hasattr(contract, "to_dict") else (contract if isinstance(contract, dict) else {})
                disallowed = cd.get("disallowed_claims") or []
                for claim in disallowed:
                    pattern = claim.get("pattern", "") if isinstance(claim, dict) else claim
                    desc = claim.get("description", pattern) if isinstance(claim, dict) else pattern
                    if pattern and re.search(pattern, current_md, re.IGNORECASE):
                        # A prohibited token does not identify a true replacement.
                        # Preserve the complete assertion, including negation.
                        repair_history.append(ClaimRepairRecord(
                            citation_index=0, original_statement=current_md,
                            failure_type="PROHIBITED_CLAIM_VIOLATION",
                            failure_reason=desc, source_url="",
                            source_passage=None, repaired_statement=None,
                            success=False,
                            status="UNREPAIRABLE_REQUIRES_HUMAN_REVIEW",
                        ))
                        return DeliverableRepairResult(
                            is_valid=False, repaired=False,
                            original_markdown=report_markdown,
                            repaired_markdown=report_markdown, repair_count=0,
                            repair_history=repair_history,
                            final_audit={"is_valid": False,
                                "errors": ["Evidence-backed whole-claim repair required"]},
                        )

            audit = self.citation_engine.validate_citations(
                project_id=project_id,
                report_markdown=current_md,
                sources=sources,
                research_run_id=research_run_id,
            )

            if audit.is_valid:
                return DeliverableRepairResult(
                    is_valid=True,
                    repaired=(pass_num > 0),
                    original_markdown=report_markdown,
                    repaired_markdown=current_md,
                    repair_count=len([r for r in repair_history if r.success]),
                    repair_history=repair_history,
                    final_audit=audit.to_dict(),
                )

            # Analyze failures and identify repair candidates
            pass_repairs = 0
            for err in audit.errors:
                # Extract citation index from error string: "Citation [N]"
                m = re.search(r"Citation\s+\[(\d+)\]", err)
                if not m:
                    continue
                idx = int(m.group(1))
                src = source_by_idx.get(idx)
                if not src:
                    repair_history.append(
                        ClaimRepairRecord(
                            citation_index=idx,
                            original_statement=f"Citation [{idx}]",
                            failure_type="UNRESOLVED",
                            failure_reason=err,
                            source_url="Unknown",
                            source_passage=None,
                            repaired_statement=None,
                            success=False,
                            status="UNREPAIRABLE_REQUIRES_HUMAN_REVIEW",
                        )
                    )
                    continue

                src_text = src.get("extracted_text") or src.get("clean_text") or ""
                src_url = src.get("canonical_url") or src.get("original_url") or "N/A"

                # Find statement containing [idx] in current markdown
                marker = f"[{idx}]"
                marker_pos = current_md.find(marker)
                if marker_pos == -1:
                    continue

                line_start = max(0, current_md.rfind("\n", 0, marker_pos) + 1)
                line_end = current_md.find("\n", marker_pos)
                if line_end == -1:
                    line_end = len(current_md)
                original_line = current_md[line_start:line_end]

                # Check entailment to get exact conflicting or grounded passage
                entailment = verify_claim_against_source(original_line, src_text)
                target_passage = entailment.conflicting_excerpt or entailment.matched_quote

                if not target_passage:
                    repair_history.append(
                        ClaimRepairRecord(
                            citation_index=idx,
                            original_statement=original_line,
                            failure_type=entailment.status,
                            failure_reason=err,
                            source_url=src_url,
                            source_passage=None,
                            repaired_statement=None,
                            success=False,
                            status="UNREPAIRABLE_REQUIRES_HUMAN_REVIEW",
                        )
                    )
                    continue

                # Determine replacement statement strictly adhering to target_passage
                cleaned_target = " ".join(target_passage.split()).strip().rstrip(".")
                if "|" in original_line:
                    # Markdown table row: preserve table formatting while substituting verified passage
                    cells = original_line.split("|")
                    # Replace longest text cell with grounded target passage
                    text_idx = -1
                    max_len = 0
                    for c_idx, cell in enumerate(cells):
                        c_clean = cell.strip()
                        if c_clean and not c_clean.startswith("[") and not c_clean.startswith(":") and len(c_clean) > max_len:
                            if not re.match(r"^C\d+$", c_clean) and c_clean not in ("Verified Fact", "SUPPORTED"):
                                max_len = len(c_clean)
                                text_idx = c_idx
                    if text_idx != -1:
                        cells[text_idx] = f" {cleaned_target} "
                        repaired_line = "|".join(cells)
                    else:
                        repaired_line = original_line
                else:
                    # Paragraph / narrative line: substitute sentence containing marker with grounded target
                    repaired_line = f"{cleaned_target} [{idx}]."

                # Apply replacement
                current_md = current_md[:line_start] + repaired_line + current_md[line_end:]
                pass_repairs += 1
                repair_history.append(
                    ClaimRepairRecord(
                        citation_index=idx,
                        original_statement=original_line.strip(),
                        failure_type=entailment.status,
                        failure_reason=err,
                        source_url=src_url,
                        source_passage=target_passage,
                        repaired_statement=repaired_line.strip(),
                        success=True,
                        status="REPAIRED",
                    )
                )

            if pass_repairs == 0:
                break

        # Final audit after all passes
        final_audit = self.citation_engine.validate_citations(
            project_id=project_id,
            report_markdown=current_md,
            sources=sources,
            research_run_id=research_run_id,
        )

        return DeliverableRepairResult(
            is_valid=final_audit.is_valid,
            repaired=len([r for r in repair_history if r.success]) > 0,
            original_markdown=report_markdown,
            repaired_markdown=current_md,
            repair_count=len([r for r in repair_history if r.success]),
            repair_history=repair_history,
            final_audit=final_audit.to_dict(),
        )
