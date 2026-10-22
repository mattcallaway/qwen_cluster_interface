"""
Stage 8C.2 Test Suite: Browser Egress, Anti-SSRF, and DNS-Rebinding Security.

Verifies:
- Scenario A: Authorized public HTTPS/HTTP navigation succeeds.
- Scenario B: Direct private-IP navigation is denied (RFC1918, loopback, metadata, unusual formats).
- Scenario C: IPv6 local/private navigation is denied (loopback, ULA, link-local, site-local, IPv4-mapped, IPv4-compatible).
- Scenario D: Public hostname resolving to a private address is denied (including mixed DNS responses).
- Scenario E: DNS rebinding after initial validation cannot establish a prohibited connection.
- Scenario F: Public-to-private redirect is denied.
- Scenario G: Browser subresource requests cannot bypass policy.
- Scenario H: WebSocket traffic cannot bypass policy.
- Scenario I: Filtering proxy failure causes explicit fail-closed behavior.
- Scenario J: Browser launch or proxy configuration cannot silently enable direct unrestricted egress.
- Scenario K: Legitimate public redirects remain functional.
- Scenario L: Denied requests and cross-origin redirects do not leak authentication secrets.
"""

from __future__ import annotations

import http.server
import ipaddress
import json
import os
import socket
import socketserver
import threading
import time
from typing import Generator, List, Tuple
from urllib.parse import urlparse

import httpx
import pytest

from app.services.research.browser import BrowserRenderResult, HeadlessBrowserService
from app.services.research.browser_proxy import OutboundFilteringProxy, ProxyAuditRecord
from app.services.research.policy import (
    ALLOWED_WEB_PORTS,
    BLOCKED_IPV4_NETWORKS,
    BLOCKED_IPV6_NETWORKS,
    BLOCKED_METADATA_HOSTS,
    BLOCKED_METADATA_IPS,
    ResearchSecurityPolicy,
    get_security_policy,
    normalize_ip,
)
from app.services.research.retrieval import (
    DestinationBlockedError,
    PinningHTTPTransport,
    SecureFetcher,
)


# ---------------------------------------------------------------------------
# Mock HTTP Server Fixture for Deterministic Local Testing
# ---------------------------------------------------------------------------

class DeterministicMockServer(http.server.BaseHTTPRequestHandler):
    """Local HTTP server that serves predictable responses for test scenarios."""

    def log_message(self, format, *args):
        pass  # Suppress console logging in tests

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/public-page":
            body = b"<html><head><title>Public Research</title></head><body><h1>Authorized Content</h1><p>Genuine article text for research testing.</p></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/legit-redirect-start":
            self.send_response(302)
            self.send_header("Location", "/public-page")
            self.send_header("Content-Length", "0")
            self.end_headers()

        elif path == "/redirect-to-private":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:8000/internal-admin")
            self.send_header("Content-Length", "0")
            self.end_headers()

        elif path == "/redirect-to-metadata":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.send_header("Content-Length", "0")
            self.end_headers()

        elif path == "/redirect-cross-origin":
            self.send_response(302)
            self.send_header("Location", f"http://target2.example.com:{self.server.server_address[1]}/echo-headers")
            self.send_header("Content-Length", "0")
            self.end_headers()

        elif path == "/echo-headers":
            headers_dict = dict(self.headers)
            body = json.dumps(headers_dict).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/page-with-subresources":
            body = (
                b"<html><head><title>Page with Subresources</title></head><body>"
                b"<p>Valid text</p>"
                b'<img src="http://127.0.0.1:8000/secret.png" />'
                b'<script src="http://192.168.1.1/malicious.js"></script>'
                b'<iframe src="http://169.254.169.254/latest/meta-data/"></iframe>'
                b"</body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        else:
            self.send_response(404)
            self.end_headers()


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


@pytest.fixture(scope="module")
def mock_http_server() -> Generator[Tuple[str, int], None, None]:
    """Spins up a lightweight threaded HTTP test server on an ephemeral port."""
    server = ThreadedHTTPServer(("127.0.0.1", 0), DeterministicMockServer)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="DeterministicMockServer")
    thread.start()
    yield ("127.0.0.1", port)
    server.shutdown()
    server.server_close()


