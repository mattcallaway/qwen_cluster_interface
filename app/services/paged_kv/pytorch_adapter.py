"""
PyTorch Paged KV Cache Adapter (Increment 17 - Tier 5 Real KV Reuse).

Bridges the virtual memory subsystem (PagedKVManager, VirtualBlockTable, PhysicalBlockPool)
with PyTorch Transformer model forward passes (DynamicCache, past_key_values).

Enforces:
1. 16-token page boundary alignment for Key and Value tensor state across all model layers.
2. Binary QCKVP2 authenticated envelope serialization for Tier 2 disk offloading.
3. Cryptographic namespace and tenant isolation via ExecutionNamespace/1.0.
4. Numerical parity with monolithic, un-cached reference executions.
"""

from __future__ import annotations

import io
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    import torch
    from transformers import DynamicCache
    HAS_TORCH = True
except ImportError:
    torch = None
    DynamicCache = None
    HAS_TORCH = False

from app.services.paged_kv.hasher import ExecutionNamespace
from app.services.paged_kv.identity import IssuedReuseAuthority
from app.services.paged_kv.manager import PagedKVManager


class PyTorchPagedKVAdapter:
    """
    Adapter integrating PagedKVManager with PyTorch CausalLM transformer architectures.
    Stores and reconstructs multi-layer Key and Value tensors for 16-token page blocks.
    """

    def __init__(self, manager: PagedKVManager, device: str = "cuda:0"):
        if not HAS_TORCH:
            raise RuntimeError("PyTorch and transformers are required to use PyTorchPagedKVAdapter.")
        self.manager = manager
        self.device = device
        # In-RAM tensor block lookup: block_hash -> (key_tensor_dict, value_tensor_dict)
        # Each dict maps layer_idx -> tensor of shape (batch, heads, 16, head_dim)
        self._ram_tensor_cache: Dict[str, Tuple[List[Any], List[Any]]] = {}

    def serialize_page_tensors(
        self,
        key_layers: List[torch.Tensor],
        val_layers: List[torch.Tensor],
    ) -> bytes:
        """Serializes 16-token KV tensor layers into portable binary bytes."""
        buffer = io.BytesIO()
        # Save as CPU half-precision tensors
        cpu_keys = [k.detach().cpu().to(torch.float16) for k in key_layers]
        cpu_vals = [v.detach().cpu().to(torch.float16) for v in val_layers]
        torch.save({"k": cpu_keys, "v": cpu_vals}, buffer)
        return buffer.getvalue()

    def deserialize_page_tensors(
        self,
        payload_bytes: bytes,
        device: Optional[str] = None,
    ) -> Tuple[List[torch.Tensor], List[torch.Tensor]]:
        """Deserializes binary bytes into GPU/CPU KV tensor layers."""
        dev = device or self.device
        buffer = io.BytesIO(payload_bytes)
        data = torch.load(buffer, weights_only=False, map_location=dev)
        return data["k"], data["v"]

    def extract_and_cache_sequence_kv(
        self,
        sequence_id: str,
        token_ids: List[int],
        past_key_values: Any,
        namespace: ExecutionNamespace,
        authority: Optional[IssuedReuseAuthority] = None,
    ) -> int:
        """
        Extracts 16-token KV tensor slices from past_key_values and saves them
        into the PagedKVManager virtual memory blocks.
        """
        num_tokens = len(token_ids)
        num_full_blocks = num_tokens // 16
        if num_full_blocks == 0:
            return 0

        # Ensure sequence is mapped in manager
        ingest_res = self.manager.ingest_prompt(
            sequence_id=sequence_id,
            tokens=token_ids,
            namespace=namespace,
            authority=authority,
        )

        block_hashes = self.manager.table.get_block_hashes(sequence_id)
        num_layers = (
            len(past_key_values)
            if isinstance(past_key_values, tuple)
            else len(past_key_values.key_cache)
        )

        cached_blocks = 0
        for block_idx in range(min(num_full_blocks, len(block_hashes))):
            b_hash = block_hashes[block_idx]
            start_tok = block_idx * 16
            end_tok = start_tok + 16

            # Extract layer slices
            k_layers: List[torch.Tensor] = []
            v_layers: List[torch.Tensor] = []

            for layer_idx in range(num_layers):
                if isinstance(past_key_values, tuple):
                    k_full = past_key_values[layer_idx][0]
                    v_full = past_key_values[layer_idx][1]
                else:
                    k_full = past_key_values.key_cache[layer_idx]
                    v_full = past_key_values.value_cache[layer_idx]

                k_slice = k_full[:, :, start_tok:end_tok, :].clone()
                v_slice = v_full[:, :, start_tok:end_tok, :].clone()
                k_layers.append(k_slice)
                v_layers.append(v_slice)

            # Store in fast RAM tensor cache
            self._ram_tensor_cache[b_hash] = (k_layers, v_layers)

            # Also persist payload through PagedKVStore for disk persistence & QCKVP2 integrity
            serialized = self.serialize_page_tensors(k_layers, v_layers)
            ns_digest = namespace.compute_digest()
            self.manager.store.write_page(b_hash, serialized, namespace_digest=ns_digest)
            cached_blocks += 1

        return cached_blocks

    def prepare_prefill_context(
        self,
        sequence_id: str,
        token_ids: List[int],
        namespace: ExecutionNamespace,
        authority: Optional[IssuedReuseAuthority] = None,
    ) -> Dict[str, Any]:
        """
        Inspects PagedKVManager for existing prefix hits. If authorized reuse exists,
        retrieves cached KV tensors, builds a pre-populated DynamicCache, and identifies
        the remaining suffix tokens for accelerated prefill.
        """
        t0 = time.perf_counter()
        ingest_res = self.manager.ingest_prompt(
            sequence_id=sequence_id,
            tokens=token_ids,
            namespace=namespace,
            authority=authority,
        )

        matched_tokens = ingest_res.get("matched_tokens", 0)
        is_authorized = ingest_res.get("verified_reuse_authorized", False)

        # Only use prefix if authorized and at least 1 full 16-token page matches
        candidate_blocks = (matched_tokens // 16) if is_authorized else 0
        if candidate_blocks == 0:
            return {
                "sequence_id": sequence_id,
                "warm_cache_hit": False,
                "matched_tokens": 0,
                "input_ids": token_ids,
                "position_ids": list(range(len(token_ids))),
                "past_key_values": None,
                "prepare_time_ms": (time.perf_counter() - t0) * 1000.0,
            }

        block_hashes = self.manager.table.get_block_hashes(sequence_id)
        ns_digest = namespace.compute_digest()

        # Validate that candidate blocks have authentic tensor state (not uncomputed placeholders)
        valid_blocks = 0
        for b_idx in range(min(candidate_blocks, len(block_hashes))):
            b_hash = block_hashes[b_idx]
            if b_hash in self._ram_tensor_cache:
                valid_blocks += 1
            else:
                raw_bytes = self.manager.store.read_page(b_hash, expected_namespace_digest=ns_digest)
                if raw_bytes and (raw_bytes.startswith(b"\x80") or len(raw_bytes) > 1024):
                    valid_blocks += 1
                else:
                    break

        reusable_tokens = valid_blocks * 16
        if reusable_tokens == 0:
            return {
                "sequence_id": sequence_id,
                "warm_cache_hit": False,
                "matched_tokens": 0,
                "input_ids": token_ids,
                "position_ids": list(range(len(token_ids))),
                "past_key_values": None,
                "prepare_time_ms": (time.perf_counter() - t0) * 1000.0,
            }

        num_blocks = valid_blocks

        # Assemble DynamicCache across all matching blocks
        reconstructed_cache = DynamicCache()
        sample_b_hash = block_hashes[0]

        # Ensure tensors are in RAM (either from _ram_tensor_cache or restored from disk)
        ns_digest = namespace.compute_digest()
        for b_idx in range(num_blocks):
            b_hash = block_hashes[b_idx]
            if b_hash not in self._ram_tensor_cache:
                # Read from PagedKVStore (authenticates QCKVP2 envelope and validates namespace)
                raw_bytes = self.manager.store.read_page(b_hash, expected_namespace_digest=ns_digest)
                if raw_bytes is None:
                    raise ValueError(f"Missing page in storage for block {b_hash}")
                k_layers, v_layers = self.deserialize_page_tensors(raw_bytes, device=self.device)
                self._ram_tensor_cache[b_hash] = (k_layers, v_layers)

        # Merge blocks per layer
        first_k, _ = self._ram_tensor_cache[sample_b_hash]
        num_layers = len(first_k)

        for layer_idx in range(num_layers):
            layer_k_chunks = [
                self._ram_tensor_cache[block_hashes[b_idx]][0][layer_idx].to(self.device)
                for b_idx in range(num_blocks)
            ]
            layer_v_chunks = [
                self._ram_tensor_cache[block_hashes[b_idx]][1][layer_idx].to(self.device)
                for b_idx in range(num_blocks)
            ]
            merged_k = torch.cat(layer_k_chunks, dim=2)
            merged_v = torch.cat(layer_v_chunks, dim=2)
            reconstructed_cache.update(merged_k, merged_v, layer_idx)

        suffix_tokens = token_ids[reusable_tokens:]
        position_ids = list(range(reusable_tokens, len(token_ids)))

        return {
            "sequence_id": sequence_id,
            "warm_cache_hit": True,
            "matched_tokens": reusable_tokens,
            "input_ids": suffix_tokens,
            "position_ids": position_ids,
            "past_key_values": reconstructed_cache,
            "prepare_time_ms": (time.perf_counter() - t0) * 1000.0,
        }

    def release_sequence(self, sequence_id: str):
        """Frees virtual memory mapping and associated RAM tensor handles."""
        hashes = self.manager.table.get_block_hashes(sequence_id)
        self.manager.release_sequence(sequence_id)
        # Note: physical blocks may still be retained in pool/store if referenced by other sequences
