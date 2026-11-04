import hashlib
import hmac
import math
import struct
import time
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple, Union

MAGIC_BYTES = b"QCACT1"  # Qwen Collective Activation Protocol v1
MAX_PAYLOAD_BYTES = 262144  # 256 KB max per token hidden activation frame
HEADER_FORMAT = ">6sB64sHIIBd"
HEADER_RAW_SIZE = struct.calcsize(HEADER_FORMAT)  # 83 bytes
HMAC_HEX_SIZE = 64
MIN_FRAME_SIZE = HEADER_RAW_SIZE + HMAC_HEX_SIZE  # 147 bytes
MAX_FRAME_BYTES = MIN_FRAME_SIZE + MAX_PAYLOAD_BYTES


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
    Guarantees cryptographic authenticity (HMAC-SHA256), timestamp freshness,
    dtype validity, finite numeric values, and strict bounds checks.
    """

    def __init__(
        self,
        secret_key: Union[str, bytes],
        max_age_seconds: float = 60.0,
        max_future_skew_seconds: float = 10.0,
    ):
        if isinstance(secret_key, str):
            secret_key = secret_key.encode("utf-8")
        if len(secret_key) < 16:
            raise ValueError("ActivationProtocol secret_key must be at least 16 bytes")
        if max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive")
        self.secret_key = secret_key
        self.max_age_seconds = float(max_age_seconds)
        self.max_future_skew_seconds = float(max_future_skew_seconds)

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
        timestamp: Optional[float] = None,
    ) -> bytes:
        """
        Packs activations into an authenticated binary frame.
        Supported dtypes: 'float32' (4 bytes/val) and 'float16' (2 bytes/val).
        """
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        try:
            session_bytes = session_id.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError("session_id must be ASCII") from exc
        if len(session_bytes) < 1 or len(session_bytes) > 64:
            raise ValueError("session_id length must be between 1 and 64 ASCII chars")
        if any(b < 0x20 or b > 0x7E for b in session_bytes):
            raise ValueError("session_id must contain printable ASCII characters only")

        if stage_index < 0 or stage_index > 65535:
            raise ValueError(f"stage_index out of range: {stage_index}")
        if token_index < 0 or token_index > 0xFFFFFFFF:
            raise ValueError(f"token_index out of range: {token_index}")

        if tensor_dtype == "float16":
            dtype_code = 1
            format_char = "e"
        elif tensor_dtype == "float32":
            dtype_code = 2
            format_char = "f"
        else:
            raise ValueError(f"Unsupported tensor_dtype: {tensor_dtype!r}. Expected 'float16' or 'float32'")

        hidden_dim = len(activations)
        if hidden_dim < 1 or hidden_dim > 16384:
            raise ValueError(f"hidden_dim must be between 1 and 16384, got {hidden_dim}")

        for v in activations:
            if not math.isfinite(v):
                raise ValueError("Activation vector contains non-finite (NaN/Inf) value")

        payload_bytes = struct.pack(f">{hidden_dim}{format_char}", *activations)

        if len(payload_bytes) > MAX_PAYLOAD_BYTES:
            raise ValueError(f"Payload size {len(payload_bytes)} exceeds {MAX_PAYLOAD_BYTES}")

        ts = float(time.time() if timestamp is None else timestamp)
        if not math.isfinite(ts):
            raise ValueError("Invalid non-finite timestamp")

        # Header packing format:
        # MAGIC(6s) + session_len(B) + session(64s) + stage(H) + token(I) + hidden_dim(I) + dtype(B) + ts(d)
        header_raw = struct.pack(
            HEADER_FORMAT,
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

    def decode_frame(
        self,
        frame_bytes: bytes,
        max_age_seconds: Optional[float] = None,
    ) -> Tuple[ActivationFrame, List[float]]:
        """
        Decodes and cryptographically verifies an activation frame.
        Enforces HMAC authenticity, header bounds, timestamp freshness, dtype validity,
        and finite numerical values.
        Returns: (ActivationFrame, unpacked_float_activations)
        """
        if len(frame_bytes) < MIN_FRAME_SIZE:
            raise ValueError("Frame size too small")
        if len(frame_bytes) > MAX_FRAME_BYTES:
            raise ValueError(f"Frame size {len(frame_bytes)} exceeds maximum {MAX_FRAME_BYTES}")

        header_raw = frame_bytes[:HEADER_RAW_SIZE]
        sig_bytes = frame_bytes[HEADER_RAW_SIZE : HEADER_RAW_SIZE + HMAC_HEX_SIZE]
        payload_bytes = frame_bytes[HEADER_RAW_SIZE + HMAC_HEX_SIZE :]

        (
            magic,
            sess_len,
            sess_padded,
            stage_index,
            token_index,
            hidden_dim,
            dtype_code,
            ts,
        ) = struct.unpack(HEADER_FORMAT, header_raw)

        if magic != MAGIC_BYTES:
            raise ValueError("Invalid magic bytes in activation frame")

        try:
            received_sig = sig_bytes.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("Invalid non-ASCII HMAC signature in activation frame") from exc

        # Verify cryptographic HMAC signature before trusting any header fields
        expected_sig = self.compute_hmac(header_raw, payload_bytes)
        if not hmac.compare_digest(received_sig, expected_sig):
            raise ValueError("Cryptographic verification failed: HMAC signature mismatch")

        if sess_len < 1 or sess_len > 64:
            raise ValueError(f"Invalid session_id length in frame header: {sess_len}")
        if sess_padded[sess_len:] != b"\x00" * (64 - sess_len):
            raise ValueError("Non-zero padding bytes in session_id header field")
        raw_sess = sess_padded[:sess_len]
        if any(b < 0x20 or b > 0x7E for b in raw_sess):
            raise ValueError("Non-printable ASCII bytes in session_id")
        session_id = raw_sess.decode("ascii")

        if dtype_code == 1:
            tensor_dtype = "float16"
            format_char = "e"
            elem_bytes = 2
        elif dtype_code == 2:
            tensor_dtype = "float32"
            format_char = "f"
            elem_bytes = 4
        else:
            raise ValueError(f"Unsupported dtype_code in activation frame: {dtype_code}")

        if hidden_dim < 1 or hidden_dim > 16384:
            raise ValueError(f"Invalid hidden_dim in activation frame: {hidden_dim}")

        if not math.isfinite(ts) or ts <= 0.0:
            raise ValueError(f"Invalid timestamp in activation frame: {ts}")

        effective_max_age = self.max_age_seconds if max_age_seconds is None else float(max_age_seconds)
        age = time.time() - ts
        if age > effective_max_age:
            raise ValueError(
                f"Stale or expired activation frame timestamp: age={age:.2f}s > max_age={effective_max_age:.2f}s"
            )
        if age < -self.max_future_skew_seconds:
            raise ValueError(
                f"Future activation frame timestamp rejected: skew={-age:.2f}s > max_skew={self.max_future_skew_seconds:.2f}s"
            )

        expected_payload_len = hidden_dim * elem_bytes
        if len(payload_bytes) != expected_payload_len:
            raise ValueError(
                f"Mismatched payload size: expected {expected_payload_len}, got {len(payload_bytes)}"
            )

        activations = list(struct.unpack(f">{hidden_dim}{format_char}", payload_bytes))
        for v in activations:
            if not math.isfinite(v):
                raise ValueError("Decoded activation payload contains non-finite (NaN/Inf) value")

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
