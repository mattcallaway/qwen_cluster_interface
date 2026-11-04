"""
Live Multi-Node Pipeline Parallelism Activation Passing Benchmark Harness.
Standard-library only: operates with zero dependencies across heterogeneous nodes.
"""
import sys
import os
import time
import math
import struct
import hmac
import hashlib
import json
import socket
import threading
from typing import List, Dict, Any, Tuple

MAGIC = b"QCACT1\x00\x00"

def encode_activation_frame(
    session_id: str,
    stage_index: int,
    token_index: int,
    activations: List[float],
    secret_key: bytes,
    tensor_dtype: str = "float32"
) -> bytes:
    session_hash = hashlib.sha256(session_id.encode("utf-8")).digest()[:8]
    hidden_dim = len(activations)
    if tensor_dtype == "float32":
        dtype_code = 0
        raw_payload = struct.pack(f"<{hidden_dim}f", *activations)
    elif tensor_dtype == "float16":
        dtype_code = 1
        raw_payload = struct.pack(f"<{hidden_dim}e", *activations)
    else:
        raise ValueError(f"Unsupported dtype: {tensor_dtype}")

    reserved = b"\x00" * 7
    header_pre_hmac = struct.pack(
        "<8s8sHIHBs",
        MAGIC,
        session_hash,
        stage_index,
        token_index,
        hidden_dim,
        dtype_code,
        reserved[:1]
    ) + reserved[1:]

    signature = hmac.new(secret_key, header_pre_hmac + raw_payload, hashlib.sha256).digest()
    return header_pre_hmac + signature + raw_payload

def decode_activation_frame(frame_bytes: bytes, secret_key: bytes) -> Tuple[Dict[str, Any], List[float]]:
    if len(frame_bytes) < 64:
        raise ValueError(f"Frame too short: {len(frame_bytes)} bytes")
    
    header_pre_hmac = frame_bytes[:32]
    magic, s_hash, stage_idx, token_idx, hidden_dim, dtype_code = struct.unpack(
        "<8s8sHIHB", header_pre_hmac[:25]
    )
    if magic != MAGIC:
        raise ValueError("Invalid magic bytes")

    received_sig = frame_bytes[32:64]
    raw_payload = frame_bytes[64:]
    expected_sig = hmac.new(secret_key, header_pre_hmac + raw_payload, hashlib.sha256).digest()
    if not hmac.compare_digest(received_sig, expected_sig):
        raise ValueError("HMAC signature verification failed")

    if dtype_code == 0:
        expected_len = hidden_dim * 4
        fmt = f"<{hidden_dim}f"
    elif dtype_code == 1:
        expected_len = hidden_dim * 2
        fmt = f"<{hidden_dim}e"
    else:
        raise ValueError("Unknown dtype")

    if len(raw_payload) != expected_len:
        raise ValueError(f"Payload length mismatch: {len(raw_payload)} != {expected_len}")

    activations = list(struct.unpack(fmt, raw_payload))
    meta = {
        "stage_index": stage_idx,
        "token_index": token_idx,
        "hidden_dim": hidden_dim,
        "dtype": "float32" if dtype_code == 0 else "float16",
        "payload_bytes": len(raw_payload)
    }
    return meta, activations

def simulate_stage_compute(activations: List[float], start_layer: int, end_layer: int) -> List[float]:
    out = list(activations)
    layer_scale = 1.0 / math.sqrt(len(activations))
    for l in range(start_layer, end_layer):
        w = math.sin(l + 1.0) * layer_scale
        out = [(v + math.tanh(v * 1.02 + w)) for v in out]
    return out

class ActivationServer:
    def __init__(self, host: str, port: int, secret_key: bytes, start_layer: int = 14, end_layer: int = 28):
        self.host = host
        self.port = port
        self.secret_key = secret_key
        self.start_layer = start_layer
        self.end_layer = end_layer
        self.sock = None
        self.running = False

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.listen(5)
        self.running = True

    def serve_forever(self, max_tokens: int = 50):
        tokens_processed = 0
        while self.running and tokens_processed < max_tokens:
            client, addr = self.sock.accept()
            try:
                while tokens_processed < max_tokens:
                    # Read 4-byte length prefix
                    raw_len = client.recv(4)
                    if not raw_len or len(raw_len) < 4:
                        break
                    frame_len = struct.unpack("!I", raw_len)[0]
                    # Read frame
                    buf = bytearray()
                    while len(buf) < frame_len:
                        chunk = client.recv(min(frame_len - len(buf), 65536))
                        if not chunk:
                            break
                        buf.extend(chunk)
                    
                    if len(buf) != frame_len:
                        break
                    
                    t0 = time.perf_counter()
                    meta, activations = decode_activation_frame(bytes(buf), self.secret_key)
                    t_decode = (time.perf_counter() - t0) * 1000.0

                    t1 = time.perf_counter()
                    stage1_out = simulate_stage_compute(activations, self.start_layer, self.end_layer)
                    predicted_token = max(range(min(100, len(stage1_out))), key=lambda i: stage1_out[i])
                    t_compute = (time.perf_counter() - t1) * 1000.0

                    resp = json.dumps({
                        "token_index": meta["token_index"],
                        "predicted_token": predicted_token,
                        "t_decode_ms": round(t_decode, 3),
                        "t_compute_ms": round(t_compute, 3),
                    }).encode("utf-8")

                    client.sendall(struct.pack("!I", len(resp)) + resp)
                    tokens_processed += 1
            finally:
                client.close()
        self.stop()

    def stop(self):
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "server":
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 9190
        tokens = int(sys.argv[3]) if len(sys.argv) > 3 else 30
        secret = sys.argv[4].encode("utf-8") if len(sys.argv) > 4 else b"qc-test-secret-key-inc17"
        srv = ActivationServer("0.0.0.0", port, secret)
        srv.start()
        print(f"PIPELINE_SERVER_READY:{port}", flush=True)
        srv.serve_forever(max_tokens=tokens)
        print("PIPELINE_SERVER_DONE", flush=True)
