import hashlib
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.inference import (
    PagedKVCacheConfigurationError,
    get_paged_kv_manager,
    get_paged_kv_metrics,
    is_paged_kv_enabled,
    reset_paged_kv_state,
    send_chat_completion,
)
from app.services.paged_kv.hasher import (
    ExecutionNamespace,
    ModelIdentityManifest,
)
from app.services.paged_kv.identity import (
    ApprovedManifestRegistry,
    IdentityIssuanceAuthority,
    IssuedReuseAuthority,
)
from app.services.paged_kv.manager import PagedKVManager


@pytest.fixture
def clean_environment(monkeypatch):
    """Ensures no hidden cache keys or enablement flags leak into these tests."""
    monkeypatch.delenv("PAGED_KV_STORAGE_KEY", raising=False)
    monkeypatch.delenv("QC_PAGED_KV_STORAGE_KEY", raising=False)
    monkeypatch.delenv("QC_PAGED_KV_ENABLED", raising=False)
    monkeypatch.delenv("QC_PAGED_KV_ON_ERROR", raising=False)
    reset_paged_kv_state()
    yield
    reset_paged_kv_state()


@pytest.fixture
def synthetic_model_artifacts(tmp_path):
    """
    Creates real, independently authored synthetic artifact files on disk.
    Explicitly labeled as synthetic testing evidence, not real transformer weights.
    """
    model_dir = tmp_path / "synthetic_qwen_model"
    model_dir.mkdir(parents=True, exist_ok=True)

    weights_file = model_dir / "weights.bin"
    weights_content = b"SYNTHETIC_MODEL_WEIGHTS_TENSOR_FIXTURE_BYTES_ABC123"
    weights_file.write_bytes(weights_content)

    tok_file = model_dir / "tokenizer.json"
    tok_content = b'{"vocab": ["<pad>", "<s>", "</s>", "qwen"]}'
    tok_file.write_bytes(tok_content)

    tmpl_file = model_dir / "chat_template.jinja"
    tmpl_content = b"{% for m in messages %}{{ m.content }}{% endfor %}"
    tmpl_file.write_bytes(tmpl_content)

    rope_file = model_dir / "rope_config.json"
    rope_content = b'{"type": "linear", "factor": 1.0, "theta": 1000000.0}'
    rope_file.write_bytes(rope_content)

    manifest = ModelIdentityManifest(
        model_name="synthetic-qwen-test",
        weight_digest=hashlib.sha256(weights_content).hexdigest(),
        tokenizer_digest=hashlib.sha256(tok_content).hexdigest(),
        chat_template_digest=hashlib.sha256(tmpl_content).hexdigest(),
        kv_layout="bshd_fp16",
        rope_config_digest=hashlib.sha256(rope_content).hexdigest(),
        runtime_compatibility="qwen_collective_v2",
        tenant_id="tenant_secure",
    )

    artifact_paths = {
        "weights": weights_file,
        "tokenizer": tok_file,
        "chat_template": tmpl_file,
        "rope_config": rope_file,
    }

    return {
        "dir": model_dir,
        "manifest": manifest,
        "paths": artifact_paths,
        "weights_file": weights_file,
    }


# ===========================================================================
# 1. Identity Authority Tests
# ===========================================================================


def test_caller_selected_verified_runtime_label_cannot_authorize_reuse(tmp_path):
    """Direct construction with identity_scope='verified_runtime' must not grant reuse authority."""
    ns = ExecutionNamespace(
        model_name="fake_model",
        model_revision="fake_revision",
        tokenizer_id="fake_tok",
        chat_template_hash="fake_tmpl",
        kv_layout="bshd_fp16",
        rope_config_hash="fake_rope",
        identity_scope="verified_runtime",
    )
    # ExecutionNamespace cannot self-certify
    assert ns.is_verified_for_reuse is False

    mgr = PagedKVManager(storage_dir=tmp_path, auth_key=os.urandom(32))
    tokens = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]

    # Ingest without issued authority
    res = mgr.ingest_prompt("seq-1", tokens, namespace=ns, authority=None)
    assert res["verified_reuse_authorized"] is False

    # Second query: without authority, no blocks may be reused
    res2 = mgr.ingest_prompt("seq-2", tokens, namespace=ns, authority=None)
    assert res2["verified_reuse_authorized"] is False
    assert res2["reused_blocks_count"] == 0


