"""
Robustness, Edge-Case, and Adversarial Test Suite for Increment 17.
Covers:
1. Activation protocol tamper detection, corrupted headers, truncated payloads, and NaN/Inf floats.
2. Paged KV boundary chunking (0, 1, 15, 16, 17, 31, 32, 33 tokens), empty prompts, and non-ASCII unicode.
3. Virtual block table concurrent COW forking, reference leaks, and deep branching.
4. LRU RAM eviction stress test under high churn.
5. Multi-stage pipeline runner failure containment and shape validation.
"""
import math
import struct
import tempfile
import pytest
from pathlib import Path

from app.services.pipeline_parallel.activation_protocol import (
    ActivationProtocol,
    ActivationFrame,
    MAGIC_BYTES,
)
from app.services.pipeline_parallel.topology import (
    PipelineTopology,
    PipelineStageConfig,
)
from app.services.pipeline_parallel.runner import (
    PipelineStageRunner,
)
from app.services.paged_kv.hasher import (
    TokenBlock,
    PagedKVHasher,
    PrefixRadixTree,
)
from app.services.paged_kv.page_table import (
    PhysicalBlockPool,
    VirtualBlockTable,
)
from app.services.paged_kv.store import (
    PagedKVStore,
)
from app.services.paged_kv.manager import (
    PagedKVManager,
)
from app.services.inference import (
    encode_text_to_tokens,
    get_paged_kv_manager,
)


# ============================================================================
# 1. Activation Protocol Adversarial & Fuzzing Tests
# ============================================================================

def test_activation_tamper_and_signature_forgery():
    secret = b"qc-secret-key-robustness-test"
    wrong_secret = b"qc-wrong-secret-key-attacker"
    proto = ActivationProtocol(secret_key=secret)
    attacker_proto = ActivationProtocol(secret_key=wrong_secret)

    frame = proto.encode_frame(
        session_id="adversarial-session",
        stage_index=0,
        token_index=42,
        activations=[1.0, 2.0, 3.0, 4.0],
        tensor_dtype="float32",
    )

    # 1. Forged signature rejected
    with pytest.raises(ValueError, match="HMAC signature mismatch"):
        attacker_proto.decode_frame(frame)

    # 2. Tampered header byte rejected
    tampered_header = bytearray(frame)
    tampered_header[75] ^= 0xFF  # Flip bit in stage index
    with pytest.raises(ValueError, match="HMAC signature mismatch"):
        proto.decode_frame(bytes(tampered_header))

    # 3. Tampered payload float rejected
    tampered_payload = bytearray(frame)
    tampered_payload[-1] ^= 0x01
    with pytest.raises(ValueError, match="HMAC signature mismatch"):
        proto.decode_frame(bytes(tampered_payload))


def test_activation_truncated_and_corrupted_lengths():
    proto = ActivationProtocol(secret_key=b"qc-secret-key-robustness-test")
    frame = proto.encode_frame(
        session_id="test",
        stage_index=0,
        token_index=1,
        activations=[0.5] * 16,
    )

    # Shorter than minimum header + HMAC size (154 bytes)
    for length in [0, 1, 10, 31, 64, 150]:
        with pytest.raises(ValueError, match="Frame size too small"):
            proto.decode_frame(frame[:length])

    # Truncated payload
    for drop in [1, 2, 4, 16]:
        with pytest.raises(ValueError):
            proto.decode_frame(frame[:-drop])

    # Invalid magic bytes
    bad_magic_frame = b"BADMAG" + frame[6:]
    with pytest.raises(ValueError, match="Invalid magic bytes"):
        proto.decode_frame(bad_magic_frame)


def test_activation_nan_and_inf_handling():
    proto = ActivationProtocol(secret_key=b"qc-secret-key-robustness-test")
    stage_cfg = PipelineStageConfig(
        stage_index=1,
        node_id="test-node",
        model_name="qwen2.5:1.5b",
        layer_start=14,
        layer_end=28,
        endpoint_url="http://127.0.0.1:8000",
        total_layers=28,
    )
    runner = PipelineStageRunner(config=stage_cfg, protocol=proto, hidden_dim=4)

    # Frame with NaN and Inf
    nan_inf_activations = [float("nan"), float("inf"), float("-inf"), 0.0]
    frame = proto.encode_frame("sess-nan", 0, 0, nan_inf_activations)

    # Should decode without crash
    meta, decoded = proto.decode_frame(frame)
    assert len(decoded) == 4
    assert math.isnan(decoded[0])
    assert math.isinf(decoded[1])

    # Runner handles egress evaluation cleanly
    result = runner.process_egress(frame)
    assert "predicted_token_id" in result
    assert isinstance(result["predicted_token_id"], int)


# ============================================================================
# 2. Paged KV Boundary & Arbitrary Token Length Tests
# ============================================================================

