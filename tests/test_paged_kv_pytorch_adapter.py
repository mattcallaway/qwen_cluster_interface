import hashlib
import os
import tempfile
import pytest

try:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

from app.services.paged_kv.hasher import ExecutionNamespace, ModelIdentityManifest
from app.services.paged_kv.identity import ApprovedManifestRegistry, IdentityIssuanceAuthority
from app.services.paged_kv.manager import PagedKVManager
from app.services.paged_kv.pytorch_adapter import PyTorchPagedKVAdapter


@pytest.fixture
def adapter_environment(tmp_path):
    dummy_dir = tmp_path / "artifacts"
    dummy_dir.mkdir(parents=True, exist_ok=True)

    dummy_content = b"TEST_ADAPTER_ARTIFACT_DIGEST"
    paths = {
        "weights": dummy_dir / "weights.bin",
        "tokenizer": dummy_dir / "tok.json",
        "chat_template": dummy_dir / "tmpl.jinja",
        "rope_config": dummy_dir / "rope.json",
    }
    for p in paths.values():
        p.write_bytes(dummy_content)

    m_digest = hashlib.sha256(dummy_content).hexdigest()
    manifest = ModelIdentityManifest(
        model_name="Qwen2.5-Test-Adapter",
        weight_digest=m_digest,
        tokenizer_digest=m_digest,
        chat_template_digest=m_digest,
        kv_layout="bshd_fp16",
        rope_config_digest=m_digest,
        runtime_compatibility="qwen_collective_v2",
        tenant_id="tenant_primary",
    )

    registry = ApprovedManifestRegistry()
    registry.register_approved_manifest(manifest)
    issuer = IdentityIssuanceAuthority(registry=registry)
    authority = issuer.verify_and_issue(manifest, paths)

    auth_key = b"0123456789abcdef0123456789abcdef"
    manager = PagedKVManager(
        storage_dir=tmp_path / "store",
        auth_key=auth_key,
        max_ram_blocks=32,
        trusted_issuer=issuer,
    )
    return {
        "manifest": manifest,
        "issuer": issuer,
        "authority": authority,
        "manager": manager,
        "namespace": ExecutionNamespace.from_manifest(manifest),
    }


@pytest.mark.skipif(not HAS_TORCH, reason="PyTorch not installed in this environment")
def test_pytorch_adapter_serialization_and_deserialization(adapter_environment):
    manager = adapter_environment["manager"]
    adapter = PyTorchPagedKVAdapter(manager=manager, device="cpu")

    # Generate synthetic 2-layer KV tensors (16 tokens each)
    num_layers = 2
    keys = [torch.randn(1, 2, 16, 64, dtype=torch.float16) for _ in range(num_layers)]
    vals = [torch.randn(1, 2, 16, 64, dtype=torch.float16) for _ in range(num_layers)]

    serialized = adapter.serialize_page_tensors(keys, vals)
    assert isinstance(serialized, bytes)
    assert len(serialized) > 1000

    loaded_k, loaded_v = adapter.deserialize_page_tensors(serialized, device="cpu")
    assert len(loaded_k) == num_layers
    assert len(loaded_v) == num_layers
    for l in range(num_layers):
        assert torch.allclose(keys[l], loaded_k[l], atol=1e-5)
        assert torch.allclose(vals[l], loaded_v[l], atol=1e-5)


@pytest.mark.skipif(not HAS_TORCH, reason="PyTorch not installed in this environment")
def test_pytorch_adapter_cold_and_warm_prefill_context(adapter_environment):
    manager = adapter_environment["manager"]
    ns = adapter_environment["namespace"]
    authority = adapter_environment["authority"]
    adapter = PyTorchPagedKVAdapter(manager=manager, device="cpu")

    tokens_1 = list(range(100, 148))  # 48 tokens = 3 full 16-token pages
    tokens_2 = list(range(100, 132)) + list(range(200, 216))  # 32 tokens shared prefix, 16 novel

    # 1. Cold check
    ctx_cold = adapter.prepare_prefill_context("seq-cold", tokens_1, namespace=ns, authority=authority)
    assert ctx_cold["warm_cache_hit"] is False
    assert ctx_cold["matched_tokens"] == 0
    assert ctx_cold["input_ids"] == tokens_1

    # 2. Mock forward pass & caching
    mock_kv = tuple(
        (torch.randn(1, 2, 48, 64, dtype=torch.float16), torch.randn(1, 2, 48, 64, dtype=torch.float16))
        for _ in range(4)
    )
    cached_blocks = adapter.extract_and_cache_sequence_kv(
        "seq-cold",
        tokens_1,
        mock_kv,
        namespace=ns,
        authority=authority,
    )
    assert cached_blocks == 3

    # 3. Warm check on sequence 2
    ctx_warm = adapter.prepare_prefill_context("seq-warm", tokens_2, namespace=ns, authority=authority)
    assert ctx_warm["warm_cache_hit"] is True
    assert ctx_warm["matched_tokens"] == 32
    assert ctx_warm["input_ids"] == list(range(200, 216))
    assert ctx_warm["position_ids"] == list(range(32, 48))
    assert ctx_warm["past_key_values"] is not None
    assert ctx_warm["past_key_values"].get_seq_length() == 32

    # 4. Cross-tenant isolation check
    ns_diff_tenant = ExecutionNamespace(
        model_name=ns.model_name,
        model_revision=ns.model_revision,
        tokenizer_id=ns.tokenizer_id,
        chat_template_hash=ns.chat_template_hash,
        kv_layout=ns.kv_layout,
        rope_config_hash=ns.rope_config_hash,
        runtime_compatibility=ns.runtime_compatibility,
        tenant_id="tenant_ATTACKER",
        identity_scope="verified_runtime",
    )
    ctx_cross = adapter.prepare_prefill_context("seq-cross", tokens_2, namespace=ns_diff_tenant, authority=authority)
    assert ctx_cross["warm_cache_hit"] is False
    assert ctx_cross["matched_tokens"] == 0
