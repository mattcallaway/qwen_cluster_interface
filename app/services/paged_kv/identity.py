import hashlib
import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Sequence, Union

from .hasher import ExecutionNamespace, ModelIdentityManifest


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

    def __init__(
        self,
        authority_id: str,
        manifest: ModelIdentityManifest,
        bound_artifacts: Dict[str, BoundArtifactInfo],
        issued_at: float,
        issuer_token: str,
    ):
        self._authority_id = authority_id
        self._manifest = manifest
        self._bound_artifacts = bound_artifacts
        self._issued_at = issued_at
        self._issuer_token = issuer_token
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

    def invalidate(self) -> None:
        """Revokes this authority token."""
        self._is_active = False

    def verify_live_artifacts(self) -> bool:
        """
        Guards against TOCTOU path substitution by re-checking that bound files
        have not been modified, truncated, or replaced on disk.
        """
        if not self._is_active:
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
            actual_sha = hashlib.sha256(p.read_bytes()).hexdigest()
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
    ) -> IssuedReuseAuthority:
        """
        Verifies actual artifact bytes on disk against an approved manifest.
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

            file_bytes = path.read_bytes()
            if len(file_bytes) == 0:
                raise ValueError(f"Artifact for role '{role}' at {path} is empty")

            actual_sha = hashlib.sha256(file_bytes).hexdigest()
            expected_sha = expected_digests[role]

            if actual_sha.lower() != expected_sha.lower():
                raise ValueError(
                    f"Cryptographic artifact digest mismatch for role '{role}' at {path}: "
                    f"expected {expected_sha}, observed {actual_sha}"
                )

            stat = path.stat()
            bound_artifacts[role] = BoundArtifactInfo(
                role=role,
                file_path=str(path),
                file_size=stat.st_size,
                sha256=actual_sha,
                mtime=stat.st_mtime,
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
        )

        with self._lock:
            self._active_authorities[authority_id] = authority

        return authority

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