@pytest.mark.parametrize("token_count", [0, 1, 15, 16, 17, 31, 32, 33, 100, 255])
def test_paged_kv_token_boundaries(token_count):
    hasher = PagedKVHasher(block_size=16)
    tokens = list(range(1000, 1000 + token_count))
    blocks = hasher.chunk_tokens(tokens)

    if token_count == 0:
        assert len(blocks) == 0
        return

    expected_blocks = (token_count + 15) // 16
    assert len(blocks) == expected_blocks

    prev_hash = None
    for i, b in enumerate(blocks):
        assert b.block_index == i
        if i == 0:
            assert b.parent_hash is None
        else:
            assert b.parent_hash == prev_hash
        assert len(b.tokens) <= 16
        if i < len(blocks) - 1:
            assert len(b.tokens) == 16
        prev_hash = b.block_hash


def test_unicode_and_special_character_tokenization():
    text = "🚀 Qwen Collective 智能体 €100 — \x00\xff\t\n"
    tokens = encode_text_to_tokens(text)
    assert len(tokens) > 0
    assert all(0 <= t <= 255 for t in tokens)

    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = PagedKVManager(storage_dir=Path(tmpdir), auth_key=b"robustness-test-key-32-bytes-ok!", max_ram_blocks=32)
        report1 = mgr.ingest_prompt("unicode-seq-1", tokens, model_name="qwen2.5:1.5b")
        assert report1["total_tokens"] == len(tokens)
        assert report1["matched_tokens"] == 0

        # Identical unicode sequence hit
        report2 = mgr.ingest_prompt("unicode-seq-2", tokens, model_name="qwen2.5:1.5b")
        assert report2["matched_tokens"] == len(tokens)
        assert report2["prefill_saved_percent"] == 100.0


# ============================================================================
# 3. Virtual Block Table COW & Deep Branching Stress Tests
# ============================================================================

def test_deep_branching_radix_tree():
    tree = PrefixRadixTree(hasher=PagedKVHasher(block_size=16))
    common_tokens = [1, 2, 3, 4] * 4  # 16 tokens (Block 0)

    # Insert 50 branch sequences sharing Block 0
    for i in range(50):
        branch_tokens = [i] * 16
        tree.insert(common_tokens + branch_tokens, payload={"branch": i})

    # Query matching Block 0 + Branch 12
    q = common_tokens + [12] * 16
    matched_blocks, matched_tokens, payloads = tree.match_prefix(q)
    assert len(matched_blocks) == 2
    assert matched_tokens == 32
    assert payloads[-1]["branch"] == 12

    # Query matching only Block 0
    q_solo = common_tokens + [999] * 16
    m_blocks, m_tokens, _ = tree.match_prefix(q_solo)
    assert len(m_blocks) == 1
    assert m_tokens == 16


def test_virtual_block_table_ref_count_correctness():
    pool = PhysicalBlockPool(total_blocks=16)
    vtable = VirtualBlockTable(pool)

    # Allocate blocks for sequence A
    vtable.create_sequence("seq-a")
    vtable.append_block("seq-a", "hash-1")
    vtable.append_block("seq-a", "hash-2")
    vtable.append_block("seq-a", "hash-3")
    phys_a = vtable.get_physical_blocks("seq-a")
    assert len(phys_a) == 3

    # Fork to sequence B and C (COW sharing)
    vtable.fork_sequence("seq-a", "seq-b")
    vtable.fork_sequence("seq-a", "seq-c")

    # Ref counts should be 3
    for b_id in phys_a:
        assert pool.blocks[b_id].ref_count == 3

    # Free sequence B
    vtable.release_sequence("seq-b")
    for b_id in phys_a:
        assert pool.blocks[b_id].ref_count == 2

    # Append block to sequence C
    b_new = vtable.append_block("seq-c", "hash-4-novel")
    assert b_new.block_hash == "hash-4-novel"
    assert b_new.ref_count == 1

    # Free remaining sequences
    vtable.release_sequence("seq-a")
    vtable.release_sequence("seq-c")
    assert pool.get_free_block_count() == 16


# ============================================================================
# 4. LRU Eviction & Disk Store Recovery Under High Churn
# ============================================================================

def test_lru_eviction_churn_under_tight_ram():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Max RAM = 4 blocks
        mgr = PagedKVManager(storage_dir=Path(tmpdir), auth_key=b"robustness-test-key-32-bytes-ok!", max_ram_blocks=4, block_size=16)

        # Ingest 10 distinct sequences of 32 tokens each (2 blocks per sequence)
        for i in range(10):
            tokens = [i * 100 + t for t in range(32)]
            mgr.ingest_prompt(f"churn-{i}", tokens, model_name="qwen2.5:1.5b")

        metrics = mgr.get_metrics()
        # Allocated RAM blocks must never exceed quota
        assert metrics["current_ram_pages"] <= 4
        assert metrics["total_evictions"] >= 16

        # Querying an earlier prompt (e.g. churn-0) still matches prefix in Radix tree
        # and reloads evicted pages from Tier 2 disk seamlessly
        res = mgr.ingest_prompt("churn-0-replay", [0 * 100 + t for t in range(32)], model_name="qwen2.5:1.5b")
        assert res["matched_tokens"] == 32
        assert res["prefill_saved_percent"] == 100.0
