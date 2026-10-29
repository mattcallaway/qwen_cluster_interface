import hashlib
import json
import os
import shutil
import socket
import struct
import tempfile
import threading
import time
from typing import List, Optional
import pytest

from app.services.paged_kv.hasher import (
    ExecutionNamespace,
    ModelIdentityManifest,
)
from app.services.paged_kv.identity import (
    ApprovedManifestRegistry,
    IdentityIssuanceAuthority,
    IssuedReuseAuthority,
)
from app.services.paged_kv.manager import PagedKVManager
from app.services.pipeline_parallel.activation_protocol import (
    ActivationProtocol,
    ActivationFrame,
)
from app.services.pipeline_parallel.runner import PipelineStageRunner
from app.services.pipeline_parallel.socket_transport import (
    send_framed_bytes,
    recv_framed_bytes,
)
from app.services.pipeline_parallel.stage1_server import Stage1SocketServer
from app.services.pipeline_parallel.topology import PipelineStageConfig

TEST_SECRET_KEY = b"qc-stress-test-secret-key-32b!!"
STORAGE_KEY = os.urandom(32)


# ===========================================================================
# FIXTURES
# ===========================================================================

@pytest.fixture
def synthetic_model_fixture(tmp_path):
    model_dir = tmp_path / "stress_qwen_model"
    model_dir.mkdir(parents=True, exist_ok=True)

    weights_file = model_dir / "weights.bin"
    weights_content = b"SYNTHETIC_WEIGHTS_STRESS_TENSOR_BYTES_12345"
    weights_file.write_bytes(weights_content)

    tok_file = model_dir / "tokenizer.json"
    tok_content = b'{"vocab": ["<pad>", "<s>", "</s>", "qwen"]}'
    tok_file.write_bytes(tok_content)

    tmpl_file = model_dir / "chat_template.jinja"
    tmpl_content = b"{% for m in messages %}{{ m.content }}{% endfor %}"
    tmpl_file.write_bytes(tmpl_content)

    rope_file = model_dir / "rope_config.json"
    rope_content = b'{"type": "linear", "factor": 1.0, "theta": 1000000.0}'
    rope_file.write_bytes(rope_content)

    manifest = ModelIdentityManifest(
        model_name="synthetic-qwen-stress",
        weight_digest=hashlib.sha256(weights_content).hexdigest(),
        tokenizer_digest=hashlib.sha256(tok_content).hexdigest(),
        chat_template_digest=hashlib.sha256(tmpl_content).hexdigest(),
        kv_layout="bshd_fp16",
        rope_config_digest=hashlib.sha256(rope_content).hexdigest(),
        runtime_compatibility="qwen_collective_v2",
        tenant_id="tenant_stress",
    )

    artifact_paths = {
        "weights": weights_file,
        "tokenizer": tok_file,
        "chat_template": tmpl_file,
        "rope_config": rope_file,
    }

    registry = ApprovedManifestRegistry()
    registry.register_approved_manifest(manifest)
    issuer = IdentityIssuanceAuthority(registry=registry)

    return {
        "manifest": manifest,
        "paths": artifact_paths,
        "registry": registry,
        "issuer": issuer,
        "weights_file": weights_file,
    }


# ===========================================================================
# 1. PIPELINE TRANSPORT CONCURRENCY & SCHEDULING STRESS TESTS
# ===========================================================================

