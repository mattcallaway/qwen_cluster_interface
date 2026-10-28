"""Comprehensive regression test suite for RC4: Objective-Aware Autonomous Project Execution.

Verifies:
1. Domain-agnostic DeliverableRequirementContract decomposition.
2. Objective-aware research query generation & category mapping.
3. Evidence coverage assessment & readiness classification (SUFFICIENT, PARTIAL, INSUFFICIENT, BLOCKED).
4. Level 3 Substantive Objective Fulfillment Evaluation.
5. Sonoma Regression: Prevents false completion when execution succeeds but evidence is off-topic.
6. Honest gap reporting in synthesized deliverables without hallucination.
7. General-purpose multi-domain adaptability (non-Sonoma domain verification).
"""
import json
from certification_fixtures import recorded_native_outputs
import pytest
from typing import Any, Dict, List

from app.database import db_transaction, get_db_connection
from app.services.orchestration import (
    approval_manager,
    artifact_manager,
    contract,
    engine,
    evaluator,
    planner,
    states,
    task_graph,
)
from app.services.orchestration.contract import (
    DeliverableRequirementContract,
    decompose_deliverable_requirements,
)
from app.services.research.coverage import assess_evidence_coverage
from app.services.research.planner import ResearchPlanner
from app.services.research.synthesis import ResearchSynthesizer


def test_deliverable_contract_decomposition():
    """Verify contracts identify purpose, categories, evidence types, and acceptance criteria generically."""
    intake = {
        "objective": "Research funding programs and grant opportunities for environmental protection in Sonoma County.",
        "success_criteria": "Preserve all deliverables. Scheduler-mediated inference.",
    }
    
    # 1. Inventory deliverable
    c_inv = decompose_deliverable_requirements(intake, "Funding Opportunities Inventory", "funding_inventory")
    assert c_inv.key == "funding_inventory"
    assert "program_names_and_funders" in c_inv.required_info_categories
    assert "award_amounts_and_funding_scale" in c_inv.required_info_categories
    assert "primary_official" in c_inv.necessary_evidence_types
    assert c_inv.measurable_acceptance_criteria["require_citations"] is True

    # 2. Strategy deliverable
    c_strat = decompose_deliverable_requirements(intake, "Strategic Funding Priorities Brief", "strategic_brief")
    assert "strategic_opportunities_evaluated" in c_strat.required_info_categories
    assert "inference" in c_strat.required_execution_capabilities

    # 3. Calendar deliverable
    c_cal = decompose_deliverable_requirements(intake, "2027 Funding and Grant Calendar", "funding_calendar")
    assert any("deadline" in cat or "milestone" in cat for cat in c_cal.required_info_categories)

    # 4. Partnership deliverable
    c_part = decompose_deliverable_requirements(intake, "Partnership Development Strategy", "partnership_strategy")
    assert "potential_partner_organizations" in c_part.required_info_categories


def test_research_planner_generates_deliverable_targeted_queries():
    """Verify research planner generates queries mapped to deliverable evidence needs without hardcoding."""
    r_planner = ResearchPlanner()
    intake = {
        "objective": "Research grant opportunities and philanthropic funding for Sonoma County conservation.",
        "deliverables": [
            {"name": "Grant Opportunities Inventory"},
            {"name": "Conservation Partnership Strategy"},
        ]
    }
    
    contracts = [
        decompose_deliverable_requirements(intake, d["name"]).to_dict()
        for d in intake["deliverables"]
    ]
    strategy = r_planner.generate_strategy(
        objective=intake["objective"],
        deliverable_contracts=contracts,
    )
    
    assert strategy is not None
    # Check that search queries incorporate subject keywords and deliverable requirements
    all_queries = [q.lower() for q in strategy.initial_queries]
    assert len(all_queries) >= 3
    # At least one query should target grants/opportunities/funding
    assert any("grant" in q or "fund" in q or "opportunit" in q for q in all_queries)
    # At least one query should target Sonoma/conservation
    assert any("sonoma" in q or "conservation" in q for q in all_queries)