def test_arbitrary_format_only_manifest_rejected_without_registry(synthetic_model_artifacts):
    """Self-authored or unapproved manifests must be rejected by the issuance authority."""
    registry = ApprovedManifestRegistry()
    authority_issuer = IdentityIssuanceAuthority(registry=registry)

    # Manifest is NOT registered in registry
    manifest = synthetic_model_artifacts["manifest"]
    paths = synthetic_model_artifacts["paths"]

    with pytest.raises(ValueError, match="not in the approved manifest registry"):
        authority_issuer.verify_and_issue(manifest, paths)


def test_tampered_artifact_fails_verification(synthetic_model_artifacts):
    """If an artifact file on disk differs from the approved manifest, issuance fails."""
    registry = ApprovedManifestRegistry()
    manifest = synthetic_model_artifacts["manifest"]
    registry.register_approved_manifest(manifest)

    authority_issuer = IdentityIssuanceAuthority(registry=registry)
    paths = dict(synthetic_model_artifacts["paths"])

    # Tamper with weights file on disk
    weights_file = synthetic_model_artifacts["weights_file"]
    weights_file.write_bytes(b"TAMPERED_WEIGHT_BYTES_DIFFERENT_HASH")

    with pytest.raises(ValueError, match="Cryptographic artifact digest mismatch for role 'weights'"):
        authority_issuer.verify_and_issue(manifest, paths)


def test_missing_or_empty_artifact_fails_verification(synthetic_model_artifacts, tmp_path):
    """Missing or empty files must fail verification immediately."""
    registry = ApprovedManifestRegistry()
    manifest = synthetic_model_artifacts["manifest"]
    registry.register_approved_manifest(manifest)
    authority_issuer = IdentityIssuanceAuthority(registry=registry)

    paths = dict(synthetic_model_artifacts["paths"])
    # Empty out tokenizer
    empty_tok = tmp_path / "empty_tok.json"
    empty_tok.write_bytes(b"")
    paths["tokenizer"] = empty_tok

    with pytest.raises(ValueError, match="Artifact for role 'tokenizer'.*is empty"):
        authority_issuer.verify_and_issue(manifest, paths)


def test_verified_synthetic_artifacts_issue_scoped_authority(synthetic_model_artifacts, tmp_path):
    """Valid approved manifest and matching disk artifacts issue authority enabling genuine reuse."""
    registry = ApprovedManifestRegistry()
    manifest = synthetic_model_artifacts["manifest"]
    registry.register_approved_manifest(manifest)
    authority_issuer = IdentityIssuanceAuthority(registry=registry)

    authority = authority_issuer.verify_and_issue(manifest, synthetic_model_artifacts["paths"])
    assert isinstance(authority, IssuedReuseAuthority)
    assert authority.is_active is True

    # Build namespace corresponding to manifest
    ns = ExecutionNamespace.from_manifest(manifest)
    assert authority.is_valid_for(ns) is True

    mgr = PagedKVManager(storage_dir=tmp_path, auth_key=os.urandom(32), trusted_issuer=authority_issuer)
    tokens = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25]

    res1 = mgr.ingest_prompt("seq-auth-1", tokens, namespace=ns, authority=authority)
    assert res1["verified_reuse_authorized"] is True

    # Second sequence with shared prefix can genuinely reuse blocks with valid authority
    res2 = mgr.ingest_prompt("seq-auth-2", tokens, namespace=ns, authority=authority)
    assert res2["verified_reuse_authorized"] is True
    assert res2["reused_blocks_count"] == 1


def test_authority_cannot_be_reused_for_incompatible_scope_or_runtime(synthetic_model_artifacts):
    """Authority bound to tenant A or layout X cannot authorize tenant B or layout Y."""
    registry = ApprovedManifestRegistry()
    manifest = synthetic_model_artifacts["manifest"]
    registry.register_approved_manifest(manifest)
    authority_issuer = IdentityIssuanceAuthority(registry=registry)

    authority = authority_issuer.verify_and_issue(manifest, synthetic_model_artifacts["paths"])

    # Attempt to use authority on mismatched tenant namespace
    mismatched_ns = ExecutionNamespace(
        model_name=manifest.model_name,
        model_revision=manifest.weight_digest,
        tokenizer_id=manifest.tokenizer_digest,
        chat_template_hash=manifest.chat_template_digest,
        kv_layout=manifest.kv_layout,
        rope_config_hash=manifest.rope_config_digest,
        runtime_compatibility=manifest.runtime_compatibility,
        tenant_id="tenant_DIFFERENT",
        identity_scope="verified_runtime",
    )
    assert authority.is_valid_for(mismatched_ns) is False


