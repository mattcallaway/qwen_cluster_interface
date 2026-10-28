"""Comprehensive adversarial test suite for strict_json_equal and strict JSON verification."""
import json
import pytest
from vps_agent.independent_verifier import strict, strict_json_equal, evaluate_observations

def test_nominal_exact_matches():
    assert strict_json_equal(
        {"valid": True, "words": 4, "length": 21, "preview": "hello worl"},
        {"valid": True, "words": 4, "length": 21, "preview": "hello worl"}
    ) is True
    assert strict_json_equal([1, 2, 3, "test", True, None], [1, 2, 3, "test", True, None]) is True
    assert strict_json_equal({}, {}) is True
    assert strict_json_equal([], []) is True

def test_boolean_integer_segregation():
    # False != 0
    assert strict_json_equal(False, 0) is False
    assert strict_json_equal(0, False) is False
    # True != 1
    assert strict_json_equal(True, 1) is False
    assert strict_json_equal(1, True) is False
    # In dicts
    assert strict_json_equal({"valid": 1}, {"valid": True}) is False
    assert strict_json_equal({"valid": 0}, {"valid": False}) is False
    # In lists
    assert strict_json_equal([False], [0]) is False
    assert strict_json_equal([True], [1]) is False

def test_integer_float_segregation():
    assert strict_json_equal(4, 4.0) is False
    assert strict_json_equal(4.0, 4) is False
    assert strict_json_equal(0, 0.0) is False
    assert strict_json_equal({"length": 21.0}, {"length": 21}) is False
    assert strict_json_equal([1.0, 2.0], [1, 2]) is False

def test_dict_key_strictness():
    # Extra key
    assert strict_json_equal({"a": 1, "b": 2, "extra": 3}, {"a": 1, "b": 2}) is False
    # Missing key
    assert strict_json_equal({"a": 1}, {"a": 1, "b": 2}) is False
    # Null vs missing
    assert strict_json_equal({"a": None}, {}) is False
    assert strict_json_equal({}, {"a": None}) is False

def test_array_ordering():
    assert strict_json_equal([1, 2, 3], [1, 3, 2]) is False
    assert strict_json_equal(["a", "b"], ["b", "a"]) is False
    assert strict_json_equal([{"id": 1}, {"id": 2}], [{"id": 2}, {"id": 1}]) is False

def test_collection_mismatches():
    assert strict_json_equal({}, []) is False
    assert strict_json_equal([], {}) is False
    assert strict_json_equal({"a": []}, {"a": {}}) is False

def test_unicode_semantics():
    # Exact Unicode codepoint identity
    assert strict_json_equal("qwen \u2728 2026", "qwen \u2728 2026") is True
    # Different emoji codepoint
    assert strict_json_equal("qwen \u2728 2026", "qwen \u2729 2026") is False
    # Accented characters
    assert strict_json_equal("café", "café") is True
    assert strict_json_equal("café", "cafe") is False

def test_nesting_depth_bounds():
    # Build nested dict beyond 16 levels
    deep_dict = 1
    for _ in range(20):
        deep_dict = {"nested": deep_dict}
    
    assert strict_json_equal(deep_dict, deep_dict) is False

def test_strict_json_parser_rejects_malformed_inputs():
    # Duplicate keys
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        strict('{"a": 1, "a": 2}')
    
    # Nonfinite constants
    with pytest.raises(ValueError, match="Nonfinite JSON"):
        strict('{"val": NaN}')
    with pytest.raises(ValueError, match="Nonfinite JSON"):
        strict('{"val": Infinity}')
    with pytest.raises(ValueError, match="Nonfinite JSON"):
        strict('{"val": -Infinity}')
    
    # Trailing data
    with pytest.raises(json.JSONDecodeError):
        strict('{"a": 1} trailing_exploit')

def test_evaluate_observations_adversarial_matrix():
    cases = [
        {"id": "c1", "requirement_id": "r1", "input": {"x": 1}, "expected": {"status": "ok", "count": 5, "active": True}},
        {"id": "c2", "requirement_id": "r1", "input": {"x": 2}, "expected": [1, 2, 3]}
    ]

    # Mutant 1: Coerced types (count=5.0, active=1)
    obs_mutant1 = [
        {"case_id": "c1", "executed": True, "reason": "completed", "exit_code": 0,
         "cleanup_confirmed": True, "stdout": json.dumps({"status": "ok", "count": 5.0, "active": 1}),
         "unit": "qc-sandbox-0123456789abcdef0123456789abcdef.service"},
        {"case_id": "c2", "executed": True, "reason": "completed", "exit_code": 0,
         "cleanup_confirmed": True, "stdout": json.dumps([1, 2, 3]),
         "unit": "qc-sandbox-0123456789abcdef0123456789abcdef.service"}
    ]
    res1 = evaluate_observations(cases, obs_mutant1)
    assert res1[0]["passed"] is False  # Rejected!
    assert res1[1]["passed"] is True

    # Mutant 2: Array reordering in c2
    obs_mutant2 = [
        {"case_id": "c1", "executed": True, "reason": "completed", "exit_code": 0,
         "cleanup_confirmed": True, "stdout": json.dumps({"status": "ok", "count": 5, "active": True}),
         "unit": "qc-sandbox-0123456789abcdef0123456789abcdef.service"},
        {"case_id": "c2", "executed": True, "reason": "completed", "exit_code": 0,
         "cleanup_confirmed": True, "stdout": json.dumps([1, 3, 2]),
         "unit": "qc-sandbox-0123456789abcdef0123456789abcdef.service"}
    ]
    res2 = evaluate_observations(cases, obs_mutant2)
    assert res2[0]["passed"] is True
    assert res2[1]["passed"] is False  # Rejected!

    # Mutant 3: Extra key injected
    obs_mutant3 = [
        {"case_id": "c1", "executed": True, "reason": "completed", "exit_code": 0,
         "cleanup_confirmed": True, "stdout": json.dumps({"status": "ok", "count": 5, "active": True, "pwn": True}),
         "unit": "qc-sandbox-0123456789abcdef0123456789abcdef.service"},
        {"case_id": "c2", "executed": True, "reason": "completed", "exit_code": 0,
         "cleanup_confirmed": True, "stdout": json.dumps([1, 2, 3]),
         "unit": "qc-sandbox-0123456789abcdef0123456789abcdef.service"}
    ]
    res3 = evaluate_observations(cases, obs_mutant3)
    assert res3[0]["passed"] is False  # Rejected!
    assert res3[1]["passed"] is True
