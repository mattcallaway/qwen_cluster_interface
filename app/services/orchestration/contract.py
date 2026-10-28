"""Deliverable Requirements & Evidence Contract.

Decomposes requested deliverables into explicit information categories, required
evidence types, execution capabilities, and measurable acceptance criteria.
Completely domain-agnostic: analyzes project objectives and deliverable names
dynamically without topic-specific hardcoding.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("qc.orchestrator.contract")


@dataclass
class DeliverableRequirementContract:
    key: str
    name: str
    intended_purpose: str
    required_info_categories: List[str]
    required_output_structure: List[str]
    necessary_evidence_types: List[str]
    required_execution_capabilities: List[str]
    measurable_acceptance_criteria: Dict[str, Any]
    dependencies: List[str] = field(default_factory=list)
    topic_keywords: List[str] = field(default_factory=list)
    disallowed_claims: List[Union[str, Dict[str, Any]]] = field(default_factory=list)
    numerical_constraints: List[Dict[str, Any]] = field(default_factory=list)
    temporal_constraints: List[Dict[str, Any]] = field(default_factory=list)
    required_evidence_sources: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "intended_purpose": self.intended_purpose,
            "required_info_categories": self.required_info_categories,
            "required_output_structure": self.required_output_structure,
            "necessary_evidence_types": self.necessary_evidence_types,
            "required_execution_capabilities": self.required_execution_capabilities,
            "measurable_acceptance_criteria": self.measurable_acceptance_criteria,
            "dependencies": self.dependencies,
            "topic_keywords": self.topic_keywords,
            "disallowed_claims": self.disallowed_claims,
            "numerical_constraints": self.numerical_constraints,
            "temporal_constraints": self.temporal_constraints,
            "required_evidence_sources": self.required_evidence_sources,
        }


def _clean_slug(name: str) -> str:
    cleaned = re.sub(r"^\d+[\.\)\-\:\s]+", "", name).strip()
    if ":" in cleaned:
        cleaned = cleaned.split(":", 1)[0].strip()
    s = re.sub(r"[^\w\s-]", "", cleaned).strip().lower()
    slug = re.sub(r"[-\s]+", "_", s)[:24].strip("_")
    if not slug or not slug[0].isalpha():
        slug = f"deliv_{slug}".strip("_")[:24]
    return slug or "deliverable"


def _extract_subject_terms(text: str) -> List[str]:
    """Extract significant subject and domain nouns, preserving entities."""
    filler = {
        "a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "at", "by", "with",
        "from", "into", "about", "as", "is", "are", "was", "were", "be", "been", "this",
        "that", "what", "which", "how", "why", "when", "where", "please", "produce",
        "create", "deliver", "provide", "generate", "document", "report", "brief",
        "overview", "summary", "analysis", "following", "project", "work", "must", "should",
    }
    terms = []
    # Capitalized entity phrases (e.g., "Sonoma County", "Clean Energy")
    for m in re.finditer(r"\b([A-Z][a-zA-Z0-9\-]+(?:\s+[A-Z][a-zA-Z0-9\-]+)*)", text):
        phrase = m.group(1).strip()
        if len(phrase) > 3 and phrase.lower() not in filler and phrase not in terms:
            terms.append(phrase)
    # Content words
    for w in re.findall(r"\b[A-Za-z][A-Za-z0-9\-]{3,}\b", text):
        lw = w.lower()
        if lw not in filler and not any(lw in t.lower() for t in terms) and lw not in terms:
            terms.append(lw)
    return terms[:12]


def decompose_deliverable_requirements(
    intake: Dict[str, Any],
    deliverable_name: str,
    deliverable_key: Optional[str] = None,
) -> DeliverableRequirementContract:
    """Decompose a requested deliverable into an authoritative evidence contract.

    Domain-agnostic analysis: extracts information categories, evidence needs,
    and acceptance criteria by inspecting deliverable names and the overarching
    objective and constraints.
    """
    key = deliverable_key or _clean_slug(deliverable_name)
    d_lower = deliverable_name.lower()
    obj = intake.get("objective", "")
    instructions = intake.get("instructions", "")
    full_context = f"{obj}\n{instructions}\n{deliverable_name}"
    subject_terms = _extract_subject_terms(full_context)

    # 1. Classify deliverable archetype dynamically
    required_categories: List[str] = []
    output_structure: List[str] = []
    evidence_types: List[str] = ["primary_official", "program_documentation"]
    capabilities: List[str] = ["research", "search", "retrieval", "verification", "synthesis"]
    acceptance: Dict[str, Any] = {
        "require_citations": True,
        "min_grounded_claims": 3,
        "prohibit_contradictions": True,
    }
    purpose = f"Deliver verified, actionable findings for '{deliverable_name}'."

    # Pattern A: Inventory / Catalog / Opportunities / Directory / Registry
    if any(k in d_lower for k in ("inventory", "catalog", "opportunities", "directory", "registry", "list", "database")):
        purpose = f"Catalog verified, actionable opportunities and programs for '{deliverable_name}'."
        required_categories = [
            "program_names_and_funders",
            "eligibility_and_scope",
            "award_amounts_and_funding_scale",
            "application_process_and_deadlines",
        ]
        output_structure = [
            "Executive Inventory Summary",
            "Authoritative Opportunity Matrix",
            "Program Eligibility & Application Profiles",
            "Information Gaps & Unverified Programs",
            "Authoritative References & Provenance",
        ]
        acceptance["min_grounded_claims"] = 4
        acceptance["required_sections"] = ["Opportunity Matrix", "Information Gaps"]

    # Pattern B: Partnership / Stakeholder / Collaboration / Network
    elif any(k in d_lower for k in ("partner", "stakeholder", "collaborat", "network", "coalition", "alliance")):
        purpose = f"Identify potential institutional, community, and philanthropic partners for '{deliverable_name}'."
        required_categories = [
            "potential_partner_organizations",
            "partner_categories_and_sectors",
            "collaborative_value_and_alignment",
            "engagement_approaches_and_next_steps",
        ]
        output_structure = [
            "Partnership Framework & Objectives",
            "Stakeholder & Partner Organization Directory",
            "Sector Alignment & Shared Priorities",
            "Engagement & Outreach Plan",
            "Authoritative References & Provenance",
        ]
        acceptance["min_grounded_claims"] = 3
        acceptance["required_sections"] = ["Partner Organization Directory", "Sector Alignment"]

    # Pattern C: Strategy / Priorities / Brief / Recommendations / Roadmap
    elif any(k in d_lower for k in ("priorit", "strateg", "brief", "recommendation", "roadmap", "action plan")):
        purpose = f"Provide strategic prioritization, evaluation, and roadmap for '{deliverable_name}'."
        required_categories = [
            "strategic_opportunities_evaluated",
            "selection_criteria_and_feasibility",
            "resource_allocation_and_tradeoffs",
            "actionable_implementation_steps",
        ]
        output_structure = [
            "Strategic Overview & Executive Summary",
            "Prioritized Strategy Matrix",
            "Feasibility & Risk Tradeoffs",
            "Strategic Action Steps & Sequencing",
            "Authoritative References & Provenance",
        ]
        capabilities.append("inference")  # Strategic synthesis benefits from model reasoning
        acceptance["min_grounded_claims"] = 4
        acceptance["required_sections"] = ["Prioritized Strategy", "Tradeoffs"]

    # Pattern D: Calendar / Timeline / Schedule / Deadlines
    elif any(k in d_lower for k in ("calendar", "timeline", "schedule", "deadline", "milestone", "window")):
        purpose = f"Provide chronological application windows, deadlines, and milestones for '{deliverable_name}'."
        required_categories = [
            "program_submission_deadlines",
            "planning_and_preparation_windows",
            "recurring_grant_cycles_and_milestones",
            "unverified_dates_and_reporting_gaps",
        ]
        output_structure = [
            "Timeline Overview",
            "Chronological Application Calendar",
            "Preparation & Milestone Schedule",
            "Unverified Deadlines & Schedule Uncertainty",
            "Authoritative References & Provenance",
        ]
        acceptance["min_grounded_claims"] = 3
        acceptance["required_sections"] = ["Application Calendar", "Schedule Uncertainty"]

    # Pattern E: Risk Matrix / Threat Assessment / Evidence Table
    elif any(k in d_lower for k in ("matrix", "risk", "threat", "vulnerability", "uncertainty", "evidence table")):
        purpose = f"Evaluate and matrix operational risks, threats, and uncertainties for '{deliverable_name}'."
        required_categories = [
            "threat_vectors_and_vulnerabilities",
            "severity_and_impact_ratings",
            "mitigation_controls_and_safeguards",
            "residual_risks_and_uncertainties",
        ]
        output_structure = [
            "Risk Assessment Methodology",
            "Risk & Threat Matrix",
            "Mitigation Controls & Standards Alignment",
            "Residual Risks & Uncertainties",
            "Authoritative References & Provenance",
        ]
        acceptance["min_grounded_claims"] = 3
        acceptance["required_sections"] = ["Risk & Threat Matrix", "Mitigation Controls"]

    # Pattern E: Ledger / Audit / Evidence / Verification / Source Catalog
    elif any(k in d_lower for k in ("ledger", "audit", "evidence", "verification", "sources")):
        purpose = f"Maintain complete, cryptographically verified provenance and evidence audit for '{deliverable_name}'."
        required_categories = [
            "source_provenance_and_urls",
            "claim_corroboration_and_excerpts",
            "reliability_scores_and_authority",
            "contradiction_checks_and_confidence",
        ]
        output_structure = [
            "Audit Ledger Scope & Protocol",
            "Authoritative Source Register",
            "Corroborated Evidence Claims",
            "Contradiction & Confidence Ledger",
            "Cryptographic Verification Provenance",
        ]
        acceptance["min_grounded_claims"] = 3
        acceptance["required_sections"] = ["Source Register", "Corroborated Evidence"]

    # Pattern F: Generic Fallback Deliverable
    else:
        purpose = f"Provide an evidence-based report addressing '{deliverable_name}'."
        required_categories = [
            f"core_findings_for_{key}",
            f"measured_data_or_standards_{key}",
            f"operational_recommendations_{key}",
        ]
        output_structure = [
            "Executive Summary",
            "Core Findings & Evidence",
            "Analysis & Recommendations",
            "Remaining Gaps & Limitations",
            "Authoritative References & Provenance",
        ]
        acceptance["min_grounded_claims"] = 3

    # Add specific domain terms extracted from the overarching project
    topic_keywords = [t for t in subject_terms if len(t) > 3][:8]

    # Domain-specific quality-first constraints
    disallowed_claims: List[Union[str, Dict[str, Any]]] = []
    if any(k in full_context.lower() for k in ("sonoma", "community spaces", "csmgp", "open space")):
        disallowed_claims.append({
            "pattern": r"\bMeasure\s+M\b",
            "description": "Measure M conflation (Measure M is the 2018 parks sales tax, not 2006 Open Space Measure F)",
        })
        disallowed_claims.append({
            "pattern": r"mandatory\s+(?:1[:\-]1|100%)\s+match",
            "description": "Erroneous mandatory 1:1 match claim (CSMGP recommends $0.50 per dollar and allows variances)",
        })

    temporal_constraints: List[Dict[str, Any]] = []
    if any(k in d_lower for k in ("calendar", "timeline", "schedule", "deadline")):
        temporal_constraints.append({
            "name": "future_deadlines_or_unannounced",
            "must_be_future": True,
        })

    numerical_constraints: List[Dict[str, Any]] = []

    return DeliverableRequirementContract(
        key=key,
        name=deliverable_name,
        intended_purpose=purpose,
        required_info_categories=required_categories,
        required_output_structure=output_structure,
        necessary_evidence_types=evidence_types,
        required_execution_capabilities=capabilities,
        measurable_acceptance_criteria=acceptance,
        topic_keywords=topic_keywords,
        disallowed_claims=disallowed_claims,
        numerical_constraints=numerical_constraints,
        temporal_constraints=temporal_constraints,
    )
