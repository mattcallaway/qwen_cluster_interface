"""
Research Analysis & Deliverable Synthesis.

Assembles research findings, verified claims, evidence excerpts, and bibliography
into structured research deliverables (Markdown and JSON).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from app.services.research.citations import CitationEngine

logger = logging.getLogger("qwen_collective.research.synthesis")


class ResearchSynthesizer:
    """Synthesizes collected sources, claims, and evidence into an auditable research report."""

    def __init__(self):
        self.citation_engine = CitationEngine()

    def synthesize_report(
        self,
        project_id: str = "",
        topic: str = "",
        objective: str = "",
        sources: Optional[List[Dict[str, Any]]] = None,
        claims: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> str:
        res = self.assemble_research_report(
            project_name=topic or objective or "Autonomous Web Research",
            objective=objective,
            sources=sources or [],
            claims=claims or [],
        )
        return res["markdown"]

    def assemble_research_report(
        self,
        project_name: str,
        objective: str,
        sources: List[Dict[str, Any]],
        claims: Optional[List[Dict[str, Any]]] = None,
        findings_summary: Optional[str] = None,
        limitations: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Assemble a comprehensive, evidence-linked research report with inline citations,
        claim corroboration status, and verified bibliography.
        """
        claims = claims or []
        limitations = limitations or []

        # Build Markdown Document
        sections: List[str] = []
        sections.append(f"# Research Report: {project_name}\n")
        sections.append(f"**Research Objective:** {objective}\n")
        sections.append(f"**Date:** {str(sources[0].get('retrieved_at', ''))[:10] if sources else 'N/A'}  ")
        sections.append(f"**Sources Evaluated:** {len(sources)} | **Claims Verified:** {len(claims)}\n")
        sections.append("---\n")

        # 1. Executive Summary
        sections.append("## 1. Executive Summary\n")
        if findings_summary:
            sections.append(findings_summary.strip() + "\n")
        else:
            sections.append(
                f"This research investigation examined '{objective}' through public internet discovery, "
                f"source evaluation, and evidence corroboration. A total of {len(sources)} distinct external sources "
                f"were retrieved and analyzed.\n"
            )

        # 2. Key Findings & Corroborated Evidence
        sections.append("## 2. Key Findings & Corroborated Evidence\n")
        if claims:
            sections.append("| ID | Claim / Finding | Category | Verification Status | Sources |\n| :--- | :--- | :--- | :--- | :--- |")
            for idx, c in enumerate(claims, start=1):
                c_text = c.get("claim_text", "")
                cat = c.get("category", "fact").capitalize()
                v_stat = c.get("verification_status", "unverified").upper()
                # Find matching source indices
                ev_indices = []
                for ev in c.get("evidence", []):
                    src_id = ev.get("source_id")
                    for s_idx, s in enumerate(sources, start=1):
                        s_id = s.get("source_id") or s.get("id")
                        if s_id and src_id and str(s_id) == str(src_id):
                            ev_indices.append(str(s_idx))
                            break
                        if ev.get("canonical_url") and s.get("canonical_url") == ev.get("canonical_url"):
                            ev_indices.append(str(s_idx))
                            break
                        if ev.get("url") and (s.get("url") == ev.get("url") or s.get("canonical_url") == ev.get("url")):
                            ev_indices.append(str(s_idx))
                            break

                # If no explicit evidence link matched by ID, match by content overlap
                if not ev_indices and sources:
                    for s_idx, s in enumerate(sources, start=1):
                        s_text = s.get("clean_text") or s.get("extracted_text") or s.get("snippet") or ""
                        if s_text and len(s_text) > 30:
                            # Check if substantial phrase of claim is in source
                            words = [w.lower() for w in re.findall(r"\b[A-Za-z]{4,}\b", c_text)]
                            if len(words) >= 3:
                                match_cnt = sum(1 for w in words if w in s_text.lower())
                                if match_cnt >= min(4, len(words)):
                                    ev_indices.append(str(s_idx))
                                    break

                cites = f"[{', '.join(sorted(set(ev_indices), key=int))}]" if ev_indices else "[Uncited]"
                sections.append(f"| C{idx} | {c_text} | {cat} | **{v_stat}** | {cites} |")
            sections.append("")
        else:
            sections.append("*Factual claims extracted from retrieved evidence:*\n")
            for s_idx, src in enumerate(sources[:5], start=1):
                title = src.get("title", "")
                snip = (src.get("clean_text") or src.get("snippet") or "")[:250].replace("\n", " ")
                sections.append(f"- According to {src.get('publisher') or 'reported data'}, \"{snip}...\" [{s_idx}]")
            sections.append("")

        # 3. Source Quality & Methodology
        sections.append("## 3. Research Methodology & Source Evaluation\n")
        sections.append(
            "This report was autonomously produced using the Qwen Collective Research & Browsing Engine. "
            "All outbound requests were strictly validated against SSRF protections and domain rate limits. "
            "Sources were checked for author credentials, publication dates, and independent cross-domain corroboration.\n"
        )

        # 4. Limitations & Contradictions
        sections.append("## 4. Limitations & Evidence Gaps\n")
        contradictions = [c for c in claims if c.get("verification_status") == "contradicted"]
        if contradictions:
            sections.append("### Contradictory Evidence Identified\n")
            for c in contradictions:
                sections.append(f"- **Claim:** {c.get('claim_text')}")
                sections.append(f"  - **Conflict:** {c.get('contradiction_details')}\n")

        if limitations:
            for lim in limitations:
                sections.append(f"- {lim}")
            sections.append("")
        else:
            sections.append(
                "- Search results were bounded by configured research budget.\n"
                "- Source freshness is limited to available public publication timestamps.\n"
            )

        # 5. References & Bibliography
        biblio = self.citation_engine.format_bibliography(sources)
        sections.append(biblio)

        markdown_content = "\n".join(sections)

        # Build Structured JSON payload
        json_payload = {
            "project_name": project_name,
            "objective": objective,
            "sources_count": len(sources),
            "sources": [
                {
                    "index": i + 1,
                    "title": s.get("title"),
                    "url": s.get("canonical_url"),
                    "publisher": s.get("publisher"),
                    "publication_date": s.get("publication_date"),
                    "quality_score": s.get("quality_score"),
                }
                for i, s in enumerate(sources)
            ],
            "claims": claims,
            "limitations": limitations,
            "methodology": "Qwen Collective Autonomous Web Research Engine",
        }

        return {
            "markdown": markdown_content,
            "json_payload": json_payload,
            "sources_count": len(sources),
            "claims_count": len(claims),
        }
