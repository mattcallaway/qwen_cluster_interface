"""
Stage 4 Review Correction Test Suite.

Authoritative tests for:
1. Sandbox bypass elimination: QC_ENABLE_OS_ENFORCED_SANDBOX=1 cannot permit insecure execution.
2. Factual entailment vs textual similarity: Cases A through G (adversarial negation, direction,
   modality, debunking, percentage mismatch, topic overlap without evidence, exact quote).
3. Confidence score semantics: EvidenceAssessment, no numerical floors for contradicted assertions,
   monotonicity, and explicit heuristic labeling.
4. Maintained Public Suffix List parsing, publisher identity resolution, and content deduplication.
5. Database Migration 14 and research-run isolation end-to-end (the 8 audit scenarios).
"""

import os
import uuid
import pytest

from app.database import get_db_connection
from app.services.orchestration.code_sandbox import (
    is_os_enforced_sandbox_available,
    run_isolated_code_evaluation,
)
from app.services.orchestration.evaluator import (
    evaluate_code_deliverable,
    evaluate_deliverable_acceptance,
    evaluate_report,
)
from app.services.research.citations import (
    CitationEngine,
    CitationValidationReport,
)
from app.services.research.entailment import (
    ClaimEntailmentResult,
    verify_claim_against_source,
)
from app.services.research.public_suffix import (
    are_sources_independent,
    is_duplicate_or_syndicated,
    parse_registered_domain,
    resolve_publisher_identity,
)
from app.services.research.sources import SourceLibrary
from app.services.research.verification import (
    EvidenceAssessment,
    EvidenceLedger,
    compute_defensible_confidence,
    compute_evidence_assessment,
    extract_registered_domain,
)


# ============================================================================
# 1. ELIMINATE CONFIGURATION-ONLY SANDBOX BYPASSES
# ============================================================================

class TestSandboxBypassElimination:
    """Verify that configuration or environment variables alone can NEVER bypass execution gates."""

    def test_env_var_override_does_not_permit_sandbox_availability(self, monkeypatch):
        """Setting QC_ENABLE_OS_ENFORCED_SANDBOX=1 must NOT enable sandbox availability."""
        monkeypatch.setenv("QC_ENABLE_OS_ENFORCED_SANDBOX", "1")
        assert is_os_enforced_sandbox_available() is False

    def test_run_isolated_code_evaluation_blocked_despite_override_flag(self, monkeypatch):
        """Code evaluation must remain strictly BLOCKED even with override env var."""
        monkeypatch.setenv("QC_ENABLE_OS_ENFORCED_SANDBOX", "1")
        res = run_isolated_code_evaluation("print('untrusted')", test_code="assert True")
        assert res.status == "BLOCKED"
        assert res.success is False
        assert "SECURE_EXECUTION_UNAVAILABLE" in (res.error_message or "")
        assert "secure_execution_unavailable" in (res.failed_checks or [])

    def test_evaluator_returns_blocked_when_execution_requested_with_env_override(self, monkeypatch):
        """Evaluator acceptance must return BLOCKED with SECURE_EXECUTION_UNAVAILABLE under env override."""
        monkeypatch.setenv("QC_ENABLE_OS_ENFORCED_SANDBOX", "1")
        code = "def add(x, y):\n    return x + y\n"
        res = evaluate_code_deliverable(
            code=code,
            filename="add.py",
            test_code="assert add(1, 2) == 3",
            acceptance_criteria={"require_execution": True},
        )
        assert res["accepted"] is False
        assert res["decision"] == "BLOCKED"
        assert res["blocked_reason"] == "SECURE_EXECUTION_UNAVAILABLE"
        assert "SECURE_EXECUTION_UNAVAILABLE" in res["summary"]

    def test_static_validation_does_not_claim_dynamic_execution_verified(self):
        """Static AST and syntax validation succeeds but explicitly disclaims dynamic execution."""
        code = "def compute():\n    return 42\n"
        res = evaluate_code_deliverable(
            code=code,
            filename="compute.py",
            acceptance_criteria={"required_functions": ["compute"]},
        )
        assert res["accepted"] is True
        assert res["decision"] == "ACCEPTED"
        # Confirm static syntax check does NOT claim dynamic execution was verified
        chk = next(c for c in res["goal_checks"] if c["name"] == "code_syntax_valid")
        assert "dynamic execution unverified" in chk["detail"]


# ============================================================================
# 2. FACTUAL ENTAILMENT VS TEXTUAL SIMILARITY (ADVERSARIAL CASES A - G)
# ============================================================================

