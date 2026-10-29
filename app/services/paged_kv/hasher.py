import hashlib
import json
import struct
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union


SCHEMA_VERSION = "1.0"

KNOWN_MODEL_PROFILES: Dict[str, Dict[str, str]] = {
    "qwen2.5:1.5b": {
        "model_revision": "qwen2.5_1.5b_rel_b3287e",
        "tokenizer_id": "qwen2.5_bpe_151643",
        "chat_template_hash": "qwen2.5_chatml_v1",
        "kv_layout": "bshd_fp16",
        "rope_config_hash": "rope_theta_1m_maxpos_32k",
        "tenant_id": "system_local",
    },
    "qwen2.5:0.5b": {
        "model_revision": "qwen2.5_0.5b_rel_4f92c1",
        "tokenizer_id": "qwen2.5_bpe_151643",
        "chat_template_hash": "qwen2.5_chatml_v1",
        "kv_layout": "bshd_fp16",
        "rope_config_hash": "rope_theta_1m_maxpos_32k",
        "tenant_id": "system_local",
    },
    "llama3.2:1b": {
        "model_revision": "llama3.2_1b_rel_1289cf",
        "tokenizer_id": "llama3.2_bpe_128256",
        "chat_template_hash": "llama3_instruct_v1",
        "kv_layout": "bshd_fp16",
        "rope_config_hash": "rope_theta_500k_maxpos_128k",
        "tenant_id": "system_local",
    },
}


