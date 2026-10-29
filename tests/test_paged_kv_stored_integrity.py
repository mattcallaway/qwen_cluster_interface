import tempfile
from pathlib import Path
import pytest
from app.services.paged_kv.store import PagedKVStore


def test_reproduce_tampered_disk_page_returned_without_verification():
    """
    Reproduction test for Material Finding 5 / probe tampered_disk_read:
    Writing a page, evicting it from RAM, modifying its disk file to corrupt/tamper bytes,
    and then reading it back must NOT return the tampered bytes!
    It MUST reject the corrupted page (raise ValueError or integrity error) and return None/error.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Uncompressed or compressed
        store = PagedKVStore(storage_dir=Path(tmpdir), compress=False)
        block_hash = "c" * 64
        original_payload = b"GENUINE_KV_TENSOR_BYTES_PAYLOAD"

        store.write_page(block_hash, original_payload, persist_to_disk=True)
        # Evict from RAM so read_page must read from disk
        store.evict_from_ram(block_hash)
        assert not store.is_in_ram(block_hash)

        # Tamper with the disk file
        disk_path = store._get_disk_path(block_hash)
        assert disk_path.exists()
        with open(disk_path, "wb") as f:
            f.write(b"tampered")

        # Attempt to read the page
        # Expected: Fails integrity check (raises ValueError safely without exposing tampered bytes)
        with pytest.raises(ValueError, match="Corrupt page envelope|integrity check failed"):
            store.read_page(block_hash)


def test_payload_bit_flip_tampering_rejected():
    """
    Modifying a single byte inside the serialized payload must trigger a SHA-256 digest failure.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        store = PagedKVStore(storage_dir=Path(tmpdir), compress=False)
        block_hash = "d" * 64
        original_payload = b"X" * 128

        store.write_page(block_hash, original_payload, persist_to_disk=True)
        store.evict_from_ram(block_hash)

        disk_path = store._get_disk_path(block_hash)
        raw = bytearray(disk_path.read_bytes())
        # Flip bit in payload area (past 62-byte header)
        raw[70] ^= 0x01
        disk_path.write_bytes(bytes(raw))

        with pytest.raises(ValueError, match="SHA-256 digest mismatch"):
            store.read_page(block_hash)


def test_path_traversal_rejected_fail_closed():
    """
    Malformed hashes attempting directory traversal must be rejected immediately.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        store = PagedKVStore(storage_dir=Path(tmpdir), compress=True)

        with pytest.raises(ValueError, match="Invalid block hash identifier"):
            store.write_page("../../../etc/passwd", b"bad", persist_to_disk=True)

        with pytest.raises(ValueError, match="Invalid block hash identifier"):
            store.read_page("../../../etc/passwd")


def test_corrupt_magic_bytes_rejected():
    """
    Pages with corrupted magic bytes must fail closed.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        store = PagedKVStore(storage_dir=Path(tmpdir), compress=False)
        block_hash = "e" * 64
        store.write_page(block_hash, b"valid payload", persist_to_disk=True)
        store.evict_from_ram(block_hash)

        disk_path = store._get_disk_path(block_hash)
        raw = bytearray(disk_path.read_bytes())
        raw[0:6] = b"BADMAG"
        disk_path.write_bytes(bytes(raw))

        with pytest.raises(ValueError, match="Invalid magic bytes"):
            store.read_page(block_hash)

