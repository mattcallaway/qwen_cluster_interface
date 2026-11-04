"""
Trusted Local Model Loader & Runtime Identity Binder (Increment 17).

Bridges approved local model artifacts on disk (weights, tokenizer, chat template,
positional/RoPE configuration) with the actual loaded PyTorch model instance and
IdentityIssuanceAuthority.

Security invariants:
1. Requires an explicit 40-character hexadecimal snapshot revision (rejects unpinned
   refs such as 'main' or 'latest').
2. Streams SHA-256 verification of all required artifacts before loading without
   reading full weight files into extra memory buffers.
3. Loads model and tokenizer strictly offline (`local_files_only=True`) from the
   verified snapshot directory.
4. Re-verifies all artifact files on disk after loading completes to detect TOCTOU
   file replacement between verification and loading.
5. Verifies loaded in-memory model config, positional/RoPE settings, tensor dtype/KV layout,
   and computes a live parameter tensor fingerprint across all transformer layers.
6. Issues a narrowly scoped `IssuedReuseAuthority` bound to `model_instance_id`
   (`require_instance_binding=True`) only after both verification and loading succeed.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple, Union

try:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    HAS_TORCH = True
except ImportError:
    torch = None
    AutoModelForCausalLM = None
    AutoTokenizer = None
    HAS_TORCH = False

from .hasher import ExecutionNamespace, ModelIdentityManifest
from .identity import IdentityIssuanceAuthority, IssuedReuseAuthority, stream_file_sha256

PINNED_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")

DTYPE_TO_KV_LAYOUT = {
    "torch.float16": "bshd_fp16",
    "torch.float32": "bshd_fp32",
    "torch.bfloat16": "bshd_bf16",
}


def compute_live_model_fingerprint(model: Any) -> str:
    """
    Computes a fast, deterministic cryptographic fingerprint of a live in-memory
    PyTorch model instance covering its parameter dtype, architectural/RoPE config,
    and sampled parameter bytes across all transformer layers.
    """
    if not HAS_TORCH or model is None:
        return "no_torch_model"

    h = hashlib.sha256()
    cfg = getattr(model, "config", None)
    if cfg is not None:
        cfg_summary = {
            "model_type": getattr(cfg, "model_type", None),
            "num_hidden_layers": getattr(cfg, "num_hidden_layers", None),
            "hidden_size": getattr(cfg, "hidden_size", None),
            "num_attention_heads": getattr(cfg, "num_attention_heads", None),
            "num_key_value_heads": getattr(cfg, "num_key_value_heads", None),
            "rope_theta": float(getattr(cfg, "rope_theta", 0.0) or 0.0),
            "max_position_embeddings": getattr(cfg, "max_position_embeddings", None),
            "vocab_size": getattr(cfg, "vocab_size", None),
        }
        h.update(json.dumps(cfg_summary, sort_keys=True).encode("utf-8"))

    with torch.no_grad():
        for name, param in model.named_parameters():
            h.update(name.encode("utf-8"))
            h.update(str(param.dtype).encode("ascii"))
            h.update(str(tuple(param.shape)).encode("ascii"))
            flat = param.detach().reshape(-1)
            numel = flat.numel()
            if numel > 0:
                head_slice = flat[: min(128, numel)].to(torch.float32).cpu().numpy().tobytes()
                tail_slice = flat[max(0, numel - 128) :].to(torch.float32).cpu().numpy().tobytes()
                h.update(head_slice)
                h.update(tail_slice)

    return h.hexdigest()


def verify_live_model_instance(
    model: Any,
    expected_namespace: Optional[ExecutionNamespace] = None,
) -> Optional[str]:
    """
    Verifies that a live model instance carries authentic loader bindings,
    that its in-memory weights/config/dtype have not been mutated since loading,
    and that its dtype matches the expected namespace kv_layout.
    Returns the verified model_instance_id, or an invalid marker string if verification fails.
    """
    if model is None:
        return None

    stamped_id = getattr(model, "_qc_model_instance_id", None)
    stamped_fp = getattr(model, "_qc_param_fingerprint", None)
    stamped_manifest: Optional[ModelIdentityManifest] = getattr(model, "_qc_manifest", None)

    if not stamped_id or not stamped_fp or stamped_manifest is None:
        return "unverified_external_model_instance"

    live_fp = compute_live_model_fingerprint(model)
    if live_fp != stamped_fp:
        return "mutated_in_memory_model_instance"

    try:
        first_param = next(model.parameters())
        expected_layout = DTYPE_TO_KV_LAYOUT.get(str(first_param.dtype))
    except StopIteration:
        return "empty_model_instance"

    if expected_layout != stamped_manifest.kv_layout:
        return "dtype_layout_mismatch_instance"

    if expected_namespace is not None:
        if expected_namespace.kv_layout != expected_layout:
            return "namespace_dtype_layout_mismatch"
        if expected_namespace.model_revision != stamped_manifest.weight_digest:
            return "namespace_weight_digest_mismatch"

    return stamped_id


@dataclass(frozen=True)
class VerifiedLoadedModelBundle:
    """Container returned by TrustedLocalModelLoader.load_verified_model."""
    model: Any
    tokenizer: Any
    manifest: ModelIdentityManifest
    namespace: ExecutionNamespace
    authority: IssuedReuseAuthority
    model_instance_id: str
    snapshot_revision: str
    snapshot_dir: Path
    artifact_paths: Dict[str, Path]


class TrustedLocalModelLoader:
    """
    Loads a local Hugging Face CausalLM snapshot at an explicitly pinned commit revision,
    verifies all disk artifacts before and after loading (preventing TOCTOU replacement),
    binds the loaded in-memory model instance, and issues narrowly scoped reuse authority.
    """

    @staticmethod
    def resolve_snapshot_dir(
        cache_or_snapshot_dir: Union[str, Path],
        model_name: str,
        snapshot_revision: str,
    ) -> Path:
        if not isinstance(snapshot_revision, str) or not PINNED_REVISION_RE.match(snapshot_revision):
            raise ValueError(
                f"snapshot_revision must be an explicit 40-char lowercase hex commit SHA, got {snapshot_revision!r}"
            )
        base = Path(cache_or_snapshot_dir).resolve()
        if not base.exists():
            raise FileNotFoundError(f"Local model cache/snapshot path does not exist: {base}")

        # Case 1: base is already the exact snapshot directory
        if base.name == snapshot_revision and (base / "config.json").is_file():
            return base

        # Case 2: standard Hugging Face hub cache layout
        hf_folder = f"models--{model_name.replace('/', '--')}"
        candidate = base / hf_folder / "snapshots" / snapshot_revision
        if candidate.is_dir():
            return candidate

        # Case 3: base is models--<org>--<repo>
        candidate_direct = base / "snapshots" / snapshot_revision
        if candidate_direct.is_dir():
            return candidate_direct

        raise FileNotFoundError(
            f"Pinned snapshot revision '{snapshot_revision}' for model '{model_name}' not found under {base}"
        )

    @staticmethod
    def resolve_required_artifacts(snapshot_dir: Path) -> Dict[str, Path]:
        paths = {
            "weights": snapshot_dir / "model.safetensors",
            "tokenizer": snapshot_dir / "tokenizer.json",
            "chat_template": snapshot_dir / "tokenizer_config.json",
            "rope_config": snapshot_dir / "config.json",
        }
        for role, p in paths.items():
            if not p.is_file():
                raise FileNotFoundError(f"Required local model artifact '{role}' missing at {p}")
        return paths

    @classmethod
    def inspect_local_snapshot_manifest(
        cls,
        cache_or_snapshot_dir: Union[str, Path],
        model_name: str,
        snapshot_revision: str,
        kv_layout: str = "bshd_fp16",
        runtime_compatibility: str = "pytorch_cu121_v1",
        tenant_id: str = "tenant_default",
    ) -> Tuple[ModelIdentityManifest, Dict[str, Path]]:
        """
        Streams SHA-256 digests of the 4 canonical artifacts in a pinned local snapshot
        directory to construct a ModelIdentityManifest for administrator registration.
        """
        snapshot_dir = cls.resolve_snapshot_dir(cache_or_snapshot_dir, model_name, snapshot_revision)
        artifact_paths = cls.resolve_required_artifacts(snapshot_dir)

        digests: Dict[str, str] = {}
        for role, p in artifact_paths.items():
            sha, size, _ = stream_file_sha256(p)
            if size == 0:
                raise ValueError(f"Artifact '{role}' at {p} is empty")
            digests[role] = sha

        manifest = ModelIdentityManifest(
            model_name=model_name,
            weight_digest=digests["weights"],
            tokenizer_digest=digests["tokenizer"],
            chat_template_digest=digests["chat_template"],
            kv_layout=kv_layout,
            rope_config_digest=digests["rope_config"],
            runtime_compatibility=runtime_compatibility,
            tenant_id=tenant_id,
        )
        return manifest, artifact_paths

    @classmethod
    def load_verified_model(
        cls,
        cache_or_snapshot_dir: Union[str, Path],
        model_name: str,
        snapshot_revision: str,
        manifest: ModelIdentityManifest,
        issuer: IdentityIssuanceAuthority,
        device: str = "cuda:0",
        torch_dtype: Any = None,
        tenant_id: Optional[str] = None,
        _mid_load_hook: Optional[Callable[[Dict[str, Path]], None]] = None,
    ) -> VerifiedLoadedModelBundle:
        """
        Verifies pinned local artifacts against an approved manifest, loads the model
        and tokenizer offline, re-verifies all artifacts post-load to prevent TOCTOU
        substitution, binds the live model instance, and issues reuse authority.
        """
        if not HAS_TORCH:
            raise RuntimeError("PyTorch and transformers are required for TrustedLocalModelLoader.")
        if not isinstance(manifest, ModelIdentityManifest):
            raise TypeError(f"Expected ModelIdentityManifest, got {type(manifest).__name__}")
        if issuer is None or not isinstance(issuer, IdentityIssuanceAuthority):
            raise ValueError("An explicit IdentityIssuanceAuthority is required.")

        if torch_dtype is None:
            torch_dtype = torch.float16

        expected_layout = DTYPE_TO_KV_LAYOUT.get(str(torch_dtype))
        if expected_layout is None:
            raise ValueError(f"Unsupported torch_dtype for KV layout binding: {torch_dtype}")
        if manifest.kv_layout != expected_layout:
            raise ValueError(
                f"Manifest kv_layout '{manifest.kv_layout}' does not match requested torch_dtype "
                f"'{torch_dtype}' (expected '{expected_layout}')."
            )
        if manifest.model_name != model_name:
            raise ValueError(
                f"Manifest model_name '{manifest.model_name}' does not match requested model_name '{model_name}'."
            )

        # 1. Verify manifest is pre-approved in registry before touching disk or loading weights
        if not issuer.registry.is_approved(manifest):
            raise ValueError(
                f"Manifest for '{manifest.model_name}' (weights={manifest.weight_digest[:16]}...) "
                f"is not registered in ApprovedManifestRegistry."
            )

        # 2. Resolve pinned snapshot directory & required artifact files
        snapshot_dir = cls.resolve_snapshot_dir(cache_or_snapshot_dir, model_name, snapshot_revision)
        artifact_paths = cls.resolve_required_artifacts(snapshot_dir)

        expected_digests = {
            "weights": manifest.weight_digest,
            "tokenizer": manifest.tokenizer_digest,
            "chat_template": manifest.chat_template_digest,
            "rope_config": manifest.rope_config_digest,
        }

        # 3. Pre-load streaming SHA-256 verification and file metadata capture
        pre_load_state: Dict[str, Tuple[str, int, float]] = {}
        for role, path in artifact_paths.items():
            sha, size, mtime = stream_file_sha256(path)
            if size == 0:
                raise ValueError(f"Artifact for role '{role}' at {path} is empty")
            if sha.lower() != expected_digests[role].lower():
                raise ValueError(
                    f"Pre-load artifact digest mismatch for role '{role}' at {path}: "
                    f"expected {expected_digests[role]}, observed {sha}"
                )
            pre_load_state[role] = (sha, size, mtime)

        # Validate config.json and tokenizer_config.json contents
        raw_cfg = json.loads(artifact_paths["rope_config"].read_text(encoding="utf-8"))
        raw_tok_cfg = json.loads(artifact_paths["chat_template"].read_text(encoding="utf-8"))
        if not raw_tok_cfg.get("chat_template") or not isinstance(raw_tok_cfg.get("chat_template"), str):
            raise ValueError("tokenizer_config.json is missing a valid non-empty 'chat_template'")
        for req_cfg_key in ("num_hidden_layers", "hidden_size", "num_attention_heads", "num_key_value_heads", "rope_theta"):
            if req_cfg_key not in raw_cfg:
                raise ValueError(f"config.json is missing required architectural/positional key '{req_cfg_key}'")

        # Optional test hook to simulate mid-load TOCTOU file replacement
        if callable(_mid_load_hook):
            _mid_load_hook(artifact_paths)

        # 4. Offline load from the verified local snapshot directory
        tokenizer = AutoTokenizer.from_pretrained(str(snapshot_dir), local_files_only=True)
        model = AutoModelForCausalLM.from_pretrained(
            str(snapshot_dir),
            torch_dtype=torch_dtype,
            local_files_only=True,
        ).to(device)
        model.eval()

        # 5. Post-load TOCTOU re-verification of all bound artifacts
        for role, path in artifact_paths.items():
            post_sha, post_size, post_mtime = stream_file_sha256(path)
            pre_sha, pre_size, pre_mtime = pre_load_state[role]
            if (
                post_sha.lower() != pre_sha.lower()
                or post_size != pre_size
                or abs(post_mtime - pre_mtime) > 1e-6
            ):
                raise ValueError(
                    f"TOCTOU artifact modification or replacement detected during model load for role '{role}' at {path}"
                )

        # 6. Verify loaded in-memory model config & tokenizer match verified disk configs
        if (
            getattr(model.config, "num_hidden_layers", None) != raw_cfg["num_hidden_layers"]
            or getattr(model.config, "hidden_size", None) != raw_cfg["hidden_size"]
            or getattr(model.config, "num_attention_heads", None) != raw_cfg["num_attention_heads"]
            or getattr(model.config, "num_key_value_heads", None) != raw_cfg["num_key_value_heads"]
            or abs(float(getattr(model.config, "rope_theta", 0.0)) - float(raw_cfg["rope_theta"])) > 1e-3
        ):
            raise ValueError("Loaded model.config does not match verified config.json parameters")

        actual_param_dtype = next(model.parameters()).dtype
        if actual_param_dtype != torch_dtype:
            raise ValueError(
                f"Loaded model parameter dtype '{actual_param_dtype}' does not match requested '{torch_dtype}'"
            )

        # 7. Compute live parameter fingerprint & stamp instance identity onto loaded model
        effective_tenant = tenant_id or manifest.tenant_id
        effective_manifest = manifest
        if effective_tenant != manifest.tenant_id:
            effective_manifest = ModelIdentityManifest(
                model_name=manifest.model_name,
                weight_digest=manifest.weight_digest,
                tokenizer_digest=manifest.tokenizer_digest,
                chat_template_digest=manifest.chat_template_digest,
                kv_layout=manifest.kv_layout,
                rope_config_digest=manifest.rope_config_digest,
                runtime_compatibility=manifest.runtime_compatibility,
                tenant_id=effective_tenant,
            )

        param_fp = compute_live_model_fingerprint(model)
        model_instance_id = f"inst:{model_name}:{snapshot_revision[:12]}:{param_fp[:24]}"
        setattr(model, "_qc_model_instance_id", model_instance_id)
        setattr(model, "_qc_param_fingerprint", param_fp)
        setattr(model, "_qc_manifest", effective_manifest)
        setattr(model, "_qc_snapshot_revision", snapshot_revision)

        # 8. Issue narrowly scoped reuse authority bound to this exact loaded model instance
        authority = issuer.verify_and_issue(
            manifest=manifest,
            artifact_paths=artifact_paths,
            tenant_id=effective_tenant,
            model_instance_id=model_instance_id,
            require_instance_binding=True,
        )
        namespace = ExecutionNamespace.from_manifest(effective_manifest)

        return VerifiedLoadedModelBundle(
            model=model,
            tokenizer=tokenizer,
            manifest=effective_manifest,
            namespace=namespace,
            authority=authority,
            model_instance_id=model_instance_id,
            snapshot_revision=snapshot_revision,
            snapshot_dir=snapshot_dir,
            artifact_paths=artifact_paths,
        )
