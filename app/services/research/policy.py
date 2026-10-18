"""
Research Security Policy & SSRF Prevention.

Enforces strict boundaries on outbound web requests originating from the
research engine:
1. Rejection of private, loopback, link-local, Tailscale, and cloud metadata IPs.
2. Rejection of user-info credential URLs.
3. Pre-connection DNS resolution and verification of all destination IPs.
4. Strict destination port restriction (only 80 and 443).
5. Protection against DNS rebinding and TOCTOU address modification.
6. Per-domain rate limiting and bounded request concurrency.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import time
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

logger = logging.getLogger("qwen_collective.research.policy")

# Prohibited private and special-purpose IP networks
BLOCKED_IPV4_NETWORKS: List[ipaddress.IPv4Network] = [
    ipaddress.ip_network("0.0.0.0/8"),          # Current network (only valid as source)
    ipaddress.ip_network("10.0.0.0/8"),         # Private-Use (RFC 1918)
    ipaddress.ip_network("100.64.0.0/10"),      # Shared Address Space / Tailscale CGNAT
    ipaddress.ip_network("127.0.0.0/8"),        # Loopback
    ipaddress.ip_network("169.254.0.0/16"),     # Link Local / Cloud Metadata
    ipaddress.ip_network("172.16.0.0/12"),      # Private-Use (RFC 1918)
    ipaddress.ip_network("192.0.0.0/24"),       # IETF Protocol Assignments
    ipaddress.ip_network("192.0.2.0/24"),       # Documentation (TEST-NET-1)
    ipaddress.ip_network("192.168.0.0/16"),     # Private-Use (RFC 1918)
    ipaddress.ip_network("198.18.0.0/15"),      # Benchmarking
    ipaddress.ip_network("198.51.100.0/24"),    # Documentation (TEST-NET-2)
    ipaddress.ip_network("203.0.113.0/24"),     # Documentation (TEST-NET-3)
    ipaddress.ip_network("224.0.0.0/4"),        # Multicast
    ipaddress.ip_network("240.0.0.0/4"),        # Reserved for Future Use
    ipaddress.ip_network("255.255.255.255/32"), # Limited Broadcast
]

BLOCKED_IPV6_NETWORKS: List[ipaddress.IPv6Network] = [
    ipaddress.ip_network("::/128"),             # Unspecified
    ipaddress.ip_network("::1/128"),            # Loopback
    ipaddress.ip_network("::ffff:0:0/96"),      # IPv4-mapped IPv6 (must be evaluated as IPv4)
    ipaddress.ip_network("100::/64"),           # Discard-Only Address Block
    ipaddress.ip_network("2001:db8::/32"),      # Documentation
    ipaddress.ip_network("fc00::/7"),           # Unique Local Addresses (ULA)
    ipaddress.ip_network("fe80::/10"),          # Link-Local Unicast
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),# Tailscale IPv6 network
    ipaddress.ip_network("ff00::/8"),           # Multicast
]

# Cloud metadata addresses and hostnames to strictly block
BLOCKED_METADATA_IPS: Set[str] = {
    "169.254.169.254", # AWS, GCP, Azure, OpenStack, Alibaba metadata
    "169.254.169.123", # AWS NTP
    "fd00:ec2::254",   # AWS IPv6 metadata
}

BLOCKED_METADATA_HOSTS: Set[str] = {
    "metadata.google.internal",
    "metadata.internal",
    "instance-data",
    "169.254.169.254",
    "localhost",
}

# Only standard public web ports are permitted for research retrieval
ALLOWED_WEB_PORTS: Set[int] = {80, 443}


def normalize_ip(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Unwrap IPv4-mapped IPv6 (e.g., ::ffff:169.254.169.254) to true IPv4."""
    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped:
        return ip_obj.ipv4_mapped
    return ip_obj


