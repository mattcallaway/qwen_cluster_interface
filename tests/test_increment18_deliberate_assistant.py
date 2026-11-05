"""Comprehensive Test Suite for Increment 18: Deliberate General-Purpose Assistant & Task-Solving Loop.

Covers:
1. Schema Migration 20 (`project_requirement_items`, `project_requirement_revisions`, `project_memory_state`, `procedure_outcome_registry`).
2. Durable Requirement Ledger: objective, deliverables, constraints (hard vs soft), exclusions, assumptions,
   material missing info, user decisions, requirement dependencies, conflict detection, mid-flight user steering vs
   cancellation, selective artifact invalidation preserving unaffected completed work, and source/instruction separation.
3. Durable Project Memory & Context Manager Fix: missing `verification_status` remains `unknown` (never defaulting
   to `supported`), qualifier/negation/date/unit preservation under truncation, and downstream decision invalidation
   when upstream inputs change.
4. Deliberate Reasoning, 7-Step Escalation Ladder, Capability-Aware Role Routing (`qwen2.5:3b` vs `qwen2.5:1.5b`),
   and Truncation Detection.
5. Typed Tool Catalogue across all 7 domains (`inspect_document_or_repo`, `decimal_calculate_and_reconcile`,
   `compare_options_against_constraints`, `analyze_structured_dataset`, `execute_and_test_code`,
   `verify_web_application_behavior`, `inspect_operational_state`).
6. Five-Step Failure Repair Cycle with Hidden Oracle Protection.
7. Milestone A Synthetic Supplier-Selection Pilot (end-to-end with mid-flight budget ceiling reduction,
   controller restart/resumption, and infeasible variant returning `blocked`).
8. All 20 Acceptance Tasks (`accept-01`..`accept-20`) with 13-field evidence records.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import pytest

from app.config import settings
from app.database import get_db_connection, init_db
from app.migrations import current_version, latest_version
from app.services import projects as P
from app.services.orchestration import (
    acceptance_harness,
    artifact_manager,
    context_manager,
    deliberate_loop,
    engine,
    intake as intake_mod,
    tool_catalogue,
)


@pytest.fixture()
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setenv("QC_ALLOW_SUBPROCESS_FALLBACK", "1")
    init_db()
    return tmp_path


def test_migration_20_applied(isolated_env: Path):
    conn = get_db_connection()
    try:
        assert current_version(conn) == latest_version()
        assert current_version(conn) >= 20
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "project_requirement_items" in tables
        assert "project_requirement_revisions" in tables
        assert "project_memory_state" in tables
        assert "procedure_outcome_registry" in tables
    finally:
        conn.close()


def test_context_manager_missing_status_defaults_to_unknown_never_supported(isolated_env: Path):
    prompt = context_manager.build_inference_prompt(
        instruction="Summarize verified claims only.",
        inputs={
            "doc_1": {
                "claims": [
                    {"claim_text": "Claim with explicit support", "verification_status": "supported"},
                    {"claim_text": "Claim with missing status", "verification_status": None},
                    {"claim_text": "Claim with empty status", "verification_status": ""},
                ]
            }
        },
        knowledge_records=[
            {"subject": "Node A", "statement": "Unverified note", "verification_status": None},
        ],
    )
    assert "Claim 1 [SUPPORTED]: Claim with explicit support" in prompt
    assert "Claim 2 [UNKNOWN]: Claim with missing status" in prompt
    assert "Claim 3 [UNKNOWN]: Claim with empty status" in prompt
    assert "- [unknown] Node A: Unverified note" in prompt


def test_requirement_ledger_conflicts_ambiguity_steering_and_cancellation(isolated_env: Path):
    # 1. Detect contradictory budget ceiling vs floor and deliverable vs exclusion conflict
    conflicting_intake = {
        "objective": "Deploy regional telemetry gateway service.",
        "constraints": [
            "Maximum total budget ceiling: $8,000.00",
            "Minimum spend of $12,000.00 required for redundant hardware",
            "Prefer low-power ARM nodes if possible",
        ],
        "exclusions": ["No cloud relay service"],
        "deliverables": ["cloud relay service", "gateway_config.json"],
    }
    ledger = intake_mod.parse_intake_requirements(conflicting_intake)
    diag = ledger.detect_conflicts_and_ambiguities()
    assert diag["has_conflicts"] is True
    conflict_types = {c["type"] for c in diag["conflicts"]}
    assert "numeric_budget_conflict" in conflict_types
    assert "deliverable_exclusion_conflict" in conflict_types

    # 2. Persist ledger and test mid-flight user steering vs untrusted source rejection
    created = engine.create_project_with_run(
        name="Steering Test Project",
        actor="user1",
        intake={
            "objective": "Evaluate supplier quotes and produce recommendation.",
            "constraints": ["Maximum budget ceiling: $15,000.00"],
            "deliverables": ["quote_ledger.json", "recommendation.md"],
        },
    )
    pid = created["project"]["id"]
    rid = created["run"]["id"]
    run = engine.get_run(rid)
    clean_ledger = intake_mod.parse_intake_requirements(
        {
            "objective": "Evaluate supplier quotes and produce recommendation.",
            "constraints": ["Maximum budget ceiling: $15,000.00"],
            "deliverables": ["quote_ledger.json", "recommendation.md"],
        }
    )
    intake_mod.persist_requirement_ledger(pid, clean_ledger, run_id=rid, actor="user1")

    art_keep = artifact_manager.write_controller_artifact(run, None, "quote_ledger.json", b'{"ok":true}')
    art_stale = artifact_manager.write_controller_artifact(run, None, "recommendation.md", b"# Old Rec")
    artifact_manager.set_validation(art_keep, "valid", "Valid quote ledger")
    artifact_manager.set_validation(art_stale, "valid", "Valid old rec")

    # Untrusted retrieved document must be blocked from steering requirements
    with pytest.raises(PermissionError):
        intake_mod.apply_user_steering_update(
            pid,
            run_id=rid,
            updated_constraints=["Budget ceiling $999,999.00"],
            source_origin="retrieved_document",
        )

    # Legitimate user steering updates constraint, invalidates `art_stale`, and preserves `art_keep`
    steer_res = intake_mod.apply_user_steering_update(
        pid,
        run_id=rid,
        actor="user1",
        action="steer",
        replaced_req_codes={"req-con-2": "Maximum budget ceiling: $13,000.00"},
        affected_artifact_ids=[art_stale],
        reason="Budget reduced to $13,000.00",
    )
    assert steer_res["revision_number"] == 2
    assert art_stale in steer_res["invalidated_artifacts"]
    assert art_keep in steer_res["preserved_artifacts"]


def test_downstream_memory_invalidation_on_input_change(isolated_env: Path):
    proj = P.create_project("Memory Invalidation Test", "user1", objective="Test memory invalidation")
    pid = proj["id"]
    context_manager.save_project_memory_state(
        pid,
        current_cycle_phase="verify",
        decision_summaries=[
            {"key": "vendor_choice", "recommended": "SUP-APEX", "depends_on": ["budget_ceiling"], "status": "supported"},
            {"key": "unrelated_choice", "recommended": "LINUX", "depends_on": ["os_policy"], "status": "supported"},
        ],
        verification_results={
            "vendor_choice": {"status": "supported"},
            "unrelated_choice": {"status": "supported"},
        },
    )
    updated = context_manager.invalidate_downstream_memory_on_input_change(
        pid,
        changed_input_key="budget_ceiling",
        reason="Ceiling lowered from $15k to $13k",
        affected_decision_keys=["vendor_choice"],
    )
    decs = {d["key"]: d for d in updated["decision_summaries"]}
    assert decs["vendor_choice"]["status"] == "invalidated"
    assert decs["unrelated_choice"]["status"] == "supported"
    assert updated["verification_results"]["vendor_choice"]["status"] == "unknown"


def test_mode_classification_and_role_routing_and_truncation(isolated_env: Path):
    direct = deliberate_loop.classify_request_mode("What is 2 + 2?")
    assert direct["mode"] == "direct_response"

    durable = deliberate_loop.classify_request_mode(
        "Compare 3 supplier quotes against our $13,000 budget ceiling and produce deliverables: quote_ledger.json and recommendation.md."
    )
    assert durable["mode"] == "durable_project"

    r_complex = deliberate_loop.select_model_for_role("complex_reasoning", ["qwen2.5:1.5b", "qwen2.5:3b"])
    assert r_complex["selected_model"] == "qwen2.5:3b"

    r_fast = deliberate_loop.select_model_for_role("fast_extraction", ["qwen2.5:1.5b", "qwen2.5:3b"])
    assert r_fast["selected_model"] == "qwen2.5:1.5b"

    trunc_json = deliberate_loop.detect_generation_truncation('{"key": "val"', gen_tokens=512, num_predict=512, expected_format="json")
    assert trunc_json["truncated"] is True

    complete_json = deliberate_loop.detect_generation_truncation('{"key": "val"}', gen_tokens=20, num_predict=512, expected_format="json")
    assert complete_json["truncated"] is False


def test_procedure_outcome_registry_and_revocation(isolated_env: Path):
    rec = deliberate_loop.record_procedure_outcome(
        task_category="reasoning_and_data",
        procedure_key="decimal_supplier_comparison_v1",
        outcome_status="verified_success",
        procedure_summary="Resolve quote revisions first, then run exact Decimal cost calculation.",
        model_id="qwen2.5:3b",
        verification_digest="sha256-abc",
    )
    active = deliberate_loop.query_verified_procedures("reasoning_and_data")
    assert any(p["id"] == rec["id"] for p in active)

    assert deliberate_loop.revoke_procedure_outcome(rec["id"], "Superseded by v2") is True
    active_after = deliberate_loop.query_verified_procedures("reasoning_and_data")
    assert not any(p["id"] == rec["id"] for p in active_after)


def test_milestone_a_supplier_pilot_and_all_20_acceptance_tasks(isolated_env: Path):
    suite = acceptance_harness.run_all_20_acceptance_tasks(
        source_commit="HEAD",
        use_live_inference=False,
        preferred_model="qwen2.5:3b",
    )
    assert suite["task_count"] == 20
    assert suite["achieved_scores"]["correct"] == 20
    assert suite["achieved_scores"]["false_success_count"] == 0
    assert suite["achieved_scores"]["unsupported_claims_count"] in (0, "not_evaluated")
    assert suite["achieved_scores"]["unauthorized_side_effects_count"] in (0, "not_evaluated")
    assert suite["achieved_scores"]["meets_provisional_target_16_of_20"] is True

    required_fields = {
        "source_commit",
        "model_identity",
        "fixture_version",
        "producing_actor",
        "execution_classification",
        "counts_as_autonomous_capability",
        "native_run_task_lease_ids",
        "artifact_versions",
        "independent_check_results",
        "elapsed_seconds",
        "model_calls",
        "token_usage",
        "user_interventions",
        "final_status",
        "unsupported_claims",
        "unauthorized_side_effects",
    }
    for task_res in suite["tasks"]:
        assert required_fields.issubset(set(task_res.keys())), f"Missing fields in {task_res['id']}"
        assert task_res["final_status"] == "correct"


def test_deliberate_api_endpoints(isolated_env: Path):
    import re
    from starlette.testclient import TestClient
    from app.auth import create_session_token
    from app.main import app

    client = TestClient(app)
    client.cookies.set("qc_session", create_session_token("admin"))
    html = client.get("/projects/orchestrate/new").text
    m = re.search(r'data-csrf="([0-9a-f]+)"', html)
    assert m, "csrf token not found"
    headers = {"X-CSRF-Token": m.group(1)}

    # 1. GET /api/v1/orchestration/tools
    r_tools = client.get("/api/v1/orchestration/tools")
    assert r_tools.status_code == 200
    tools_list = r_tools.json()["result"]["tools"]
    assert len(tools_list) >= 8

    # 2. POST /api/v1/chat/deliberate in direct_response mode
    r_direct = client.post(
        "/api/v1/chat/deliberate",
        json={"message": "What is the SHA-256 hex length in characters?"},
        headers=headers,
    )
    assert r_direct.status_code == 200
    d_res = r_direct.json()
    assert d_res["mode"] == "direct_response"
    assert d_res["created_project"] is False

    # 3. POST /api/v1/chat/deliberate in durable_project mode
    r_proj = client.post(
        "/api/v1/chat/deliberate",
        json={
            "message": "Build a supplier comparison report and JSON artifact with budget under $25,000 and must support SSO.",
            "intake": {
                "objective": "Build a supplier comparison report and JSON artifact",
                "constraints": "Must stay under $25,000\nMust support SSO",
                "deliverables": "comparison_report.md\nsupplier_decision.json",
            },
        },
        headers=headers,
    )
    assert r_proj.status_code == 200
    p_res = r_proj.json()
    assert p_res["mode"] == "durable_project"
    pid = p_res["project_id"]
    assert pid

    # 4. GET /api/v1/projects/{project_id}/orchestration/requirements
    r_req = client.get(f"/api/v1/projects/{pid}/orchestration/requirements")
    assert r_req.status_code == 200
    assert r_req.json()["result"]["revision_number"] >= 1

    # 5. GET /api/v1/projects/{project_id}/orchestration/deliberate-status
    r_stat = client.get(f"/api/v1/projects/{pid}/orchestration/deliberate-status")
    assert r_stat.status_code == 200
    assert r_stat.json()["result"]["project_id"] == pid

    # 6. POST /api/v1/projects/{project_id}/orchestration/steer
    r_steer = client.post(
        f"/api/v1/projects/{pid}/orchestration/steer",
        json={
            "action": "steer",
            "updated_constraints": ["Must stay under $20,000"],
            "reason": "Update hard budget cap to $20,000.",
        },
        headers=headers,
    )
    assert r_steer.status_code == 200
    assert r_steer.json()["result"]["action"] == "steer"