def test_toctou_artifact_tampering_invalidates_authority(synthetic_model_artifacts):
    """If an artifact file is modified after issuance, live verification invalidates authority."""
    registry = ApprovedManifestRegistry()
    manifest = synthetic_model_artifacts["manifest"]
    registry.register_approved_manifest(manifest)
    authority_issuer = IdentityIssuanceAuthority(registry=registry)

    authority = authority_issuer.verify_and_issue(manifest, synthetic_model_artifacts["paths"])
    ns = ExecutionNamespace.from_manifest(manifest)
    assert authority.is_valid_for(ns) is True

    # Modify file on disk after issuance
    synthetic_model_artifacts["weights_file"].write_bytes(b"POST_ISSUANCE_TAMPERED_WEIGHT_BYTES")

    # Re-check live artifacts -> must fail and invalidate authority
    assert authority.is_valid_for(ns) is False
    assert authority.is_active is False


def test_prototype_simulation_cannot_cross_into_genuine_reuse(tmp_path):
    """Prototype simulation does not grant verified_reuse_authorized even if blocks match."""
    mgr = PagedKVManager(storage_dir=tmp_path, auth_key=os.urandom(32))
    proto_ns = ExecutionNamespace.create_prototype_simulation("proto_model")
    tokens = [i for i in range(16)]

    res1 = mgr.ingest_prompt("p1", tokens, namespace=proto_ns)
    assert res1["verified_reuse_authorized"] is False

    res2 = mgr.ingest_prompt("p2", tokens, namespace=proto_ns)
    # Prototype prefix matched in Radix tree, but verified_reuse_authorized is strictly False
    assert res2["verified_reuse_authorized"] is False


def test_empty_or_incomplete_artifact_bindings_rejected():
    """IssuedReuseAuthority constructor rejects empty or incomplete artifact dictionaries fail-closed."""
    import time

    manifest = ModelIdentityManifest(
        model_name="qwen2.5:1.5b",
        weight_digest="11" * 32,
        tokenizer_digest="22" * 32,
        chat_template_digest="33" * 32,
        kv_layout="bshd_fp16",
        rope_config_digest="44" * 32,
        runtime_compatibility="qwen_collective_v2",
    )
    # Empty artifact mapping must raise ValueError
    with pytest.raises(ValueError, match="bound_artifacts must be a non-empty mapping"):
        IssuedReuseAuthority(
            authority_id="auth_empty",
            manifest=manifest,
            bound_artifacts={},
            issued_at=time.time(),
            issuer_token="token",
        )

    # Missing required role must raise ValueError
    from app.services.paged_kv.identity import BoundArtifactInfo
    incomplete_artifacts = {
        "weights": BoundArtifactInfo("weights", "weights.bin", 100, "11" * 32, time.time())
    }
    with pytest.raises(ValueError, match="bound_artifacts is missing required roles"):
        IssuedReuseAuthority(
            authority_id="auth_incomplete",
            manifest=manifest,
            bound_artifacts=incomplete_artifacts,
            issued_at=time.time(),
            issuer_token="token",
        )


def test_reproduce_untrusted_issuer_and_empty_artifacts_bypass(tmp_path, synthetic_model_artifacts):
    """
    Reproduction test for independent recheck bypass:
    An authority not issued or registered by the manager's trusted issuer must NOT be accepted,
    must report verified_reuse_authorized: False, and must NOT reuse cached prefix tokens.
    """
    import time
    from app.services.paged_kv.identity import BoundArtifactInfo

    manifest = synthetic_model_artifacts["manifest"]
    ns = ExecutionNamespace.from_manifest(manifest)

    # Fabricate an authority with simulated artifact records but bogus issuer secret
    synthetic_bound = {
        role: BoundArtifactInfo(role, str(path), path.stat().st_size, "11" * 32, path.stat().st_mtime)
        for role, path in synthetic_model_artifacts["paths"].items()
    }
    unregistered_authority = IssuedReuseAuthority(
        authority_id="unregistered_auth_123",
        manifest=manifest,
        bound_artifacts=synthetic_bound,
        issued_at=time.time(),
        issuer_token="unregistered_bogus_token",
    )

    # Manager configured with a trusted issuer that never issued unregistered_authority
    trusted_issuer = IdentityIssuanceAuthority(registry=ApprovedManifestRegistry())
    mgr = PagedKVManager(storage_dir=tmp_path, auth_key=b"k" * 32, trusted_issuer=trusted_issuer)

    # Request 1
    tokens = list(range(16))
    res1 = mgr.ingest_prompt("seq-1", tokens, namespace=ns, authority=unregistered_authority)
    assert res1["verified_reuse_authorized"] is False, (
        "Bypass prevented: unregistered authority denied verified_reuse_authorized"
    )

    # Request 2
    res2 = mgr.ingest_prompt("seq-2", tokens, namespace=ns, authority=unregistered_authority)
    assert res2["verified_reuse_authorized"] is False
    assert res2["matched_tokens"] == 0, (
        f"Bypass prevented: unregistered authority denied token reuse, got {res2['matched_tokens']}"
    )


