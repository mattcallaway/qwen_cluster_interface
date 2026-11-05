"""Audit Increment R3 Reproduction and Regression Tests (AUD-01, AUD-02, AUD-08, AUD-10a, AUD-12).

Covers:
- AUD-01: Supplier-option projects without user-supplied quotes transition to `clarify` / `missing_info`
  and never silently fall back to `SYNTHETIC_SUPPLIER_QUOTES`.
- AUD-02 & AUD-12:
  * Multi-domain detection and task contract decomposition (`financial_reconciliation`,
    `structured_data_analysis`, `research_synthesis`, `web_app_verification`, `repository_bugfix`,
    `supplier_option_evaluation`, and `awaiting_clarification` / `unsupported_request`).
  * `validate_proposed_action` enforces `spec.input_schema` required keys, allowed keys, and types.
  * `ToolCatalogue.execute_tool` implements real handlers for `search_and_retrieve_sources` and
    `create_or_revise_artifact`, and blocks direct unleased `worker_model_inference`.
- AUD-08: Financial ledger reconciliation enforces target-grant match category eligibility,
  cash/in-kind match share caps (`max_inkind_match_share`, `min_cash_match_share`), currency consistency,
  unknown rule field rejection, and `rule_evidence_id` verification against scoped project evidence.
- AUD-10a: `execute_checkpointed_work_boundary` indexes fence-failed items in `project_epistemic_fts`,
  and `WorkingMemoryRetriever.search_project_epistemic_fts` excludes `stale` and `superseded` items
  by default (`include_historical=False`).
"""
from __future__ import annotations

from pathlib import Path
import pytest

from app.config import settings
from app.database import get_db_connection, init_db
from app.services.orchestration import (
    artifact_manager,
    deliberate_loop,
    engine,
    financial_ledger,
    intake as intake_mod,
    protected_repo_verifier,
    tool_catalogue,
    working_memory as wm,
)


@pytest.fixture()
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    wm.GLOBAL_WORKING_SET_CACHE = wm.WorkingSetCache()
    protected_repo_verifier.configure_protected_repo_verifier(None)
    yield tmp_path
    protected_repo_verifier.configure_protected_repo_verifier(None)


def _create_project_from_intake(name: str, intake_payload: dict) -> tuple[str, str]:
    created = engine.create_project_with_run(
        name=name,
        actor="tester",
        intake=intake_payload,
        autonomy_level=2,
    )
    project_id = created["project"]["id"]
    run_id = created["run"]["id"]
    ledger = intake_mod.parse_intake_requirements(intake_payload)
    intake_mod.persist_requirement_ledger(project_id, ledger, run_id=run_id, actor="tester")
    return project_id, run_id


def test_aud01_supplier_project_without_quotes_requires_clarification_never_synthetic_quotes(isolated_db: Path):
    """AUD-01: Supplier project without user-supplied quotes must halt in `clarify` and never inject SYNTHETIC_SUPPLIER_QUOTES."""
    project_id, _ = _create_project_from_intake(
        "Supplier No Quotes Project",
        {
            "objective": "Evaluate supplier quotes for 10 server units and recommend the lowest-cost compliant vendor.",
            "constraints": [
                "Maximum total budget ceiling: $14,800.00",
                "Maximum delivery lead time: 21 days",
            ],
            "deliverables": [
                "quote_reconciliation_ledger.json",
                "supplier_recommendation.json",
            ],
        },
    )

    res = deliberate_loop.advance_deliberate_project(
        project_id,
        max_steps=3,
        use_live_worker=False,
    )
    assert res.get("status") == "clarify" or res.get("final_cycle_phase") == "clarify"
    gaps = res.get("material_gaps") or []
    assert any("quote" in str(g).lower() or "supplier" in str(g).lower() for g in gaps)

    arts = artifact_manager.list_artifacts(project_id)
    assert len(arts) == 0, f"Must not fabricate artifacts from SYNTHETIC_SUPPLIER_QUOTES: {arts}"


