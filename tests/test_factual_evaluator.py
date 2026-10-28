import json
import pytest
from app.services.orchestration import evaluator

def test_evaluator_accepts_accurate_factual_inference():
    """Verify factual evaluator passes when model response matches verified facts."""
    orch_task = {
        "id": "orch-fact-01",
        "plan_key": "fact_check_pass",
        "dispatched_node": "desktop-worker-01",
        "dispatched_model": "qwen2.5:3b",
        "action_json": json.dumps({"instruction": "Summarize cluster node memory"}),
        "acceptance_json": json.dumps({
            "min_chars": 50,
            "require_factual_consistency": True,
            "verified_facts": {
                "desktop_ram": {
                    "required_patterns": ["24gb"],
                    "prohibited_patterns": ["32gb", "64gb"],
                },
                "cluster_posture": {
                    "required_patterns": ["inference"],
                }
            }
        }),
    }
    
    valid_response = (
        "Analysis of Cluster Hardware:\n"
        "desktop-worker-01 is operating with 24GB of verified RAM on Windows.\n"
        "The cluster posture is strictly restricted to inference tasks."
    )
    req = {
        "status": "completed",
        "response": valid_response,
        "node_id": "desktop-worker-01",
        "model": "qwen2.5:3b",
        "gen_tokens": 45,
        "prompt_tokens": 80,
        "latency_s": 1.2,
    }
    
    res = evaluator.evaluate_inference(orch_task, req, "Summarize cluster node memory")
    assert res["execution_checks"][0]["passed"] is True, "Technical execution must pass"
    assert res["schema_checks"][0]["passed"] is True, "Schema check must pass"
    assert res["accepted"] is True, "Accurate response must be accepted"
    assert res["decision"] == "ACCEPTED"


def test_evaluator_rejects_factual_contradiction_while_marking_execution_success():
    """Verify factual evaluator catches contradiction (e.g. 32GB instead of 24GB) and rejects task."""
    orch_task = {
        "id": "orch-fact-02",
        "plan_key": "fact_check_fail",
        "dispatched_node": "desktop-worker-01",
        "dispatched_model": "qwen2.5:3b",
        "action_json": json.dumps({"instruction": "Summarize cluster node memory"}),
        "acceptance_json": json.dumps({
            "min_chars": 50,
            "require_factual_consistency": True,
            "verified_facts": {
                "desktop_ram": {
                    "required_patterns": ["24gb"],
                    "prohibited_patterns": ["32gb", "64gb"],
                }
            }
        }),
    }
    
    # Model response contains inaccurate 32GB statement
    contradictory_response = (
        "Analysis of Cluster Hardware:\n"
        "desktop-worker-01 is equipped with 32GB of RAM on a Windows platform.\n"
        "The cluster posture is strictly restricted to inference tasks."
    )
    req = {
        "status": "completed",
        "response": contradictory_response,
        "node_id": "desktop-worker-01",
        "model": "qwen2.5:3b",
        "gen_tokens": 45,
        "prompt_tokens": 80,
        "latency_s": 1.2,
    }
    
    res = evaluator.evaluate_inference(orch_task, req, "Summarize cluster node memory")
    # Technical execution is SUCCESSFUL
    assert res["execution_checks"][0]["passed"] is True, "Technical execution completed"
    assert res["schema_checks"][0]["passed"] is True, "Schema is valid"
    # But Factual Consistency is REJECTED
    assert res["accepted"] is False, "Factually inaccurate response must not be accepted"
    assert res["decision"] == "REJECTED"
    
    fact_check = next((c for c in res["goal_checks"] if c["name"] == "factual_consistency"), None)
    assert fact_check is not None
    assert fact_check["passed"] is False
    assert "32gb" in fact_check["detail"]


def test_evaluator_preserves_creative_subjective_tasks_without_factual_checks():
    """Creative or subjective tasks without factual constraints pass without factual checking."""
    orch_task = {
        "id": "orch-creative-01",
        "plan_key": "creative_step",
        "dispatched_node": "desktop-worker-01",
        "dispatched_model": "qwen2.5:3b",
        "action_json": json.dumps({"instruction": "Write a welcoming introduction"}),
        "acceptance_json": json.dumps({"min_chars": 20}),
    }
    
    creative_response = "Welcome to the collective! We collaborate to build intelligent distributed workflows."
    req = {
        "status": "completed",
        "response": creative_response,
        "node_id": "desktop-worker-01",
        "model": "qwen2.5:3b",
        "gen_tokens": 20,
        "prompt_tokens": 30,
        "latency_s": 0.8,
    }
    
    res = evaluator.evaluate_inference(orch_task, req, "Write a welcoming introduction")
    assert res["accepted"] is True
    assert res["decision"] == "ACCEPTED"
    assert not any(c["name"] == "factual_consistency" for c in res["goal_checks"])
