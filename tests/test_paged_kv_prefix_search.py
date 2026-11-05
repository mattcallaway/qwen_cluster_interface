import pytest
from app.services.paged_kv.hasher import PagedKVHasher, PrefixRadixTree, TokenBlock
from app.services.paged_kv.page_table import (
    BlockMapping,
    PhysicalBlockPool,
    VirtualBlockTable,
)


def test_token_block_properties():
    tb = TokenBlock(
        block_index=0,
        tokens=(101, 102, 103),
        parent_hash=None,
        block_hash="abc123def456",
    )
    assert tb.token_count == 3
    d = tb.to_dict()
    assert d["block_index"] == 0
    assert d["tokens"] == [101, 102, 103]
    assert d["parent_hash"] is None
    assert d["block_hash"] == "abc123def456"


def test_hasher_chunking_and_chaining():
    hasher = PagedKVHasher(block_size=16)
    tokens = list(range(40))  # 16 + 16 + 8 tokens -> 3 blocks
    blocks = hasher.chunk_tokens(tokens)

    assert len(blocks) == 3
    assert blocks[0].token_count == 16
    assert blocks[0].parent_hash is None
    assert blocks[0].block_index == 0

    assert blocks[1].token_count == 16
    assert blocks[1].parent_hash == blocks[0].block_hash
    assert blocks[1].block_index == 1

    assert blocks[2].token_count == 8
    assert blocks[2].parent_hash == blocks[1].block_hash
    assert blocks[2].block_index == 2

    # Determinism: identical tokens yield identical hashes
    blocks_repeat = hasher.chunk_tokens(tokens)
    for b1, b2 in zip(blocks, blocks_repeat):
        assert b1.block_hash == b2.block_hash

    # Sensitivity: changing one token in block 0 alters block 0, 1, and 2 hashes
    mutated = list(tokens)
    mutated[0] = 999
    blocks_mut = hasher.chunk_tokens(mutated)
    assert blocks_mut[0].block_hash != blocks[0].block_hash
    assert blocks_mut[1].block_hash != blocks[1].block_hash
    assert blocks_mut[2].block_hash != blocks[2].block_hash


def test_radix_tree_prefix_matching():
    tree = PrefixRadixTree(hasher=PagedKVHasher(block_size=16))

    system_prompt_tokens = [1, 2, 3, 4] * 4  # 16 tokens (Block 0)
    schema_tokens = [5, 6, 7, 8] * 4         # 16 tokens (Block 1)
    task1_tokens = [9, 10, 11, 12] * 4       # 16 tokens (Block 2)
    task2_tokens = [20, 21, 22, 23] * 4     # 16 tokens (Block 2 - alternate)

    # Insert Task 1 sequence
    seq1 = system_prompt_tokens + schema_tokens + task1_tokens
    tree.insert(seq1, payload={"model": "qwen2.5:1.5b", "owner": "task1"})

    # Insert Task 2 sequence (shares Block 0 and Block 1 with Task 1)
    seq2 = system_prompt_tokens + schema_tokens + task2_tokens
    tree.insert(seq2, payload={"model": "qwen2.5:1.5b", "owner": "task2"})

    # Query with a third sequence sharing only the system prompt + schema + novel suffix
    query_seq = system_prompt_tokens + schema_tokens + [99, 100] * 8
    matched_blocks, matched_tokens, payloads = tree.match_prefix(query_seq)

    # Should match exactly 2 blocks (32 tokens)
    assert len(matched_blocks) == 2
    assert matched_tokens == 32
    assert matched_blocks[0].tokens == tuple(system_prompt_tokens)
    assert matched_blocks[1].tokens == tuple(schema_tokens)

    # Query with an entirely unknown sequence
    unknown_seq = [500] * 32
    u_blocks, u_tokens, _ = tree.match_prefix(unknown_seq)
    assert len(u_blocks) == 0
    assert u_tokens == 0


def test_physical_block_pool_allocation_and_reuse():
    pool = PhysicalBlockPool(total_blocks=4)
    assert pool.get_free_block_count() == 4

    # Allocate Block A
    b1 = pool.allocate_block(block_hash="hash_a")
    assert b1.block_id == 0
    assert b1.ref_count == 1
    assert pool.get_free_block_count() == 3

    # Re-allocate with same hash -> reuses b1, increments ref_count
    b1_reuse = pool.allocate_block(block_hash="hash_a")
    assert b1_reuse.block_id == 0
    assert b1_reuse.ref_count == 2
    assert pool.get_free_block_count() == 3

    # Release once -> ref_count 1, not yet freed
    freed = pool.release_block(0)
    assert not freed
    assert pool.blocks[0].ref_count == 1
    assert pool.get_free_block_count() == 3

    # Release again -> ref_count 0, returned to free list
    freed = pool.release_block(0)
    assert freed
    assert pool.blocks[0].ref_count == 0
    assert pool.blocks[0].is_free
    assert pool.get_free_block_count() == 4


def test_virtual_block_table_fork_cow():
    pool = PhysicalBlockPool(total_blocks=8)
    table = VirtualBlockTable(pool=pool)

    # Build sequence seq_parent with 2 blocks
    table.create_sequence("seq_parent")
    b0 = table.append_block("seq_parent", "hash_0")
    b1 = table.append_block("seq_parent", "hash_1")

    assert table.get_physical_blocks("seq_parent") == [b0.block_id, b1.block_id]
    assert pool.blocks[b0.block_id].ref_count == 1
    assert pool.blocks[b1.block_id].ref_count == 1

    # Fork sequence -> seq_child shares physical blocks
    table.fork_sequence("seq_parent", "seq_child")
    assert table.get_physical_blocks("seq_child") == [b0.block_id, b1.block_id]
    assert pool.blocks[b0.block_id].ref_count == 2
    assert pool.blocks[b1.block_id].ref_count == 2

    # Append to child only
    b2 = table.append_block("seq_child", "hash_2")
    assert table.get_physical_blocks("seq_child") == [b0.block_id, b1.block_id, b2.block_id]
    assert table.get_physical_blocks("seq_parent") == [b0.block_id, b1.block_id]

    # Release parent -> child retains shared blocks
    freed = table.release_sequence("seq_parent")
    assert freed == []  # None freed because child is still referencing them
    assert pool.blocks[b0.block_id].ref_count == 1
    assert pool.blocks[b1.block_id].ref_count == 1

    # Release child -> now all 3 blocks are freed
    freed_child = table.release_sequence("seq_child")
    assert set(freed_child) == {b0.block_id, b1.block_id, b2.block_id}
    assert pool.get_free_block_count() == 8