def test_aud02_and_aud12_multi_domain_detection_and_decomposition_without_supplier_fallback(isolated_db: Path):
    """AUD-02 & AUD-12: Coordinator classifies and executes financial_reconciliation, structured_data_analysis,
    research_synthesis, web_app_verification, and routes ambiguous/unsupported requests to clarify rather than supplier evaluation.
    """
    # 1. Ambiguous / unsupported prompt without domain inputs must NOT route to supplier_option_evaluation
    p_unknown, _ = _create_project_from_intake(
        "General Strategy Prompt",
        {
            "objective": "Draft a high-level organizational charter for a community gardening club.",
            "deliverables": ["charter_notes.md"],
        },
    )
    dom_unknown = deliberate_loop.detect_project_domain(p_unknown)
    assert dom_unknown != "supplier_option_evaluation"
    res_unknown = deliberate_loop.advance_deliberate_project(p_unknown, max_steps=3, use_live_worker=False)
    assert res_unknown.get("domain") != "supplier_option_evaluation"
    assert res_unknown.get("status") == "clarify" or res_unknown.get("final_cycle_phase") == "clarify"

    # 2. Financial reconciliation domain
    p_fin, run_fin = _create_project_from_intake(
        "Grant Budget Reconciliation Project",
        {
            "objective": "Reconcile grant budget allocations and match requirements for the watershed restoration ledger.",
            "deliverables": ["financial_reconciliation_report.json"],
        },
    )
    wm.record_epistemic_item(
        p_fin,
        epistemic_category="source_observation",
        statement="Attached grant budget ledger specification.",
        verification_status="observed",
        run_id=run_fin,
        qualifiers={
            "financial_ledger": {
                "total": "10000.00",
                "costs": [
                    {"id": "C_EQUIP", "category": "equipment", "amount": "8000.00"},
                    {"id": "C_LABOR", "category": "labor", "amount": "2000.00"},
                ],
                "funds": [
                    {
                        "id": "F_GRANT",
                        "kind": "grant",
                        "status": "committed",
                        "amount": "8000.00",
                        "eligible_categories": ["equipment", "labor"],
                        "rule_evidence_id": "EV-GRANT-01",
                        "match_rate": "0.25",
                        "match_basis": "grant",
                        "accepted_match_kinds": ["cash"],
                        "category_caps": [["labor", "0.50"]],
                    },
                    {
                        "id": "F_CASH",
                        "kind": "cash",
                        "status": "committed",
                        "amount": "2000.00",
                        "eligible_categories": ["equipment", "labor"],
                        "rule_evidence_id": "EV-CASH-01",
                    },
                ],
                "allocations": [
                    {"cost_id": "C_EQUIP", "fund_id": "F_GRANT", "amount": "8000.00"},
                    {"cost_id": "C_LABOR", "fund_id": "F_CASH", "amount": "2000.00", "match_for": "F_GRANT"},
                ],
            }
        },
    )
    assert deliberate_loop.detect_project_domain(p_fin) == "financial_reconciliation"
    res_fin = deliberate_loop.advance_deliberate_project(p_fin, max_steps=3, use_live_worker=False)
    assert res_fin["domain"] == "financial_reconciliation"
    assert res_fin["final_cycle_phase"] == "completed"

    # 3. Structured data analysis domain
    p_data, run_data = _create_project_from_intake(
        "Sensor Dataset Analysis Project",
        {
            "objective": "Analyze structured dataset of regional sensor readings by station and flag z-score anomalies.",
            "deliverables": ["dataset_analysis_summary.json"],
        },
    )
    wm.record_epistemic_item(
        p_data,
        epistemic_category="source_observation",
        statement="Attached structured dataset rows.",
        verification_status="observed",
        run_id=run_data,
        qualifiers={
            "dataset_rows": [
                {"id": "r1", "station": "north", "amount": "100.00"},
                {"id": "r2", "station": "north", "amount": "102.00"},
                {"id": "r3", "station": "south", "amount": "98.00"},
                {"id": "r4", "station": "south", "amount": "100.00"},
            ],
            "group_by": "station",
            "value_field": "amount",
        },
    )
    assert deliberate_loop.detect_project_domain(p_data) == "structured_data_analysis"
    res_data = deliberate_loop.advance_deliberate_project(p_data, max_steps=3, use_live_worker=False)
    assert res_data["domain"] == "structured_data_analysis"
    assert res_data["final_cycle_phase"] == "completed"

    # 4. Research synthesis domain
    p_res, run_res = _create_project_from_intake(
        "Research Synthesis Project",
        {
            "objective": "Search and retrieve project sources on RFC 9110 HTTP caching semantics and synthesize findings.",
            "deliverables": ["research_evidence_synthesis.json"],
        },
    )
    wm.record_epistemic_item(
        p_res,
        epistemic_category="source_observation",
        statement="RFC 9110 Section 5.2 specifies Cache-Control directives including max-age and no-store.",
        verification_status="observed",
        run_id=run_res,
        source_refs=["src:rfc9110"],
        qualifiers={"research_queries": ["RFC 9110 Cache-Control max-age"]},
    )
    assert deliberate_loop.detect_project_domain(p_res) == "research_synthesis"
    res_research = deliberate_loop.advance_deliberate_project(p_res, max_steps=3, use_live_worker=False)
    assert res_research["domain"] == "research_synthesis"
    assert res_research["final_cycle_phase"] == "completed"

    # 5. Web application verification domain
    p_web, run_web = _create_project_from_intake(
        "Web App DOM Verification Project",
        {
            "objective": "Verify web application HTML DOM elements and interactive JS handler behavior.",
            "deliverables": ["web_app_verification_report.json"],
        },
    )
    wm.record_epistemic_item(
        p_web,
        epistemic_category="source_observation",
        statement="Attached web application HTML and JS bundle.",
        verification_status="observed",
        run_id=run_web,
        qualifiers={
            "web_app_bundle": {
                "html_content": "<!doctype html><html><body><main id='app-root'><button id='calc-btn'>Run</button></main></body></html>",
                "js_content": "function handleCalc() { return true; }",
                "required_elements": ["app-root", "calc-btn"],
                "interaction_cases": [{"name": "calc_click", "required_handler": "handleCalc"}],
            }
        },
    )
    assert deliberate_loop.detect_project_domain(p_web) == "web_app_verification"
    res_web = deliberate_loop.advance_deliberate_project(p_web, max_steps=3, use_live_worker=False)
    assert res_web["domain"] == "web_app_verification"
    assert res_web["final_cycle_phase"] == "completed"


