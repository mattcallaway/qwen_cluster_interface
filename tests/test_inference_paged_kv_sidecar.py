import pytest
from unittest.mock import AsyncMock, patch
import httpx

from app.services.inference import (
    encode_text_to_tokens,
    get_paged_kv_manager,
    get_paged_kv_metrics,
    send_chat_completion,
    register_inference_backend,
    update_backend_status,
)
from app.database import get_db_connection, create_baseline_schema
from app.migrations import apply_migrations, MIGRATIONS


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    # Isolated test database and kv cache directory
    test_db = tmp_path / "test_inference.db"
    test_kv = tmp_path / "kv_cache"
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)

    conn = get_db_connection()
    create_baseline_schema(conn)
    apply_migrations(conn, MIGRATIONS)
    conn.close()

    # Reset paged_kv singleton
    import app.services.inference as inf_mod
    inf_mod._paged_kv_manager = None


def test_encode_text_to_tokens():
    text = "Hello, Qwen Collective!"
    tokens = encode_text_to_tokens(text)
    assert len(tokens) == len(text.encode("utf-8"))
    assert all(isinstance(t, int) and 0 <= t <= 255 for t in tokens)

    # Identical string yields identical tokens
    assert encode_text_to_tokens(text) == tokens


def test_paged_kv_manager_initialization_and_metrics(monkeypatch):
    from app.services.inference import reset_paged_kv_state
    monkeypatch.setenv("QC_PAGED_KV_ENABLED", "true")
    monkeypatch.setenv("PAGED_KV_STORAGE_KEY", "b" * 64)
    reset_paged_kv_state()
    mgr = get_paged_kv_manager()
    assert mgr is not None
    metrics = get_paged_kv_metrics()
    assert "total_queries" in metrics
    assert "prefix_hit_rate_percent" in metrics
    assert "current_ram_pages" in metrics


@pytest.mark.anyio
async def test_send_chat_completion_with_paged_kv_sidecar_hit(monkeypatch):
    from app.services.inference import reset_paged_kv_state
    monkeypatch.setenv("QC_PAGED_KV_ENABLED", "true")
    monkeypatch.setenv("PAGED_KV_STORAGE_KEY", "c" * 64)
    reset_paged_kv_state()
    # Register mock backend
    b = register_inference_backend(
        name="Mock Ollama Engine",
        backend_type="ollama",
        endpoint_url="http://127.0.0.1:11434",
        context_window=4096,
        tier="tier_1_primary",
        priority=1,
    )
    update_backend_status(b["id"], "ready", model_name="qwen2.5:1.5b")

    mock_resp_1 = {
        "message": {"role": "assistant", "content": "I am Qwen Collective assistant."},
    }
    mock_resp_2 = {
        "message": {"role": "assistant", "content": "The cluster status is healthy."},
    }

    system_prompt = "You are Qwen Collective, a sovereign distributed AI cluster."
    query_1_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Who are you?"},
    ]
    query_2_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "What is the cluster status?"},
    ]

    with patch("httpx.AsyncClient.post") as mock_post:
        # First call: cold prompt
        mock_post.return_value = httpx.Response(
            status_code=200,
            json=mock_resp_1,
            request=httpx.Request("POST", "http://127.0.0.1:11434/api/chat"),
        )
        res1 = await send_chat_completion(query_1_messages)
        assert res1["success"]
        assert "paged_kv" in res1
        kv1 = res1["paged_kv"]
        assert kv1["matched_tokens"] == 0
        assert kv1["novel_tokens"] > 0
        assert kv1["prefill_saved_percent"] == 0.0

        # Second call: shares identical system_prompt prefix
        mock_post.return_value = httpx.Response(
            status_code=200,
            json=mock_resp_2,
            request=httpx.Request("POST", "http://127.0.0.1:11434/api/chat"),
        )
        res2 = await send_chat_completion(query_2_messages)
        assert res2["success"]
        assert "paged_kv" in res2
        kv2 = res2["paged_kv"]

        # Second call MUST hit cached prefix blocks from system_prompt!
        assert kv2["matched_tokens"] > 0
        assert kv2["reused_blocks_count"] > 0
        assert kv2["prefill_saved_percent"] > 0.0
        assert kv2["total_sequence_blocks"] >= kv2["reused_blocks_count"]