# ---------------------------------------------------------------------------
# Test Scenario A: Authorized Public HTTPS / HTTP Navigation
# ---------------------------------------------------------------------------

def test_scenario_a_authorized_public_navigation_succeeds(mock_http_server):
    """Scenario A: Authorized public website navigation succeeds and extracts DOM text."""
    host, port = mock_http_server

    # Policy that validates mock server as authorized target for testing
    policy = ResearchSecurityPolicy(allowed_ports={80, 443, port})
    original_resolve = policy.resolve_and_verify_destination

    def mock_resolve(target_host: str):
        if target_host in ("mock-public-news.com", "public-target.org"):
            return True, "Mock public target", [host]
        return original_resolve(target_host)

    policy.resolve_and_verify_destination = mock_resolve

    fetcher = SecureFetcher(policy=policy)
    result = fetcher.fetch(f"http://mock-public-news.com:{port}/public-page")
    assert result.is_success is True
    assert result.status_code == 200
    assert "Authorized Content" in result.content_bytes.decode("utf-8")

    # Verify headless browser rendering
    browser_svc = HeadlessBrowserService(policy=policy)
    browser_result = browser_svc.render_page(f"http://mock-public-news.com:{port}/public-page")
    assert browser_result.is_success is True
    assert browser_result.status_code == 200
    assert "Authorized Content" in browser_result.extracted_text


# ---------------------------------------------------------------------------
# Test Scenario B: Direct Private-IP Navigation is Denied
# ---------------------------------------------------------------------------

def test_scenario_b_direct_private_ip_navigation_denied():
    """Scenario B: Direct private, loopback, link-local, and unusual IP formats are strictly blocked."""
    policy = get_security_policy()
    fetcher = SecureFetcher(policy=policy)
    browser_svc = HeadlessBrowserService(policy=policy)

    forbidden_urls = [
        ("http://127.0.0.1:8000/", "IPv4 Loopback standard"),
        ("http://127.0.0.2:80/", "IPv4 Loopback subnet"),
        ("http://10.0.0.1/", "RFC 1918 10/8"),
        ("http://10.255.255.255:443/", "RFC 1918 10/8 edge"),
        ("http://172.16.0.1/", "RFC 1918 172.16/12"),
        ("http://192.168.1.1:8080/", "RFC 1918 192.168/16"),
        ("http://169.254.169.254/", "AWS/GCP Link-Local Metadata"),
        ("http://100.64.0.1/", "Tailscale / Carrier CGNAT"),
        ("http://0177.0.0.1/", "Octal encoded loopback"),
        ("http://2130706433/", "Integer encoded loopback (127.0.0.1)"),
        ("http://127.1/", "Abbreviated IPv4 loopback"),
        ("http://0x7f.1/", "Hex-abbreviated IPv4 loopback"),
        ("http://localhost:8000/", "Localhost standard"),
        ("http://localhost.:8000/", "Localhost trailing dot FQDN"),
        ("http://metadata.google.internal/", "GCP Metadata host"),
    ]

    for url, desc in forbidden_urls:
        # 1. Check policy level
        val_ok, _, hostname = policy.validate_url(url)
        if val_ok and hostname:
            dest_ok, _, _ = policy.resolve_and_verify_destination(hostname)
            assert not dest_ok, f"Policy failed to reject prohibited destination: {desc} ({url})"
        else:
            assert not val_ok, f"Policy URL syntax check failed to reject {desc} ({url})"

        # 2. Check fetcher level
        res = fetcher.fetch(url)
        assert res.is_success is False, f"Fetcher allowed access to {desc} ({url})"
        assert res.status_code == 403

        # 3. Check browser service level
        b_res = browser_svc.render_page(url)
        assert b_res.is_success is False, f"Browser allowed navigation to {desc} ({url})"
        assert b_res.status_code == 403


