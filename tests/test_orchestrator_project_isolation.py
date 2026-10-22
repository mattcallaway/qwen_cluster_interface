"""Regression tests for Project Orchestrator context isolation, capability restriction,
self-dependency detection, and non-telemetry project execution.
"""
from __future__ import annotations

import copy
import json
import pytest

from app.database import get_db_connection, db_transaction
from app.services.orchestration import (
    approval_manager, context_manager, engine, evaluator,
    planner, reporting, script_library, states, task_graph, telemetry
)
from app.services.orchestration.common import new_id, now_iso, jdump, jload, OrchestrationError, ValidationError
import app.services.projects as P
import app.services.deliverables as D


@pytest.fixture
def test_fleet():
    """Simulated fleet with heterogeneous nodes."""
    return [
        {
            "id": "qwen-laptop",
            "hostname": "qwen-laptop",
            "is_worker": True,
            "enabled": True,
            "authenticated": True,
            "health": "online",
            "capabilities": ["model_inference", "inference"],
            "models": ["qwen2.5:1.5b", "llama3.2:1b"],
            "available_ram_mb": 4096,
            "restrictions": {},
        },
        {
            "id": "qwen-vps",
            "hostname": "qwen-vps",
            "is_worker": True,
            "enabled": True,
            "authenticated": True,
            "health": "online",
            "capabilities": ["python_execution", "system_diagnostics"],
            "models": [],
            "available_ram_mb": 1024,
            "restrictions": {},
        },
        {
            "id": "desktop-worker-01",
            "hostname": "desktop-worker-01",
            "is_worker": True,
            "enabled": True,
            "authenticated": True,
            "health": "online",
            "capabilities": ["model_inference", "inference", "python_execution"],
            "models": ["qwen2.5:1.5b"],
            "available_ram_mb": 8192,
            "restrictions": {},
        },
    ]


def _create_isolated_project(name: str, objective: str):
    res = engine.create_project_with_run(name=name, actor="tester", intake={"objective": objective}, autonomy_level=2)
    return res["project"], res["run"]


# ---------------------------------------------------------------------------
# 1. Capability Restriction & Unknown Script Detection
# ---------------------------------------------------------------------------

def test_unknown_script_rejected_by_validation(test_fleet):
    """Actions referencing hallucinated scripts (like generate_natural_response) must be rejected."""
    plan = {
        "schema": "qc.plan.v1",
        "overview": {
            "objective": "Avoid bot detection by generating natural human responses.",
            "scope": "Local simulation",
            "assumptions": [], "constraints": [],
            "expected_outcome": "Natural responses generated."
        },
        "paths": [{
            "key": "main", "name": "Main Path", "rationale": "Direct execution",
            "stages": [{"key": "s1", "name": "Execution", "objective": "Run tasks"}]
        }],
        "tasks": [
            {
                "key": "t1", "title": "Analyze User Interaction", "description": "Identify intent",
                "stage": "s1", "agent_role": "analysis", "depends_on": [], "allowed_nodes": ["qwen-laptop"],
                "action": {"type": "inference", "instruction": "Analyze interaction patterns", "inputs": [], "max_tokens": 200},
                "acceptance": {}, "priority": 3, "max_attempts": 2, "timeout_seconds": 300,
                "preferred_models": [], "required_capabilities": [], "expected_outputs": [], "approval": "auto", "risk": "low"
            },
            {
                "key": "t2", "title": "Generate Natural Language Response", "description": "Generate response",
                "stage": "s1", "agent_role": "integration", "depends_on": ["t1"], "allowed_nodes": ["qwen-vps"],
                "action": {"type": "library_script", "script_id": "generate_natural_response"},
                "acceptance": {}, "priority": 3, "max_attempts": 2, "timeout_seconds": 300,
                "preferred_models": [], "required_capabilities": [], "expected_outputs": [], "approval": "auto", "risk": "low"
            },
            {
                "key": "t3", "title": "Final Assembly", "description": "Compile report",
                "stage": "s1", "agent_role": "integration", "depends_on": ["t2"], "allowed_nodes": [],
                "action": {"type": "controller_step", "step": "assemble_report", "inputs": ["t2"]},
                "acceptance": {}, "priority": 3, "max_attempts": 2, "timeout_seconds": 300,
                "preferred_models": [], "required_capabilities": [], "expected_outputs": [], "approval": "auto", "risk": "low"
            }
        ],
        "deliverables": [{
            "key": "d1", "name": "Final Report", "type": "report", "acceptance_criteria": "Validated output",
            "produced_by": "t3", "stage": "s1", "terminal": True
        }],
        "risks": [], "approval_gates": []
    }

    norm = task_graph.normalize_plan(plan, test_fleet)
    val = task_graph.validate_plan(norm, test_fleet)
    assert not val["valid"], "Plan with unknown script must be invalid"
    assert any("references unknown script 'generate_natural_response'" in err for err in val["errors"])


