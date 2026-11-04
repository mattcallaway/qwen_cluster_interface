import hashlib
import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple, Union

from .hasher import ExecutionNamespace, ModelIdentityManifest

STREAM_CHUNK_BYTES = 4 * 1024 * 1024
FAST_FULL_HASH_LIMIT_BYTES = 32 * 1024 * 1024


def stream_file_sha256(
    path: Union[str, Path],
    chunk_size: int = STREAM_CHUNK_BYTES,
) -> Tuple[str, int, float]:
    """
    Computes SHA-256 digest of a file using bounded streaming chunks
    so large model weight files are never loaded into memory at once.
    Returns (hex_digest, total_bytes, st_mtime).
    """
    p = Path(path).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"Artifact file not found: {p}")
    stat_before = p.stat()
    h = hashlib.sha256()
    total_read = 0
    with open(p, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
            total_read += len(chunk)
    stat_after = p.stat()
    if stat_after.st_size != total_read or abs(stat_after.st_mtime - stat_before.st_mtime) > 1e-6:
        raise ValueError(f"Concurrent modification detected while streaming artifact {p}")
    return h.hexdigest(), total_read, stat_after.st_mtime


@dataclass(frozen=True)
class BoundArtifactInfo:
    """Records immutable verification attributes of an approved artifact on disk."""
    role: str
    file_path: str
    file_size: int
    sha256: str
    mtime: float


class IssuedReuseAuthority:
    """
    Cryptographic authority token issued by IdentityIssuanceAuthority.
    Authorizes genuine KV reuse in PagedKVManager for a specific model instance,
    layout, runtime, and tenant.
    Cannot be fabricated by caller-selected string labels or dataclass arguments.
    """

    REQUIRED_ROLES = frozenset({"weights", "tokenizer", "chat_template", "rope_config"})

    def __init__(
        self,
        authority_id: str,
        manifest: ModelIdentityManifest,
        bound_artifacts: Dict[str, BoundArtifactInfo],
        issued_at: float,
        issuer_token: str,
        model_instance_id: Optional[str] = None,
        require_instance_binding: bool = False,
    ):
        if not authority_id or not isinstance(authority_id, str):
            raise ValueError("authority_id must be a non-empty string")
        if not isinstance(manifest, ModelIdentityManifest):
            raise TypeError(f"manifest must be ModelIdentityManifest, got {type(manifest).__name__}")
        if not bound_artifacts or not isinstance(bound_artifacts, dict):
            raise ValueError("bound_artifacts must be a non-empty mapping")
        missing_roles = self.REQUIRED_ROLES - set(bound_artifacts.keys())
        if missing_roles:
            raise ValueError(f"bound_artifacts is missing required roles: {sorted(missing_roles)}")
        for role, info in bound_artifacts.items():
            if not isinstance(info, BoundArtifactInfo):
                raise TypeError(f"Artifact for role '{role}' must be BoundArtifactInfo")
        if not issuer_token or not isinstance(issuer_token, str):
            raise ValueError("issuer_token must be a non-empty string")

        self._authority_id = authority_id
        self._manifest = manifest
        self._bound_artifacts = bound_artifacts
        self._issued_at = issued_at
        self._issuer_token = issuer_token
        self._model_instance_id = model_instance_id or manifest.model_name
        self._require_instance_binding = bool(require_instance_binding)
        self._is_active = True

    @property
    def authority_id(self) -> str:
        return self._authority_id

    @property
    def manifest(self) -> ModelIdentityManifest:
        return self._manifest

    @property
    def model_name(self) -> str:
        return self._manifest.model_name

    @property
    def model_revision(self) -> str:
        return self._manifest.weight_digest

    @property
    def tenant_id(self) -> str:
        return self._manifest.tenant_id

    @property
    def kv_layout(self) -> str:
        return self._manifest.kv_layout

    @property
    def runtime_compatibility(self) -> str:
        return self._manifest.runtime_compatibility

    @property
    def is_active(self) -> bool:
        return self._is_active

    @property
    def bound_artifacts(self) -> Dict[str, BoundArtifactInfo]:
        return dict(self._bound_artifacts)

    @property
    def model_instance_id(self) -> str:
        return self._model_instance_id

    @property
    def require_instance_binding(self) -> bool:
        return self._require_instance_binding

    def invalidate(self) -> None:
        """Revokes this authority token."""
        self._is_active = False

    def verify_live_artifacts(self, full_stream: bool = False) -> bool:
        """
        Guards against TOCTOU path substitution by re-checking that bound files
        have not been modified, truncated, or replaced on disk using streaming reads.
        """
        if not self._is_active:
            return False
        if not self._bound_artifacts or len(self._bound_artifacts) < len(self.REQUIRED_ROLES):
            self._is_active = False
            return False
        for role in self.REQUIRED_ROLES:
            if role not in self._bound_artifacts:
                self._is_active = False
                return False
        for role, info in self._bound_artifacts.items():
            p = Path(info.file_path)
            if not p.is_file():
                self._is_active = False
                return False
            stat = p.stat()
            if stat.st_size != info.file_size or abs(stat.st_mtime - info.mtime) > 1e-4:
                self._is_active = False
                return False
            if full_stream or stat.st_size <= FAST_FULL_HASH_LIMIT_BYTES:
                try:
                    actual_sha, _, _ = stream_file_sha256(p)
                except Exception:
                    self._is_active = False
                    return False
                if actual_sha.lower() != info.sha256.lower():
                    self._is_active = False
                    return False
        return True

    def is_valid_for(self, namespace: ExecutionNamespace) -> bool:
        """
        Verifies that this authority token authorizes the specific namespace.
        Checks all authoritative fields and verifies live artifact binding.
        """
        if not self._is_active:
            return False
        if not self.verify_live_artifacts():
            return False
        return (
            namespace.model_name == self.model_name
            and namespace.model_revision == self.model_revision
            and namespace.tokenizer_id == self._manifest.tokenizer_digest
            and namespace.chat_template_hash == self._manifest.chat_template_digest
            and namespace.kv_layout == self.kv_layout
            and namespace.rope_config_hash == self._manifest.rope_config_digest
            and namespace.tenant_id == self.tenant_id
            and namespace.runtime_compatibility == self.runtime_compatibility
        )


class ApprovedManifestRegistry:
    """
    Registry of system/administrator-approved ModelIdentityManifest definitions.
    Callers cannot author an arbitrary manifest and have it automatically verified.
    Only approved manifests registered in this authority can obtain reuse authority.
    """

    def __init__(self):
        self._approved_manifests: Dict[str, ModelIdentityManifest] = {}
        self._lock = threading.Lock()

    def register_approved_manifest(self, manifest: ModelIdentityManifest) -> None:
        if not isinstance(manifest, ModelIdentityManifest):
            raise TypeError(f"Expected ModelIdentityManifest, got {type(manifest).__name__}")
        with self._lock:
            key = f"{manifest.model_name}:{manifest.weight_digest}"
            self._approved_manifests[key] = manifest

    def get_approved_manifest(self, model_name: str, weight_digest: str) -> Optional[ModelIdentityManifest]:
        with self._lock:
            key = f"{model_name}:{weight_digest}"
            return self._approved_manifests.get(key)

    def is_approved(self, manifest: ModelIdentityManifest) -> bool:
        with self._lock:
            key = f"{manifest.model_name}:{manifest.weight_digest}"
            approved = self._approved_manifests.get(key)
            if approved is None:
                return False
            # Check exact match across all attributes
            return (
                approved.model_name == manifest.model_name
                and approved.weight_digest == manifest.weight_digest
                and approved.tokenizer_digest == manifest.tokenizer_digest
                and approved.chat_template_digest == manifest.chat_template_digest
                and approved.kv_layout == manifest.kv_layout
                and approved.rope_config_digest == manifest.rope_config_digest
                and approved.runtime_compatibility == manifest.runtime_compatibility
                and approved.tenant_id == manifest.tenant_id
            )

    def clear(self) -> None:
        with self._lock:
            self._approved_manifests.clear()


class IdentityIssuanceAuthority:
    """
    Trusted identity verification and authority issuance authority.
    Verifies approved manifest against actual artifact bytes on disk.
    Issues runtime-bound IssuedReuseAuthority instances.
    """

    def __init__(self, registry: Optional[ApprovedManifestRegistry] = None):
        self.registry = registry or ApprovedManifestRegistry()
        self._issuer_secret = uuid.uuid4().hex
        self._active_authorities: Dict[str, IssuedReuseAuthority] = {}
        self._lock = threading.Lock()

    def verify_and_issue(
        self,
        manifest: ModelIdentityManifest,
        artifact_paths: Dict[str, Union[str, Path]],
        tenant_id: Optional[str] = None,
        model_instance_id: Optional[str] = None,
        require_instance_binding: bool = False,
    ) -> IssuedReuseAuthority:
        """
        Verifies actual artifact bytes on disk against an approved manifest using streaming SHA-256.
        Rejects unapproved manifests, missing files, empty files, or checksum mismatches.
        Issues an IssuedReuseAuthority token only on successful verification.
        """
        if not isinstance(manifest, ModelIdentityManifest):
            raise TypeError(f"Expected ModelIdentityManifest, got {type(manifest).__name__}")

        # 1. Check approved registry
        if not self.registry.is_approved(manifest):
            raise ValueError(
                f"Identity verification rejected: manifest for model '{manifest.model_name}' "
                f"(weights={manifest.weight_digest[:16]}...) is not in the approved manifest registry. "
                f"Self-authored or unapproved manifests cannot obtain reuse authority."
            )

        # 2. Check required artifact paths
        required_roles = ["weights", "tokenizer", "chat_template", "rope_config"]
        for role in required_roles:
            if role not in artifact_paths:
                raise ValueError(f"Missing required artifact path for role: '{role}'")

        expected_digests = {
            "weights": manifest.weight_digest,
            "tokenizer": manifest.tokenizer_digest,
            "chat_template": manifest.chat_template_digest,
            "rope_config": manifest.rope_config_digest,
        }

        bound_artifacts: Dict[str, BoundArtifactInfo] = {}

        for role, raw_path in artifact_paths.items():
            if role not in expected_digests:
                continue
            path = Path(raw_path).resolve()
            if not path.is_file():
                raise FileNotFoundError(f"Artifact for role '{role}' not found at {path}")

            actual_sha, file_size, file_mtime = stream_file_sha256(path)
            if file_size == 0:
                raise ValueError(f"Artifact for role '{role}' at {path} is empty")

            expected_sha = expected_digests[role]

            if actual_sha.lower() != expected_sha.lower():
                raise ValueError(
                    f"Cryptographic artifact digest mismatch for role '{role}' at {path}: "
                    f"expected {expected_sha}, observed {actual_sha}"
                )

            bound_artifacts[role] = BoundArtifactInfo(
                role=role,
                file_path=str(path),
                file_size=file_size,
                sha256=actual_sha,
                mtime=file_mtime,
            )

        # 3. Create issued authority
        authority_id = f"auth_{uuid.uuid4().hex}"
        target_tenant = tenant_id or manifest.tenant_id
        if target_tenant != manifest.tenant_id:
            manifest = ModelIdentityManifest(
                model_name=manifest.model_name,
                weight_digest=manifest.weight_digest,
                tokenizer_digest=manifest.tokenizer_digest,
                chat_template_digest=manifest.chat_template_digest,
                kv_layout=manifest.kv_layout,
                rope_config_digest=manifest.rope_config_digest,
                runtime_compatibility=manifest.runtime_compatibility,
                tenant_id=target_tenant,
            )

        authority = IssuedReuseAuthority(
            authority_id=authority_id,
            manifest=manifest,
            bound_artifacts=bound_artifacts,
            issued_at=time.time(),
            issuer_token=self._issuer_secret,
            model_instance_id=model_instance_id or manifest.model_name,
            require_instance_binding=require_instance_binding,
        )

        with self._lock:
            self._active_authorities[authority_id] = authority

        return authority

    def validate_authority_for_reuse(
        self,
        authority: Any,
        namespace: ExecutionNamespace,
        model_instance_id: Optional[str] = None,
    ) -> bool:
        """
        Validates that an authority token:
        1. Is an authentic IssuedReuseAuthority instance.
        2. Was actually issued by this specific IdentityIssuanceAuthority instance.
        3. Remains active in this issuer's records.
        4. Matches this issuer's private secret token.
        5. Has not been revoked or invalidated.
        6. Its approved manifest remains in this issuer's registry.
        7. Required artifact bindings are complete, non-empty, and intact on disk.
        8. Matches the requested execution namespace, layout, tenant, runtime, and model instance.
        """
        if not isinstance(authority, IssuedReuseAuthority):
            return False
        if not isinstance(namespace, ExecutionNamespace):
            return False

        with self._lock:
            # 1. Authority must actually have been issued by this issuer instance
            active_auth = self._active_authorities.get(authority.authority_id)
            if active_auth is None or active_auth is not authority:
                return False

            # 2. Issuer token must match private secret
            if authority._issuer_token != self._issuer_secret:
                return False

            # 3. Must be actively valid (not invalidated/revoked)
            if not authority.is_active:
                return False

            # 4. Manifest must still be approved in registry
            if not self.registry.is_approved(authority.manifest):
                return False

            # 5. Required artifact bindings must be complete and non-empty
            bound = authority.bound_artifacts
            if not bound:
                return False
            missing_roles = IssuedReuseAuthority.REQUIRED_ROLES - set(bound.keys())
            if missing_roles:
                return False

            # 6. Live disk artifact verification (TOCTOU)
            if not authority.verify_live_artifacts():
                return False

            # 7. Check namespace attributes against authority
            if (
                namespace.model_name != authority.model_name
                or namespace.model_revision != authority.model_revision
                or namespace.tokenizer_id != authority.manifest.tokenizer_digest
                or namespace.chat_template_hash != authority.manifest.chat_template_digest
                or namespace.kv_layout != authority.kv_layout
                or namespace.rope_config_hash != authority.manifest.rope_config_digest
                or namespace.tenant_id != authority.tenant_id
                or namespace.runtime_compatibility != authority.runtime_compatibility
            ):
                return False

            # 8. Check model instance binding
            if authority.require_instance_binding and not model_instance_id:
                return False
            if model_instance_id and authority.model_instance_id != model_instance_id:
                return False

            return True

    def revoke_authority(self, authority_id: str) -> bool:
        with self._lock:
            auth = self._active_authorities.pop(authority_id, None)
            if auth is not None:
                auth.invalidate()
                return True
            return False


_global_registry = ApprovedManifestRegistry()
_global_authority = IdentityIssuanceAuthority(registry=_global_registry)


def get_default_manifest_registry() -> ApprovedManifestRegistry:
    return _global_registry


def get_default_issuance_authority() -> IdentityIssuanceAuthority:
    return _global_authority


def reset_default_issuance_authority() -> None:
    global _global_registry, _global_authority
    _global_registry = ApprovedManifestRegistry()
    _global_authority = IdentityIssuanceAuthority(registry=_global_registry)

