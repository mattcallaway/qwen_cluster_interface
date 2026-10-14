import pytest
from app.services.inference import (
    register_inference_backend,
    get_all_backends,
    update_backend_status,
    create_distributed_group,
    get_distributed_group_by_id,
    generate_distributed_launch_command,
    add_benchmark_result,
    get_benchmarks
)

def test_inference_backend_registration():
    b = register_inference_backend(
        name="Local llama-server",
        backend_type="llama_cpp",
        endpoint_url="http://192.168.1.50:8080"
    )
    assert b["id"].startswith("inf-")
    
    backends = get_all_backends()
    found = next((x for x in backends if x["id"] == b["id"]), None)
    assert found is not None
    assert found["status"] == "untested"
    
    update_backend_status(b["id"], "ready", model_name="Qwen2.5-3B-Instruct")
    backends_after = get_all_backends()
    updated = next((x for x in backends_after if x["id"] == b["id"]), None)
    assert updated["status"] == "ready"
    assert updated["model_name"] == "Qwen2.5-3B-Instruct"

def test_distributed_group_and_command_generation():
    group_id = create_distributed_group(
        name="Dual-Laptop Cluster",
        coordinator_machine_id="node-coord",
        model_file="models/qwen2.5-7b-instruct-q4_k_m.gguf",
        rpc_endpoints=["192.168.1.102:50052", "192.168.1.103:50052"],
        context_window=4096,
        runtime_parameters="-ngl 0"
    )
    assert group_id.startswith("dist-")
    
    group = get_distributed_group_by_id(group_id)
    assert group is not None
    assert len(group["rpc_endpoints"]) == 2
    
    cmd = generate_distributed_launch_command(group)
    assert "./build/bin/llama-server" in cmd
    assert "-m models/qwen2.5-7b-instruct-q4_k_m.gguf" in cmd
    assert "--rpc 192.168.1.102:50052,192.168.1.103:50052" in cmd
    assert "-ngl 0" in cmd

def test_benchmark_recorder_and_durability():
    add_benchmark_result(
        group_or_backend_name="Dual-Laptop Cluster",
        model_name="Qwen2.5-7B-Instruct",
        quantization="Q4_K_M",
        context_length=4096,
        machines_involved="2x ThinkPad T430",
        prompt_tokens_sec=16.8,
        gen_tokens_sec=4.1,
        memory_usage_mb=3250.0,
        notes="Measured on release build 2026-10-14"
    )
    
    benchmarks = get_benchmarks()
    assert len(benchmarks) >= 1
    recent = benchmarks[0]
    assert recent["model_name"] == "Qwen2.5-7B-Instruct"
    assert recent["gen_tokens_sec"] == 4.1
    assert recent["prompt_tokens_sec"] == 16.8
