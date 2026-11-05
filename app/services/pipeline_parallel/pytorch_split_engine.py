"""
PyTorch Multi-Stage Split Execution Engine (Increment 17 - Tier 3 Local Split Execution).

Provides genuine multi-stage transformer layer partitioning for HuggingFace CausalLM models
(e.g., Qwen/Qwen2.5-0.5B-Instruct) with authenticated QCACT1 wire framing at every stage boundary.
Supports 2-stage and N-stage partitions, intermediate activation verification, single-step prefill,
and deterministic multi-step greedy autoregressive decoding.
"""

from __future__ import annotations

import copy
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import torch
    import torch.nn as nn
    HAS_TORCH = True
except ImportError:
    torch = None
    nn = None
    HAS_TORCH = False

from .activation_protocol import ActivationProtocol


class TransformerStagePartition:
    """
    Represents a contiguous slice of transformer layers [start_layer, end_layer):
    - Stage 0 owns embed_tokens + layers[0:end_layer].
    - Intermediate stages own layers[start_layer:end_layer].
    - Final stage owns layers[start_layer:total_layers] + final RMSNorm + lm_head.
    """

    def __init__(
        self,
        full_model: Any,
        stage_index: int,
        total_stages: int,
        start_layer: int,
        end_layer: int,
        device: str = "cuda:0",
        share_modules: bool = True,
    ):
        if not HAS_TORCH:
            raise RuntimeError("PyTorch is required for TransformerStagePartition.")
        total_layers = len(full_model.model.layers)
        if start_layer < 0 or end_layer > total_layers or start_layer >= end_layer:
            raise ValueError(
                f"Invalid layer range [{start_layer}, {end_layer}) for model with {total_layers} layers."
            )

        self.stage_index = stage_index
        self.total_stages = total_stages
        self.start_layer = start_layer
        self.end_layer = end_layer
        self.device = device
        self.is_first = stage_index == 0
        self.is_last = stage_index == (total_stages - 1)

        def _bind(mod: Any) -> Any:
            return mod.to(device) if share_modules else copy.deepcopy(mod).to(device)

        self.embed_tokens = _bind(full_model.model.embed_tokens) if self.is_first else None
        self.rotary_emb = _bind(full_model.model.rotary_emb)
        self.layers = nn.ModuleList(
            [_bind(full_model.model.layers[i]) for i in range(start_layer, end_layer)]
        )
        self.norm = _bind(full_model.model.norm) if self.is_last else None
        self.lm_head = _bind(full_model.lm_head) if self.is_last else None

    def forward_stage(
        self,
        hidden_or_input_ids: torch.Tensor,
        position_ids: Optional[torch.Tensor] = None,
        attention_mask: Optional[torch.Tensor] = None,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        with torch.no_grad():
            if self.is_first:
                if hidden_or_input_ids.ndim != 2 or hidden_or_input_ids.shape[1] == 0:
                    raise ValueError("input_ids must be a non-empty 2D tensor [batch, seq_len].")
                input_ids = hidden_or_input_ids.to(self.device)
                _, seq_len = input_ids.shape
                hidden_states = self.embed_tokens(input_ids)
                if position_ids is None:
                    position_ids = torch.arange(0, seq_len, dtype=torch.long, device=self.device).unsqueeze(0)
                else:
                    position_ids = position_ids.to(self.device)
            else:
                hidden_states = hidden_or_input_ids.to(self.device)
                if position_ids is None:
                    seq_len = hidden_states.shape[1]
                    position_ids = torch.arange(0, seq_len, dtype=torch.long, device=self.device).unsqueeze(0)
                else:
                    position_ids = position_ids.to(self.device)

            cos, sin = self.rotary_emb(hidden_states, position_ids)
            seq_len = hidden_states.shape[1]
            causal_mask = attention_mask
            if causal_mask is None and seq_len > 1:
                min_dtype = torch.finfo(hidden_states.dtype).min
                causal_mask = torch.full(
                    (1, 1, seq_len, seq_len),
                    fill_value=min_dtype,
                    dtype=hidden_states.dtype,
                    device=self.device,
                )
                causal_mask = torch.triu(causal_mask, diagonal=1)

            for layer in self.layers:
                layer_outputs = layer(
                    hidden_states,
                    attention_mask=causal_mask,
                    position_ids=position_ids,
                    position_embeddings=(cos, sin),
                    use_cache=False,
                )
                hidden_states = layer_outputs[0]

            if self.is_last:
                hidden_states = self.norm(hidden_states)
                logits = self.lm_head(hidden_states)
                return logits, position_ids

            return hidden_states, position_ids


class LocalSplitPipelineEngine:
    """
    Executes a HuggingFace CausalLM across N local stage partitions connected by
    authenticated QCACT1 wire framing at every boundary.
    """

    def __init__(
        self,
        full_model: Any,
        layer_splits: Sequence[Tuple[int, int]],
        secret_key: bytes = b"qc_wireguard_pipeline_secret_key_32b!",
        device: str = "cuda:0",
        share_modules: bool = True,
    ):
        if not HAS_TORCH:
            raise RuntimeError("PyTorch is required for LocalSplitPipelineEngine.")
        total_layers = len(full_model.model.layers)
        if not layer_splits:
            raise ValueError("layer_splits cannot be empty.")

        expected_start = 0
        for idx, (s, e) in enumerate(layer_splits):
            if s != expected_start or e <= s or e > total_layers:
                raise ValueError(
                    f"Non-contiguous or invalid split {layer_splits} for {total_layers} layers at stage {idx}."
                )
            expected_start = e
        if expected_start != total_layers:
            raise ValueError(
                f"layer_splits must cover all {total_layers} layers; ended at {expected_start}."
            )

        self.protocol = ActivationProtocol(secret_key=secret_key)
        self.device = device
        self.model_dtype = next(full_model.parameters()).dtype
        self.stages: List[TransformerStagePartition] = [
            TransformerStagePartition(
                full_model=full_model,
                stage_index=idx,
                total_stages=len(layer_splits),
                start_layer=s,
                end_layer=e,
                device=device,
                share_modules=share_modules,
            )
            for idx, (s, e) in enumerate(layer_splits)
        ]

    def forward_split(
        self,
        input_ids: torch.Tensor,
        session_id: str = "split-seq-01",
        wire_dtype: Optional[str] = None,
    ) -> Dict[str, Any]:
        if input_ids.ndim != 2 or input_ids.shape[0] != 1 or input_ids.shape[1] == 0:
            raise ValueError("input_ids must have shape [1, seq_len] with seq_len >= 1.")

        seq_len = input_ids.shape[1]
        if wire_dtype is None:
            wire_dtype = "float16" if self.model_dtype == torch.float16 else "float32"
        torch_wire_dtype = torch.float16 if wire_dtype == "float16" else torch.float32

        t_start = time.perf_counter()
        stage_latencies_ms: List[float] = []
        wire_latencies_ms: List[float] = []
        boundary_hidden_states: List[torch.Tensor] = []
        total_wire_bytes = 0
        total_frames = 0

        current_tensor = input_ids
        position_ids: Optional[torch.Tensor] = None

        for idx, stage in enumerate(self.stages):
            t_stage = time.perf_counter()
            out_tensor, position_ids = stage.forward_stage(current_tensor, position_ids=position_ids)
            if self.device.startswith("cuda"):
                torch.cuda.synchronize()
            stage_latencies_ms.append((time.perf_counter() - t_stage) * 1000.0)

            if not stage.is_last:
                boundary_hidden_states.append(out_tensor.detach().cpu())
                t_wire = time.perf_counter()
                frames: List[bytes] = []
                out_cpu = out_tensor.detach().cpu()
                for t_idx in range(seq_len):
                    vec = out_cpu[0, t_idx, :].tolist()
                    frame_bytes = self.protocol.encode_frame(
                        session_id=session_id,
                        stage_index=idx,
                        token_index=t_idx,
                        activations=vec,
                        tensor_dtype=wire_dtype,
                    )
                    frames.append(frame_bytes)
                    total_wire_bytes += len(frame_bytes)
                    total_frames += 1

                unpacked_vecs: List[List[float]] = []
                for expected_t_idx, f_bytes in enumerate(frames):
                    parsed, vec = self.protocol.decode_frame(f_bytes)
                    if parsed.stage_index != idx or parsed.token_index != expected_t_idx:
                        raise ValueError("Stage boundary frame ordering mismatch.")
                    unpacked_vecs.append(vec)

                current_tensor = torch.tensor(
                    unpacked_vecs, dtype=torch_wire_dtype, device=self.device
                ).unsqueeze(0).to(self.model_dtype)
                wire_latencies_ms.append((time.perf_counter() - t_wire) * 1000.0)
            else:
                current_tensor = out_tensor

        total_ms = (time.perf_counter() - t_start) * 1000.0
        return {
            "logits": current_tensor.detach().cpu(),
            "boundary_hidden_states": boundary_hidden_states,
            "stage_latencies_ms": stage_latencies_ms,
            "wire_latencies_ms": wire_latencies_ms,
            "total_wire_bytes": total_wire_bytes,
            "total_frames": total_frames,
            "total_ms": total_ms,
        }

    def generate_greedy(
        self,
        input_ids: torch.Tensor,
        max_new_tokens: int = 8,
        session_id: str = "split-gen-01",
        wire_dtype: Optional[str] = None,
    ) -> Dict[str, Any]:
        if max_new_tokens < 1:
            raise ValueError("max_new_tokens must be >= 1.")
        running_ids = input_ids.clone()
        generated_tokens: List[int] = []
        step_latencies_ms: List[float] = []
        total_wire_bytes = 0

        for step in range(max_new_tokens):
            res = self.forward_split(
                running_ids,
                session_id=f"{session_id}-s{step}",
                wire_dtype=wire_dtype,
            )
            logits = res["logits"]
            next_tok = int(torch.argmax(logits[0, -1, :]).item())
            generated_tokens.append(next_tok)
            step_latencies_ms.append(res["total_ms"])
            total_wire_bytes += res["total_wire_bytes"]
            next_tensor = torch.tensor([[next_tok]], dtype=running_ids.dtype, device=running_ids.device)
            running_ids = torch.cat([running_ids, next_tensor], dim=1)

        return {
            "generated_tokens": generated_tokens,
            "full_token_ids": running_ids[0].cpu().tolist(),
            "step_latencies_ms": step_latencies_ms,
            "total_wire_bytes": total_wire_bytes,
        }