def test_evidence_coverage_assessment_flags_off_topic_sources():
    """Verify coverage assessment detects that fiscal policy documents are INSUFFICIENT for funding opportunities."""
    c_inv = DeliverableRequirementContract(
        key="funding_inventory",
        name="Funding Opportunities Inventory",
        intended_purpose="Catalog verified funding opportunities and grant programs.",
        required_info_categories=[
            "program_names_and_funders",
            "eligibility_and_scope",
            "award_amounts_and_funding_scale",
            "application_process_and_deadlines",
        ],
        required_output_structure=["Matrix", "Eligibility Profiles"],
        necessary_evidence_types=["primary_official"],
        required_execution_capabilities=["controller"],
        measurable_acceptance_criteria={"require_citations": True},
        topic_keywords=["funding", "grant", "opportunities", "sonoma", "environmental", "conservation"],
    )

    # 3 off-topic fiscal policy documents (representing the RC3 regression evidence)
    sources = [
        {
            "source_id": "src-1",
            "title": "Sonoma County Fiscal Policy - Financial Policies 2024-2025",
            "domain": "sonomacounty.ca.gov",
            "canonical_url": "https://sonomacounty.ca.gov/fiscal-policy",
            "clean_text": "The Board of Supervisors establishes financial policies to govern budget reserves and capital asset investments.",
            "quality_score": 0.9,
        },
        {
            "source_id": "src-2",
            "title": "County of Sonoma FY 2023-24 Adopted Budget",
            "domain": "sonomacounty.ca.gov",
            "canonical_url": "https://sonomacounty.ca.gov/adopted-budget",
            "clean_text": "The adopted budget outlines departmental allocations and internal operating expenditures across county departments.",
            "quality_score": 0.85,
        },
        {
            "source_id": "src-3",
            "title": "US Treasury Coronavirus State and Local Fiscal Recovery Funds Compliance Report",
            "domain": "treasury.gov",
            "canonical_url": "https://treasury.gov/compliance-report",
            "clean_text": "State and local fiscal recovery funds compliance and reporting guidance under the American Rescue Plan Act.",
            "quality_score": 0.95,
        },
    ]

    claims = [
        {"claim_id": "clm-1", "claim_text": "The Board establishes financial policies to govern budget reserves.", "status": "corroborated"},
        {"claim_id": "clm-2", "claim_text": "The adopted budget outlines departmental allocations.", "status": "corroborated"},
    ]

    assessment = assess_evidence_coverage(
        contracts=[c_inv],
        sources=sources,
        claims=claims,
        project_objective="Research funding programs and grant opportunities for environmental nonprofits.",
    )

    assert assessment["overall_status"] in ("INSUFFICIENT", "PARTIAL")
    deliv_assessments = assessment["deliverable_assessments"]
    deliv_ass = deliv_assessments[0] if isinstance(deliv_assessments, list) else deliv_assessments.get("funding_inventory")
    assert deliv_ass["readiness_for_synthesis"] in ("INSUFFICIENT", "PARTIAL")
    assert deliv_ass["evidence_relevance_score"] < 0.40
    # Missing categories must be explicitly identified
    assert len(deliv_ass["missing_evidence"]) > 0
    assert any("program" in m or "award" in m or "application" in m for m in deliv_ass["missing_evidence"])