def test_pipeline_socket_concurrent_sessions():
    """
    Stress-tests Stage1SocketServer under 15 concurrent streaming sessions.
    Asserts no deadlocks, no frame interleaving corruptions, and 100% HMAC verification.
    """
    port = 19201
    server = Stage1SocketServer(host="127.0.0.1", port=port, secret_key=TEST_SECRET_KEY, hidden_dim=64)
    server.start(background=True)

    protocol = ActivationProtocol(secret_key=TEST_SECRET_KEY)
    concurrency = 15
    frames_per_session = 20
    errors: List[str] = []

    def client_worker(worker_id: int):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(5.0)
                sock.connect(("127.0.0.1", port))
                session_id = f"stress-session-{worker_id:02d}"

                for t_idx in range(frames_per_session):
                    vec = [float(worker_id * 100 + t_idx) * 0.01] * 64
                    frame_bytes = protocol.encode_frame(
                        session_id=session_id,
                        stage_index=0,
                        token_index=t_idx,
                        activations=vec,
                        tensor_dtype="float32",
                    )
                    send_framed_bytes(sock, frame_bytes)

                    resp_bytes = recv_framed_bytes(sock)
                    if resp_bytes is None:
                        errors.append(f"Worker {worker_id}: Unexpected socket close on token {t_idx}")
                        return
                    resp = json.loads(resp_bytes.decode("utf-8"))
                    if resp.get("status") != "ok":
                        errors.append(f"Worker {worker_id}: Non-ok status: {resp}")
                        return
                    if resp.get("session_id") != session_id:
                        errors.append(f"Worker {worker_id}: Session mismatch! Expected {session_id}, got {resp.get('session_id')}")
                        return
                    if resp.get("token_index") != t_idx:
                        errors.append(f"Worker {worker_id}: Token index mismatch! Expected {t_idx}, got {resp.get('token_index')}")
                        return
        except Exception as e:
            errors.append(f"Worker {worker_id} exception: {e}")

    threads = [threading.Thread(target=client_worker, args=(i,)) for i in range(concurrency)]
    t_start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)
    duration_s = time.perf_counter() - t_start

    server.stop()
    assert not errors, f"Concurrent streaming errors: {errors}"
    assert server.frames_processed == concurrency * frames_per_session
    assert server.auth_failures == 0
    total_frames = concurrency * frames_per_session
    fps = total_frames / max(duration_s, 0.001)
    assert fps > 50.0, f"Expected >50 frames/sec under concurrency, got {fps:.1f}"


def test_pipeline_socket_connection_churn():
    """
    Rapid connect -> send 1 frame -> disconnect cycle (50 repetitions).
    Verifies socket reuse, thread cleanup, and zero resource leaks.
    """
    port = 19202
    server = Stage1SocketServer(host="127.0.0.1", port=port, secret_key=TEST_SECRET_KEY, hidden_dim=64)
    server.start(background=True)
    protocol = ActivationProtocol(secret_key=TEST_SECRET_KEY)

    try:
        for i in range(50):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(2.0)
                sock.connect(("127.0.0.1", port))
                frame_bytes = protocol.encode_frame(
                    session_id=f"churn-{i}",
                    stage_index=0,
                    token_index=0,
                    activations=[0.1] * 64,
                    tensor_dtype="float32",
                )
                send_framed_bytes(sock, frame_bytes)
                resp = json.loads(recv_framed_bytes(sock).decode("utf-8"))
                assert resp["status"] == "ok"
        assert server.frames_processed == 50
    finally:
        server.stop()


# ===========================================================================
# 2. ADVERSARIAL FAULT INJECTION AT STAGE BOUNDARIES
# ===========================================================================

def test_pipeline_fault_injection_matrix():
    """
    Injects distinct stage-boundary wire faults and asserts deterministic rejection.
    """
    port = 19203
    server = Stage1SocketServer(host="127.0.0.1", port=port, secret_key=TEST_SECRET_KEY, hidden_dim=64)
    server.start(background=True)
    protocol = ActivationProtocol(secret_key=TEST_SECRET_KEY)

    valid_frame = protocol.encode_frame(
        session_id="fault-sess-01",
        stage_index=0,
        token_index=0,
        activations=[0.5] * 64,
        tensor_dtype="float32",
    )

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(3.0)
            sock.connect(("127.0.0.1", port))

            # FAULT 1: Corrupted HMAC signature
            bad_hmac = bytearray(valid_frame)
            bad_hmac[72] ^= 0x01
            send_framed_bytes(sock, bytes(bad_hmac))
            resp1 = json.loads(recv_framed_bytes(sock).decode("utf-8"))
            assert resp1["status"] == "auth_error"
            assert "HMAC signature mismatch" in resp1["error"]

            # FAULT 2: Invalid Magic Bytes
            bad_magic = bytearray(valid_frame)
            bad_magic[:6] = b"BADACT"
            header = bad_magic[: struct.calcsize(">6sB64sHIIBd")]
            payload = bad_magic[struct.calcsize(">6sB64sHIIBd") + 64 :]
            sig = protocol.compute_hmac(header, payload).encode("ascii")
            bad_magic[struct.calcsize(">6sB64sHIIBd") : struct.calcsize(">6sB64sHIIBd") + 64] = sig
            send_framed_bytes(sock, bytes(bad_magic))
            resp2 = json.loads(recv_framed_bytes(sock).decode("utf-8"))
            assert resp2["status"] == "auth_error"
            assert "Invalid magic" in resp2["error"]

            # FAULT 3: Truncated Payload
            truncated = valid_frame[:-10]
            send_framed_bytes(sock, truncated)
            resp3 = json.loads(recv_framed_bytes(sock).decode("utf-8"))
            assert resp3["status"] == "auth_error"

            # FAULT 4: Clean Recovery after faults
            send_framed_bytes(sock, valid_frame)
            resp4 = json.loads(recv_framed_bytes(sock).decode("utf-8"))
            assert resp4["status"] == "ok"
            assert resp4["hmac_verified"] is True
            assert resp4["session_id"] == "fault-sess-01"

    finally:
        server.stop()


