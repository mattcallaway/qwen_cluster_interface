"""
Unit and regression tests for General-Purpose Autonomous Project Recovery.
Tests:
Phase 1: Zero cross-domain contamination, specialized deliverable synthesis.
Phase 2: Model context assembly, input/output relevance, and prompt boundaries.
Phase 3: Citation parsing, year disambiguation ([2026]), strict contradiction detection, bounded repair.
Phase 4: Deliverable-independent failure isolation in orchestrator execution.
Phase 6: Artifact reuse and lineage integrity (no self-loops).
"""

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

from app.database import get_db_connection, init_db
from app.services.artifacts.service import ArtifactService
from app.services.orchestration.context_manager import build_inference_prompt
from app.services.orchestration.evaluator import evaluate_inference
from app.services.research.citations import CitationEngine
from app.services.research.entailment import verify_claim_against_source
from app.services.research.repair import EvidenceRepairEngine
from app.services.research.synthesis import ResearchSynthesizer


# Sample mock sources for testing
CYBERSECURITY_SOURCES = [
    {
        "id": "src-cisa-01",
        "title": "Cross-Sector Cybersecurity Performance Goals (CPGs) [2026] Update",
        "publisher": "Cybersecurity and Infrastructure Security Agency (CISA)",
        "canonical_url": "https://www.cisa.gov/cpgs",
        "clean_text": (
            "The Cybersecurity and Infrastructure Security Agency released baseline goals to reduce cyber risk. "
            "Universal multi-factor authentication (MFA) substantially reduces unauthorized credential access. "
            "Organizations should implement basic phishing-resistant authentication controls for administrative accounts."
        ),
        "quality_score": 0.95,
        "retrieved_at": "2026-10-22T10:00:00Z",
    },
    {
        "id": "src-nist-02",
        "title": "NIST Small Business Cybersecurity Corner",
        "publisher": "National Institute of Standards and Technology (NIST)",
        "canonical_url": "https://www.nist.gov/cybersecurity/smallbusiness",
        "clean_text": (
            "Small businesses and non-profit organizations face severe operational risks from unpatched software vulnerabilities. "
            "Automated patching of operating systems and application software mitigates common remote exploit vectors. "
            "Maintaining secure offline backups ensures resilience against ransomware attacks."
        ),
        "quality_score": 0.90,
        "retrieved_at": "2026-10-22T10:00:00Z",
    },
    {
        "id": "src-ftc-03",
        "title": "FTC Cybersecurity Basics for Small Organizations",
        "publisher": "Federal Trade Commission (FTC)",
        "canonical_url": "https://www.ftc.gov/business-guidance/small-business/cybersecurity",
        "clean_text": (
            "Staff and volunteer security awareness training is an essential organizational defense against email phishing. "
            "Immediate incident response notification protocols prevent widespread unauthorized data exfiltration. "
            "Restricting administrative privileges adheres to the principle of least privilege."
        ),
        "quality_score": 0.88,
        "retrieved_at": "2026-10-22T10:00:00Z",
    },
    {
        "id": "src-nonprofit-04",
        "title": "Nonprofit Cyber Readiness Toolkit",
        "publisher": "CyberPeace Institute",
        "canonical_url": "https://cyberpeaceinstitute.org/nonprofit-toolkit",
        "clean_text": (
            "Resource-constrained non-profit organizations must prioritize high-impact security measures within 90 days. "
            "Establishing a 90-day phased roadmap enables incremental adoption of basic security safeguards without operational paralysis. "
            "Clear donor and beneficiary privacy policies establish trust and ensure regulatory compliance."
        ),
        "quality_score": 0.85,
        "retrieved_at": "2026-10-22T10:00:00Z",
    },
]

