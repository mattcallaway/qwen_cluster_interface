"""
ASGI Transport-Layer Upload Size Enforcement and Ingestion Protection.
Enforces Task 8C.3:
- Rejects oversized Content-Length before body buffering (fails closed with HTTP 413).
- Incrementally tracks received bytes during ASGI stream reception.
- Immediately aborts reception upon exceeding limit without unbounded buffering or draining.
- Clearly distinguishes raw 25MB artifact payload from multipart framing overhead.
- Validates transfer metadata and rejects malformed/negative/conflicting Content-Length (HTTP 400).
- Bounded memory guarantees for all HTTP endpoints.
"""
from __future__ import annotations

import json
import logging
import math
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("qwen_collective.upload_security")

# Exact Byte Definitions (Task 8C.3)
# 1. Maximum raw artifact payload: exactly 25.0 MiB
MAX_ARTIFACT_PAYLOAD_BYTES: int = 25 * 1024 * 1024  # 26,214,400 bytes

# 2. Maximum multipart framing overhead: 64.0 KiB
# Covers MIME boundaries, Content-Disposition, headers, and form metadata fields (expected_sha256, lease_id)
MAX_MULTIPART_FRAMING_BYTES: int = 64 * 1024  # 65,536 bytes

# 3. Maximum permitted multipart transport stream: payload + framing
MAX_MULTIPART_REQUEST_BYTES: int = MAX_ARTIFACT_PAYLOAD_BYTES + MAX_MULTIPART_FRAMING_BYTES  # 26,279,936 bytes

# 4. Maximum Base64 payload encoding: 25 MiB * 4/3
MAX_BASE64_PAYLOAD_BYTES: int = math.ceil(MAX_ARTIFACT_PAYLOAD_BYTES / 3) * 4  # 34,952,536 bytes

# 5. Maximum JSON framing overhead: 1.0 MiB for keys, whitespace, and task metadata
MAX_JSON_FRAMING_BYTES: int = 1024 * 1024  # 1,048,576 bytes

# 6. Maximum permitted JSON request body: Base64 payload + framing headroom (exact sum)
MAX_JSON_REQUEST_BYTES: int = MAX_BASE64_PAYLOAD_BYTES + MAX_JSON_FRAMING_BYTES  # 36,001,112 bytes

# 7. Maximum default body limit for all standard API endpoints: 10.0 MiB
MAX_DEFAULT_BODY_BYTES: int = 10 * 1024 * 1024  # 10,485,760 bytes

# Regex pattern for worker artifact ingestion endpoint
_WORKER_ARTIFACT_PATTERN = re.compile(r"^/api/v1/worker/tasks/[^/]+/artifacts/?$", re.IGNORECASE)
_GENERAL_ARTIFACT_PATTERN = re.compile(r"^/api/v1/artifacts.*$", re.IGNORECASE)


class PayloadTooLargeError(Exception):
    """Raised when an incoming request body exceeds the transport size limit."""
    def __init__(self, message: str, limit_bytes: int, received_bytes: int):
        super().__init__(message)
        self.message = message
        self.limit_bytes = limit_bytes
        self.received_bytes = received_bytes


class MalformedTransferMetadataError(Exception):
    """Raised when transfer metadata (Content-Length, Transfer-Encoding) is malformed."""
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def resolve_request_limit(path: str, method: str, content_type: str) -> int:
    """
    Determine the strict byte limit for an incoming request based on target endpoint
    and declared content type. Path is normalized to lowercase and stripped of trailing slashes.
    """
    norm_path = path.lower().rstrip("/")
    if not norm_path.endswith("/artifacts"):
        norm_path_with_slash = norm_path + "/"
    else:
        norm_path_with_slash = norm_path
        
    is_artifact_upload = bool(
        _WORKER_ARTIFACT_PATTERN.match(norm_path) or
        _WORKER_ARTIFACT_PATTERN.match(norm_path_with_slash) or
        _GENERAL_ARTIFACT_PATTERN.match(norm_path)
    )
    
    if is_artifact_upload:
        ct = content_type.lower()
        if "multipart/form-data" in ct:
            return MAX_MULTIPART_REQUEST_BYTES
        elif "application/octet-stream" in ct:
            return MAX_ARTIFACT_PAYLOAD_BYTES
        elif "application/json" in ct:
            return MAX_JSON_REQUEST_BYTES
        else:
            # Fallback for artifact upload routes: default to multipart allowance
            return MAX_MULTIPART_REQUEST_BYTES
    
    return MAX_DEFAULT_BODY_BYTES


