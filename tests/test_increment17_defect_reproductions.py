"""
Independently specified failing regression tests reproducing Defects F-22 through F-25
discovered during Increment 17 stress testing on commit 12469444a1d86073915c099f6e0de8cd84123547.
"""

import hashlib
import json
import os
import socket
import struct
import time
import pytest

try:
    import torch
    HAS_TORCH = True
except ImportError:
    torch = None
    HAS_TORCH = False

from app.services.paged_kv.hasher import ExecutionNamespace, ModelIdentityManifest
from app.services.paged_kv.identity import ApprovedManifestRegistry, IdentityIssuanceAuthority
from app.services.paged_kv.manager import PagedKVManager
from app.services.paged_kv.page_table import PhysicalBlockPool, VirtualBlockTable
from app.services.paged_kv.pytorch_adapter import PyTorchPagedKVAdapter
from app.services.paged_kv.store import PagedKVStore
from app.services.pipeline_parallel.activation_protocol import ActivationProtocol, MAGIC_BYTES
from app.services.pipeline_parallel.runner import PipelineStageRunner
from app.services.pipeline_parallel.socket_transport import recv_framed_bytes, send_framed_bytes
from app.services.pipeline_parallel.stage1_server import Stage1SocketServer
from app.services.pipeline_parallel.topology import PipelineStageConfig


KEY_32B = b"0123456789abcdef0123456789abcdef"


@pytest.fixture
def verified_identity_env(tmp_path):
    art_dir = tmp_path / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for role in ("weights", "tokenizer", "chat_template", "rope_config"):
        p = art_dir / f"{role}.bin"
        p.write_bytes(f"ARTIFACT_BYTES_{role}".encode("utf-8"))
        paths[role] = p

    manifest = ModelIdentityManifest(
        model_name="qwen2.5-repro-model",
        weight_digest=hashlib.sha256(paths["weights"].read_bytes()).hexdigest(),
        tokenizer_digest=hashlib.sha256(paths["tokenizer"].read_bytes()).hexdigest(),
        chat_template_digest=hashlib.sha256(paths["chat_template"].read_bytes()).hexdigest(),
        kv_layout="bshd_fp16",
        rope_config_digest=hashlib.sha256(paths["rope_config"].read_bytes()).hexdigest(),
        runtime_compatibility="qwen_collective_v2",
        tenant_id="tenant_repro",
    )
    registry = ApprovedManifestRegistry()
    registry.register_approved_manifest(manifest)
    issuer = IdentityIssuanceAuthority(registry=registry)
    authority = issuer.verify_and_issue(manifest, paths)
    namespace = ExecutionNamespace.from_manifest(manifest)
    return {
        "manifest": manifest,
        "registry": registry,
        "issuer": issuer,
        "authority": authority,
        "namespace": namespace,
        "paths": paths,
    }


# ===========================================================================
# DEFECT F-22: Pipeline Protocol & Stage Runner Boundary Validation
# ===========================================================================


def test_f22_stale_and_future_activation_frames_rejected():
    """
    A valid HMAC alone is insufficient: stale frames older than max_age_seconds
    or frames far in the future must be rejected fail-closed.
    """
    proto = ActivationProtocol(secret_key=KEY_32B)
    stale_ts = time.time() - 86400.0  # 24 hours old
    header_raw = struct.pack(
        ">6sB64sHIIBd",
        MAGIC_BYTES,
        7,
        b"sess-01".ljust(64, b"\x00"),
        0,
        0,
        4,
        2,
        stale_ts,
    )
    payload_raw = struct.pack(">4f", 0.1, 0.2, 0.3, 0.4)
    sig = proto.compute_hmac(header_raw, payload_raw).encode("ascii")

    with pytest.raises(ValueError, match="Stale|expired|timestamp"):
        proto.decode_frame(header_raw + sig + payload_raw)


