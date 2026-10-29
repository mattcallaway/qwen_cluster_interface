import hashlib
import hmac
import os
import struct
import tempfile
import zlib
from pathlib import Path

import pytest

from app.services.paged_kv.manager import PagedKVManager
from app.services.paged_kv.store import (
    HEADER_FORMAT,
    HEADER_SIZE,
    MAGIC_BYTES,
    PagedKVStore,
)

try:
    from app.services.paged_kv.store import FLAG_AUTHENTICATED, FLAG_COMPRESSED_ZLIB
except ImportError:
    FLAG_COMPRESSED_ZLIB = 1
    FLAG_AUTHENTICATED = 2

try:
    from app.services.paged_kv.hasher import (
        ExecutionNamespace,
        ModelIdentityManifest,
        PagedKVHasher,
    )
except ImportError:
    from app.services.paged_kv.hasher import ExecutionNamespace, PagedKVHasher
    ModelIdentityManifest = None  # type: ignore


@pytest.fixture
def ephemeral_test_key():
    """Generates an independent, protected 256-bit test key fixture."""
    return os.urandom(32)


# ---------------------------------------------------------------------------
# Requirement 2: Eliminate authentication downgrade
# ---------------------------------------------------------------------------


def test_auth_flag_downgrade_with_altered_payload_rejected(ephemeral_test_key):
    """
    Reproduces defect: clearing FLAG_AUTHENTICATED in the page header must NOT
    bypass HMAC verification. Even with an altered payload and updated unkeyed
    checksum, reading the downgraded page must fail closed with an authentication error.
    """
    with tempfile.TemporaryDirectory() as td:
        store = PagedKVStore(storage_dir=td, compress=True, auth_key=ephemeral_test_key)
        block_hash = "a" * 64
        store.write_page(block_hash, b"LEGITIMATE_PAGE_PAYLOAD", persist_to_disk=True, namespace_digest="ns_alpha")
        store._ram_cache.clear()

        disk_path = store._get_disk_path(block_hash)
        with open(disk_path, "rb") as f:
            raw_data = bytearray(f.read())

        magic, ver, flags, bh, ns, orig_len, comp_len, sha, hmac_tag = struct.unpack(
            HEADER_FORMAT, raw_data[:HEADER_SIZE]
        )
        assert flags & FLAG_AUTHENTICATED, "Original page must have FLAG_AUTHENTICATED set"

        # Craft malicious payload with matching unkeyed SHA-256 and clear FLAG_AUTHENTICATED
        evil_payload = b"EVIL_SUBSTITUTED_PAYLOAD_UNAUTHORIZED"
        evil_comp = zlib.compress(evil_payload)
        evil_sha = hashlib.sha256(evil_payload).digest()

        # Clear bit 1 (auth flag)
        downgraded_flags = flags & ~FLAG_AUTHENTICATED
        tampered_header = struct.pack(
            HEADER_FORMAT,
            magic,
            ver,
            downgraded_flags,
            bh,
            ns,
            len(evil_payload),
            len(evil_comp),
            evil_sha,
            hmac_tag,
        )

        with open(disk_path, "wb") as f:
            f.write(tampered_header + evil_comp)

        # Must fail closed: reader policy mandates authentication regardless of file flags
        with pytest.raises(ValueError, match="(?i)(authentication|unauthenticated|hmac)"):
            store.read_page(block_hash, expected_namespace_digest="ns_alpha")


def test_unknown_flag_combinations_rejected(ephemeral_test_key):
    """Stored page envelopes with unknown or unsupported flags must be rejected fail-closed."""
    with tempfile.TemporaryDirectory() as td:
        store = PagedKVStore(storage_dir=td, compress=False, auth_key=ephemeral_test_key)
        block_hash = "b" * 64
        store.write_page(block_hash, b"SAFE_DATA", persist_to_disk=True, namespace_digest="ns_beta")
        store._ram_cache.clear()

        disk_path = store._get_disk_path(block_hash)
        with open(disk_path, "rb") as f:
            raw_data = bytearray(f.read())

        magic, ver, flags, bh, ns, orig_len, comp_len, sha, hmac_tag = struct.unpack(
            HEADER_FORMAT, raw_data[:HEADER_SIZE]
        )
        # Inject an unknown flag bit (0x0080)
        corrupted_flags = flags | 0x0080
        header_prefix = struct.pack(
            ">6sHH64s32sQQ32s", magic, ver, corrupted_flags, bh, ns, orig_len, comp_len, sha
        )
        recomputed_hmac = hmac.new(ephemeral_test_key, header_prefix, hashlib.sha256).digest()
        tampered_header = header_prefix + recomputed_hmac

        with open(disk_path, "wb") as f:
            f.write(tampered_header + raw_data[HEADER_SIZE:])

        with pytest.raises(ValueError, match="(?i)unsupported.*flag"):
            store.read_page(block_hash, expected_namespace_digest="ns_beta")


