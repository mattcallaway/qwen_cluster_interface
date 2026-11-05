"""
Real-Artifact Runtime Identity & Authority Binding Verification Suite (Increment 17 - Section 2).

Tests the complete trusted local model loading and KV reuse authorization chain:
    TrustedLocalModelLoader -> IdentityIssuanceAuthority -> PagedKVManager -> PyTorchPagedKVAdapter

Demonstrates all 7 mandatory requirements against actual pinned Qwen/Qwen2.5-0.5B-Instruct artifacts:
1. Actual pinned local artifacts load and obtain narrowly scoped authority.
2. The authority reaches the real KV adapter through the normal trusted path.
3. Different weights under the same display name cannot reuse that authority.
4. Changed tokenizer, template, positional configuration, dtype/layout, runtime, tenant or model-instance binding is rejected.
5. Missing, altered or substituted artifacts (including mid-load TOCTOU replacement) fail verification.
6. Synthetic-fixture authority cannot authorize the separately loaded real model.
7. Revoked authority cannot authorize subsequent RAM or disk reuse.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Dict

import pytest
import torch
from transformers import AutoModelForCausalLM

from app.services.paged_kv.hasher import ExecutionNamespace, ModelIdentityManifest
from app.services.paged_kv.identity import (
    ApprovedManifestRegistry,
    IdentityIssuanceAuthority,
    stream_file_sha256,
)
from app.services.paged_kv.manager import PagedKVManager
from app.services.paged_kv.model_loader import (
    TrustedLocalModelLoader,
    compute_live_model_fingerprint,
    verify_live_model_instance,
)
from app.services.paged_kv.pytorch_adapter import PyTorchPagedKVAdapter

MODEL_ID = "Qwen/Qwen2.5-0.5B-Instruct"
PINNED_REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"


def _resolve_local_model_dir() -> Path:
    env_dir = os.environ.get("QWEN_LOCAL_MODEL_DIR")
    if env_dir and Path(env_dir).exists():
        return Path(env_dir).resolve()
    local_candidate = Path(__file__).resolve().parent.parent / "scratch" / "models" / "qwen2.5-0.5b-instruct"
    if local_candidate.exists():
        return local_candidate
    sibling_candidate = (
        Path(__file__).resolve().parent.parent.parent
        / "qwen-collective-integration"
        / "scratch"
        / "models"
        / "qwen2.5-0.5b-instruct"
    )
    if sibling_candidate.exists():
        return sibling_candidate
    return local_candidate


LOCAL_MODEL_DIR = _resolve_local_model_dir()
STORE_KEY = b"qc_real_loader_auth_store_key32b"

EXPECTED_WEIGHT_SHA256 = "fdf756fa7fcbe7404d5c60e26bff1a0c8b8aa1f72ced49e7dd0210fe288fb7fe"
EXPECTED_TOKENIZER_SHA256 = "c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539"
EXPECTED_CHAT_TEMPLATE_SHA256 = "5b5d4f65d0acd3b2d56a35b56d374a36cbc1c8fa5cf3b3febbbfabf22f359583"
EXPECTED_ROPE_CONFIG_SHA256 = "18e18afcaccafade98daf13a54092927904649e1dd4eba8299ab717d5d94ff45"


def _make_approved_manifest(
    tenant_id: str = "tenant_verified_prod",
    kv_layout: str = "bshd_fp16",
    runtime_compatibility: str = "pytorch_cu121_v1",
) -> ModelIdentityManifest:
    return ModelIdentityManifest(
        model_name=MODEL_ID,
        weight_digest=EXPECTED_WEIGHT_SHA256,
        tokenizer_digest=EXPECTED_TOKENIZER_SHA256,
        chat_template_digest=EXPECTED_CHAT_TEMPLATE_SHA256,
        kv_layout=kv_layout,
        rope_config_digest=EXPECTED_ROPE_CONFIG_SHA256,
        runtime_compatibility=runtime_compatibility,
        tenant_id=tenant_id,
    )


@pytest.fixture(scope="module")
def real_verified_bundle():
    """Loads the real pinned Qwen/Qwen2.5-0.5B-Instruct snapshot once via TrustedLocalModelLoader."""
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    manifest = _make_approved_manifest()
    registry = ApprovedManifestRegistry()
    registry.register_approved_manifest(manifest)
    issuer = IdentityIssuanceAuthority(registry=registry)

    bundle = TrustedLocalModelLoader.load_verified_model(
        cache_or_snapshot_dir=LOCAL_MODEL_DIR,
        model_name=MODEL_ID,
        snapshot_revision=PINNED_REVISION,
        manifest=manifest,
        issuer=issuer,
        device=device,
        torch_dtype=torch.float16,
    )
    return {
        "device": device,
        "manifest": manifest,
        "registry": registry,
        "issuer": issuer,
        "bundle": bundle,
    }


def _link_or_copy_file(src: Path, dst: Path) -> None:
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def test_1_pinned_local_artifacts_load_and_obtain_narrowly_scoped_authority(real_verified_bundle):
    """
    Requirement 1: Actual pinned local artifacts load and obtain narrowly scoped authority.
    Verifies streaming SHA-256 digests of the real 988 MB safetensors weight file, tokenizer,
    chat template, and config, plus instance binding attributes on the issued authority.
    """
    bundle = real_verified_bundle["bundle"]
    issuer = real_verified_bundle["issuer"]
    authority = bundle.authority

    # Verify inspected manifest matches the pinned hashes on disk
    inspected_manifest, paths = TrustedLocalModelLoader.inspect_local_snapshot_manifest(
        cache_or_snapshot_dir=LOCAL_MODEL_DIR,
        model_name=MODEL_ID,
        snapshot_revision=PINNED_REVISION,
        kv_layout="bshd_fp16",
        runtime_compatibility="pytorch_cu121_v1",
        tenant_id="tenant_verified_prod",
    )
    assert inspected_manifest == bundle.manifest
    assert paths["weights"].stat().st_size == 988_097_824

    # Verify authority is narrowly scoped to the loaded instance and real files
    assert authority.is_active is True
    assert authority.require_instance_binding is True
    assert authority.model_instance_id == bundle.model_instance_id
    assert authority.model_instance_id.startswith(f"inst:{MODEL_ID}:{PINNED_REVISION[:12]}:")
    assert authority.bound_artifacts["weights"].sha256 == EXPECTED_WEIGHT_SHA256
    assert authority.bound_artifacts[" weights".strip()].file_size == 988_097_824
    assert authority.bound_artifacts["tokenizer"].sha256 == EXPECTED_TOKENIZER_SHA256
    assert authority.bound_artifacts["chat_template"].sha256 == EXPECTED_CHAT_TEMPLATE_SHA256
    assert authority.bound_artifacts["rope_config"].sha256 == EXPECTED_ROPE_CONFIG_SHA256

    # Rejects validation if model_instance_id is omitted or mismatched
    assert issuer.validate_authority_for_reuse(authority, bundle.namespace, model_instance_id=None) is False
    assert (
        issuer.validate_authority_for_reuse(
            authority, bundle.namespace, model_instance_id="inst:wrong_instance"
        )
        is False
    )
    assert (
        issuer.validate_authority_for_reuse(
            authority, bundle.namespace, model_instance_id=bundle.model_instance_id
        )
        is True
    )


def test_2_authority_reaches_real_kv_adapter_through_trusted_path(tmp_path, real_verified_bundle):
    """
    Requirement 2: The authority reaches the real KV adapter through the normal trusted path
    (loader -> issuer -> manager -> adapter) and achieves genuine warm KV reuse with numerical parity.
    """
    bundle = real_verified_bundle["bundle"]
    issuer = real_verified_bundle["issuer"]
    device = real_verified_bundle["device"]
    model = bundle.model

    manager = PagedKVManager(
        storage_dir=tmp_path / "trusted_path_store",
        max_ram_blocks=32,
        auth_key=STORE_KEY,
        trusted_issuer=issuer,
    )
    adapter = PyTorchPagedKVAdapter(manager=manager, device=device, model=model)

    prefix_32 = [151644, 8948, 198, 2610, 525, 264, 10950, 17847, 13, 151645, 198, 151644, 872, 198, 3838, 374] * 2
    tokens_cold = prefix_32 + [1100, 1200, 1300, 1400, 1500, 1600, 1700, 1800]
    tokens_warm = prefix_32 + [2100, 2200, 2300, 2400, 2500, 2600, 2700, 2800]

    cold_ctx = adapter.prepare_prefill_context(
        "seq-cold", tokens_cold, namespace=bundle.namespace, authority=bundle.authority
    )
    assert cold_ctx["warm_cache_hit"] is False

    with torch.no_grad():
        cold_out = model(torch.tensor([tokens_cold], dtype=torch.long, device=device), use_cache=True)
        ref_warm_out = model(torch.tensor([tokens_warm], dtype=torch.long, device=device), use_cache=True)
        ref_logits = ref_warm_out.logits[:, -1, :].detach().cpu()

    cached_blocks = adapter.extract_and_cache_sequence_kv(
        "seq-cold",
        tokens_cold,
        cold_out.past_key_values,
        namespace=bundle.namespace,
        authority=bundle.authority,
    )
    assert cached_blocks == 2

    warm_ctx = adapter.prepare_prefill_context(
        "seq-warm", tokens_warm, namespace=bundle.namespace, authority=bundle.authority
    )
    assert warm_ctx["warm_cache_hit"] is True
    assert warm_ctx["matched_tokens"] == 32
    assert warm_ctx["input_ids"] == tokens_warm[32:]

    with torch.no_grad():
        warm_out = model(
            input_ids=torch.tensor([warm_ctx["input_ids"]], dtype=torch.long, device=device),
            position_ids=torch.tensor([warm_ctx["position_ids"]], dtype=torch.long, device=device),
            past_key_values=warm_ctx["past_key_values"],
            use_cache=True,
        )
        warm_logits = warm_out.logits[:, -1, :].detach().cpu()

    max_diff = float(torch.max(torch.abs(ref_logits - warm_logits)).item())
    assert max_diff <= 0.1
    assert int(torch.argmax(ref_logits[0]).item()) == int(torch.argmax(warm_logits[0]).item())


def test_3_different_weights_under_same_display_name_cannot_reuse_authority(
    tmp_path, real_verified_bundle
):
    """
    Requirement 3: Different weights under the same display name ("Qwen/Qwen2.5-0.5B-Instruct")
    cannot reuse that authority, whether via in-memory weight perturbation or an altered weight
    artifact under the same model display name.
    """
    bundle = real_verified_bundle["bundle"]
    issuer = real_verified_bundle["issuer"]
    device = real_verified_bundle["device"]
    model = bundle.model

    manager = PagedKVManager(
        storage_dir=tmp_path / "diff_weights_store",
        max_ram_blocks=16,
        auth_key=STORE_KEY,
        trusted_issuer=issuer,
    )
    adapter = PyTorchPagedKVAdapter(manager=manager, device=device, model=model)

    tokens = list(range(500, 540))
    adapter.prepare_prefill_context("seed", tokens, namespace=bundle.namespace, authority=bundle.authority)
    with torch.no_grad():
        out = model(torch.tensor([tokens], dtype=torch.long, device=device), use_cache=True)
    adapter.extract_and_cache_sequence_kv(
        "seed", tokens, out.past_key_values, namespace=bundle.namespace, authority=bundle.authority
    )

    # Case A: Perturb a weight parameter in memory under the same model display name and restore afterwards
    target_param = model.model.layers[0].self_attn.q_proj.weight
    saved_first_val = float(target_param.data[0, 0].item())
    try:
        with torch.no_grad():
            target_param.data[0, 0] = saved_first_val + 1.0

        assert verify_live_model_instance(model, expected_namespace=bundle.namespace) == "mutated_in_memory_model_instance"

        # Attempting warm reuse with the mutated weights under the same display name and authority is rejected
        mutated_ctx = adapter.prepare_prefill_context(
            "seq-mutated-weights", tokens, namespace=bundle.namespace, authority=bundle.authority
        )
        assert mutated_ctx["warm_cache_hit"] is False
        assert mutated_ctx["matched_tokens"] == 0

        # Attempting to extract/cache KV tensors from the mutated model is also rejected
        with pytest.raises(ValueError, match="KV cache extraction denied"):
            adapter.extract_and_cache_sequence_kv(
                "seq-mutated-extract",
                tokens,
                out.past_key_values,
                namespace=bundle.namespace,
                authority=bundle.authority,
            )
    finally:
        with torch.no_grad():
            target_param.data[0, 0] = saved_first_val

    # Verify restoring the exact weight restores valid fingerprint
    assert verify_live_model_instance(model, expected_namespace=bundle.namespace) == bundle.model_instance_id

    # Case B: Different weight digest registered under the same display name ("Qwen/Qwen2.5-0.5B-Instruct")
    ns_diff_weights_same_name = ExecutionNamespace(
        model_name=MODEL_ID,
        model_revision="a" * 64,  # Different weights digest under same display name
        tokenizer_id=bundle.namespace.tokenizer_id,
        chat_template_hash=bundle.namespace.chat_template_hash,
        kv_layout=bundle.namespace.kv_layout,
        rope_config_hash=bundle.namespace.rope_config_hash,
        runtime_compatibility=bundle.namespace.runtime_compatibility,
        tenant_id=bundle.namespace.tenant_id,
        identity_scope=bundle.namespace.identity_scope,
    )
    diff_w_ctx = adapter.prepare_prefill_context(
        "seq-diff-weight-digest", tokens, namespace=ns_diff_weights_same_name, authority=bundle.authority
    )
    assert diff_w_ctx["warm_cache_hit"] is False
    assert diff_w_ctx["matched_tokens"] == 0


def test_4_changed_tokenizer_template_positional_dtype_runtime_tenant_or_instance_rejected(
    tmp_path, real_verified_bundle
):
    """
    Requirement 4: Changed tokenizer, chat template, positional/RoPE configuration,
    dtype/layout, runtime compatibility, tenant scope, or model-instance binding is rejected.
    """
    bundle = real_verified_bundle["bundle"]
    issuer = real_verified_bundle["issuer"]
    device = real_verified_bundle["device"]
    model = bundle.model
    base_ns = bundle.namespace

    manager = PagedKVManager(
        storage_dir=tmp_path / "field_mismatch_store",
        max_ram_blocks=16,
        auth_key=STORE_KEY,
        trusted_issuer=issuer,
    )
    adapter = PyTorchPagedKVAdapter(manager=manager, device=device, model=model)

    tokens = list(range(600, 640))
    adapter.prepare_prefill_context("seed-4", tokens, namespace=base_ns, authority=bundle.authority)
    with torch.no_grad():
        out = model(torch.tensor([tokens], dtype=torch.long, device=device), use_cache=True)
    adapter.extract_and_cache_sequence_kv(
        "seed-4", tokens, out.past_key_values, namespace=base_ns, authority=bundle.authority
    )

    mutations = {
        "tokenizer_id": {"tokenizer_id": "b" * 64},
        "chat_template_hash": {"chat_template_hash": "c" * 64},
        "rope_config_hash": {"rope_config_hash": "d" * 64},
        "kv_layout": {"kv_layout": "bshd_fp32"},
        "runtime_compatibility": {"runtime_compatibility": "pytorch_cu118_incompatible"},
        "tenant_id": {"tenant_id": "tenant_other_unauthorized"},
    }
    for label, overrides in mutations.items():
        ns_dict = {
            "model_name": base_ns.model_name,
            "model_revision": base_ns.model_revision,
            "tokenizer_id": base_ns.tokenizer_id,
            "chat_template_hash": base_ns.chat_template_hash,
            "kv_layout": base_ns.kv_layout,
            "rope_config_hash": base_ns.rope_config_hash,
            "runtime_compatibility": base_ns.runtime_compatibility,
            "tenant_id": base_ns.tenant_id,
            "identity_scope": base_ns.identity_scope,
        }
        ns_dict.update(overrides)
        mutated_ns = ExecutionNamespace(**ns_dict)
        ctx = adapter.prepare_prefill_context(
            f"seq-mismatch-{label}", tokens, namespace=mutated_ns, authority=bundle.authority
        )
        assert ctx["warm_cache_hit"] is False, f"Expected rejection for changed {label}"
        assert ctx["matched_tokens"] == 0, f"Expected 0 matched tokens for changed {label}"

    # Loader-level dtype vs manifest kv_layout mismatch is also rejected prior to authority issuance
    with pytest.raises(ValueError, match="Manifest kv_layout 'bshd_fp16' does not match requested torch_dtype"):
        TrustedLocalModelLoader.load_verified_model(
            cache_or_snapshot_dir=LOCAL_MODEL_DIR,
            model_name=MODEL_ID,
            snapshot_revision=PINNED_REVISION,
            manifest=bundle.manifest,
            issuer=issuer,
            device=device,
            torch_dtype=torch.float32,
        )

    # Changed in-memory positional configuration (rope_theta) on model instance is rejected
    orig_theta = model.config.rope_theta
    try:
        model.config.rope_theta = 500000.0
        ctx_rope = adapter.prepare_prefill_context(
            "seq-mismatch-inmem-rope", tokens, namespace=base_ns, authority=bundle.authority
        )
        assert ctx_rope["warm_cache_hit"] is False
        assert ctx_rope["matched_tokens"] == 0
    finally:
        model.config.rope_theta = orig_theta

    # Adapter without model instance binding cannot use an instance-bound authority
    unbound_adapter = PyTorchPagedKVAdapter(manager=manager, device=device, model=None)
    ctx_unbound = unbound_adapter.prepare_prefill_context(
        "seq-unbound-model", tokens, namespace=base_ns, authority=bundle.authority
    )
    assert ctx_unbound["warm_cache_hit"] is False
    assert ctx_unbound["matched_tokens"] == 0


def test_5_missing_altered_substituted_and_toctou_replaced_artifacts_fail_verification(
    tmp_path, real_verified_bundle
):
    """
    Requirement 5: Missing, altered, or substituted artifacts (plus unpinned revisions
    and mid-load TOCTOU file replacement between pre-load verification and post-load check)
    fail verification and do not obtain reuse authority.
    """
    bundle = real_verified_bundle["bundle"]
    issuer = real_verified_bundle["issuer"]
    device = real_verified_bundle["device"]
    real_snap = bundle.snapshot_dir

    # 5a. Unpinned revision ('main') is rejected
    with pytest.raises(ValueError, match="explicit 40-char lowercase hex commit SHA"):
        TrustedLocalModelLoader.load_verified_model(
            cache_or_snapshot_dir=LOCAL_MODEL_DIR,
            model_name=MODEL_ID,
            snapshot_revision="main",
            manifest=bundle.manifest,
            issuer=issuer,
            device=device,
        )

    # Create a disposable snapshot directory hard-linking the 988 MB weights and copying the small configs
    fake_snap = tmp_path / f"models--{MODEL_ID.replace('/', '--')}" / "snapshots" / PINNED_REVISION
    fake_snap.mkdir(parents=True, exist_ok=True)
    for fname in os.listdir(real_snap):
        src_f = real_snap / fname
        if src_f.is_file():
            if fname == "model.safetensors":
                _link_or_copy_file(src_f, fake_snap / fname)
            else:
                shutil.copy2(src_f, fake_snap / fname)

    # 5b. Missing artifact (delete tokenizer.json) fails with FileNotFoundError
    tok_path = fake_snap / "tokenizer.json"
    tok_backup = tok_path.read_bytes()
    tok_path.unlink()
    with pytest.raises(FileNotFoundError, match="Required local model artifact 'tokenizer' missing"):
        TrustedLocalModelLoader.load_verified_model(
            cache_or_snapshot_dir=tmp_path,
            model_name=MODEL_ID,
            snapshot_revision=PINNED_REVISION,
            manifest=bundle.manifest,
            issuer=issuer,
            device=device,
        )
    tok_path.write_bytes(tok_backup)

    # 5c. Altered/substituted artifact (modify tokenizer_config.json chat_template) fails pre-load SHA-256 check
    tmpl_path = fake_snap / "tokenizer_config.json"
    tmpl_backup = tmpl_path.read_bytes()
    tmpl_path.write_bytes(tmpl_backup + b"\n")
    with pytest.raises(ValueError, match="Pre-load artifact digest mismatch for role 'chat_template'"):
        TrustedLocalModelLoader.load_verified_model(
            cache_or_snapshot_dir=tmp_path,
            model_name=MODEL_ID,
            snapshot_revision=PINNED_REVISION,
            manifest=bundle.manifest,
            issuer=issuer,
            device=device,
        )
    tmpl_path.write_bytes(tmpl_backup)

    # 5d. Mid-load TOCTOU replacement: file passes pre-load verification, is replaced before/during load,
    # and is caught by post-load TOCTOU re-verification before any authority is issued.
    active_count_before = len(issuer._active_authorities)

    def _tamper_mid_load(paths: Dict[str, Path]) -> None:
        cfg_data = json.loads(paths["rope_config"].read_text(encoding="utf-8"))
        cfg_data["rope_theta"] = 10000.0
        paths["rope_config"].write_text(json.dumps(cfg_data), encoding="utf-8")

    with pytest.raises(ValueError, match="TOCTOU artifact modification or replacement detected"):
        TrustedLocalModelLoader.load_verified_model(
            cache_or_snapshot_dir=tmp_path,
            model_name=MODEL_ID,
            snapshot_revision=PINNED_REVISION,
            manifest=bundle.manifest,
            issuer=issuer,
            device=device,
            _mid_load_hook=_tamper_mid_load,
        )
    assert len(issuer._active_authorities) == active_count_before


def test_6_synthetic_fixture_authority_cannot_authorize_separately_loaded_real_model(
    tmp_path, real_verified_bundle
):
    """
    Requirement 6: Synthetic-fixture authority (issued for synthetic marker files)
    cannot authorize a separately loaded real Qwen model instance, nor can a separately
    loaded unverified real model borrow the real bundle's authority.
    """
    bundle = real_verified_bundle["bundle"]
    device = real_verified_bundle["device"]

    # Create a synthetic fixture authority (like unit tests use)
    synth_dir = tmp_path / "synth_artifacts"
    synth_dir.mkdir(parents=True, exist_ok=True)
    marker = b"SYNTHETIC_UNIT_TEST_MARKER_BYTES"
    synth_paths = {
        role: synth_dir / f"{role}.bin"
        for role in ("weights", "tokenizer", "chat_template", "rope_config")
    }
    for p in synth_paths.values():
        p.write_bytes(marker)
    m_sha = hashlib.sha256(marker).hexdigest()
    synth_manifest = ModelIdentityManifest(
        model_name=MODEL_ID,
        weight_digest=m_sha,
        tokenizer_digest=m_sha,
        chat_template_digest=m_sha,
        kv_layout="bshd_fp16",
        rope_config_digest=m_sha,
        runtime_compatibility="pytorch_cu121_v1",
        tenant_id="tenant_synth",
    )
    synth_reg = ApprovedManifestRegistry()
    synth_reg.register_approved_manifest(synth_manifest)
    synth_issuer = IdentityIssuanceAuthority(registry=synth_reg)
    synth_authority = synth_issuer.verify_and_issue(synth_manifest, synth_paths)
    synth_ns = ExecutionNamespace.from_manifest(synth_manifest)

    # Load a separate real Qwen model directly via AutoModelForCausalLM (outside TrustedLocalModelLoader)
    separate_real_model = AutoModelForCausalLM.from_pretrained(
        str(bundle.snapshot_dir),
        torch_dtype=torch.float16,
        local_files_only=True,
    ).to(device)
    separate_real_model.eval()

    assert (
        verify_live_model_instance(separate_real_model, expected_namespace=synth_ns)
        == "unverified_external_model_instance"
    )

    mgr = PagedKVManager(
        storage_dir=tmp_path / "synth_vs_real_store",
        max_ram_blocks=16,
        auth_key=STORE_KEY,
        trusted_issuer=synth_issuer,
    )
    adapter_with_separate_real = PyTorchPagedKVAdapter(
        manager=mgr, device=device, model=separate_real_model
    )

    tokens = list(range(700, 732))
    with torch.no_grad():
        sep_out = separate_real_model(
            torch.tensor([tokens], dtype=torch.long, device=device), use_cache=True
        )

    # 6a. Synthetic authority is rejected when attempting to extract/cache KV from the real model
    with pytest.raises(ValueError, match="KV cache extraction denied"):
        adapter_with_separate_real.extract_and_cache_sequence_kv(
            "seq-sep-real",
            tokens,
            sep_out.past_key_values,
            namespace=synth_ns,
            authority=synth_authority,
        )

    # 6b. Even if synthetic pages were seeded by a model-less unit test, the real model cannot warm-reuse them
    unit_adapter = PyTorchPagedKVAdapter(manager=mgr, device=device, model=None)
    unit_adapter.extract_and_cache_sequence_kv(
        "seq-unit-seed", tokens, sep_out.past_key_values, namespace=synth_ns, authority=synth_authority
    )
    warm_attempt = adapter_with_separate_real.prepare_prefill_context(
        "seq-real-borrow-synth", tokens, namespace=synth_ns, authority=synth_authority
    )
    assert warm_attempt["warm_cache_hit"] is False
    assert warm_attempt["matched_tokens"] == 0

    # 6c. Separately loaded real model also cannot borrow the TrustedLocalModelLoader bundle's authority
    real_mgr = PagedKVManager(
        storage_dir=tmp_path / "real_borrow_store",
        max_ram_blocks=16,
        auth_key=STORE_KEY,
        trusted_issuer=real_verified_bundle["issuer"],
    )
    real_adapter = PyTorchPagedKVAdapter(manager=real_mgr, device=device, model=bundle.model)
    real_adapter.extract_and_cache_sequence_kv(
        "seq-real-seed", tokens, sep_out.past_key_values, namespace=bundle.namespace, authority=bundle.authority
    )
    borrow_attempt = real_adapter.prepare_prefill_context(
        "seq-unverified-borrow",
        tokens,
        namespace=bundle.namespace,
        authority=bundle.authority,
        model=separate_real_model,
    )
    assert borrow_attempt["warm_cache_hit"] is False
    assert borrow_attempt["matched_tokens"] == 0

    del separate_real_model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def test_7_revoked_authority_cannot_authorize_subsequent_ram_or_disk_reuse(
    tmp_path, real_verified_bundle
):
    """
    Requirement 7: Revoked authority cannot authorize subsequent Tier 1 RAM reuse
    or Tier 2 disk restoration.
    """
    bundle = real_verified_bundle["bundle"]
    device = real_verified_bundle["device"]
    model = bundle.model

    # Issue a fresh authority for the verified loaded model so revoking it does not mutate module fixture
    registry = ApprovedManifestRegistry()
    registry.register_approved_manifest(bundle.manifest)
    issuer = IdentityIssuanceAuthority(registry=registry)
    fresh_authority = issuer.verify_and_issue(
        manifest=bundle.manifest,
        artifact_paths=bundle.artifact_paths,
        tenant_id=bundle.manifest.tenant_id,
        model_instance_id=bundle.model_instance_id,
        require_instance_binding=True,
    )

    manager = PagedKVManager(
        storage_dir=tmp_path / "revocation_store",
        max_ram_blocks=16,
        auth_key=STORE_KEY,
        trusted_issuer=issuer,
    )
    adapter = PyTorchPagedKVAdapter(manager=manager, device=device, model=model)

    tokens_a = list(range(800, 832)) + list(range(900, 908))
    tokens_b = list(range(800, 832)) + list(range(950, 958))

    adapter.prepare_prefill_context("seq-rev-a", tokens_a, namespace=bundle.namespace, authority=fresh_authority)
    with torch.no_grad():
        out_a = model(torch.tensor([tokens_a], dtype=torch.long, device=device), use_cache=True)
    adapter.extract_and_cache_sequence_kv(
        "seq-rev-a", tokens_a, out_a.past_key_values, namespace=bundle.namespace, authority=fresh_authority
    )

    # Confirm warm RAM reuse works before revocation
    pre_rev_ctx = adapter.prepare_prefill_context(
        "seq-pre-rev", tokens_b, namespace=bundle.namespace, authority=fresh_authority
    )
    assert pre_rev_ctx["warm_cache_hit"] is True
    assert pre_rev_ctx["matched_tokens"] == 32

    # Revoke authority
    assert issuer.revoke_authority(fresh_authority.authority_id) is True
    assert fresh_authority.is_active is False

    # 7a. Subsequent Tier 1 RAM warm reuse is rejected
    post_rev_ram = adapter.prepare_prefill_context(
        "seq-post-rev-ram", tokens_b, namespace=bundle.namespace, authority=fresh_authority
    )
    assert post_rev_ram["warm_cache_hit"] is False
    assert post_rev_ram["matched_tokens"] == 0

    # 7b. Evict sequence blocks to disk and verify Tier 2 disk restoration is rejected
    for h in manager.table.get_block_hashes("seq-rev-a"):
        manager.store.evict_from_ram(h)
        manager.table.offload_block(h)

    with pytest.raises(ValueError, match="reuse authority has been revoked or invalidated"):
        manager.restore_sequence_to_ram("seq-rev-a", model_instance_id=bundle.model_instance_id)

    post_rev_disk = adapter.prepare_prefill_context(
        "seq-post-rev-disk", tokens_b, namespace=bundle.namespace, authority=fresh_authority
    )
    assert post_rev_disk["warm_cache_hit"] is False
    assert post_rev_disk["matched_tokens"] == 0