FOOD_BANK_SOURCES = [
    {
        "id": "src-fb-01",
        "title": "Volunteer Recruitment Best Practices for Community Food Banks [2025]",
        "publisher": "Feeding America Research",
        "canonical_url": "https://www.feedingamerica.org/research/volunteers",
        "clean_text": (
            "Food bank volunteer programs require structured onboarding and flexible scheduling to maintain retention. "
            "Providing clear role descriptions and recognition programs increases volunteer retention rates by 35 percent. "
            "Community food distribution relies heavily on reliable volunteer scheduling and coordination."
        ),
        "quality_score": 0.90,
        "retrieved_at": "2026-10-22T10:00:00Z",
    },
    {
        "id": "src-fb-02",
        "title": "Community Engagement and Outreach Strategies",
        "publisher": "Nonprofit Quarterly",
        "canonical_url": "https://nonprofitquarterly.org/volunteer-outreach",
        "clean_text": (
            "Targeted outreach through local civic organizations and community colleges expands volunteer recruitment pools. "
            "Digital volunteer management software streamlines shift scheduling and orientation communications."
        ),
        "quality_score": 0.85,
        "retrieved_at": "2026-10-22T10:00:00Z",
    },
]

SOLAR_SOURCES = [
    {
        "id": "src-solar-01",
        "title": "Community Solar Program Design Guide",
        "publisher": "National Renewable Energy Laboratory (NREL)",
        "canonical_url": "https://www.nrel.gov/solar/community-programs",
        "clean_text": (
            "Community solar programs allow local residents and renters to access solar energy without installing rooftop panels. "
            "Virtual net metering frameworks are essential for equitable subscriber bill credits across municipal utilities. "
            "Upfront interconnection costs and grid hosting capacity represent primary feasibility constraints."
        ),
        "quality_score": 0.95,
        "retrieved_at": "2026-10-22T10:00:00Z",
    },
    {
        "id": "src-solar-02",
        "title": "State Community Solar Policy and Regulatory Frameworks",
        "publisher": "Clean Energy States Alliance",
        "canonical_url": "https://www.cesa.org/projects/community-solar",
        "clean_text": (
            "State legislation establishing community solar carve-outs drives local renewable project financing. "
            "Low-income subscriber participation targets ensure energy equity across community solar deployments."
        ),
        "quality_score": 0.90,
        "retrieved_at": "2026-10-22T10:00:00Z",
    },
]


# ==============================================================================
# Phase 1: Cross-Domain Contamination Tests
# ==============================================================================

def test_synthesis_no_water_contamination_cybersecurity():
    """Verify all 6 cybersecurity deliverables have ZERO water/dam/potter valley/Ollama boilerplate."""
    synth = ResearchSynthesizer()
    obj = "Create an actionable cybersecurity baseline and governance toolkit for a small community nonprofit organization"

    deliverable_topics = [
        "Executive Cybersecurity Briefing",
        "Cybersecurity Risk Assessment Matrix",
        "Organizational Data Protection Policy",
        "90-Day Security Improvement Roadmap",
        "Staff and Volunteer Security Quick-Reference Guide",
        "Research Sources & Verification Ledger",
    ]

    forbidden_terms = [
        "potter valley", "scott dam", "ferc surrender", "van arsdale",
        "russian river", "eel river", "hydrologic variability", "water-rights",
        "anadromous", "salmonid", "storage impoundments", "ollama.host", "4 gb ram",
    ]

    for topic in deliverable_topics:
        md = synth.synthesize_report(
            project_id="test-p1",
            topic=topic,
            objective=obj,
            sources=CYBERSECURITY_SOURCES,
        )
        assert md, f"Deliverable {topic} produced empty markdown"
        md_lower = md.lower()

        # Check for forbidden domain contamination
        for term in forbidden_terms:
            assert term not in md_lower, f"Deliverable '{topic}' contains contaminated boilerplate: '{term}'"

        # Check that citations exist
        engine = CitationEngine()
        indices = engine.extract_citation_indices(md, max_valid_index=len(CYBERSECURITY_SOURCES))
        assert len(indices) >= 2, f"Deliverable '{topic}' contains insufficient citations: {indices}"