def parse_and_validate_transfer_metadata(headers: List[Tuple[bytes, bytes]]) -> Optional[int]:
    """
    Extract, validate, and parse transfer metadata from raw ASGI headers.
    Enforces RFC 7230 §3.3.3 and RFC 9112 §6.1:
    - If both Transfer-Encoding and Content-Length are present -> MalformedTransferMetadataError
    - Multiple conflicting Content-Length headers -> MalformedTransferMetadataError
    - Non-numeric or negative values -> MalformedTransferMetadataError
    - Absent -> None (chunked or streaming transfer)
    """
    has_transfer_encoding = False
    cl_values: List[str] = []
    
    for k, v in headers:
        k_lower = k.lower()
        if k_lower == b"transfer-encoding":
            has_transfer_encoding = True
        elif k_lower == b"content-length":
            cl_values.append(v.decode("latin1", errors="replace").strip())
    
    # RFC 7230 §3.3.3: Content-Length and Transfer-Encoding combined is an HTTP desync risk
    if has_transfer_encoding and cl_values:
        raise MalformedTransferMetadataError(
            "Request cannot contain both Transfer-Encoding and Content-Length headers (RFC 7230/9112 desync protection)."
        )
    
    if not cl_values:
        return None
    
    if len(cl_values) > 1:
        # Multiple Content-Length headers are strictly invalid unless identical
        unique = set(cl_values)
        if len(unique) > 1:
            raise MalformedTransferMetadataError(
                f"Conflicting multiple Content-Length headers detected: {cl_values}"
            )
    
    raw_val = cl_values[0]
    
    # Handle comma-separated duplicate list if sent by proxy (e.g. "100, 100")
    if "," in raw_val:
        parts = [p.strip() for p in raw_val.split(",")]
        if len(set(parts)) > 1:
            raise MalformedTransferMetadataError(
                f"Conflicting comma-separated Content-Length values: '{raw_val}'"
            )
        raw_val = parts[0]
    
    if not raw_val.isdigit():
        raise MalformedTransferMetadataError(
            f"Malformed non-numeric Content-Length value: '{raw_val}'"
        )
    
    try:
        val = int(raw_val)
    except ValueError:
        raise MalformedTransferMetadataError(
            f"Invalid integer in Content-Length: '{raw_val}'"
        )
    
    if val < 0:
        raise MalformedTransferMetadataError(
            f"Negative Content-Length value rejected: {val}"
        )
    
    return val


def get_header_value(headers: List[Tuple[bytes, bytes]], key: bytes) -> str:
    """Retrieve string value for a specific header key from ASGI headers."""
    target = key.lower()
    for k, v in headers:
        if k.lower() == target:
            return v.decode("latin1", errors="replace")
    return ""


class UploadSizeLimitMiddleware:
    """
    ASGI transport-level middleware that enforces upload size limits before and during
    request body reception.
    """
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET").upper()
        path = scope.get("path", "")
        headers = scope.get("headers", [])
        
        content_type = get_header_value(headers, b"content-type")
        
        # 1. Parse and validate transfer metadata
        try:
            declared_content_length = parse_and_validate_transfer_metadata(headers)
        except MalformedTransferMetadataError as ex:
            logger.warning("Rejected request with malformed transfer metadata on %s %s: %s", method, path, ex)
            await self._send_json_error(
                send,
                status_code=400,
                detail=f"Malformed transfer metadata: {ex.message}"
            )
            return

        limit = resolve_request_limit(path, method, content_type)

        # 2. Pre-buffering Content-Length verification (zero bytes buffered if exceeded)
        if declared_content_length is not None and declared_content_length > limit:
            logger.warning(
                "Rejected oversized upload before buffering on %s %s: declared Content-Length %d exceeds limit %d",
                method, path, declared_content_length, limit
            )
            await self._send_json_error(
                send,
                status_code=413,
                detail=f"Payload Too Large: declared Content-Length {declared_content_length} exceeds maximum allowed size of {limit} bytes."
            )
            return

        # 3. Incremental body-receive bounding
        bytes_received = 0
        response_started = False

        async def bounded_receive() -> Message:
            nonlocal bytes_received
            message = await receive()
            if message["type"] == "http.request":
                chunk = message.get("body", b"")
                bytes_received += len(chunk)
                if bytes_received > limit:
                    logger.warning(
                        "Streaming body exceeded limit on %s %s: received %d bytes (limit %d)",
                        method, path, bytes_received, limit
                    )
                    raise PayloadTooLargeError(
                        f"Payload Too Large: received {bytes_received} bytes exceeding maximum allowed size of {limit} bytes.",
                        limit_bytes=limit,
                        received_bytes=bytes_received
                    )
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, bounded_receive, tracking_send)
        except PayloadTooLargeError as pte:
            if not response_started:
                await self._send_json_error(
                    send,
                    status_code=413,
                    detail=pte.message
                )
            else:
                logger.warning("Payload limit exceeded but response already started; avoiding duplicate response on %s %s", method, path)
            return
        except Exception:
            raise

    async def _send_json_error(self, send: Send, status_code: int, detail: str) -> None:
        """Send an immediate JSON error response and request socket closure."""
        body = json.dumps({"detail": detail}).encode("utf-8")
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"connection", b"close"),
        ]
        await send({
            "type": "http.response.start",
            "status": status_code,
            "headers": headers,
        })
        await send({
            "type": "http.response.body",
            "body": body,
            "more_body": False,
        })