class TestFactualEntailmentAdversarialCases:
    """
    Test deterministic factual entailment engine across the 7 required adversarial scenarios.
    Textual/keyword overlap alone must NEVER result in SUPPORTED.
    """

    def test_case_a_negation_conflict(self):
        """
        Case A:
        Source: 'The county did not approve the project.'
        Claim: 'The county approved the project.'
        Must be CONTRADICTED (negation polarity clash).
        """
        source = "The county did not approve the project after public hearing."
        claim = "The county approved the project."
        res = verify_claim_against_source(claim, source)
        assert res.status == "CONTRADICTED"
        assert "Polarity conflict" in res.reason or "negation" in res.reason.lower()

    def test_case_b_directional_metric_conflict(self):
        """
        Case B:
        Source: 'The budget decreased from $20 million to $15 million.'
        Claim: 'The budget increased from $15 million to $20 million.'
        Must be CONTRADICTED (directional conflict & inverted transition).
        """
        source = "The municipal budget decreased from $20 million to $15 million in the latest fiscal cycle."
        claim = "The budget increased from $15 million to $20 million."
        res = verify_claim_against_source(claim, source)
        assert res.status == "CONTRADICTED"
        assert ("Directional metric conflict" in res.reason or "Inverted transition" in res.reason)

    def test_case_c_temporal_modal_conflict(self):
        """
        Case C:
        Source: 'The project is expected to finish in 2028.'
        Claim: 'The project was completed in 2028.'
        Must be CONTRADICTED (future expectation vs completed past fact).
        """
        source = "The transit expansion project is expected to finish in 2028 according to regional planners."
        claim = "The project was completed in 2028."
        res = verify_claim_against_source(claim, source)
        assert res.status == "CONTRADICTED"
        assert "Temporal/modality conflict" in res.reason

    def test_case_d_epistemic_debunking(self):
        """
        Case D:
        Source: 'Critics falsely claimed that the bridge collapsed.'
        Claim: 'The bridge collapsed.'
        Must be CONTRADICTED (source frames assertion as debunked/false).
        """
        source = "Social media critics falsely claimed that the bridge collapsed during the storm."
        claim = "The bridge collapsed."
        res = verify_claim_against_source(claim, source)
        assert res.status == "CONTRADICTED"
        assert "debunked" in res.reason.lower() or "false" in res.reason.lower()

    def test_case_e_numeric_percentage_mismatch(self):
        """
        Case E:
        Source: 'The population grew by 5%.'
        Claim: 'The population grew by 50%.'
        Must be CONTRADICTED (5% vs 50% mismatch despite shared entity/verb).
        """
        source = "Over the past decade, the county population grew by 5% according to official census records."
        claim = "The population grew by 50%."
        res = verify_claim_against_source(claim, source)
        assert res.status == "CONTRADICTED"
        assert "Numeric percentage mismatch" in res.reason

    def test_case_f_topic_overlap_without_evidence(self):
        """
        Case F:
        Citation points to a real document discussing the same subject but containing no evidence for the claim.
        Must be INSUFFICIENT_EVIDENCE or UNVERIFIED (never SUPPORTED).
        """
        source = (
            "The regional transit authority held a meeting regarding subway safety, track maintenance, "
            "and electrical power sub-stations. Multiple city officials and engineers attended the session."
        )
        claim = "The transit authority replaced 15 miles of subway track in 2024."
        res = verify_claim_against_source(claim, source)
        assert res.status in ("INSUFFICIENT_EVIDENCE", "UNVERIFIED")
        assert res.status != "SUPPORTED"

    def test_case_g_exact_supporting_source_quote(self):
        """
        Case G:
        Citation points to an exact supporting source quote.
        Must be SUPPORTED with exact quote verified.
        """
        source = "Official records confirm that the county approved the project during the December session."
        claim = "The county approved the project"
        res = verify_claim_against_source(claim, source)
        assert res.status == "SUPPORTED"
        assert res.is_exact_quote is True

    def test_unprovable_claim_returns_unverified_not_supported(self):
        """When deterministic verification cannot establish entailment, returns UNVERIFIED."""
        source = "The department reviewed several infrastructure proposals during the annual conference."
        claim = "The infrastructure proposal was favored by a majority of department delegates."
        res = verify_claim_against_source(claim, source)
        assert res.status in ("UNVERIFIED", "INSUFFICIENT_EVIDENCE")
        assert res.status != "SUPPORTED"


# ============================================================================
# 3. CITATION VALIDATION & DELIVERABLE EVALUATION INTEGRATION
# ============================================================================

