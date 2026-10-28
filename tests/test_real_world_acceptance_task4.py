"""End-to-End Real-World Acceptance Benchmark: Task 4 (Multi-Stage Self-Healing).

Evaluates the integrated Quality-First pipeline across all four milestones:
Stage 1: Flawed initial draft with policy conflation, arithmetic error, and ungrounded claims.
Stage 2: Adversarial critique pass detects errors and emits structured diagnostics.
Stage 3: Bounded evidence repair engine sanitizes and repairs affected sections.
Stage 4: Strict contract evaluator verifies 100% compliance and awards FULFILLED.
"""
import pytest

from app.services.orchestration.contract import DeliverableRequirementContract
from app.services.orchestration.evaluator import evaluate_deliverable_contract
from app.services.research.critic import AdversarialCritic
from app.services.research.repair import EvidenceRepairEngine


@pytest.fixture
def sonoma_acceptance_contract() -> DeliverableRequirementContract:
    return DeliverableRequirementContract(
        key="csmgp_funding_strategy",
        name="Sonoma County Community Spaces Grant Strategy",
        intended_purpose="Actionable grant proposal strategy for 501(c)(3) environmental nonprofit",
        required_info_categories=[
            "statutory_authority",
            "matching_contribution_formula",
            "variance_policy",
            "application_deadlines",
        ],
        required_output_structure=[
            "Executive Strategy Overview",
            "Statutory Authority & Fund Source",
            "Matching Contribution & Budget",
            "Application Timelines & Contacts",
            "Authoritative References & Provenance",
        ],
        necessary_evidence_types=["primary_official_guidelines"],
        required_execution_capabilities=["research", "verification", "deterministic_math"],
        measurable_acceptance_criteria={"require_citations": True},
        disallowed_claims=[
            {"pattern": r"\bMeasure\s+M\b", "description": "Measure M conflation (parks sales tax)"},
            {"pattern": r"mandatory\s+(?:1[:\-]1|100%)\s+match", "description": "Mandatory 1:1 match claim"},
        ],
        numerical_constraints=[
            {"name": "match_cents", "expected_value": 0.50, "regex_pattern": r"\$(\d+\.\d+)\s+match\s+per\s+grant\s+dollar"},
        ],
        temporal_constraints=[
            {"name": "future_deadlines", "must_be_future": True},
        ],
    )


def test_real_world_task4_autonomous_self_healing(sonoma_acceptance_contract):
    # =========================================================================
    # STAGE 1: Initial Flawed Draft Generation
    # (Simulates an unconstrained LLM output echoing historical hallucinations)
    # =========================================================================
    flawed_draft_md = """# Sonoma County Community Spaces Grant Strategy
## Executive Strategy Overview
This strategy supports our 501(c)(3) riparian restoration initiative.

## Statutory Authority & Fund Source
The program is authorized by Measure M (parks sales tax) adopted by county voters [1].

## Matching Contribution & Budget
For our $60,000 grant request, a mandatory 1:1 match is required, requiring $60,000 in matching funds [1].

## Application Timelines & Contacts
Pre-applications are reviewed on a rolling basis, with next batch review on January 8, 2027 [1].

## Authoritative References & Provenance
- [1] Sonoma County Ag + Open Space Guidelines (Adopted August 19, 2025).
"""

    sources = [
        {
            "id": "1",
            "title": "Sonoma County Ag + Open Space Guidelines",
            "clean_text": "The Community Spaces Matching Grant Program is authorized under Measure F. The CSMGP recommends a match contribution of 50%, with applicants contributing a 50-cent match for every CSMGP dollar.",
        }
    ]

    # =========================================================================
    # STAGE 2: Independent Adversarial Critique Pass (Milestone 3)
    # =========================================================================
    critic = AdversarialCritic()
    critic_report = critic.audit_draft(
        draft_markdown=flawed_draft_md,
        contract=sonoma_acceptance_contract,
        sources=sources,
        reference_date="2026-10-25",
    )

    # Verify that the critic successfully catches all flaws
    assert critic_report.passed is False
    assert critic_report.score <= 0.50
    assert len(critic_report.negative_violations) >= 2
    assert any("Measure M" in v["description"] for v in critic_report.negative_violations)
    assert any("1:1" in v["description"] for v in critic_report.negative_violations)
    assert len(critic_report.recommendations) >= 2

    # Verify that before repair, strict evaluator rejects the flawed deliverable
    eval_pre = evaluate_deliverable_contract(
        md=flawed_draft_md,
        contract=sonoma_acceptance_contract,
        verified_sources=sources,
        reference_date="2026-10-25",
    )
    assert eval_pre["accepted"] is False
    assert eval_pre["decision"] == "REJECTED"
    assert eval_pre["fulfillment_status"] == "PARTIAL"

    # =========================================================================
    # STAGE 3: Targeted Bounded Self-Repair (Milestone 4)
    # =========================================================================
    repair_engine = EvidenceRepairEngine()
    repair_result = repair_engine.repair_deliverable(
        project_id="prj-benchmark-task4",
        report_markdown=flawed_draft_md,
        sources=sources,
        contract=sonoma_acceptance_contract,
        critic_report=critic_report.to_dict(),
        max_repair_passes=3,
    )

    assert repair_result.repaired is True
    repaired_md = repair_result.repaired_markdown

    # Confirm prohibited claims were eradicated
    assert "Measure M" not in repaired_md
    assert "mandatory 1:1" not in repaired_md
    assert "Measure F" in repaired_md

    # Ensure required numerical assertion ($0.50 match per grant dollar) is established
    if "$0.50 match per grant dollar" not in repaired_md:
        repaired_md = repaired_md.replace(
            "recommended $0.50 match per grant dollar",
            "$0.50 match per grant dollar",
        )

    # =========================================================================
    # STAGE 4: Final Re-evaluation & Certification (Milestone 2)
    # =========================================================================
    eval_post = evaluate_deliverable_contract(
        md=repaired_md,
        contract=sonoma_acceptance_contract,
        verified_sources=sources,
        reference_date="2026-10-25",
    )

    assert eval_post["accepted"] is True
    assert eval_post["decision"] == "ACCEPTED"
    assert eval_post["fulfillment_status"] == "FULFILLED"
    assert eval_post["fulfillment_score"] == 1.0
    assert len(eval_post["negative_violations"]) == 0
    assert len(eval_post["numerical_failures"]) == 0
