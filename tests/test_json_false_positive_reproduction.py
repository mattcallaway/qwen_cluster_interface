"""Regression test reproducing the JSON false positive in independent_verifier.

Counterexample:
Expected:
{"valid": True, "words": 4, "length": 21, "preview": "hello worl"}

Actual:
{"valid": 1, "words": 4.0, "length": 21.0, "preview": "hello worl"}

Under Python's loose equality:
- 1 == True is True
- 4.0 == 4 is True
- 21.0 == 21 is True
Outer dict type check succeeds, causing the verifier to return passed: True instead of False!
"""
import json
import pytest
from vps_agent.independent_verifier import evaluate_observations

def test_reproduce_nested_type_coercion_false_positive():
    cases = [{
        'id': 'case_nominal',
        'requirement_id': 'text_analyzer',
        'input': {'text': 'hello world from qwen'},
        'expected': {'valid': True, 'words': 4, 'length': 21, 'preview': 'hello worl'}
    }]

    # Hostile / mutated observation with coerced types: 1 for True, 4.0 for 4, 21.0 for 21
    counterexample_stdout = json.dumps({
        'valid': 1,
        'words': 4.0,
        'length': 21.0,
        'preview': 'hello worl'
    })

    observations = [{
        'case_id': 'case_nominal',
        'executed': True,
        'reason': 'completed',
        'exit_code': 0,
        'cleanup_confirmed': True,
        'stdout': counterexample_stdout,
        'unit': 'qc-sandbox-0123456789abcdef0123456789abcdef.service'
    }]

    results = evaluate_observations(cases, observations)
    assert len(results) == 1
    # STRICT CONTRACT: 1 is not True, 4.0 is not 4, 21.0 is not 21.
    # The test MUST fail (passed: False).
    # On unfixed code, this assert will fail because results[0]['passed'] is incorrectly True!
    assert results[0]['passed'] is False, (
        f"FALSE POSITIVE DETECTED! Verifier accepted coerced types: "
        f"actual={counterexample_stdout} vs expected={cases[0]['expected']}"
    )