class TestCitationGroundingInDeliverables:
    """Verify that citation validation rejects contradicted and unverified claims."""

    def test_citation_contradiction_fails_report_validation(self):
        """A report whose citation contradicts the source fails citation audit."""
        engine = CitationEngine()
        sources = [{
            "id": "src-1",
            "canonical_url": "https://example.gov/budget",
            "status": "extracted",
            "extracted_text": "The municipal budget decreased from $20 million to $15 million.",
            "content_hash": "hash123",
        }]
        md = "According to official city data, the budget increased from $15 million to $20 million [1]."
        report = engine.validate_citations("proj-1", md, sources=sources)
        
        assert report.is_valid is False
        assert len(report.contradicted_citations) > 0
        assert any("CLAIM_CONTRADICTED" in err for err in report.errors)

    def test_deliverable_evaluator_rejects_report_with_contradicted_citations(self):
        """Deliverable evaluator must decide REJECTED when citation audit has contradictions."""
        md = (
            "# Research Report\n\n"
            "## Summary\n"
            "The budget increased from $15 million to $20 million [1].\n\n"
            "## Provenance\n"
            "Data analyzed from city records.\n"
        )
        audit_data = {
            "valid": False,
            "total_citations": 1,
            "resolved_citations": 1,
            "contradicted": ["[1]"],
            "unverified": [],
            "errors": ["CLAIM_CONTRADICTED: Citation [1] is contradicted by referenced source"],
        }
        res = evaluate_report(
            md=md,
            requires_citations=True,
            citation_audit=audit_data,
        )
        assert res["accepted"] is False
        assert res["decision"] == "REJECTED"
        assert any(c["name"] == "citation_contradiction" and not c["passed"] for c in res["goal_checks"])

    def test_deliverable_evaluator_routes_unverified_claims_to_human_review(self):
        """Deliverable evaluator routes unverified citations to NEEDS_HUMAN_REVIEW."""
        md = (
            "# Research Report\n\n"
            "## Summary\n"
            "The county completed several transit upgrades in 2024 [1].\n\n"
            "## Provenance\n"
            "General findings.\n"
        )
        audit_data = {
            "valid": False,
            "total_citations": 1,
            "resolved_citations": 1,
            "contradicted": [],
            "unverified": ["[1]"],
            "errors": ["UNVERIFIED: Citation [1] cannot be verified against source"],
        }
        res = evaluate_report(
            md=md,
            requires_citations=True,
            citation_audit=audit_data,
        )
        assert res["accepted"] is False
        assert res["decision"] == "NEEDS_HUMAN_REVIEW"
        assert any(c["name"] == "citation_unverified" and not c["passed"] for c in res["goal_checks"])


# ============================================================================
# 4. CONFIDENCE SCORE SEMANTICS & MONOTONICITY
# ============================================================================

class TestConfidenceSemantics:
    """Verify defensible confidence semantics, EvidenceAssessment structure, and monotonicity."""

    def test_contradicted_claims_receive_zero_confidence_no_floor(self):
        """Contradicted assertions must receive score 0.0 without any numerical floor."""
        assessment = compute_evidence_assessment(
            verification_status="contradicted",
            independent_domain_count=3,
            source_quality_scores=[0.9, 0.95],
            has_contradiction=True,
        )
        assert assessment.evidence_strength_score == 0.0
        assert assessment.has_contradiction is True
        assert assessment.empirical_confidence is None
        assert "Heuristic evidence-strength indicator" in assessment.score_limitations

    def test_monotonicity_two_independent_sources_higher_than_one(self):
        """Corroborated by 2 independent sources must yield higher evidence strength than 1 source."""
        score_1_source = compute_defensible_confidence(
            verification_status="supported",
            independent_domain_count=1,
            source_quality_scores=[0.8],
        )
        score_2_sources = compute_defensible_confidence(
            verification_status="corroborated",
            independent_domain_count=2,
            source_quality_scores=[0.8, 0.8],
        )
        assert score_2_sources > score_1_source

    def test_directness_verbatim_quote_higher_than_paraphrase(self):
        """Exact verbatim quotes receive higher evidence strength than generic paraphrase."""
        quote_assessment = compute_evidence_assessment(
            verification_status="supported",
            independent_domain_count=1,
            source_quality_scores=[0.8],
            has_exact_quote=True,
            evidence_strengths=["strong"],
        )
        paraphrase_assessment = compute_evidence_assessment(
            verification_status="supported",
            independent_domain_count=1,
            source_quality_scores=[0.8],
            has_exact_quote=False,
            evidence_strengths=["moderate"],
        )
        assert quote_assessment.evidence_strength_score > paraphrase_assessment.evidence_strength_score
        assert quote_assessment.directness_of_evidence == "verbatim_quote"
        assert paraphrase_assessment.directness_of_evidence == "paraphrase"

    def test_non_independent_source_does_not_inflate_confidence(self):
        """Adding a non-independent source (same registered domain or wire copy) does not inflate score."""
        score_1 = compute_defensible_confidence(
            verification_status="supported",
            independent_domain_count=1,
            source_quality_scores=[0.8],
        )
        # Still 1 independent domain despite 2 source articles
        score_non_independent = compute_defensible_confidence(
            verification_status="supported",
            independent_domain_count=1,
            source_quality_scores=[0.8, 0.8],
        )
        assert score_non_independent == score_1


# ============================================================================
# 5. PUBLIC SUFFIX LIST PARSING & PUBLISHER INDEPENDENCE
# ============================================================================

