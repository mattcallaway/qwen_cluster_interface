"""
Dedicated REST Search API Provider.

Supports commercial or custom search APIs (e.g., Brave Search, SerpAPI, Tavily).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.services.research.providers.base import QueryResult, SearchProvider, SearchResult

logger = logging.getLogger("qwen_collective.research.providers.api_provider")


class ApiSearchProvider(SearchProvider):
    """Generic search provider communicating with a REST JSON search API."""

    def __init__(
        self,
        api_url: str,
        api_key: str,
        provider_name: str = "custom_api",
        timeout_seconds: float = 12.0,
    ):
        super().__init__(name=provider_name)
        self.api_url = api_url
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

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
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "X-Subscription-Token": self.api_key, # Compatibility with Brave
        }
        params: Dict[str, Any] = {
            "q": query,
            "count": max_results,
            "offset": (page - 1) * max_results,
        }

        try:
            with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
                resp = client.get(self.api_url, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            results: List[SearchResult] = []
            # Extract standard web results from common schemas
            items = data.get("web", {}).get("results") or data.get("results") or data.get("organic_results") or []
            for rank, item in enumerate(items[:max_results], start=1):
                url = item.get("url") or item.get("link") or ""
                if not url:
                    continue
                results.append(SearchResult(
                    title=item.get("title") or "",
                    url=url,
                    snippet=item.get("description") or item.get("snippet") or "",
                    provider=self.name,
                    rank=rank,
                ))

            duration_ms = round((time.time() - start_time) * 1000.0, 1)
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
            logger.warning("API Search failed: %s", e)
            return QueryResult(
                query=query,
                provider=self.name,
                results=[],
                total_results=0,
                duration_ms=duration_ms,
                error_message=f"API Search error: {e}",
                is_success=False,
            )

    def health_check(self) -> Tuple[bool, str]:
        if not self.api_key:
            return False, "API key is not configured."
        return True, "API search provider configured."