def test_authority_from_different_issuer_rejected(synthetic_model_artifacts, tmp_path):
    """Authority issued by Issuer A must be rejected by a manager trusting Issuer B."""
    reg_a = ApprovedManifestRegistry()
    reg_b = ApprovedManifestRegistry()
    manifest = synthetic_model_artifacts["manifest"]
    reg_a.register_approved_manifest(manifest)
    reg_b.register_approved_manifest(manifest)

    issuer_a = IdentityIssuanceAuthority(registry=reg_a)
    issuer_b = IdentityIssuanceAuthority(registry=reg_b)

    auth_a = issuer_a.verify_and_issue(manifest, synthetic_model_artifacts["paths"])
    ns = ExecutionNamespace.from_manifest(manifest)

    # Manager trusts issuer_b only
    mgr = PagedKVManager(storage_dir=tmp_path, auth_key=b"k" * 32, trusted_issuer=issuer_b)

    res = mgr.ingest_prompt("seq-cross-issuer", list(range(16)), namespace=ns, authority=auth_a)
    assert res["verified_reuse_authorized"] is False
    assert res["matched_tokens"] == 0


def test_manager_without_trusted_issuer_denies_all_reuse(synthetic_model_artifacts, tmp_path):
    """A manager without a configured trusted_issuer strictly denies all genuine reuse."""
    registry = ApprovedManifestRegistry()
    manifest = synthetic_model_artifacts["manifest"]
    registry.register_approved_manifest(manifest)
    issuer = IdentityIssuanceAuthority(registry=registry)

    authority = issuer.verify_and_issue(manifest, synthetic_model_artifacts["paths"])
    ns = ExecutionNamespace.from_manifest(manifest)

    # Manager without trusted_issuer
    mgr = PagedKVManager(storage_dir=tmp_path, auth_key=b"k" * 32, trusted_issuer=None)

    res1 = mgr.ingest_prompt("seq-untrusted-mgr-1", list(range(16)), namespace=ns, authority=authority)
    assert res1["verified_reuse_authorized"] is False

    res2 = mgr.ingest_prompt("seq-untrusted-mgr-2", list(range(16)), namespace=ns, authority=authority)
    assert res2["verified_reuse_authorized"] is False
    assert res2["matched_tokens"] == 0


def test_revoked_authority_rejected_on_subsequent_reuse_and_restore(synthetic_model_artifacts, tmp_path):
    """Revoking an authority prevents subsequent reuse and blocks RAM restoration of the sequence."""
    registry = ApprovedManifestRegistry()
    manifest = synthetic_model_artifacts["manifest"]
    registry.register_approved_manifest(manifest)
    issuer = IdentityIssuanceAuthority(registry=registry)

    authority = issuer.verify_and_issue(manifest, synthetic_model_artifacts["paths"])
    ns = ExecutionNamespace.from_manifest(manifest)

    mgr = PagedKVManager(storage_dir=tmp_path, auth_key=b"k" * 32, trusted_issuer=issuer)
    tokens = list(range(16))

    # Request 1: authorized and successful
    res1 = mgr.ingest_prompt("seq-revokable-1", tokens, namespace=ns, authority=authority)
    assert res1["verified_reuse_authorized"] is True

    # Revoke authority in issuer
    revoked = issuer.revoke_authority(authority.authority_id)
    assert revoked is True
    assert authority.is_active is False

    # Request 2: reuse must now be rejected
    res2 = mgr.ingest_prompt("seq-revokable-2", tokens, namespace=ns, authority=authority)
    assert res2["verified_reuse_authorized"] is False
    assert res2["matched_tokens"] == 0

    # Evict RAM to disk
    mgr.store.evict_from_ram(mgr.table.get_block_hashes("seq-revokable-1")[0])

    # Attempting to restore seq-revokable-1 to RAM must fail closed due to revoked authority
    with pytest.raises(ValueError, match="reuse authority has been revoked or invalidated"):
        mgr.restore_sequence_to_ram("seq-revokable-1")


