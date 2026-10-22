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

@pytest.mark.anyio
async def test_multi_tier_ordering_and_cascading_fallback(monkeypatch):
    from unittest.mock import AsyncMock
    from app.services.inference import (
        get_ordered_backends,
        update_backend_tier_priority,
        send_chat_completion,
    )

    # 1. Register test backends in distinct tiers
    b_primary = register_inference_backend(
        name="Tier 1 Test Primary",
        backend_type="ollama",
        endpoint_url="http://127.0.0.1:11434",
        tier="tier_1_primary",
        priority=1,
    )
    update_backend_status(b_primary["id"], "ready", model_name="qwen2.5:3b")

    b_secondary = register_inference_backend(
        name="Tier 2 Test Secondary",
        backend_type="ollama",
        endpoint_url="http://100.101.187.115:11434",
        tier="tier_2_secondary",
        priority=2,
    )
    update_backend_status(b_secondary["id"], "ready", model_name="qwen2.5:1.5b")

    b_edge = register_inference_backend(
        name="Tier 3 Test Edge",
        backend_type="ollama",
        endpoint_url="http://100.111.178.12:11434",
        tier="tier_3_edge",
        priority=3,
    )
    update_backend_status(b_edge["id"], "ready", model_name="qwen2.5:0.5b")

    # Verify priority ordering
    ordered = get_ordered_backends()
    test_ids = [b["id"] for b in ordered if b["id"] in (b_primary["id"], b_secondary["id"], b_edge["id"])]
    assert test_ids == [b_primary["id"], b_secondary["id"], b_edge["id"]]

    # 2. Test Cascade: Primary fails -> Fallback to Secondary succeeds
    call_log = []

    class MockResponse:
        def __init__(self, status_code, json_data):
            self.status_code = status_code
            self._json = json_data
            self.text = str(json_data)
        def json(self):
            return self._json

    async def mock_post(self, url, *args, **kwargs):
        call_log.append(url)
        url_str = str(url)
        if "127.0.0.1:11434" in url_str:
            # Primary fails with 500 error
            return MockResponse(500, {"error": "GPU memory full"})
        elif "100.101.187.115" in url_str:
            # Secondary succeeds!
            return MockResponse(200, {"message": {"content": "Hello from Tier 2 Secondary"}})
        return MockResponse(200, {"message": {"content": "Hello from Tier 3 Edge"}})

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    result = await send_chat_completion([{"role": "user", "content": "hi"}])
    assert result["success"] is True
    assert result["backend"] == "Tier 2 Test Secondary"
    assert result["fallback_occurred"] is True
    assert result["content"] == "Hello from Tier 2 Secondary"
    assert len(result["attempts"]) == 1
    assert "GPU memory full" in result["attempts"][0]["error"]

    # 3. Test Double Cascade: Both Primary and Secondary fail -> Tier 3 Edge succeeds
    async def mock_post_double_fail(self, url, *args, **kwargs):
        url_str = str(url)
        if "127.0.0.1:11434" in url_str:
            raise httpx.ConnectError("Connection refused on 127.0.0.1")
        elif "100.101.187.115" in url_str:
            raise httpx.TimeoutException("Timeout on laptop")
        return MockResponse(200, {"message": {"content": "Hello from Pi-Brain Edge"}})

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_double_fail)

    res2 = await send_chat_completion([{"role": "user", "content": "hi"}])
    assert res2["success"] is True
    assert res2["backend"] == "Tier 3 Test Edge"
    assert res2["fallback_occurred"] is True
    assert res2["content"] == "Hello from Pi-Brain Edge"
    assert len(res2["attempts"]) == 2
    assert "Connection refused" in res2["attempts"][0]["error"]
    assert "Timed out" in res2["attempts"][1]["error"]

    # 4. Test strict tier targeting without fallback
    res3 = await send_chat_completion([{"role": "user", "content": "hi"}], target_tier="tier_1_primary", enable_fallback=False)
    assert res3["success"] is False
    assert "failed" in res3["error"]


