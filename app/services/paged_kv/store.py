import hashlib
import hmac
import os
import re
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple, Union

# Envelope format QCKVP2 (186 bytes):
# 6s: magic (b"QCKVP2")
# H: version (uint16 = 2)
# H: flags (uint16: bit 0 = zlib compressed, bit 1 = HMAC authenticated)
# 64s: block_hash key (ASCII padded with \x00)
# 32s: namespace_digest (32 raw bytes)
# Q: orig_uncompressed_length (uint64)
# Q: comp_payload_length (uint64)
# 32s: payload_sha256 (32 raw bytes)
# 32s: auth_hmac (32 raw bytes HMAC-SHA256 over preceding 154 bytes)
HEADER_FORMAT = ">6sHH64s32sQQ32s32s"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
MAGIC_BYTES = b"QCKVP2"
V1_MAGIC_BYTES = b"QCKVP1"
MAX_PAGE_BYTES = 64 * 1024 * 1024  # 64 MB ceiling per block page


def _ns_to_bytes(ns: str) -> bytes:
    """Converts a namespace digest string (64-char hex or arbitrary string) to 32 raw bytes."""
    if len(ns) == 64:
        try:
            return bytes.fromhex(ns)
        except ValueError:
            pass
    return hashlib.sha256(ns.encode("utf-8")).digest()


@dataclass
class RamCacheEntry:
    tensor_bytes: bytes
    namespace_digest: str
    block_hash: str
    payload_sha: bytes