def test_missing_or_zeroed_hmac_rejected(ephemeral_test_key):
    """An envelope with a zeroed authentication tag must be rejected."""
    with tempfile.TemporaryDirectory() as td:
        store = PagedKVStore(storage_dir=td, compress=False, auth_key=ephemeral_test_key)
        block_hash = "c" * 64
        store.write_page(block_hash, b"INTEGRITY_CHECK", persist_to_disk=True, namespace_digest="ns_gamma")
        store._ram_cache.clear()

        disk_path = store._get_disk_path(block_hash)
        with open(disk_path, "rb") as f:
            raw_data = bytearray(f.read())

        # Zero out the HMAC tag (last 32 bytes of 186-byte header)
        raw_data[HEADER_SIZE - 32 : HEADER_SIZE] = b"\x00" * 32
        with open(disk_path, "wb") as f:
            f.write(raw_data)

        with pytest.raises(ValueError, match="(?i)authentication failed"):
            store.read_page(block_hash, expected_namespace_digest="ns_gamma")


def test_independent_secret_forged_file_fails(ephemeral_test_key):
    """
    An adversary with an arbitrary chosen secret key creates a perfectly structured
    page envelope. The authentic store with an independent secret MUST reject it.
    """
    adversary_key = os.urandom(32)
    assert adversary_key != ephemeral_test_key

    with tempfile.TemporaryDirectory() as td:
        adv_store = PagedKVStore(storage_dir=td, compress=False, auth_key=adversary_key)
        block_hash = "d" * 64
        adv_store.write_page(block_hash, b"FORGED_PAYLOAD", persist_to_disk=True, namespace_digest="ns_delta")

        # Legitimate store opens the directory
        legit_store = PagedKVStore(storage_dir=td, compress=False, auth_key=ephemeral_test_key)
        with pytest.raises(ValueError, match="(?i)authentication failed"):
            legit_store.read_page(block_hash, expected_namespace_digest="ns_delta")


# ---------------------------------------------------------------------------
# Requirement 3: Remove the hardcoded fallback key
# ---------------------------------------------------------------------------


def test_unconfigured_store_fails_closed_without_hardcoded_key(monkeypatch):
    """
    When PAGED_KV_STORAGE_KEY is not set and no auth_key is passed,
    PagedKVStore must fail closed with a configuration error rather than
    silently defaulting to a hardcoded key.
    """
    monkeypatch.delenv("PAGED_KV_STORAGE_KEY", raising=False)
    with tempfile.TemporaryDirectory() as td:
        with pytest.raises(ValueError, match="(?i)(missing storage authentication key|requires an explicit)"):
            PagedKVStore(storage_dir=td)


def test_insufficient_key_length_rejected():
    """Keys shorter than 32 bytes (256 bits) must be strictly rejected."""
    with tempfile.TemporaryDirectory() as td:
        with pytest.raises(ValueError, match="(?i)(insufficient storage key length|at least 32 bytes)"):
            PagedKVStore(storage_dir=td, auth_key=b"too_short_key_16b")


def test_invalid_key_types_rejected():
    """Non-bytes / non-string key types must raise TypeError."""
    with tempfile.TemporaryDirectory() as td:
        with pytest.raises(TypeError, match="(?i)auth_key must be bytes or str"):
            PagedKVStore(storage_dir=td, auth_key=1234567890)


def test_key_rotation_and_wrong_key_behavior():
    """
    Pages stored under Key A cannot be verified with Key B.
    Access produces an explicit authentication failure and admits nothing into RAM.
    """
    key_a = os.urandom(32)
    key_b = os.urandom(32)

    with tempfile.TemporaryDirectory() as td:
        store_a = PagedKVStore(storage_dir=td, compress=False, auth_key=key_a)
        block_hash = "e" * 64
        store_a.write_page(block_hash, b"SECRET_TENANT_TENSOR", persist_to_disk=True, namespace_digest="ns_eps")
        store_a._ram_cache.clear()

        # New process or rotated instance uses Key B
        store_b = PagedKVStore(storage_dir=td, compress=False, auth_key=key_b)
        with pytest.raises(ValueError, match="(?i)authentication failed"):
            store_b.read_page(block_hash, expected_namespace_digest="ns_eps")

        # Verify nothing was admitted into RAM
        assert store_b.get_ram_page_count() == 0
        assert store_b.get_ram_bytes() == 0


