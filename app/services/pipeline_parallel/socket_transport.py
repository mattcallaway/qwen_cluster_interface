import socket
import struct
from typing import Optional

from .activation_protocol import MAX_FRAME_BYTES

DEFAULT_MAX_WIRE_FRAME_BYTES = MAX_FRAME_BYTES + 4096  # ~266 KB bounded wire frame limit


def send_framed_bytes(sock: socket.socket, payload: bytes) -> None:
    """
    Sends a 4-byte big-endian length prefix followed by the payload bytes.
    """
    if len(payload) > DEFAULT_MAX_WIRE_FRAME_BYTES:
        raise ValueError(f"Payload exceeds wire frame limit: {len(payload)} > {DEFAULT_MAX_WIRE_FRAME_BYTES}")
    length_prefix = struct.pack(">I", len(payload))
    sock.sendall(length_prefix + payload)


def recv_exact(sock: socket.socket, num_bytes: int) -> Optional[bytes]:
    """
    Reads exactly num_bytes from a socket, or returns None if the socket was closed cleanly
    at the start of a frame. Raises ConnectionError if truncated mid-read.
    """
    buf = bytearray()
    while len(buf) < num_bytes:
        chunk = sock.recv(min(65536, num_bytes - len(buf)))
        if not chunk:
            if len(buf) == 0:
                return None
            raise ConnectionError(
                f"Socket closed mid-frame: expected {num_bytes} bytes, received only {len(buf)} bytes"
            )
        buf.extend(chunk)
    return bytes(buf)


def recv_framed_bytes(
    sock: socket.socket,
    max_frame_bytes: int = DEFAULT_MAX_WIRE_FRAME_BYTES,
) -> Optional[bytes]:
    """
    Reads a 4-byte length prefix and then reads the exact payload.
    Returns None on clean socket closure before prefix.
    Raises ValueError on zero or oversized length prefix, or ConnectionError on truncated frame.
    """
    prefix = recv_exact(sock, 4)
    if prefix is None:
        return None
    (length,) = struct.unpack(">I", prefix)
    if length == 0:
        raise ValueError("Zero-length framed payload rejected")
    if length > max_frame_bytes:
        raise ValueError(f"Framed payload exceeds limit: {length} > {max_frame_bytes} bytes")
    data = recv_exact(sock, length)
    if data is None:
        raise ConnectionError(f"Socket closed immediately after length prefix ({length} bytes expected)")
    return data
