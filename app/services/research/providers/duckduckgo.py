"""
DuckDuckGo Search Provider.

Zero-configuration public web search provider.
Primary implementation: `duckduckgo_search` library.
Fallback: Lightweight HTML parsing of DuckDuckGo results.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

import httpx

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

from app.services.research.providers.base import QueryResult, SearchProvider, SearchResult
from app.services.research.retrieval import PinningHTTPTransport

logger = logging.getLogger("qwen_collective.research.providers.duckduckgo")


class DuckDuckGoProvider(SearchProvider):
    """Real public internet search provider backed by DuckDuckGo."""

    def __init__(self, name: str = "duckduckgo", timeout_seconds: float = 12.0):
        super().__init__(name=name)
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
        effective_query = query
        if domains:
            site_clauses = " OR ".join(f"site:{d.strip()}" for d in domains if d.strip())
            if site_clauses:
                effective_query = f"{query} ({site_clauses})"

        # Attempt 1: ddgs / duckduckgo_search library
        try:
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS

            with DDGS(timeout=int(self.timeout_seconds)) as ddgs:
                ddg_gen = ddgs.text(
                    effective_query,
                    safesearch="moderate",
                    timelimit=time_range or None,
                    max_results=max_results,
                )
                raw_results = list(ddg_gen) if ddg_gen else []

            results: List[SearchResult] = []
            for rank, r in enumerate(raw_results, start=1):
                url = r.get("href") or r.get("url") or ""
                title = r.get("title") or ""
                body = r.get("body") or r.get("snippet") or ""
                if url:
                    results.append(SearchResult(
                        title=title,
                        url=url,
                        snippet=body,
                        provider=self.name,
                        rank=rank,
                    ))

            duration_ms = round((time.time() - start_time) * 1000.0, 1)
            logger.info("DuckDuckGo text search returned %d results in %.1f ms", len(results), duration_ms)
            return QueryResult(
                query=query,
                provider=self.name,
                results=results,
                total_results=len(results),
                duration_ms=duration_ms,
                is_success=True,
            )
        except Exception as ddg_err:
            logger.warning("DDGS library failed (%s), attempting fallback HTML fetch", ddg_err)

        # Attempt 2: Direct HTTP fallback to DuckDuckGo HTML / Lite
        try:
            import html as html_lib
            import re
            from urllib.parse import parse_qs, urlparse

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://html.duckduckgo.com/",
            }
            transport = PinningHTTPTransport(trust_env=False)
            resp = None
            with httpx.Client(transport=transport, timeout=self.timeout_seconds, follow_redirects=True) as client:
                try:
                    resp = client.post("https://html.duckduckgo.com/html/", data={"q": effective_query}, headers=headers)
                    if resp.status_code == 202 or len(resp.text) < 500:
                        raise ValueError("HTML endpoint returned challenge/empty response")
                    resp.raise_for_status()
                except Exception as html_err:
                    logger.debug("DuckDuckGo html post failed (%s), trying lite post", html_err)
                    lite_headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                        "Accept-Language": "en-US,en;q=0.9",
                        "Referer": "https://lite.duckduckgo.com/",
                    }
                    resp = client.post("https://lite.duckduckgo.com/lite/", data={"q": effective_query}, headers=lite_headers)
                    resp.raise_for_status()

            results = []
            # Extract result blocks
            blocks = re.findall(
                r"<div[^>]*class=[\"'][^\"']*result\s+results_links[\s\S]*?(?=<div[^>]*class=[\"'][^\"']*result\s+results_links|<div[^>]*class=[\"']results_links_more|$)",
                resp.text,
            )

            if blocks:
                for rank, block in enumerate(blocks, start=1):
                    if rank > max_results:
                        break
                    a_match = re.search(r"<a[^>]+class=[\"'][^\"']*result__a[^\"]*[\"'][^>]+href=[\"']([^\"]+)[\"'][^>]*>([\s\S]*?)</a>", block)
                    if not a_match:
                        a_match = re.search(r"<a[^>]+class=[\"'][^\"']*result__url[^\"]*[\"'][^>]+href=[\"']([^\"]+)[\"'][^>]*>([\s\S]*?)</a>", block)
                    if not a_match:
                        continue
                    raw_url, raw_title = a_match.groups()
                    target_url = raw_url
                    if "uddg=" in target_url:
                        parsed_redirect = parse_qs(urlparse(target_url).query)
                        if "uddg" in parsed_redirect:
                            target_url = parsed_redirect["uddg"][0]
                    if target_url.startswith("//"):
                        target_url = "https:" + target_url

                    title = html_lib.unescape(re.sub(r"<[^>]+>", "", raw_title)).strip()
                    s_match = re.search(r"<(?:a|td|div)[^>]+class=[\"'][^\"']*result__snippet[^\"]*[\"'][^>]*>([\s\S]*?)</(?:a|td|div)>", block)
                    snippet = html_lib.unescape(re.sub(r"<[^>]+>", "", s_match.group(1))).strip() if s_match else ""

                    results.append(SearchResult(
                        title=title or "Search Result",
                        url=target_url,
                        snippet=snippet,
                        provider=self.name,
                        rank=rank,
                    ))
            else:
                # Fallback to direct anchor regex
                items = re.findall(
                    r"<a[^>]*class=[\"'][^\"']*(?:result-link|result__title|result__a)[^\"']*[\"'][^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
                    resp.text,
                    re.IGNORECASE | re.DOTALL,
                )
                snippets = re.findall(
                    r"class=[\"'][^\"']*(?:result-snippet|result__snippet)[^\"']*[\"'][^>]*>(.*?)<",
                    resp.text,
                    re.IGNORECASE | re.DOTALL,
                )
                for rank, (raw_url, raw_title) in enumerate(items, start=1):
                    if rank > max_results:
                        break
                    target_url = raw_url
                    if "uddg=" in target_url:
                        parsed_redirect = parse_qs(urlparse(target_url).query)
                        if "uddg" in parsed_redirect:
                            target_url = parsed_redirect["uddg"][0]
                    if target_url.startswith("//"):
                        target_url = "https:" + target_url

                    title = html_lib.unescape(re.sub(r"<[^>]+>", "", raw_title)).strip()
                    snippet = ""
                    if rank - 1 < len(snippets):
                        snippet = html_lib.unescape(snippets[rank - 1]).strip()

                    results.append(SearchResult(
                        title=title or "Search Result",
                        url=target_url,
                        snippet=snippet,
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
        except Exception as html_err:
            duration_ms = round((time.time() - start_time) * 1000.0, 1)
            logger.error("DuckDuckGo fallback also failed: %s", html_err)
            return QueryResult(
                query=query,
                provider=self.name,
                results=[],
                total_results=0,
                duration_ms=duration_ms,
                error_message=f"DuckDuckGo search error: {html_err}",
                is_success=False,
            )

    def health_check(self) -> Tuple[bool, str]:
        try:
            res = self.search("test", max_results=1)
            if res.is_success and len(res.results) > 0:
                return True, "DuckDuckGo is operational."
            return False, res.error_message or "No results returned."
        except Exception as e:
            return False, str(e)
