import hashlib
import hmac
import struct
import time
from dataclasses import dataclass
from typing import List, Sequence, Tuple, Union

MAGIC_BYTES = b"QCACT1"  # Qwen Collective Activation Protocol v1
MAX_PAYLOAD_BYTES = 262144  # 256 KB max per token hidden activation frame


@dataclass(frozen=True)
class ActivationFrame:
    """Parsed activation frame metadata passed between pipeline stages."""
    session_id: str
    stage_index: int
    token_index: int
    hidden_dim: int
    tensor_dtype: str
    timestamp: float
    hmac_signature: str
    payload_bytes: bytes


class ActivationProtocol:
    """
    Serializes and verifies activation tensor frames streamed between cluster nodes.
    Guarantees cryptographic authenticity (HMAC-SHA256) and bounds checks.
    """

    def __init__(self, secret_key: Union[str, bytes]):
        if isinstance(secret_key, str):
            secret_key = secret_key.encode("utf-8")
        if len(secret_key) < 16:
            raise ValueError("ActivationProtocol secret_key must be at least 16 bytes")
        self.secret_key = secret_key

    def compute_hmac(self, header_bytes: bytes, payload_bytes: bytes) -> str:
        h = hmac.new(self.secret_key, digestmod=hashlib.sha256)
        h.update(header_bytes)
        h.update(payload_bytes)
        return h.hexdigest()

    def encode_frame(
        self,
        session_id: str,
        stage_index: int,
        token_index: int,
        activations: Sequence[float],
        tensor_dtype: str = "float32",
    ) -> bytes:
        """
        Packs activations into a authenticated binary frame.
        Supported dtypes: 'float32' (4 bytes/val) and 'float16' (2 bytes/val).
        """
        hidden_dim = len(activations)
        if hidden_dim < 1 or hidden_dim > 16384:
            raise ValueError(f"hidden_dim must be between 1 and 16384, got {hidden_dim}")

        format_char = "e" if tensor_dtype == "float16" else "f"
        payload_bytes = struct.pack(f">{hidden_dim}{format_char}", *activations)

        if len(payload_bytes) > MAX_PAYLOAD_BYTES:
            raise ValueError(f"Payload size {len(payload_bytes)} exceeds {MAX_PAYLOAD_BYTES}")

        ts = time.time()
        session_bytes = session_id.encode("ascii")
        if len(session_bytes) > 64:
            raise ValueError("session_id too long (> 64 chars)")

        dtype_code = 1 if tensor_dtype == "float16" else 2

        # Header packing format:
        # MAGIC(6s) + session_len(B) + session(64s) + stage(H) + token(I) + hidden_dim(I) + dtype(B) + ts(d)
        header_raw = struct.pack(
            ">6sB64sHIIBd",
            MAGIC_BYTES,
            len(session_bytes),
            session_bytes.ljust(64, b"\x00"),
            stage_index,
            token_index,
            hidden_dim,
            dtype_code,
            ts,
        )

        signature = self.compute_hmac(header_raw, payload_bytes)
        sig_bytes = signature.encode("ascii")  # 64 bytes hex

        return header_raw + sig_bytes + payload_bytes

    def decode_frame(self, frame_bytes: bytes) -> Tuple[ActivationFrame, List[float]]:
        """
        Decodes and cryptographically verifies an activation frame.
        Returns: (ActivationFrame, unpacked_float_activations)
        """
        min_header_size = struct.calcsize(">6sB64sHIIBd") + 64  # header + 64-byte hex HMAC
        if len(frame_bytes) < min_header_size:
            raise ValueError("Frame size too small")

        header_len = struct.calcsize(">6sB64sHIIBd")
        header_raw = frame_bytes[:header_len]
        sig_bytes = frame_bytes[header_len : header_len + 64]
        payload_bytes = frame_bytes[header_len + 64 :]

        (
            magic,
            sess_len,
            sess_padded,
            stage_index,
            token_index,
            hidden_dim,
            dtype_code,
            ts,
        ) = struct.unpack(">6sB64sHIIBd", header_raw)

        if magic != MAGIC_BYTES:
            raise ValueError("Invalid magic bytes in activation frame")

        session_id = sess_padded[:sess_len].decode("ascii")
        received_sig = sig_bytes.decode("ascii")

        # Verify cryptographic HMAC signature
        expected_sig = self.compute_hmac(header_raw, payload_bytes)
        if not hmac.compare_digest(received_sig, expected_sig):
            raise ValueError("Cryptographic verification failed: HMAC signature mismatch")

        tensor_dtype = "float16" if dtype_code == 1 else "float32"
        format_char = "e" if dtype_code == 1 else "f"

        expected_payload_len = hidden_dim * (2 if dtype_code == 1 else 4)
        if len(payload_bytes) != expected_payload_len:
            raise ValueError(
                f"Mismatched payload size: expected {expected_payload_len}, got {len(payload_bytes)}"
            )

        activations = list(struct.unpack(f">{hidden_dim}{format_char}", payload_bytes))

        frame = ActivationFrame(
            session_id=session_id,
            stage_index=stage_index,
            token_index=token_index,
            hidden_dim=hidden_dim,
            tensor_dtype=tensor_dtype,
            timestamp=ts,
            hmac_signature=received_sig,
            payload_bytes=payload_bytes,
        )

        return frame, activations
