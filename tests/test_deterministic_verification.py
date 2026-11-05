"""Unit tests for Deterministic Verification & Arithmetic Engine (Phase C/D Milestone 1)."""
from datetime import date
import pytest

from app.services.orchestration.verification import (
    DeterministicVerificationEngine,
    SafeMathEvaluator,
    safe_eval_math,
)


def test_safe_math_evaluator_basic():
    assert safe_eval_math("1 + 2 * 3") == 7.0
    assert safe_eval_math("(100 - 25) / 5") == 15.0
    assert safe_eval_math("$500,000 * 0.50") == 250000.0


def test_safe_math_evaluator_with_variables():
    vars = {"grant_req": 60000.0, "match_rate": 0.50}
    assert safe_eval_math("grant_req * match_rate", vars) == 30000.0
    assert safe_eval_math("grant_req + (grant_req * match_rate)", vars) == 90000.0


def test_safe_math_evaluator_security_blocks_code():
    with pytest.raises(Exception):
        safe_eval_math("__import__('os').system('dir')")


def test_verify_grant_match_csmgp_exact():
    engine = DeterministicVerificationEngine()
    # Sonoma CSMGP: $60k requested, 50c match per dollar -> $30k match
    res = engine.verify_grant_match(
        grant_requested=60000.0,
        match_rate=0.50,
        declared_match=30000.0,
    )
    assert res["passed"] is True
    assert res["expected_match"] == 30000.0
    assert res["total_matching_project_cost"] == 90000.0
    assert res["match_pct_of_total"] == 33.33


def test_verify_grant_match_catches_hallucinated_1_to_1():
    engine = DeterministicVerificationEngine()
    # Model hallucinated 1:1 match ($60k match on $60k request)
    res = engine.verify_grant_match(
        grant_requested=60000.0,
        match_rate=0.50,
        declared_match=60000.0,
    )
    assert res["passed"] is False
    assert res["discrepancy"] == 30000.0


def test_verify_cost_cap():
    engine = DeterministicVerificationEngine()
    # 10% admin cap on $500,000 grant
    res_pass = engine.verify_cost_cap(base_amount=500000.0, cost_amount=45000.0, cap_pct=10.0)
    assert res_pass["passed"] is True

    res_fail = engine.verify_cost_cap(base_amount=500000.0, cost_amount=65000.0, cap_pct=10.0)
    assert res_fail["passed"] is False
    assert res_fail["max_allowable"] == 50000.0


def test_compute_multi_grant_allocation():
    engine = DeterministicVerificationEngine()
    line_items = [
        {"category": "design", "cost": 180000.0, "eligible_grant_a": True, "eligible_grant_b": True},
        {"category": "permitting", "cost": 65000.0, "eligible_grant_a": False, "eligible_grant_b": False},
        {"category": "planting", "cost": 520000.0, "eligible_grant_a": True, "eligible_grant_b": True},
        {"category": "earthwork", "cost": 465000.0, "eligible_grant_a": True, "eligible_grant_b": True},
        {"category": "admin", "cost": 220000.0, "eligible_grant_a": True, "eligible_grant_b": True},
    ]
    grants = [
        {"id": "grant_a", "name": "CSMGP", "max_award": 500000.0, "match_rate": 0.50, "max_admin_pct": 10.0},
        {"id": "grant_b", "name": "WCB", "max_award": 750000.0, "match_rate": 0.20, "max_design_pct": 15.0},
    ]

    res = engine.compute_multi_grant_allocation(
        total_project_cost=1450000.0,
        line_items=line_items,
        grants=grants,
        local_cash=200000.0,
        local_inkind=50000.0,
    )

    assert res["total_grant_funding"] == 1250000.0  # $500k + $750k
    assert res["total_match_required"] == 400000.0  # ($500k * 0.50 = 250k) + ($750k * 0.20 = 150k)
    assert res["total_local_available"] == 250000.0
    assert res["match_deficit"] == 150000.0  # 400k req - 250k avail


def test_verify_deadline():
    engine = DeterministicVerificationEngine()
    ref_date = date(2026, 10, 25)

    # Future deadline
    res_future = engine.verify_deadline("January 8, 2027", reference_date=ref_date)
    assert res_future["passed"] is True
    assert res_future["days_remaining"] == 75

    # Expired deadline
    res_expired = engine.verify_deadline("October 15, 2024", reference_date=ref_date)
    assert res_expired["passed"] is False
    assert res_expired["error"] == "EXPIRED_DEADLINE"


def test_verify_negative_constraints():
    engine = DeterministicVerificationEngine()
    sample_text = (
        "The project is funded by Measure F. However, some staff mentioned Measure M (parks tax) "
        "and stated that a mandatory 1:1 match is required."
    )
    disallowed = [
        {"pattern": r"\bMeasure\s+M\b", "description": "Conflation with Measure M parks sales tax"},
        {"pattern": r"mandatory\s+1[:\-]1\s+match", "description": "Erroneous mandatory 1:1 match claim"},
    ]
    violations = engine.verify_negative_constraints(sample_text, disallowed)
    assert len(violations) == 2
    assert "Measure M" in violations[0]["description"]
    assert "mandatory 1:1" in violations[1]["description"]


def test_verify_required_numerical_assertions():
    engine = DeterministicVerificationEngine()
    doc_text = "The recommended match is 50-cents ($0.50) per grant dollar, or 33.33% of total project cost."
    assertions = [
        {"name": "match_cents", "expected_value": 0.50, "regex_pattern": r"\$(\d+\.\d+)\)?\s+per\s+grant\s+dollar"},
        {"name": "match_percentage", "expected_value": 33.33, "regex_pattern": r"(\d+\.\d+)%\s+of\s+total"},
    ]
    failures = engine.verify_required_numerical_assertions(doc_text, assertions)
    assert len(failures) == 0