def test_sonoma_regression_unfulfilled_objective_blocks_completion():
    """Verify that when tasks succeed but evidence is off-topic, project objective fulfillment rejects completion."""
    project_id = "prj-test-sonoma-reg"
    run_id = "run-test-sonoma-reg"
    
    intake = {
        "objective": "Produce a practical funding and partnership strategy for Sonoma County environmental nonprofits.",
        "success_criteria": (
            "1. Preserve all five deliverables.\n"
            "2. Independent meaningful plan.\n"
            "3. Respect human approval gate.\n"
            "4. Genuine live web research.\n"
            "5. Source grounding.\n"
            "6. Scheduler-mediated inference."
        ),
    }

    plan = {
        "deliverables": [
            {"key": "funding_inventory", "name": "Funding Opportunities Inventory", "terminal": True, "produced_by": "t_inv"},
            {"key": "strategic_brief", "name": "Strategic Funding Priorities Brief", "terminal": True, "produced_by": "t_strat"},
            {"key": "grant_calendar", "name": "2027 Funding and Grant Calendar", "terminal": True, "produced_by": "t_cal"},
            {"key": "partnership_strategy", "name": "Partnership Development Strategy", "terminal": True, "produced_by": "t_part"},
            {"key": "research_ledger", "name": "Research and Verification Ledger", "terminal": True, "produced_by": "t_ledg"},
        ]
    }

    # Simulate Level 2 deliverable evaluations (all technical tasks passed, but off-topic)
    deliverables_tested = [
        {"key": d["key"], "name": d["name"], "status": "accepted", "criteria_tested": {}, "evidence_satisfied": [{"id": "art-1"}]}
        for d in plan["deliverables"]
    ]

    # Coverage assessment indicating insufficient evidence for environmental funding
    coverage_assessment = {
        "overall_status": "INSUFFICIENT",
        "deliverable_assessments": {
            d["key"]: {
                "readiness_for_synthesis": "INSUFFICIENT",
                "evidence_relevance_score": 0.15,
                "missing_evidence": ["program_names_and_funders", "award_amounts_and_funding_scale", "application_deadlines"],
            }
            for d in plan["deliverables"]
        }
    }

    # Model inference did NOT execute (simulating the RC3 controller-only run)
    run = {
        "id": run_id,
        "project_id": project_id,
        "state": "EVALUATING",
        "intake_json": json.dumps(intake),
        "analysis_json": json.dumps({}),
    }

    project_id, run, deliverables_tested = recorded_native_outputs(plan, intake)
    result = evaluator.evaluate_project_objective_fulfillment(
        project_id=project_id,
        run=run,
        plan=plan,
        intake=intake,
        coverage_assessment=coverage_assessment,
        deliverables_tested=deliverables_tested,
    )

    # 1. Technical execution passed
    assert result["technical_execution_success"] is True
    assert result["deliverable_acceptance_success"] is True

    # 2. Substantive objective was NOT fulfilled!
    assert result["substantively_fulfilled"] is False
    assert result["substantive_status"] in ("UNFULFILLED", "INSUFFICIENT", "BLOCKED")
    assert result["fulfillment_score"] < 0.40

    # 3. Disclosed critical unfulfilled criteria
    assert any("inference" in u.lower() for u in result["unfulfilled_criteria"])
    assert len(result["critical_gaps"]) >= 4


def test_coverage_cannot_waive_trusted_evidence_hold():
    """A coverage label cannot certify research even with recorded native outputs."""
    intake = {
        "objective": "Research clean energy grant opportunities for public institutions.",
        "success_criteria": "Preserve deliverable. Grounded in verified sources.",
    }

    plan = {
        "deliverables": [
            {"key": "energy_grants", "name": "Clean Energy Grants", "terminal": True, "produced_by": "t_eg"}
        ]
    }

    deliverables_tested = [
        {"key": "energy_grants", "name": "Clean Energy Grants", "status": "accepted", "criteria_tested": {}, "evidence_satisfied": [{"id": "art-1"}]}
    ]

    coverage_assessment = {
        "overall_status": "SUFFICIENT",
        "deliverable_assessments": {
            "energy_grants": {
                "readiness_for_synthesis": "SUFFICIENT",
                "evidence_relevance_score": 0.92,
                "missing_evidence": [],
            }
        }
    }

    run = {
        "id": "run-test-sufficient",
        "project_id": "prj-test-sufficient",
        "state": "EVALUATING",
        "intake_json": json.dumps(intake),
        "analysis_json": json.dumps({}),
    }

    project_id, run, deliverables_tested = recorded_native_outputs(plan, intake, inference=True)
    result = evaluator.evaluate_project_objective_fulfillment(
        project_id=project_id,
        run=run,
        plan=plan,
        intake=intake,
        coverage_assessment=coverage_assessment,
        deliverables_tested=deliverables_tested,
    )

    assert result["technical_execution_success"] is True
    assert result["substantively_fulfilled"] is False
    assert result["substantive_status"] == "BLOCKED"
    assert result["fulfillment_score"] == 0.0
    assert any("claim-evidence" in reason for reason in result["unfulfilled_criteria"])