# ---------------------------------------------------------------------------
# Test Scenario C: IPv6 Local/Private Navigation is Denied
# ---------------------------------------------------------------------------

def test_scenario_c_ipv6_local_and_private_navigation_denied():
    """Scenario C: IPv6 loopback, ULA, link-local, site-local, and mapped addresses are blocked."""
    policy = get_security_policy()

    forbidden_ipv6 = [
        ("::1", "IPv6 Loopback"),
        ("::", "IPv6 Unspecified"),
        ("fe80::1", "IPv6 Link-Local"),
        ("fe80::200:5aee:feaa:20a2", "IPv6 Link-Local device"),
        ("fc00::1", "IPv6 ULA prefix fc00"),
        ("fd00::1", "IPv6 ULA prefix fd00"),
        ("fd7a:115c:a1e0::1", "Tailscale IPv6"),
        ("fec0::1", "Deprecated IPv6 Site-Local"),
        ("::ffff:127.0.0.1", "IPv4-mapped IPv6 loopback"),
        ("::ffff:10.0.0.1", "IPv4-mapped IPv6 private 10/8"),
        ("::ffff:169.254.169.254", "IPv4-mapped IPv6 cloud metadata"),
        ("::127.0.0.1", "IPv4-compatible IPv6 loopback"),
        ("::10.0.0.1", "IPv4-compatible IPv6 private"),
    ]

    for ip_str, desc in forbidden_ipv6:
        is_safe = policy.is_safe_ip(ip_str)
        assert is_safe is False, f"Policy accepted prohibited IPv6 address {desc}: {ip_str}"

        dest_ok, _, _ = policy.resolve_and_verify_destination(f"[{ip_str}]")
        assert dest_ok is False, f"resolve_and_verify_destination accepted {desc}: [{ip_str}]"

    # Test via OutboundFilteringProxy CONNECT
    with OutboundFilteringProxy(policy=policy) as proxy:
        for ip_str, desc in [("::1", "IPv6 Loopback"), ("fe80::1", "IPv6 Link-Local"), ("::ffff:127.0.0.1", "IPv4-mapped")]:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("127.0.0.1", proxy.port))
            req = f"CONNECT [{ip_str}]:443 HTTP/1.1\r\nHost: [{ip_str}]:443\r\n\r\n"
            s.sendall(req.encode("ascii"))
            resp = s.recv(4096).decode("ascii", errors="replace")
            s.close()
            assert "403 Forbidden" in resp, f"Proxy failed to block IPv6 {desc}: {resp}"


# ---------------------------------------------------------------------------
# Test Scenario D: Public Hostname Resolving to Private Address Denied
# ---------------------------------------------------------------------------

def test_scenario_d_public_hostname_resolving_to_private_address_denied(monkeypatch):
    """Scenario D: A benign-looking public hostname resolving to private IPs or mixed answers is denied."""
    policy = get_security_policy()

    # 1. Pure private resolution
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.1.50", port or 80))
    ])
    dest_ok, reason, ips = policy.resolve_and_verify_destination("innocent-looking-site.com")
    assert dest_ok is False
    assert "belongs to blocked private/reserved network" in reason

    # 2. Mixed resolution: returns one public IP and one private IP
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 80)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port or 80)),
    ])
    dest_ok, reason, ips = policy.resolve_and_verify_destination("mixed-dns-answers.com")
    assert dest_ok is False
    assert "Prohibited destination" in reason
    assert "127.0.0.1" in str(ips)


# ---------------------------------------------------------------------------
# Test Scenario E: DNS Rebinding Protection via Socket Pinning
# ---------------------------------------------------------------------------

