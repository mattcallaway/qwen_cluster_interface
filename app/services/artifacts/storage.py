"""
Storage abstraction for Qwen Collective Unified Deliverables & Artifact Management.
Supports:
- Local filesystem backend with atomic writes and checksum integrity
- Strict path traversal prevention and symlink escape rejection
- Bounded file sizes and quota safeguards
- Pluggable interface for future NAS / S3-compatible backends
"""
from __future__ import annotations

import abc
import hashlib
import logging
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from app.config import settings

logger = logging.getLogger("qwen_collective.artifacts.storage")

DEFAULT_MAX_ARTIFACT_SIZE = 50 * 1024 * 1024  # 50 MB
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._\-]")


def sanitize_filename(filename: str) -> str:
    """Sanitize filename to prevent directory traversal or control characters."""
    raw = Path(filename).name
    clean = _SAFE_FILENAME_RE.sub("_", raw)[:150].strip("._")
    if not clean:
        clean = f"artifact_{uuid.uuid4().hex[:8]}.bin"
    return clean


class ArtifactStorageBackend(abc.ABC):
    """Abstract interface for artifact physical storage."""

    @abc.abstractmethod
    def store_bytes(
        self,
        storage_key: str,
        data: bytes,
        expected_sha256: Optional[str] = None,
        max_size_bytes: int = DEFAULT_MAX_ARTIFACT_SIZE,
    ) -> Dict[str, Any]:
        """Store bytes atomically under storage_key and return metadata."""
        pass

    @abc.abstractmethod
    def read_bytes(self, storage_key: str) -> bytes:
        """Read all bytes for the given storage_key."""
        pass

    @abc.abstractmethod
    def exists(self, storage_key: str) -> bool:
        """Check if artifact physical file exists."""
        pass

    @abc.abstractmethod
    def get_real_path(self, storage_key: str) -> Path:
        """Return the verified local filesystem path for authorized streaming/reading."""
        pass

    @abc.abstractmethod
    def verify_integrity(self, storage_key: str, expected_sha256: str) -> Tuple[bool, str]:
        """Verify checksum integrity of stored artifact."""
        pass

    @abc.abstractmethod
    def delete(self, storage_key: str) -> bool:
        """Delete stored physical file."""
        pass


class LocalFilesystemStorageBackend(ArtifactStorageBackend):
    """
    Local filesystem storage with atomic writes and path confinement.
    Confinement roots: settings.artifacts_dir (primary) and settings.evidence_dir (legacy/shared).
    """

    def __init__(self, base_dir: Optional[Path] = None, allowed_roots: Optional[list[Path]] = None):
        self.base_dir = (base_dir or settings.artifacts_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.allowed_roots = [self.base_dir]
        if allowed_roots:
            self.allowed_roots.extend([p.resolve() for p in allowed_roots])
        else:
            self.allowed_roots.append(settings.evidence_dir.resolve())
            self.allowed_roots.append(settings.data_dir.resolve())

    def _resolve_safe_path(self, storage_key: str, must_exist: bool = False) -> Path:
        """Resolve storage_key ensuring it stays confined within allowed roots."""
        # Normalize key
        norm_key = storage_key.replace("\\", "/").strip("/")
        
        # If absolute path was given (e.g. from historical metadata)
        p = Path(norm_key)
        if p.is_absolute():
            cand = p.resolve()
        else:
            cand = (self.base_dir / norm_key).resolve()

        # Check confinement
        is_confined = any(
            cand == root or cand.is_relative_to(root)
            for root in self.allowed_roots
        )
        if not is_confined:
            raise PermissionError(f"Access denied: storage key '{storage_key}' escapes authorized artifact roots.")

        # Reject path pointing directly to sensitive files
        name = cand.name.lower()
        if name.endswith(".db") or name.endswith(".env") or name == "secret_key" or "qwen_collective" in name:
            raise PermissionError(f"Access denied: target '{cand.name}' is a protected system resource.")

        if must_exist and not cand.is_file():
            raise FileNotFoundError(f"Artifact storage file not found: {storage_key}")

        return cand

    def store_bytes(
        self,
        storage_key: str,
        data: bytes,
        expected_sha256: Optional[str] = None,
        max_size_bytes: int = DEFAULT_MAX_ARTIFACT_SIZE,
    ) -> Dict[str, Any]:
        """Atomically write bytes to storage_key with size and checksum validation."""
        if len(data) > max_size_bytes:
            raise ValueError(f"Artifact exceeds maximum permitted size of {max_size_bytes} bytes (received {len(data)}).")

        computed_sha256 = hashlib.sha256(data).hexdigest()
        if expected_sha256 and expected_sha256.strip().lower() != computed_sha256:
            raise ValueError(
                f"Checksum mismatch: expected '{expected_sha256.strip().lower()}', computed '{computed_sha256}'."
            )

        target_path = self._resolve_safe_path(storage_key, must_exist=False)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write via temporary file in the same directory
        temp_fd, temp_path_str = tempfile.mkstemp(
            prefix=".art_upload_",
            suffix=".tmp",
            dir=str(target_path.parent),
        )
        temp_path = Path(temp_path_str)
        try:
            with os.fdopen(temp_fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())

            # Atomic replace
            temp_path.replace(target_path)
        except Exception:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass
            raise

        logger.info(
            "Stored artifact at %s (%d bytes, sha256=%s)",
            target_path.name,
            len(data),
            computed_sha256,
        )

        # Generate relative storage_key for portability if inside base_dir
        try:
            rel_key = str(target_path.relative_to(self.base_dir)).replace("\\", "/")
        except ValueError:
            rel_key = str(target_path.resolve()).replace("\\", "/")

        return {
            "storage_backend": "local_filesystem",
            "storage_key": rel_key,
            "resolved_path": str(target_path),
            "size_bytes": len(data),
            "checksum_sha256": computed_sha256,
        }

    def read_bytes(self, storage_key: str) -> bytes:
        p = self._resolve_safe_path(storage_key, must_exist=True)
        return p.read_bytes()

    def exists(self, storage_key: str) -> bool:
        try:
            p = self._resolve_safe_path(storage_key, must_exist=False)
            return p.is_file()
        except (PermissionError, FileNotFoundError):
            return False

    def get_real_path(self, storage_key: str) -> Path:
        return self._resolve_safe_path(storage_key, must_exist=True)

    def verify_integrity(self, storage_key: str, expected_sha256: str) -> Tuple[bool, str]:
        p = self._resolve_safe_path(storage_key, must_exist=True)
        data = p.read_bytes()
        computed = hashlib.sha256(data).hexdigest()
        matches = computed.lower() == expected_sha256.strip().lower()
        return matches, computed

    def delete(self, storage_key: str) -> bool:
        try:
            p = self._resolve_safe_path(storage_key, must_exist=True)
            p.unlink()
            return True
        except (FileNotFoundError, PermissionError):
            return False


# Default singleton instance
storage_backend = LocalFilesystemStorageBackend()