def test_synthesis_outputs_honest_evidence_gaps():
    """Verify synthesized deliverables explicitly include evidence sufficiency and unverified gaps."""
    synth = ResearchSynthesizer()
    c_inv = DeliverableRequirementContract(
        key="funding_inventory",
        name="Funding Opportunities Inventory",
        intended_purpose="Catalog verified funding programs for Sonoma County nonprofits.",
        required_info_categories=["program_names_and_funders", "eligibility_and_scope", "application_deadlines"],
        required_output_structure=["Matrix", "Profiles"],
        necessary_evidence_types=["primary_official"],
        required_execution_capabilities=["controller"],
        measurable_acceptance_criteria={"require_citations": True},
    )

    cov_assessment = {
        "overall_status": "INSUFFICIENT",
        "deliverable_assessments": {
            "funding_inventory": {
                "readiness_for_synthesis": "INSUFFICIENT — FURTHER RESEARCH REQUIRED",
                "evidence_relevance_score": 0.22,
                "satisfied_categories": [],
                "missing_evidence": ["program_names_and_funders", "application_deadlines"],
                "research_confidence": 0.25,
            }
        }
    }

    sources = [
        {
            "source_id": "src-budget",
            "title": "Sonoma County Fiscal Policy",
            "publisher": "County of Sonoma",
            "canonical_url": "https://sonomacounty.ca.gov/fiscal-policy",
            "extracted_text": "The Board establishes general municipal budget policies and funding allocation procedures.",
            "quality_score": 0.9,
        }
    ]

    report = synth.synthesize_report(
        project_id="prj-test-synth",
        topic="Funding Opportunities Inventory",
        objective="Research environmental grant opportunities in Sonoma County.",
        sources=sources,
        deliverable_contract=c_inv,
        coverage_assessment=cov_assessment,
    )

    assert "## Evidence Sufficiency & Information Coverage Assessment" in report
    assert "INSUFFICIENT — FURTHER RESEARCH REQUIRED" in report
    assert "program_names_and_funders" in report
    assert "application_deadlines" in report
    assert "## 2. Authoritative Source Catalog" in report or "## Provenance" in report or "References" in report


