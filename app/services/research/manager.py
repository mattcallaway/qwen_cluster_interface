"""Autonomous Research Subsystem Coordinator and Orchestrator Integration Manager.

Coordinates search, retrieval, browser automation, document extraction, source persistence,
claim-level verification, citation validation, and report synthesis.
Executes securely on the controller node, preserving worker sandbox isolation.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.database import get_db_connection
from app.services import deliverables
from app.services.research.browser import HeadlessBrowserService
from app.services.research.cache import ResearchCache
from app.services.research.citations import CitationEngine
from app.services.research.extraction import DocumentExtractor
from app.services.research.planner import ResearchBudget, ResearchPlanner, ResearchStrategy
from app.services.research.policy import ResearchSecurityPolicy
from app.services.research.retrieval import SecureFetcher
from app.services.research.search import SearchManager
from app.services.research.security import ResearchSecurityScanner
from app.services.research.sources import SourceLibrary
from app.services.research.synthesis import ResearchSynthesizer
from app.services.research.telemetry import ResearchTelemetryService
from app.services.research.verification import EvidenceLedger

logger = logging.getLogger(__name__)

# Registered research capabilities exposed to the orchestrator
REGISTERED_RESEARCH_CAPABILITIES = (
    "web_search",
    "web_fetch",
    "browser_read",
    "document_extract",
    "source_evaluate",
    "claim_verify",
    "research_synthesize",
    "citation_validate",
)

# Supported controller research steps
RESEARCH_CONTROLLER_STEPS = (
    "research_search",
    "research_fetch",
    "research_browser_read",
    "research_extract",
    "research_verify",
    "research_synthesize",
    "research_citation_audit",
    "research_full_cycle",
)


class ResearchManager:
    """Central research coordinator executing research workflows."""

    def __init__(
        self,
        base_dir: Optional[Path] = None,
        security_policy: Optional[ResearchSecurityPolicy] = None,
    ):
        self.base_dir = base_dir or Path(getattr(settings, "DATA_DIR", "data"))
        self.policy = security_policy or ResearchSecurityPolicy()
        self.scanner = ResearchSecurityScanner()
        self.cache = ResearchCache(db_path=self.base_dir / "research_cache.db")
        self.search_manager = SearchManager()
        self.fetcher = SecureFetcher(policy=self.policy)
        self.browser = HeadlessBrowserService(policy=self.policy)
        self.extractor = DocumentExtractor()
        self.source_library = SourceLibrary(base_storage_dir=self.base_dir / "research_sources")
        self.evidence_ledger = EvidenceLedger()
        self.citation_engine = CitationEngine()
        self.synthesizer = ResearchSynthesizer()
        self.planner = ResearchPlanner()
        self.telemetry = ResearchTelemetryService()

    def create_or_get_budget(self, project_id: str, custom_budget: Optional[ResearchBudget] = None) -> ResearchBudget:
        """Retrieves or initializes the research budget for a project."""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT max_queries, max_docs, max_browser_seconds, max_tokens,
                       queries_used, docs_used, browser_seconds_used, tokens_used
                FROM research_budgets WHERE project_id = ?
                """,
                (project_id,)
            )
            row = cursor.fetchone()
            if row:
                return ResearchBudget(
                    max_search_queries=row[0],
                    max_retrieved_documents=row[1],
                    max_browsing_seconds=row[2],
                    max_inference_tokens=row[3],
                    queries_executed=row[4],
                    documents_retrieved=row[5],
                    browsing_seconds_used=row[6],
                    tokens_consumed=row[7],
                )
            # Create new
            b = custom_budget or ResearchBudget()
            now = datetime.now(timezone.utc).isoformat()
            cursor.execute(
                """
                INSERT OR REPLACE INTO research_budgets (
                    id, project_id, max_queries, max_docs,
                    max_browser_seconds, max_tokens,
                    queries_used, docs_used, browser_seconds_used, tokens_used,
                    is_exceeded, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    f"rbgt-{project_id}", project_id, b.max_search_queries, b.max_retrieved_documents,
                    b.max_browsing_seconds, b.max_inference_tokens,
                    b.queries_executed, b.documents_retrieved, b.browsing_seconds_used, b.tokens_consumed,
                    now
                )
            )
            conn.commit()
            return b
        finally:
            conn.close()

    def update_budget(self, project_id: str, budget: ResearchBudget) -> None:
        """Saves current budget counters to the database."""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                UPDATE research_budgets SET
                    queries_used = ?, docs_used = ?,
                    browser_seconds_used = ?, tokens_used = ?,
                    updated_at = ?
                WHERE project_id = ?
                """,
                (
                    budget.queries_executed, budget.documents_retrieved,
                    round(budget.browsing_seconds_used, 2), budget.tokens_consumed,
                    datetime.now(timezone.utc).isoformat(), project_id
                )
            )
            conn.commit()
        finally:
            conn.close()

    def execute_search_step(
        self,
        project_id: str,
        task_id: str,
        queries: List[str],
        max_results_per_query: int = 5,
    ) -> Dict[str, Any]:
        """Executes search queries and persists results within budget."""
        budget = self.create_or_get_budget(project_id)
        start_time = time.time()
        all_results = []

        for q in queries:
            if not budget.can_search():
                logger.warning("Project %s search budget exhausted.", project_id)
                break
            res = self.search_manager.search(project_id, q, max_results=max_results_per_query, research_task_id=task_id)
            budget.queries_executed += 1
            all_results.extend(res.results)

        self.update_budget(project_id, budget)
        duration_ms = (time.time() - start_time) * 1000
        self.telemetry.record_event(
            event_type="search",
            project_id=project_id,
            task_id=task_id,
            duration_ms=duration_ms,
            bytes_processed=len(str(all_results)),
            metadata={"queries_count": len(queries), "results_count": len(all_results)}
        )

        return {
            "status": "success",
            "results_count": len(all_results),
            "results": [r.to_dict() for r in all_results],
        }

    def execute_fetch_and_extract_step(
        self,
        project_id: str,
        task_id: str,
        search_results: List[Dict[str, Any]],
        use_browser_fallback: bool = True,
    ) -> Dict[str, Any]:
        """Retrieves web pages, extracts clean document content, and registers sources."""
        budget = self.create_or_get_budget(project_id)
        start_time = time.time()
        ingested_sources = []

        for item in search_results:
            if not budget.can_retrieve():
                logger.warning("Project %s retrieval document budget reached.", project_id)
                break

            url = item.get("url")
            if not url:
                continue

            query = item.get("search_query", "")
            provider = item.get("provider", "unknown")

            # 1. First attempt secure HTTP fetch
            fetch_res = self.fetcher.fetch(url)
            text_content = ""
            title = item.get("title", "")
            meta: Dict[str, Any] = {}

            if fetch_res.status == "success":
                doc = self.extractor.extract(fetch_res.content_bytes, content_type=fetch_res.content_type, base_url=fetch_res.final_url)
                text_content = doc.text
                title = doc.title or title
                meta = doc.metadata

                # Check if SPA fallback is needed
                if doc.is_spa_placeholder and use_browser_fallback and budget.browsing_seconds_used < budget.max_browsing_seconds:
                    logger.info("SPA detected for %s, falling back to headless browser...", url)
                    b_start = time.time()
                    b_res = self.browser.browse_and_extract(url)
                    b_elapsed = time.time() - b_start
                    budget.browsing_seconds_used += b_elapsed
                    if b_res.status == "success":
                        text_content = b_res.extracted_text
                        title = b_res.title or title

            elif use_browser_fallback and budget.browsing_seconds_used < budget.max_browsing_seconds:
                # Direct browser fallback if HTTP returned 403 / JS barrier
                b_start = time.time()
                b_res = self.browser.browse_and_extract(url)
                b_elapsed = time.time() - b_start
                budget.browsing_seconds_used += b_elapsed
                if b_res.status == "success":
                    text_content = b_res.extracted_text
                    title = b_res.title or title

            if not text_content or len(text_content.strip()) < 80:
                logger.warning("Failed to obtain sufficient body content from %s", url)
                continue

            # Ingest into persistent project source library
            source_rec = self.source_library.add_source(
                project_id=project_id,
                url=url,
                title=title,
                raw_content=text_content.encode("utf-8"),
                extracted_text=text_content,
                canonical_url=fetch_res.final_url if fetch_res.status == "success" else url,
                content_type=fetch_res.content_type if fetch_res.status == "success" else "text/html",
                research_task_id=task_id,
                search_query=query,
                search_provider=provider,
                metadata=meta,
            )

            budget.documents_retrieved += 1
            ingested_sources.append(source_rec)

        self.update_budget(project_id, budget)
        duration_ms = (time.time() - start_time) * 1000
        self.telemetry.record_event(
            event_type="fetch",
            project_id=project_id,
            task_id=task_id,
            duration_ms=duration_ms,
            bytes_processed=sum(len(s.get("extracted_text", "")) for s in ingested_sources),
            metadata={"retrieved_sources": len(ingested_sources)},
        )

        return {
            "status": "success",
            "sources_count": len(ingested_sources),
            "sources": ingested_sources,
        }

    def execute_verify_step(
        self,
        project_id: str,
        task_id: str,
        claims: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Validates factual claims against retrieved evidence in the EvidenceLedger."""
        start_time = time.time()
        tracked_claims = []

        for c_data in claims:
            c_text = c_data.get("claim_text", "")
            c_type = c_data.get("claim_type", "fact")
            if not c_text:
                continue

            claim = self.evidence_ledger.add_claim(project_id, c_text, claim_type=c_type)
            # Add evidence associations
            for ev in c_data.get("evidence", []):
                src_id = ev.get("source_id")
                passage = ev.get("passage", "")
                if src_id and passage:
                    self.evidence_ledger.add_evidence(
                        project_id=project_id,
                        claim_id=claim.claim_id,
                        source_id=src_id,
                        supporting_passage=passage,
                        confidence_score=ev.get("confidence", 0.9),
                    )

            # Auto-corroborate
            updated = self.evidence_ledger.corroborate_claim(claim.claim_id)
            tracked_claims.append(updated.to_dict())

        duration_ms = (time.time() - start_time) * 1000
        self.telemetry.record_event(
            event_type="verify",
            project_id=project_id,
            task_id=task_id,
            duration_ms=duration_ms,
            metadata={"claims_verified": len(tracked_claims)},
        )

        return {
            "status": "success",
            "claims_count": len(tracked_claims),
            "claims": tracked_claims,
        }

    def execute_synthesis_and_citation_step(
        self,
        project_id: str,
        task_id: str,
        objective: str,
        topic: str,
    ) -> Dict[str, Any]:
        """Synthesizes structured research report and audits all citations."""
        start_time = time.time()

        # 1. Fetch sources and verified claims from DB
        sources = self.source_library.list_sources(project_id)
        claims = self.evidence_ledger.list_claims(project_id)

        # 2. Synthesize initial report
        report_md = self.synthesizer.synthesize_report(
            project_id=project_id,
            topic=topic,
            objective=objective,
            sources=sources,
            claims=claims,
        )

        # 3. Deterministically audit citations
        audit = self.citation_engine.validate_report_citations(project_id, report_md, sources)

        # 4. Record citations in DB
        self.citation_engine.record_citations(project_id, task_id, audit)

        duration_ms = (time.time() - start_time) * 1000
        self.telemetry.record_event(
            event_type="cite",
            project_id=project_id,
            task_id=task_id,
            duration_ms=duration_ms,
            status="success" if audit["valid"] else "failure",
            metadata={"citation_valid": audit["valid"], "total_citations": audit["total_citations"]},
        )

        return {
            "status": "success" if audit["valid"] else "citation_errors",
            "report_markdown": report_md,
            "audit": audit,
        }

    def run_full_cycle(
        self,
        project_id: str,
        objective: str,
        task_id: str = "research_full_cycle",
    ) -> Dict[str, Any]:
        """Executes a complete autonomous end-to-end research cycle.

        Objective -> Planning -> Search -> Fetch -> Extract -> Verify -> Synthesize -> Citation Audit.
        """
        logger.info("Starting autonomous research cycle for project %s: %s", project_id, objective[:80])

        # 1. Strategy formulation
        strategy = self.planner.generate_strategy(objective)

        # 2. Search
        search_res = self.execute_search_step(project_id, task_id, strategy.initial_queries)
        raw_results = search_res.get("results", [])

        # 3. Fetch & Extract
        fetch_res = self.execute_fetch_and_extract_step(project_id, task_id, raw_results)
        ingested_sources = fetch_res.get("sources", [])

        # 4. Extract claims from sources
        candidate_claims = []
        for src in ingested_sources:
            text = src.get("extracted_text", "")
            paras = [p.strip() for p in text.split("\n\n") if len(p.strip()) > 80][:3]
            for p in paras:
                # Formulate a factual claim statement from paragraph
                first_sent = p.split(".")[0].strip() + "."
                candidate_claims.append({
                    "claim_text": first_sent,
                    "claim_type": "fact",
                    "evidence": [{
                        "source_id": src["source_id"],
                        "passage": p[:300],
                        "confidence": 0.9,
                    }]
                })

        # 5. Verify claims
        verify_res = self.execute_verify_step(project_id, task_id, candidate_claims[:10])

        # 6. Synthesize & audit
        synth_res = self.execute_synthesis_and_citation_step(
            project_id=project_id,
            task_id=task_id,
            objective=objective,
            topic=strategy.main_question,
        )

        return {
            "status": "completed",
            "strategy": strategy.to_dict(),
            "searches_executed": len(strategy.initial_queries),
            "sources_ingested": len(ingested_sources),
            "claims_tracked": verify_res.get("claims_count", 0),
            "report_markdown": synth_res.get("report_markdown", ""),
            "citation_audit": synth_res.get("audit", {}),
        }
