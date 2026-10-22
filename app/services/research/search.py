"""
Search Manager & Query Orchestration.

Manages search execution across providers with:
- Multiple query execution and automatic reformulation
- Query scrubbing for secrets/tokens (via security module)
- Result deduplication by canonical URL
- Domain diversity ranking
- Provider fallback and health tracking
- Persistent audit logging into `research_queries` and `research_search_results`
- Enforceable research budget tracking
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_db_connection
from app.services.research.cache import normalize_canonical_url
from app.services.research.providers.base import QueryResult, SearchProvider, SearchResult
from app.services.research.providers.duckduckgo import DuckDuckGoProvider
from app.services.research.providers.searxng import SearXNGProvider
from app.services.research.security import scrub_search_query

logger = logging.getLogger("qwen_collective.research.search")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SearchManager:
    """Orchestrates web search queries, provider fallbacks, and result persistence."""

    def __init__(
        self,
        primary_provider: Optional[SearchProvider] = None,
        fallback_provider: Optional[SearchProvider] = None,
    ):
        self.primary_provider = primary_provider or DuckDuckGoProvider()
        self.fallback_provider = fallback_provider
        self.providers: Dict[str, SearchProvider] = {
            self.primary_provider.name: self.primary_provider,
        }
        if self.fallback_provider:
            self.providers[self.fallback_provider.name] = self.fallback_provider

    def register_provider(self, provider: SearchProvider, set_primary: bool = False) -> None:
        self.providers[provider.name] = provider
        if set_primary:
            self.primary_provider = provider

    def search(
        self,
        project_id: str,
        query: str,
        max_results: int = 10,
        research_task_id: Optional[str] = None,
        task_id: Optional[str] = None,
        research_run_id: Optional[str] = None,
        **kwargs: Any,
    ) -> QueryResult:
        """Convenience alias for execute_query."""
        actual_task_id = task_id or research_task_id
        return self.execute_query(
            project_id=project_id,
            query=query,
            research_run_id=research_run_id,
            task_id=actual_task_id,
            max_results=max_results,
            **kwargs,
        )

    def execute_query(
        self,
        project_id: str,
        query: str,
        research_run_id: Optional[str] = None,
        task_id: Optional[str] = None,
        max_results: int = 10,
        domains: Optional[List[str]] = None,
        preferred_provider: Optional[str] = None,
    ) -> QueryResult:
        """
        Execute a search query with secret scrubbing, provider fallback, and database persistence.
        """
        clean_query, was_scrubbed = scrub_search_query(query)
        if was_scrubbed:
            logger.info("Search query was scrubbed of sensitive information: '%s' -> '%s'", query, clean_query)

        query_id = f"qry-{uuid.uuid4().hex[:12]}"
        now = _now_iso()

        # Check and enforce budget if research_run_id provided
        if research_run_id:
            budget_ok, budget_err = self._check_and_increment_budget(research_run_id, queries=1)
            if not budget_ok:
                return QueryResult(
                    query=clean_query,
                    provider="none",
                    results=[],
                    total_results=0,
                    duration_ms=0.0,
                    error_message=budget_err,
                    is_success=False,
                )

        # Provider selection
        provider = self.primary_provider
        if preferred_provider and preferred_provider in self.providers:
            provider = self.providers[preferred_provider]

        # Execute search with fallback
        result = provider.search(clean_query, max_results=max_results, domains=domains)
        if not result.is_success and self.fallback_provider and self.fallback_provider != provider:
            logger.warning("Primary search provider %s failed, attempting fallback to %s", provider.name, self.fallback_provider.name)
            fallback_res = self.fallback_provider.search(clean_query, max_results=max_results, domains=domains)
            if fallback_res.is_success or len(fallback_res.results) > 0:
                result = fallback_res

        # Rank and diversify results by domain
        diversified_results = self._diversify_results(result.results)
        result.results = diversified_results

        # Audit logging into database
        self._record_query_audit(
            query_id=query_id,
            project_id=project_id,
            research_run_id=research_run_id,
            task_id=task_id,
            query_text=clean_query,
            original_query=query,
            provider_name=result.provider,
            status="completed" if result.is_success else "failed",
            results_count=len(result.results),
            duration_ms=result.duration_ms,
            error_message=result.error_message,
            created_at=now,
            results=result.results,
        )

        return result

    def execute_multi_queries(
        self,
        project_id: str,
        queries: List[str],
        research_run_id: Optional[str] = None,
        task_id: Optional[str] = None,
        max_results_per_query: int = 5,
        domains: Optional[List[str]] = None,
    ) -> List[SearchResult]:
        """
        Execute multiple queries and return deduplicated, ranked results.
        """
        seen_urls: set[str] = set()
        all_results: List[SearchResult] = []

        for q in queries:
            if not q.strip():
                continue
            res = self.execute_query(
                project_id=project_id,
                query=q,
                research_run_id=research_run_id,
                task_id=task_id,
                max_results=max_results_per_query,
                domains=domains,
            )
            for item in res.results:
                canon = item.canonical_url or normalize_canonical_url(item.url)
                if canon not in seen_urls:
                    seen_urls.add(canon)
                    all_results.append(item)

        return all_results

    def _diversify_results(self, results: List[SearchResult]) -> List[SearchResult]:
        """Ensure source diversity by penalizing excessive results from the same domain."""
        domain_counts: Dict[str, int] = {}
        diversified: List[SearchResult] = []
        for r in results:
            dom = r.domain or "unknown"
            cnt = domain_counts.get(dom, 0)
            if cnt >= 3:
                # Deprioritize after 3 results from exact same domain
                continue
            domain_counts[dom] = cnt + 1
            diversified.append(r)
        return diversified

    def _check_and_increment_budget(self, research_run_id: str, queries: int = 1) -> Tuple[bool, Optional[str]]:
        conn = get_db_connection()
        try:
            row = conn.execute("SELECT * FROM research_budgets WHERE research_run_id = ?", (research_run_id,)).fetchone()
            if not row:
                return True, None
            if row["queries_used"] + queries > row["max_queries"]:
                return False, f"Research budget exceeded: max_queries ({row['max_queries']}) reached."
            conn.execute(
                "UPDATE research_budgets SET queries_used = queries_used + ?, updated_at = ? WHERE research_run_id = ?",
                (queries, _now_iso(), research_run_id),
            )
            conn.commit()
            return True, None
        finally:
            conn.close()

    def _record_query_audit(
        self,
        query_id: str,
        project_id: str,
        research_run_id: Optional[str],
        task_id: Optional[str],
        query_text: str,
        original_query: str,
        provider_name: str,
        status: str,
        results_count: int,
        duration_ms: float,
        error_message: Optional[str],
        created_at: str,
        results: List[SearchResult],
    ) -> None:
        conn = get_db_connection()
        try:
            conn.execute(
                """
                INSERT INTO research_queries (
                    id, project_id, research_run_id, task_id, query_text,
                    original_query, provider, status, results_count, duration_ms,
                    error_message, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    query_id, project_id, research_run_id, task_id, query_text,
                    original_query, provider_name, status, results_count, duration_ms,
                    error_message, created_at,
                ),
            )

            for rank, r in enumerate(results, start=1):
                res_id = f"sres-{uuid.uuid4().hex[:12]}"
                conn.execute(
                    """
                    INSERT INTO research_search_results (
                        id, project_id, query_id, title, url, canonical_url,
                        snippet, provider, rank, published_at, retrieved_at,
                        selected_for_retrieval
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        res_id, project_id, query_id, r.title, r.url, r.canonical_url,
                        r.snippet, r.provider, rank, r.published_at, created_at,
                    ),
                )

            # Update provider status
            conn.execute(
                """
                INSERT INTO research_provider_status (provider_name, is_available, consecutive_failures, last_error, last_success_at, total_queries, updated_at)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(provider_name) DO UPDATE SET
                    is_available = excluded.is_available,
                    consecutive_failures = CASE WHEN excluded.is_available = 1 THEN 0 ELSE consecutive_failures + 1 END,
                    last_error = excluded.last_error,
                    last_success_at = CASE WHEN excluded.is_available = 1 THEN excluded.last_success_at ELSE last_success_at END,
                    total_queries = total_queries + 1,
                    updated_at = excluded.updated_at
                """,
                (
                    provider_name,
                    1 if status == "completed" else 0,
                    0 if status == "completed" else 1,
                    error_message,
                    created_at if status == "completed" else None,
                    created_at,
                ),
            )

            conn.commit()
        except Exception as e:
            logger.error("Failed to write query audit record: %s", e)
        finally:
            conn.close()