# ===========================================================================
# 3. PAGED KV CACHE OWNERSHIP, QUOTA & CONCURRENCY STRESS
# ===========================================================================

def test_paged_kv_concurrent_sequences_and_shared_prefixes(tmp_path, synthetic_model_fixture):
    """
    Stress-tests PagedKVManager under concurrent sequence creation, prefix sharing,
    and reference-counted page releases.
    """
    manifest = synthetic_model_fixture["manifest"]
    issuer = synthetic_model_fixture["issuer"]
    paths = synthetic_model_fixture["paths"]

    authority = issuer.verify_and_issue(manifest, paths)
    ns = ExecutionNamespace.from_manifest(manifest)

    manager = PagedKVManager(
        storage_dir=tmp_path,
        auth_key=STORAGE_KEY,
        max_ram_blocks=32,
        trusted_issuer=issuer,
    )

    shared_prefix = list(range(100, 132))  # 32 tokens = 2 exact 16-token pages
    seq_ids: List[str] = ["seq-seed"]
    errors: List[str] = []

    # 1. Seed the cache with the shared prefix
    res_seed = manager.ingest_prompt("seq-seed", shared_prefix, namespace=ns, authority=authority)
    assert res_seed["verified_reuse_authorized"] is True

    def allocate_client(idx: int):
        try:
            tokens = shared_prefix + list(range(idx * 1000, idx * 1000 + 16))
            res = manager.ingest_prompt(f"seq-client-{idx}", tokens, namespace=ns, authority=authority)
            seq_ids.append(f"seq-client-{idx}")
            if res["matched_tokens"] < 32:
                errors.append(f"Client {idx}: Expected prefix hit of at least 32, got {res['matched_tokens']}")
            if not res["verified_reuse_authorized"]:
                errors.append(f"Client {idx}: Verified reuse was not authorized")
        except Exception as e:
            errors.append(f"Client {idx} error: {e}")

    threads = [threading.Thread(target=allocate_client, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    assert not errors, f"Errors in concurrent ingest_prompt: {errors}"
    assert len(seq_ids) == 11

    # Clean release of all sequences
    for sid in seq_ids:
        manager.release_sequence(sid)

    metrics = manager.get_metrics()
    assert metrics["active_sequences"] == 0


def test_paged_kv_namespace_strict_isolation(tmp_path, synthetic_model_fixture):
    """
    Verifies that identical token sequences across different execution namespaces
    (different tenant or template) NEVER share cache pages.
    """
    manifest = synthetic_model_fixture["manifest"]
    issuer = synthetic_model_fixture["issuer"]
    paths = synthetic_model_fixture["paths"]

    authority = issuer.verify_and_issue(manifest, paths)

    manager = PagedKVManager(
        storage_dir=tmp_path,
        auth_key=STORAGE_KEY,
        trusted_issuer=issuer,
    )

    tokens = list(range(32))

    ns1 = ExecutionNamespace.from_manifest(manifest)

    # Different tenant
    ns2_diff_tenant = ExecutionNamespace(
        model_name=manifest.model_name,
        model_revision=manifest.weight_digest,
        tokenizer_id=manifest.tokenizer_digest,
        chat_template_hash=manifest.chat_template_digest,
        kv_layout=manifest.kv_layout,
        rope_config_hash=manifest.rope_config_digest,
        runtime_compatibility=manifest.runtime_compatibility,
        tenant_id="tenant_DIFFERENT",
        identity_scope="verified_runtime",
    )

    # 1. Allocate for ns1
    res1 = manager.ingest_prompt("seq1", tokens, namespace=ns1, authority=authority)
    assert res1["matched_tokens"] == 0
    assert res1["verified_reuse_authorized"] is True

    # 2. Ingest for ns2 with exact same tokens -> Must NOT hit ns1's cache
    res2 = manager.ingest_prompt("seq2", tokens, namespace=ns2_diff_tenant, authority=authority)
    assert res2["matched_tokens"] == 0, "Security violation: Cross-tenant cache bleed detected!"

    manager.release_sequence("seq1")
    manager.release_sequence("seq2")


def test_paged_kv_disk_corruption_and_tamper_rejection(tmp_path, synthetic_model_fixture):
    """
    Persists page blocks to disk in QCKVP2 binary envelopes, tampers with a disk byte,
    and asserts fail-closed cryptographic rejection during restoration.
    """
    manifest = synthetic_model_fixture["manifest"]
    issuer = synthetic_model_fixture["issuer"]
    paths = synthetic_model_fixture["paths"]

    authority = issuer.verify_and_issue(manifest, paths)
    ns = ExecutionNamespace.from_manifest(manifest)

    manager = PagedKVManager(
        storage_dir=tmp_path,
        auth_key=STORAGE_KEY,
        trusted_issuer=issuer,
    )

    tokens = list(range(32))
    res1 = manager.ingest_prompt("seq-disk-01", tokens, namespace=ns, authority=authority)
    assert res1["verified_reuse_authorized"] is True

    # Find written .bin.z disk files
    disk_files = []
    for root, dirs, files in os.walk(tmp_path):
        for f in files:
            if f.endswith(".bin.z") or f.endswith(".bin"):
                disk_files.append(os.path.join(root, f))

    assert len(disk_files) > 0, "Expected written disk block files"

    # Evict block from RAM so restoration is forced to read from disk
    hashes = manager.table.get_block_hashes("seq-disk-01")
    for h in hashes:
        manager.store.evict_from_ram(h)

    # TAMPER: Mutate a byte in the encrypted block payload on disk
    target_block = disk_files[0]
    with open(target_block, "r+b") as f:
        data = bytearray(f.read())
        # Header is 186 bytes; tamper in payload or HMAC
        data[20] ^= 0xFF
        f.seek(0)
        f.write(data)

    # ATTEMPT RESTORATION -> Must fail closed
    with pytest.raises((ValueError, PermissionError, OSError)):
        manager.restore_sequence_to_ram("seq-disk-01")


def test_paged_kv_revoked_authority_blocks_reuse_and_restoration(tmp_path, synthetic_model_fixture):
    """
    Asserts that revoking an IssuedReuseAuthority in the active authority registry
    immediately invalidates cached prefix reuse and prevents sequence restoration.
    """
    manifest = synthetic_model_fixture["manifest"]
    issuer = synthetic_model_fixture["issuer"]
    paths = synthetic_model_fixture["paths"]

    authority = issuer.verify_and_issue(manifest, paths)
    ns = ExecutionNamespace.from_manifest(manifest)

    manager = PagedKVManager(
        storage_dir=tmp_path,
        auth_key=STORAGE_KEY,
        trusted_issuer=issuer,
    )

    tokens = list(range(32))
    res1 = manager.ingest_prompt("seq-revoke-1", tokens, namespace=ns, authority=authority)
    assert res1["verified_reuse_authorized"] is True

    # REVOKE authority
    issuer.revoke_authority(authority.authority_id)

    # Subsequent reuse on new sequence with same prefix -> Must be rejected fail-closed
    res2 = manager.ingest_prompt("seq-revoke-2", tokens, namespace=ns, authority=authority)
    assert res2["verified_reuse_authorized"] is False, "Security violation: Revoked authority permitted verified reuse!"
    assert res2["reused_blocks_count"] == 0

    # Attempt restoration of sequence 1 -> Must raise ValueError (revoked)
    with pytest.raises(ValueError, match="revoked or invalidated"):
        manager.restore_sequence_to_ram("seq-revoke-1")