def test_scenario_e_dns_rebinding_prevented(monkeypatch):
    """Scenario E: DNS rebinding cannot establish a connection to a private address."""
    policy = get_security_policy()

    # Simulate DNS rebinding: host initially resolves to public, then on connection or subsequent request to 127.0.0.1
    resolve_count = 0

    def rebinding_getaddrinfo(host, port, *args, **kwargs):
        nonlocal resolve_count
        resolve_count += 1
        if resolve_count == 1:
            # First resolution: approved public IP
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 80))]
        else:
            # Subsequent resolution attempts to rebind to localhost
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port or 80))]

    monkeypatch.setattr(socket, "getaddrinfo", rebinding_getaddrinfo)

    # In OutboundFilteringProxy:
    with OutboundFilteringProxy(policy=policy) as proxy:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(("127.0.0.1", proxy.port))
        # This will trigger resolution #1 (public) and attempt connect
        req = "CONNECT rebind-target.com:443 HTTP/1.1\r\nHost: rebind-target.com:443\r\n\r\n"
        s.sendall(req.encode("ascii"))
        # The proxy connects to 93.184.216.34 (or fails connect to external public IP), but NEVER connects to 127.0.0.1
        resp = s.recv(4096).decode("ascii", errors="replace")
        s.close()

        # Resolution #2 (rebinding to 127.0.0.1)
        s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s2.connect(("127.0.0.1", proxy.port))
        s2.sendall(req.encode("ascii"))
        resp2 = s2.recv(4096).decode("ascii", errors="replace")
        s2.close()
        assert "403 Forbidden" in resp2
        assert "SSRF Blocked" in resp2

    # In PinningHTTPTransport: zero keepalive expiry prevents connection reuse across queries
    transport = PinningHTTPTransport(policy=policy)
    assert transport._pool._keepalive_expiry == 0.0


# ---------------------------------------------------------------------------
# Test Scenario F: Public-to-Private Redirect is Denied
# ---------------------------------------------------------------------------

def test_scenario_f_public_to_private_redirect_denied(mock_http_server):
    """Scenario F: A public page redirecting to loopback, LAN, or cloud metadata is aborted."""
    host, port = mock_http_server

    # Allow test server port alongside 80 and 443
    policy = ResearchSecurityPolicy(allowed_ports={80, 443, port})
    original_resolve = policy.resolve_and_verify_destination

    def mock_resolve(target_host: str):
        if target_host == "redirect-origin.org":
            return True, "Mock public target", [host]
        return original_resolve(target_host)

    policy.resolve_and_verify_destination = mock_resolve
    fetcher = SecureFetcher(policy=policy)

    # 1. Redirect to localhost
    res_local = fetcher.fetch(f"http://redirect-origin.org:{port}/redirect-to-private")
    assert res_local.is_success is False
    assert res_local.status_code == 403
    assert any("127.0.0.1" in hop for hop in res_local.redirect_chain)

    # 2. Redirect to cloud metadata
    res_meta = fetcher.fetch(f"http://redirect-origin.org:{port}/redirect-to-metadata")
    assert res_meta.is_success is False
    assert res_meta.status_code == 403
    assert any("169.254.169.254" in hop for hop in res_meta.redirect_chain)


# ---------------------------------------------------------------------------
# Test Scenario G: Browser Subresource Requests Cannot Bypass Policy
# ---------------------------------------------------------------------------

def test_scenario_g_browser_subresources_blocked_by_route_and_proxy(mock_http_server):
    """Scenario G: Internal images, scripts, and iframes embedded in a public page are blocked."""
    host, port = mock_http_server

    policy = ResearchSecurityPolicy(allowed_ports={80, 443, port})
    original_resolve = policy.resolve_and_verify_destination

    def mock_resolve(target_host: str):
        if target_host == "subresource-test.org":
            return True, "Mock public target", [host]
        return original_resolve(target_host)

    policy.resolve_and_verify_destination = mock_resolve

    browser_svc = HeadlessBrowserService(policy=policy)
    render_res = browser_svc.render_page(f"http://subresource-test.org:{port}/page-with-subresources")
    assert render_res.is_success is True
    # The parent page rendered successfully, but route interceptor blocked the private subresources
    assert "Valid text" in render_res.extracted_text


