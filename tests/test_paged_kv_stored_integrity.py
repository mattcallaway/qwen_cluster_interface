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
        store = PagedKVStore(storage_dir=Path(tmpdir), auth_key=b"k" * 32, compress=False)
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
        store = PagedKVStore(storage_dir=Path(tmpdir), auth_key=b"k" * 32, compress=False)
        block_hash = "d" * 64
        original_payload = b"X" * 128

        store.write_page(block_hash, original_payload, persist_to_disk=True)
        store.evict_from_ram(block_hash)

        disk_path = store._get_disk_path(block_hash)
        raw = bytearray(disk_path.read_bytes())
        # Flip bit in payload area (past header)
        raw[store.HEADER_SIZE + 5] ^= 0x01
        disk_path.write_bytes(bytes(raw))

        with pytest.raises(ValueError, match="SHA-256 digest mismatch"):
            store.read_page(block_hash)


def test_header_tampering_hmac_rejected():
    """
    Modifying any byte inside the authenticated envelope header must fail HMAC verification.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        store = PagedKVStore(storage_dir=Path(tmpdir), auth_key=b"k" * 32, compress=False)
        block_hash = "f" * 64
        original_payload = b"GENUINE_AUTHENTICATED_PAYLOAD"

        store.write_page(block_hash, original_payload, persist_to_disk=True)
        store.evict_from_ram(block_hash)

        disk_path = store._get_disk_path(block_hash)
        raw = bytearray(disk_path.read_bytes())
        # Flip bit at offset 70 (inside stored block_hash in header)
        raw[70] ^= 0x01
        disk_path.write_bytes(bytes(raw))

        with pytest.raises(ValueError, match="Envelope authentication failed|header HMAC mismatch"):
            store.read_page(block_hash)


def test_deprecated_v1_envelope_rejected_fail_closed():
    """
    Compatibility policy: Deprecated QCKVP1 envelopes lacking cryptographic key binding
    must be rejected fail-closed, never silently accepted as valid QCKVP2 pages.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        store = PagedKVStore(storage_dir=Path(tmpdir), auth_key=b"k" * 32, compress=False)
        block_hash = "1" * 64
        disk_path = store._get_disk_path(block_hash)
        # Write legacy QCKVP1 mock envelope
        v1_data = b"QCKVP1" + b"\x00" * 100
        disk_path.write_bytes(v1_data)

        with pytest.raises(ValueError, match="Deprecated or unsupported stored page version 1"):
            store.read_page(block_hash)


def test_trailing_data_in_envelope_rejected():
    """
    Envelopes with extra trailing data past the declared payload must be rejected.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        store = PagedKVStore(storage_dir=Path(tmpdir), auth_key=b"k" * 32, compress=False)
        block_hash = "2" * 64
        store.write_page(block_hash, b"bounded_payload", persist_to_disk=True)
        store.evict_from_ram(block_hash)

        disk_path = store._get_disk_path(block_hash)
        raw = disk_path.read_bytes() + b"TRAILING_UNAUTHORIZED_BYTES"
        disk_path.write_bytes(raw)

        with pytest.raises(ValueError, match="Page envelope length mismatch"):
            store.read_page(block_hash)


def test_truncated_envelope_rejected():
    """
    Envelopes truncated before completing declared payload must fail closed.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        store = PagedKVStore(storage_dir=Path(tmpdir), auth_key=b"k" * 32, compress=False)
        block_hash = "3" * 64
        store.write_page(block_hash, b"payload_to_truncate", persist_to_disk=True)
        store.evict_from_ram(block_hash)

        disk_path = store._get_disk_path(block_hash)
        raw = disk_path.read_bytes()
        disk_path.write_bytes(raw[:-5])  # Truncate 5 bytes

        with pytest.raises(ValueError, match="Page envelope length mismatch"):
            store.read_page(block_hash)



def test_path_traversal_rejected_fail_closed():
    """
    Malformed hashes attempting directory traversal must be rejected immediately.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        store = PagedKVStore(storage_dir=Path(tmpdir), auth_key=b"k" * 32, compress=True)

        with pytest.raises(ValueError, match="Invalid block hash identifier"):
            store.write_page("../../../etc/passwd", b"bad", persist_to_disk=True)

        with pytest.raises(ValueError, match="Invalid block hash identifier"):
            store.read_page("../../../etc/passwd")


def test_corrupt_magic_bytes_rejected():
    """
    Pages with corrupted magic bytes must fail closed.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        store = PagedKVStore(storage_dir=Path(tmpdir), auth_key=b"k" * 32, compress=False)
        block_hash = "e" * 64
        store.write_page(block_hash, b"valid payload", persist_to_disk=True)
        store.evict_from_ram(block_hash)

        disk_path = store._get_disk_path(block_hash)
        raw = bytearray(disk_path.read_bytes())
        raw[0:6] = b"BADMAG"
        disk_path.write_bytes(bytes(raw))

        with pytest.raises(ValueError, match="Invalid magic bytes"):
            store.read_page(block_hash)


def test_reproduce_valid_page_substitution_across_keys_and_namespaces():
    """
    Reproduction test for valid-page substitution (Finding 2 / probe valid_page_substitution):
    1. Write page A and page B with different keys and namespace identities.
    2. Evict both from RAM.
    3. Copy B's complete valid stored envelope over A's file.
    4. Read A.
    Expected: Reading A must detect that the stored page does NOT belong to A's expected identity,
    and MUST REJECT it (raise ValueError or fail verification), never returning B's payload!
    """
    import shutil

    with tempfile.TemporaryDirectory() as tmpdir:
        store = PagedKVStore(storage_dir=Path(tmpdir), auth_key=b"k" * 32, compress=False)
        hash_a = "a" * 64
        hash_b = "b" * 64
        payload_a = b"PAGE_A_GENUINE_TENSOR"
        payload_b = b"PAGE_B_SUBSTITUTE_TENSOR"
        ns_a = "ns_digest_aaaa_12345678"
        ns_b = "ns_digest_bbbb_87654321"

        store.write_page(hash_a, payload_a, persist_to_disk=True, namespace_digest=ns_a)
        store.write_page(hash_b, payload_b, persist_to_disk=True, namespace_digest=ns_b)

        store.evict_from_ram(hash_a)
        store.evict_from_ram(hash_b)

        path_a = store._get_disk_path(hash_a)
        path_b = store._get_disk_path(hash_b)
        assert path_a.exists() and path_b.exists()

        # Adversarially copy valid envelope B over A's file
        shutil.copyfile(path_b, path_a)

        # In unfixed code: read_page(hash_a) accepts B's envelope and returns payload_b!
        # In repaired implementation: read_page(hash_a) detects the identity mismatch and raises ValueError.
        with pytest.raises(ValueError, match="Page substitution detected|Page namespace mismatch"):
            store.read_page(hash_a, expected_namespace_digest=ns_a)



