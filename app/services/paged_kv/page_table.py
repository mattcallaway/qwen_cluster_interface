import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


@dataclass
class PhysicalBlock:
    """Represents a physical memory block slot in the KV cache."""
    block_id: int
    block_hash: Optional[str] = None
    ref_count: int = 0
    is_free: bool = True
    generation: int = 0
    pinned: bool = False
    last_accessed: float = field(default_factory=time.time)

    def touch(self):
        self.last_accessed = time.time()


@dataclass
class BlockMapping:
    """Mapping from logical block index in a sequence to a physical block slot."""
    logical_index: int
    physical_block_id: int
    block_hash: str
    generation: int = 0


class PhysicalBlockPool:
    """
    Manages fixed-size physical memory block slots.
    Implements reference counting, generation tracking, explicit pinning,
    eviction callbacks for disk offload, and fail-closed allocation under memory pressure.
    """

    def __init__(self, total_blocks: int = 1024):
        if total_blocks < 1:
            raise ValueError("total_blocks must be positive")
        self.total_blocks = total_blocks
        self.blocks: List[PhysicalBlock] = [
            PhysicalBlock(block_id=i) for i in range(total_blocks)
        ]
        self.free_block_ids: List[int] = list(range(total_blocks))
        # Hash to physical block id map for fast sharing
        self.hash_to_block_id: Dict[str, int] = {}
        # Optional callback invoked to offload unpinned resident blocks to disk under pressure
        self.eviction_callback = None

    def get_free_block_count(self) -> int:
        return len(self.free_block_ids)

    def allocate_block(self, block_hash: Optional[str] = None) -> PhysicalBlock:
        """
        Allocates a physical block. If block_hash is given and already cached,
        reuses the existing block and increments its reference count.
        Under memory pressure, never recycles an active referenced block (ref_count > 0).
        If no unreferenced blocks are available, attempts offload via eviction_callback.
        Fails closed with MemoryError if all physical blocks are referenced and pinned.
        """
        if block_hash and block_hash in self.hash_to_block_id:
            bid = self.hash_to_block_id[block_hash]
            block = self.blocks[bid]
            block.ref_count += 1
            block.touch()
            return block

        if not self.free_block_ids:
            # Under churn, only recycle unreferenced blocks (ref_count == 0, not pinned)
            unreferenced_candidates = self.get_lru_blocks(count=1, unreferenced_only=True)
            if unreferenced_candidates:
                victim = unreferenced_candidates[0]
                if victim.block_hash and victim.block_hash in self.hash_to_block_id:
                    del self.hash_to_block_id[victim.block_hash]
                victim.block_hash = block_hash
                victim.ref_count = 1
                victim.is_free = False
                victim.generation += 1
                victim.touch()
                if block_hash:
                    self.hash_to_block_id[block_hash] = victim.block_id
                return victim

            # Try to offload an unpinned resident page to disk via eviction_callback
            if self.eviction_callback is not None:
                if self.eviction_callback():
                    if self.free_block_ids:
                        bid = self.free_block_ids.pop(0)
                        block = self.blocks[bid]
                        block.block_hash = block_hash
                        block.ref_count = 1
                        block.is_free = False
                        block.generation += 1
                        block.touch()
                        if block_hash:
                            self.hash_to_block_id[block_hash] = bid
                        return block

            raise MemoryError("PhysicalBlockPool exhausted: all physical blocks are referenced by active sequences")

        bid = self.free_block_ids.pop(0)
        block = self.blocks[bid]
        block.block_hash = block_hash
        block.ref_count = 1
        block.is_free = False
        block.generation += 1
        block.touch()

        if block_hash:
            self.hash_to_block_id[block_hash] = bid

        return block

    def pin_block(self, block_id: int) -> None:
        """Explicitly pins a block in RAM, preventing eviction/recycling."""
        block = self.blocks[block_id]
        if block.is_free:
            raise ValueError(f"Cannot pin freed block {block_id}")
        block.pinned = True
        block.touch()

    def unpin_block(self, block_id: int) -> None:
        """Removes explicit pin from a block."""
        block = self.blocks[block_id]
        block.pinned = False

    def retain_block(self, block_id: int) -> None:
        """Increments reference count of a block (e.g. for sequence forks)."""
        block = self.blocks[block_id]
        if block.is_free:
            raise ValueError(f"Cannot retain freed block {block_id}")
        block.ref_count += 1
        block.touch()

    def release_block(self, block_id: int) -> bool:
        """
        Decrements reference count. If ref_count reaches 0, returns the block to the free list.
        Returns True if the block was physically freed.
        """
        block = self.blocks[block_id]
        if block.is_free:
            return False

        block.ref_count -= 1
        if block.ref_count <= 0:
            block.ref_count = 0
            block.is_free = True
            block.pinned = False
            if block.block_hash and block.block_hash in self.hash_to_block_id:
                del self.hash_to_block_id[block.block_hash]
            block.block_hash = None
            self.free_block_ids.append(block_id)
            return True
        return False

    def get_lru_blocks(self, count: int, unreferenced_only: bool = False) -> List[PhysicalBlock]:
        """
        Returns active blocks sorted by least recently accessed timestamp.
        Used to select eviction candidates when RAM threshold is reached.
        """
        if unreferenced_only:
            active = [b for b in self.blocks if not b.is_free and b.ref_count == 0 and not b.pinned]
        else:
            active = [b for b in self.blocks if not b.is_free and not b.pinned]
        active.sort(key=lambda b: b.last_accessed)
        return active[:count]


