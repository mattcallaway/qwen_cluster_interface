"""Automated functional tests for the Autonomous Web Research Engine.

Covers search providers, document extraction, source deduplication,
evidence corroboration, deterministic citations, and orchestrator integration.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from app.database import init_db
from app.services.research.cache import normalize_canonical_url, compute_content_hash
from app.services.research.extraction import DocumentExtractor
from app.services.research.sources import SourceLibrary
from app.services.research.verification import EvidenceLedger
from app.services.research.citations import CitationEngine
from app.services.research.planner import ResearchPlanner, ResearchBudget
from app.services.research.providers.base import SearchResult, QueryResult
from app.services.research.search import SearchManager
from app.services.research.manager import ResearchManager
from app.services.orchestration import task_graph, planner as orch_planner


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path):
    init_db()


def test_url_normalization_and_content_hashing():
    url1 = "https://Example.ORG:443/article/?utm_source=twitter&utm_medium=social#heading"
    url2 = "https://example.org/article/"
    assert normalize_canonical_url(url1) == "https://example.org/article"
    assert normalize_canonical_url(url2) == "https://example.org/article"

    content = b"Sample text content for hashing verification."
    h1 = compute_content_hash(content)
    h2 = compute_content_hash(content)
    assert h1 == h2
    assert len(h1) == 64


def test_document_extraction_html():
    extractor = DocumentExtractor()
    sample_html = """
    <!DOCTYPE html>
    <html>
    <head><title>Test Renewable Energy Study</title></head>
    <body>
        <nav><a href="/home">Home</a><a href="/about">About</a></nav>
        <article>
            <h1>Solid-State Battery Progress</h1>
            <p>Solid-state batteries achieve 400 Wh/kg in recent lab benchmarks.</p>
            <table>
                <tr><th>Technology</th><th>Density</th></tr>
                <tr><td>Solid-State</td><td>400 Wh/kg</td></tr>
                <tr><td>Lithium-Ion</td><td>250 Wh/kg</td></tr>
            </table>
        </article>
        <footer>Copyright 2026 Energy Institute</footer>
    </body>
    </html>
    """
    doc = extractor.extract(sample_html.encode("utf-8"), content_type="text/html", base_url="https://energy.org/study")
    assert doc.title == "Test Renewable Energy Study"
    assert "Solid-State Battery Progress" in doc.text
    assert "400 Wh/kg" in doc.text
    # Nav and footer stripped
    assert "Home" not in doc.text
    assert "Copyright 2026" not in doc.text
    # Table extracted
    assert len(doc.tables) > 0
    assert "Technology" in doc.tables[0][0]


def test_source_library_deduplication_and_versioning(tmp_path):
    from app.services import projects as ps
    library = SourceLibrary(base_storage_dir=tmp_path / "sources")
    proj = ps.create_project("Source Lib Test Proj", owner="admin")
    project_id = proj["id"]

    # Ingest version 1
    content_v1 = b"First edition of the energy report."
    src1 = library.add_source(
        project_id=project_id,
        url="https://example.org/report",
        title="Energy Report V1",
        raw_content=content_v1,
        extracted_text=content_v1.decode("utf-8"),
    )
    assert src1["source_id"].startswith("src-")
    assert src1["version"] == 1

    # Ingest exact identical content -> deduplicated, returns same record
    src_dupe = library.add_source(
        project_id=project_id,
        url="https://example.org/report?utm_campaign=feed",
        title="Energy Report Duplicate",
        raw_content=content_v1,
        extracted_text=content_v1.decode("utf-8"),
    )
    assert src_dupe["source_id"] == src1["source_id"]
    assert src_dupe["version"] == 1

    # Ingest modified content -> creates version 2
    content_v2 = b"Second edition of the energy report with updated stats."
    src2 = library.add_source(
        project_id=project_id,
        url="https://example.org/report",
        title="Energy Report V2",
        raw_content=content_v2,
        extracted_text=content_v2.decode("utf-8"),
    )
    assert src2["source_id"] == src1["source_id"]
    assert src2["version"] == 2


def test_evidence_ledger_corroboration_and_contradictions(tmp_path):
    from app.services import projects as ps
    ledger = EvidenceLedger()
    library = SourceLibrary(base_storage_dir=tmp_path / "sources")
    proj = ps.create_project("Evidence Ledger Test Proj", owner="admin")
    project_id = proj["id"]

    # Create 2 independent sources on different domains
    s1 = library.add_source(
        project_id=project_id,
        url="https://lab-a.org/findings",
        title="Lab A Findings",
        raw_content=b"Measured battery density: 400 Wh/kg.",
        extracted_text="Measured battery density: 400 Wh/kg.",
    )
    s2 = library.add_source(
        project_id=project_id,
        url="https://lab-b.com/study",
        title="Lab B Study",
        raw_content=b"Confirmed battery energy density at 400 Wh/kg.",
        extracted_text="Confirmed battery energy density at 400 Wh/kg.",
    )

    # Add claim
    claim = ledger.add_claim(project_id, "Solid state battery achieved 400 Wh/kg density.")
    assert claim.verification_status == "UNVERIFIED"

    # Add evidence from source 1 -> status SUPPORTED
    ledger.add_evidence(project_id, claim.claim_id, s1["source_id"], "Measured battery density: 400 Wh/kg.", confidence_score=0.9)
    claim = ledger.corroborate_claim(claim.claim_id)
    assert claim.verification_status == "SUPPORTED"

    # Add evidence from independent domain source 2 -> status CORROBORATED
    ledger.add_evidence(project_id, claim.claim_id, s2["source_id"], "Confirmed battery energy density at 400 Wh/kg.", confidence_score=0.95)
    claim = ledger.corroborate_claim(claim.claim_id)
    assert claim.verification_status == "CORROBORATED"

    # Flag contradiction
    claim = ledger.flag_contradiction(claim.claim_id, "Third study claims density was limited to 280 Wh/kg.")
    assert claim.verification_status == "CONTRADICTED"
    assert claim.contradictory_evidence is not None


def test_citation_engine_validation(tmp_path):
    engine = CitationEngine()
    project_id = "prj_test_citations_789"
    sources = [
        {"source_id": "src_1", "title": "DOE Renewable Report", "original_url": "https://energy.gov/report", "publisher": "US DOE", "extracted_text": "Solar cost dropped 80% over the last decade."},
        {"source_id": "src_2", "title": "MIT Technology Review", "original_url": "https://technologyreview.com/solar", "publisher": "MIT", "extracted_text": "Battery storage is key for grid balancing."},
    ]

    # Valid report with matching citations
    valid_report = """
    # Solar and Storage Analysis
    Residential solar installation costs have dropped significantly [1].
    Grid balancing relies heavily on stationary storage batteries [2].

    ## Sources
    [1] DOE Renewable Report. Published by US DOE. URL: https://energy.gov/report
    [2] MIT Technology Review. Published by MIT. URL: https://technologyreview.com/solar
    """
    audit = engine.validate_report_citations(project_id, valid_report, sources)
    assert audit["valid"] is True
    assert audit["total_citations"] == 2
    assert len(audit["errors"]) == 0

    # Invalid report with hallucinated citation [99]
    invalid_report = """
    # Unverified Findings
    This claim references a non-existent source [99].
    """
    bad_audit = engine.validate_report_citations(project_id, invalid_report, sources)
    assert bad_audit["valid"] is False
    assert any("out of bounds" in err or "does not resolve" in err for err in bad_audit["errors"])


def test_research_planner_strategy_and_budget():
    planner = ResearchPlanner()
    objective = "Research the latest developments in renewable energy storage, compare major technologies, identify reputable sources, and produce a detailed report with citations."
    assert planner.requires_research(objective)

    strategy = planner.generate_strategy(objective)
    assert strategy.main_question
    assert len(strategy.subquestions) >= 3
    assert len(strategy.initial_queries) >= 3
    assert strategy.budget.can_search()
    assert strategy.budget.can_retrieve()


def test_orchestrator_template_research_plan_validation():
    intake = {
        "objective": "Research the latest developments in renewable energy storage and produce a cited report."
    }
    nodes = [
        {"id": "controller-local", "is_worker": False, "enabled": True, "authenticated": True, "health": "online", "capabilities": ["controller"], "models": []},
        {"id": "qwen-vps", "is_worker": True, "enabled": True, "authenticated": True, "health": "online", "capabilities": ["python_execution"], "models": []},
    ]

    # Generate plan
    plan = orch_planner.template_research_plan(intake, nodes)
    normalized = task_graph.normalize_plan(plan, nodes)
    validation = task_graph.validate_plan(normalized, nodes)

    assert validation["valid"] is True
    assert len(validation["errors"]) == 0

    # Ensure all tasks are controller_step with valid steps
    task_steps = [t["action"].get("step") for t in plan["tasks"]]
    assert "research_search" in task_steps
    assert "research_fetch" in task_steps
    assert "research_verify" in task_steps
    assert "research_synthesize" in task_steps
