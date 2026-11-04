"""
Stage 4 Research Integrity & Execution Safety Test Suite.

Covers:
1. Security Mandate (Pre-Stage 4):
   - Fail-closed execution gate (is_os_enforced_sandbox_available -> False)
   - Code evaluation returns BLOCKED with SECURE_EXECUTION_UNAVAILABLE
   - Static syntax validation without dynamic execution verification
2. Finding F-06: Authoritative Claim Extraction & Non-Synthetic Confidence
   - Character/line source spans, exact context passages, exact quotes
   - Grounded, non-synthetic confidence calculation (no hardcoded 0.9)
3. Finding F-07: Strict Citation Validation & Zero-Citation Rejection
   - Zero citations fail with MISSING_REQUIRED_CITATIONS
   - Unresolved citations, failed source retrieval, and ungrounded claims fail
   - Evaluator report checks reject zero-citation and ungrounded reports
4. Finding F-08: Authoritative Domain Parsing, Syndication Deduplication, Contradictions
   - Public suffix and multi-tenant parsing (bbc.co.uk, com.au, alice.github.io vs bob.github.io)
   - Syndicated content with matching hashes/titles counts as 1 source (supported vs corroborated)
   - Contradiction preservation (status=contradicted, confidence penalty)
5. Finding F-09: Research-Run Isolation
   - Strict scoping of queries, sources, and claims to research_run_id
   - Cross-run citation contamination detection in audit and evaluator
"""

import ast
import uuid
import pytest

from app.database import get_db_connection
from app.services.orchestration.code_sandbox import (
    SandboxResult,
    is_os_enforced_sandbox_available,
    run_isolated_code_evaluation,
)
from app.services.orchestration.evaluator import (
    evaluate_code_deliverable,
    evaluate_report,
)
from app.services.research.citations import (
    CitationEngine,
    CitationValidationReport,
)
from app.services.research.extraction import (
    FactualClaimSpan,
    extract_claims_with_spans,
)
from app.services.research.sources import SourceLibrary
from app.services.research.verification import (
    EvidenceLedger,
    compute_defensible_confidence,
    extract_registered_domain,
)


# ============================================================================
# 1. Pre-Stage 4 Security Mandate: Untrusted Code Execution Gate
# ============================================================================

class TestSecurityExecutionRestriction:
    """Verify mandatory pre-Stage 4 execution safety constraints."""

    def test_os_enforced_sandbox_is_unavailable_by_default(self):
        """OS-level isolation backend must report unavailable by default."""
        assert is_os_enforced_sandbox_available() is False

    def test_run_isolated_code_evaluation_blocks_execution(self):
        """run_isolated_code_evaluation must return BLOCKED with SECURE_EXECUTION_UNAVAILABLE."""
        result = run_isolated_code_evaluation("print('Hello world')", timeout_seconds=5)
        assert result.status == "BLOCKED"
        assert result.success is False
        assert "SECURE_EXECUTION_UNAVAILABLE" in (result.error_message or "")
        assert result.stdout == ""
        assert "secure_execution_unavailable" in (result.failed_checks or [])

    def test_evaluate_code_deliverable_blocks_execution(self):
        """evaluate_code_deliverable returns decision=BLOCKED when dynamic execution is needed."""
        code = "def add(a, b):\n    return a + b\n"
        test_code = "def test_add():\n    assert add(1, 2) == 3\n"
        res = evaluate_code_deliverable(code, filename="math_util.py", test_code=test_code)
        
        assert res["decision"] == "BLOCKED"
        assert res["blocked_reason"] == "SECURE_EXECUTION_UNAVAILABLE"
        assert res["accepted"] is False
        # Static checks must pass without claiming dynamic execution
        assert any(c["name"] == "python_syntax_valid" and c["passed"] for c in res["execution_checks"])
        assert any(c["name"] == "code_syntax_valid" and c["passed"] for c in res["goal_checks"])
        assert any(c["name"] == "secure_execution_environment" and not c["passed"] for c in res["goal_checks"])


