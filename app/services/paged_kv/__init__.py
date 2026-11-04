"""
Searchable Paged KV Cache Architecture for Qwen Collective.
Provides virtual block paging, prefix hashing, tiered KV cache storage,
and cryptographic identity issuance authority.
"""

from .hasher import ExecutionNamespace, ModelIdentityManifest, PagedKVHasher, PrefixRadixTree, TokenBlock
from .identity import (
    ApprovedManifestRegistry,
    BoundArtifactInfo,
    IdentityIssuanceAuthority,
    IssuedReuseAuthority,
    get_default_issuance_authority,
    get_default_manifest_registry,
    stream_file_sha256,
)
from .manager import PagedKVManager
from .model_loader import (
    TrustedLocalModelLoader,
    VerifiedLoadedModelBundle,
    compute_live_model_fingerprint,
    verify_live_model_instance,
)
from .page_table import BlockMapping, PhysicalBlockPool, VirtualBlockTable
from .store import PagedKVStore

__all__ = [
    "ExecutionNamespace",
    "ModelIdentityManifest",
    "PagedKVHasher",
    "PrefixRadixTree",
    "TokenBlock",
    "VirtualBlockTable",
    "PhysicalBlockPool",
    "BlockMapping",
    "PagedKVStore",
    "PagedKVManager",
    "ApprovedManifestRegistry",
    "BoundArtifactInfo",
    "IdentityIssuanceAuthority",
    "IssuedReuseAuthority",
    "get_default_issuance_authority",
    "get_default_manifest_registry",
    "stream_file_sha256",
    "TrustedLocalModelLoader",
    "VerifiedLoadedModelBundle",
    "compute_live_model_fingerprint",
    "verify_live_model_instance",
]
