import tempfile
from pathlib import Path
import pytest
from app.services.paged_kv.manager import PagedKVManager


def test_reproduce_restore_bypasses_ram_quota():
    """
    Reproduction test for Material Finding 6:
    restore_sequence_to_ram must re-enforce the RAM byte/block quota.
    Currently, restoring multiple sequences loads pages into RAM without ever evicting,
    exceeding max_ram_blocks.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Max RAM: 2 blocks
        mgr = PagedKVManager(storage_dir=Path(tmpdir), auth_key=b"0" * 32, max_ram_blocks=2, block_size=16)

        # Ingest seq-1 (2 blocks = 32 tokens)
        mgr.ingest_prompt("seq-1", list(range(32)), model_name="qwen2.5:1.5b")
        # Ingest seq-2 (2 blocks = 32 tokens, distinct)
        mgr.ingest_prompt("seq-2", [1000 + i for i in range(32)], model_name="qwen2.5:1.5b")

        # Quota enforced after ingestion: RAM pages <= 2
        assert mgr.store.get_ram_page_count() <= 2

        # Now restore seq-1 to RAM
        mgr.restore_sequence_to_ram("seq-1")

        # Expected: RAM quota must still be respected (RAM pages <= 2, evicting seq-2 blocks)
        # Broken behavior: store loads seq-1 blocks without evicting, resulting in > 2 pages in RAM!
        assert mgr.store.get_ram_page_count() <= 2, (
            f"RAM quota exceeded after restore: {mgr.store.get_ram_page_count()} > 2"
        )


def test_byte_level_accounting_and_metrics():
    """
    Metrics must report actual allocated tensor bytes and memory bounds,
    not just dictionary entry counts.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = PagedKVManager(storage_dir=Path(tmpdir), auth_key=b"0" * 32, max_ram_blocks=4, block_size=16)
        mgr.ingest_prompt("seq-1", list(range(32)), model_name="qwen2.5:1.5b")

        metrics = mgr.get_metrics()
        # Must contain byte-accurate fields
        assert "current_ram_bytes" in metrics, "Missing current_ram_bytes metric"
        assert "max_ram_bytes" in metrics, "Missing max_ram_bytes metric"
        assert metrics["current_ram_bytes"] > 0, "current_ram_bytes should be greater than 0"
