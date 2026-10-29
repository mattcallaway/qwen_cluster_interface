import socket
import struct
from typing import Optional


def send_framed_bytes(sock: socket.socket, payload: bytes) -> None:
    """
    Sends a 4-byte big-endian length prefix followed by the payload bytes.
    """
    length_prefix = struct.pack(">I", len(payload))
    sock.sendall(length_prefix + payload)


def recv_exact(sock: socket.socket, num_bytes: int) -> Optional[bytes]:
    """
    Reads exactly num_bytes from a socket, or returns None if the socket was closed.
    """
    buf = bytearray()
    while len(buf) < num_bytes:
        chunk = sock.recv(num_bytes - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def recv_framed_bytes(sock: socket.socket) -> Optional[bytes]:
    """
    Reads a 4-byte length prefix and then reads the exact payload.
    Returns None on clean socket closure.
    """
    prefix = recv_exact(sock, 4)
    if prefix is None:
        return None
    (length,) = struct.unpack(">I", prefix)
    if length > 16 * 1024 * 1024:  # 16 MB safety limit
        raise ValueError(f"Framed payload exceeds limit: {length} bytes")
    return recv_exact(sock, length)