# ---------------------------------------------------------------------------
# Requirement 4: Require verified runtime identities
# ---------------------------------------------------------------------------


def test_changed_weights_under_same_display_name_changes_namespace():
    """
    Two models sharing the display name 'qwen2.5:1.5b' but possessing different
    verified weight digests MUST produce distinct namespace digests.
    """
    manifest_v1 = ModelIdentityManifest(
        model_name="qwen2.5:1.5b",
        weight_digest="11" * 32,
        tokenizer_digest="aa" * 32,
        chat_template_digest="bb" * 32,
        kv_layout="bshd_fp16",
        rope_config_digest="cc" * 32,
        runtime_compatibility="qwen_collective_v2",
        tenant_id="tenant_alpha",
    )
    manifest_v2 = ModelIdentityManifest(
        model_name="qwen2.5:1.5b",
        weight_digest="22" * 32,  # Weights updated!
        tokenizer_digest="aa" * 32,
        chat_template_digest="bb" * 32,
        kv_layout="bshd_fp16",
        rope_config_digest="cc" * 32,
        runtime_compatibility="qwen_collective_v2",
        tenant_id="tenant_alpha",
    )

    ns1 = ExecutionNamespace.from_manifest(manifest_v1)
    ns2 = ExecutionNamespace.from_manifest(manifest_v2)

    assert ns1.compute_digest() != ns2.compute_digest()
    assert ns1.is_verified_for_reuse is True
    assert ns2.is_verified_for_reuse is True


def test_prototype_simulation_cannot_authorize_genuine_reuse():
    """
    Namespaces created for prototype simulation must have identity_scope='prototype_simulation'
    and is_verified_for_reuse=False, isolating them from verified production reuse.
    """
    sim_ns = ExecutionNamespace.create_prototype_simulation(model_name="test_sim_model")
    assert sim_ns.identity_scope == "prototype_simulation"
    assert sim_ns.is_verified_for_reuse is False

    # Attempting to derive a verified namespace without a manifest must fail
    with pytest.raises(ValueError, match="(?i)(manifest|verified)"):
        ExecutionNamespace.from_manifest(None)


def test_tampered_manifest_digests_rejected():
    """Manifest digests that are not 64-character hexadecimal SHA-256 strings must be rejected."""
    with pytest.raises(ValueError, match="(?i)(digest|64-character hex)"):
        ModelIdentityManifest(
            model_name="qwen2.5:1.5b",
            weight_digest="invalid_short_hash",
            tokenizer_digest="aa" * 32,
            chat_template_digest="bb" * 32,
            kv_layout="bshd_fp16",
            rope_config_digest="cc" * 32,
            runtime_compatibility="qwen_collective_v2",
        )


# ---------------------------------------------------------------------------
# Requirement 5: Audit storage and cache interactions
# ---------------------------------------------------------------------------


def test_ram_cache_corruption_purges_entry_and_adjusts_bytes(ephemeral_test_key):
    """
    If a tensor in RAM cache is modified in-place, read_page must detect the payload
    digest mismatch, purge the corrupt entry from RAM, and decrement RAM byte accounting.
    """
    with tempfile.TemporaryDirectory() as td:
        store = PagedKVStore(storage_dir=td, compress=False, auth_key=ephemeral_test_key)
        block_hash = "f" * 64
        initial_tensor = bytearray(b"ORIGINAL_CLEAN_TENSOR_BYTES_12345")
        store.write_page(block_hash, bytes(initial_tensor), persist_to_disk=False, namespace_digest="ns_clean")

        initial_ram_bytes = store.get_ram_bytes()
        assert initial_ram_bytes == len(initial_tensor)
        assert store.get_ram_page_count() == 1

        # Simulate in-memory corruption
        entry = store._ram_cache[block_hash]
        entry.tensor_bytes = b"CORRUPTED_IN_MEMORY_TENSOR_BYTES!"

        with pytest.raises(ValueError, match="(?i)RAM cache payload digest mismatch"):
            store.read_page(block_hash, expected_namespace_digest="ns_clean")

        # Corrupted entry must be evicted immediately and byte accounting adjusted
        assert block_hash not in store._ram_cache
        assert store.get_ram_page_count() == 0
        assert store.get_ram_bytes() == 0