def test_end_to_end_engine_transitions_to_blocked_on_unmet_objective():
    """Verify engine transitions run to BLOCKED and prevents project completed status when objective is unfulfilled."""
    project_id = f"prj-block-{id(test_end_to_end_engine_transitions_to_blocked_on_unmet_objective)}"
    run_id = f"run-block-{id(test_end_to_end_engine_transitions_to_blocked_on_unmet_objective)}"
    did = f"del-block-{id(test_end_to_end_engine_transitions_to_blocked_on_unmet_objective)}"

    plan = {
        "deliverables": [
            {
                "key": "deliv_inventory",
                "name": "Funding Opportunities Inventory",
                "deliverable_type": "report",
                "produced_by": "task_synth",
                "terminal": True,
                "acceptance_criteria": "Supported by verified evidence."
            }
        ],
        "tasks": [
            {"key": "task_synth", "action": {"type": "controller_step", "step": "research_synthesize"}}
        ]
    }

    intake = {
        "objective": "Research environmental grants in Sonoma County.",
        "success_criteria": "1. Preserve deliverables. 2. Scheduler-mediated inference.",
    }

    from app.services import projects as P
    proj = P.create_project("Test Project Block", "tester", objective=intake["objective"], success_criteria=intake["success_criteria"])
    project_id = proj["id"]

    with db_transaction() as c:
        c.execute(
            "INSERT INTO orchestration_runs (id, project_id, state, created_by, version, autonomy_level, intake_json, analysis_json, created_at, updated_at) VALUES (?, ?, 'EVALUATING', 'tester', 1, 2, ?, ?, '2026-10-23T00:00:00Z', '2026-10-23T00:00:00Z')",
            (run_id, project_id, json.dumps(intake), json.dumps({"deliverable_map": {"deliv_inventory": did}}))
        )
        c.execute(
            "INSERT INTO orchestration_plan_revisions (id, run_id, revision_number, status, source, plan_json, created_by, created_at) VALUES ('rev-blk-1', ?, 1, 'approved', 'template', ?, 'tester', '2026-10-23T00:00:00Z')",
            (run_id, json.dumps(plan))
        )
        c.execute(
            "INSERT INTO orchestration_tasks (id, run_id, revision_id, plan_key, title, agent_role, state, attempts, max_attempts, action_json, created_at, updated_at) VALUES ('ot-blk-1', ?, 'rev-blk-1', 'task_synth', 'Synthesize', 'integration', 'accepted', 1, 1, '{}', '2026-10-23T00:00:00Z', '2026-10-23T00:00:00Z')",
            (run_id,)
        )
        c.execute(
            "INSERT INTO orchestration_attempts (id, orch_task_id, attempt_number, started_at, outcome) VALUES ('att-blk-1', 'ot-blk-1', 1, '2026-10-23T00:00:00Z', 'accepted')"
        )
        c.execute(
            "INSERT INTO deliverables (id, project_id, name, deliverable_type, status, created_by, created_at, updated_at) VALUES (?, ?, 'Funding Opportunities Inventory', 'report', 'planned', 'tester', '2026-10-23T00:00:00Z', '2026-10-23T00:00:00Z')",
            (did, project_id)
        )

    # Write report artifact that passes citation audit
    run_obj = engine.get_run(run_id)
    aid_rep = artifact_manager.write_controller_artifact(run_obj, "ot-blk-1", "deliv_inventory.md", b"# Inventory\nContent")
    artifact_manager.set_validation(aid_rep, "valid", "Citation audit passed")

    # Write evidence coverage assessment showing INSUFFICIENT evidence
    cov_data = {
        "overall_status": "INSUFFICIENT",
        "deliverable_assessments": {
            "deliv_inventory": {
                "readiness_for_synthesis": "INSUFFICIENT",
                "evidence_relevance_score": 0.18,
                "missing_evidence": ["program_names_and_funders"],
            }
        }
    }
    aid_cov = artifact_manager.write_controller_artifact(run_obj, "ot-blk-1", "evidence_sufficiency_assessment.json", json.dumps(cov_data).encode("utf-8"))
    artifact_manager.set_validation(aid_cov, "valid", "Assessment recorded")

    # Trigger evaluation
    engine._evaluate_deliverables(engine.get_run(run_id))

    fresh_run = engine.get_run(run_id)
    # 1. Run must NOT be COMPLETED! It must be BLOCKED!
    assert fresh_run["state"] == "BLOCKED"

    # 2. Project status must remain 'active' (not 'completed')
    conn = get_db_connection()
    try:
        p_row = conn.execute("SELECT status FROM projects WHERE id=?", (project_id,)).fetchone()
        assert p_row["status"] != "completed"
        assert p_row["status"] in ("draft", "active")
    finally:
        conn.close()

    # 3. Substantive evaluation recorded in analysis_json
    meta = json.loads(fresh_run["analysis_json"])
    assert "substantive_evaluation" in meta
    sub = meta["substantive_evaluation"]
    assert sub["substantively_fulfilled"] is False
    assert sub["substantive_status"] == "BLOCKED"
    assert sub["evaluation_scope"] == "deliverable_precheck"
    assert sub["objective_evaluation_performed"] is False
    assert sub["reason_code"] == "DELIVERABLE_EVIDENCE_BLOCKED"
    assert sub["checks"]
    assert meta["deliverable_map"]["deliv_inventory"] == did