def test_registered_script_accepted_and_pinning_enforced(test_fleet):
    """sys_health_basic is allowed on worker node with python_execution capability."""
    plan = {
        "schema": "qc.plan.v1",
        "overview": {
            "objective": "Measure system telemetry.", "scope": "Fleet snapshot",
            "assumptions": [], "constraints": [], "expected_outcome": "Metrics collected."
        },
        "paths": [{
            "key": "main", "name": "Main Path", "rationale": "Direct",
            "stages": [{"key": "s1", "name": "Execution", "objective": "Run tasks"}]
        }],
        "tasks": [
            {
                "key": "t1", "title": "Collect Health", "description": "Run diagnostic",
                "stage": "s1", "agent_role": "execution", "depends_on": [], "allowed_nodes": ["qwen-vps"],
                "action": {"type": "library_script", "script_id": "sys_health_basic"},
                "acceptance": {}, "priority": 3, "max_attempts": 2, "timeout_seconds": 60,
                "preferred_models": [], "required_capabilities": [], "expected_outputs": [], "approval": "auto", "risk": "low"
            }
        ],
        "deliverables": [{
            "key": "d1", "name": "Diagnostics", "type": "dataset", "acceptance_criteria": "JSON output",
            "produced_by": "t1", "stage": "s1", "terminal": True
        }],
        "risks": [], "approval_gates": []
    }
    norm = task_graph.normalize_plan(plan, test_fleet)
    val = task_graph.validate_plan(norm, test_fleet)
    assert val["valid"]


# ---------------------------------------------------------------------------
# 2. Self-Dependency & Reference Validation
# ---------------------------------------------------------------------------

def test_task_self_reference_in_inputs_rejected_or_sanitized(test_fleet):
    """Task t1 using output of t1 in action.inputs must be caught as self-dependency."""
    raw_plan = {
        "schema": "qc.plan.v1",
        "overview": {
            "objective": "Observe intent without bot detection.", "scope": "Bot detection",
            "assumptions": [], "constraints": [], "expected_outcome": "Human-like actions."
        },
        "paths": [{
            "key": "main", "name": "Main", "rationale": "Direct",
            "stages": [{"key": "s1", "name": "Execution", "objective": "Run tasks"}]
        }],
        "tasks": [
            {
                "key": "t1", "title": "Observe User Intent", "description": "Analyze intent",
                "stage": "s1", "agent_role": "analysis", "depends_on": [], "allowed_nodes": ["qwen-laptop"],
                # Intentionally invalid: self-reference in inputs
                "action": {"type": "inference", "instruction": "Summarize results", "inputs": ["t1"], "max_tokens": 200},
                "acceptance": {}, "priority": 3, "max_attempts": 2, "timeout_seconds": 300,
                "preferred_models": [], "required_capabilities": [], "expected_outputs": [], "approval": "auto", "risk": "low"
            }
        ],
        "deliverables": [{
            "key": "d1", "name": "Report", "type": "document", "acceptance_criteria": "Validated findings",
            "produced_by": "t1", "stage": "s1", "terminal": True
        }],
        "risks": [], "approval_gates": []
    }

    # 1. Validation directly on un-normalized plan must flag the self-dependency
    val_raw = task_graph.validate_plan(raw_plan, test_fleet)
    assert not val_raw["valid"]
    assert any("uses output of itself ('t1') as an input" in err for err in val_raw["errors"])

    # 2. Normalization must preserve declared inputs and depends_on without silent mutation
    norm = task_graph.normalize_plan(raw_plan, test_fleet)
    assert norm["tasks"][0]["action"]["inputs"] == ["t1"], "Self-reference must not be silently stripped from inputs"
    val_norm = task_graph.validate_plan(norm, test_fleet)
    assert not val_norm["valid"], "Validation must reject the self-referencing inputs"
    assert any("uses output of itself ('t1') as an input" in err for err in val_norm["errors"])