@dataclass(frozen=True)
class ExecutionNamespace:
    """
    Immutable execution namespace binding cache entries to:
    - model architecture, weights revision or unique hash
    - tokenizer identity and chat template
    - tensor KV layout and numerical dtype
    - RoPE base and position configurations
    - tenant or security scope
    """
    model_name: str
    model_revision: str
    tokenizer_id: str = "tok_pinned_v1"
    chat_template_hash: str = "tmpl_pinned_v1"
    kv_layout: str = "bshd_fp16"
    rope_config_hash: str = "rope_theta_1m_v1"
    tenant_id: str = "tenant_local"

    def __post_init__(self):
        fields = [
            ("model_name", self.model_name),
            ("model_revision", self.model_revision),
            ("tokenizer_id", self.tokenizer_id),
            ("chat_template_hash", self.chat_template_hash),
            ("kv_layout", self.kv_layout),
            ("rope_config_hash", self.rope_config_hash),
            ("tenant_id", self.tenant_id),
        ]
        for name, val in fields:
            if val is None or not isinstance(val, str):
                raise TypeError(f"ExecutionNamespace field '{name}' must be a non-null string, got {type(val).__name__}")
            if not val.strip():
                raise ValueError(f"ExecutionNamespace field '{name}' cannot be empty or whitespace only")
            if val.strip().lower() == "default":
                raise ValueError(f"ExecutionNamespace field '{name}' cannot be generic placeholder 'default'; explicit identity required")
            if len(val) > 256:
                raise ValueError(f"ExecutionNamespace field '{name}' exceeds maximum allowed length of 256 chars (got {len(val)})")

    def canonical_bytes(self) -> bytes:
        """
        Produces unambiguous, deterministic canonical byte representation.
        Uses sorted keys with no whitespace (RFC 8785 JSON Canonicalization principles)
        and an explicit schema version header.
        """
        payload = {
            "schema": f"ExecutionNamespace/{SCHEMA_VERSION}",
            "fields": {
                "chat_template_hash": self.chat_template_hash,
                "kv_layout": self.kv_layout,
                "model_name": self.model_name,
                "model_revision": self.model_revision,
                "rope_config_hash": self.rope_config_hash,
                "tenant_id": self.tenant_id,
                "tokenizer_id": self.tokenizer_id,
            },
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    def compute_digest(self) -> str:
        """Computes the full 64-character SHA-256 hexadecimal digest of the canonical encoding."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @classmethod
    def create_canonical(
        cls,
        model_name: str,
        model_revision: Optional[str] = None,
        tokenizer_id: Optional[str] = None,
        chat_template_hash: Optional[str] = None,
        kv_layout: Optional[str] = None,
        rope_config_hash: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> "ExecutionNamespace":
        """
        Factory to construct an ExecutionNamespace with authentic model profiles
        or deterministic model-bound attributes, eliminating generic placeholders.
        """
        if not model_name or not isinstance(model_name, str) or not model_name.strip():
            raise ValueError("model_name must be a non-empty string")

        norm_name = model_name.strip()
        profile = KNOWN_MODEL_PROFILES.get(norm_name, {})

        rev = model_revision or profile.get("model_revision") or f"rev_{hashlib.sha256(f'rev:{norm_name}'.encode()).hexdigest()[:16]}"
        tok = tokenizer_id or profile.get("tokenizer_id") or f"tok_{hashlib.sha256(f'tok:{norm_name}'.encode()).hexdigest()[:16]}"
        tmpl = chat_template_hash or profile.get("chat_template_hash") or f"tmpl_{hashlib.sha256(f'tmpl:{norm_name}'.encode()).hexdigest()[:16]}"
        layout = kv_layout or profile.get("kv_layout") or "bshd_fp16"
        rope = rope_config_hash or profile.get("rope_config_hash") or f"rope_{hashlib.sha256(f'rope:{norm_name}'.encode()).hexdigest()[:16]}"
        tenant = tenant_id or profile.get("tenant_id") or "tenant_local"

        return cls(
            model_name=norm_name,
            model_revision=rev,
            tokenizer_id=tok,
            chat_template_hash=tmpl,
            kv_layout=layout,
            rope_config_hash=rope,
            tenant_id=tenant,
        )


@dataclass(frozen=True)
class TokenBlock:
    """Represents a fixed-size chunk of tokens with its cryptographic hash."""
    block_index: int
    tokens: Tuple[int, ...]
    parent_hash: Optional[str]
    block_hash: str
    namespace_digest: str = "ns_unbound"
    token_count: int = field(init=False)

    def __post_init__(self):
        object.__setattr__(self, "token_count", len(self.tokens))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "block_index": self.block_index,
            "tokens": list(self.tokens),
            "parent_hash": self.parent_hash,
            "block_hash": self.block_hash,
            "namespace_digest": self.namespace_digest,
            "token_count": self.token_count,
        }


class PagedKVHasher:
    """
    Computes deterministic cryptographic prefix hash chains over token sequences.
    Enforces fixed block sizing (default: 16 tokens/block) and namespace isolation.
    """

    def __init__(self, block_size: int = 16):
        if block_size < 1 or block_size > 128:
            raise ValueError("block_size must be between 1 and 128 tokens")
        self.block_size = block_size

    def serialize_tokens(self, tokens: Sequence[int]) -> bytes:
        """Serializes integer token sequence into big-endian 32-bit packed bytes."""
        return struct.pack(f">{len(tokens)}i", *tokens)

    def compute_block_hash(
        self,
        tokens: Sequence[int],
        parent_hash: Optional[str] = None,
        namespace: Optional[Union[ExecutionNamespace, str]] = None,
    ) -> str:
        """
        Computes SHA-256 for a token block chained to its parent block hash
        and bound to its immutable execution namespace.
        """
        ns_digest = "ns_unbound"
        if isinstance(namespace, ExecutionNamespace):
            ns_digest = namespace.compute_digest()
        elif isinstance(namespace, str) and namespace:
            ns_digest = ExecutionNamespace.create_canonical(model_name=namespace).compute_digest()

        hasher = hashlib.sha256()
        hasher.update(b"NS:")
        hasher.update(ns_digest.encode("ascii"))
        if parent_hash:
            hasher.update(b":PARENT:")
            hasher.update(parent_hash.encode("ascii"))
            hasher.update(b":TOKENS:")
        else:
            hasher.update(b":ROOT:TOKENS:")
        hasher.update(self.serialize_tokens(tokens))
        return hasher.hexdigest()

    def chunk_tokens(
        self,
        tokens: Sequence[int],
        namespace: Optional[Union[ExecutionNamespace, str]] = None,
    ) -> List[TokenBlock]:
        """
        Segments a sequence of tokens into fixed-size TokenBlocks with chained hashes
        bound to the given execution namespace.
        """
        blocks: List[TokenBlock] = []
        parent_hash: Optional[str] = None
        ns_digest = "ns_unbound"
        if isinstance(namespace, ExecutionNamespace):
            ns_digest = namespace.compute_digest()
        elif isinstance(namespace, str) and namespace:
            ns_digest = ExecutionNamespace.create_canonical(model_name=namespace).compute_digest()

        for idx, start in enumerate(range(0, len(tokens), self.block_size)):
            chunk = tuple(tokens[start : start + self.block_size])
            b_hash = self.compute_block_hash(chunk, parent_hash, namespace=namespace)
            block = TokenBlock(
                block_index=idx,
                tokens=chunk,
                parent_hash=parent_hash,
                block_hash=b_hash,
                namespace_digest=ns_digest,
            )
            blocks.append(block)
            parent_hash = b_hash

        return blocks


class RadixNode:
    """Node in the Radix Prefix Tree keyed by cryptographic block hashes."""

    def __init__(
        self,
        block_hash: Optional[str] = None,
        block: Optional[TokenBlock] = None,
        namespace_digest: str = "default",
    ):
        self.block_hash = block_hash
        self.block = block
        self.namespace_digest = namespace_digest
        self.children: Dict[str, RadixNode] = {}
        self.payload: Optional[Dict[str, Any]] = None
        self.access_count: int = 0


class PrefixRadixTree:
    """
    In-memory Radix tree for zero-prefill prefix matching across prompt sequences.
    Enables O(K) lookup of pre-computed KV cache pages with strict namespace isolation.
    """

    def __init__(self, hasher: Optional[PagedKVHasher] = None):
        self.hasher = hasher or PagedKVHasher()
        self.root = RadixNode()
        self.total_nodes = 0
        self.total_stored_blocks = 0

    def insert(
        self,
        tokens: Sequence[int],
        payload: Optional[Dict[str, Any]] = None,
        namespace: Optional[Union[ExecutionNamespace, str]] = None,
    ) -> List[TokenBlock]:
        """
        Inserts a token sequence into the prefix radix tree bound to its execution namespace.
        """
        blocks = self.hasher.chunk_tokens(tokens, namespace=namespace)
        current = self.root

        for block in blocks:
            if block.block_hash not in current.children:
                node = RadixNode(
                    block_hash=block.block_hash,
                    block=block,
                    namespace_digest=block.namespace_digest,
                )
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

    def match_prefix(
        self,
        tokens: Sequence[int],
        namespace: Optional[Union[ExecutionNamespace, str]] = None,
    ) -> Tuple[List[TokenBlock], int, List[Dict[str, Any]]]:
        """
        Finds the longest contiguous prefix of blocks present in the tree matching
        the exact execution namespace.
        """
        blocks = self.hasher.chunk_tokens(tokens, namespace=namespace)
        matched_blocks: List[TokenBlock] = []
        page_payloads: List[Dict[str, Any]] = []
        matched_tokens = 0
        current = self.root

        for block in blocks:
            if block.block_hash in current.children:
                child = current.children[block.block_hash]
                # Enforce namespace isolation
                if child.namespace_digest != block.namespace_digest:
                    break
                current = child
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
