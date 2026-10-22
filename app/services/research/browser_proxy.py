"""
Local Outbound Filtering Proxy for Browser Egress Security (F-14 / ADR-06).

Provides network-restricted egress for headless browser processes (Playwright/Chromium):
- Pins outbound socket connections to verified destination IP addresses.
- Blocks connections to localhost, private RFC1918, link-local, multicast, cloud metadata, and Tailscale internal endpoints.
- Intercepts both HTTP and HTTPS/WSS (CONNECT tunneling).
- Retains an immutable audit log of all connection attempts, destinations, and policy decisions.
- Fails closed if proxy unavailable or connection violates security policy.
"""

from __future__ import annotations

import logging
import select
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from app.services.research.policy import ResearchSecurityPolicy, get_security_policy

logger = logging.getLogger("qwen_collective.research.browser_proxy")


@dataclass
class ProxyAuditRecord:
    timestamp: float
    client_ip: str
    target_host: str
    target_port: int
    method: str
    is_allowed: bool
    reason: str
    resolved_ips: List[str]
    connected_ip: Optional[str] = None


class OutboundFilteringProxy:
    """
    Local TCP proxy server for browser egress destination-IP enforcement.
    Runs on loopback (127.0.0.1) on an ephemeral port.
    """

    def __init__(
        self,
        policy: Optional[ResearchSecurityPolicy] = None,
        bind_host: str = "127.0.0.1",
        port: int = 0,
        timeout: float = 15.0,
    ):
        self.policy = policy or get_security_policy()
        self.bind_host = bind_host
        self.requested_port = port
        self.timeout = timeout
        self.audit_log: List[ProxyAuditRecord] = []
        self._audit_lock = threading.Lock()

        self._server_socket: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self.port: int = 0
        self.proxy_url: str = ""

    def start(self) -> OutboundFilteringProxy:
        """Start the proxy listener thread."""
        if self._running:
            return self

        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.bind((self.bind_host, self.requested_port))
        self._server_socket.listen(64)
        self.port = self._server_socket.getsockname()[1]
        self.proxy_url = f"http://{self.bind_host}:{self.port}"
        self._running = True

        self._thread = threading.Thread(target=self._serve_loop, daemon=True, name="BrowserFilteringProxy")
        self._thread.start()
        logger.info("OutboundFilteringProxy started on %s", self.proxy_url)
        return self

    def close(self):
        """Stop proxy server and close listening socket."""
        self._running = False
        if self._server_socket:
            try:
                self._server_socket.close()
            except Exception:
                pass
            self._server_socket = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        logger.info("OutboundFilteringProxy stopped.")

    def __enter__(self) -> OutboundFilteringProxy:
        return self.start()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def record_audit(self, record: ProxyAuditRecord):
        with self._audit_lock:
            self.audit_log.append(record)

    def get_audit_log(self) -> List[ProxyAuditRecord]:
        with self._audit_lock:
            return list(self.audit_log)

    def _serve_loop(self):
        while self._running and self._server_socket:
            try:
                r, _, _ = select.select([self._server_socket], [], [], 0.3)
                if not r or not self._running:
                    continue
                client_sock, client_addr = self._server_socket.accept()
                client_thread = threading.Thread(
                    target=self._handle_client,
                    args=(client_sock, client_addr),
                    daemon=True,
                )
                client_thread.start()
            except Exception as e:
                if self._running:
                    logger.debug("Proxy serve_loop exception: %s", e)
                break

    def _handle_client(self, client_sock: socket.socket, client_addr: Tuple[str, int]):
        client_sock.settimeout(self.timeout)
        try:
            # Read HTTP request header
            raw_req = b""
            while b"\r\n\r\n" not in raw_req and len(raw_req) < 65536:
                chunk = client_sock.recv(4096)
                if not chunk:
                    break
                raw_req += chunk

            if not raw_req:
                client_sock.close()
                return

            header_part = raw_req.split(b"\r\n\r\n", 1)[0]
            lines = header_part.decode("iso-8859-1", errors="replace").split("\r\n")
            if not lines or not lines[0]:
                client_sock.close()
                return

            request_line = lines[0]
            parts = request_line.split()
            if len(parts) < 2:
                client_sock.close()
                return

            method = parts[0].upper()
            target = parts[1]

            if method == "CONNECT":
                # HTTPS / WSS tunneling
                if target.startswith("["):
                    closing = target.rfind("]")
                    target_host = target[1:closing]
                    rest = target[closing + 1:]
                    target_port = int(rest.lstrip(":")) if rest.startswith(":") else 443
                elif ":" in target:
                    target_host, port_str = target.rsplit(":", 1)
                    target_port = int(port_str)
                else:
                    target_host = target
                    target_port = 443
            else:
                # Plain HTTP proxy request (e.g. GET http://example.com/path HTTP/1.1 or GET /path HTTP/1.1)
                parsed = urlsplit(target)
                target_host = parsed.hostname or ""
                target_port = parsed.port or (443 if parsed.scheme == "https" else 80)
                if not target_host:
                    # Fallback to Host header
                    for line in lines[1:]:
                        if line.lower().startswith("host:"):
                            host_val = line.split(":", 1)[1].strip()
                            if host_val.startswith("["):
                                closing = host_val.rfind("]")
                                target_host = host_val[1:closing]
                                rest = host_val[closing + 1:]
                                target_port = int(rest.lstrip(":")) if rest.startswith(":") else 80
                            elif ":" in host_val:
                                target_host, port_str = host_val.rsplit(":", 1)
                                try:
                                    target_port = int(port_str)
                                except ValueError:
                                    target_port = 80
                            else:
                                target_host = host_val
                                target_port = 80
                            break

            # Enforce destination policy and resolve IP
            url_host = f"[{target_host}]" if ":" in target_host and not target_host.startswith("[") else target_host
            is_valid, reason, _ = self.policy.validate_url(f"http://{url_host}:{target_port}")
            is_dest_valid, dest_reason, resolved_ips = self.policy.resolve_and_verify_destination(target_host)

            if not is_valid or not is_dest_valid or not resolved_ips:
                denial_reason = dest_reason or reason or "Forbidden destination"
                logger.warning(
                    "Proxy blocked connection to %s:%d from %s: %s",
                    target_host, target_port, client_addr[0], denial_reason
                )
                self.record_audit(ProxyAuditRecord(
                    timestamp=time.time(),
                    client_ip=client_addr[0],
                    target_host=target_host,
                    target_port=target_port,
                    method=method,
                    is_allowed=False,
                    reason=denial_reason,
                    resolved_ips=resolved_ips or [],
                    connected_ip=None,
                ))

                body = f"HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain\r\nX-Proxy-Blocked: SSRF-Violation\r\nConnection: close\r\n\r\nSSRF Blocked: {denial_reason}\r\n"
                try:
                    client_sock.sendall(body.encode("ascii"))
                except Exception:
                    pass
                client_sock.close()
                return

            # Permitted destination: pin to resolved IP
            pinned_ip = resolved_ips[0]
            self.record_audit(ProxyAuditRecord(
                timestamp=time.time(),
                client_ip=client_addr[0],
                target_host=target_host,
                target_port=target_port,
                method=method,
                is_allowed=True,
                reason="Permitted public destination",
                resolved_ips=resolved_ips,
                connected_ip=pinned_ip,
            ))

            # Connect socket to pinned IP using dual-stack create_connection
            try:
                remote_sock = socket.create_connection((pinned_ip, target_port), timeout=self.timeout)
            except Exception as conn_err:
                logger.warning("Proxy failed to connect to pinned IP %s:%d: %s", pinned_ip, target_port, conn_err)
                err_resp = f"HTTP/1.1 502 Bad Gateway\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\nConnection failed to {pinned_ip}: {conn_err}\r\n"
                try:
                    client_sock.sendall(err_resp.encode("ascii"))
                except Exception:
                    pass
                client_sock.close()
                return

            if method == "CONNECT":
                # Established tunnel response
                client_sock.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                self._tunnel_sockets(client_sock, remote_sock)
            else:
                # Plain HTTP: forward initial request with rewritten target URI
                parsed = urlsplit(target)
                relative_uri = parsed.path or "/"
                if parsed.query:
                    relative_uri += f"?{parsed.query}"
                rewritten_first_line = f"{method} {relative_uri} {parts[2] if len(parts) > 2 else 'HTTP/1.1'}"
                lines[0] = rewritten_first_line
                rewritten_headers = "\r\n".join(lines).encode("iso-8859-1") + b"\r\n\r\n"

                # Send rewritten headers plus any body already received
                body_already = raw_req.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in raw_req else b""
                remote_sock.sendall(rewritten_headers + body_already)
                self._tunnel_sockets(client_sock, remote_sock)

        except Exception as ex:
            logger.debug("Exception handling proxy client %s: %s", client_addr, ex)
            try:
                client_sock.close()
            except Exception:
                pass

    def _tunnel_sockets(self, s1: socket.socket, s2: socket.socket):
        """Bidirectional TCP pipe using select loop."""
        sockets = [s1, s2]
        try:
            while self._running:
                r, _, x = select.select(sockets, [], sockets, 10.0)
                if x:
                    break
                if not r:
                    continue
                for sock in r:
                    other = s2 if sock is s1 else s1
                    data = sock.recv(32768)
                    if not data:
                        return
                    other.sendall(data)
        except Exception:
            pass
        finally:
            try:
                s1.close()
            except Exception:
                pass
            try:
                s2.close()
            except Exception:
                pass