def test_task_depends_on_self_rejected(test_fleet):
    """Task depends_on containing itself must be flagged as a self-dependency."""
    plan = {
        "schema": "qc.plan.v1",
        "overview": {"objective": "Test", "scope": "Test", "assumptions": [], "constraints": [], "expected_outcome": "Outcome"},
        "paths": [{"key": "main", "name": "Main", "rationale": "Direct", "stages": [{"key": "s1", "name": "S1", "objective": "O"}]}],
        "tasks": [{
            "key": "t1", "title": "Task 1", "description": "Desc", "stage": "s1", "agent_role": "analysis",
            "depends_on": ["t1"], "allowed_nodes": [],
            "action": {"type": "inference", "instruction": "Analyze", "inputs": [], "max_tokens": 100},
            "acceptance": {}, "priority": 3, "max_attempts": 2, "timeout_seconds": 60,
            "preferred_models": [], "required_capabilities": [], "expected_outputs": [], "approval": "auto", "risk": "low"
        }],
        "deliverables": [{"key": "d1", "name": "D1", "type": "document", "acceptance_criteria": "C", "produced_by": "t1", "stage": "s1", "terminal": True}],
        "risks": [], "approval_gates": []
    }
    val = task_graph.validate_plan(plan, test_fleet)
    assert not val["valid"]
    assert any("depends on itself" in err for err in val["errors"])


# ---------------------------------------------------------------------------
# 3. Bounded Automatic Plan Repair
# ---------------------------------------------------------------------------

def test_auto_repair_converts_hallucinated_scripts_and_sanitizes_dependencies(test_fleet):
    """auto_repair_plan must repair unknown scripts into inference and sanitize self-references."""
    failed_plan = {
        "schema": "qc.plan.v1",
        "overview": {
            "objective": "Prevent bot detection with natural interactions.",
            "scope": "Human imitation",
            "assumptions": ["Trusted environment"], "constraints": [],
            "expected_outcome": "Natural conversation."
        },
        "paths": [{
            "key": "main", "name": "Main", "rationale": "Direct",
            "stages": [{"key": "s1", "name": "Execution", "objective": "Execute"}]
        }],
        "tasks": [
            {
                "key": "t1", "title": "Observe User Intent", "description": "Analyze intent",
                "stage": "s1", "agent_role": "analysis", "depends_on": [], "allowed_nodes": ["qwen-laptop"],
                # Self reference in inputs
                "action": {"type": "inference", "instruction": "Summarize results", "inputs": ["t1"], "max_tokens": 200},
                "acceptance": {}, "priority": 3, "max_attempts": 2, "timeout_seconds": 300,
                "preferred_models": [], "required_capabilities": [], "expected_outputs": [], "approval": "auto", "risk": "low"
            },
            {
                "key": "t2", "title": "Generate Natural Language Response", "description": "Create natural response",
                "stage": "s1", "agent_role": "integration", "depends_on": ["t1"], "allowed_nodes": ["qwen-vps"],
                # Hallucinated script
                "action": {"type": "library_script", "script_id": "generate_natural_response"},
                "acceptance": {}, "priority": 3, "max_attempts": 2, "timeout_seconds": 300,
                "preferred_models": [], "required_capabilities": [], "expected_outputs": [], "approval": "auto", "risk": "low"
            },
            {
                "key": "t3", "title": "Final Assembly", "description": "Assemble response",
                "stage": "s1", "agent_role": "integration", "depends_on": ["t2"], "allowed_nodes": [],
                # Hallucinated controller step
                "action": {"type": "controller_step", "step": "assemble_response", "inputs": ["t2"]},
                "acceptance": {}, "priority": 3, "max_attempts": 2, "timeout_seconds": 300,
                "preferred_models": [], "required_capabilities": [], "expected_outputs": [], "approval": "auto", "risk": "low"
            }
        ],
        "deliverables": [
            # Hallucinated deliverable type
            {"key": "d1", "name": "Natural Response", "type": "response", "acceptance_criteria": "Valid response",
             "produced_by": "t3", "stage": "s1", "terminal": True}
        ],
        "risks": [], "approval_gates": []
    }

    raw_val = task_graph.validate_plan(failed_plan, test_fleet)
    assert not raw_val["valid"]

    # Run bounded auto-repair (Stage 2: Anti-substitution enforced, no silent dependency mutation)
    repaired, remaining_errors = task_graph.auto_repair_plan(failed_plan, raw_val["errors"], test_fleet)

    # Execution bindings are preserved verbatim without silent mutation:
    assert repaired["tasks"][0]["action"]["inputs"] == ["t1"]
    assert repaired["deliverables"][0]["type"] == "document", "Deliverable type 'response' mapped to 'document'"

    # Silent action substitutions are strictly rejected (F-01 remediation):
    assert repaired["tasks"][1]["action"]["type"] == "library_script"
    assert repaired["tasks"][1]["action"]["script_id"] == "generate_natural_response"
    assert repaired["tasks"][2]["action"]["step"] == "assemble_response"
    assert any("references unknown script 'generate_natural_response'" in err for err in remaining_errors)
    assert any("has unknown controller step 'assemble_response'" in err for err in remaining_errors)