# ============================================================================
# 2. Finding F-06: Authoritative Claim Extraction & Grounded Confidence
# ============================================================================

class TestClaimExtractionAndConfidence:
    """Verify F-06 claim extraction with character/line spans and dynamic confidence."""

    def test_extract_claims_with_spans_character_offsets(self):
        sample_text = (
            "Introduction to AI systems.\n"
            "Project Qwen achieved high throughput in benchmark testing across 12 clusters.\n"
            "This is general commentary.\n"
            "Latency decreased by 45% during peak hours under sustained load.\n"
            "Final conclusion."
        )
        claims = extract_claims_with_spans(sample_text, source_id="src-123", source_quality=0.9)
        assert len(claims) >= 2

        for c in claims:
            assert isinstance(c, FactualClaimSpan)
            assert c.source_id == "src-123"
            # Verify character offsets match exact substring
            extracted_sub = sample_text[c.start_char:c.end_char]
            assert extracted_sub == c.claim_text
            # Verify line numbers
            assert c.start_line >= 1
            assert c.end_line >= c.start_line
            # Verify context passage contains the claim
            assert c.claim_text in c.context_passage
            # Verify confidence score is defensible, float, non-synthetic (not hardcoded 0.9)
            assert 0.0 < c.confidence_score <= 1.0

    def test_extract_claims_filters_noise_and_short_fragments(self):
        noise_text = "### Header\n* Bullet item\nShort.\n| col1 | col2 |\nhttp://example.com"
        claims = extract_claims_with_spans(noise_text)
        assert len(claims) == 0

    def test_extract_claims_empty_or_minimal_text(self):
        assert extract_claims_with_spans("") == []
        assert extract_claims_with_spans("Too short text.") == []

    def test_compute_defensible_confidence_calculation(self):
        """Verify non-synthetic confidence calculation."""
        # Contradicted claims drop to 0.0 (no numerical confidence floor per Stage 4 review)
        conf_contra = compute_defensible_confidence(
            verification_status="contradicted",
            independent_domain_count=3,
            source_quality_scores=[0.9, 0.9],
            has_contradiction=True,
        )
        assert conf_contra == 0.0

        # Insufficient evidence drops to 0.0
        conf_none = compute_defensible_confidence(
            verification_status="insufficient_evidence",
            independent_domain_count=0,
            source_quality_scores=[],
        )
        assert conf_none == 0.0

        # Supported (single domain)
        conf_supp_low = compute_defensible_confidence(
            verification_status="supported",
            independent_domain_count=1,
            source_quality_scores=[0.3],
            has_exact_quote=False,
        )
        conf_supp_high = compute_defensible_confidence(
            verification_status="supported",
            independent_domain_count=1,
            source_quality_scores=[0.95],
            has_exact_quote=True,
        )
        assert conf_supp_low < conf_supp_high
        assert conf_supp_high <= 0.80

        # Corroborated (multi-domain)
        conf_corr = compute_defensible_confidence(
            verification_status="corroborated",
            independent_domain_count=3,
            source_quality_scores=[0.9, 0.85],
            has_exact_quote=True,
            evidence_strengths=["strong"],
        )
        assert conf_corr > conf_supp_high
        assert conf_corr <= 0.98
        # Must not be a hardcoded 0.9
        assert conf_corr != 0.9


# ============================================================================
# 3. Finding F-07: Strict Citation Validation & Zero-Citation Rejection
# ============================================================================