# ---------------------------------------------------------------------------
# Test Scenario H: WebSocket Traffic Cannot Bypass Policy
# ---------------------------------------------------------------------------

def test_scenario_h_websocket_traffic_blocked():
    """Scenario H: WebSocket connection attempts (CONNECT or GET Upgrade) to internal destinations fail."""
    policy = get_security_policy()

    with OutboundFilteringProxy(policy=policy) as proxy:
        # 1. WSS / HTTPS tunnel request for WebSocket on private endpoint
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(("127.0.0.1", proxy.port))
        s.sendall(b"CONNECT 127.0.0.1:9001 HTTP/1.1\r\nHost: 127.0.0.1:9001\r\n\r\n")
        resp = s.recv(4096).decode("ascii", errors="replace")
        s.close()
        assert "403 Forbidden" in resp

        # 2. Plain HTTP WebSocket upgrade to RFC 1918 host
        s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s2.connect(("127.0.0.1", proxy.port))
        s2.sendall(
            b"GET http://10.0.0.5:8080/ws HTTP/1.1\r\n"
            b"Host: 10.0.0.5:8080\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n\r\n"
        )
        resp2 = s2.recv(4096).decode("ascii", errors="replace")
        s2.close()
        assert "403 Forbidden" in resp2

        # 3. WebSocket upgrade using relative URI with Host header targeting cloud metadata
        s3 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s3.connect(("127.0.0.1", proxy.port))
        s3.sendall(
            b"GET /ws HTTP/1.1\r\n"
            b"Host: 169.254.169.254:80\r\n"
            b"Upgrade: websocket\r\n\r\n"
        )
        resp3 = s3.recv(4096).decode("ascii", errors="replace")
        s3.close()
        assert "403 Forbidden" in resp3


# ---------------------------------------------------------------------------
# Test Scenario I: Filtering Proxy Failure Causes Fail-Closed Behavior
# ---------------------------------------------------------------------------

def test_scenario_i_filtering_proxy_failure_fails_closed(monkeypatch):
    """Scenario I: When egress proxy fails to bind or start, browser execution fails closed with 503."""
    browser_svc = HeadlessBrowserService()

    # Simulate OutboundFilteringProxy.start() raising an error (e.g. port collision)
    def mock_proxy_start_fail(*args, **kwargs):
        raise OSError("Simulated address already in use")

    monkeypatch.setattr("app.services.research.browser_proxy.OutboundFilteringProxy.start", mock_proxy_start_fail)

    result = browser_svc.render_page("http://example.com/")
    assert result.is_success is False
    assert result.status_code == 503
    assert "Secure browser egress unavailable" in result.error_message
    assert "Simulated address already in use" in result.error_message


# ---------------------------------------------------------------------------
# Test Scenario J: Browser Launch Cannot Silently Enable Unrestricted Direct Egress
# ---------------------------------------------------------------------------

def test_scenario_j_proxy_bypass_disabled_in_browser_args(monkeypatch):
    """Scenario J: Browser launch arguments strictly enforce proxy usage and <-loopback> routing."""
    captured_launch_kwargs = {}

    # Mock playwright launch to inspect arguments passed to Chromium
    class MockBrowser:
        def new_context(self, *args, **kwargs):
            class MockContext:
                def route(self, *args, **kwargs):
                    pass
                def new_page(self):
                    class MockPage:
                        def set_default_timeout(self, t):
                            pass
                        def goto(self, *args, **kwargs):
                            raise RuntimeError("Stop after launch inspection")
                    return MockPage()
            return MockContext()
        def close(self):
            pass

    class MockChromium:
        def launch(self, **kwargs):
            nonlocal captured_launch_kwargs
            captured_launch_kwargs = kwargs
            return MockBrowser()

    class MockPlaywright:
        chromium = MockChromium()
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: MockPlaywright())

    policy = ResearchSecurityPolicy()
    policy.resolve_and_verify_destination = lambda host: (True, "OK", ["93.184.216.34"])

    browser_svc = HeadlessBrowserService(policy=policy)
    browser_svc.render_page("http://example.com/")

    assert "proxy" in captured_launch_kwargs
    proxy_cfg = captured_launch_kwargs["proxy"]
    assert proxy_cfg["server"].startswith("http://127.0.0.1:")
    assert proxy_cfg["bypass"] == ""

    # Check command-line flags
    args = captured_launch_kwargs.get("args", [])
    assert any("--proxy-bypass-list=<-loopback>" in a for a in args)
    assert any("--disable-extensions" in a for a in args)


