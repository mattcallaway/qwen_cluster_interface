"""Test automated orchestrator project execution for Increment 17 capabilities & fault injection."""
import json
import pytest

from app.database import get_db_connection
from app.services.orchestration import (
    approval_manager,
    artifact_manager,
    engine,
    planner,
    states,
    task_graph,
)
from app.services.orchestration.model_router import node_inventory
from app.services.orchestration.pipeline_kv_diagnostics import run_diagnostics


def test_pipeline_kv_diagnostics_unit():
    """Verify diagnostic runner executes all 9 tests and traps all 5 faults."""
    res = run_diagnostics()
    assert res["status"] == "PASSED"
    assert res["total_tests"] == 9
    assert res["passed_tests"] == 9
    assert res["failed_tests"] == 0
    assert res["faults_injected"] == 5
    assert res["faults_trapped_and_contained"] == 5
    assert res["fault_containment_ratio"] == 1.0


def test_orchestrator_project_dag_validation_and_plan():
    """Verify the plan DAG for testing Increment 17 capabilities passes task_graph validation."""
    plan = {
        "schema": "qc.plan.v1",
        "overview": {
            "objective": "Verify Increment 17 capabilities under fault injection",
            "scope": "Searchable paged KV cache and pipeline parallelism",
            "assumptions": ["Controller is online"],
            "constraints": ["Isolated execution"],
            "expected_outcome": "Certified deliverable report",
        },
        "paths": [
            {
                "key": "main",
                "name": "Main Path",
                "rationale": "Sequential execution",
                "stages": [
                    {"key": "s_diag", "name": "Diagnostics", "objective": "Run tests"},
                    {"key": "s_report", "name": "Report", "objective": "Assemble report"},
                ],
            }
        ],
        "tasks": [
            {
                "key": "run_diagnostics",
                "title": "Execute Pipeline Diagnostics",
                "description": "Run live algorithmic and fault tests",
                "stage": "s_diag",
                "agent_role": "execution",
                "depends_on": [],
                "allowed_nodes": [],
                "priority": 1,
                "timeout_seconds": 120,
                "max_attempts": 2,
                "risk": "low",
                "approval": "auto",
                "action": {
                    "type": "controller_step",
                    "step": "pipeline_kv_diagnostics",
                    "inputs": [],
                },
                "expected_outputs": ["pipeline_kv_diagnostics.json"],
                "acceptance": {},
            },
            {
                "key": "assemble_report",
                "title": "Assemble Report",
                "description": "Compile markdown deliverable",
                "stage": "s_report",
                "agent_role": "integration",
                "depends_on": ["run_diagnostics"],
                "allowed_nodes": [],
                "priority": 2,
                "timeout_seconds": 120,
                "max_attempts": 2,
                "risk": "low",
                "approval": "auto",
                "action": {
                    "type": "controller_step",
                    "step": "assemble_report",
                    "inputs": ["run_diagnostics"],
                },
                "expected_outputs": ["health_report.md"],
                "acceptance": {},
            },
        ],
        "deliverables": [
            {
                "key": "d1",
                "name": "Verification Report",
                "type": "report",
                "acceptance_criteria": "Validated findings",
                "produced_by": "assemble_report",
                "stage": "s_report",
                "terminal": True,
            }
        ],
        "risks": [{"description": "None", "mitigation": "None"}],
        "approval_gates": [],
    }

    val = task_graph.validate_plan(plan, node_inventory())
    assert val["valid"], f"Plan validation errors: {val.get('errors')}"
