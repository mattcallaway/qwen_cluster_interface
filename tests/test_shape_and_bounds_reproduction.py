"""Regression test reproducing:
1. JSON answer-shape bypass: scalar 4 vs {"value": 4} in qc.json_io_cases.v1
2. Declared float magnitude limit: strict_json_equal(1e100, 1e100)
3. Deep nesting in cases_from: depth 25 accepted despite depth limit 16
4. Expected answer magnitude limit in cases_from: 1e100 in expected answer
"""
import json
import pytest
from vps_agent.independent_verifier import cases_from, evaluate_observations, strict_json_equal

def test_reproduce_json_shape_bypass():
    # In qc.json_io_cases.v1, expected is scalar 4.
    # The candidate stdout is {"value": 4}.
    # This MUST fail in the broader JSON profile!
    cases = [{
        'id': 'case_scalar',
        'requirement_id': 'req1',
        'input': {'text': 'test'},
        'expected': 4,
        'profile': 'qc.json_io_cases.v1'
    }]
    observations = [{
        'case_id': 'case_scalar',
        'executed': True,
        'reason': 'completed',
        'exit_code': 0,
        'cleanup_confirmed': True,
        'stdout': '{"value": 4}',
        'unit': 'qc-sandbox-' + '0'*32 + '.service'
    }]
    results = evaluate_observations(cases, observations)
    # If the bypass exists, results[0]['passed'] will be True!
    assert not results[0]['passed'], "SHAPE BYPASS: {'value': 4} was accepted for expected scalar 4 in json_io profile!"

def test_reproduce_float_magnitude_limit():
    # Absolute numeric maximum is declared as 10^14.
    # 1e100 exceeds 10^14.
    # strict_json_equal must reject numbers > 10^14!
    result = strict_json_equal(1e100, 1e100)
    assert not result, "MAGNITUDE BYPASS: strict_json_equal accepted 1e100 which exceeds 10^14!"

def test_reproduce_deep_nesting_in_cases_from():
    # Construct input nested 25 levels deep
    nested = {"leaf": 1}
    for _ in range(25):
        nested = {"nest": nested}
    
    doc = {
        'schema': 'qc.independent_tests.v1',
        'requirement_ids': ['req1'],
        'files': [{
            'path': 'tests/cases.json',
            'content': json.dumps({
                'schema': 'qc.json_io_cases.v1',
                'cases': [
                    {'id': 'c1', 'requirement_id': 'req1', 'input': nested, 'expected': {'ok': True}},
                    {'id': 'c2', 'requirement_id': 'req1', 'input': {'a': 1}, 'expected': {'ok': True}}
                ]
            })
        }]
    }
    with pytest.raises(ValueError, match="Nesting depth"):
        cases_from(json.dumps(doc), ['req1'])

def test_reproduce_oversized_number_in_cases_from():
    doc = {
        'schema': 'qc.independent_tests.v1',
        'requirement_ids': ['req1'],
        'files': [{
            'path': 'tests/cases.json',
            'content': json.dumps({
                'schema': 'qc.json_io_cases.v1',
                'cases': [
                    {'id': 'c1', 'requirement_id': 'req1', 'input': {'val': 1}, 'expected': {'val': 1e100}},
                    {'id': 'c2', 'requirement_id': 'req1', 'input': {'val': 2}, 'expected': {'val': 2.0}}
                ]
            })
        }]
    }
    with pytest.raises(ValueError, match="magnitude"):
        cases_from(json.dumps(doc), ['req1'])


def test_integer_profile_requires_wrapped_value():
    # In qc.integer_cases.v1, expected is 5.
    case = {'id': 'c1', 'requirement_id': 'r1', 'input': {'a': 2, 'b': 3}, 'expected': 5, 'profile': 'qc.integer_cases.v1'}
    obs_wrapped = {'case_id': 'c1', 'executed': True, 'reason': 'completed', 'exit_code': 0, 'cleanup_confirmed': True, 'stdout': '{"value": 5}', 'unit': 'qc-sandbox-' + '0'*32 + '.service'}
    obs_scalar = {'case_id': 'c1', 'executed': True, 'reason': 'completed', 'exit_code': 0, 'cleanup_confirmed': True, 'stdout': '5', 'unit': 'qc-sandbox-' + '0'*32 + '.service'}
    obs_extra_key = {'case_id': 'c1', 'executed': True, 'reason': 'completed', 'exit_code': 0, 'cleanup_confirmed': True, 'stdout': '{"value": 5, "other": 1}', 'unit': 'qc-sandbox-' + '0'*32 + '.service'}
    
    assert evaluate_observations([case], [obs_wrapped])[0]['passed'] is True
    # Scalar 5 must FAIL in integer profile because contract requires {"value": int}
    assert evaluate_observations([case], [obs_scalar])[0]['passed'] is False
    assert evaluate_observations([case], [obs_extra_key])[0]['passed'] is False


def test_json_io_profile_strictly_separates_scalar_and_object():
    # Test A: expected is scalar 4
    case_scalar = {'id': 'c1', 'requirement_id': 'r1', 'input': {'x': 1}, 'expected': 4, 'profile': 'qc.json_io_cases.v1'}
    obs_exact_scalar = {'case_id': 'c1', 'executed': True, 'reason': 'completed', 'exit_code': 0, 'cleanup_confirmed': True, 'stdout': '4', 'unit': 'qc-sandbox-' + '0'*32 + '.service'}
    obs_wrapped = {'case_id': 'c1', 'executed': True, 'reason': 'completed', 'exit_code': 0, 'cleanup_confirmed': True, 'stdout': '{"value": 4}', 'unit': 'qc-sandbox-' + '0'*32 + '.service'}
    obs_float = {'case_id': 'c1', 'executed': True, 'reason': 'completed', 'exit_code': 0, 'cleanup_confirmed': True, 'stdout': '4.0', 'unit': 'qc-sandbox-' + '0'*32 + '.service'}

    assert evaluate_observations([case_scalar], [obs_exact_scalar])[0]['passed'] is True
    assert evaluate_observations([case_scalar], [obs_wrapped])[0]['passed'] is False
    assert evaluate_observations([case_scalar], [obs_float])[0]['passed'] is False

    # Test B: expected is object {"value": 4}
    case_obj = {'id': 'c2', 'requirement_id': 'r1', 'input': {'x': 1}, 'expected': {'value': 4}, 'profile': 'qc.json_io_cases.v1'}
    obs_obj_exact = {'case_id': 'c2', 'executed': True, 'reason': 'completed', 'exit_code': 0, 'cleanup_confirmed': True, 'stdout': '{"value": 4}', 'unit': 'qc-sandbox-' + '0'*32 + '.service'}
    obs_obj_unwrapped = {'case_id': 'c2', 'executed': True, 'reason': 'completed', 'exit_code': 0, 'cleanup_confirmed': True, 'stdout': '4', 'unit': 'qc-sandbox-' + '0'*32 + '.service'}
    obs_obj_float = {'case_id': 'c2', 'executed': True, 'reason': 'completed', 'exit_code': 0, 'cleanup_confirmed': True, 'stdout': '{"value": 4.0}', 'unit': 'qc-sandbox-' + '0'*32 + '.service'}

    assert evaluate_observations([case_obj], [obs_obj_exact])[0]['passed'] is True
    assert evaluate_observations([case_obj], [obs_obj_unwrapped])[0]['passed'] is False
    assert evaluate_observations([case_obj], [obs_obj_float])[0]['passed'] is False

