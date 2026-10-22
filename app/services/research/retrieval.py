"""
Secure URL Retrieval Service.

Implements streaming HTTP client with:
- Pre-connection SSRF and private-network checks via ResearchSecurityPolicy
- Per-redirect target revalidation (preventing open redirect into metadata or LAN)
- Streaming body reads with strict byte limits (preventing memory exhaustion)
- Connection and read timeouts
- Domain rate-limiting
- SPA / JavaScript rendering detection to trigger headless browser fallback
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import socket
import typing
import httpcore
from httpcore._backends.sync import SyncBackend, SyncStream
from httpcore._exceptions import ConnectError, ConnectTimeout, ExceptionMapping, map_exceptions
import httpx
from httpx._config import DEFAULT_LIMITS, create_ssl_context

from app.services.research.cache import compute_content_hash, normalize_canonical_url
from app.services.research.policy import ResearchSecurityPolicy, get_security_policy

logger = logging.getLogger("qwen_collective.research.retrieval")


class DestinationBlockedError(ConnectError):
    """Raised when destination IP or host is blocked by SSRF policy."""
    pass


class PinningSyncBackend(SyncBackend):
    """
    HTTPCore network backend that enforces socket-level destination IP verification (F-14).
    Prevents TOCTOU DNS rebinding by pinning the raw TCP socket to the verified IP address,
    while allowing TLS handshake to use the original hostname for SNI and certificate validation.
    """
    def __init__(self, policy: ResearchSecurityPolicy):
        super().__init__()
        self.policy = policy

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: typing.Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.NetworkStream:
        is_dest_valid, dest_reason, resolved_ips = self.policy.resolve_and_verify_destination(host)
        if not is_dest_valid or not resolved_ips:
            logger.warning("PinningSyncBackend blocked destination %s:%d - %s", host, port, dest_reason)
            raise DestinationBlockedError(f"Destination blocked by SSRF policy: {dest_reason}")

        pinned_ip = resolved_ips[0]

        if socket_options is None:
            socket_options = []
        address = (pinned_ip, port)
        source_address = None if local_address is None else (local_address, 0)
        exc_map: ExceptionMapping = {
            socket.timeout: ConnectTimeout,
            OSError: ConnectError,
        }

        with map_exceptions(exc_map):
            sock = socket.create_connection(
                address,
                timeout,
                source_address=source_address,
            )
            for option in socket_options:
                sock.setsockopt(*option)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return SyncStream(sock)


class PinningHTTPTransport(httpx.HTTPTransport):
    """
    HTTPX transport with socket-level IP pinning to prevent SSRF and DNS rebinding (F-14 / ADR-06).
    - Ignores proxy environment variables by default (trust_env=False) to prevent proxy bypass.
    - Disables keepalive connection reuse (keepalive_expiry=0.0) so every connection revalidates destination.
    - Preserves TLS certificate verification and SNI with original hostname.
    - Enforces destination IP policy at socket creation time.
    """
    def __init__(
        self,
        policy: Optional[ResearchSecurityPolicy] = None,
        verify: Any = True,
        cert: Any = None,
        trust_env: bool = False,
        http1: bool = True,
        http2: bool = False,
        limits: Any = None,
        retries: int = 0,
        socket_options: Any = None,
    ):
        self.policy = policy or get_security_policy()
        if limits is None:
            # Zero keepalive to prevent DNS rebinding connection reuse across queries
            limits = httpx.Limits(max_connections=50, max_keepalive_connections=0, keepalive_expiry=0.0)
        super().__init__(
            verify=verify,
            cert=cert,
            trust_env=trust_env,
            http1=http1,
            http2=http2,
            limits=limits,
            retries=retries,
            socket_options=socket_options,
        )
        ssl_context = create_ssl_context(verify=verify, cert=cert, trust_env=trust_env)
        self._pool = httpcore.ConnectionPool(
            ssl_context=ssl_context,
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            http1=http1,
            http2=http2,
            retries=retries,
            socket_options=socket_options,
            network_backend=PinningSyncBackend(policy=self.policy),
        )

# Indicators that page is a JavaScript Single Page App needing browser automation
SPA_INDICATORS = [
    "you need to enable javascript to run this app",
    "please enable javascript to continue",
    "enable javascript and refresh the page",
    "<noscript>",
    "id=\"root\"></div>",
    "id=\"app\"></div>",
    "id=\"__next\"></div>",
]


@dataclass
class FetchResult:
    """Result of an HTTP document retrieval."""
    url: str
    canonical_url: str
    status_code: int
    content_bytes: bytes
    content_type: str
    headers: Dict[str, str]
    duration_ms: float
    content_hash: str
    is_success: bool = True
    error_message: Optional[str] = None
    redirect_chain: List[str] = None
    needs_browser_fallback: bool = False

    def __post_init__(self):
        if self.redirect_chain is None:
            self.redirect_chain = []
        if not self.content_hash and self.content_bytes:
            self.content_hash = compute_content_hash(self.content_bytes)

    @property
    def status(self) -> str:
        return "success" if self.is_success else "refused" if self.status_code in (403, 413, 429) else "error"

    @property
    def final_url(self) -> str:
        return self.redirect_chain[-1] if self.redirect_chain else self.url

    @property
    def error(self) -> str:
        return self.error_message or ""


class SecureFetcher:
    """Secure, streaming HTTP fetcher for reading public documents."""

    def __init__(
        self,
        policy: Optional[ResearchSecurityPolicy] = None,
        user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 (QwenCollective-Research/1.0)",
    ):
        self.policy = policy or get_security_policy()
        self.user_agent = user_agent

    def fetch(
        self,
        url: str,
        timeout_seconds: Optional[float] = None,
        max_bytes: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> FetchResult:
        """
        Fetch a public document with pre-connection SSRF screening, redirect revalidation,
        and streaming byte-budget enforcement.
        """
        start_time = time.time()
        timeout = timeout_seconds or self.policy.request_timeout_seconds
        byte_limit = max_bytes or self.policy.max_response_bytes
        canonical = normalize_canonical_url(url)
        redirect_chain: List[str] = [url]

        current_url = url
        redirects_followed = 0

        req_headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf;q=0.8,text/plain;q=0.8,*/*;q=0.7",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if headers:
            req_headers.update(headers)

        while redirects_followed <= self.policy.max_redirects:
            # 1. URL syntax and policy check
            is_valid, reason, hostname = self.policy.validate_url(current_url)
            if not is_valid:
                duration_ms = round((time.time() - start_time) * 1000.0, 1)
                logger.warning("SSRF / Policy violation blocked URL %s: %s", current_url, reason)
                return FetchResult(
                    url=current_url,
                    canonical_url=canonical,
                    status_code=403,
                    content_bytes=b"",
                    content_type="",
                    headers={},
                    duration_ms=duration_ms,
                    content_hash="",
                    is_success=False,
                    error_message=f"Security policy rejected URL: {reason}",
                    redirect_chain=redirect_chain,
                )

            # 2. DNS resolution and address verification
            is_dest_valid, dest_reason, resolved_ips = self.policy.resolve_and_verify_destination(hostname)
            if not is_dest_valid:
                duration_ms = round((time.time() - start_time) * 1000.0, 1)
                logger.warning("SSRF destination resolution blocked %s: %s", hostname, dest_reason)
                return FetchResult(
                    url=current_url,
                    canonical_url=canonical,
                    status_code=403,
                    content_bytes=b"",
                    content_type="",
                    headers={},
                    duration_ms=duration_ms,
                    content_hash="",
                    is_success=False,
                    error_message=f"Destination address blocked: {dest_reason}",
                    redirect_chain=redirect_chain,
                )

            # 3. Domain rate limit
            allowed, wait = self.policy.check_rate_limit(hostname)
            if not allowed and wait > 0:
                if wait > 3.0:
                    duration_ms = round((time.time() - start_time) * 1000.0, 1)
                    return FetchResult(
                        url=current_url,
                        canonical_url=canonical,
                        status_code=429,
                        content_bytes=b"",
                        content_type="",
                        headers={},
                        duration_ms=duration_ms,
                        content_hash="",
                        is_success=False,
                        error_message=f"Rate limit exceeded for domain {hostname}; try again later.",
                        redirect_chain=redirect_chain,
                    )
                time.sleep(wait)

            # 4. Perform streaming GET request without auto-following redirects
            try:
                transport = PinningHTTPTransport(policy=self.policy)
                with httpx.Client(transport=transport, timeout=timeout, follow_redirects=False) as client:
                    with client.stream("GET", current_url, headers=req_headers) as resp:
                        # Check redirect status codes (301, 302, 303, 307, 308)
                        if resp.status_code in (301, 302, 303, 307, 308):
                            loc = resp.headers.get("Location")
                            if not loc:
                                duration_ms = round((time.time() - start_time) * 1000.0, 1)
                                return FetchResult(
                                    url=current_url,
                                    canonical_url=canonical,
                                    status_code=resp.status_code,
                                    content_bytes=b"",
                                    content_type=resp.headers.get("Content-Type", ""),
                                    headers=dict(resp.headers),
                                    duration_ms=duration_ms,
                                    content_hash="",
                                    is_success=False,
                                    error_message="Redirect missing Location header.",
                                    redirect_chain=redirect_chain,
                                 )

                            next_url = urljoin(current_url, loc)
                            redirect_chain.append(next_url)

                            # Credential isolation: Strip sensitive auth headers on cross-origin redirects
                            orig_parsed = urlparse(url)
                            next_parsed = urlparse(next_url)
                            if (next_parsed.scheme, next_parsed.hostname, next_parsed.port) != (orig_parsed.scheme, orig_parsed.hostname, orig_parsed.port):
                                req_headers = {
                                    k: v for k, v in req_headers.items()
                                    if k.lower() not in ("authorization", "cookie", "proxy-authorization", "x-api-key")
                                }

                            current_url = next_url
                            redirects_followed += 1
                            continue

                        # Check non-200 responses
                        if resp.status_code >= 400:
                            duration_ms = round((time.time() - start_time) * 1000.0, 1)
                            return FetchResult(
                                url=current_url,
                                canonical_url=canonical,
                                status_code=resp.status_code,
                                content_bytes=b"",
                                content_type=resp.headers.get("Content-Type", ""),
                                headers=dict(resp.headers),
                                duration_ms=duration_ms,
                                content_hash="",
                                is_success=False,
                                error_message=f"HTTP {resp.status_code} returned by origin.",
                                redirect_chain=redirect_chain,
                            )

                        # Enforce streaming byte limits
                        chunks: List[bytes] = []
                        total_bytes = 0
                        for chunk in resp.iter_bytes(chunk_size=16384):
                            total_bytes += len(chunk)
                            if total_bytes > byte_limit:
                                logger.warning("Document at %s exceeded maximum byte limit (%d bytes)", current_url, byte_limit)
                                duration_ms = round((time.time() - start_time) * 1000.0, 1)
                                return FetchResult(
                                    url=current_url,
                                    canonical_url=canonical,
                                    status_code=413,
                                    content_bytes=b"",
                                    content_type=resp.headers.get("Content-Type", ""),
                                    headers=dict(resp.headers),
                                    duration_ms=duration_ms,
                                    content_hash="",
                                    is_success=False,
                                    error_message=f"Document exceeded maximum allowed size of {byte_limit} bytes (read {total_bytes} bytes).",
                                    redirect_chain=redirect_chain,
                                )
                            chunks.append(chunk)

                        content_bytes = b"".join(chunks)
                        content_type = resp.headers.get("Content-Type", "")
                        duration_ms = round((time.time() - start_time) * 1000.0, 1)

                        # Detect SPA shell needing browser automation
                        needs_browser = False
                        if "html" in content_type.lower():
                            sample_text = content_bytes[:4096].decode("utf-8", errors="ignore").lower()
                            if any(ind in sample_text for ind in SPA_INDICATORS) and len(content_bytes) < 8192:
                                needs_browser = True

                        return FetchResult(
                            url=current_url,
                            canonical_url=normalize_canonical_url(current_url),
                            status_code=resp.status_code,
                            content_bytes=content_bytes,
                            content_type=content_type,
                            headers=dict(resp.headers),
                            duration_ms=duration_ms,
                            content_hash=compute_content_hash(content_bytes),
                            is_success=True,
                            redirect_chain=redirect_chain,
                            needs_browser_fallback=needs_browser,
                        )

            except (DestinationBlockedError, httpx.ConnectError) as conn_err:
                err_str = str(conn_err)
                is_ssrf = (
                    isinstance(conn_err, DestinationBlockedError)
                    or "SSRF" in err_str
                    or "blocked" in err_str.lower()
                    or "prohibited" in err_str.lower()
                )
                duration_ms = round((time.time() - start_time) * 1000.0, 1)
                logger.warning("SSRF / connection block for %s: %s", current_url, conn_err)
                return FetchResult(
                    url=current_url,
                    canonical_url=canonical,
                    status_code=403 if is_ssrf else 502,
                    content_bytes=b"",
                    content_type="",
                    headers={},
                    duration_ms=duration_ms,
                    content_hash="",
                    is_success=False,
                    error_message=f"SSRF destination blocked: {conn_err}" if is_ssrf else f"Connection error: {conn_err}",
                    redirect_chain=redirect_chain,
                )
            except Exception as req_err:
                duration_ms = round((time.time() - start_time) * 1000.0, 1)
                logger.warning("HTTP fetch failure for %s: %s", current_url, req_err)
                return FetchResult(
                    url=current_url,
                    canonical_url=canonical,
                    status_code=500,
                    content_bytes=b"",
                    content_type="",
                    headers={},
                    duration_ms=duration_ms,
                    content_hash="",
                    is_success=False,
                    error_message=f"Connection error: {req_err}",
                    redirect_chain=redirect_chain,
                )

        duration_ms = round((time.time() - start_time) * 1000.0, 1)
        return FetchResult(
            url=current_url,
            canonical_url=canonical,
            status_code=310,
            content_bytes=b"",
            content_type="",
            headers={},
            duration_ms=duration_ms,
            content_hash="",
            is_success=False,
            error_message=f"Maximum redirects ({self.policy.max_redirects}) exceeded.",
            redirect_chain=redirect_chain,
        )