class TestPublicSuffixAndIndependence:
    """Verify maintained Public Suffix List parsing, private domains, and publisher networks."""

    def test_multipart_public_suffixes(self):
        """Multi-part ccSLDs correctly resolve registered domain without truncation."""
        assert extract_registered_domain("https://news.bbc.co.uk/world") == "bbc.co.uk"
        assert extract_registered_domain("https://dept.gov.au/policy") == "dept.gov.au"
        assert extract_registered_domain("https://sub.tokyo.co.jp/about") == "tokyo.co.jp"
        assert extract_registered_domain("https://service.gc.ca/login") == "service.gc.ca"

    def test_private_hosting_domains_treated_as_distinct_tenants(self):
        """Multi-tenant platforms treat distinct subdomains as independent tenant entities."""
        domain_alice, _, is_alice_tenant = parse_registered_domain("https://alice.github.io/blog")
        domain_bob, _, is_bob_tenant = parse_registered_domain("https://bob.github.io/site")
        
        assert is_alice_tenant is True
        assert is_bob_tenant is True
        assert domain_alice == "alice.github.io"
        assert domain_bob == "bob.github.io"
        assert domain_alice != domain_bob
        assert are_sources_independent("https://alice.github.io", "https://bob.github.io") is True

    def test_syndicated_wire_stories_not_independent(self):
        """Syndicated wire stories across different domains share publisher identity."""
        ap_primary = "https://apnews.com/article/inflation-report-123"
        local_paper = "https://dailygazette.com/news/economy-inflation"
        
        # Local paper states declared publisher is Associated Press
        pub_primary = resolve_publisher_identity(ap_primary)
        pub_syndicated = resolve_publisher_identity(local_paper, declared_publisher="Associated Press")
        
        assert pub_primary == "wire:associated_press"
        assert pub_syndicated == "wire:associated_press"
        assert are_sources_independent(ap_primary, local_paper, publisher_b="Associated Press") is False

    def test_shared_conglomerate_ownership_not_independent(self):
        """Outlets belonging to the same media ownership conglomerate are not independent."""
        wsj = "https://wsj.com/economy/update"
        nypost = "https://nypost.com/business/economy"
        
        pub_wsj = resolve_publisher_identity(wsj)
        pub_nypost = resolve_publisher_identity(nypost)
        
        assert pub_wsj == "conglomerate:news_corp"
        assert pub_nypost == "conglomerate:news_corp"
        assert are_sources_independent(wsj, nypost) is False

    def test_syndicated_stories_with_modified_titles_detected_as_duplicate(self):
        """Syndicated articles with modified headlines are detected via text shingling."""
        body = (
            "The Federal Reserve announced on Wednesday that benchmark interest rates will remain unchanged. "
            "Chair Jerome Powell indicated that future policy decisions will depend on incoming economic data, "
            "inflation indicators, and labor market resilience across sectors."
        )
        src1 = {
            "title": "Fed Holds Interest Rates Steady Amid Inflation Concerns",
            "extracted_text": body,
            "content_hash": "hash_fed_1",
        }
        src2 = {
            "title": "Rates Stay Put as Central Bank Weighs Economic Indicators",  # Modified headline
            "extracted_text": body,
            "content_hash": "hash_fed_2",
        }
        is_dup, reason = is_duplicate_or_syndicated(src1, src2)
        assert is_dup is True
        assert "syndicated_content_similarity" in reason

    def test_same_generic_title_unrelated_articles_not_falsely_deduplicated(self):
        """Generic headlines like 'Breaking News' must NOT cause false deduplication of distinct stories."""
        src1 = {
            "title": "Breaking News",
            "extracted_text": "A severe earthquake struck off the coast of Japan early Tuesday morning.",
            "content_hash": "h_quake",
        }
        src2 = {
            "title": "Breaking News",
            "extracted_text": "Tech company unveils new quantum computing processor with 1000 qubits.",
            "content_hash": "h_quantum",
        }
        is_dup, reason = is_duplicate_or_syndicated(src1, src2)
        assert is_dup is False
        assert reason == "distinct_sources"


# ============================================================================
# 6. DATABASE MIGRATION & RUN ISOLATION END-TO-END (THE 8 AUDIT SCENARIOS)
# ============================================================================

@pytest.fixture
def setup_project():
    """Creates an isolated test project and cleans up after."""
    proj_id = f"proj-iso-{uuid.uuid4().hex[:8]}"
    conn = get_db_connection()
    try:
        conn.execute(
            """INSERT INTO projects (id, name, owner, knowledge_dir, created_at, updated_at, last_activity_at)
               VALUES (?, ?, 'admin', ?, datetime('now'), datetime('now'), datetime('now'))""",
            (proj_id, f"Isolation Project {proj_id}", f"knowledge_{proj_id}"),
        )
        conn.commit()
    finally:
        conn.close()
    yield proj_id