# ---------------------------------------------------------------------------
# 4. Acceptance Criteria & Non-Telemetry Report Assembly
# ---------------------------------------------------------------------------

def test_non_telemetry_project_report_assembly_and_evaluation():
    """A non-telemetry project (e.g. pure AI bot analysis) must not fail with has_measured_data."""
    mock_run = {"id": "run-test-isolation-01", "project_id": "prj-test-01"}
    title = "Don't Look Sketchy Final Report"

    # Accepted inputs consist only of AI model outputs (no worker_telemetry)
    results = {
        "t1": {
            "kind": "model_output",
            "model": "qwen2.5:1.5b",
            "node": "desktop-worker-01",
            "text": "1. Analyze traffic patterns and identify bot heuristics.\n2. Add random human jitter.\n3. Emulate natural keystroke timing.",
            "label": "AI-generated, unverified"
        },
        "t2": {
            "kind": "model_output",
            "model": "qwen2.5:1.5b",
            "node": "desktop-worker-01",
            "text": "Mitigation implemented: adaptive typing delay applied.",
            "label": "AI-generated, unverified"
        }
    }
    provenance = [
        {"key": "t1", "node": "desktop-worker-01", "attempts": 1, "kind": "model_output"},
        {"key": "t2", "node": "desktop-worker-01", "attempts": 1, "kind": "model_output"},
    ]

    # Assemble report
    rep = reporting.assemble_project_report(
        mock_run, title, results, provenance,
        objective="Avoid detection as a bot when interacting with web services."
    )

    md = rep["markdown"]
    assert "# Don't Look Sketchy Final Report" in md
    assert "## Objective" in md
    assert "## AI-generated summary" in md
    assert "AI-generated, unverified" in md
    assert "## Provenance" in md
    # Must NOT have empty measured telemetry table claiming qc.health.v1
    assert "## Measured telemetry" not in md, "Non-telemetry project must not have measured telemetry section"

    # Evaluate report with adapted evaluator
    has_telemetry = any(v.get("kind") == "worker_telemetry" for v in results.values())
    has_ai = any(v.get("kind") == "model_output" for v in results.values())
    has_data = any(v.get("kind") in ("worker_telemetry", "model_output", "controller_generated") for v in results.values())

    ev = evaluator.evaluate_report(md, has_telemetry=has_telemetry, has_ai=has_ai)
    ev["checks"].append({
        "name": "has_valid_inputs",
        "passed": has_data,
        "detail": "at least one validated input source"
    })
    ev["accepted"] = ev["accepted"] and all(c["passed"] for c in ev["checks"])

    assert ev["accepted"] is True, f"Report evaluation failed: {ev['checks']}"
    assert not any(c["name"] == "has_measured_data" and not c["passed"] for c in ev["checks"])