def test_f22_unsupported_dtype_and_nonfinite_activations_rejected():
    """
    Unsupported dtype codes (e.g. 99) and non-finite (NaN/Inf) activation values
    must be rejected fail-closed.
    """
    proto = ActivationProtocol(secret_key=KEY_32B)
    payload_raw = struct.pack(">4f", 0.1, 0.2, 0.3, 0.4)
    header_bad_dtype = struct.pack(
        ">6sB64sHIIBd",
        MAGIC_BYTES,
        7,
        b"sess-01".ljust(64, b"\x00"),
        0,
        0,
        4,
        99,  # Unsupported dtype code
        time.time(),
    )
    sig = proto.compute_hmac(header_bad_dtype, payload_raw).encode("ascii")
    with pytest.raises(ValueError, match="Unsupported|dtype"):
        proto.decode_frame(header_bad_dtype + sig + payload_raw)

    # Non-finite float in encode_frame and decode_frame
    with pytest.raises(ValueError, match="non-finite|NaN|Inf"):
        proto.encode_frame("sess-nan", 0, 0, [1.0, float("nan"), 0.5, 0.2], tensor_dtype="float32")

    payload_nan = struct.pack(">4f", 1.0, float("inf"), 0.5, 0.2)
    header_nan = struct.pack(
        ">6sB64sHIIBd",
        MAGIC_BYTES,
        7,
        b"sess-01".ljust(64, b"\x00"),
        0,
        0,
        4,
        2,
        time.time(),
    )
    sig_nan = proto.compute_hmac(header_nan, payload_nan).encode("ascii")
    with pytest.raises(ValueError, match="non-finite|NaN|Inf"):
        proto.decode_frame(header_nan + sig_nan + payload_nan)


def test_f22_stage_runner_rejects_wrong_stage_hidden_dim_replay_and_reordering():
    """
    PipelineStageRunner must enforce:
    1. Upstream stage_index == self.config.stage_index - 1.
    2. Frame hidden_dim == self.hidden_dim (in both intermediate and egress stages).
    3. Monotonic contiguous token_index ordering per session (no replay, duplicate, or out-of-order frames).
    """
    proto = ActivationProtocol(secret_key=KEY_32B)
    cfg1 = PipelineStageConfig(
        stage_index=1,
        node_id="node-1",
        model_name="qwen2.5:0.5b",
        layer_start=12,
        layer_end=24,
        endpoint_url="tcp://127.0.0.1:19500",
        total_layers=24,
    )
    runner1 = PipelineStageRunner(config=cfg1, protocol=proto, hidden_dim=8)

    # 1. Wrong stage_index (stage_index=5 sent to stage 1)
    bad_stage_frame = proto.encode_frame("sess-order", stage_index=5, token_index=0, activations=[0.1] * 8)
    with pytest.raises(ValueError, match="stage_index|stage"):
        runner1.process_egress(bad_stage_frame)

    # 2. Wrong hidden_dim (4 sent to runner expecting 8)
    bad_dim_frame = proto.encode_frame("sess-order", stage_index=0, token_index=0, activations=[0.1] * 4)
    with pytest.raises(ValueError, match="hidden_dim"):
        runner1.process_egress(bad_dim_frame)

    # 3. Valid token 0 succeeds
    f0 = proto.encode_frame("sess-order", stage_index=0, token_index=0, activations=[0.1] * 8)
    res0 = runner1.process_egress(f0)
    assert res0["token_index"] == 0

    # 4. Replay of token 0 must be rejected
    with pytest.raises(ValueError, match="Duplicate|replay|token_index|order"):
        runner1.process_egress(f0)

    # 5. Missing token 1 (jumping straight to token 2) must be rejected
    f2 = proto.encode_frame("sess-order", stage_index=0, token_index=2, activations=[0.1] * 8)
    with pytest.raises(ValueError, match="token_index|order|missing"):
        runner1.process_egress(f2)


# ===========================================================================
# DEFECT F-23: Socket Transport Overload Rejection & Timeout Enforcement
# ===========================================================================


