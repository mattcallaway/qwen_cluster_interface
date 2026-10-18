from app.services.research.providers.base import QueryResult, SearchProvider, SearchResult
from app.services.research.providers.duckduckgo import DuckDuckGoProvider
from app.services.research.providers.searxng import SearXNGProvider
from app.services.research.providers.api_provider import ApiSearchProvider

__all__ = [
    "SearchResult",
    "QueryResult",
    "SearchProvider",
    "DuckDuckGoProvider",
    "SearXNGProvider",
    "ApiSearchProvider",
]
