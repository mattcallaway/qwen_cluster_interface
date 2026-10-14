"""
Qwen Collective - Network Security & SSRF Protection Policy
-----------------------------------------------------------
Implements strict outbound network policy:
- Rejection of cloud metadata endpoints (IPv4 and IPv6)
- IPv4-mapped IPv6 normalization and defense
- DNS resolution and validation of all returned addresses
- Mitigation of DNS rebinding and TOCTOU address spoofing
- Strict port restrictions (localhost and remote probe)
- URL normalization and user-info rejection
- Redirect prevention and revalidation
"""

import ipaddress
import socket
import logging
from urllib.parse import urlparse, urljoin
from typing import Tuple, Optional, List, Union, Dict, Any, Set
import httpx

logger = logging.getLogger("qwen_collective.network_security")

# Cloud metadata addresses and hostnames to strictly block
BLOCKED_METADATA_IPS: Set[str] = {
    "169.254.169.254", # AWS, GCP, Azure, OpenStack IPv4 metadata
    "fd00:ec2::254",    # AWS IPv6 metadata
}

BLOCKED_METADATA_HOSTS: Set[str] = {
    "metadata.google.internal",
    "metadata.internal",
    "instance-data",
}

# Link-local / metadata networks
IPV4_LINK_LOCAL_NET = ipaddress.ip_network("169.254.0.0/16")
IPV6_LINK_LOCAL_NET = ipaddress.ip_network("fe80::/10")

# Standard allowed ports for machine ping / reachability testing
ALLOWED_PROBE_PORTS: Set[int] = {
    22,     # SSH
    80,     # HTTP
    443,    # HTTPS
    8000,   # Control Server
    8080,   # llama-server HTTP
    8081,   # VPS Companion Worker
    11434,  # Ollama API
    50052,  # llama.cpp RPC Server
}

ALLOWED_LOCALHOST_PORTS: Set[int] = {
    8000,   # Control Server
    8080,   # Local llama-server
    8081,   # Local test worker
    11434,  # Local Ollama
    50052,  # Local RPC worker
}

# Allowed ports for outbound HTTP/HTTPS endpoint communication
ALLOWED_ENDPOINT_PORTS: Set[int] = {
    80,
    443,
    8000,
    8080,
    8081,
    11434,
}

# Tailscale networks
TAILSCALE_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")
TAILSCALE_IPV6_NET = ipaddress.ip_network("fd7a:115c:a1e0::/48")

def normalize_ip(ip_obj: Union[ipaddress.IPv4Address, ipaddress.IPv6Address]) -> Union[ipaddress.IPv4Address, ipaddress.IPv6Address]:
    """
    Normalize an IP address object, converting IPv4-mapped IPv6 (e.g., ::ffff:169.254.169.254)
    into its true IPv4 representation to prevent parser bypasses.
    """
    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped:
        return ip_obj.ipv4_mapped
    return ip_obj

def is_cloud_metadata(ip_or_host: str) -> bool:
    """Check if address matches known cloud provider metadata endpoints or link-local ranges."""
    cleaned = ip_or_host.strip().lower().strip("[]")
    if cleaned in BLOCKED_METADATA_HOSTS or cleaned in BLOCKED_METADATA_IPS:
        return True
    try:
        raw_ip = ipaddress.ip_address(cleaned)
        ip_obj = normalize_ip(raw_ip)
        
        if ip_obj.is_link_local:
            return True
        if str(ip_obj) in BLOCKED_METADATA_IPS:
            return True
        if isinstance(ip_obj, ipaddress.IPv4Address) and ip_obj in IPV4_LINK_LOCAL_NET:
            return True
        if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj in IPV6_LINK_LOCAL_NET:
            return True
    except ValueError:
        pass
    return False

def resolve_hostname_ips(hostname: str, port: int) -> Tuple[bool, Union[List[Union[ipaddress.IPv4Address, ipaddress.IPv6Address]], str]]:
    """
    Resolve a hostname or IP string to all destination IP objects via socket.getaddrinfo.
    Returns: (success, list_of_normalized_ips_or_error_string)
    """
    clean_host = hostname.strip().lower().strip("[]")
    if not clean_host:
        return False, "Target host cannot be empty."

    # Direct IP literal check
    try:
        raw_ip = ipaddress.ip_address(clean_host)
        return True, [normalize_ip(raw_ip)]
    except ValueError:
        pass

    # Resolve via DNS
    try:
        addrinfo = socket.getaddrinfo(clean_host, port, proto=socket.IPPROTO_TCP)
        ips = []
        seen = set()
        for item in addrinfo:
            sockaddr = item[4]
            ip_str = sockaddr[0]
            if ip_str not in seen:
                seen.add(ip_str)
                raw_ip = ipaddress.ip_address(ip_str)
                ips.append(normalize_ip(raw_ip))
        if not ips:
            return False, f"Could not resolve host '{clean_host}'."
        return True, ips
    except socket.gaierror as e:
        return False, f"DNS resolution failed for '{clean_host}': {str(e)}"
    except Exception as e:
        return False, f"Failed resolving target '{clean_host}': {str(e)}"

