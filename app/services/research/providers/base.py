"""
Search Provider Abstraction & Data Models.

Defines the generic interface that all search providers (DuckDuckGo, SearXNG,
Brave, SerpAPI, etc.) must implement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from app.services.research.cache import normalize_canonical_url


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SearchResult:
    """Standardized search result schema conforming to Qwen Collective requirements."""
    title: str
    url: str
    snippet: str
    provider: str
    rank: int = 1
    published_at: Optional[str] = None
    retrieved_at: str = field(default_factory=_utc_now_iso)
    canonical_url: str = ""
    domain: str = ""
    language: str = "en"
    score: float = 1.0

    def __post_init__(self):
        if not self.canonical_url and self.url:
            self.canonical_url = normalize_canonical_url(self.url)
        if not self.domain and self.url:
            try:
                self.domain = (urlparse(self.url).hostname or "").lower()
            except Exception:
                self.domain = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class QueryResult:
    """The aggregate response from a search provider query."""
    query: str
    provider: str
    results: List[SearchResult]
    total_results: int
    duration_ms: float
    error_message: Optional[str] = None
    is_success: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "provider": self.provider,
            "results": [r.to_dict() for r in self.results],
            "total_results": self.total_results,
            "duration_ms": self.duration_ms,
            "error_message": self.error_message,
            "is_success": self.is_success,
        }


class SearchProvider(ABC):
    """Abstract base class for all search provider integrations."""

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def search(
        self,
        query: str,
        max_results: int = 10,
        page: int = 1,
        language: str = "en",
        time_range: Optional[str] = None,
        domains: Optional[List[str]] = None,
    ) -> QueryResult:
        """Execute a search query synchronously."""
        pass

    @abstractmethod
    def health_check(self) -> Tuple[bool, str]:
        """Verify provider availability and operational readiness."""
        pass