class TestDatabaseMigrationAndRunIsolationEndToEnd:
    """
    Isolated integration tests for Migration 14 covering all 8 specified scenarios:
    1. Two research runs under the same project.
    2. Different sources and claims in each run.
    3. Missing or NULL run IDs.
    4. Historical pre-migration source records.
    5. Cross-run citation references.
    6. Explicit operator-authorized reuse of a historical source.
    7. Retention of original provenance after authorized reuse.
    8. No implicit promotion of old evidence into a new run.
    9. Arbitrary run reattribution prevention.
    """

    def test_scenarios_1_and_2_two_runs_different_sources_and_claims(self, setup_project):
        """Scenarios 1 & 2: Two runs under same project keep sources and claims isolated."""
        proj_id = setup_project
        lib = SourceLibrary()
        ledger = EvidenceLedger()

        run_a = f"run-a-{uuid.uuid4().hex[:6]}"
        run_b = f"run-b-{uuid.uuid4().hex[:6]}"

        conn = get_db_connection()
        try:
            conn.execute(
                "INSERT INTO research_runs (id, project_id, created_at, updated_at) VALUES (?, ?, datetime('now'), datetime('now'))",
                (run_a, proj_id),
            )
            conn.execute(
                "INSERT INTO research_runs (id, project_id, created_at, updated_at) VALUES (?, ?, datetime('now'), datetime('now'))",
                (run_b, proj_id),
            )
            conn.commit()
        finally:
            conn.close()

        # Run A records Source A and Claim A
        src_a = lib.add_source(
            project_id=proj_id,
            url="https://source-a.org/data",
            title="Source A",
            extracted_text="Finding A verified.",
            research_run_id=run_a,
        )
        claim_a = ledger.record_claim(proj_id, "Claim A text", research_run_id=run_a)

        # Run B records Source B and Claim B
        src_b = lib.add_source(
            project_id=proj_id,
            url="https://source-b.org/data",
            title="Source B",
            extracted_text="Finding B verified.",
            research_run_id=run_b,
        )
        claim_b = ledger.record_claim(proj_id, "Claim B text", research_run_id=run_b)

        # List sources for Run A must only contain Source A
        sources_run_a = lib.list_sources(proj_id, research_run_id=run_a)
        assert len(sources_run_a) == 1
        assert sources_run_a[0]["id"] == src_a["id"]

        # List sources for Run B must only contain Source B
        sources_run_b = lib.list_sources(proj_id, research_run_id=run_b)
        assert len(sources_run_b) == 1
        assert sources_run_b[0]["id"] == src_b["id"]

    def test_scenarios_3_and_4_missing_or_null_run_ids_historical_records(self, setup_project):
        """Scenarios 3 & 4: Historical pre-migration records have NULL run ID and remain intact."""
        proj_id = setup_project
        lib = SourceLibrary()

        # Simulate a pre-migration historical source inserted with NULL research_run_id
        src_hist = lib.add_source(
            project_id=proj_id,
            url="https://historical-archive.org/legacy",
            title="Historical Archive",
            extracted_text="Historical record text.",
            research_run_id=None,
        )
        assert src_hist["research_run_id"] is None

        # Listing without research_run_id returns all project sources including historical
        all_sources = lib.list_sources(proj_id, research_run_id=None)
        assert any(s["id"] == src_hist["id"] for s in all_sources)

    def test_scenario_5_cross_run_citation_detected(self, setup_project):
        """Scenario 5: Referencing a source from a different research run fails citation validation."""
        proj_id = setup_project
        lib = SourceLibrary()
        engine = CitationEngine()

        run_1 = f"run-1-{uuid.uuid4().hex[:6]}"
        run_2 = f"run-2-{uuid.uuid4().hex[:6]}"

        src_1 = lib.add_source(
            project_id=proj_id,
            url="https://example.org/run1-data",
            title="Run 1 Source",
            extracted_text="Data from run 1.",
            research_run_id=run_1,
        )

        # In Run 2, attempt to cite Source 1 without authorization
        report_md = "According to run 1 data, findings are confirmed [1]."
        audit = engine.validate_citations(
            project_id=proj_id,
            report_markdown=report_md,
            sources=[src_1],
            research_run_id=run_2,
        )
        assert audit.is_valid is False
        assert any("violates research run isolation" in err for err in audit.errors)

    def test_scenarios_6_and_7_operator_authorized_reuse_preserves_provenance(self, setup_project):
        """Scenarios 6 & 7: Explicit authorized reuse makes source available while preserving provenance."""
        proj_id = setup_project
        lib = SourceLibrary()
        engine = CitationEngine()

        run_old = f"run-old-{uuid.uuid4().hex[:6]}"
        run_new = f"run-new-{uuid.uuid4().hex[:6]}"

        src_old = lib.add_source(
            project_id=proj_id,
            url="https://benchmark.org/v1",
            title="Benchmark 2023",
            extracted_text="Standard benchmark score is 98.",
            research_run_id=run_old,
        )

        # Before authorization, run_new cannot see src_old
        sources_before = lib.list_sources(proj_id, research_run_id=run_new)
        assert not any(s["id"] == src_old["id"] for s in sources_before)

        # Operator explicitly authorizes reuse of src_old in run_new
        reuse_rec = lib.reuse_source_in_run(
            project_id=proj_id,
            source_id=src_old["id"],
            target_run_id=run_new,
            authorized_by="lead_operator",
            notes="Authorized reuse for comparative benchmark baseline",
        )
        assert reuse_rec["status"] == "authorized"
        assert reuse_rec["origin_run_id"] == run_old

        # After authorization, run_new can see src_old
        sources_after = lib.list_sources(proj_id, research_run_id=run_new)
        assert any(s["id"] == src_old["id"] for s in sources_after)

        # Verify original source provenance remains UNCHANGED in research_sources table
        re_fetched = lib.get_source(proj_id, src_old["id"])
        assert re_fetched["research_run_id"] == run_old  # Preserves origin run ID!

        # Citation validation in run_new now recognizes authorized reuse
        audit = engine.validate_citations(
            project_id=proj_id,
            report_markdown="Standard benchmark score is 98 [1].",
            sources=[re_fetched],
            research_run_id=run_new,
        )
        assert audit.is_valid is True

    def test_scenario_8_no_implicit_promotion_of_old_evidence(self, setup_project):
        """Scenario 8: Old or NULL run sources are never implicitly promoted into new runs."""
        proj_id = setup_project
        lib = SourceLibrary()

        # Add pre-migration source with NULL run ID
        src_unscoped = lib.add_source(
            project_id=proj_id,
            url="https://old-source.org/legacy",
            title="Old Source",
            extracted_text="Legacy content.",
            research_run_id=None,
        )

        new_run = f"run-fresh-{uuid.uuid4().hex[:6]}"
        sources_in_new_run = lib.list_sources(proj_id, research_run_id=new_run)
        
        # Must NOT contain the unscoped legacy source
        assert not any(s["id"] == src_unscoped["id"] for s in sources_in_new_run)

    def test_scenario_9_prevent_arbitrary_run_reattribution(self, setup_project):
        """Ensure records cannot be reattributed to a different run simply by submitting arbitrary run_id."""
        proj_id = setup_project
        lib = SourceLibrary()

        run_original = f"run-orig-{uuid.uuid4().hex[:6]}"
        run_impostor = f"run-impostor-{uuid.uuid4().hex[:6]}"

        # Create original source
        src = lib.add_source(
            project_id=proj_id,
            url="https://secure-repo.org/report",
            title="Security Report",
            extracted_text="Original text.",
            research_run_id=run_original,
        )
        assert src["research_run_id"] == run_original

        # An attacker or secondary run attempts to re-record the same URL with run_impostor
        src_reattempt = lib.add_source(
            project_id=proj_id,
            url="https://secure-repo.org/report",
            title="Updated Title",
            extracted_text="Modified text.",
            research_run_id=run_impostor,
        )

        # The source's research_run_id MUST remain run_original!
        src_after = lib.get_source(proj_id, src["id"])
        assert src_after["research_run_id"] == run_original