def test_blocked_system_preserves_partial_work_and_requests_human_direction():
    """Verify that when stopped honestly, partial work and artifacts remain preserved and operator review is requested."""
    project_id = f"prj-part-{id(test_blocked_system_preserves_partial_work_and_requests_human_direction)}"
    run_id = f"run-part-{id(test_blocked_system_preserves_partial_work_and_requests_human_direction)}"
    
    intake = {"objective": "Research clean hydrogen grants.", "success_criteria": "Preserve deliverable."}
    plan = {"deliverables": [{"key": "h2_grants", "name": "H2 Grants", "terminal": True, "produced_by": "t1"}]}

    deliverables_tested = [{"key": "h2_grants", "name": "H2 Grants", "status": "accepted", "criteria_tested": {}, "evidence_satisfied": []}]
    coverage_assessment = {
        "overall_status": "BLOCKED",
        "deliverable_assessments": {
            "h2_grants": {
                "readiness_for_synthesis": "BLOCKED",
                "evidence_relevance_score": 0.05,
                "missing_evidence": ["official_program_guidance"],
            }
        }
    }

    run = {
        "id": run_id,
        "project_id": project_id,
        "state": "EVALUATING",
        "intake_json": json.dumps(intake),
        "analysis_json": json.dumps({}),
    }

    res = evaluator.evaluate_project_objective_fulfillment(
        project_id=project_id,
        run=run,
        plan=plan,
        intake=intake,
        coverage_assessment=coverage_assessment,
        deliverables_tested=deliverables_tested,
    )

    assert res["substantively_fulfilled"] is False
    assert res["substantive_status"] == "BLOCKED"
    assert "Operator review required" in res["recommended_action"]


def test_general_purpose_healthcare_compliance_domain():
    """Verify that contract decomposition and coverage assessment work generically on a healthcare domain."""
    intake = {
        "objective": "Research HIPAA Security Rule standards and clinical telemetry privacy requirements.",
        "success_criteria": "Preserve deliverables. Ensure cryptographic evidence grounding.",
    }

    # 1. Standards Catalog
    c_std = decompose_deliverable_requirements(intake, "HIPAA Security Standards Inventory", "hipaa_inventory")
    assert c_std.key == "hipaa_inventory"
    assert "program_names_and_funders" in c_std.required_info_categories or "eligibility_and_scope" in c_std.required_info_categories

    # 2. Risk Assessment Matrix
    c_risk = decompose_deliverable_requirements(intake, "Clinical Telemetry Risk Matrix", "risk_matrix")
    assert "threat_vectors_and_vulnerabilities" in c_risk.required_info_categories or "severity_and_impact_ratings" in c_risk.required_info_categories
    assert "Risk & Threat Matrix" in c_risk.required_output_structure[1] or "Matrix" in c_risk.required_output_structure[1]

    # Synthesize test report with healthcare domain source
    synth = ResearchSynthesizer()
    med_source = {
        "source_id": "src-hhs",
        "title": "HHS OCR HIPAA Security Rule Guidance",
        "publisher": "U.S. Department of Health and Human Services",
        "canonical_url": "https://hhs.gov/hipaa-guidance",
        "extracted_text": "Covered entities must implement physical and technical safeguards to maintain the integrity of electronic protected health information.",
        "quality_score": 0.95,
    }
    rep = synth.synthesize_report(
        project_id="prj-med",
        topic="Clinical Telemetry Risk Matrix",
        objective=intake["objective"],
        sources=[med_source],
        deliverable_contract=c_risk,
    )
    assert "Risk" in rep or "Telemetr" in rep
    assert "https://hhs.gov/hipaa-guidance" in rep or "HHS" in rep
