import pytest
from app.services.paged_kv.page_table import PhysicalBlockPool, VirtualBlockTable


def test_reproduce_live_page_overwrite_in_one_block_pool():
    """
    Reproduction test for Material Finding 3 / probe live_block_recycled:
    In a 1-block pool, allocating A then B while A is still referenced must NOT
    silently recycle A's slot or overwrite A's contents.
    It MUST fail closed with MemoryError, preserving A's reference and hash.
    """
    pool = PhysicalBlockPool(total_blocks=1)
    hash_a = "a" * 64
    hash_b = "b" * 64

    # Allocate A
    block_a = pool.allocate_block(block_hash=hash_a)
    assert block_a.block_id == 0
    assert block_a.block_hash == hash_a
    assert block_a.ref_count == 1

    # Attempt to allocate B while A is still live/referenced
    # Expected: Fail closed with MemoryError because all physical blocks are live.
    # Broken behavior: Overwrites block 0 in place, mutating block_a.block_hash to hash_b.
    with pytest.raises(MemoryError, match="exhausted"):
        pool.allocate_block(block_hash=hash_b)

    # Invariant: block_a must retain its identity, hash, and ref_count
    assert block_a.block_hash == hash_a
    assert block_a.ref_count == 1


def test_generation_identity_and_safe_reuse_after_release():
    """
    When an active block is legitimately released (ref_count == 0),
    reallocating it must safely increment its generation and update mappings.
    """
    pool = PhysicalBlockPool(total_blocks=1)
    hash_a = "a" * 64
    hash_b = "b" * 64

    block_a = pool.allocate_block(block_hash=hash_a)
    gen_0 = getattr(block_a, "generation", 0)

    # Legitimate release
    freed = pool.release_block(block_a.block_id)
    assert freed is True
    assert pool.get_free_block_count() == 1

    # Now allocating B should succeed
    block_b = pool.allocate_block(block_hash=hash_b)
    assert block_b.block_id == 0
    assert block_b.block_hash == hash_b
    assert block_b.ref_count == 1
    # Generation must have advanced
    assert getattr(block_b, "generation", 0) > gen_0


def test_cow_fork_and_reference_lifecycle():
    """
    Verifies reference counting and Copy-on-Write semantics across sequence forks.
    """
    pool = PhysicalBlockPool(total_blocks=4)
    table = VirtualBlockTable(pool=pool)

    table.create_sequence("seq-1")
    table.append_block("seq-1", "hash-1")
    table.append_block("seq-1", "hash-2")

    bids_s1 = table.get_physical_blocks("seq-1")
    assert len(bids_s1) == 2
    assert pool.blocks[bids_s1[0]].ref_count == 1
    assert pool.blocks[bids_s1[1]].ref_count == 1

    # Fork seq-1 to seq-2
    table.fork_sequence("seq-1", "seq-2")
    bids_s2 = table.get_physical_blocks("seq-2")
    assert len(bids_s2) == 2
    # Both blocks now have 2 active owners
    assert pool.blocks[bids_s1[0]].ref_count == 2
    assert pool.blocks[bids_s1[1]].ref_count == 2

    # Release seq-1: ref counts must decrement to 1, no blocks freed
    freed_s1 = table.release_sequence("seq-1")
    assert len(freed_s1) == 0
    assert pool.blocks[bids_s2[0]].ref_count == 1
    assert pool.blocks[bids_s2[1]].ref_count == 1

    # Release seq-2: now ref count hits 0, physical blocks freed
    freed_s2 = table.release_sequence("seq-2")
    assert len(freed_s2) == 2
    assert pool.get_free_block_count() == 4


def test_pinning_prevents_eviction_and_recycling():
    """
    Pinned blocks must never be selected as eviction/recycling candidates.
    """
    pool = PhysicalBlockPool(total_blocks=2)
    b0 = pool.allocate_block("hash-0")
    b1 = pool.allocate_block("hash-1")

    # b0 is pinned
    pool.pin_block(b0.block_id)
    assert b0.pinned is True

    # Free b1
    pool.release_block(b1.block_id)
    assert pool.get_free_block_count() == 1

    # Allocate a new block - consumes free b1
    b2 = pool.allocate_block("hash-2")
    assert b2.block_id == 1

    # Pool is full now (b0 pinned & live, b2 live). Attempting b3 must fail closed.
    with pytest.raises(MemoryError, match="exhausted"):
        pool.allocate_block("hash-3")

    # Unpinning b0 doesn't automatically recycle it if ref_count > 0
    pool.unpin_block(b0.block_id)
    assert b0.pinned is False
    with pytest.raises(MemoryError, match="exhausted"):
        pool.allocate_block("hash-3")


def test_exhaustion_recovery_and_release_ordering():
    """
    Tests allocating to exhaustion, releasing in arbitrary order, and recovering capacity.
    """
    pool = PhysicalBlockPool(total_blocks=3)
    b0 = pool.allocate_block("h0")
    b1 = pool.allocate_block("h1")
    b2 = pool.allocate_block("h2")
    assert pool.get_free_block_count() == 0

    # Exhausted: cannot allocate h3
    with pytest.raises(MemoryError, match="exhausted"):
        pool.allocate_block("h3")

    # Release middle block b1
    freed = pool.release_block(b1.block_id)
    assert freed is True
    assert pool.get_free_block_count() == 1

    # Recovered: allocation of h3 succeeds in b1's slot
    b3 = pool.allocate_block("h3")
    assert b3.block_id == b1.block_id
    assert b3.block_hash == "h3"
    assert pool.get_free_block_count() == 0

    # Release remaining in reverse order
    pool.release_block(b3.block_id)
    pool.release_block(b2.block_id)
    pool.release_block(b0.block_id)
    assert pool.get_free_block_count() == 3


def test_concurrent_allocation_safety():
    """
    Tests concurrent allocation and release across threads.
    """
    import threading
    pool = PhysicalBlockPool(total_blocks=16)
    errors = []

    def worker(worker_id: int):
        try:
            for i in range(20):
                h = f"worker-{worker_id}-block-{i}"
                block = pool.allocate_block(h)
                assert block.ref_count >= 1
                # Small work
                pool.release_block(block.block_id)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Note: if any error occurred, report
    assert len(errors) == 0