# ============================================================================
# Directive Requirement 3: Real Deliverable Completion Gate Four Entailment States
# ============================================================================

class TestDeliverableCompletionGateFourEntailmentStates:
    """
    Tests all four factual entailment states through the deliverable completion gate:
    1. SUPPORTED -> ACCEPTED (Passes completion gate)
    2. CONTRADICTED -> REJECTED (Fails closed, strictly blocks completion gate)
    3. INSUFFICIENT_EVIDENCE -> REJECTED (Fails closed on missing proof, strictly blocks completion gate)
    4. UNVERIFIED -> NEEDS_HUMAN_REVIEW (Blocks automatic completion, requires human review)
    """

    def test_entailment_supported_passes_completion_gate(self):
        """SUPPORTED state passes all 3 layers and achieves ACCEPTED decision."""
        report_md = (
            "# Nuclear Power Generation Q3\n\n"
            "## Summary\n"
            "The commercial nuclear reactor operated at 99.8% capacity throughout Q3 [1].\n\n"
            "## References\n"
            "- [1] Nuclear Regulatory Commission Q3 Operations Log\n"
        )
        sources = [{
            "id": 1,
            "source_id": "src-nrc-01",
            "canonical_url": "https://nrc.gov/q3-report",
            "extracted_text": "The commercial nuclear reactor operated at 99.8% capacity throughout Q3.",
            "clean_text": "The commercial nuclear reactor operated at 99.8% capacity throughout Q3.",
            "status": "fetched",
            "content_hash": "hash_nrc_q3",
        }]

        audit = CitationEngine().validate_report_citations("proj-1", report_md, sources=sources)
        assert audit["valid"] is True
        assert "[1]" in audit["supported"]
        assert len(audit["contradicted"]) == 0
        assert len(audit["insufficient_evidence"]) == 0

        eval_res = evaluate_report(
            md=report_md,
            requires_citations=True,
            verified_sources=sources,
            citation_audit=audit,
        )

        assert eval_res["decision"] == "ACCEPTED"
        assert eval_res["accepted"] is True
        assert any(c["name"] == "citation_audit" and c["passed"] for c in eval_res["goal_checks"])

    def test_entailment_contradicted_blocks_and_rejects_completion_gate(self):
        """CONTRADICTED state triggers citation_contradiction failure and REJECTED decision."""
        report_md = (
            "# Nuclear Power Generation Q3\n\n"
            "## Summary\n"
            "The commercial nuclear reactor operated at 99.8% capacity throughout Q3 [1].\n\n"
            "## References\n"
            "- [1] Nuclear Regulatory Commission Q3 Operations Log\n"
        )
        sources = [{
            "id": 1,
            "source_id": "src-nrc-01",
            "canonical_url": "https://nrc.gov/q3-report",
            "extracted_text": "The commercial nuclear reactor was shut down and operated at 0.0% capacity throughout Q3 due to emergency repairs.",
            "clean_text": "The commercial nuclear reactor was shut down and operated at 0.0% capacity throughout Q3 due to emergency repairs.",
            "status": "fetched",
            "content_hash": "hash_nrc_q3_contra",
        }]

        audit = CitationEngine().validate_report_citations("proj-1", report_md, sources=sources)
        assert audit["valid"] is False
        assert "[1]" in audit["contradicted"]

        eval_res = evaluate_report(
            md=report_md,
            requires_citations=True,
            verified_sources=sources,
            citation_audit=audit,
        )

        assert eval_res["decision"] == "REJECTED"
        assert eval_res["accepted"] is False
        assert any(c["name"] == "citation_contradiction" and not c["passed"] for c in eval_res["goal_checks"])

    def test_entailment_insufficient_evidence_blocks_and_rejects_completion_gate(self):
        """INSUFFICIENT_EVIDENCE state fails closed with citation_insufficient_evidence and REJECTED decision."""
        report_md = (
            "# Corporate Financial Strategy\n\n"
            "## Summary\n"
            "The technology conglomerate authorized a $10 billion share repurchase program for FY2026 [1].\n\n"
            "## References\n"
            "- [1] Annual Shareholder Meeting Transcript\n"
        )
        # Source discusses shareholder meeting and capital strategy generally, but lacks the specific $10B buyback claim
        sources = [{
            "id": 1,
            "source_id": "src-sec-01",
            "canonical_url": "https://sec.gov/filings/corp-10k",
            "extracted_text": "During the meeting, executives reviewed capital allocation strategies, general dividend policies, and broad market conditions.",
            "clean_text": "During the meeting, executives reviewed capital allocation strategies, general dividend policies, and broad market conditions.",
            "status": "fetched",
            "content_hash": "hash_sec_insufficient",
        }]

        audit = CitationEngine().validate_report_citations("proj-1", report_md, sources=sources)
        assert audit["valid"] is False
        assert "[1]" in audit["insufficient_evidence"]

        eval_res = evaluate_report(
            md=report_md,
            requires_citations=True,
            verified_sources=sources,
            citation_audit=audit,
        )

        # Fails closed on insufficient evidence
        assert eval_res["decision"] == "REJECTED"
        assert eval_res["accepted"] is False
        assert any(c["name"] == "citation_insufficient_evidence" and not c["passed"] for c in eval_res["goal_checks"])

    def test_entailment_unverified_requires_human_review(self):
        """UNVERIFIED citations map to NEEDS_HUMAN_REVIEW, preventing automated completion."""
        report_md = (
            "# Emerging Energy Grid Architecture\n\n"
            "## Summary\n"
            "Superconducting transmission lines achieve zero electrical resistance when cooled below 77K [1].\n\n"
            "## References\n"
            "- [1] Technical Specification\n"
        )
        # Construct audit report where assertion is unverified (indeterminate)
        audit = {
            "valid": False,
            "errors": ["UNVERIFIED: Citation [1] cannot be verified against source"],
            "contradicted": [],
            "insufficient_evidence": [],
            "unverified": ["[1]"],
            "missing_evidence_claims": ["[1]"],
        }
        sources = [{
            "id": 1,
            "source_id": "src-spec-01",
            "canonical_url": "https://energy.gov/spec",
            "status": "fetched",
            "content_hash": "h_spec",
        }]

        eval_res = evaluate_report(
            md=report_md,
            requires_citations=True,
            verified_sources=sources,
            citation_audit=audit,
        )

        assert eval_res["decision"] == "NEEDS_HUMAN_REVIEW"
        assert eval_res["accepted"] is False
        assert any(c["name"] == "citation_unverified" and not c["passed"] for c in eval_res["goal_checks"])