def validate_destination_ip(
    ip_obj: Union[ipaddress.IPv4Address, ipaddress.IPv6Address],
    port: int,
    allow_localhost: bool = True
) -> Tuple[bool, str]:
    """
    Validate a destination IP against the explicit authorized network policy.
    Prevents SSRF, metadata probing, and unauthorized internal network access.
    """
    normalized = normalize_ip(ip_obj)
    ip_str = str(normalized)

    # 1. Reject cloud metadata & link-local
    if (
        ip_str in BLOCKED_METADATA_IPS
        or normalized.is_link_local
        or (isinstance(normalized, ipaddress.IPv4Address) and normalized in IPV4_LINK_LOCAL_NET)
        or (isinstance(normalized, ipaddress.IPv6Address) and normalized in IPV6_LINK_LOCAL_NET)
    ):
        return False, f"Access to link-local / cloud metadata address ({ip_str}) is strictly blocked."

    # 2. Handle Loopback
    if normalized.is_loopback:
        if not allow_localhost:
            return False, f"Loopback address ({ip_str}) is prohibited for this endpoint."
        if port not in ALLOWED_LOCALHOST_PORTS:
            return False, f"Localhost access to port {port} is prohibited. Allowed local ports: {sorted(ALLOWED_LOCALHOST_PORTS)}."
        return True, "Authorized localhost destination."

    # 3. Reject multicast, unspecified, reserved
    if normalized.is_multicast or normalized.is_unspecified or normalized.is_reserved:
        return False, f"Target IP ({ip_str}) is not a permitted unicast destination."

    # 4. Authorized Destinations:
    # A. Private RFC1918 or IPv6 ULA for local cluster devices (e.g. Linux laptops)
    if normalized.is_private:
        return True, "Authorized private cluster network destination."

    # B. Tailscale network (CGNAT IPv4 or Tailscale ULA IPv6)
    if (isinstance(normalized, ipaddress.IPv4Address) and normalized in TAILSCALE_CGNAT_NET) or \
       (isinstance(normalized, ipaddress.IPv6Address) and normalized in TAILSCALE_IPV6_NET):
        return True, "Authorized Tailscale network destination."

    # C. Global / Public internet destination:
    # Enforce strict port policy: only permitted for standard web/VPS services, never arbitrary internal ports
    if normalized.is_global:
        if port not in ALLOWED_ENDPOINT_PORTS:
            return False, f"Port {port} is prohibited for external network destination {ip_str}. Allowed: {sorted(ALLOWED_ENDPOINT_PORTS)}."
        return True, "Authorized external network destination."

    return False, f"IP address ({ip_str}) does not match authorized collective network policy."

def validate_probe_target(host: str, port: int) -> Tuple[bool, str]:
    """
    Validate that a machine connectivity probe is safe and not an SSRF or metadata probe.
    Resolves hostname and verifies all destination IPs.
    """
    host_clean = host.strip().lower().strip("[]")
    if not host_clean:
        return False, "Target host cannot be empty."

    # Reject user-info syntax in host string
    if "@" in host_clean or "/" in host_clean or "\\" in host_clean or "\0" in host_clean:
        return False, "Target host contains invalid or prohibited user-info syntax."

    if is_cloud_metadata(host_clean):
        return False, "Access to cloud metadata endpoints is strictly blocked."

    if port not in ALLOWED_PROBE_PORTS:
        return False, f"Port {port} is not in the allowed collective probe ports ({sorted(ALLOWED_PROBE_PORTS)})."

    success, ips_or_err = resolve_hostname_ips(host_clean, port)
    if not success:
        return False, str(ips_or_err)

    # Every resolved IP must pass validation
    for ip_obj in ips_or_err:
        valid, reason = validate_destination_ip(ip_obj, port, allow_localhost=True)
        if not valid:
            return False, f"Probe target rejected for IP {ip_obj}: {reason}"

    return True, "Target is valid."

