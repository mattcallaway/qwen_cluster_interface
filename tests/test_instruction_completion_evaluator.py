"""
Test suite for multi-instruction objective fulfillment and truncation detection.
Validates the 6 required evaluation test cases for factual inference tasks.
"""
import copy
import json
import pytest
from app.services.orchestration.evaluator import evaluate_inference

BASE_ACCEPTANCE = {
    "require_factual_consistency": True,
    "check_truncation": True,
    "max_tokens": 150,
    "verified_facts": {
        "server_alpha_ram": {
            "required_patterns": ["3,802 MB"],
            "prohibited_patterns": ["32 GB", "16 GB"]
        },
        "server_alpha_swap": {
            "required_patterns": ["36"],
            "prohibited_patterns": []
        },
        "server_beta_ram": {
            "required_patterns": ["1,844 MB"],
            "prohibited_patterns": []
        },
        "server_beta_swap": {
            "required_patterns": ["1.0 GB"],
            "prohibited_patterns": []
        }
    },
    "required_instructions": [
        {
            "id": "compare_alpha_beta",
            "description": "Compare Server Alpha and Server Beta physical RAM and swap",
            "semantic_patterns": [
                "(?i)alpha.*?(?:ram|memory).*?beta|beta.*?(?:ram|memory).*?alpha",
                "(?i)alpha.*?(?:swap).*?beta|beta.*?(?:swap).*?alpha"
            ],
            "pattern_mode": "all"
        },
        {
            "id": "conclude_largest_swap",
            "description": "Conclude clearly which server has largest swap capacity among all three servers",
            "semantic_patterns": [
                "(?i)(?:largest|greatest|most|highest)\\s+(?:swap|capacity).*?Server\\s+Alpha|Server\\s+Alpha.*?(?:largest|greatest|highest|most)\\s+(?:swap|capacity)"
            ],
            "pattern_mode": "any"
        },
        {
            "id": "state_alpha_ram",
            "description": "State exact RAM amount of Server Alpha",
            "semantic_patterns": [
                "(?i)(?:Server\\s+Alpha.*?3,?802\\s*mb|3,?802\\s*mb.*?Server\\s+Alpha)"
            ],
            "pattern_mode": "any"
        }
    ]
}

@pytest.fixture
def c1_eval_fixture():
    orch = {
        "dispatched_node": "qwen-laptop",
        "acceptance_json": json.dumps(BASE_ACCEPTANCE),
        "action_json": json.dumps({"instruction": "Compare Server Alpha and Server Beta RAM and swap. Conclude largest swap. State Alpha exact RAM."})
    }
    req = {
        "status": "completed",
        "node_id": "qwen-laptop",
        "model": "qwen2.5:1.5b",
        "num_predict": 150,
        "gen_tokens": 120,
        "prompt_tokens": 191,
        "latency_s": 11.5,
    }
    return copy.deepcopy(orch), copy.deepcopy(req)

def test_case_1_correct_facts_missing_one_required_answer(c1_eval_fixture):
    """Case 1: Facts are correct, but missing exact RAM statement for Server Alpha."""
    orch, req = c1_eval_fixture
    req["response"] = (
        "### Hardware Comparison\n"
        "1. Comparing Server Alpha and Server Beta: Server Alpha has 3,802 MB RAM and 36 GB swap, while Server Beta has 1,844 MB RAM and 1.0 GB swap.\n"
        "2. Conclusion: Server Alpha has the largest swap capacity of all three servers with 36 GB.\n"
        # Omitted instruction 3 (state Alpha exact RAM as an explicit conclusion)
    )
    # Customize instructions to require explicit conclusion for instruction 3
    acc = json.loads(orch["acceptance_json"])
    acc["required_instructions"][2]["semantic_patterns"] = [
        "(?i)Conclusion\\s*#?3:?\\s*Server\\s+Alpha\\s+has\\s+3,?802\\s*MB"
    ]
    orch["acceptance_json"] = json.dumps(acc)

    res = evaluate_inference(orch, req, "test prompt")
    
    assert all(c["passed"] for c in res["execution_checks"]) is True
    assert all(c["passed"] for c in res["schema_checks"]) is True
    
    instr_check = next(c for c in res["goal_checks"] if c["name"] == "instruction_completion")
    assert instr_check["passed"] is False
    assert "state_alpha_ram" in instr_check["detail"]
    assert res["accepted"] is False

def test_case_2_correct_facts_missing_final_conclusion(c1_eval_fixture):
    """Case 2: Facts are correct, but missing final conclusion on largest swap capacity."""
    orch, req = c1_eval_fixture
    req["response"] = (
        "### Hardware Comparison\n"
        "1. Comparing Server Alpha and Server Beta: Server Alpha has 3,802 MB RAM and 36 GB swap, while Server Beta has 1,844 MB RAM and 1.0 GB swap.\n"
        "3. Server Alpha has exact physical RAM of 3,802 MB.\n"
        # Missing Instruction 2: conclusion on which server has largest swap
    )
    res = evaluate_inference(orch, req, "test prompt")
    
    assert all(c["passed"] for c in res["execution_checks"]) is True
    instr_check = next(c for c in res["goal_checks"] if c["name"] == "instruction_completion")
    assert instr_check["passed"] is False
    assert "conclude_largest_swap" in instr_check["detail"]
    assert res["accepted"] is False