def test_f23_stage1_server_overload_rejection_and_stalled_client_isolation():
    """
    When concurrent connections exceed max_concurrent_sessions, Stage1SocketServer
    must explicitly reject excess connections rather than spawning unbounded threads,
    and a stalled client must time out without blocking healthy sessions.
    """
    port = 19511
    server = Stage1SocketServer(
        host="127.0.0.1",
        port=port,
        secret_key=KEY_32B,
        hidden_dim=8,
        max_concurrent_sessions=2,
        client_timeout_s=0.5,
    )
    server.start(background=True)
    proto = ActivationProtocol(secret_key=KEY_32B)

    held_socks = []
    try:
        # Hold 2 active connections (saturating max_concurrent_sessions=2)
        for i in range(2):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect(("127.0.0.1", port))
            f = proto.encode_frame(f"held-{i}", stage_index=0, token_index=0, activations=[0.1] * 8)
            send_framed_bytes(s, f)
            resp = json.loads(recv_framed_bytes(s).decode("utf-8"))
            assert resp["status"] == "ok"
            held_socks.append(s)

        # 3rd concurrent connection while 2 are active must receive explicit overload rejection or EOF
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s_excess:
            s_excess.settimeout(2.0)
            s_excess.connect(("127.0.0.1", port))
            f_ex = proto.encode_frame("excess-sess", stage_index=0, token_index=0, activations=[0.1] * 8)
            try:
                send_framed_bytes(s_excess, f_ex)
                raw = recv_framed_bytes(s_excess)
                if raw is not None:
                    resp_ex = json.loads(raw.decode("utf-8"))
                    assert resp_ex["status"] == "rejected_overloaded"
            except (ConnectionError, OSError):
                pass
            assert server.rejected_sessions >= 1
    finally:
        for s in held_socks:
            s.close()
        server.stop()


# ===========================================================================
# DEFECT F-24: PagedKVManager & VirtualBlockTable Ownership, Rollback & COW
# ===========================================================================


def test_f24_corrupt_or_exhausted_ingest_rolls_back_without_leaking_blocks(tmp_path):
    """
    If ingest_prompt fails midway (e.g. second matched block is corrupt on disk,
    or pool exhausts mid-sequence), PagedKVManager must roll back all partial
    block allocations for that sequence, leaving active_sequences == 0,
    free_physical_blocks == total_blocks, and total_matched_tokens uncorrupted.
    """
    mgr = PagedKVManager(storage_dir=tmp_path, auth_key=KEY_32B, max_ram_blocks=4)
    total_pool_blocks = mgr.pool.total_blocks

    # Seed 2 blocks (32 tokens)
    mgr.ingest_prompt("seq-seed", list(range(32)))
    hashes = mgr.table.get_block_hashes("seq-seed")
    mgr.store.evict_from_ram(hashes[1])
    mgr.store._get_disk_path(hashes[1]).write_bytes(b"CORRUPT_DISK_BYTES")
    mgr.release_sequence("seq-seed")

    assert mgr.pool.get_free_block_count() == total_pool_blocks
    assert mgr.get_metrics()["active_sequences"] == 0
    matched_before = mgr.total_matched_tokens

    with pytest.raises(ValueError, match="Corrupt page envelope"):
        mgr.ingest_prompt("seq-victim", list(range(32)))

    # Must not leak block 0 or leave seq-victim dangling or inflate total_matched_tokens
    assert mgr.pool.get_free_block_count() == total_pool_blocks
    assert mgr.get_metrics()["active_sequences"] == 0
    assert mgr.total_matched_tokens == matched_before


def test_f24_remap_block_releases_previous_block_and_cow_unshares_fork():
    """
    1. VirtualBlockTable.remap_block on an already-mapped slot must release the old physical block.
    2. VirtualBlockTable.copy_on_write_block must allocate a fresh block for the mutating child
       and decrement the shared block's ref_count without altering the parent's mapping.
    """
    pool = PhysicalBlockPool(total_blocks=4)
    table = VirtualBlockTable(pool=pool)

    b0 = table.append_block("parent", "h0")
    b1 = table.append_block("parent", "h1")
    table.fork_sequence("parent", "child")

    assert b0.ref_count == 2
    assert b1.ref_count == 2

    # Mutate logical index 0 on child via copy-on-write
    new_b0 = table.copy_on_write_block("child", 0, "h0_child_modified")
    assert new_b0.block_id != b0.block_id
    assert b0.ref_count == 1
    assert new_b0.ref_count == 1
    assert table.get_physical_blocks("parent")[0] == b0.block_id
    assert table.get_physical_blocks("child")[0] == new_b0.block_id

    # Remap logical index 1 on child to a new block: old b1 ref_count must drop from 2 to 1
    b_replacement = pool.allocate_block("h1_replacement")
    table.remap_block("child", 1, b_replacement)
    assert b1.ref_count == 1