class PagedKVStore:
    """
    Tiered physical page storage for KV cache tensors.
    Stores pages in-memory (Tier 1) and offloads to disk (Tier 2).
    Enforces content-addressable storage with cryptographic envelope integrity verification,
    HMAC authentication, caller identity binding, bounded decompression, atomic file replacement,
    and path validation.
    """

    HEADER_SIZE = HEADER_SIZE
    MAGIC_BYTES = MAGIC_BYTES

    def __init__(
        self,
        storage_dir: Union[str, Path],
        compress: bool = True,
        auth_key: Optional[bytes] = None,
    ):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.compress = compress
        # Storage envelope authentication key (HMAC-SHA256)
        raw_key = auth_key or os.environ.get("PAGED_KV_STORAGE_KEY", "").encode("utf-8")
        self.auth_key = raw_key if raw_key else b"qc_internal_storage_key_v2_f89c1a"
        # In-memory RAM cache: block_hash -> RamCacheEntry
        self._ram_cache: Dict[str, RamCacheEntry] = {}
        self.current_ram_bytes: int = 0

    def _validate_hash(self, block_hash: str) -> None:
        """Prevents path traversal and injection via malformed block hashes."""
        if not block_hash or not re.match(r"^[a-fA-F0-9_-]{8,128}$", block_hash):
            raise ValueError(f"Invalid block hash identifier: {block_hash!r}")

    def _get_disk_path(self, block_hash: str) -> Path:
        self._validate_hash(block_hash)
        prefix = block_hash[:2]
        folder = self.storage_dir / prefix
        folder.mkdir(parents=True, exist_ok=True)
        ext = ".bin.z" if self.compress else ".bin"
        return folder / f"{block_hash}{ext}"

    def write_page(
        self,
        block_hash: str,
        tensor_bytes: bytes,
        persist_to_disk: bool = True,
        namespace_digest: str = "ns_unbound",
    ) -> int:
        """
        Writes a KV page tensor to Tier 1 RAM and optionally flushes to Tier 2 disk.
        Binds envelope integrity to uncompressed SHA-256 digest, key identity, and execution namespace.
        Returns the byte size of the raw uncompressed tensor.
        """
        self._validate_hash(block_hash)
        payload_sha = hashlib.sha256(tensor_bytes).digest()

        old_val = self._ram_cache.get(block_hash)
        if old_val is not None:
            self.current_ram_bytes -= len(old_val.tensor_bytes)

        self._ram_cache[block_hash] = RamCacheEntry(
            tensor_bytes=tensor_bytes,
            namespace_digest=namespace_digest,
            block_hash=block_hash,
            payload_sha=payload_sha,
        )
        self.current_ram_bytes += len(tensor_bytes)

        if persist_to_disk:
            disk_path = self._get_disk_path(block_hash)

            if self.compress:
                disk_payload = zlib.compress(tensor_bytes, level=6)
                flags = 1 | 2  # bit 0: compressed, bit 1: authenticated
            else:
                disk_payload = tensor_bytes
                flags = 2  # bit 1: authenticated

            hash_bytes = block_hash.encode("ascii")[:64].ljust(64, b"\x00")
            ns_bytes = _ns_to_bytes(namespace_digest)
            orig_len = len(tensor_bytes)
            comp_len = len(disk_payload)

            header_prefix = struct.pack(
                ">6sHH64s32sQQ32s",
                MAGIC_BYTES,
                2,  # Version 2
                flags,
                hash_bytes,
                ns_bytes,
                orig_len,
                comp_len,
                payload_sha,
            )
            auth_hmac = hmac.new(self.auth_key, header_prefix, hashlib.sha256).digest()
            header = header_prefix + auth_hmac
            assert len(header) == HEADER_SIZE

            temp_path = disk_path.with_suffix(".tmp")
            with open(temp_path, "wb") as f:
                f.write(header)
                f.write(disk_payload)
            os.replace(temp_path, disk_path)

        return len(tensor_bytes)

    def read_page(
        self,
        block_hash: str,
        expected_namespace_digest: Optional[str] = None,
        expected_payload_sha: Optional[bytes] = None,
    ) -> Optional[bytes]:
        """
        Reads a page tensor from Tier 1 RAM if present, otherwise fetches from Tier 2 disk.
        Verifies cryptographic integrity against header digest on disk retrieval before returning.
        Also validates that the stored page matches the caller's expected block hash and namespace.
        """
        self._validate_hash(block_hash)
        if block_hash in self._ram_cache:
            entry = self._ram_cache.pop(block_hash)
            self._ram_cache[block_hash] = entry

            if expected_namespace_digest is not None:
                expected_ns_b = _ns_to_bytes(expected_namespace_digest)
                stored_ns_b = _ns_to_bytes(entry.namespace_digest)
                if stored_ns_b != expected_ns_b:
                    raise ValueError(
                        f"RAM cache namespace mismatch for {block_hash}: "
                        f"expected {expected_namespace_digest}, found {entry.namespace_digest}"
                    )
            if expected_payload_sha is not None and entry.payload_sha != expected_payload_sha:
                raise ValueError(f"RAM cache payload digest mismatch for {block_hash}")

            return entry.tensor_bytes

        disk_path = self._get_disk_path(block_hash)
        if not disk_path.exists():
            return None

        file_size = disk_path.stat().st_size
        if file_size > MAX_PAGE_BYTES:
            raise ValueError(f"Page file exceeds maximum allowable size: {file_size} > {MAX_PAGE_BYTES}")
        if file_size < HEADER_SIZE:
            if file_size >= 6:
                with open(disk_path, "rb") as f:
                    if f.read(6) == V1_MAGIC_BYTES:
                        raise ValueError(f"Deprecated or unsupported stored page version 1 for {block_hash}")
            raise ValueError(f"Corrupt page envelope for {block_hash}: file size {file_size} < header {HEADER_SIZE}")

        with open(disk_path, "rb") as f:
            raw = f.read(MAX_PAGE_BYTES + 1)

        if len(raw) > MAX_PAGE_BYTES:
            raise ValueError("Read exceeded maximum page limit")

        magic = raw[:6]
        if magic == V1_MAGIC_BYTES:
            raise ValueError(f"Deprecated or unsupported stored page version 1 for {block_hash}")
        if magic != MAGIC_BYTES:
            raise ValueError(f"Invalid magic bytes in stored page {block_hash}: {magic!r}")

        magic, version, flags, stored_hash_bytes, stored_ns_bytes, orig_len, comp_len, expected_sha, stored_hmac = struct.unpack(
            HEADER_FORMAT, raw[:HEADER_SIZE]
        )

        if version != 2:
            raise ValueError(f"Unsupported stored page version {version} for {block_hash}")

        # Verify header HMAC authentication tag
        if flags & 2:
            header_prefix = raw[:HEADER_SIZE - 32]
            computed_hmac = hmac.new(self.auth_key, header_prefix, hashlib.sha256).digest()
            if not hmac.compare_digest(computed_hmac, stored_hmac):
                raise ValueError(f"Envelope authentication failed for {block_hash}: header HMAC mismatch")

        # Verify block hash (key identity)
        stored_hash = stored_hash_bytes.rstrip(b"\x00").decode("ascii", errors="replace")
        if stored_hash != block_hash:
            raise ValueError(
                f"Page substitution detected: stored block hash {stored_hash!r} != requested block hash {block_hash!r}"
            )

        # Verify namespace identity
        if expected_namespace_digest is not None:
            expected_ns_b = _ns_to_bytes(expected_namespace_digest)
            if stored_ns_bytes != expected_ns_b:
                raise ValueError(
                    f"Page namespace mismatch for {block_hash}: expected {expected_namespace_digest!r}, stored {stored_ns_bytes.hex()!r}"
                )

        if orig_len > MAX_PAGE_BYTES:
            raise ValueError(f"Declared uncompressed length exceeds safe maximum: {orig_len}")

        disk_payload = raw[HEADER_SIZE:]
        if len(disk_payload) != comp_len:
            raise ValueError(
                f"Page envelope length mismatch for {block_hash}: declared compressed length {comp_len}, actual payload bytes {len(disk_payload)}"
            )

        if flags & 1:  # Compressed
            try:
                decompressor = zlib.decompressobj()
                tensor_bytes = decompressor.decompress(disk_payload, max_length=orig_len + 1024)
            except Exception as e:
                raise ValueError(f"Decompression failed for stored page {block_hash}: {e}")
        else:
            tensor_bytes = disk_payload

        if len(tensor_bytes) != orig_len:
            raise ValueError(
                f"Page payload length mismatch for {block_hash}: expected {orig_len}, got {len(tensor_bytes)}"
            )

        # Cryptographic integrity check: verify tensor payload matches authentic SHA-256
        actual_sha = hashlib.sha256(tensor_bytes).digest()
        if actual_sha != expected_sha:
            raise ValueError(
                f"Stored page integrity check failed for {block_hash}: SHA-256 digest mismatch"
            )
        if expected_payload_sha is not None and actual_sha != expected_payload_sha:
            raise ValueError(
                f"Stored page payload digest does not match caller expected payload SHA-256 for {block_hash}"
            )

        self._ram_cache[block_hash] = RamCacheEntry(
            tensor_bytes=tensor_bytes,
            namespace_digest=stored_ns_bytes.hex(),
            block_hash=stored_hash,
            payload_sha=actual_sha,
        )
        self.current_ram_bytes += len(tensor_bytes)
        return tensor_bytes

    def evict_from_ram(self, block_hash: str) -> bool:
        """
        Evicts a page from Tier 1 RAM cache while ensuring it remains durable on Tier 2 disk.
        Returns True if evicted, False if not in RAM.
        """
        self._validate_hash(block_hash)
        if block_hash not in self._ram_cache:
            return False

        # Ensure page is on disk before discarding from RAM
        disk_path = self._get_disk_path(block_hash)
        if not disk_path.exists():
            entry = self._ram_cache[block_hash]
            self.write_page(
                block_hash,
                entry.tensor_bytes,
                persist_to_disk=True,
                namespace_digest=entry.namespace_digest,
            )

        evicted_entry = self._ram_cache.pop(block_hash)
        self.current_ram_bytes -= len(evicted_entry.tensor_bytes)
        return True

    def is_in_ram(self, block_hash: str) -> bool:
        return block_hash in self._ram_cache

    def is_on_disk(self, block_hash: str) -> bool:
        return self._get_disk_path(block_hash).exists()

    def get_ram_page_count(self) -> int:
        return len(self._ram_cache)

    def get_ram_bytes(self) -> int:
        return self.current_ram_bytes

    def delete_page(self, block_hash: str) -> bool:
        """Removes a page from both RAM and disk."""
        self._validate_hash(block_hash)
        existed = False
        if block_hash in self._ram_cache:
            entry = self._ram_cache.pop(block_hash)
            self.current_ram_bytes -= len(entry.tensor_bytes)
            existed = True

        disk_path = self._get_disk_path(block_hash)
        if disk_path.exists():
            disk_path.unlink()
            existed = True

        return existed