# ---------------------------------------------------------------------------
# 5. Multi-Project Context Isolation & Non-Leakage
# ---------------------------------------------------------------------------

def test_multi_project_context_and_memory_isolation():
    """Ensure context_manager, prompts, and task results do not leak across distinct projects."""
    p_health, run_health = _create_isolated_project(
        "Cluster Health Check", "Collect and monitor hardware telemetry across collective nodes."
    )
    p_bot, run_bot = _create_isolated_project(
        "Don't look sketchy", "Design human-like bot detection mitigation."
    )

    # Health project prompt
    prompt_health = context_manager.build_inference_prompt(
        "Summarize telemetry",
        {"t1": {"kind": "worker_telemetry", "node": "qwen-vps", "data": {"cpu_percent": 12.5, "mem_total_mib": 1024}}}
    )

    # Bot project prompt
    prompt_bot = context_manager.build_inference_prompt(
        "Analyze bot heuristics",
        {"t1": {"kind": "model_output", "node": "desktop-worker-01", "text": "Heuristics identify typing cadence."}}
    )

    assert "cpu_percent" in prompt_health
    assert "cpu_percent" not in prompt_bot, "Telemetry from health project leaked into bot project prompt"

    assert "typing cadence" in prompt_bot
    assert "typing cadence" not in prompt_health, "Bot project content leaked into health project prompt"

    # Memory isolation
    mem_health = context_manager.project_memory(run_health["id"])
    mem_bot = context_manager.project_memory(run_bot["id"])

    assert "Cluster Health Check" in mem_health["objective"] or "Collect and monitor" in mem_health["objective"]
    assert "bot detection" not in mem_health["objective"]

    assert "bot detection" in mem_bot["objective"].lower()
    assert "telemetry" not in mem_bot["objective"].lower()


# ---------------------------------------------------------------------------
# 6. Prevention of Invalid Plans Entering Execution
# ---------------------------------------------------------------------------

def test_prevent_invalid_plan_from_entering_execution():
    """Approval manager and start_run must reject plans that have invalid actions or self-dependencies."""
    proj, run = _create_isolated_project("Invalid Execution Test", "Attempt executing an invalid plan.")

    # Fabricate an invalid proposed revision directly
    with db_transaction() as c:
        rid = new_id("rev")
        invalid_plan = {
            "schema": "qc.plan.v1",
            "overview": {"objective": "Bad plan", "scope": "", "assumptions": [], "constraints": [], "expected_outcome": ""},
            "paths": [{"key": "main", "name": "Main", "rationale": "Direct", "stages": [{"key": "s1", "name": "S1", "objective": "O"}]}],
            "tasks": [{
                "key": "t1", "title": "Self Dependent", "description": "Desc", "stage": "s1", "agent_role": "analysis",
                "depends_on": ["t1"], "allowed_nodes": [],
                "action": {"type": "library_script", "script_id": "hallucinated_unknown_script"},
                "acceptance": {}, "priority": 3, "max_attempts": 2, "timeout_seconds": 60,
                "preferred_models": [], "required_capabilities": [], "expected_outputs": [], "approval": "auto", "risk": "low"
            }],
            "deliverables": [{"key": "d1", "name": "D", "type": "document", "acceptance_criteria": "A", "produced_by": "t1", "stage": "s1", "terminal": True}],
            "risks": [], "approval_gates": []
        }
        c.execute("""INSERT INTO orchestration_plan_revisions (id, run_id, revision_number, status, source, plan_json,
                     validation_json, model_info_json, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                  (rid, run["id"], 1, "proposed", "model", jdump(invalid_plan), jdump({"valid": False}), "{}", "tester", now_iso()))
        c.execute("UPDATE orchestration_runs SET current_revision_id=? WHERE id=?", (rid, run["id"]))
    states.transition(run["id"], "PLANNING", "plan_started", "tester")
    states.transition(run["id"], "AWAITING_PLAN_APPROVAL", "plan_proposed", "tester")

    # Attempt approving the invalid plan: must fail
    with pytest.raises(OrchestrationError) as exc_info:
        approval_manager.approve_plan(run["id"], "tester", "Approved", revision_id=rid)
    assert "Plan is not valid" in str(exc_info.value)