def test_concurrent_revocation_safety(synthetic_model_artifacts, tmp_path):
    """Concurrent ingestion and revocation do not cause race conditions or corrupt authority records."""
    registry = ApprovedManifestRegistry()
    manifest = synthetic_model_artifacts["manifest"]
    registry.register_approved_manifest(manifest)
    issuer = IdentityIssuanceAuthority(registry=registry)

    authority = issuer.verify_and_issue(manifest, synthetic_model_artifacts["paths"])
    ns = ExecutionNamespace.from_manifest(manifest)
    mgr = PagedKVManager(storage_dir=tmp_path, auth_key=b"k" * 32, trusted_issuer=issuer)

    errors = []

    def worker_ingest(seq_num):
        try:
            tokens = [seq_num * 100 + i for i in range(16)]
            mgr.ingest_prompt(f"concurrent-seq-{seq_num}", tokens, namespace=ns, authority=authority)
        except Exception as e:
            errors.append(e)

    def worker_revoke():
        time.sleep(0.01)
        issuer.revoke_authority(authority.authority_id)

    threads = [threading.Thread(target=worker_ingest, args=(i,)) for i in range(10)]
    revoke_thread = threading.Thread(target=worker_revoke)

    for t in threads:
        t.start()
    revoke_thread.start()

    for t in threads:
        t.join()
    revoke_thread.join()

    assert len(errors) == 0
    assert authority.is_active is False




# ===========================================================================
# 2. Application Behavior & Controlled Cache Modes Tests
# ===========================================================================


@pytest.mark.anyio
async def test_cache_disabled_mode_inference_succeeds_without_key(clean_environment, monkeypatch):
    """
    When QC_PAGED_KV_ENABLED is false and no key is configured,
    ordinary inference succeeds through backend and touches no persistent cache.
    """
    monkeypatch.setenv("QC_PAGED_KV_ENABLED", "false")
    assert is_paged_kv_enabled() is False

    # Mock backend response
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "message": {"role": "assistant", "content": "Clean inference output without cache."}
        }
        mock_post.return_value = mock_resp

        with patch("app.services.inference.get_ordered_backends") as mock_backends:
            mock_backends.return_value = [{
                "id": "mock_b1",
                "name": "Mock Ollama",
                "backend_type": "ollama",
                "endpoint_url": "http://127.0.0.1:11434",
                "model_name": "qwen2.5:3b",
                "tier": "tier_1_primary",
                "priority": 1,
            }]

            res = await send_chat_completion([{"role": "user", "content": "Hello"}])
            assert res["success"] is True
            assert res["content"] == "Clean inference output without cache."
            assert res["paged_kv"]["enabled"] is False
            assert res["paged_kv"]["status"] == "disabled"


@pytest.mark.anyio
async def test_cache_enabled_without_key_fail_closed_policy(clean_environment, monkeypatch):
    """
    When QC_PAGED_KV_ENABLED is true but no key is configured and policy is fail_closed (default),
    it returns a structured error response without crashing or leaking credentials.
    """
    monkeypatch.setenv("QC_PAGED_KV_ENABLED", "true")
    monkeypatch.setenv("QC_PAGED_KV_ON_ERROR", "fail_closed")

    with patch("app.services.inference.get_ordered_backends") as mock_backends:
        mock_backends.return_value = [{
            "id": "mock_b1",
            "name": "Mock Ollama",
            "backend_type": "ollama",
            "endpoint_url": "http://127.0.0.1:11434",
            "model_name": "qwen2.5:3b",
            "tier": "tier_1_primary",
        }]

        res = await send_chat_completion([{"role": "user", "content": "Hello"}])
        assert res["success"] is False
        assert res["error_code"] == "paged_kv_configuration_error"
        assert "storage key configuration is invalid or missing" in res["error"]


@pytest.mark.anyio
async def test_cache_enabled_without_key_bypass_policy(clean_environment, monkeypatch):
    """
    When QC_PAGED_KV_ENABLED is true and key is absent but policy is bypass,
    ordinary inference runs and reports bypassed_unavailable.
    """
    monkeypatch.setenv("QC_PAGED_KV_ENABLED", "true")
    monkeypatch.setenv("QC_PAGED_KV_ON_ERROR", "bypass")

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "message": {"role": "assistant", "content": "Bypassed cache output."}
        }
        mock_post.return_value = mock_resp

        with patch("app.services.inference.get_ordered_backends") as mock_backends:
            mock_backends.return_value = [{
                "id": "mock_b1",
                "name": "Mock Ollama",
                "backend_type": "ollama",
                "endpoint_url": "http://127.0.0.1:11434",
                "model_name": "qwen2.5:3b",
                "tier": "tier_1_primary",
            }]

            res = await send_chat_completion([{"role": "user", "content": "Hello"}])
            assert res["success"] is True
            assert res["content"] == "Bypassed cache output."
            assert res["paged_kv"]["status"] == "bypassed_unavailable"


