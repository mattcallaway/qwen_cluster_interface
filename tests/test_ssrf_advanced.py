import socket
import pytest
import httpx
from app.services.network_security import (
    validate_destination_ip,
    validate_endpoint_url,
    validate_probe_target,
    safe_execute_http,
    normalize_ip,
    is_cloud_metadata
)
import ipaddress

def test_ipv4_mapped_ipv6_normalization():
    # IPv4-mapped IPv6 pointing to metadata: ::ffff:169.254.169.254
    mapped_metadata = ipaddress.ip_address("::ffff:169.254.169.254")
    normalized = normalize_ip(mapped_metadata)
    assert isinstance(normalized, ipaddress.IPv4Address)
    assert str(normalized) == "169.254.169.254"
    assert is_cloud_metadata(str(mapped_metadata)) is True

    # Validate destination rejection
    valid, reason = validate_destination_ip(mapped_metadata, port=80, allow_localhost=False)
    assert valid is False
    assert "cloud metadata" in reason

def test_ipv4_mapped_ipv6_loopback():
    mapped_loopback = ipaddress.ip_address("::ffff:127.0.0.1")
    # Rejected when localhost not allowed
    valid, reason = validate_destination_ip(mapped_loopback, port=8000, allow_localhost=False)
    assert valid is False
    assert "Loopback" in reason

    # Rejected when port is prohibited
    valid_port, reason_port = validate_destination_ip(mapped_loopback, port=22, allow_localhost=True)
    assert valid_port is False
    assert "prohibited" in reason_port

def test_ipv6_link_local():
    link_local_v6 = ipaddress.ip_address("fe80::1")
    valid, reason = validate_destination_ip(link_local_v6, port=8000, allow_localhost=True)
    assert valid is False
    assert "cloud metadata" in reason or "link-local" in reason

def test_user_info_syntax_rejected():
    # Attempting to bypass SSRF with userinfo syntax
    urls = [
        "http://admin:secret@127.0.0.1:8000/",
        "http://user@192.168.1.100:8080/",
        "http://foo:bar@example.com:443/"
    ]
    for u in urls:
        valid, reason = validate_endpoint_url(u, allow_localhost=True)
        assert valid is False
        assert "user/password" in reason or "credentials" in reason

def test_probe_target_user_info_and_invalid_chars():
    targets = [
        ("admin:pass@192.168.1.1", 80),
        ("192.168.1.1/evil", 80),
        ("192.168.1.1\\evil", 80),
        ("192.168.1.1\0", 80),
    ]
    for host, port in targets:
        valid, reason = validate_probe_target(host, port)
        assert valid is False

def test_mixed_dns_answers_rejected(monkeypatch):
    # Simulate a domain that resolves to both a legitimate IP and metadata IP
    def fake_getaddrinfo(host, port, proto=0):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)), # valid global
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port)) # blocked metadata!
        ]
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    valid, reason = validate_endpoint_url("http://dual-stack-test.local:80", allow_localhost=False)
    assert valid is False
    assert "cloud metadata" in reason

def test_tailscale_and_private_destinations():
    # Tailscale IPv4
    ts_v4 = ipaddress.ip_address("100.122.228.19")
    valid, _ = validate_destination_ip(ts_v4, port=8000, allow_localhost=False)
    assert valid is True

    # Tailscale IPv6
    ts_v6 = ipaddress.ip_address("fd7a:115c:a1e0::1")
    valid, _ = validate_destination_ip(ts_v6, port=8000, allow_localhost=False)
    assert valid is True

    # Private RFC1918
    priv = ipaddress.ip_address("192.168.1.45")
    valid, _ = validate_destination_ip(priv, port=8080, allow_localhost=False)
    assert valid is True

@pytest.mark.anyio
async def test_safe_execute_http_blocks_redirect_to_metadata(monkeypatch):
    # Mock httpx client where first hop is 302 to metadata
    class MockRedirectTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if "redirector" in str(request.url):
                return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data"})
            return httpx.Response(200, text="ok")

    async with httpx.AsyncClient(transport=MockRedirectTransport()) as client:
        # Initial request should be revalidated on redirect and blocked
        with pytest.raises(ValueError, match="SSRF policy"):
            await safe_execute_http(client, "GET", "http://127.0.0.1:8000/redirector")
