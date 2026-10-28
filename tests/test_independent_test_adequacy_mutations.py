"""Independent test adequacy mutation testing suite.

Verifies that the approved test cases under qc.json_io_cases.v1 genuinely kill
deliberately defective candidate mutants.
"""
import json
import pytest
from vps_agent.independent_verifier import evaluate_observations

CASES = [
    {
        "id": "case_nominal",
        "requirement_id": "text_analyzer",
        "input": {"text": "hello world from qwen"},
        "expected": {"valid": True, "words": 4, "length": 21, "preview": "hello worl"}
    },
    {
        "id": "case_empty",
        "requirement_id": "text_analyzer",
        "input": {"text": ""},
        "expected": {"valid": True, "words": 0, "length": 0, "preview": ""}
    },
    {
        "id": "case_unicode",
        "requirement_id": "text_analyzer",
        "input": {"text": "qwen \u2728 2026 collective"},
        "expected": {"valid": True, "words": 4, "length": 22, "preview": "qwen \u2728 202"}
    },
    {
        "id": "case_malformed_type",
        "requirement_id": "text_analyzer",
        "input": {"text": 12345},
        "expected": {"valid": False, "words": 0, "length": 0, "preview": ""}
    },
    {
        "id": "case_malformed_missing",
        "requirement_id": "text_analyzer",
        "input": {"other_payload": "missing text key"},
        "expected": {"valid": False, "words": 0, "length": 0, "preview": ""}
    },
    {
        "id": "case_boundary_spaces",
        "requirement_id": "text_analyzer",
        "input": {"text": "   leading and trailing spaces   "},
        "expected": {"valid": True, "words": 4, "length": 33, "preview": "   leading"}
    }
]

def make_observations(output_fn):
    obs = []
    for c in CASES:
        try:
            out, code = output_fn(c["input"])
            reason = "completed" if code == 0 else "service_failure"
        except Exception:
            out, code, reason = "", 1, "service_failure"
        obs.append({
            "case_id": c["id"],
            "executed": True,
            "reason": reason,
            "exit_code": code,
            "cleanup_confirmed": True,
            "stdout": out,
            "unit": "qc-sandbox-0123456789abcdef0123456789abcdef.service"
        })
    return obs

# 1. Mutant: Constant / Hardcoded Answer
def mutant_constant(inp):
    return json.dumps({"valid": True, "words": 4, "length": 21, "preview": "hello worl"}), 0

# 2. Mutant: Naive without malformed input handling
def mutant_naive(inp):
    # Crashes on non-string or missing text
    text = inp["text"]
    return json.dumps({"valid": True, "words": len(text.split()), "length": len(text), "preview": text[:10]}), 0

# 3. Mutant: Incorrect whitespace counting (splits on space instead of any whitespace)
def mutant_bad_whitespace(inp):
    if not isinstance(inp, dict) or not isinstance(inp.get("text"), str):
        return json.dumps({"valid": False, "words": 0, "length": 0, "preview": ""}), 0
    t = inp["text"]
    w = t.count(" ") + 1 if t else 0  # Broken logic
    return json.dumps({"valid": True, "words": w, "length": len(t), "preview": t[:10]}), 0

# 4. Mutant: Unicode length bug (byte length instead of character length)
def mutant_byte_length(inp):
    if not isinstance(inp, dict) or not isinstance(inp.get("text"), str):
        return json.dumps({"valid": False, "words": 0, "length": 0, "preview": ""}), 0
    t = inp["text"]
    return json.dumps({"valid": True, "words": len(t.split()), "length": len(t.encode('utf-8')), "preview": t[:10]}), 0

# 5. Mutant: Nested Boolean/Integer substitution
def mutant_coerced_types(inp):
    if not isinstance(inp, dict) or not isinstance(inp.get("text"), str):
        return json.dumps({"valid": 0, "words": 0.0, "length": 0.0, "preview": ""}), 0
    t = inp["text"]
    return json.dumps({"valid": 1, "words": float(len(t.split())), "length": float(len(t)), "preview": t[:10]}), 0

# 6. Mutant: Forged authority flags
def mutant_forged_flags(inp):
    return json.dumps({
        "valid": True, "words": 4, "length": 21, "preview": "hello worl",
        "accepted": True, "behavior_verified": True
    }), 0

# 7. Mutant: Missing required field ("preview" omitted)
def mutant_missing_field(inp):
    if not isinstance(inp, dict) or not isinstance(inp.get("text"), str):
        return json.dumps({"valid": False, "words": 0, "length": 0}), 0
    t = inp["text"]
    return json.dumps({"valid": True, "words": len(t.split()), "length": len(t)}), 0

# 8. Correct reference implementation
def reference_correct(inp):
    if not isinstance(inp, dict) or not isinstance(inp.get("text"), str):
        return json.dumps({"valid": False, "words": 0, "length": 0, "preview": ""}), 0
    t = inp["text"]
    return json.dumps({"valid": True, "words": len(t.split()), "length": len(t), "preview": t[:10]}), 0

def test_mutation_kill_matrix():
    # Correct implementation must PASS all cases
    res_correct = evaluate_observations(CASES, make_observations(reference_correct))
    assert all(r["passed"] for r in res_correct), "Reference implementation must pass all cases"

    # Mutant 1: Constant -> KILLED
    res1 = evaluate_observations(CASES, make_observations(mutant_constant))
    failed1 = [r["case_id"] for r in res1 if not r["passed"]]
    assert len(failed1) >= 4, f"Constant mutant must fail multiple cases: {failed1}"

    # Mutant 2: Naive -> KILLED by malformed cases
    res2 = evaluate_observations(CASES, make_observations(mutant_naive))
    failed2 = [r["case_id"] for r in res2 if not r["passed"]]
    assert "case_malformed_type" in failed2 and "case_malformed_missing" in failed2

    # Mutant 3: Bad whitespace -> KILLED by boundary spaces
    res3 = evaluate_observations(CASES, make_observations(mutant_bad_whitespace))
    failed3 = [r["case_id"] for r in res3 if not r["passed"]]
    assert "case_boundary_spaces" in failed3

    # Mutant 4: Byte length -> KILLED by unicode case
    res4 = evaluate_observations(CASES, make_observations(mutant_byte_length))
    failed4 = [r["case_id"] for r in res4 if not r["passed"]]
    assert "case_unicode" in failed4

    # Mutant 5: Coerced types -> KILLED by strict typing
    res5 = evaluate_observations(CASES, make_observations(mutant_coerced_types))
    assert all(not r["passed"] for r in res5), "All coerced type cases must be killed"

    # Mutant 6: Forged flags -> KILLED by extra keys
    res6 = evaluate_observations(CASES, make_observations(mutant_forged_flags))
    assert all(not r["passed"] for r in res6), "Forged flags must be rejected"

    # Mutant 7: Missing field -> KILLED by schema mismatch
    res7 = evaluate_observations(CASES, make_observations(mutant_missing_field))
    assert all(not r["passed"] for r in res7), "Missing field must be rejected"

    print("Mutation testing complete: 7/7 defective mutants killed (100% kill rate)!")