class TestCitationValidationAndZeroCitationRejection:
    """Verify F-07 strict zero-citation rejection and claim-level grounding."""

    def test_zero_citation_report_fails_validation(self):
        engine = CitationEngine()
        report_no_cites = "# Executive Summary\nThe system performed well with zero citations."
        res = engine.validate_citations("proj-1", report_no_cites, sources=[])
        assert res.is_valid is False
        assert res.total_markers_found == 0
        assert any("MISSING_REQUIRED_CITATIONS" in err for err in res.errors)

    def test_unresolved_citation_fails(self):
        engine = CitationEngine()
        report = "According to recent findings [1], the latency improved."
        # No sources in library
        res = engine.validate_citations("proj-1", report, sources=[])
        assert res.is_valid is False
        assert 1 in res.unresolved_citation_numbers
        assert any("does not resolve" in err for err in res.errors)

    def test_citation_to_failed_source_fails(self):
        engine = CitationEngine()
        report = "Performance metrics showed improvement [1]."
        sources = [
            {
                "id": "src-1",
                "canonical_url": "https://example.org/bad",
                "status": "failed",
                "extracted_text": "",
                "content_hash": "",
            }
        ]
        res = engine.validate_citations("proj-1", report, sources=sources)
        assert res.is_valid is False
        assert len(res.unretrieved_sources) == 1

    def test_citation_lacking_claim_level_support_fails(self):
        engine = CitationEngine()
        report = "Quantum computing algorithms achieved quadratic acceleration in database queries [1]."
        sources = [
            {
                "id": "src-1",
                "title": "Cooking Recipes for Italian Pasta",
                "canonical_url": "https://recipes.org/pasta",
                "status": "completed",
                "extracted_text": "Boil salted water and add spaghetti. Simmer for 10 minutes.",
                "content_hash": "hash123",
            }
        ]
        res = engine.validate_citations("proj-1", report, sources=sources)
        assert res.is_valid is False
        assert any("lacks demonstrable claim-level support" in err for err in res.errors)

    def test_grounded_citation_passes(self):
        engine = CitationEngine()
        report = "Model inference throughput reached 1400 tokens per second across distributed clusters [1]."
        sources = [
            {
                "id": "src-1",
                "title": "Distributed Inference Benchmark",
                "canonical_url": "https://benchmarks.org/ai-throughput",
                "status": "completed",
                "extracted_text": "Our benchmark results show model inference throughput reached 1400 tokens per second.",
                "content_hash": "hash-grounded",
            }
        ]
        res = engine.validate_citations("proj-1", report, sources=sources)
        assert res.is_valid is True
        assert len(res.errors) == 0

    def test_evaluate_report_zero_citations_rejected(self):
        """evaluate_report with requires_citations=True rejects 0-citation deliverable."""
        report_md = "# Architecture Review\nThe architecture is highly scalable and resilient."
        res = evaluate_report(report_md, requires_citations=True, project_id="proj-test")
        assert res["accepted"] is False
        assert res["decision"] == "REJECTED"
        coverage_check = next(c for c in res["goal_checks"] if c["name"] == "citation_coverage")
        assert coverage_check["passed"] is False
        assert "MISSING_REQUIRED_CITATIONS" in coverage_check["detail"]


# ----------------------------------------------------------------------------
# Test Fixture Helpers
# ----------------------------------------------------------------------------

def _setup_test_project_and_runs(proj_id: str, run_ids: list[str] | None = None) -> None:
    """Helper to insert project and research_runs rows to satisfy foreign key constraints."""
    conn = get_db_connection()
    try:
        now = "2026-10-19T12:00:00Z"
        conn.execute(
            """
            INSERT OR IGNORE INTO projects (id, name, owner, knowledge_dir, created_at, updated_at, last_activity_at)
            VALUES (?, ?, 'tester', ?, ?, ?, ?)
            """,
            (proj_id, f"Project {proj_id}", f"/tmp/{proj_id}", now, now, now),
        )
        for r_id in (run_ids or []):
            conn.execute(
                """
                INSERT OR IGNORE INTO research_runs (id, project_id, status, created_at, updated_at)
                VALUES (?, ?, 'active', ?, ?)
                """,
                (r_id, proj_id, now, now),
            )
        conn.commit()
    finally:
        conn.close()


# ============================================================================
# 4. Finding F-08: Authoritative Domain Parsing, Syndication & Contradictions
# ============================================================================