def test_synthesis_cross_domain_project_a_food_bank():
    """Verify Project A (Food Bank Strategy) produces domain-relevant deliverables with zero contamination."""
    synth = ResearchSynthesizer()
    obj = "Develop a comprehensive volunteer recruitment and retention strategy for a regional food bank"

    topics = [
        "Volunteer Recruitment and Retention Research Brief",
        "Volunteer Program Evidence Table",
        "Volunteer Outreach and Engagement Plan",
        "90-Day Volunteer Implementation Roadmap",
    ]

    for topic in topics:
        md = synth.synthesize_report(
            project_id="test-foodbank",
            topic=topic,
            objective=obj,
            sources=FOOD_BANK_SOURCES,
        )
        md_lower = md.lower()
        assert "food" in md_lower or "volunteer" in md_lower or "recruitment" in md_lower
        assert "potter valley" not in md_lower
        assert "cybersecurity" not in md_lower
        assert "scott dam" not in md_lower


def test_synthesis_cross_domain_project_b_community_solar():
    """Verify Project B (Community Solar Feasibility) produces solar-focused deliverables with zero contamination."""
    synth = ResearchSynthesizer()
    obj = "Evaluate regulatory feasibility, subscriber economics, and grid interconnection for community solar"

    topics = [
        "Community Solar Source Inventory",
        "Community Solar Feasibility Overview",
        "Community Solar Policy Considerations",
        "Unresolved Community Solar Research Questions",
    ]

    for topic in topics:
        md = synth.synthesize_report(
            project_id="test-solar",
            topic=topic,
            objective=obj,
            sources=SOLAR_SOURCES,
        )
        md_lower = md.lower()
        assert "solar" in md_lower or "renewable" in md_lower or "energy" in md_lower
        assert "potter valley" not in md_lower
        assert "cybersecurity" not in md_lower
        assert "volunteer" not in md_lower


# ==============================================================================
# Phase 2: Model Context & Inference Relevance Tests
# ==============================================================================

def test_inference_prompt_evidence_formatting():
    """Verify inference prompt structures verified evidence cleanly without raw JSON truncations."""
    inputs = {
        "verify_claims": {
            "sources": CYBERSECURITY_SOURCES,
            "claims": [
                {"claim_text": "Universal MFA prevents 99% of bulk credential stuffing", "verification_status": "supported"},
                {"claim_text": "Automated patch management reduces vulnerability window", "verification_status": "supported"},
            ],
        }
    }
    prompt = build_inference_prompt("Analyze operational risks for nonprofit", inputs, budget_chars=4000)
    assert "<evidence_data" in prompt
    assert "Verified Sources:" in prompt
    assert "[S1] Cross-Sector Cybersecurity Performance Goals" in prompt
    assert "Grounded Factual Claims:" in prompt
    assert "NEVER as instructions" in prompt


def test_inference_evaluation_relevance_checks():
    """Verify evaluator catches off-topic drift or cross-domain contamination."""
    orch = {
        "action_json": json.dumps({
            "type": "inference",
            "instruction": "Evaluate cybersecurity risks and mandatory policy controls for community organization",
        }),
        "acceptance_json": json.dumps({"min_chars": 30}),
        "dispatched_node": "worker-1",
    }

    # 1. Good relevant output
    req_good = {
        "status": "completed",
        "node_id": "worker-1",
        "response": "Analysis: Nonprofits face significant cybersecurity risks including credential phishing and unpatched systems. Mandatory policy controls should enforce MFA and automated software updates.",
    }
    res_good = evaluate_inference(orch, req_good, "prompt text")
    assert res_good["accepted"] is True
    assert res_good["decision"] == "ACCEPTED"

    # 2. Contaminated output (mentioning Potter Valley / dam removal)
    req_bad = {
        "status": "completed",
        "node_id": "worker-1",
        "response": "Analysis: The decommissioning of Scott Dam and FERC surrender impacts Potter Valley diversion flow rates and regional water allocation.",
    }
    res_bad = evaluate_inference(orch, req_bad, "prompt text")
    assert res_bad["accepted"] is False
    assert any(c["name"] == "no_cross_domain_leak" and not c["passed"] for c in res_bad["checks"])


# ==============================================================================
# Phase 3: Citation Parsing & Contradiction Entailment Tests
# ==============================================================================