def test_metrics_reporting_under_disabled_and_unavailable_modes(clean_environment, monkeypatch):
    """Health/observatory metrics requests do not fail or initialize storage unexpectedly."""
    # 1. Disabled mode
    monkeypatch.setenv("QC_PAGED_KV_ENABLED", "false")
    reset_paged_kv_state()
    m1 = get_paged_kv_metrics()
    assert m1["enabled"] is False
    assert m1["status"] == "disabled"

    # 2. Enabled without key
    monkeypatch.setenv("QC_PAGED_KV_ENABLED", "true")
    monkeypatch.delenv("PAGED_KV_STORAGE_KEY", raising=False)
    reset_paged_kv_state()
    m2 = get_paged_kv_metrics()
    assert m2["enabled"] is True
    assert m2["status"] in ("unavailable", "bypassed_unavailable")


def test_recovery_after_configuration_correction(clean_environment, monkeypatch, tmp_path):
    """Failed initialization recovers cleanly after valid configuration is supplied."""
    monkeypatch.setenv("QC_PAGED_KV_ENABLED", "true")
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)

    # First attempt: key is missing -> raises configuration error
    with pytest.raises(PagedKVCacheConfigurationError):
        get_paged_kv_manager()

    # Correct configuration by supplying valid 256-bit key
    valid_key = os.urandom(32).hex()
    monkeypatch.setenv("PAGED_KV_STORAGE_KEY", valid_key)
    reset_paged_kv_state()

    # Second attempt: recovers cleanly
    mgr = get_paged_kv_manager()
    assert mgr is not None
    metrics = get_paged_kv_metrics()
    assert metrics["status"] == "active"


def test_concurrent_initialization_safety(clean_environment, monkeypatch, tmp_path):
    """Concurrent requests do not race or create conflicting singleton instances."""
    monkeypatch.setenv("QC_PAGED_KV_ENABLED", "true")
    valid_key = os.urandom(32).hex()
    monkeypatch.setenv("PAGED_KV_STORAGE_KEY", valid_key)
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    reset_paged_kv_state()

    managers = []
    errors = []

    def worker():
        try:
            m = get_paged_kv_manager()
            managers.append(m)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(m is managers[0] for m in managers)


def test_clean_environment_disabled_by_default_settings(clean_environment):
    """
    Verifies Section 4 requirement: Caching is strictly disabled by default without hidden configuration.
    No automatic fixture injects keys or enabled flags.
    """
    assert is_paged_kv_enabled() is False
    assert get_paged_kv_manager() is None
    metrics = get_paged_kv_metrics()
    assert metrics["status"] == "disabled"
    assert metrics["enabled"] is False


def test_disabled_cache_startup_does_not_initialize_persistent_storage(clean_environment, tmp_path, monkeypatch):
    """
    Verifies Section 4 requirement: Disabled-cache startup does not initialize persistent cache storage.
    """
    cache_dir = tmp_path / "paged_kv_cache"
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    
    # Under disabled mode, manager is None and storage directory is not created
    mgr = get_paged_kv_manager()
    assert mgr is None
    assert not cache_dir.exists()


def test_invalid_authority_never_verified_via_fallback_paths(tmp_path, synthetic_model_artifacts):
    """
    Verifies Section 4 requirement: Invalid authorities never become verified through fallback paths.
    """
    manifest = synthetic_model_artifacts["manifest"]
    ns = ExecutionNamespace.from_manifest(manifest)
    trusted_issuer = IdentityIssuanceAuthority(registry=ApprovedManifestRegistry())
    mgr = PagedKVManager(storage_dir=tmp_path, auth_key=b"k" * 32, trusted_issuer=trusted_issuer)

    for bogus_auth in [None, "string_authority", 12345, object(), {}]:
        res = mgr.ingest_prompt("seq-bogus", list(range(16)), namespace=ns, authority=bogus_auth)
        assert res["verified_reuse_authorized"] is False
        assert res["matched_tokens"] == 0