class TestDomainParsingAndCorroboration:
    """Verify F-08 domain parsing, syndication deduplication, and contradiction tracking."""

    def test_extract_registered_domain_public_suffixes(self):
        # ccSLD multi-part suffixes
        assert extract_registered_domain("https://news.bbc.co.uk/world/article") == "bbc.co.uk"
        assert extract_registered_domain("https://www.finance.gov.uk/data") == "finance.gov.uk"
        assert extract_registered_domain("https://it.news.com.au/tech") == "news.com.au"
        assert extract_registered_domain("https://portal.research.co.jp/paper") == "research.co.jp"
        assert extract_registered_domain("https://sub.univ.ac.in/dept") == "univ.ac.in"

    def test_extract_registered_domain_multi_tenant(self):
        # Multi-tenant platforms where tenant subdomain is the independent entity
        alice = extract_registered_domain("https://alice.github.io/paper.html")
        bob = extract_registered_domain("https://bob.github.io/benchmark.html")
        assert alice == "alice.github.io"
        assert bob == "bob.github.io"
        assert alice != bob

        app1 = extract_registered_domain("https://alpha.pages.dev/api")
        app2 = extract_registered_domain("https://beta.pages.dev/api")
        assert app1 == "alpha.pages.dev"
        assert app2 == "beta.pages.dev"
        assert app1 != app2

    def test_extract_registered_domain_standard_gtld(self):
        assert extract_registered_domain("https://deepmind.google.com/research") == "google.com"
        assert extract_registered_domain("https://blog.cloudflare.com/post") == "cloudflare.com"

    def test_syndicated_content_does_not_falsely_corroborate(self):
        """Two sources from different domains with identical content hash count as 1 source."""
        ledger = EvidenceLedger()
        proj_id = f"proj-{uuid.uuid4().hex[:8]}"
        _setup_test_project_and_runs(proj_id)
        claim = ledger.record_claim(proj_id, "Target system reached 99.99% availability.")
        claim_id = claim["id"]

        # Register two sources with distinct domains but identical content_hash (wire syndication)
        shared_hash = "syndicated-hash-abc-123"
        s1_id = f"src-{uuid.uuid4().hex[:8]}"
        s2_id = f"src-{uuid.uuid4().hex[:8]}"
        conn = get_db_connection()
        try:
            conn.execute(
                "INSERT INTO research_sources (id, project_id, canonical_url, original_url, title, content_hash, status, created_at, updated_at, retrieved_at) VALUES (?, ?, ?, ?, ?, ?, ?, '2026-10-19', '2026-10-19', '2026-10-19')",
                (s1_id, proj_id, "https://news-outlet-one.com/story", "https://news-outlet-one.com/story", "Syndicated Story Title", shared_hash, "extracted"),
            )
            conn.execute(
                "INSERT INTO research_sources (id, project_id, canonical_url, original_url, title, content_hash, status, created_at, updated_at, retrieved_at) VALUES (?, ?, ?, ?, ?, ?, ?, '2026-10-19', '2026-10-19', '2026-10-19')",
                (s2_id, proj_id, "https://wire-republish-two.co.uk/story", "https://wire-republish-two.co.uk/story", "Syndicated Story Title", shared_hash, "extracted"),
            )
            conn.commit()
        finally:
            conn.close()

        ledger.attach_evidence(proj_id, claim_id, s1_id, "99.99% availability.")
        ledger.attach_evidence(proj_id, claim_id, s2_id, "99.99% availability.")

        # Recompute status
        status = ledger.recompute_claim_verification(proj_id, claim_id)
        # Because content hashes match, it counts as 1 independent source -> SUPPORTED, NOT CORROBORATED
        assert status == "SUPPORTED"

        claims = ledger.list_claims(proj_id)
        clm = next(c for c in claims if c["id"] == claim_id)
        assert clm["verification_status"] == "supported"

    def test_independent_sources_corroborate_claim(self):
        """Two genuinely independent sources promote claim to CORROBORATED."""
        ledger = EvidenceLedger()
        proj_id = f"proj-{uuid.uuid4().hex[:8]}"
        _setup_test_project_and_runs(proj_id)
        claim = ledger.record_claim(proj_id, "Benchmark latency is under 15ms.")
        claim_id = claim["id"]

        s1_id = f"src-{uuid.uuid4().hex[:8]}"
        s2_id = f"src-{uuid.uuid4().hex[:8]}"
        conn = get_db_connection()
        try:
            conn.execute(
                "INSERT INTO research_sources (id, project_id, canonical_url, original_url, title, content_hash, status, created_at, updated_at, retrieved_at) VALUES (?, ?, ?, ?, ?, ?, ?, '2026-10-19', '2026-10-19', '2026-10-19')",
                (s1_id, proj_id, "https://domain-alpha.com/lab-report", "https://domain-alpha.com/lab-report", "Lab Report Alpha", "unique-hash-111", "extracted"),
            )
            conn.execute(
                "INSERT INTO research_sources (id, project_id, canonical_url, original_url, title, content_hash, status, created_at, updated_at, retrieved_at) VALUES (?, ?, ?, ?, ?, ?, ?, '2026-10-19', '2026-10-19', '2026-10-19')",
                (s2_id, proj_id, "https://domain-beta.org/audit", "https://domain-beta.org/audit", "Independent Audit", "unique-hash-222", "extracted"),
            )
            conn.commit()
        finally:
            conn.close()

        ledger.attach_evidence(proj_id, claim_id, s1_id, "Latency under 15ms measured.")
        ledger.attach_evidence(proj_id, claim_id, s2_id, "Confirmed latency below 15ms.")

        status = ledger.recompute_claim_verification(proj_id, claim_id)
        assert status == "CORROBORATED"

    def test_contradiction_is_preserved_and_penalizes_confidence(self):
        """Contradicted claim is flagged as contradicted, not averaged away."""
        ledger = EvidenceLedger()
        proj_id = f"proj-{uuid.uuid4().hex[:8]}"
        _setup_test_project_and_runs(proj_id)
        claim = ledger.record_claim(proj_id, "Cache hit ratio remained above 95%.")
        claim_id = claim["id"]

        s1_id = f"src-{uuid.uuid4().hex[:8]}"
        s2_id = f"src-{uuid.uuid4().hex[:8]}"
        conn = get_db_connection()
        try:
            conn.execute(
                "INSERT INTO research_sources (id, project_id, canonical_url, original_url, title, content_hash, status, created_at, updated_at, retrieved_at) VALUES (?, ?, ?, ?, ?, ?, ?, '2026-10-19', '2026-10-19', '2026-10-19')",
                (s1_id, proj_id, "https://monitoring-source.com/stats", "https://monitoring-source.com/stats", "Stats Report", "stats-hash", "extracted"),
            )
            conn.execute(
                "INSERT INTO research_sources (id, project_id, canonical_url, original_url, title, content_hash, status, created_at, updated_at, retrieved_at) VALUES (?, ?, ?, ?, ?, ?, ?, '2026-10-19', '2026-10-19', '2026-10-19')",
                (s2_id, proj_id, "https://incident-log.org/outage", "https://incident-log.org/outage", "Incident Report", "incident-hash", "extracted"),
            )
            conn.commit()
        finally:
            conn.close()

        ledger.attach_evidence(proj_id, claim_id, s1_id, "Cache hit ratio 96%.")
        ledger.record_contradiction(proj_id, claim_id, s2_id, "Actual cache hit ratio dropped to 42% during incident.")

        status = ledger.recompute_claim_verification(proj_id, claim_id)
        assert status == "CONTRADICTED"

        claims = ledger.list_claims(proj_id)
        clm = next(c for c in claims if c["id"] == claim_id)
        assert clm["verification_status"] == "contradicted"
        assert clm["confidence_score"] <= 0.15


