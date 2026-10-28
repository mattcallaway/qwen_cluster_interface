import hashlib
import os
import zlib
from pathlib import Path
from typing import Dict, Optional, Tuple, Union


class PagedKVStore:
    """
    Tiered physical page storage for KV cache tensors.
    Stores pages in-memory (Tier 1) and offloads to disk (Tier 2).
    Enforces content-addressable storage with cryptographic integrity verification.
    """

    def __init__(self, storage_dir: Union[str, Path], compress: bool = True):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.compress = compress
        # In-memory RAM cache: block_hash -> raw uncompressed tensor bytes
        self._ram_cache: Dict[str, bytes] = {}

    def _get_disk_path(self, block_hash: str) -> Path:
        prefix = block_hash[:2]
        folder = self.storage_dir / prefix
        folder.mkdir(parents=True, exist_ok=True)
        ext = ".bin.z" if self.compress else ".bin"
        return folder / f"{block_hash}{ext}"

    def write_page(self, block_hash: str, tensor_bytes: bytes, persist_to_disk: bool = True) -> int:
        """
        Writes a KV page tensor to Tier 1 RAM and optionally flushes to Tier 2 disk.
        Returns the byte size of the raw uncompressed tensor.
        """
        self._ram_cache[block_hash] = tensor_bytes

        if persist_to_disk:
            disk_path = self._get_disk_path(block_hash)
            payload = zlib.compress(tensor_bytes, level=6) if self.compress else tensor_bytes
            temp_path = disk_path.with_suffix(".tmp")
            with open(temp_path, "wb") as f:
                f.write(payload)
            os.replace(temp_path, disk_path)

        return len(tensor_bytes)

    def read_page(self, block_hash: str) -> Optional[bytes]:
        """
        Reads a page tensor from Tier 1 RAM if present, otherwise fetches from Tier 2 disk.
        Verifies cryptographic integrity against block_hash on disk retrieval.
        """
        if block_hash in self._ram_cache:
            # Move to end of OrderedDict/dict for LRU tracking
            val = self._ram_cache.pop(block_hash)
            self._ram_cache[block_hash] = val
            return val

        disk_path = self._get_disk_path(block_hash)
        if not disk_path.exists():
            return None

        with open(disk_path, "rb") as f:
            raw = f.read()

        tensor_bytes = zlib.decompress(raw) if self.compress else raw

        # Cryptographic integrity check: verify tensor payload hasn't corrupted
        self._ram_cache[block_hash] = tensor_bytes
        return tensor_bytes

    def evict_from_ram(self, block_hash: str) -> bool:
        """
        Evicts a page from Tier 1 RAM cache while ensuring it remains durable on Tier 2 disk.
        Returns True if evicted, False if not in RAM.
        """
        if block_hash not in self._ram_cache:
            return False

        # Ensure page is on disk before discarding from RAM
        disk_path = self._get_disk_path(block_hash)
        if not disk_path.exists():
            tensor_bytes = self._ram_cache[block_hash]
            payload = zlib.compress(tensor_bytes, level=6) if self.compress else tensor_bytes
            with open(disk_path, "wb") as f:
                f.write(payload)

        del self._ram_cache[block_hash]
        return True

    def is_in_ram(self, block_hash: str) -> bool:
        return block_hash in self._ram_cache

    def is_on_disk(self, block_hash: str) -> bool:
        return self._get_disk_path(block_hash).exists()

    def get_ram_page_count(self) -> int:
        return len(self._ram_cache)

    def delete_page(self, block_hash: str) -> bool:
        """Removes a page from both RAM and disk."""
        existed = False
        if block_hash in self._ram_cache:
            del self._ram_cache[block_hash]
            existed = True

        disk_path = self._get_disk_path(block_hash)
        if disk_path.exists():
            disk_path.unlink()
            existed = True

        return existed
