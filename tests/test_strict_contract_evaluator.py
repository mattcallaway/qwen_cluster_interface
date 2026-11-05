"""Unit tests for Strict Acceptance Contract Engine (Phase C/D Milestone 2)."""
from datetime import date
import pytest

from app.services.orchestration.contract import (
    DeliverableRequirementContract,
    decompose_deliverable_requirements,
)
from app.services.orchestration.evaluator import (
    evaluate_deliverable_acceptance,
    evaluate_deliverable_contract,
    evaluate_report,
)


@pytest.fixture
def sample_valid_inventory_md() -> str:
    return """# Verified Funding Opportunities Inventory
## Executive Summary
This document provides an inventory of verified environmental grants for Sonoma County.

## Authoritative Opportunity Matrix
| Program | Funder | Statutory Basis | Match Ratio | Deadlines |
|---|---|---|---|---|
| Community Spaces Matching Grant | Sonoma County Ag + Open Space | Measure F (2006) | $0.50 per dollar (1:2 ratio) | Rolling Pre-App |

## Program Eligibility & Application Profiles
* Statutory Authority: Measure F (2006 Open Space sales tax).
* Award Amounts: No statutory minimum or maximum grant amounts.
* Matching Requirement: Recommended match of 50-cents ($0.50) per grant dollar, or 33.33% of total project cost. Discretionary variance available for disadvantaged communities.
* Application Process: Pre-application submitted via email [src-1].

## Information Gaps & Unverified Programs
* CFSC Resilient Organizations grant round is unannounced for 2027.

## Authoritative References & Provenance
- [src-1] Sonoma County Ag + Open Space Guidelines (Adopted August 19, 2025).
"""


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
        required_output_structure=[
            "Executive Summary",
            "Authoritative Opportunity Matrix",
            "Program Eligibility & Application Profiles",
            "Information Gaps & Unverified Programs",
            "Authoritative References & Provenance",
        ],
        necessary_evidence_types=["primary_official"],
        required_execution_capabilities=["research"],
        measurable_acceptance_criteria={"require_citations": True},
        disallowed_claims=[
            {"pattern": r"\bMeasure\s+M\b", "description": "Measure M conflation"},
            {"pattern": r"mandatory\s+(?:1[:\-]1|100%)\s+match", "description": "Mandatory 1:1 match claim"},
        ],
        numerical_constraints=[
            {"name": "match_cents", "expected_value": 0.50, "regex_pattern": r"\$(\d+\.\d+)\)\s+per\s+grant\s+dollar"},
            {"name": "match_pct", "expected_value": 33.33, "regex_pattern": r"(\d+\.\d+)%\s+of\s+total"},
        ],
    )


def test_evaluate_contract_perfect_fulfillment(sample_valid_inventory_md, sample_contract):
    sources = [{"id": "src-1", "title": "Sonoma Ag + Open Space Guidelines"}]
    res = evaluate_deliverable_contract(
        md=sample_valid_inventory_md,
        contract=sample_contract,
        verified_sources=sources,
    )

    assert res["accepted"] is True
    assert res["decision"] == "ACCEPTED"
    assert res["fulfillment_status"] == "FULFILLED"
    assert res["fulfillment_score"] == 1.0
    assert len(res["negative_violations"]) == 0
    assert len(res["numerical_failures"]) == 0


def test_evaluate_contract_catches_negative_constraint_violation(sample_valid_inventory_md, sample_contract):
    # Inject historical Measure M / mandatory 1:1 match error into valid text
    corrupted_md = sample_valid_inventory_md.replace(
        "Measure F (2006)", "Measure M (2018 parks sales tax)"
    ).replace(
        "Recommended match of 50-cents", "Mandatory 1:1 match is required"
    )

    sources = [{"id": "src-1", "title": "Sonoma Ag + Open Space Guidelines"}]
    res = evaluate_deliverable_contract(
        md=corrupted_md,
        contract=sample_contract,
        verified_sources=sources,
    )

    assert res["accepted"] is False
    assert res["decision"] == "REJECTED"
    assert res["fulfillment_status"] == "PARTIAL"
    assert res["fulfillment_score"] == 0.50
    assert len(res["negative_violations"]) >= 1

    descriptions = [v["description"] for v in res["negative_violations"]]
    assert any("Measure M" in d for d in descriptions)


def test_evaluate_contract_catches_arithmetic_discrepancy(sample_valid_inventory_md, sample_contract):
    # Corrupt arithmetic percentage from 33.33% to 66.6%
    corrupted_md = sample_valid_inventory_md.replace("33.33%", "66.6%")

    sources = [{"id": "src-1", "title": "Sonoma Ag + Open Space Guidelines"}]
    res = evaluate_deliverable_contract(
        md=corrupted_md,
        contract=sample_contract,
        verified_sources=sources,
    )

    assert res["accepted"] is False
    assert res["decision"] == "REJECTED"
    assert res["fulfillment_status"] == "PARTIAL"
    assert len(res["numerical_failures"]) >= 1
    assert res["numerical_failures"][0]["name"] == "match_pct"


def test_decompose_deliverable_requirements_creates_quality_first_contract():
    intake = {
        "objective": "Research environmental grants in Sonoma County under Community Spaces program",
        "instructions": "Ensure strict verification against controlling legal texts",
    }
    contract = decompose_deliverable_requirements(
        intake=intake,
        deliverable_name="1. Verified Funding Opportunities Inventory",
    )

    assert contract.key == "verified_funding_opportu"
    assert len(contract.disallowed_claims) >= 2
    assert any("Measure M" in str(dc) for dc in contract.disallowed_claims)
    assert any("mandatory" in str(dc) for dc in contract.disallowed_claims)


def test_evaluate_deliverable_acceptance_integration(tmp_path, sample_valid_inventory_md, sample_contract):
    # Write sample markdown to a temp file simulating primary artifact
    art_file = tmp_path / "inventory.md"
    art_file.write_text(sample_valid_inventory_md, encoding="utf-8")

    deliverable = {
        "id": "deliv-01",
        "name": "Verified Funding Opportunities Inventory",
        "deliverable_type": "report",
        "contract": sample_contract,
    }
    artifacts = [
        {
            "id": "art-01",
            "filename": "inventory.md",
            "storage_path": str(art_file),
            "validation_state": "valid",
        }
    ]

    res = evaluate_deliverable_acceptance(
        deliverable=deliverable,
        artifacts=artifacts,
        acceptance_criteria=sample_contract.to_dict(),
    )

    assert "fulfillment_status" in res
    assert res["fulfillment_status"] in ("FULFILLED", "QUALIFIED_FULFILLMENT")
    assert len(res["negative_violations"]) == 0
