import tempfile
from pathlib import Path
import pytest
from app.services.paged_kv.manager import PagedKVManager


def test_reproduce_cross_model_prefix_collision():
    """
    Reproduction test for Material Finding 4 / probe cross_model_prefix:
    Identical tokens ingested for Model A then Model B must NOT yield a prefix hit for Model B.
    Reusable pages must be bound to immutable execution namespaces.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = PagedKVManager(storage_dir=Path(tmpdir), max_ram_blocks=16, block_size=16)

        # 16 identical tokens
        common_tokens = list(range(16))

        # Ingest for Model A
        res_a = mgr.ingest_prompt(
            sequence_id="seq-model-a",
            tokens=common_tokens,
            model_name="qwen2.5:1.5b"
        )
        assert res_a["matched_tokens"] == 0, "First ingestion should have 0 matched tokens"

        # Ingest identical tokens for Model B (different model architecture/namespace)
        # Expected: 0 matched tokens because Model B must not reuse Model A's KV cache.
        # Broken behavior in prototype: matches all 16 tokens across models!
        res_b = mgr.ingest_prompt(
            sequence_id="seq-model-b",
            tokens=common_tokens,
            model_name="llama3.2:1b"
        )
        assert res_b["matched_tokens"] == 0, (
            f"Cross-model cache pollution detected: Model B matched {res_b['matched_tokens']} tokens from Model A"
        )


def test_compatible_namespace_prefix_reuse():
    """
    Identical tokens within the SAME model namespace MUST hit and reuse cached pages.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = PagedKVManager(storage_dir=Path(tmpdir), max_ram_blocks=16, block_size=16)

        common_tokens = list(range(32))

        # First ingestion
        res_1 = mgr.ingest_prompt("seq-1", common_tokens, model_name="qwen2.5:1.5b")
        assert res_1["matched_tokens"] == 0

        # Second ingestion with same model namespace and longer prompt sharing prefix
        full_tokens = common_tokens + [100, 101, 102]
        res_2 = mgr.ingest_prompt("seq-2", full_tokens, model_name="qwen2.5:1.5b")
        assert res_2["matched_tokens"] == 32
        assert res_2["reused_blocks_count"] == 2


def test_reproduce_ambiguous_namespace_delimiter_collision():
    """
    Reproduction test for ambiguous namespace encoding:
    Namespace A: model_name="a:b", model_revision="c"
    Namespace B: model_name="a", model_revision="b:c"
    These are distinct namespaces with different field values.
    They must NOT produce the same digest!
    """
    from app.services.paged_kv.hasher import ExecutionNamespace

    ns_a = ExecutionNamespace(model_name="a:b", model_revision="c")
    ns_b = ExecutionNamespace(model_name="a", model_revision="b:c")
    assert ns_a != ns_b
    digest_a = ns_a.compute_digest()
    digest_b = ns_b.compute_digest()
    assert digest_a != digest_b, (
        f"Ambiguous delimiter collision: Namespace A {ns_a} and Namespace B {ns_b} "
        f"both produced identical digest {digest_a}"
    )


def test_delimiter_containing_values_do_not_collide():
    """
    Ensure values with commas, quotes, braces, and colons do not create canonical collisions.
    """
    from app.services.paged_kv.hasher import ExecutionNamespace

    ns_1 = ExecutionNamespace(model_name='mod{"k":"v"}', model_revision="rev,1")
    ns_2 = ExecutionNamespace(model_name="mod", model_revision='{"k":"v"},rev,1')
    assert ns_1.compute_digest() != ns_2.compute_digest()


def test_empty_or_whitespace_fields_rejected():
    """ExecutionNamespace must reject empty or blank strings for any required identity."""
    from app.services.paged_kv.hasher import ExecutionNamespace

    with pytest.raises(ValueError, match="cannot be empty or whitespace only"):
        ExecutionNamespace(model_name="", model_revision="rev1")

    with pytest.raises(ValueError, match="cannot be empty or whitespace only"):
        ExecutionNamespace(model_name="m1", model_revision="   ")


def test_wrong_types_rejected():
    """ExecutionNamespace must enforce string types."""
    from app.services.paged_kv.hasher import ExecutionNamespace

    with pytest.raises(TypeError, match="must be a non-null string"):
        ExecutionNamespace(model_name=123, model_revision="rev1")

    with pytest.raises(TypeError, match="must be a non-null string"):
        ExecutionNamespace(model_name="m1", model_revision=None)


