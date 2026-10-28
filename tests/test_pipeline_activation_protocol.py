import pytest
from app.services.pipeline_parallel import (
    ActivationFrame,
    ActivationProtocol,
    PipelineStageConfig,
    PipelineStageRunner,
    PipelineTopology,
)

SECRET_KEY = b"qc-cluster-verification-secret-key-32b"


def test_topology_uniform_partition_and_validation():
    # Model with 28 layers (Qwen2.5-7B) partitioned across 3 nodes
    nodes = [
        ("qwen-laptop", "http://100.101.187.115:8000"),
        ("desktop-worker-01", "http://100.122.228.19:8000"),
        ("qwen-vps", "http://100.71.194.68:8000"),
    ]
    topo = PipelineTopology.create_uniform_partition(
        pipeline_name="qwen2.5-7b-cluster",
        model_name="qwen2.5:7b",
        total_layers=28,
        node_endpoints=nodes,
    )
    assert len(topo.stages) == 3
    # 28 // 3 = 9, rem 1 -> Stage 0: 10 layers, Stage 1: 9 layers, Stage 2: 9 layers
    assert topo.stages[0].layer_start == 0
    assert topo.stages[0].layer_end == 10
    assert topo.stages[0].is_first_stage
    assert not topo.stages[0].is_last_stage

    assert topo.stages[1].layer_start == 10
    assert topo.stages[1].layer_end == 19
    assert not topo.stages[1].is_first_stage
    assert not topo.stages[1].is_last_stage

    assert topo.stages[2].layer_start == 19
    assert topo.stages[2].layer_end == 28
    assert not topo.stages[2].is_first_stage
    assert topo.stages[2].is_last_stage

    # Check validation catches layer gaps
    bad_stage = PipelineStageConfig(
        stage_index=1,
        node_id="desktop-worker-01",
        model_name="qwen2.5:7b",
        layer_start=12,  # Gap from 10 to 12
        layer_end=20,
        endpoint_url="http://localhost",
        total_layers=28,
    )
    bad_topo = PipelineTopology("bad", total_layers=28, stages=[topo.stages[0], bad_stage, topo.stages[2]])
    with pytest.raises(ValueError, match="Gap or overlap"):
        bad_topo.validate()


def test_activation_protocol_roundtrip_and_tamper_detection():
    protocol = ActivationProtocol(secret_key=SECRET_KEY)
    activations = [0.12345, -0.98765, 3.14159, -2.71828] * 16  # 64 elements

    # Test float32 roundtrip
    frame_bytes_f32 = protocol.encode_frame(
        session_id="sess_12345",
        stage_index=0,
        token_index=42,
        activations=activations,
        tensor_dtype="float32",
    )
    frame, unpacked = protocol.decode_frame(frame_bytes_f32)
    assert frame.session_id == "sess_12345"
    assert frame.stage_index == 0
    assert frame.token_index == 42
    assert frame.hidden_dim == len(activations)
    assert frame.tensor_dtype == "float32"
    for v_orig, v_unp in zip(activations, unpacked):
        assert pytest.approx(v_orig, rel=1e-5) == v_unp

    # Test float16 roundtrip
    frame_bytes_f16 = protocol.encode_frame(
        session_id="sess_f16",
        stage_index=1,
        token_index=10,
        activations=activations,
        tensor_dtype="float16",
    )
    frame_f16, unpacked_f16 = protocol.decode_frame(frame_bytes_f16)
    assert frame_f16.tensor_dtype == "float16"
    for v_orig, v_unp in zip(activations, unpacked_f16):
        assert pytest.approx(v_orig, rel=1e-3) == v_unp

    # Tamper detection: flip one bit in payload
    mutated = bytearray(frame_bytes_f32)
    mutated[-1] ^= 0x01
    with pytest.raises(ValueError, match="HMAC signature mismatch"):
        protocol.decode_frame(bytes(mutated))


def test_three_stage_pipeline_forward_flow():
    protocol = ActivationProtocol(secret_key=SECRET_KEY)
    hidden_dim = 128  # Compact test hidden dimension

    stage0_cfg = PipelineStageConfig(
        stage_index=0,
        node_id="node_a",
        model_name="qwen2.5:1.5b",
        layer_start=0,
        layer_end=10,
        endpoint_url="http://node_a",
        total_layers=28,
    )
    stage1_cfg = PipelineStageConfig(
        stage_index=1,
        node_id="node_b",
        model_name="qwen2.5:1.5b",
        layer_start=10,
        layer_end=20,
        endpoint_url="http://node_b",
        total_layers=28,
    )
    stage2_cfg = PipelineStageConfig(
        stage_index=2,
        node_id="node_c",
        model_name="qwen2.5:1.5b",
        layer_start=20,
        layer_end=28,
        endpoint_url="http://node_c",
        total_layers=28,
    )

    runner0 = PipelineStageRunner(config=stage0_cfg, protocol=protocol, hidden_dim=hidden_dim)
    runner1 = PipelineStageRunner(config=stage1_cfg, protocol=protocol, hidden_dim=hidden_dim)
    runner2 = PipelineStageRunner(config=stage2_cfg, protocol=protocol, hidden_dim=hidden_dim)

    # Ingress token at Stage 0
    token_id = 42
    frame0_bytes = runner0.process_ingress(
        session_id="pipeline_session_001",
        token_index=0,
        token_id=token_id,
        tensor_dtype="float32",
    )
    assert len(frame0_bytes) > 0

    # Intermediate compute at Stage 1
    frame1_bytes = runner1.process_intermediate(frame_bytes=frame0_bytes, tensor_dtype="float32")
    assert len(frame1_bytes) > 0

    # Egress compute and token prediction at Stage 2
    result = runner2.process_egress(frame_bytes=frame1_bytes)
    assert result["session_id"] == "pipeline_session_001"
    assert result["token_index"] == 0
    assert "predicted_token_id" in result
    assert isinstance(result["predicted_token_id"], int)
    assert result["final_stage_index"] == 2