class VirtualBlockTable:
    """
    Maintains logical-to-physical block mappings per inference sequence.
    Enforces Copy-On-Write (COW) semantics for branching agent rollouts,
    and supports safe offloading and remapping of virtual pages.
    """

    def __init__(self, pool: Optional[PhysicalBlockPool] = None):
        self.pool = pool or PhysicalBlockPool()
        # sequence_id -> list of physical block ids (None if page is offloaded to disk)
        self.seq_tables: Dict[str, List[Optional[int]]] = {}
        # sequence_id -> list of block hashes
        self.seq_hashes: Dict[str, List[str]] = {}

    def create_sequence(self, sequence_id: str) -> None:
        if sequence_id in self.seq_tables:
            raise KeyError(f"Sequence {sequence_id} already exists")
        self.seq_tables[sequence_id] = []
        self.seq_hashes[sequence_id] = []

    def append_block(self, sequence_id: str, block_hash: str) -> PhysicalBlock:
        """Allocates or reuses a block and appends it to the sequence."""
        if sequence_id not in self.seq_tables:
            self.create_sequence(sequence_id)

        block = self.pool.allocate_block(block_hash=block_hash)
        self.seq_tables[sequence_id].append(block.block_id)
        self.seq_hashes[sequence_id].append(block_hash)
        return block

    def offload_block(self, block_hash: str) -> int:
        """
        Unmaps physical block for block_hash across all sequences,
        offloading the page to disk and releasing the physical pool block.
        Returns the number of references released.
        """
        if block_hash not in self.pool.hash_to_block_id:
            return 0
        bid = self.pool.hash_to_block_id[block_hash]
        released_count = 0
        for seq_id, hashes in list(self.seq_hashes.items()):
            table = self.seq_tables.get(seq_id, [])
            for idx, h in enumerate(hashes):
                if h == block_hash and idx < len(table):
                    if table[idx] == bid:
                        table[idx] = None
                        self.pool.release_block(bid)
                        released_count += 1
        return released_count

    def remap_block(self, sequence_id: str, logical_idx: int, new_block: PhysicalBlock) -> None:
        """Remaps a restored physical block back into the sequence table."""
        if sequence_id not in self.seq_tables:
            raise KeyError(f"Sequence {sequence_id} not found")
        if logical_idx >= len(self.seq_tables[sequence_id]):
            raise IndexError(f"Logical block index {logical_idx} out of range")
        self.seq_tables[sequence_id][logical_idx] = new_block.block_id

    def get_physical_blocks(self, sequence_id: str) -> List[Optional[int]]:
        """Returns the list of physical block IDs for a sequence."""
        if sequence_id not in self.seq_tables:
            raise KeyError(f"Sequence {sequence_id} not found")
        for bid in self.seq_tables[sequence_id]:
            if bid is not None:
                self.pool.blocks[bid].touch()
        return list(self.seq_tables[sequence_id])

    def get_block_hashes(self, sequence_id: str) -> List[str]:
        if sequence_id not in self.seq_hashes:
            raise KeyError(f"Sequence {sequence_id} not found")
        return list(self.seq_hashes[sequence_id])

    def fork_sequence(self, parent_seq_id: str, child_seq_id: str) -> None:
        """
        Forks a parent sequence into a child sequence sharing physical blocks (COW).
        Increments reference count of all shared physical blocks.
        """
        if parent_seq_id not in self.seq_tables:
            raise KeyError(f"Parent sequence {parent_seq_id} not found")
        if child_seq_id in self.seq_tables:
            raise KeyError(f"Child sequence {child_seq_id} already exists")

        parent_bids = self.seq_tables[parent_seq_id]
        parent_hashes = self.seq_hashes[parent_seq_id]

        for bid in parent_bids:
            if bid is not None:
                self.pool.retain_block(bid)

        self.seq_tables[child_seq_id] = list(parent_bids)
        self.seq_hashes[child_seq_id] = list(parent_hashes)

    def release_sequence(self, sequence_id: str) -> List[int]:
        """
        Releases all blocks allocated to a sequence.
        Returns the list of physical block IDs that were completely freed.
        """
        if sequence_id not in self.seq_tables:
            return []

        bids = self.seq_tables.pop(sequence_id)
        self.seq_hashes.pop(sequence_id, None)

        freed_bids: List[int] = []
        for bid in bids:
            if bid is not None and self.pool.release_block(bid):
                freed_bids.append(bid)
        return freed_bids