def test_aud02_tool_catalogue_input_schema_validation_and_real_handlers(isolated_db: Path):
    """AUD-02: validate_proposed_action enforces input_schema types/keys, and execute_tool runs real handlers."""
    project_id, run_id = _create_project_from_intake(
        "Tool Schema & Handler Test",
        {"objective": "Test tool schema validation and real tool handlers."},
    )
    tc = wm.register_task_contract(
        project_id,
        run_id=run_id,
        task_key="t_schema_test",
        purpose="Test schema enforcement.",
        expected_output="out.json",
        selected_tool="inspect_repository_workspace",
        permitted_effects=["read_only", "read_scoped_workspace", "write_project_artifact", "worker_inference"],
    )

    # 1a. Missing required argument (`workspace_path`) must be rejected
    res_missing = deliberate_loop.validate_proposed_action(
        project_id,
        tc,
        {"selected_tool": "inspect_repository_workspace", "arguments": {"max_file_chars": 4000}},
    )
    assert res_missing["valid"] is False
    assert res_missing["error_class"] == "invalid_arguments_schema"

    # 1b. Wrong type for argument (`max_file_chars` passed as string instead of int) must be rejected
    res_bad_type = deliberate_loop.validate_proposed_action(
        project_id,
        tc,
        {
            "selected_tool": "inspect_repository_workspace",
            "arguments": {"workspace_path": "repo", "max_file_chars": "not_an_int"},
        },
    )
    assert res_bad_type["valid"] is False
    assert res_bad_type["error_class"] == "invalid_arguments_schema"

    # 1c. Unknown extra argument (`unexpected_extra_field`) must be rejected
    res_unknown_arg = deliberate_loop.validate_proposed_action(
        project_id,
        tc,
        {
            "selected_tool": "inspect_repository_workspace",
            "arguments": {"workspace_path": "repo", "max_file_chars": 4000, "unexpected_extra_field": 123},
        },
    )
    assert res_unknown_arg["valid"] is False
    assert res_unknown_arg["error_class"] == "invalid_arguments_schema"

    # 2a. Real handler for `search_and_retrieve_sources`
    wm.record_epistemic_item(
        project_id,
        epistemic_category="source_observation",
        statement="Hydrology telemetry station S-42 recorded peak flow of 412 cfs on 2026-11-01.",
        verification_status="observed",
        run_id=run_id,
        source_refs=["station:S-42"],
    )
    search_obs = tool_catalogue.GLOBAL_TOOL_CATALOGUE.execute_tool(
        "search_and_retrieve_sources",
        {"queries": ["Hydrology telemetry S-42"], "project_id": project_id, "as_of_date": "2026-11-05"},
    )
    assert search_obs["status"] == "observed"
    assert "sources" in search_obs["result"]
    assert len(search_obs["result"]["sources"]) >= 1
    assert any("412 cfs" in s.get("statement", "") for s in search_obs["result"]["sources"])

    # 2b. Real handler for `create_or_revise_artifact`
    art_obs = tool_catalogue.GLOBAL_TOOL_CATALOGUE.execute_tool(
        "create_or_revise_artifact",
        {
            "project_id": project_id,
            "run_id": run_id,
            "filename": "real_handler_test.txt",
            "content": "Verified content written through create_or_revise_artifact",
            "provenance": "controller_generated",
        },
    )
    assert art_obs["status"] in ("observed", "created")
    assert art_obs["result"].get("artifact_id")
    assert len(art_obs["result"].get("sha256", "")) == 64

    # 2c. Direct `worker_model_inference` invocation through execute_tool must fail closed
    inf_obs = tool_catalogue.GLOBAL_TOOL_CATALOGUE.execute_tool(
        "worker_model_inference",
        {"purpose": "task", "prompt": "Hello", "role": "direct_qa", "max_tokens": 64},
    )
    assert inf_obs["status"] == "failed"
    assert "lease" in str(inf_obs.get("error", "")).lower()


