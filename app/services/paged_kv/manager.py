import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from .hasher import PagedKVHasher, PrefixRadixTree, TokenBlock
from .page_table import PhysicalBlock, PhysicalBlockPool, VirtualBlockTable
from .store import PagedKVStore


class PagedKVManager:
    """
    Coordinates paged KV cache allocation, cryptographic prefix matching,
    tiered storage offloading, and memory boundary enforcement.
    """

    def __init__(
        self,
        storage_dir: Union[str, Path],
        max_ram_blocks: int = 64,
        block_size: int = 16,
        compress: bool = True,
    ):
        self.block_size = block_size
        self.max_ram_blocks = max_ram_blocks
        self.hasher = PagedKVHasher(block_size=block_size)
        self.radix_tree = PrefixRadixTree(hasher=self.hasher)
        self.pool = PhysicalBlockPool(total_blocks=max_ram_blocks * 4)
        self.table = VirtualBlockTable(pool=self.pool)
        self.store = PagedKVStore(storage_dir=storage_dir, compress=compress)

        # Statistics
        self.total_queries = 0
        self.total_matched_tokens = 0
        self.total_evaluated_tokens = 0
        self.total_evictions = 0

    def ingest_prompt(
        self,
        sequence_id: str,
        tokens: Sequence[int],
        model_name: str,
        dummy_tensor_generator: Optional[callable] = None,
    ) -> Dict[str, Any]:
        """
        Ingests a prompt sequence, identifies cached prefix blocks, and attaches
        them to the sequence table, bypassing redundant prefill forward compute.
        """
        self.total_queries += 1
        total_tokens = len(tokens)
        self.total_evaluated_tokens += total_tokens

        # Step 1: Prefix search in Radix Tree
        matched_blocks, matched_token_count, payloads = self.radix_tree.match_prefix(tokens)
        self.total_matched_tokens += matched_token_count

        if sequence_id not in self.table.seq_tables:
            self.table.create_sequence(sequence_id)

        # Step 2: Attach matched prefix blocks to sequence
        for block in matched_blocks:
            self.table.append_block(sequence_id, block.block_hash)
            # Ensure block tensor is loaded if needed
            self.store.read_page(block.block_hash)

        # Step 3: Chunk and allocate remaining novel suffix tokens
        remaining_tokens = tokens[matched_token_count:]
        new_blocks: List[TokenBlock] = []

        if remaining_tokens:
            all_blocks = self.hasher.chunk_tokens(tokens)
            new_blocks = all_blocks[len(matched_blocks) :]

            for block in new_blocks:
                self.table.append_block(sequence_id, block.block_hash)

                # Generate or record KV tensor bytes for the new block
                if dummy_tensor_generator:
                    t_bytes = dummy_tensor_generator(block)
                else:
                    # Default deterministic representation: packed tokens + hash
                    t_bytes = f"TENSOR:{model_name}:{block.block_hash}:{block.tokens}".encode()

                self.store.write_page(block.block_hash, t_bytes, persist_to_disk=True)

            # Insert new sequence branch into Radix tree
            self.radix_tree.insert(
                tokens,
                payload={"model": model_name, "last_seq": sequence_id},
            )

        # Step 4: Check RAM quota and evict LRU blocks if necessary
        self._enforce_ram_quota()

        prefill_saved_pct = (
            (matched_token_count / total_tokens * 100.0) if total_tokens > 0 else 0.0
        )

        return {
            "sequence_id": sequence_id,
            "total_tokens": total_tokens,
            "matched_tokens": matched_token_count,
            "novel_tokens": total_tokens - matched_token_count,
            "prefill_saved_percent": round(prefill_saved_pct, 2),
            "reused_blocks_count": len(matched_blocks),
            "new_blocks_count": len(new_blocks),
            "total_sequence_blocks": len(self.table.get_physical_blocks(sequence_id)),
            "ram_blocks_in_use": self.store.get_ram_page_count(),
        }

    def _enforce_ram_quota(self) -> int:
        """Flushes least recently used blocks to disk when RAM quota is exceeded."""
        current_ram_pages = self.store.get_ram_page_count()
        if current_ram_pages <= self.max_ram_blocks:
            return 0

        excess = current_ram_pages - self.max_ram_blocks
        lru_blocks = self.pool.get_lru_blocks(count=excess)

        evicted = 0
        for b in lru_blocks:
            if b.block_hash and self.store.is_in_ram(b.block_hash):
                if self.store.evict_from_ram(b.block_hash):
                    evicted += 1

        # Fallback guarantee: if cache still exceeds max_ram_blocks, evict oldest entries
        while self.store.get_ram_page_count() > self.max_ram_blocks:
            oldest_hash = next(iter(self.store._ram_cache))
            if self.store.evict_from_ram(oldest_hash):
                evicted += 1
            else:
                break

        self.total_evictions += evicted
        return evicted

    def restore_sequence_to_ram(self, sequence_id: str) -> int:
        """Ensures all physical blocks of a sequence are loaded in Tier 1 RAM."""
        hashes = self.table.get_block_hashes(sequence_id)
        restored = 0
        for h in hashes:
            if not self.store.is_in_ram(h):
                self.store.read_page(h)
                restored += 1
        return restored

    def release_sequence(self, sequence_id: str) -> int:
        """Releases sequence resources from the table."""
        freed_bids = self.table.release_sequence(sequence_id)
        return len(freed_bids)

    def get_metrics(self) -> Dict[str, Any]:
        hit_rate = (
            (self.total_matched_tokens / self.total_evaluated_tokens * 100.0)
            if self.total_evaluated_tokens > 0
            else 0.0
        )
        return {
            "total_queries": self.total_queries,
            "total_evaluated_tokens": self.total_evaluated_tokens,
            "total_matched_tokens": self.total_matched_tokens,
            "prefix_hit_rate_percent": round(hit_rate, 2),
            "total_evictions": self.total_evictions,
            "current_ram_pages": self.store.get_ram_page_count(),
            "max_ram_blocks": self.max_ram_blocks,
            "free_physical_blocks": self.pool.get_free_block_count(),
        }
