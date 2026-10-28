"""
Research Cache & URL Normalization.

Provides:
1. Canonical URL normalization (removing tracking tags, fragments, default ports).
2. Content-addressable SHA-256 calculation for deduplication and versioning.
3. In-memory / persistent cache for query results and document extractions.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# Query parameters commonly used for tracking that do not affect page content
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "ref", "source",
}


def normalize_canonical_url(raw_url: str) -> str:
    """
    Produce a clean canonical URL representation for deduplication:
    - Lowercases scheme and hostname
    - Strips default ports (80 for http, 443 for https)
    - Strips URL fragments (#...)
    - Filters out marketing tracking query parameters (utm_*, fbclid, etc.)
    - Removes trailing slashes on paths (unless root)
    """
    if not raw_url:
        return ""
    try:
        parsed = urlparse(raw_url.strip())
    except Exception:
        return raw_url.strip().lower()

    scheme = (parsed.scheme or "http").lower()
    netloc = (parsed.netloc or "").lower()

    # Strip default ports from netloc
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    elif scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]

    path = parsed.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]

    # Filter query parameters
    query = ""
    if parsed.query:
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        filtered = [(k, v) for k, v in pairs if k.lower() not in TRACKING_PARAMS]
        filtered.sort(key=lambda x: x[0])
        query = urlencode(filtered)

    return urlunparse((scheme, netloc, path, "", query, ""))


def compute_content_hash(data: str | bytes) -> str:
    """Compute standard SHA-256 hash for content-addressable storage and change detection."""
    if isinstance(data, str):
        data = data.encode("utf-8", errors="replace")
    return hashlib.sha256(data).hexdigest()


class ResearchCache:
    """Lightweight TTL-aware in-memory cache for search queries and HTTP responses."""

    def __init__(self, default_ttl_seconds: float = 3600.0, db_path: Optional[Any] = None):
        self.default_ttl_seconds = default_ttl_seconds
        self.db_path = db_path
        self._cache: Dict[str, Dict[str, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        entry = self._cache.get(key)
        if not entry:
            return None
        if time.time() > entry["expires_at"]:
            del self._cache[key]
            return None
        return entry["value"]

    def set(self, key: str, value: Any, ttl_seconds: Optional[float] = None) -> None:
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds
        self._cache[key] = {
            "value": value,
            "expires_at": time.time() + ttl,
        }

    def clear(self) -> None:
        self._cache.clear()
