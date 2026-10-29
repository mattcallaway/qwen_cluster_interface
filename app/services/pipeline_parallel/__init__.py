"""
Distributed Pipeline Parallelism for Qwen Collective.
Coordinates layer partitioning, activation streaming, and inter-node stage execution.
"""

from .topology import PipelineStageConfig, PipelineTopology
from .activation_protocol import ActivationFrame, ActivationProtocol
from .runner import PipelineStageRunner
from .socket_transport import send_framed_bytes, recv_framed_bytes
from .stage1_server import Stage1SocketServer

__all__ = [
    "PipelineStageConfig",
    "PipelineTopology",
    "ActivationFrame",
    "ActivationProtocol",
    "PipelineStageRunner",
    "Stage1SocketServer",
    "send_framed_bytes",
    "recv_framed_bytes",
]
