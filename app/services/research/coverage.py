"""Evidence Sufficiency and Coverage Assessment Engine.

Evaluates retrieved research evidence against the explicit DeliverableRequirementContract.
Determines whether required information categories are adequately supported by verified
sources or if critical evidence gaps remain.

Statuses:
- SUFFICIENT — READY FOR SYNTHESIS: Core required information found and verified.
- PARTIAL — GAPS REMAIN: Some categories supported, but documented gaps exist.
- INSUFFICIENT — FURTHER RESEARCH REQUIRED: Evidence does not address core deliverable needs.
- BLOCKED — REQUIRED EVIDENCE UNAVAILABLE: Searches exhausted without finding essential data.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("qc.research.coverage")


def assess_evidence_coverage(
    contracts: List[Dict[str, Any]],
    sources: List[Dict[str, Any]],
    claims: List[Dict[str, Any]],
    project_objective: str = "",
) -> Dict[str, Any]:
    """Assess whether retrieved sources and verified claims cover deliverable requirements.

    Domain-agnostic: matches content concepts against contract categories and checks
    source relevance and corroboration.
    """
    total_sources = len(sources)
    total_claims = len(claims)

    # Compute source metrics
    authoritative_sources = [s for s in sources if s.get("reliability_score", 0.0) >= 0.5 or s.get("is_primary")]
    avg_authority = (
        sum(s.get("reliability_score", 0.0) for s in sources) / max(1, total_sources)
        if total_sources else 0.0
    )

    # Calculate topical relevance across all retrieved source snippets and titles
    obj_terms = {w.lower() for w in re.findall(r"\b[A-Za-z0-9\-]{4,}\b", project_objective)}
    source_corpus = " ".join([
        (s.get("title") or "") + " " + (s.get("snippet") or "") + " " + (s.get("extracted_text") or "")[:2000]
        for s in sources
    ]).lower()
    claim_corpus = " ".join([c.get("claim_text", "") for c in claims]).lower()

    deliverable_assessments = []
    overall_readiness_counts = {"SUFFICIENT": 0, "PARTIAL": 0, "INSUFFICIENT": 0, "BLOCKED": 0}

    for raw_contract in contracts:
        contract = raw_contract.to_dict() if hasattr(raw_contract, "to_dict") else (raw_contract if isinstance(raw_contract, dict) else vars(raw_contract))
        c_key = contract.get("key", "")
        c_name = contract.get("name", "")
        req_cats = contract.get("required_info_categories", [])
        topic_kws = contract.get("topic_keywords", [])

        supported_cats = []
        missing_cats = []
        matching_claims = []

        # Category keyword heuristics: check if claims or sources address this category
        for cat in req_cats:
            cat_words = [w for w in cat.split("_") if len(w) > 3]
            cat_matched = False

            # Check if any claim touches this category and the deliverable's topic
            for clm in claims:
                c_text = clm.get("claim_text", "").lower()
                # Check for category relevance
                has_cat_word = any(w in c_text for w in cat_words)
                # Check for topic alignment
                has_topic_word = any(t.lower() in c_text for t in topic_kws) if topic_kws else True

                if has_cat_word or (has_topic_word and any(w in source_corpus for w in cat_words)):
                    cat_matched = True
                    if clm not in matching_claims:
                        matching_claims.append(clm)
                    break

            if cat_matched:
                supported_cats.append(cat)
            else:
                missing_cats.append(cat)

        # Check for ledger / provenance / source register meta-deliverables
        is_ledger = any(k in c_name.lower() or k in c_key.lower() for k in ("ledger", "provenance", "audit", "sources_and_verification"))
        if is_ledger and total_sources >= 2 and total_claims >= 2:
            for cat in list(missing_cats):
                if any(k in cat for k in ("source", "url", "provenance", "claim", "corroborat", "reliability", "confidence")):
                    missing_cats.remove(cat)
                    supported_cats.append(cat)

        # Topical relevance: check whether evidence corpus grounds the project objective and domain topic
        target_terms = [t.lower() for t in topic_kws] if topic_kws else [w for w in obj_terms if len(w) > 4]
        if target_terms:
            relevance_matches = sum(1 for w in target_terms if w in source_corpus or w in claim_corpus)
            relevance_score = min(1.0, relevance_matches / max(1, min(5, len(target_terms))))
        else:
            relevance_score = 0.5

        # Corroboration and contradiction analysis
        supported_claims = [
            c for c in matching_claims
            if c.get("verification_status") in ("SUPPORTED", "supported")
            or c.get("status") in ("corroborated", "supported", "verified")
        ]
        contradicted_claims = [c for c in matching_claims if c.get("contradiction_details") or c.get("verification_status") == "CONTRADICTED"]

        corroboration_rate = len(supported_claims) / max(1, len(matching_claims)) if matching_claims else 0.0

        # Confidence calculation
        coverage_ratio = len(supported_cats) / max(1, len(req_cats)) if req_cats else 0.0
        confidence = round(
            (coverage_ratio * 0.45) + (relevance_score * 0.35) + (min(1.0, len(matching_claims) / 2.0) * 0.20),
            2
        )

        # Status determination
        if not sources or not claims:
            status = "BLOCKED"
            detail = "Required evidence is unavailable; no sources or claims found."
        elif coverage_ratio >= 0.70 and relevance_score >= 0.40 and len(supported_claims) >= 1:
            status = "SUFFICIENT"
            detail = f"Sufficient evidence available: {len(supported_cats)}/{len(req_cats)} required categories supported."
        elif coverage_ratio >= 0.30 or len(supported_claims) >= 1:
            status = "PARTIAL"
            detail = f"Partial evidence available: {len(supported_cats)}/{len(req_cats)} categories supported; {len(missing_cats)} gaps remain."
        else:
            status = "INSUFFICIENT"
            detail = f"Evidence is insufficient for '{c_name}': general sources retrieved do not address specific deliverable requirements."

        overall_readiness_counts[status] += 1

        deliverable_assessments.append({
            "key": c_key,
            "name": c_name,
            "status": status,
            "readiness_for_synthesis": status,
            "confidence": confidence,
            "research_confidence": confidence,
            "relevance_score": round(relevance_score, 2),
            "evidence_relevance_score": round(relevance_score, 2),
            "required_categories": req_cats,
            "supported_categories": supported_cats,
            "satisfied_categories": supported_cats,
            "missing_categories": missing_cats,
            "missing_evidence": missing_cats,
            "supporting_claims_count": len(matching_claims),
            "corroborated_claims_count": len(supported_claims),
            "contradictions_count": len(contradicted_claims),
            "detail": detail,
        })

    # Overall project evidence readiness
    if overall_readiness_counts["INSUFFICIENT"] == len(contracts) or (not sources and not claims):
        overall_status = "INSUFFICIENT"
        overall_summary = "Retrieved evidence does not address the core requirements for project deliverables."
    elif overall_readiness_counts["SUFFICIENT"] == len(contracts):
        overall_status = "SUFFICIENT"
        overall_summary = "All project deliverables have sufficient verified evidence coverage."
    elif overall_readiness_counts["BLOCKED"] > 0:
        overall_status = "BLOCKED"
        overall_summary = "Required evidence could not be retrieved; critical information is missing."
    else:
        overall_status = "PARTIAL"
        overall_summary = f"Evidence partially covers project requirements; {overall_readiness_counts['PARTIAL'] + overall_readiness_counts['INSUFFICIENT']} deliverables have evidence gaps."

    return {
        "overall_status": overall_status,
        "overall_summary": overall_summary,
        "total_sources_evaluated": total_sources,
        "authoritative_sources_count": len(authoritative_sources),
        "total_claims_evaluated": total_claims,
        "average_source_authority": round(avg_authority, 2),
        "deliverable_assessments": deliverable_assessments,
        "readiness_breakdown": overall_readiness_counts,
    }
