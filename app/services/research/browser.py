"""
Headless Browser Automation Service.

Provides headless browser rendering for JavaScript-heavy or SPA websites via Playwright:
- Ephemeral, isolated browser context per research task (zero cross-project cookie/cache leaks)
- Route-level network request interception enforcing SSRF and private-network security policy
- Extraction of dynamically rendered DOM text, tables, and document metadata
- Strict resource caps: execution timeouts, disabled extensions, unprivileged sandbox
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from app.services.research.cache import compute_content_hash, normalize_canonical_url
from app.services.research.policy import ResearchSecurityPolicy, get_security_policy

logger = logging.getLogger("qwen_collective.research.browser")


@dataclass
class BrowserRenderResult:
    """Result of a headless browser page render and extraction."""
    url: str
    canonical_url: str
    title: str
    rendered_html: str
    extracted_text: str
    status_code: int
    duration_ms: float
    content_hash: str
    is_success: bool = True
    error_message: Optional[str] = None
    tables: List[List[List[str]]] = None

    def __post_init__(self):
        if self.tables is None:
            self.tables = []
        if not self.content_hash and self.rendered_html:
            self.content_hash = compute_content_hash(self.rendered_html)


    @property
    def status(self) -> str:
        return "success" if self.is_success else "error"

    @property
    def error(self) -> Optional[str]:
        return self.error_message


class HeadlessBrowserService:
    """Manages secure headless Chromium rendering via Playwright."""

    def __init__(
        self,
        policy: Optional[ResearchSecurityPolicy] = None,
        default_timeout_seconds: float = 15.0,
    ):
        self.policy = policy or get_security_policy()
        self.default_timeout_seconds = default_timeout_seconds

    def browse_and_extract(
        self,
        url: str,
        timeout_seconds: Optional[float] = None,
        wait_until: str = "domcontentloaded",
    ) -> BrowserRenderResult:
        """Alias for render_page."""
        return self.render_page(url, timeout_seconds=timeout_seconds, wait_until=wait_until)

    def render_page(
        self,
        url: str,
        timeout_seconds: Optional[float] = None,
        wait_until: str = "domcontentloaded",
    ) -> BrowserRenderResult:
        """
        Navigate to a webpage in an isolated browser context, render client-side JavaScript,
        and extract visible content while enforcing route-level network security.
        """
        start_time = time.time()
        timeout = timeout_seconds or self.default_timeout_seconds
        timeout_ms = int(timeout * 1000.0)
        canonical = normalize_canonical_url(url)

        # 1. Pre-navigation security policy check
        is_valid, reason, hostname = self.policy.validate_url(url)
        if not is_valid:
            duration_ms = round((time.time() - start_time) * 1000.0, 1)
            return BrowserRenderResult(
                url=url,
                canonical_url=canonical,
                title="",
                rendered_html="",
                extracted_text="",
                status_code=403,
                duration_ms=duration_ms,
                content_hash="",
                is_success=False,
                error_message=f"Browser navigation blocked by security policy: {reason}",
            )

        is_dest_valid, dest_reason, _ = self.policy.resolve_and_verify_destination(hostname)
        if not is_dest_valid:
            duration_ms = round((time.time() - start_time) * 1000.0, 1)
            return BrowserRenderResult(
                url=url,
                canonical_url=canonical,
                title="",
                rendered_html="",
                extracted_text="",
                status_code=403,
                duration_ms=duration_ms,
                content_hash="",
                is_success=False,
                error_message=f"Browser navigation blocked by destination IP check: {dest_reason}",
            )

        # 2. Launch ephemeral Playwright browser session with outbound filtering proxy (F-14 / ADR-06)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            duration_ms = round((time.time() - start_time) * 1000.0, 1)
            logger.warning("Playwright is not installed. Secure browser fallback unavailable.")
            return BrowserRenderResult(
                url=url,
                canonical_url=canonical,
                title="",
                rendered_html="",
                extracted_text="",
                status_code=503,
                duration_ms=duration_ms,
                content_hash="",
                is_success=False,
                error_message="Secure browser egress unavailable: Playwright automation library is not installed.",
            )

        try:
            from app.services.research.browser_proxy import OutboundFilteringProxy
            proxy = OutboundFilteringProxy(policy=self.policy)
            proxy.start()
        except Exception as proxy_err:
            duration_ms = round((time.time() - start_time) * 1000.0, 1)
            logger.error("Failed to initialize outbound filtering proxy for browser: %s", proxy_err)
            return BrowserRenderResult(
                url=url,
                canonical_url=canonical,
                title="",
                rendered_html="",
                extracted_text="",
                status_code=503,
                duration_ms=duration_ms,
                content_hash="",
                is_success=False,
                error_message=f"Secure browser egress unavailable: egress filter proxy initialization failed: {proxy_err}",
            )

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    proxy={"server": proxy.proxy_url, "bypass": ""},
                    args=[
                        f"--proxy-server={proxy.proxy_url}",
                        "--proxy-bypass-list=<-loopback>",
                        "--disable-gpu",
                        "--disable-dev-shm-usage",
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--disable-extensions",
                    ],
                )
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 (QwenCollective-Headless/1.0)",
                    java_script_enabled=True,
                    bypass_csp=False,
                    ignore_https_errors=False,
                    accept_downloads=False,
                )

                # Route-level interceptor: abort any internal or prohibited network requests from page scripts (F-14)
                def _route_filter(route, request):
                    req_url = request.url
                    try:
                        p_host = (urlparse(req_url).hostname or "").lower()
                        # Only allow external public requests
                        if p_host:
                            val_ok, val_reason, _ = self.policy.validate_url(req_url)
                            if not val_ok:
                                logger.warning("Browser route filter blocked URL %s: %s", req_url, val_reason)
                                route.abort()
                                return
                            dest_ok, dest_reason, _ = self.policy.resolve_and_verify_destination(p_host)
                            if not dest_ok:
                                logger.warning("Browser route filter blocked destination IP for %s: %s", p_host, dest_reason)
                                route.abort()
                                return
                    except Exception as filter_ex:
                        logger.warning("Browser route filter exception on %s: %s", req_url, filter_ex)
                        route.abort()
                        return
                    route.continue_()

                context.route("**/*", _route_filter)

                page = context.new_page()
                page.set_default_timeout(timeout_ms)

                try:
                    response = page.goto(url, wait_until=wait_until, timeout=timeout_ms)
                    status_code = response.status if response else 200
                except Exception as nav_err:
                    duration_ms = round((time.time() - start_time) * 1000.0, 1)
                    browser.close()
                    return BrowserRenderResult(
                        url=url,
                        canonical_url=canonical,
                        title="",
                        rendered_html="",
                        extracted_text="",
                        status_code=504,
                        duration_ms=duration_ms,
                        content_hash="",
                        is_success=False,
                        error_message=f"Browser navigation error: {nav_err}",
                    )

                # Extract rendered page details
                title = page.title() or ""
                rendered_html = page.content() or ""

                # Extract visible body text (excluding script, style, noscript)
                extracted_text = page.evaluate("""() => {
                    const clone = document.body.cloneNode(true);
                    const removeTags = ['script', 'style', 'noscript', 'iframe', 'svg'];
                    removeTags.forEach(tag => {
                        const elements = clone.getElementsByTagName(tag);
                        while (elements[0]) elements[0].parentNode.removeChild(elements[0]);
                    });
                    return clone.innerText || clone.textContent || '';
                }""")

                # Extract HTML tables if present
                tables_data = page.evaluate("""() => {
                    const results = [];
                    const tables = document.querySelectorAll('table');
                    tables.forEach(t => {
                        const tableRows = [];
                        t.querySelectorAll('tr').forEach(tr => {
                            const row = [];
                            tr.querySelectorAll('th, td').forEach(td => {
                                row.push(td.innerText.trim());
                            });
                            if (row.length > 0) tableRows.push(row);
                        });
                        if (tableRows.length > 0) results.push(tableRows);
                    });
                    return results;
                }""")

                browser.close()

                duration_ms = round((time.time() - start_time) * 1000.0, 1)
                return BrowserRenderResult(
                    url=url,
                    canonical_url=canonical,
                    title=title,
                    rendered_html=rendered_html,
                    extracted_text=extracted_text,
                    status_code=status_code,
                    duration_ms=duration_ms,
                    content_hash=compute_content_hash(rendered_html),
                    is_success=True,
                    tables=tables_data,
                )

        except Exception as e:
            duration_ms = round((time.time() - start_time) * 1000.0, 1)
            logger.error("Headless browser execution failed for %s: %s", url, e)
            return BrowserRenderResult(
                url=url,
                canonical_url=canonical,
                title="",
                rendered_html="",
                extracted_text="",
                status_code=500,
                duration_ms=duration_ms,
                content_hash="",
                is_success=False,
                error_message=f"Headless browser error: {e}",
            )
        finally:
            proxy.close()
