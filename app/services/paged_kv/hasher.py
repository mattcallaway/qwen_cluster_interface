import hashlib
import json
import struct
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union


@dataclass(frozen=True)
class TokenBlock:
    """Represents a fixed-size chunk of tokens with its cryptographic hash."""
    block_index: int
    tokens: Tuple[int, ...]
    parent_hash: Optional[str]
    block_hash: str
    token_count: int = field(init=False)

    def __post_init__(self):
        object.__setattr__(self, "token_count", len(self.tokens))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "block_index": self.block_index,
            "tokens": list(self.tokens),
            "parent_hash": self.parent_hash,
            "block_hash": self.block_hash,
            "token_count": self.token_count,
        }


class PagedKVHasher:
    """
    Computes deterministic cryptographic prefix hash chains over token sequences.
    Enforces fixed block sizing (default: 16 tokens/block).
    """

    def __init__(self, block_size: int = 16):
        if block_size < 1 or block_size > 128:
            raise ValueError("block_size must be between 1 and 128 tokens")
        self.block_size = block_size

    def serialize_tokens(self, tokens: Sequence[int]) -> bytes:
        """Serializes integer token sequence into big-endian 32-bit packed bytes."""
        return struct.pack(f">{len(tokens)}i", *tokens)

    def compute_block_hash(self, tokens: Sequence[int], parent_hash: Optional[str] = None) -> str:
        """
        Computes SHA-256 for a token block chained to its parent block hash.
        """
        hasher = hashlib.sha256()
        if parent_hash:
            hasher.update(b"PARENT:")
            hasher.update(parent_hash.encode("ascii"))
            hasher.update(b":TOKENS:")
        else:
            hasher.update(b"ROOT:TOKENS:")
        hasher.update(self.serialize_tokens(tokens))
        return hasher.hexdigest()

    def chunk_tokens(self, tokens: Sequence[int]) -> List[TokenBlock]:
        """
        Segments a sequence of tokens into fixed-size TokenBlocks with chained hashes.
        Remaining trailing tokens (< block_size) form the final terminal block.
        """
        blocks: List[TokenBlock] = []
        parent_hash: Optional[str] = None

        for idx, start in enumerate(range(0, len(tokens), self.block_size)):
            chunk = tuple(tokens[start : start + self.block_size])
            b_hash = self.compute_block_hash(chunk, parent_hash)
            block = TokenBlock(
                block_index=idx,
                tokens=chunk,
                parent_hash=parent_hash,
                block_hash=b_hash,
            )
            blocks.append(block)
            parent_hash = b_hash

        return blocks


class RadixNode:
    """Node in the Radix Prefix Tree keyed by cryptographic block hashes."""

    def __init__(self, block_hash: Optional[str] = None, block: Optional[TokenBlock] = None):
        self.block_hash = block_hash
        self.block = block
        self.children: Dict[str, RadixNode] = {}
        self.payload: Optional[Dict[str, Any]] = None
        self.access_count: int = 0


class PrefixRadixTree:
    """
    In-memory Radix tree for zero-prefill prefix matching across prompt sequences.
    Enables O(K) lookup of pre-computed KV cache pages where K is the number of blocks.
    """

    def __init__(self, hasher: Optional[PagedKVHasher] = None):
        self.hasher = hasher or PagedKVHasher()
        self.root = RadixNode()
        self.total_nodes = 0
        self.total_stored_blocks = 0

    def insert(self, tokens: Sequence[int], payload: Optional[Dict[str, Any]] = None) -> List[TokenBlock]:
        """
        Inserts a token sequence into the prefix radix tree.
        Associates metadata (e.g. physical page IDs, model name) with each node.
        """
        blocks = self.hasher.chunk_tokens(tokens)
        current = self.root

        for block in blocks:
            if block.block_hash not in current.children:
                node = RadixNode(block_hash=block.block_hash, block=block)
                current.children[block.block_hash] = node
                self.total_nodes += 1
                self.total_stored_blocks += 1
            current = current.children[block.block_hash]
            current.access_count += 1
            if payload:
                if current.payload is None:
                    current.payload = {}
                current.payload.update(payload)

        return blocks

    def match_prefix(self, tokens: Sequence[int]) -> Tuple[List[TokenBlock], int, List[Dict[str, Any]]]:
        """
        Finds the longest contiguous prefix of blocks present in the tree.
        Returns:
            - matched_blocks: List[TokenBlock]
            - matched_tokens_count: int
            - page_payloads: List[Dict[str, Any]] corresponding to each matched block
        """
        blocks = self.hasher.chunk_tokens(tokens)
        matched_blocks: List[TokenBlock] = []
        page_payloads: List[Dict[str, Any]] = []
        matched_tokens = 0
        current = self.root

        for block in blocks:
            if block.block_hash in current.children:
                current = current.children[block.block_hash]
                current.access_count += 1
                matched_blocks.append(block)
                matched_tokens += block.token_count
                page_payloads.append(current.payload or {})
            else:
                break

        return matched_blocks, matched_tokens, page_payloads

    def remove_branch(self, block_hashes: Sequence[str]) -> bool:
        """
        Prunes a path of block hashes from the tree (used during eviction).
        """
        if not block_hashes:
            return False

        def _prune(node: RadixNode, depth: int) -> bool:
            if depth >= len(block_hashes):
                return len(node.children) == 0

            target_hash = block_hashes[depth]
            if target_hash not in node.children:
                return False

            child = node.children[target_hash]
            can_delete = _prune(child, depth + 1)
            if can_delete:
                del node.children[target_hash]
                self.total_nodes -= 1
                self.total_stored_blocks -= 1
                return len(node.children) == 0
            return False

        return _prune(self.root, 0)