# ============================================================================
# Directive Requirement 4 & 5: Public Suffix List Maintenance & Multi-Session Versioning
# ============================================================================

class TestMultiSessionVersionedObservationsAndPSL:
    """
    Tests PSL dataset maintenance and multi-session versioned source observations:
    - Maintained Public Suffix List dataset and update mechanism with SHA-256 verification
    - Multiple independent research sessions within one orchestration run
    - Two versioned observations of the same URL across sessions
    - Immutable provenance and explicit cross-session / cross-run reuse ledger
    """

    def test_psl_dataset_info_and_fallback(self):
        """PSL dataset info reports bundled snapshot status and canonical upstream URL."""
        from app.services.research.public_suffix import get_psl_dataset_info, UPSTREAM_PSL_URL
        info = get_psl_dataset_info()
        assert info["upstream_canonical_url"] == UPSTREAM_PSL_URL
        assert info["effective_rules_count"] > 100
        assert info["effective_icann_count"] > 50

    def test_psl_dataset_update_and_sha256_verification(self):
        """Updating PSL dataset verifies SHA-256 and updates dynamic rules."""
        import hashlib
        from app.services.research.public_suffix import update_psl_dataset, parse_registered_domain

        sample_psl = (
            "// ===BEGIN ICANN DOMAINS===\n"
            "custom-tld.org\n"
            "gov.example\n"
            "// ===BEGIN PRIVATE DOMAINS===\n"
            "tenant-portal.app\n"
        )
        sample_sha = hashlib.sha256(sample_psl.encode("utf-8")).hexdigest()

        # 1. Update with valid expected SHA-256
        res = update_psl_dataset(sample_psl.encode("utf-8"), expected_sha256=sample_sha, save_to_disk=False)
        assert res["status"] == "success"
        assert res["metadata"]["rules_count"] == 3

        # Domain parsing recognizes the newly loaded private suffix
        reg_domain, suffix, is_tenant = parse_registered_domain("https://sub.tenant-portal.app/dash")
        assert is_tenant is True
        assert reg_domain == "sub.tenant-portal.app"
        assert suffix == "tenant-portal.app"

        # 2. Update with invalid SHA-256 fails closed
        with pytest.raises(ValueError, match="PSL SHA-256 mismatch"):
            update_psl_dataset(sample_psl.encode("utf-8"), expected_sha256="wrong_hash_12345", save_to_disk=False)

    def test_multisession_versioned_observations_same_url_same_run(self, setup_project):
        """
        Demonstrates multiple independent research sessions within ONE orchestration run:
        - Session 1 observes URL at Version 1 (content hash H1)
        - Session 2 observes same URL at Version 2 (content hash H2)
        - Both version records are immutable and queryable via get_source_versions
        """
        proj_id = setup_project
        lib = SourceLibrary()

        run_id = f"orch-run-{uuid.uuid4().hex[:8]}"
        session_1 = f"session-preliminary-{uuid.uuid4().hex[:6]}"
        session_2 = f"session-refinement-{uuid.uuid4().hex[:6]}"
        target_url = "https://w3c.org/standards/sample-spec"

        # Observation 1 in Session 1
        text_v1 = "Draft Specification 1.0: Experimental status."
        src_v1 = lib.add_source(
            project_id=proj_id,
            url=target_url,
            title="W3C Sample Spec Draft",
            extracted_text=text_v1,
            research_run_id=run_id,
            session_id=session_1,
        )
        assert src_v1["version"] == 1
        assert src_v1["research_run_id"] == run_id

        # Observation 2 in Session 2 (same orchestration run, updated content)
        text_v2 = "Recommendation 2.0: Official ratified standard."
        src_v2 = lib.add_source(
            project_id=proj_id,
            url=target_url,
            title="W3C Sample Spec Official",
            extracted_text=text_v2,
            research_run_id=run_id,
            session_id=session_2,
        )
        assert src_v2["version"] == 2
        # Immutable source identity and run provenance
        assert src_v2["id"] == src_v1["id"]
        assert src_v2["research_run_id"] == run_id

        # Query versioned observation ledger
        versions = lib.get_source_versions(proj_id, src_v1["id"])
        assert len(versions) == 2
        
        v1_rec = versions[0]
        v2_rec = versions[1]

        assert v1_rec["version_number"] == 1
        assert v1_rec["session_id"] == session_1
        assert v1_rec["research_run_id"] == run_id

        assert v2_rec["version_number"] == 2
        assert v2_rec["session_id"] == session_2
        assert v2_rec["research_run_id"] == run_id

        assert v1_rec["content_hash"] != v2_rec["content_hash"]
        assert v1_rec["storage_path"] != v2_rec["storage_path"]

    def test_explicit_cross_run_source_reuse_ledger(self, setup_project):
        """
        Demonstrates that cross-run source reuse requires explicit entry in research_source_reuses:
        - Unauthorized cross-run citation fails citation validation.
        - Explicitly authorized reuse via reuse_source_in_run succeeds.
        """
        proj_id = setup_project
        lib = SourceLibrary()

        run_alpha = f"run-alpha-{uuid.uuid4().hex[:6]}"
        run_beta = f"run-beta-{uuid.uuid4().hex[:6]}"

        # Create source in run_alpha
        src = lib.add_source(
            project_id=proj_id,
            url="https://ieee.org/papers/distributed-consensus",
            title="Distributed Consensus Analysis",
            extracted_text="Paxos and Raft provide formal consensus guarantees.",
            research_run_id=run_alpha,
            session_id="session-alpha-1",
        )

        report_md = (
            "# Consensus Architecture Review\n\n"
            "## Analysis\n"
            "Paxos and Raft provide formal consensus guarantees [1].\n\n"
            "## References\n"
            "- [1] Distributed Consensus Analysis\n"
        )

        engine = CitationEngine()

        # Run Beta attempts to cite Run Alpha's source WITHOUT authorization
        unauth_audit = engine.validate_report_citations(
            proj_id, report_md, sources=[src], research_run_id=run_beta
        )
        assert unauth_audit["valid"] is False
        assert any("violates research run isolation" in err for err in unauth_audit["errors"])

        # Explicitly authorize reuse from Run Alpha to Run Beta
        reuse_rec = lib.reuse_source_in_run(
            project_id=proj_id,
            source_id=src["id"],
            target_run_id=run_beta,
            authorized_by="lead_investigator",
            notes="Authorized foundational literature reuse across projects.",
        )
        assert reuse_rec["status"] == "authorized"
        assert reuse_rec["origin_run_id"] == run_alpha
        assert reuse_rec["target_run_id"] == run_beta

        # Now Run Beta validates citations with authorized reuse
        auth_audit = engine.validate_report_citations(
            proj_id, report_md, sources=[src], research_run_id=run_beta
        )
        assert auth_audit["valid"] is True
        assert len(auth_audit["errors"]) == 0

