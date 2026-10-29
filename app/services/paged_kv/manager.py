import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from .hasher import ExecutionNamespace, PagedKVHasher, PrefixRadixTree, TokenBlock
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
        max_ram_bytes: Optional[int] = None,
        block_size: int = 16,
        compress: bool = True,
    ):
        self.block_size = block_size
        self.max_ram_blocks = max_ram_blocks
        self.max_ram_bytes = max_ram_bytes or (max_ram_blocks * 64 * 1024)
        self.hasher = PagedKVHasher(block_size=block_size)
        self.radix_tree = PrefixRadixTree(hasher=self.hasher)
        self.pool = PhysicalBlockPool(total_blocks=max_ram_blocks * 4)
        self.table = VirtualBlockTable(pool=self.pool)
        self.store = PagedKVStore(storage_dir=storage_dir, compress=compress)

        # Connect physical pool eviction callback to safely offload unpinned blocks to disk
        self.pool.eviction_callback = self._evict_lru_page_to_disk
        self.sequence_namespaces: Dict[str, ExecutionNamespace] = {}

        # Statistics
        self.total_queries = 0
        self.total_matched_tokens = 0
        self.total_evaluated_tokens = 0
        self.total_evictions = 0

    def ingest_prompt(
        self,
        sequence_id: str,
        tokens: Sequence[int],
        model_name: Optional[str] = None,
        namespace: Optional[Union[ExecutionNamespace, str]] = None,
        dummy_tensor_generator: Optional[Callable[[TokenBlock], bytes]] = None,
    ) -> Dict[str, Any]:
        """
        Ingests a prompt sequence bound to an immutable execution namespace,
        identifies cached prefix blocks, and attaches them to the sequence table.
        """
        if namespace is None:
            namespace = ExecutionNamespace.create_canonical(model_name=model_name or "qwen2.5:1.5b")
        elif isinstance(namespace, str):
            namespace = ExecutionNamespace.create_canonical(model_name=namespace)

        self.sequence_namespaces[sequence_id] = namespace
        effective_model_name = namespace.model_name
        self.total_queries += 1
        total_tokens = len(tokens)
        self.total_evaluated_tokens += total_tokens

        # Step 1: Prefix search in Radix Tree bound to execution namespace
        matched_blocks, matched_token_count, payloads = self.radix_tree.match_prefix(
            tokens, namespace=namespace
        )
        self.total_matched_tokens += matched_token_count

        if sequence_id not in self.table.seq_tables:
            self.table.create_sequence(sequence_id)

        # Step 2: Attach matched prefix blocks to sequence
        for block in matched_blocks:
            self.table.append_block(sequence_id, block.block_hash)
            # Ensure block tensor is loaded into RAM if needed with namespace verification
            self.store.read_page(block.block_hash, expected_namespace_digest=block.namespace_digest)

        # Step 3: Chunk and allocate remaining novel suffix tokens
        remaining_tokens = tokens[matched_token_count:]
        new_blocks: List[TokenBlock] = []

        if remaining_tokens:
            all_blocks = self.hasher.chunk_tokens(tokens, namespace=namespace)
            new_blocks = all_blocks[len(matched_blocks) :]

            for block in new_blocks:
                self.table.append_block(sequence_id, block.block_hash)

                # Generate or record KV tensor bytes for the new block
                if dummy_tensor_generator:
                    t_bytes = dummy_tensor_generator(block)
                else:
                    t_bytes = f"TENSOR:{effective_model_name}:{block.block_hash}:{block.tokens}".encode()

                self.store.write_page(
                    block.block_hash,
                    t_bytes,
                    persist_to_disk=True,
                    namespace_digest=block.namespace_digest,
                )

            # Insert new sequence branch into Radix tree
            self.radix_tree.insert(
                tokens,
                payload={"model": effective_model_name, "last_seq": sequence_id},
                namespace=namespace,
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
            "ram_bytes_in_use": self.store.get_ram_bytes(),
        }

    def _evict_lru_page_to_disk(self) -> bool:
        """
        Offloads least recently used unpinned page from RAM cache to disk.
        Unmaps physical block from virtual table and frees the physical pool slot.
        """
        candidates = list(self.store._ram_cache.keys())
        for candidate_hash in candidates:
            bid = self.pool.hash_to_block_id.get(candidate_hash)
            if bid is not None and self.pool.blocks[bid].pinned:
                continue

            self.store.evict_from_ram(candidate_hash)
            self.table.offload_block(candidate_hash)
            self.total_evictions += 1
            return True

        return False

    def _enforce_ram_quota(self) -> int:
        """Flushes least recently used blocks to disk when RAM quota is exceeded."""
        evicted = 0
        while self.store.get_ram_page_count() > self.max_ram_blocks or (
            self.max_ram_bytes is not None and self.store.get_ram_bytes() > self.max_ram_bytes
        ):
            if not self._evict_lru_page_to_disk():
                break
            evicted += 1
        return evicted

    def restore_sequence_to_ram(self, sequence_id: str) -> int:
        """
        Ensures all physical blocks of a sequence are loaded in Tier 1 RAM,
        remapping unmapped pages from disk, validating identities, and re-enforcing RAM quotas.
        """
        hashes = self.table.get_block_hashes(sequence_id)
        ns = self.sequence_namespaces.get(sequence_id)
        expected_ns_digest = ns.compute_digest() if ns else None
        restored = 0
        for idx, h in enumerate(hashes):
            was_in_ram = self.store.is_in_ram(h)
            # read_page touches the block, validates namespace and key identity, moves it to MRU
            self.store.read_page(h, expected_namespace_digest=expected_ns_digest)
            if not was_in_ram:
                restored += 1
            if idx < len(self.table.seq_tables[sequence_id]):
                if self.table.seq_tables[sequence_id][idx] is None:
                    new_block = self.pool.allocate_block(block_hash=h)
                    self.table.remap_block(sequence_id, idx, new_block)

        # Strictly re-enforce RAM bounds after restoration
        self._enforce_ram_quota()
        return restored

    def release_sequence(self, sequence_id: str) -> int:
        """Releases sequence resources and cleans up table mappings."""
        self.sequence_namespaces.pop(sequence_id, None)
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
            "current_ram_bytes": self.store.get_ram_bytes(),
            "max_ram_bytes": self.max_ram_bytes,
            "free_physical_blocks": self.pool.get_free_block_count(),
            "active_sequences": len(self.table.seq_tables),
        }