def test_placeholder_default_rejected():
    """Placeholder string 'default' must be rejected for execution identities."""
    from app.services.paged_kv.hasher import ExecutionNamespace

    with pytest.raises(ValueError, match="cannot be generic placeholder 'default'"):
        ExecutionNamespace(model_name="m1", model_revision="default")

    with pytest.raises(ValueError, match="cannot be generic placeholder 'default'"):
        ExecutionNamespace(model_name="m1", model_revision="rev1", tokenizer_id="DEFAULT")


def test_oversized_values_rejected():
    """Field values exceeding maximum length must be rejected fail-closed."""
    from app.services.paged_kv.hasher import ExecutionNamespace

    oversized = "x" * 300
    with pytest.raises(ValueError, match="exceeds maximum allowed length"):
        ExecutionNamespace(model_name=oversized, model_revision="rev1")


def test_unicode_values_in_namespace():
    """Unicode characters in model names or tenant IDs must be handled cleanly."""
    from app.services.paged_kv.hasher import ExecutionNamespace

    ns = ExecutionNamespace(
        model_name="通义千问:2.5",
        model_revision="リビジョン_1",
        tenant_id="テナント_アルファ",
    )
    digest = ns.compute_digest()
    assert isinstance(digest, str)
    assert len(digest) == 64  # Full SHA-256 hex string


def test_each_identity_field_changes_digest_individually():
    """Changing any single identity field must alter the resulting namespace digest."""
    from app.services.paged_kv.hasher import ExecutionNamespace

    base = ExecutionNamespace(
        model_name="qwen-base",
        model_revision="rev-001",
        tokenizer_id="tok-001",
        chat_template_hash="tmpl-001",
        kv_layout="bshd_fp16",
        rope_config_hash="rope-001",
        tenant_id="tenant-001",
    )
    base_digest = base.compute_digest()

    variants = [
        ExecutionNamespace("qwen-base-mod", "rev-001", "tok-001", "tmpl-001", "bshd_fp16", "rope-001", "tenant-001"),
        ExecutionNamespace("qwen-base", "rev-002", "tok-001", "tmpl-001", "bshd_fp16", "rope-001", "tenant-001"),
        ExecutionNamespace("qwen-base", "rev-001", "tok-002", "tmpl-001", "bshd_fp16", "rope-001", "tenant-001"),
        ExecutionNamespace("qwen-base", "rev-001", "tok-001", "tmpl-002", "bshd_fp16", "rope-001", "tenant-001"),
        ExecutionNamespace("qwen-base", "rev-001", "tok-001", "tmpl-001", "sbhd_fp16", "rope-001", "tenant-001"),
        ExecutionNamespace("qwen-base", "rev-001", "tok-001", "tmpl-001", "bshd_fp16", "rope-002", "tenant-001"),
        ExecutionNamespace("qwen-base", "rev-001", "tok-001", "tmpl-001", "bshd_fp16", "rope-001", "tenant-002"),
    ]

    seen = {base_digest}
    for v in variants:
        d = v.compute_digest()
        assert d != base_digest
        assert d not in seen
        seen.add(d)


def test_manager_lifecycle_and_restoration_isolation():
    """
    Verify isolation across manager ingestion, lookup, disk persistence, and restoration.
    """
    from app.services.paged_kv.hasher import ExecutionNamespace

    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = PagedKVManager(storage_dir=Path(tmpdir), max_ram_blocks=4, block_size=16)

        ns_a = ExecutionNamespace.create_canonical("qwen2.5:1.5b", model_revision="rev_a")
        ns_b = ExecutionNamespace.create_canonical("qwen2.5:1.5b", model_revision="rev_b")

        tokens = list(range(32))

        # Ingest sequence under ns_a
        mgr.ingest_prompt("seq-a", tokens, namespace=ns_a)

        # Lookup same tokens under ns_b must NOT hit
        res_b = mgr.ingest_prompt("seq-b", tokens, namespace=ns_b)
        assert res_b["matched_tokens"] == 0

        # Evict everything from RAM to disk
        mgr._enforce_ram_quota()

        # Restore seq-a must succeed verifying ns_a
        restored = mgr.restore_sequence_to_ram("seq-a")
        assert restored >= 0


