"""
SearXNG Metasearch Provider.

Connects to a configured SearXNG instance using its JSON API (`/search?format=json`).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.services.research.providers.base import QueryResult, SearchProvider, SearchResult
from app.services.research.retrieval import PinningHTTPTransport

logger = logging.getLogger("qwen_collective.research.providers.searxng")


class SearXNGProvider(SearchProvider):
    """SearXNG JSON API search provider."""

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        name: str = "searxng",
        timeout_seconds: float = 12.0,
        api_key: Optional[str] = None,
    ):
        super().__init__(name=name)
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.api_key = api_key

    def search(
        self,
        query: str,
        max_results: int = 10,
        page: int = 1,
        language: str = "en",
        time_range: Optional[str] = None,
        domains: Optional[List[str]] = None,
    ) -> QueryResult:
        start_time = time.time()
        endpoint = f"{self.base_url}/search"
        params: Dict[str, Any] = {
            "q": query,
            "format": "json",
            "pageno": page,
            "language": language,
        }
        if time_range:
            params["time_range"] = time_range

        headers: Dict[str, str] = {
            "User-Agent": "QwenCollective-ResearchEngine/1.0",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            transport = PinningHTTPTransport(trust_env=False)
            with httpx.Client(transport=transport, timeout=self.timeout_seconds, follow_redirects=True) as client:
                resp = client.get(endpoint, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            raw_results = data.get("results") or []
            results: List[SearchResult] = []
            for rank, item in enumerate(raw_results[:max_results], start=1):
                url = item.get("url") or ""
                if not url:
                    continue
                results.append(SearchResult(
                    title=item.get("title") or "",
                    url=url,
                    snippet=item.get("content") or "",
                    provider=self.name,
                    rank=rank,
                    published_at=item.get("publishedDate"),
                    score=float(item.get("score") or 1.0),
                ))

            duration_ms = round((time.time() - start_time) * 1000.0, 1)
            logger.info("SearXNG returned %d results in %.1f ms", len(results), duration_ms)
            return QueryResult(
                query=query,
                provider=self.name,
                results=results,
                total_results=len(results),
                duration_ms=duration_ms,
                is_success=True,
            )
        except Exception as e:
            duration_ms = round((time.time() - start_time) * 1000.0, 1)
            logger.warning("SearXNG search failed: %s", e)
            return QueryResult(
                query=query,
                provider=self.name,
                results=[],
                total_results=0,
                duration_ms=duration_ms,
                error_message=f"SearXNG query error: {e}",
                is_success=False,
            )

    def health_check(self) -> Tuple[bool, str]:
        try:
            with httpx.Client(timeout=4.0) as client:
                resp = client.get(f"{self.base_url}/healthz")
                if resp.status_code == 200:
                    return True, "SearXNG is operational."
                return False, f"HTTP status {resp.status_code}"
        except Exception as e:
            return False, f"SearXNG connection error: {e}"