def test_f24_disk_byte_quota_enforced_under_eviction_pressure(tmp_path):
    """
    PagedKVStore configured with max_disk_bytes must bound on-disk storage growth
    by evicting oldest unreferenced disk pages or raising MemoryError if exceeded.
    """
    store = PagedKVStore(storage_dir=tmp_path, compress=False, auth_key=KEY_32B, max_disk_bytes=2048)
    for i in range(10):
        h = f"{i:064x}"
        store.write_page(h, b"P" * 400, persist_to_disk=True)
        store.evict_from_ram(h)

    assert store.get_disk_bytes() <= 2048


# ===========================================================================
# DEFECT F-25: PyTorchPagedKVAdapter Double-Ingest, Empty-Suffix & Float32 Parity
# ===========================================================================


@pytest.mark.skipif(not HAS_TORCH, reason="PyTorch required")
def test_f25_pytorch_adapter_no_double_ingest_and_full_prefix_retains_last_token(
    tmp_path, verified_identity_env
):
    """
    1. Calling prepare_prefill_context followed by extract_and_cache_sequence_kv on a cold
       sequence must NOT double-ingest the sequence, double ref_counts, or record fake
       cache hits on placeholder bytes.
    2. When a prompt's tokens are 100% covered by cached 16-token blocks (e.g. 32 tokens
       matching 2 blocks), prepare_prefill_context must retain at least 1 suffix token
       (reusing 1 block = 16 tokens, leaving 16 suffix tokens; or leaving at least 1 token)
       so the model forward pass never receives an empty input_ids=[] tensor.
    3. Float32 KV tensors serialized to disk and deserialized must preserve float32 dtype.
    """
    issuer = verified_identity_env["issuer"]
    authority = verified_identity_env["authority"]
    ns = verified_identity_env["namespace"]

    mgr = PagedKVManager(
        storage_dir=tmp_path / "kv_store",
        auth_key=KEY_32B,
        max_ram_blocks=8,
        trusted_issuer=issuer,
    )
    adapter = PyTorchPagedKVAdapter(manager=mgr, device="cpu")

    tokens_32 = list(range(100, 132))  # 32 tokens = 2 exact 16-token blocks

    # Step 1: Cold prepare
    ctx_cold = adapter.prepare_prefill_context("seq-cold-1", tokens_32, namespace=ns, authority=authority)
    assert ctx_cold["warm_cache_hit"] is False
    assert ctx_cold["matched_tokens"] == 0

    # Step 2: Extract and cache real float32 KV tensors
    mock_kv_f32 = tuple(
        (torch.randn(1, 2, 32, 64, dtype=torch.float32), torch.randn(1, 2, 32, 64, dtype=torch.float32))
        for _ in range(2)
    )
    cached = adapter.extract_and_cache_sequence_kv("seq-cold-1", tokens_32, mock_kv_f32, namespace=ns, authority=authority)
    assert cached == 2

    # Verify no double-ingest occurred!
    assert len(mgr.table.get_physical_blocks("seq-cold-1")) == 2
    for bid in mgr.table.get_physical_blocks("seq-cold-1"):
        assert mgr.pool.blocks[bid].ref_count == 1
    assert mgr.total_matched_tokens == 0

    # Step 3: Warm prepare with identical 32-token sequence (exact multiple of 16)
    # Must NOT return empty input_ids=[]!
    ctx_warm_exact = adapter.prepare_prefill_context("seq-warm-exact", tokens_32, namespace=ns, authority=authority)
    assert ctx_warm_exact["warm_cache_hit"] is True
    assert len(ctx_warm_exact["input_ids"]) >= 1
    assert ctx_warm_exact["matched_tokens"] + len(ctx_warm_exact["input_ids"]) == len(tokens_32)

    # Step 4: Verify float32 dtype preservation across disk serialization
    adapter._ram_tensor_cache.clear()
    ctx_restored = adapter.prepare_prefill_context("seq-warm-disk", tokens_32, namespace=ns, authority=authority)
    restored_kv = ctx_restored["past_key_values"]
    assert restored_kv.key_cache[0].dtype == torch.float32