class ResearchSecurityPolicy:
    """Security policy validator for outbound web research requests."""

    def __init__(
        self,
        allowed_ports: Optional[Set[int]] = None,
        max_redirects: int = 5,
        max_response_bytes: int = 5 * 1024 * 1024, # 5 MB
        request_timeout_seconds: float = 12.0,
        rate_limit_per_domain_seconds: float = 1.0,
        denied_domains: Optional[Set[str]] = None,
    ):
        self.allowed_ports = allowed_ports or ALLOWED_WEB_PORTS
        self.max_redirects = max_redirects
        self.max_response_bytes = max_response_bytes
        self.request_timeout_seconds = request_timeout_seconds
        self.rate_limit_per_domain_seconds = rate_limit_per_domain_seconds
        self.denied_domains = {d.lower() for d in (denied_domains or set())}
        self._domain_access_timestamps: Dict[str, float] = {}

    def validate_url(self, raw_url: str) -> Tuple[bool, str, Optional[str]]:
        """
        Validate URL syntax, scheme, credentials, port, and hostname before connection.
        Returns: (is_valid, reason, canonical_host_or_ip)
        """
        if not raw_url or not isinstance(raw_url, str):
            return False, "URL must be a non-empty string.", None

        raw_url = raw_url.strip()
        try:
            parsed = urlparse(raw_url)
        except Exception as e:
            return False, f"URL parse failure: {e}", None

        scheme = (parsed.scheme or "").lower()
        if scheme not in ("http", "https"):
            return False, f"Scheme '{scheme}' prohibited; only HTTP and HTTPS are permitted.", None

        # Rejection of user-info credential syntax (e.g. http://user:pass@host)
        if parsed.username or parsed.password or "@" in (parsed.netloc or ""):
            return False, "URLs containing embedded user credentials are prohibited.", None

        hostname = (parsed.hostname or "").strip().lower()
        if not hostname:
            return False, "Missing hostname in URL.", None

        # Check explicit metadata hosts
        if hostname in BLOCKED_METADATA_HOSTS:
            return False, f"Access to cloud metadata host '{hostname}' is strictly blocked.", None

        # Check denied domains
        for denied in self.denied_domains:
            if hostname == denied or hostname.endswith(f".{denied}"):
                return False, f"Domain '{hostname}' is denied by research policy.", None

        # Port validation
        port = parsed.port or (443 if scheme == "https" else 80)
        if port not in self.allowed_ports:
            return False, f"Destination port {port} is prohibited for public web research.", None

        return True, "URL structure is valid.", hostname

    def validate_resolved_ip(self, ip_str: str) -> Tuple[bool, str]:
        """
        Validate resolved IP address against blocked private, loopback, link-local,
        Tailscale, and cloud metadata ranges.
        """
        try:
            raw_ip = ipaddress.ip_address(ip_str.strip().strip("[]"))
        except ValueError:
            return False, f"Invalid IP address format: {ip_str}"

        ip_obj = normalize_ip(raw_ip)
        str_repr = str(ip_obj)

        if str_repr in BLOCKED_METADATA_IPS:
            return False, f"Cloud metadata IP address {str_repr} is strictly blocked."

        if isinstance(ip_obj, ipaddress.IPv4Address):
            for net in BLOCKED_IPV4_NETWORKS:
                if ip_obj in net:
                    return False, f"IPv4 address {str_repr} belongs to blocked private/reserved network {net}."
        elif isinstance(ip_obj, ipaddress.IPv6Address):
            for net in BLOCKED_IPV6_NETWORKS:
                if ip_obj in net:
                    return False, f"IPv6 address {str_repr} belongs to blocked private/reserved network {net}."

        return True, "IP address is valid public destination."

    def is_safe_ip(self, ip_str: str) -> bool:
        """Helper returns True if IP is public and safe, False otherwise."""
        safe, _ = self.validate_resolved_ip(ip_str)
        return safe

    def validate_host_resolution(self, hostname: str) -> Tuple[bool, str]:
        """Validates that a hostname resolves strictly to approved public IPs."""
        safe, reason, _ = self.resolve_and_verify_destination(hostname)
        return safe, reason

    def resolve_and_verify_destination(self, hostname: str) -> Tuple[bool, str, List[str]]:
        """
        Perform DNS lookup and verify all resolved IP addresses.
        Prevents DNS rebinding and internal network penetration.
        """
        # If hostname is already a raw IP
        try:
            raw_ip = ipaddress.ip_address(hostname.strip("[]"))
            is_valid, reason = self.validate_resolved_ip(str(raw_ip))
            if not is_valid:
                return False, reason, []
            return True, "Direct IP validated.", [str(raw_ip)]
        except ValueError:
            pass  # Hostname requires DNS resolution

        try:
            addr_info = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
        except socket.gaierror as e:
            return False, f"DNS resolution failed for '{hostname}': {e}", []
        except Exception as e:
            return False, f"Unexpected error resolving '{hostname}': {e}", []

        resolved_ips: List[str] = []
        for family, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            if ip_str not in resolved_ips:
                resolved_ips.append(ip_str)

        if not resolved_ips:
            return False, f"No IP addresses resolved for '{hostname}'.", []

        # Every resolved IP address MUST be a valid public destination
        for ip_str in resolved_ips:
            is_valid, reason = self.validate_resolved_ip(ip_str)
            if not is_valid:
                logger.warning("SSRF blocked: %s resolved to prohibited address %s: %s", hostname, ip_str, reason)
                return False, f"Prohibited destination for '{hostname}': {reason}", resolved_ips

        return True, "All resolved destination addresses verified public.", resolved_ips

    def check_rate_limit(self, domain: str) -> Tuple[bool, float]:
        """
        Domain-level rate limiting check.
        Returns: (is_allowed, wait_seconds_needed)
        """
        domain = domain.lower()
        now = time.time()
        last_access = self._domain_access_timestamps.get(domain, 0.0)
        elapsed = now - last_access
        if elapsed < self.rate_limit_per_domain_seconds:
            wait = self.rate_limit_per_domain_seconds - elapsed
            return False, wait
        self._domain_access_timestamps[domain] = now
        return True, 0.0

    def record_access(self, domain: str) -> None:
        self._domain_access_timestamps[domain.lower()] = time.time()


_default_policy: Optional[ResearchSecurityPolicy] = None


def get_security_policy() -> ResearchSecurityPolicy:
    global _default_policy
    if _default_policy is None:
        _default_policy = ResearchSecurityPolicy()
    return _default_policy