def test_citation_parser_year_disambiguation():
    """Verify [2026] in a title is NOT interpreted as a citation, but [1] is resolved."""
    engine = CitationEngine()
    text = "According to the Cybersecurity Spending Statistics [2026] report, organizations must enforce MFA [1]."
    indices = engine.extract_citation_indices(text, max_valid_index=4)
    assert 2026 not in indices, f"Calendar year [2026] was falsely parsed as a citation: {indices}"
    assert 1 in indices, f"Valid citation [1] was not extracted: {indices}"


def test_citation_validation_unknown_source_fails():
    """Verify that citing an unretrieved source number fails validation."""
    engine = CitationEngine()
    text = "Organizations must deploy offline backups [99].\n\n## References\n[1] Source 1"
    audit = engine.validate_citations("test-proj", text, sources=CYBERSECURITY_SOURCES)
    assert audit.is_valid is False
    assert 99 in audit.unresolved_citation_numbers


def test_entailment_polarity_and_numeric_contradiction():
    """Verify strict contradiction detection: polarity and numeric conflicts fail."""
    source_text = "Universal MFA reduces unauthorized credential access by 80% to 90% across monitored enterprise networks."

    # Direct support
    res1 = verify_claim_against_source("Universal MFA reduces unauthorized credential access.", source_text)
    assert res1.status == "SUPPORTED"

    # Polarity contradiction (increases risk vs reduces risk)
    res2 = verify_claim_against_source("Universal MFA increases unauthorized credential access.", source_text)
    assert res2.status == "CONTRADICTED"

    # Material numeric contradiction (saying 20% instead of 80-90%)
    res3 = verify_claim_against_source("Universal MFA reduces unauthorized credential access by only 20%.", source_text)
    assert res3.status == "CONTRADICTED"


def test_evidence_repair_engine_fixes_ungrounded_statement():
    """Verify bounded evidence repair can substitute grounded source passage."""
    repairer = EvidenceRepairEngine()
    bad_md = (
        "# Executive Brief\n\n"
        "Organizations face challenges. MFA completely eliminates every single security threat forever [1].\n\n"
        "## References\n[1] CISA Guide"
    )
    res = repairer.repair_deliverable("test-proj", bad_md, sources=CYBERSECURITY_SOURCES)
    assert res.repaired_markdown
    # The impossible claim was repaired using actual CISA text
    assert "completely eliminates every single security threat forever" not in res.repaired_markdown


# ==============================================================================
# Phase 6: Lineage & Reuse Integrity Tests
# ==============================================================================

def test_artifact_reuse_no_self_referential_lineage(tmp_path):
    """Verify that reusing an artifact by reference does NOT insert a self-loop in artifact_lineage."""
    db_file = tmp_path / "test_lineage.db"
    os.environ["DATABASE_PATH"] = str(db_file)
    init_db()

    from app.services.orchestration.engine import create_project_with_run
    p1 = create_project_with_run("Source Proj", "test-user", {"objective": "Source Objective"})
    p2 = create_project_with_run("Target Proj", "test-user", {"objective": "Target Objective"})
    p_src = p1["project"]
    r_src = p1["run"]
    p_tgt = p2["project"]
    r_tgt = p2["run"]

    art_svc = ArtifactService(storage_dir=tmp_path / "artifacts")
    created = art_svc.register_artifact(
        name="test_policy.md",
        data=b"# Test Policy Content",
        project_id=p_src["id"],
        run_id=r_src["id"],
        created_by="test-user",
    )
    art_id = created["id"]

    # Reuse artifact in target project by reference
    reuse_res = art_svc.reuse_artifact_in_project(
        artifact_id=art_id,
        project_id=p_tgt["id"],
        actor="operator",
        action_type="reference",
        action_desc="Reusing policy standard in target project",
        target_run_id=r_tgt["id"],
    )

    assert reuse_res["status"] in ("queued", "authorized")

    # Verify database: artifact_lineage MUST NOT have a row where source == target
    conn = get_db_connection()
    try:
        self_loops = conn.execute(
            "SELECT count(*) FROM artifact_lineage WHERE source_artifact_id = target_artifact_id"
        ).fetchone()[0]
        assert self_loops == 0, f"Found {self_loops} self-referential rows in artifact_lineage!"

        # Project link must exist
        link = conn.execute(
            "SELECT * FROM artifact_project_links WHERE artifact_id = ? AND project_id = ?",
            (art_id, p_tgt["id"]),
        ).fetchone()
        assert link is not None, "artifact_project_links entry missing for reused artifact"
    finally:
        conn.close()