def test_case_3_all_requested_answers_present_and_correct(c1_eval_fixture):
    """Case 3: All requested answers present, factually accurate, non-truncated."""
    orch, req = c1_eval_fixture
    req["response"] = (
        "### Memory Architecture Analysis\n\n"
        "1. **RAM and Swap Comparison (Alpha vs. Beta):**\n"
        "Server Alpha has 3,802 MB of RAM and 36.0 GB of swap file capacity, whereas Server Beta has 1,844 MB of RAM and 1.0 GB of swap.\n\n"
        "2. **Largest Swap Capacity Conclusion:**\n"
        "Among all three servers, Server Alpha has the largest swap capacity with 36.0 GB.\n\n"
        "3. **Server Alpha Exact RAM:**\n"
        "The exact RAM of Server Alpha is 3,802 MB."
    )
    res = evaluate_inference(orch, req, "test prompt")
    
    assert res["accepted"] is True
    assert res["decision"] == "ACCEPTED"
    assert all(c["passed"] for c in res["schema_checks"]) is True
    assert all(c["passed"] for c in res["execution_checks"]) is True
    assert all(c["passed"] for c in res["goal_checks"]) is True

def test_case_4_incorrect_numerical_comparison(c1_eval_fixture):
    """Case 4: Model produces false numerical comparison (e.g. Beta has more RAM than Alpha)."""
    orch, req = c1_eval_fixture
    # Add prohibited pattern in instruction or facts
    req["response"] = (
        "1. Comparing Server Alpha and Server Beta: Server Beta has more RAM than Server Alpha.\n"
        "2. Server Beta has the largest swap capacity.\n"
        "3. Server Alpha has 3,802 MB RAM."
    )
    acc = json.loads(orch["acceptance_json"])
    acc["required_instructions"][0]["prohibited_patterns"] = [
        "(?i)Server\\s+Beta\\s+has\\s+more\\s+RAM"
    ]
    orch["acceptance_json"] = json.dumps(acc)

    res = evaluate_inference(orch, req, "test prompt")
    
    assert all(c["passed"] for c in res["execution_checks"]) is True
    instr_check = next(c for c in res["goal_checks"] if c["name"] == "instruction_completion")
    assert instr_check["passed"] is False
    assert res["accepted"] is False

def test_case_5_response_truncated_by_token_limit(c1_eval_fixture):
    """Case 5: Exact reproduction of C1 failure: output cuts off mid-sentence at max_tokens."""
    orch, req = c1_eval_fixture
    req["gen_tokens"] = 150 # Reached max_tokens
    req["num_predict"] = 150
    req["response"] = (
        "### Analysis of Server Memory Configurations\n\n"
        "#### Server Alpha:\n"
        "- **Physical RAM**: 3,802 MB\n"
        "- **Swap File**: 36.0 GB\n\n"
        "#### Server Beta:\n"
        "- **Physical RAM**: 1,844 MB\n"
        "- **Swap Partition**: 1.0 GB\n\n"
        "#### Server Gamma:\n"
        "- **Physical RAM**: 24,573 MB\n"
        "- **Pagefile**: 4.0 GB\n\n"
        "### Comparison of Physical RAM and Swap Capacity\n\n"
        "1. **Server Alpha vs. Server Beta:**\n"
        "   - **Physical RAM**: Server Alpha has 3,802 MB, while Server Beta has 1,844 MB.\n"
        "   - **Swap Capacity**: Server"
    )
    res = evaluate_inference(orch, req, "test prompt")
    
    # 1. Technical execution: PASS (completed without crash)
    assert all(c["passed"] for c in res["execution_checks"]) is True
    # 2. Schema: PASS (non-empty)
    assert all(c["passed"] for c in res["schema_checks"]) is True
    # 3. Truncation check: FAIL
    trunc_check = next(c for c in res["goal_checks"] if c["name"] == "output_not_truncated")
    assert trunc_check["passed"] is False
    assert "token cap" in trunc_check["detail"]
    # 4. Instruction completion: FAIL (missing largest swap conclusion)
    instr_check = next(c for c in res["goal_checks"] if c["name"] == "instruction_completion")
    assert instr_check["passed"] is False
    assert res["accepted"] is False
    assert res["decision"] == "REJECTED"

def test_case_6_model_response_containing_unsupported_hardware_claims(c1_eval_fixture):
    """Case 6: Model hallucinates unsupported hardware claim (e.g. 32 GB RAM on desktop)."""
    orch, req = c1_eval_fixture
    req["response"] = (
        "1. Server Alpha has 32 GB RAM and 36 GB swap, while Server Beta has 1,844 MB RAM.\n"
        "2. Server Alpha has the largest swap capacity among all three servers.\n"
        "3. Server Alpha has 3,802 MB RAM."
    )
    res = evaluate_inference(orch, req, "test prompt")
    
    fact_check = next(c for c in res["goal_checks"] if c["name"] == "factual_consistency")
    assert fact_check["passed"] is False
    assert "32 GB" in fact_check["detail"]
    assert res["accepted"] is False
