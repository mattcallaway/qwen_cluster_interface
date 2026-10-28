"""
Distributed Pipeline Parallelism for Qwen Collective.
Coordinates layer partitioning, activation streaming, and inter-node stage execution.
"""

from .topology import PipelineStageConfig, PipelineTopology
from .activation_protocol import ActivationFrame, ActivationProtocol
from .runner import PipelineStageRunner

__all__ = [
    "PipelineStageConfig",
    "PipelineTopology",
    "ActivationFrame",
    "ActivationProtocol",
    "PipelineStageRunner",
]