def test_deliverable_failure_isolation(tmp_path):
    """Phase 4 Acceptance Test:
    Create a task graph with 6 independent deliverables sharing an approved research stage.
    Force one deliverable to fail citation validation.
    Verify that unrelated deliverables finish when permitted, while the project transitions
    to BLOCKED with partial completion recorded, never falsely COMPLETED."""
    from app.config import settings
    settings.data_dir = tmp_path
    init_db()

    from app.database import db_transaction, get_db_connection
    from app.services.orchestration import engine, planner, approval_manager
    from app.services.orchestration.engine import create_project_with_run, _orch_tasks
    from app.services.orchestration.common import jload

    res = create_project_with_run(
        "Nonprofit Security Isolation Test",
        "operator",
        {
            "objective": "Conduct web research and produce a nonprofit cybersecurity governance plan",
            "deliverables": "Executive Brief\nRisk Matrix\nPolicy Document\n90-Day Roadmap\nStaff Guide\nSource Ledger",
        },
        autonomy_level=2,
    )
    p = res["project"]
    r = res["run"]

    # Generate research plan with multi-deliverables and approve it
    plan_res = planner.generate_plan(r["id"], "operator", mode="template")
    assert plan_res is not None
    approval_manager.approve_plan(r["id"], "operator", "approved")

    # Start the run
    engine.start_run(r["id"], "operator")

    # Upstream research tasks succeed
    tasks = _orch_tasks(r["id"])
    assert len(tasks) >= 6

    with db_transaction() as conn:
        # Mark upstream research tasks as accepted
        conn.execute("UPDATE orchestration_tasks SET state='accepted' WHERE run_id=? AND plan_key IN ('search_sources', 'retrieve_content', 'verify_claims', 'model_research_analysis')", (r["id"],))

    # Identify deliverable tasks
    tasks = _orch_tasks(r["id"])
    deliv_tasks = [t for t in tasks if t["plan_key"].startswith("synthesize_")]
    assert len(deliv_tasks) == 6

    # Force the first deliverable to fail citation validation
    failed_task = deliv_tasks[0]
    with db_transaction() as conn:
        conn.execute(
            """UPDATE orchestration_tasks
            SET state='failed', failure_class='citation_validation_failed', last_error='Citation validation failed: [S99] unresolved'
            WHERE id=?""",
            (failed_task["id"],),
        )

    # Mark the remaining 5 independent deliverables as accepted
    for dt in deliv_tasks[1:]:
        with db_transaction() as conn:
            conn.execute("UPDATE orchestration_tasks SET state='accepted' WHERE id=?", (dt["id"],))

    # Trigger engine tick
    engine.tick(r["id"])

    # Verify run state is BLOCKED with partial deliverables preserved
    conn = get_db_connection()
    try:
        run_row = conn.execute("SELECT state, block_reason, analysis_json FROM orchestration_runs WHERE id=?", (r["id"],)).fetchone()
        assert run_row["state"] == "BLOCKED"
        assert "Independent deliverable failure isolation" in (run_row["block_reason"] or "")

        meta = jload(run_row["analysis_json"])
        assert "partial_completion" in meta
        pc = meta["partial_completion"]
        assert pc["failed_tasks_count"] == 1
        assert failed_task["plan_key"] in pc["failed_tasks"]
        # The remaining tasks were accepted and preserved
        assert pc["accepted_tasks_count"] >= 5

        # Verify failed task state and error are preserved in database
        t_fail = conn.execute("SELECT state, failure_class, last_error FROM orchestration_tasks WHERE id=?", (failed_task["id"],)).fetchone()
        assert t_fail["state"] == "failed"
        assert t_fail["failure_class"] == "citation_validation_failed"
        assert "unresolved" in t_fail["last_error"]
    finally:
        conn.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

