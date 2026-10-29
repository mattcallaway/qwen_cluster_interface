import tempfile
from pathlib import Path
import pytest
from app.services.paged_kv.manager import PagedKVManager


def test_reproduce_cross_model_prefix_collision():
    """
    Reproduction test for Material Finding 4 / probe cross_model_prefix:
    Identical tokens ingested for Model A then Model B must NOT yield a prefix hit for Model B.
    Reusable pages must be bound to immutable execution namespaces.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = PagedKVManager(storage_dir=Path(tmpdir), max_ram_blocks=16, block_size=16)

        # 16 identical tokens
        common_tokens = list(range(16))

        # Ingest for Model A
        res_a = mgr.ingest_prompt(
            sequence_id="seq-model-a",
            tokens=common_tokens,
            model_name="qwen2.5:1.5b"
        )
        assert res_a["matched_tokens"] == 0, "First ingestion should have 0 matched tokens"

        # Ingest identical tokens for Model B (different model architecture/namespace)
        # Expected: 0 matched tokens because Model B must not reuse Model A's KV cache.
        # Broken behavior in prototype: matches all 16 tokens across models!
        res_b = mgr.ingest_prompt(
            sequence_id="seq-model-b",
            tokens=common_tokens,
            model_name="llama3.2:1b"
        )
        assert res_b["matched_tokens"] == 0, (
            f"Cross-model cache pollution detected: Model B matched {res_b['matched_tokens']} tokens from Model A"
        )


def test_compatible_namespace_prefix_reuse():
    """
    Identical tokens within the SAME model namespace MUST hit and reuse cached pages.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = PagedKVManager(storage_dir=Path(tmpdir), max_ram_blocks=16, block_size=16)

        common_tokens = list(range(32))

        # First ingestion
        res_1 = mgr.ingest_prompt("seq-1", common_tokens, model_name="qwen2.5:1.5b")
        assert res_1["matched_tokens"] == 0

        # Second ingestion with same model namespace and longer prompt sharing prefix
        full_tokens = common_tokens + [100, 101, 102]
        res_2 = mgr.ingest_prompt("seq-2", full_tokens, model_name="qwen2.5:1.5b")
        assert res_2["matched_tokens"] == 32
        assert res_2["reused_blocks_count"] == 2
