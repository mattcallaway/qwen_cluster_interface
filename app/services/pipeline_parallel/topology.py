from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(frozen=True)
class PipelineStageConfig:
    """Configuration for an individual node stage in a pipeline parallel cluster."""
    stage_index: int
    node_id: str
    model_name: str
    layer_start: int
    layer_end: int
    endpoint_url: str
    total_layers: int

    def __post_init__(self):
        if self.layer_start < 0:
            raise ValueError("layer_start must be >= 0")
        if self.layer_end <= self.layer_start:
            raise ValueError(f"layer_end ({self.layer_end}) must be > layer_start ({self.layer_start})")
        if self.layer_end > self.total_layers:
            raise ValueError(f"layer_end ({self.layer_end}) exceeds total_layers ({self.total_layers})")

    @property
    def layer_count(self) -> int:
        return self.layer_end - self.layer_start

    @property
    def is_first_stage(self) -> bool:
        return self.stage_index == 0

    @property
    def is_last_stage(self) -> bool:
        return self.layer_end == self.total_layers


class PipelineTopology:
    """
    Validates and manages the distributed pipeline topology across cluster nodes.
    Ensures that layer partitions are contiguous, complete, and non-overlapping.
    """

    def __init__(self, pipeline_name: str, total_layers: int, stages: Optional[List[PipelineStageConfig]] = None):
        self.pipeline_name = pipeline_name
        self.total_layers = total_layers
        self.stages: List[PipelineStageConfig] = []
        if stages:
            for s in stages:
                self.add_stage(s)

    def add_stage(self, stage: PipelineStageConfig) -> None:
        if stage.total_layers != self.total_layers:
            raise ValueError(f"Stage total_layers {stage.total_layers} does not match topology {self.total_layers}")
        self.stages.append(stage)
        self.stages.sort(key=lambda s: s.stage_index)

    def validate(self) -> bool:
        """
        Validates:
        1. At least 1 stage.
        2. Stage indices are strictly 0, 1, 2, ..., N-1.
        3. Layers are contiguous: Stage 0 starts at 0, Stage i starts at Stage i-1's layer_end.
        4. Final stage ends exactly at total_layers.
        """
        if not self.stages:
            raise ValueError("PipelineTopology must have at least one stage")

        for idx, stage in enumerate(self.stages):
            if stage.stage_index != idx:
                raise ValueError(f"Non-contiguous stage index: expected {idx}, got {stage.stage_index}")

        # Check layer coverage
        if self.stages[0].layer_start != 0:
            raise ValueError(f"First stage must start at layer 0, got {self.stages[0].layer_start}")

        for i in range(1, len(self.stages)):
            prev = self.stages[i - 1]
            curr = self.stages[i]
            if curr.layer_start != prev.layer_end:
                raise ValueError(
                    f"Gap or overlap between stage {prev.stage_index} (end {prev.layer_end}) "
                    f"and stage {curr.stage_index} (start {curr.layer_start})"
                )

        if self.stages[-1].layer_end != self.total_layers:
            raise ValueError(
                f"Final stage ends at layer {self.stages[-1].layer_end}, but total_layers is {self.total_layers}"
            )

        return True

    @classmethod
    def create_uniform_partition(
        cls,
        pipeline_name: str,
        model_name: str,
        total_layers: int,
        node_endpoints: List[tuple[str, str]],  # (node_id, endpoint_url)
    ) -> "PipelineTopology":
        """
        Evenly distributes total_layers across the provided nodes.
        Handles remainders by distributing them to the earlier stages.
        """
        if not node_endpoints:
            raise ValueError("At least one node required")

        n_stages = len(node_endpoints)
        base_layers = total_layers // n_stages
        remainder = total_layers % n_stages

        topology = cls(pipeline_name=pipeline_name, total_layers=total_layers)
        current_start = 0

        for i, (node_id, endpoint_url) in enumerate(node_endpoints):
            count = base_layers + (1 if i < remainder else 0)
            end = current_start + count
            stage = PipelineStageConfig(
                stage_index=i,
                node_id=node_id,
                model_name=model_name,
                layer_start=current_start,
                layer_end=end,
                endpoint_url=endpoint_url,
                total_layers=total_layers,
            )
            topology.add_stage(stage)
            current_start = end

        topology.validate()
        return topology