def test_aud08_financial_reconciliation_grant_match_categories_share_caps_currency_and_evidence(isolated_db: Path):
    """AUD-08: Financial ledger enforces target-grant match category eligibility, cash/in-kind share caps,
    currency consistency, unknown rule fields, and rule_evidence_id verification.
    """
    # 1. Cost category allowed by matching fund (`admin`) but disallowed by target grant (`equipment`, `labor`)
    costs = (
        financial_ledger.Cost(id="C_EQUIP", category="equipment", amount="80000.00"),
        financial_ledger.Cost(id="C_ADMIN", category="admin", amount="20000.00"),
    )
    funds = (
        financial_ledger.Fund(
            id="F_GRANT",
            kind="grant",
            status="committed",
            amount="80000.00",
            eligible_categories=frozenset({"equipment", "labor"}),
            rule_evidence_id="EV-GRANT",
            match_rate="0.25",
            match_basis="grant",
            accepted_match_kinds=frozenset({"cash", "inkind"}),
        ),
        financial_ledger.Fund(
            id="F_CASH_MATCH",
            kind="cash",
            status="committed",
            amount="20000.00",
            eligible_categories=frozenset({"equipment", "labor", "admin"}),
            rule_evidence_id="EV-CASH",
        ),
    )
    allocs = (
        financial_ledger.Allocation(cost_id="C_EQUIP", fund_id="F_GRANT", amount="80000.00"),
        financial_ledger.Allocation(cost_id="C_ADMIN", fund_id="F_CASH_MATCH", amount="20000.00", match_for="F_GRANT"),
    )
    res_ineligible_match_cat = financial_ledger.reconcile("100000.00", costs, funds, allocs)
    assert res_ineligible_match_cat["valid"] is False
    assert "ineligible_match_category" in res_ineligible_match_cat["errors"] or "ineligible_match" in res_ineligible_match_cat["errors"]

    # 2. In-kind match share cap (`max_inkind_match_share="0.50"`) and minimum cash match share (`min_cash_match_share="0.50"`)
    costs_2 = (
        financial_ledger.Cost(id="C_EQUIP", category="equipment", amount="80000.00"),
        financial_ledger.Cost(id="C_LABOR", category="labor", amount="20000.00"),
    )
    funds_2 = (
        financial_ledger.Fund(
            id="F_GRANT",
            kind="grant",
            status="committed",
            amount="80000.00",
            eligible_categories=frozenset({"equipment", "labor"}),
            rule_evidence_id="EV-GRANT",
            match_rate="0.25",
            match_basis="grant",
            accepted_match_kinds=frozenset({"cash", "inkind"}),
            max_inkind_match_share="0.50",
            min_cash_match_share="0.50",
        ),
        financial_ledger.Fund(
            id="F_INKIND",
            kind="inkind",
            status="committed",
            amount="20000.00",
            eligible_categories=frozenset({"labor"}),
            rule_evidence_id="EV-INKIND",
        ),
    )
    allocs_2 = (
        financial_ledger.Allocation(cost_id="C_EQUIP", fund_id="F_GRANT", amount="80000.00"),
        financial_ledger.Allocation(cost_id="C_LABOR", fund_id="F_INKIND", amount="20000.00", match_for="F_GRANT"),
    )
    res_share_cap = financial_ledger.reconcile("100000.00", costs_2, funds_2, allocs_2)
    assert res_share_cap["valid"] is False
    assert "inkind_match_cap_exceeded" in res_share_cap["errors"]
    assert "cash_match_minimum_unmet" in res_share_cap["errors"]

    # 3. Currency mismatch rejection
    costs_curr = (
        financial_ledger.Cost(id="C1", category="equipment", amount="100.00", currency="EUR"),
    )
    funds_curr = (
        financial_ledger.Fund(
            id="F1",
            kind="cash",
            status="committed",
            amount="100.00",
            eligible_categories=frozenset({"equipment"}),
            rule_evidence_id="EV-1",
            currency="USD",
        ),
    )
    allocs_curr = (
        financial_ledger.Allocation(cost_id="C1", fund_id="F1", amount="100.00", currency="USD"),
    )
    res_curr = financial_ledger.reconcile("100.00", costs_curr, funds_curr, allocs_curr, currency="USD")
    assert res_curr["valid"] is False
    assert "currency_mismatch" in res_curr["errors"]

    # 4. Unknown rule field and unverified rule_evidence_id rejection via `reconcile_grant_budget_ledger` / `decimal_calculate_and_reconcile`
    obs_unknown_field = tool_catalogue.GLOBAL_TOOL_CATALOGUE.execute_tool(
        "decimal_calculate_and_reconcile",
        {
            "mode": "reconcile",
            "payload": {
                "total": "100.00",
                "costs": [{"id": "C1", "category": "equipment", "amount": "100.00"}],
                "funds": [
                    {
                        "id": "F1",
                        "kind": "cash",
                        "status": "committed",
                        "amount": "100.00",
                        "eligible_categories": ["equipment"],
                        "rule_evidence_id": "EV-UNVERIFIED",
                        "unknown_invented_rule_flag": True,
                    }
                ],
                "allocations": [{"cost_id": "C1", "fund_id": "F1", "amount": "100.00"}],
                "verified_evidence_ids": ["EV-APPROVED-ONLY"],
            },
        },
    )
    assert obs_unknown_field["status"] == "failed"
    assert "unknown_rule_field" in obs_unknown_field["result"]["errors"]
    assert "unverified_rule_evidence" in obs_unknown_field["result"]["errors"]


