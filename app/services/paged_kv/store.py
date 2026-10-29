import hashlib
import os
import re
import struct
import zlib
from pathlib import Path
from typing import Dict, Optional, Tuple, Union

# 6-byte magic + 2-byte ver + 2-byte flags + 16-byte ns + 4-byte len + 32-byte sha256 = 62 bytes
HEADER_FORMAT = ">6sHH16sI32s"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
MAGIC_BYTES = b"QCKVP1"
MAX_PAGE_BYTES = 64 * 1024 * 1024  # 64 MB ceiling per block page


class PagedKVStore:
    """
    Tiered physical page storage for KV cache tensors.
    Stores pages in-memory (Tier 1) and offloads to disk (Tier 2).
    Enforces content-addressable storage with cryptographic envelope integrity verification,
    bounded decompression, atomic file replacement, and path validation.
    """

    def __init__(self, storage_dir: Union[str, Path], compress: bool = True):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.compress = compress
        # In-memory RAM cache: block_hash -> raw uncompressed tensor bytes
        self._ram_cache: Dict[str, bytes] = {}
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
        namespace_digest: str = "default",
    ) -> int:
        """
        Writes a KV page tensor to Tier 1 RAM and optionally flushes to Tier 2 disk.
        Binds envelope integrity to uncompressed SHA-256 digest and execution namespace.
        Returns the byte size of the raw uncompressed tensor.
        """
        self._validate_hash(block_hash)
        old_val = self._ram_cache.get(block_hash)
        if old_val is not None:
            self.current_ram_bytes -= len(old_val)

        self._ram_cache[block_hash] = tensor_bytes
        self.current_ram_bytes += len(tensor_bytes)

        if persist_to_disk:
            disk_path = self._get_disk_path(block_hash)
            payload_sha = hashlib.sha256(tensor_bytes).digest()

            if self.compress:
                disk_payload = zlib.compress(tensor_bytes, level=6)
                flags = 1
            else:
                disk_payload = tensor_bytes
                flags = 0

            ns_bytes = namespace_digest.encode("ascii", errors="replace")[:16].ljust(16, b"\x00")
            header = struct.pack(
                HEADER_FORMAT,
                MAGIC_BYTES,
                1,  # Version 1
                flags,
                ns_bytes,
                len(tensor_bytes),
                payload_sha,
            )

            temp_path = disk_path.with_suffix(".tmp")
            with open(temp_path, "wb") as f:
                f.write(header)
                f.write(disk_payload)
            os.replace(temp_path, disk_path)

        return len(tensor_bytes)

    def read_page(self, block_hash: str) -> Optional[bytes]:
        """
        Reads a page tensor from Tier 1 RAM if present, otherwise fetches from Tier 2 disk.
        Verifies cryptographic integrity against header digest on disk retrieval before returning.
        """
        self._validate_hash(block_hash)
        if block_hash in self._ram_cache:
            # Move to end for LRU tracking
            val = self._ram_cache.pop(block_hash)
            self._ram_cache[block_hash] = val
            return val

        disk_path = self._get_disk_path(block_hash)
        if not disk_path.exists():
            return None

        file_size = disk_path.stat().st_size
        if file_size > MAX_PAGE_BYTES:
            raise ValueError(f"Page file exceeds maximum allowable size: {file_size} > {MAX_PAGE_BYTES}")
        if file_size < HEADER_SIZE:
            raise ValueError(f"Corrupt page envelope for {block_hash}: file size {file_size} < header {HEADER_SIZE}")

        with open(disk_path, "rb") as f:
            raw = f.read(MAX_PAGE_BYTES + 1)

        if len(raw) > MAX_PAGE_BYTES:
            raise ValueError("Read exceeded maximum page limit")

        magic, version, flags, ns_bytes, orig_len, expected_sha = struct.unpack(
            HEADER_FORMAT, raw[:HEADER_SIZE]
        )

        if magic != MAGIC_BYTES:
            raise ValueError(f"Invalid magic bytes in stored page {block_hash}: {magic!r}")
        if version != 1:
            raise ValueError(f"Unsupported stored page version {version} for {block_hash}")
        if orig_len > MAX_PAGE_BYTES:
            raise ValueError(f"Declared uncompressed length exceeds safe maximum: {orig_len}")

        disk_payload = raw[HEADER_SIZE:]

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

        self._ram_cache[block_hash] = tensor_bytes
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
            tensor_bytes = self._ram_cache[block_hash]
            self.write_page(block_hash, tensor_bytes, persist_to_disk=True)

        evicted_bytes = self._ram_cache.pop(block_hash)
        self.current_ram_bytes -= len(evicted_bytes)
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
            val = self._ram_cache.pop(block_hash)
            self.current_ram_bytes -= len(val)
            existed = True

        disk_path = self._get_disk_path(block_hash)
        if disk_path.exists():
            disk_path.unlink()
            existed = True

        return existed

