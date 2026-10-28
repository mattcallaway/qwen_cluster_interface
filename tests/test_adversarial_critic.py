"""Unit tests for Adversarial Critic & Independent Audit Pass (Phase C Milestone 3)."""
from datetime import date
import pytest

from app.services.orchestration.contract import DeliverableRequirementContract
from app.services.research.critic import AdversarialCritic, CriticReport
from app.services.research.manager import ResearchManager
from app.services.research.repair import EvidenceRepairEngine


@pytest.fixture
def sample_contract() -> DeliverableRequirementContract:
    return DeliverableRequirementContract(
        key="funding_inventory",
        name="Verified Funding Opportunities Inventory",
        intended_purpose="Catalog verified opportunities",
        required_info_categories=[
            "program_names_and_funders",
            "eligibility_and_scope",
            "award_amounts_and_funding_scale",
            "application_process_and_deadlines",
        ],
        required_output_structure=["Executive Summary", "Opportunity Matrix"],
        necessary_evidence_types=["primary_official"],
        required_execution_capabilities=["research"],
        measurable_acceptance_criteria={"require_citations": True},
        disallowed_claims=[
            {"pattern": r"\bMeasure\s+M\b", "description": "Measure M conflation"},
            {"pattern": r"mandatory\s+(?:1[:\-]1|100%)\s+match", "description": "Mandatory 1:1 match claim"},
        ],
        numerical_constraints=[
            {"name": "match_cents", "expected_value": 0.50, "regex_pattern": r"\$(\d+\.\d+)\)\s+per\s+grant\s+dollar"},
        ],
        temporal_constraints=[
            {"name": "future_deadlines", "must_be_future": True},
        ],
    )


@pytest.fixture
def valid_draft_md() -> str:
    return """# Verified Funding Opportunities Inventory
## Executive Summary
This document catalogs verified grant opportunities for Sonoma County environmental programs.

## Opportunity Matrix
* Funder: Sonoma County Ag + Open Space.
* Program: Community Spaces Matching Grant Program.
* Statutory Basis: Measure F (2006 sales tax) [src-1].
* Match Requirement: The recommended match is 50-cents ($0.50) per grant dollar [src-1].
* Application Process: Rolling pre-applications, next batch reviewed January 8, 2027 [src-1].
* Scope: Disadvantaged community variance is available upon written application [src-1].
"""


def test_adversarial_critic_perfect_draft(valid_draft_md, sample_contract):
    critic = AdversarialCritic()
    sources = [{"id": "src-1", "title": "CSMGP Guidelines"}]
    report = critic.audit_draft(
        draft_markdown=valid_draft_md,
        contract=sample_contract,
        sources=sources,
        reference_date="2026-10-25",
    )

    assert report.passed is True
    assert report.score == 1.0
    assert len(report.fatal_errors) == 0
    assert len(report.negative_violations) == 0
    assert len(report.arithmetic_errors) == 0


def test_adversarial_critic_catches_prohibited_claims(valid_draft_md, sample_contract):
    corrupted_md = valid_draft_md.replace(
        "Measure F (2006 sales tax)", "Measure M (parks sales tax)"
    ).replace(
        "recommended match is 50-cents ($0.50)", "mandatory 1:1 match is required ($0.50)"
    )

    critic = AdversarialCritic()
    sources = [{"id": "src-1", "title": "CSMGP Guidelines"}]
    report = critic.audit_draft(
        draft_markdown=corrupted_md,
        contract=sample_contract,
        sources=sources,
        reference_date="2026-10-25",
    )

    assert report.passed is False
    assert len(report.negative_violations) >= 2
    assert any("Measure M" in v["description"] for v in report.negative_violations)
    assert any("1:1" in v["description"] for v in report.negative_violations)
    assert len(report.recommendations) >= 2


def test_adversarial_critic_catches_arithmetic_error(valid_draft_md, sample_contract):
    # Change $0.50 to $1.00
    corrupted_md = valid_draft_md.replace("($0.50)", "($1.00)")

    critic = AdversarialCritic()
    sources = [{"id": "src-1", "title": "CSMGP Guidelines"}]
    report = critic.audit_draft(
        draft_markdown=corrupted_md,
        contract=sample_contract,
        sources=sources,
        reference_date="2026-10-25",
    )

    assert report.passed is False
    assert len(report.arithmetic_errors) >= 1
    assert report.arithmetic_errors[0]["name"] == "match_cents"


def test_adversarial_critic_catches_expired_deadline(valid_draft_md, sample_contract):
    # Change January 8, 2027 to past date October 15, 2024 without caveating
    corrupted_md = valid_draft_md.replace("January 8, 2027", "October 15, 2024")

    critic = AdversarialCritic()
    sources = [{"id": "src-1", "title": "CSMGP Guidelines"}]
    report = critic.audit_draft(
        draft_markdown=corrupted_md,
        contract=sample_contract,
        sources=sources,
        reference_date="2026-10-25",
    )

    assert report.passed is False
    assert len(report.expired_deadlines) >= 1
    assert "October 15, 2024" in report.expired_deadlines[0]["date"]


def test_evidence_repair_engine_cleans_prohibited_claims(sample_contract):
    engine = EvidenceRepairEngine()
    flawed_md = (
        "# Grant Report\n"
        "The project relies on Measure M sales tax funding and requires a mandatory 1:1 match [1].\n"
        "- [1] Official Sonoma Guidelines\n"
    )
    sources = [{"id": "1", "clean_text": "Ag + Open Space operates under Measure F."}]

    res = engine.repair_deliverable(
        project_id="prj-test",
        report_markdown=flawed_md,
        sources=sources,
        contract=sample_contract,
    )

    assert res.repaired is True
    assert "Measure M" not in res.repaired_markdown
    assert "Measure F" in res.repaired_markdown
    assert "mandatory 1:1" not in res.repaired_markdown