def test_aud10a_fts5_fence_failure_indexing_and_stale_superseded_filtering(isolated_db: Path):
    """AUD-10a: Fence-failed epistemic items are indexed in `project_epistemic_fts`, and
    `WorkingMemoryRetriever.search_project_epistemic_fts` excludes `stale` and `superseded` items by default.
    """
    project_id, run_id = _create_project_from_intake(
        "FTS5 Consistency Project",
        {"objective": "Verify FTS5 indexing on fence failure and default filtering of stale/superseded items."},
    )
    wm.register_task_contract(
        project_id,
        run_id=run_id,
        task_key="t_fts_fence",
        purpose="Test fence failure FTS indexing.",
        expected_output="out.json",
        bound_requirement_revision=1,
    )

    def _stale_work(_pkt: dict) -> dict:
        intake_mod.apply_user_steering_update(
            project_id,
            run_id=run_id,
            actor="user",
            action="steer",
            updated_constraints=["Constraint updated mid-flight"],
            reason="Force fence failure",
        )
        return {
            "statement": "Unaccepted telemetry observation QUASAR_FENCE_TOKEN_991 from stale worker.",
        }

    with pytest.raises(wm.StaleWorkerResultError):
        wm.execute_checkpointed_work_boundary(project_id, "t_fts_fence", _stale_work)

    # Record a live active epistemic item and a stale epistemic item
    active_item = wm.record_epistemic_item(
        project_id,
        epistemic_category="source_observation",
        statement="Active verified observation QUASAR_ACTIVE_TOKEN_772.",
        verification_status="observed",
        run_id=run_id,
        task_key="t_fts_fence",
    )
    stale_item = wm.record_epistemic_item(
        project_id,
        epistemic_category="source_observation",
        statement="Soon-to-be-stale observation QUASAR_STALE_TOKEN_883.",
        verification_status="observed",
        run_id=run_id,
        task_key="t_fts_fence",
    )
    wm.invalidate_dependency_closure(
        project_id,
        changed_task_keys=["t_fts_fence"],
        reason="Invalidate t_fts_fence items",
    )
    # Restore active_item to observed so we have 1 active, 1 stale, and 1 superseded (from fence failure)
    conn = get_db_connection()
    try:
        conn.execute(
            "UPDATE project_epistemic_items SET verification_status = 'observed' WHERE id = ?",
            (active_item["id"],),
        )
        conn.commit()
        fts_fence_row = conn.execute(
            "SELECT * FROM project_epistemic_fts WHERE project_id = ? AND statement LIKE '%QUASAR_FENCE_TOKEN_991%'",
            (project_id,),
        ).fetchone()
        assert fts_fence_row is not None, "Fence-failed superseded item must be indexed in project_epistemic_fts"
    finally:
        conn.close()

    # Default search (`include_historical=False`) must return ONLY the active item, excluding stale & superseded
    default_search = wm.WorkingMemoryRetriever.search_project_epistemic_fts(
        project_id,
        "QUASAR_FENCE_TOKEN_991 QUASAR_ACTIVE_TOKEN_772 QUASAR_STALE_TOKEN_883",
    )
    returned_ids = {it["id"] for it in default_search["items"]}
    assert active_item["id"] in returned_ids
    assert stale_item["id"] not in returned_ids
    assert all(it["verification_status"] not in ("stale", "superseded") for it in default_search["items"])

    # Historical search (`include_historical=True`) must include the stale and fence-failed superseded items
    hist_search = wm.WorkingMemoryRetriever.search_project_epistemic_fts(
        project_id,
        "QUASAR_FENCE_TOKEN_991 QUASAR_ACTIVE_TOKEN_772 QUASAR_STALE_TOKEN_883",
        include_historical=True,
    )
    hist_statements = " ".join(it["statement"] for it in hist_search["items"])
    assert "QUASAR_FENCE_TOKEN_991" in hist_statements
    assert "QUASAR_STALE_TOKEN_883" in hist_statements
    assert "QUASAR_ACTIVE_TOKEN_772" in hist_statements