def resolve_and_pin_endpoint_url(url: str, allow_localhost: bool = True) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Validate and pin an endpoint URL to its resolved IP address.
    Eliminates TOCTOU DNS rebinding by providing pre-resolved IP connection info.
    """
    cleaned_url = url.strip()
    if not cleaned_url:
        return False, "URL cannot be empty.", None

    try:
        parsed = urlparse(cleaned_url)
    except Exception as e:
        return False, f"Malformed URL: {str(e)}", None

    if parsed.scheme not in ("http", "https"):
        return False, f"Unsupported URL scheme '{parsed.scheme}'. Only http:// and https:// are permitted.", None

    if parsed.username or parsed.password:
        return False, "URLs with embedded username/password credentials are prohibited.", None

    hostname = parsed.hostname
    if not hostname:
        return False, "URL must contain a valid hostname or IP address.", None

    hostname_clean = hostname.strip().lower().strip("[]")
    if not hostname_clean or "\0" in hostname_clean or "/" in hostname_clean:
        return False, "Invalid characters in target hostname.", None

    if is_cloud_metadata(hostname_clean):
        return False, "Access to cloud metadata services (169.254.169.254) is strictly prohibited.", None

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in ALLOWED_ENDPOINT_PORTS:
        return False, f"Port {port} is prohibited for service endpoints. Allowed: {sorted(ALLOWED_ENDPOINT_PORTS)}.", None

    # Resolve and validate ALL destination IPs
    success, ips_or_err = resolve_hostname_ips(hostname_clean, port)
    if not success:
        return False, str(ips_or_err), None

    for ip_obj in ips_or_err:
        valid, reason = validate_destination_ip(ip_obj, port, allow_localhost=allow_localhost)
        if not valid:
            return False, f"Destination IP {ip_obj} rejected: {reason}", None

    # Pin to the first validated IP address
    pinned_ip = ips_or_err[0]
    pinned_ip_str = str(pinned_ip)

    host_header = hostname_clean
    if (parsed.scheme == "http" and port != 80) or (parsed.scheme == "https" and port != 443):
        host_header = f"{hostname_clean}:{port}"

    path_and_query = parsed.path
    if not path_and_query:
        path_and_query = "/"
    if parsed.query:
        path_and_query += f"?{parsed.query}"

    if isinstance(pinned_ip, ipaddress.IPv6Address):
        pinned_netloc = f"[{pinned_ip_str}]:{port}"
    else:
        pinned_netloc = f"{pinned_ip_str}:{port}"

    pinned_url = f"{parsed.scheme}://{pinned_netloc}{path_and_query}"

    pin_info = {
        "original_url": cleaned_url,
        "pinned_url": pinned_url,
        "pinned_ip": pinned_ip_str,
        "hostname": hostname_clean,
        "port": port,
        "host_header": host_header,
        "is_https": parsed.scheme == "https"
    }

    return True, "URL is valid and pinned.", pin_info

def validate_endpoint_url(url: str, allow_localhost: bool = True) -> Tuple[bool, str]:
    """
    Validate inference and VPS endpoint URLs against SSRF, cloud metadata, and DNS-rebinding risks.
    Resolves all destination IPs and validates against destination policy.
    """
    is_safe, reason, _ = resolve_and_pin_endpoint_url(url, allow_localhost=allow_localhost)
    return is_safe, reason

async def safe_execute_http(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    max_redirects: int = 3,
    **kwargs
) -> httpx.Response:
    """
    Executes an HTTP request with strict DNS-rebinding (TOCTOU) defense and redirect revalidation.
    Pins the socket connection directly to the pre-validated IP address, while preserving
    Host header and TLS Server Name Indication (SNI).
    """
    current_url = url
    for _ in range(max_redirects + 1):
        is_safe, reason, pin_info = resolve_and_pin_endpoint_url(current_url, allow_localhost=True)
        if not is_safe or not pin_info:
            raise ValueError(f"Outbound request blocked by SSRF policy: {reason}")

        headers = dict(kwargs.get("headers", {}))
        if "Host" not in headers and "host" not in headers:
            headers["Host"] = pin_info["host_header"]

        req_kwargs = dict(kwargs)
        req_kwargs["headers"] = headers

        if pin_info["is_https"]:
            extensions = dict(req_kwargs.get("extensions", {}))
            extensions["sni_hostname"] = pin_info["hostname"]
            req_kwargs["extensions"] = extensions

        resp = await client.request(
            method,
            pin_info["pinned_url"],
            follow_redirects=False,
            **req_kwargs
        )
        
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location")
            if not location:
                return resp
            current_url = urljoin(current_url, location)
            continue
        return resp

    raise ValueError(f"Exceeded maximum redirects ({max_redirects})")