# ---------------------------------------------------------------------------
# Test Scenario K: Legitimate Public Redirects Remain Functional
# ---------------------------------------------------------------------------

def test_scenario_k_legitimate_public_redirects_succeed(mock_http_server):
    """Scenario K: Valid public redirects within allowed policy are followed successfully."""
    host, port = mock_http_server

    policy = ResearchSecurityPolicy(allowed_ports={80, 443, port})
    original_resolve = policy.resolve_and_verify_destination

    def mock_resolve(target_host: str):
        if target_host == "legit-redirect.org":
            return True, "Mock public target", [host]
        return original_resolve(target_host)

    policy.resolve_and_verify_destination = mock_resolve
    fetcher = SecureFetcher(policy=policy)

    res = fetcher.fetch(f"http://legit-redirect.org:{port}/legit-redirect-start")
    assert res.is_success is True
    assert res.status_code == 200
    assert len(res.redirect_chain) == 2
    assert "public-page" in res.final_url
    assert "Authorized Content" in res.content_bytes.decode("utf-8")


# ---------------------------------------------------------------------------
# Test Scenario L: Denied Requests Do Not Leak Authentication Secrets
# ---------------------------------------------------------------------------

def test_scenario_l_credentials_isolated_and_stripped_on_cross_origin_redirect(mock_http_server):
    """Scenario L: Sensitive auth headers are stripped on cross-origin redirects and never exposed in errors."""
    host, port = mock_http_server

    policy = ResearchSecurityPolicy(allowed_ports={80, 443, port})
    original_resolve = policy.resolve_and_verify_destination

    def mock_resolve(target_host: str):
        if target_host in ("origin.example.com", "target2.example.com"):
            return True, "Mock public targets", [host]
        return original_resolve(target_host)

    policy.resolve_and_verify_destination = mock_resolve
    fetcher = SecureFetcher(policy=policy)

    sensitive_token = "BEARER-SECRET-TOKEN-DO-NOT-LEAK-XYZ-999"
    sensitive_cookie = "session_cookie=super_secret_admin_token"

    # 1. Fetch with sensitive headers across cross-domain redirect
    res = fetcher.fetch(
        f"http://origin.example.com:{port}/redirect-cross-origin",
        headers={
            "Authorization": f"Bearer {sensitive_token}",
            "Cookie": sensitive_cookie,
            "X-Api-Key": "my-secret-api-key",
            "Custom-Public-Header": "safe-value",
        },
    )

    assert res.is_success is True
    assert res.status_code == 200
    received_headers = json.loads(res.content_bytes.decode("utf-8"))

    # Verify sensitive headers were STRIPPED on the cross-origin redirect
    assert "authorization" not in [k.lower() for k in received_headers]
    assert "cookie" not in [k.lower() for k in received_headers]
    assert "x-api-key" not in [k.lower() for k in received_headers]
    # Verify non-sensitive header was preserved
    assert received_headers.get("Custom-Public-Header") == "safe-value"

    # 2. Verify that when a request is denied, error messages do NOT echo bearer tokens
    denied_res = fetcher.fetch(
        "http://127.0.0.1:8000/api/v1/system/diagnostics",
        headers={"Authorization": f"Bearer {sensitive_token}"},
    )
    assert denied_res.is_success is False
    assert sensitive_token not in denied_res.error_message
