import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from .activation_protocol import ActivationProtocol
from .topology import PipelineStageConfig


class PipelineStageRunner:
    """
    Executes an individual pipeline stage partition.
    Integrates with ActivationProtocol for streaming tensors between nodes.
    """

    def __init__(
        self,
        config: PipelineStageConfig,
        protocol: ActivationProtocol,
        hidden_dim: int = 1536,  # Qwen2.5:1.5B default hidden dimension
        stage_compute_fn: Optional[Callable[[List[float], int, int], List[float]]] = None,
    ):
        self.config = config
        self.protocol = protocol
        self.hidden_dim = hidden_dim
        self.stage_compute_fn = stage_compute_fn

    def _default_layer_transform(
        self, input_vec: List[float], start_l: int, end_l: int
    ) -> List[float]:
        """
        Deterministic numerical simulation of transformer layer forward computation
        (LayerNorm + Attention + MLP residual additions).
        """
        out = list(input_vec)
        layer_scale = 1.0 / math.sqrt(self.hidden_dim)

        for l in range(start_l, end_l):
            # Simulated residual block: out = out + tanh(out * weights)
            weight = math.sin(l + 1.0) * layer_scale
            out = [(v + math.tanh(v * 1.05 + weight)) for v in out]

        return out

    def process_ingress(
        self,
        session_id: str,
        token_index: int,
        token_id: int,
        tensor_dtype: str = "float32",
    ) -> bytes:
        """
        Stage 0 Ingress: Embeds token into initial hidden dimension, executes
        assigned layers 0..K-1, and serializes the activation frame for Stage 1.
        """
        if not self.config.is_first_stage:
            raise RuntimeError(f"process_ingress called on non-initial stage {self.config.stage_index}")

        # Simulated token embedding: map token_id to initial hidden state
        base_val = math.sin(token_id * 0.1234)
        initial_activations = [
            base_val + math.cos(i * 0.05) * 0.1 for i in range(self.hidden_dim)
        ]

        if self.stage_compute_fn:
            layer_out = self.stage_compute_fn(
                initial_activations, self.config.layer_start, self.config.layer_end
            )
        else:
            layer_out = self._default_layer_transform(
                initial_activations, self.config.layer_start, self.config.layer_end
            )

        return self.protocol.encode_frame(
            session_id=session_id,
            stage_index=self.config.stage_index,
            token_index=token_index,
            activations=layer_out,
            tensor_dtype=tensor_dtype,
        )

    def process_intermediate(
        self,
        frame_bytes: bytes,
        tensor_dtype: str = "float32",
    ) -> bytes:
        """
        Intermediate Stage: Decodes previous activation frame, executes assigned
        layers, and encodes a new activation frame for the subsequent stage.
        """
        if self.config.is_first_stage or self.config.is_last_stage:
            raise RuntimeError(f"process_intermediate called on stage {self.config.stage_index}")

        frame, activations = self.protocol.decode_frame(frame_bytes)

        if len(activations) != self.hidden_dim:
            raise ValueError(
                f"Expected hidden_dim {self.hidden_dim}, got {len(activations)}"
            )

        if self.stage_compute_fn:
            layer_out = self.stage_compute_fn(
                activations, self.config.layer_start, self.config.layer_end
            )
        else:
            layer_out = self._default_layer_transform(
                activations, self.config.layer_start, self.config.layer_end
            )

        return self.protocol.encode_frame(
            session_id=frame.session_id,
            stage_index=self.config.stage_index,
            token_index=frame.token_index,
            activations=layer_out,
            tensor_dtype=tensor_dtype,
        )

    def process_egress(self, frame_bytes: bytes) -> Dict[str, Any]:
        """
        Final Stage: Decodes previous activation frame, computes final layers + LM head,
        and samples the predicted next token.
        """
        if not self.config.is_last_stage:
            raise RuntimeError(f"process_egress called on non-terminal stage {self.config.stage_index}")

        frame, activations = self.protocol.decode_frame(frame_bytes)

        if self.stage_compute_fn:
            layer_out = self.stage_compute_fn(
                activations, self.config.layer_start, self.config.layer_end
            )
        else:
            layer_out = self._default_layer_transform(
                activations, self.config.layer_start, self.config.layer_end
            )

        # Simulated LM Head: argmax over projected vocab slice
        # Projects first 100 dimensions to determine next token
        sampled_token_id = max(range(min(100, len(layer_out))), key=lambda i: layer_out[i])

        return {
            "session_id": frame.session_id,
            "token_index": frame.token_index,
            "predicted_token_id": sampled_token_id,
            "final_stage_index": self.config.stage_index,
        }
