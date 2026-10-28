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
    last_accessed: float = field(default_factory=time.time)

    def touch(self):
        self.last_accessed = time.time()


@dataclass
class BlockMapping:
    """Mapping from logical block index in a sequence to a physical block slot."""
    logical_index: int
    physical_block_id: int
    block_hash: str


class PhysicalBlockPool:
    """
    Manages fixed-size physical memory block slots.
    Implements reference counting and LRU eviction order tracking.
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

    def get_free_block_count(self) -> int:
        return len(self.free_block_ids)

    def allocate_block(self, block_hash: Optional[str] = None) -> PhysicalBlock:
        """
        Allocates a physical block. If block_hash is given and already cached,
        reuses the existing block and increments its reference count.
        """
        if block_hash and block_hash in self.hash_to_block_id:
            bid = self.hash_to_block_id[block_hash]
            block = self.blocks[bid]
            block.ref_count += 1
            block.touch()
            return block

        if not self.free_block_ids:
            raise MemoryError("PhysicalBlockPool exhausted: no free memory blocks available")

        bid = self.free_block_ids.pop(0)
        block = self.blocks[bid]
        block.block_hash = block_hash
        block.ref_count = 1
        block.is_free = False
        block.touch()

        if block_hash:
            self.hash_to_block_id[block_hash] = bid

        return block

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
            if block.block_hash and block.block_hash in self.hash_to_block_id:
                del self.hash_to_block_id[block.block_hash]
            block.block_hash = None
            self.free_block_ids.append(block_id)
            return True
        return False

    def get_lru_blocks(self, count: int) -> List[PhysicalBlock]:
        """
        Returns active blocks sorted by least recently accessed timestamp.
        Used to select eviction candidates when RAM threshold is reached.
        """
        active = [b for b in self.blocks if not b.is_free]
        active.sort(key=lambda b: b.last_accessed)
        return active[:count]


class VirtualBlockTable:
    """
    Maintains logical-to-physical block mappings per inference sequence.
    Enforces Copy-On-Write (COW) semantics for branching agent rollouts.
    """

    def __init__(self, pool: Optional[PhysicalBlockPool] = None):
        self.pool = pool or PhysicalBlockPool()
        # sequence_id -> list of physical block ids
        self.seq_tables: Dict[str, List[int]] = {}
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

    def get_physical_blocks(self, sequence_id: str) -> List[int]:
        """Returns the list of physical block IDs for a sequence."""
        if sequence_id not in self.seq_tables:
            raise KeyError(f"Sequence {sequence_id} not found")
        for bid in self.seq_tables[sequence_id]:
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
            if self.pool.release_block(bid):
                freed_bids.append(bid)
        return freed_bids
