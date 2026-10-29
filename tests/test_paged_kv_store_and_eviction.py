import os
import shutil
import tempfile
import pytest
from app.services.paged_kv import PagedKVManager, PagedKVStore


@pytest.fixture
def temp_kv_dir():
    d = tempfile.mkdtemp(prefix="test_kv_store_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_paged_kv_store_persistence_and_eviction(temp_kv_dir):
    store = PagedKVStore(storage_dir=temp_kv_dir, auth_key=b"k" * 32, compress=True)

    block_hash = "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
    tensor_data = b"TENSOR_PAYLOAD_SIMULATION_RAW_BYTES_FOR_KV_CACHE_PAGING_VERIFICATION"

    # Write to RAM and Disk
    byte_size = store.write_page(block_hash, tensor_data, persist_to_disk=True)
    assert byte_size == len(tensor_data)
    assert store.is_in_ram(block_hash)
    assert store.is_on_disk(block_hash)

    # Evict from RAM
    evicted = store.evict_from_ram(block_hash)
    assert evicted
    assert not store.is_in_ram(block_hash)
    assert store.is_on_disk(block_hash)

    # Read back from Disk -> restores to RAM
    read_data = store.read_page(block_hash)
    assert read_data == tensor_data
    assert store.is_in_ram(block_hash)

    # Delete page completely
    assert store.delete_page(block_hash)
    assert not store.is_in_ram(block_hash)
    assert not store.is_on_disk(block_hash)


def test_paged_kv_manager_zero_prefill_reuse(temp_kv_dir):
    manager = PagedKVManager(
        storage_dir=temp_kv_dir,
        auth_key=b"k" * 32,
        max_ram_blocks=8,
        block_size=16,
        compress=True,
    )

    system_prompt = [10, 20, 30, 40] * 4  # 16 tokens (Block 0)
    schema_spec = [50, 60, 70, 80] * 4    # 16 tokens (Block 1)
    prompt1_suffix = [100, 101, 102, 103] * 4  # 16 tokens (Block 2)

    # First request: completely cold
    res1 = manager.ingest_prompt(
        sequence_id="seq_1",
        tokens=system_prompt + schema_spec + prompt1_suffix,
        model_name="qwen2.5:1.5b",
    )
    assert res1["total_tokens"] == 48
    assert res1["matched_tokens"] == 0
    assert res1["novel_tokens"] == 48
    assert res1["prefill_saved_percent"] == 0.0
    assert res1["reused_blocks_count"] == 0
    assert res1["new_blocks_count"] == 3

    # Second request: shares system_prompt + schema_spec (32 tokens) with novel user query
    prompt2_suffix = [200, 201, 202, 203] * 4  # 16 tokens (Block 2 - alternate)
    res2 = manager.ingest_prompt(
        sequence_id="seq_2",
        tokens=system_prompt + schema_spec + prompt2_suffix,
        model_name="qwen2.5:1.5b",
    )
    assert res2["total_tokens"] == 48
    assert res2["matched_tokens"] == 32
    assert res2["novel_tokens"] == 16
    assert res2["prefill_saved_percent"] == 66.67
    assert res2["reused_blocks_count"] == 2
    assert res2["new_blocks_count"] == 1

    # Check metrics
    metrics = manager.get_metrics()
    assert metrics["total_queries"] == 2
    assert metrics["total_matched_tokens"] == 32
    assert metrics["total_evaluated_tokens"] == 96
    assert metrics["prefix_hit_rate_percent"] == 33.33


def test_paged_kv_manager_lru_ram_eviction(temp_kv_dir):
    # Set strict RAM quota: maximum 3 blocks in RAM
    manager = PagedKVManager(
        storage_dir=temp_kv_dir,
        auth_key=b"k" * 32,
        max_ram_blocks=3,
        block_size=16,
        compress=True,
    )

    # Ingest Sequence 1: 3 blocks (48 tokens)
    seq1_tokens = [i for i in range(48)]
    res1 = manager.ingest_prompt("seq_1", seq1_tokens, model_name="qwen2.5:1.5b")
    assert res1["total_sequence_blocks"] == 3
    assert manager.store.get_ram_page_count() == 3

    # Ingest Sequence 2: 2 novel blocks (32 tokens) -> Total 5 blocks
    seq2_tokens = [i + 1000 for i in range(32)]
    res2 = manager.ingest_prompt("seq_2", seq2_tokens, model_name="qwen2.5:1.5b")

    # RAM count must not exceed max_ram_blocks (3)
    assert manager.store.get_ram_page_count() <= 3
    assert manager.total_evictions >= 2

    # Verify that seq_1 blocks are still durable on disk even though evicted from RAM
    seq1_hashes = manager.table.get_block_hashes("seq_1")
    for h in seq1_hashes:
        assert manager.store.is_on_disk(h)

    # Restoring Sequence 1 reloads all blocks back to RAM
    restored = manager.restore_sequence_to_ram("seq_1")
    assert restored >= 1
    for h in seq1_hashes:
        assert manager.store.is_in_ram(h)