# ============================================================================
# 5. Finding F-09: Research-Run Isolation
# ============================================================================

class TestResearchRunIsolation:
    """Verify strict scoping by research_run_id preventing cross-run contamination."""

    def test_source_library_scoping_by_research_run_id(self):
        lib = SourceLibrary()
        proj_id = f"proj-{uuid.uuid4().hex[:8]}"
        run1 = f"rrun-alpha-{uuid.uuid4().hex[:6]}"
        run2 = f"rrun-beta-{uuid.uuid4().hex[:6]}"
        _setup_test_project_and_runs(proj_id, [run1, run2])

        s1 = lib.add_source(
            project_id=proj_id,
            url="https://alpha-site.com/doc",
            title="Alpha Doc",
            research_run_id=run1,
        )
        s2 = lib.add_source(
            project_id=proj_id,
            url="https://beta-site.com/doc",
            title="Beta Doc",
            research_run_id=run2,
        )

        sources_run1 = lib.list_sources(proj_id, research_run_id=run1)
        sources_run2 = lib.list_sources(proj_id, research_run_id=run2)

        assert len(sources_run1) == 1
        assert sources_run1[0]["id"] == s1["id"]
        assert sources_run1[0]["research_run_id"] == run1

        assert len(sources_run2) == 1
        assert sources_run2[0]["id"] == s2["id"]
        assert sources_run2[0]["research_run_id"] == run2

    def test_claims_scoping_by_research_run_id(self):
        ledger = EvidenceLedger()
        proj_id = f"proj-{uuid.uuid4().hex[:8]}"
        run1 = f"rrun-1-{uuid.uuid4().hex[:6]}"
        run2 = f"rrun-2-{uuid.uuid4().hex[:6]}"
        _setup_test_project_and_runs(proj_id, [run1, run2])

        c1 = ledger.record_claim(proj_id, "Claim from run 1", research_run_id=run1)
        c2 = ledger.record_claim(proj_id, "Claim from run 2", research_run_id=run2)

        claims_run1 = ledger.list_claims(proj_id, research_run_id=run1)
        claims_run2 = ledger.list_claims(proj_id, research_run_id=run2)

        assert len(claims_run1) == 1
        assert claims_run1[0]["id"] == c1["id"]
        assert claims_run1[0]["research_run_id"] == run1

        assert len(claims_run2) == 1
        assert claims_run2[0]["id"] == c2["id"]
        assert claims_run2[0]["research_run_id"] == run2

    def test_citation_audit_rejects_cross_run_contamination(self):
        engine = CitationEngine()
        proj_id = f"proj-{uuid.uuid4().hex[:8]}"
        run1 = "rrun-run1"
        run2 = "rrun-run2"

        # Source belongs to Run 1
        source_run1 = {
            "id": "src-run1",
            "title": "Run 1 Source",
            "canonical_url": "https://run1.example.com",
            "status": "completed",
            "extracted_text": "Grounding text for run 1 data.",
            "content_hash": "hash1",
            "research_run_id": run1,
        }

        # Report for Run 2 references Run 1's source
        report_md = "Grounding text for run 1 data [1]."
        res = engine.validate_citations(
            project_id=proj_id,
            report_markdown=report_md,
            sources=[source_run1],
            research_run_id=run2,
        )
        assert res.is_valid is False
        assert any("violates research run isolation" in err for err in res.errors)

    def test_evaluate_report_rejects_cross_run_source_contamination(self):
        run1 = "rrun-run1"
        run2 = "rrun-run2"
        source_run1 = {
            "id": "src-old",
            "title": "Old Research Report",
            "canonical_url": "https://archive.org/data",
            "status": "completed",
            "extracted_text": "Archive evidence details.",
            "content_hash": "hash-old",
            "research_run_id": run1,
        }
        report_md = "# Run 2 Report\nArchive evidence details [src-old]."
        res = evaluate_report(
            report_md,
            requires_citations=True,
            verified_sources=[source_run1],
            run_id=run2,
        )
        assert res["accepted"] is False
        assert res["decision"] == "REJECTED"
        cross_run_check = next(c for c in res["goal_checks"] if c["name"] == "cross_run_isolation")
        assert cross_run_check["passed"] is False
        assert "violate run isolation" in cross_run_check["detail"]
