import ipaddress
import socket
from urllib.parse import urlparse
from typing import Tuple, Optional, List, Union

# Cloud metadata addresses and hostnames to strictly block
BLOCKED_METADATA_IPS = {
    "169.254.169.254", # AWS, GCP, Azure, OpenStack IPv4 metadata
    "fd00:ec2::254",    # AWS IPv6 metadata
}

BLOCKED_METADATA_HOSTS = {
    "metadata.google.internal",
    "metadata.internal",
    "instance-data",
}

# Standard allowed ports for machine ping / reachability testing
ALLOWED_PROBE_PORTS = {
    22,     # SSH
    80,     # HTTP
    443,    # HTTPS
    8000,   # Alternative HTTP / Control Server
    8080,   # llama-server HTTP
    8081,   # VPS Companion Worker
    11434,  # Ollama API
    50052,  # llama.cpp RPC Server
}

ALLOWED_LOCALHOST_PORTS = {
    8000,   # Control Server
    8080,   # Local llama-server
    8081,   # Local test worker
    11434,  # Local Ollama
    50052,  # Local RPC worker
}

# Tailscale CGNAT subnet (100.64.0.0/10)
TAILSCALE_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")

def is_cloud_metadata(ip_or_host: str) -> bool:
    """Check if address matches known cloud provider metadata endpoints."""
    cleaned = ip_or_host.strip().lower().strip("[]")
    if cleaned in BLOCKED_METADATA_HOSTS or cleaned in BLOCKED_METADATA_IPS:
        return True
    try:
        ip_obj = ipaddress.ip_address(cleaned)
        if ip_obj.is_link_local:
            return True
        if str(ip_obj) in BLOCKED_METADATA_IPS:
            return True
    except ValueError:
        pass
    return False

def resolve_hostname_ips(hostname: str, port: int) -> Tuple[bool, Union[List[Union[ipaddress.IPv4Address, ipaddress.IPv6Address]], str]]:
    """
    Resolve a hostname or IP string to all destination IP objects via socket.getaddrinfo.
    Returns: (success, list_of_ips_or_error_string)
    """
    clean_host = hostname.strip().lower().strip("[]")
    if not clean_host:
        return False, "Target host cannot be empty."

    # Direct IP literal check
    try:
        ip_obj = ipaddress.ip_address(clean_host)
        return True, [ip_obj]
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
                ips.append(ipaddress.ip_address(ip_str))
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
    ip_str = str(ip_obj)

    # 1. Reject cloud metadata & link-local
    if ip_str in BLOCKED_METADATA_IPS or ip_obj.is_link_local:
        return False, f"Access to link-local / cloud metadata address ({ip_str}) is strictly blocked."

    # 2. Handle Loopback (Note: IPv6 ::1 is both loopback and reserved in python ipaddress)
    if ip_obj.is_loopback:
        if not allow_localhost:
            return False, f"Loopback address ({ip_str}) is prohibited for this endpoint."
        if port not in ALLOWED_LOCALHOST_PORTS:
            return False, f"Localhost access to port {port} is prohibited. Allowed local ports: {sorted(ALLOWED_LOCALHOST_PORTS)}."
        return True, "Authorized localhost destination."

    # 3. Reject multicast, unspecified, reserved
    if ip_obj.is_multicast or ip_obj.is_unspecified or ip_obj.is_reserved:
        return False, f"Target IP ({ip_str}) is not a permitted unicast destination."

    # 4. Authorized Destinations:
    # - Private RFC1918 / ULA IPv6
    # - Tailscale CGNAT (100.64.0.0/10)
    # - Publicly routable unicast (global)
    is_private = ip_obj.is_private
    is_tailscale = (isinstance(ip_obj, ipaddress.IPv4Address) and ip_obj in TAILSCALE_CGNAT_NET)
    is_global = ip_obj.is_global

    if is_private or is_tailscale or is_global:
        return True, "Authorized network destination."

    return False, f"IP address ({ip_str}) does not match authorized collective network policy."

def validate_probe_target(host: str, port: int) -> Tuple[bool, str]:
    """
    Validate that a machine connectivity probe is safe and not an SSRF or metadata probe.
    Resolves hostname and verifies destination IP.
    """
    host_clean = host.strip().lower().strip("[]")
    if not host_clean:
        return False, "Target host cannot be empty."

    if is_cloud_metadata(host_clean):
        return False, "Access to cloud metadata endpoints is strictly blocked."

    if port not in ALLOWED_PROBE_PORTS:
        return False, f"Port {port} is not in the allowed collective probe ports ({sorted(ALLOWED_PROBE_PORTS)})."

    success, ips_or_err = resolve_hostname_ips(host_clean, port)
    if not success:
        return False, str(ips_or_err)

    for ip_obj in ips_or_err:
        valid, reason = validate_destination_ip(ip_obj, port, allow_localhost=True)
        if not valid:
            return False, f"Probe target rejected for IP {ip_obj}: {reason}"

    return True, "Target is valid."

def validate_endpoint_url(url: str, allow_localhost: bool = True) -> Tuple[bool, str]:
    """
    Validate inference and VPS endpoint URLs against SSRF, cloud metadata, and DNS-rebinding risks.
    Resolves all destination IPs and validates against destination policy.
    """
    try:
        parsed = urlparse(url.strip())
    except Exception as e:
        return False, f"Malformed URL: {str(e)}"

    if parsed.scheme not in ("http", "https"):
        return False, f"Unsupported URL scheme '{parsed.scheme}'. Only http:// and https:// are permitted."

    hostname = parsed.hostname
    if not hostname:
        return False, "URL must contain a valid hostname or IP address."

    hostname_clean = hostname.strip().lower().strip("[]")
    if is_cloud_metadata(hostname_clean):
        return False, "Access to cloud metadata services (169.254.169.254) is strictly prohibited."

    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    # Resolve and validate all destination IPs
    success, ips_or_err = resolve_hostname_ips(hostname_clean, port)
    if not success:
        return False, str(ips_or_err)

    for ip_obj in ips_or_err:
        valid, reason = validate_destination_ip(ip_obj, port, allow_localhost=allow_localhost)
        if not valid:
            return False, f"Destination IP {ip_obj} rejected: {reason}"

    return True, "URL is valid."
