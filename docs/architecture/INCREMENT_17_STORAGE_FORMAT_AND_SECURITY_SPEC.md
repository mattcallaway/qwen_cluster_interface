# Increment 17: Storage Format, Cache Identity, and Security Specification
## Document Identifier: `SPEC-INC17-PAGED-KV-V2.1`
**Version:** 2.1  
**Status:** Implemented & Verified  
**Date:** 2026-10-28  
**Scope:** `app/services/paged_kv` subsystem  

---

## 1. Overview & Threat Model

The Searchable Paged KV Cache persists fixed-size token KV tensors across Tier 1 RAM and Tier 2 disk. To guarantee cache safety and prevent unauthorized cross-request or cross-model tensor leakage, the storage layer must defend against accidental corruption, deliberate identity substitution, authentication downgrade, and unverified identity spoofing.

### Threat Model & Boundaries
1. **Accidental File Corruption:**
   - *Threat:* Bit flips, truncated disk writes, partial downloads, OS filesystem crashes.
   - *Defense:* Uncompressed SHA-256 payload checksum, declared length checks, atomic write-then-rename (`os.replace`) publishing.
2. **Identity & Substitution Attacks:**
   - *Threat:* An adversary or rogue process swaps valid page files across storage directories (e.g. copying valid Page B over Page A's path), replaying an old page, or forging headers.
   - *Defense:* 
     - Complete block key (`stored_hash`, 64 bytes) and full 32-byte execution namespace digest (`stored_ns`) are embedded into the envelope header.
     - The read API receives caller-trusted expected identities (`expected_block_hash`, `expected_namespace_digest`) and verifies them before exposing payload bytes.
     - The header is authenticated with an HMAC-SHA256 tag keyed by an internal secret storage key (`auth_key`). Any modification to header metadata (changing key or namespace) triggers immediate authentication rejection.
3. **Authentication Downgrade Attacks:**
   - *Threat:* An adversary alters a stored envelope payload and clears the authentication flag in the header (`flags & ~FLAG_AUTHENTICATED`) to bypass HMAC verification.
   - *Defense:* 
     - Reader policy unconditionally mandates authentication. `FLAG_AUTHENTICATED` (bit 1, value `0x0002`) is strictly required on every `QCKVP2` page envelope.
     - Any envelope with missing authentication flag or unknown flags (`flags & ~0x0003 != 0`) is rejected fail-closed.
     - Header HMAC-SHA256 is verified using constant-time comparison before any payload parsing or decompression begins.
4. **Decompression Bombs (Zip Bombs):**
   - *Threat:* Highly compressed payload that expands to gigabytes when decompressed.
   - *Defense:* Streaming decompression bounded by `max_length = orig_len + 1`, capped by `MAX_PAGE_BYTES = 64 MB`. Any trailing stream data or decompressed size mismatch is rejected fail-closed.
5. **Path Traversal & Injection:**
   - *Threat:* Malformed block hash containing `../` or `/` attempting directory traversal.
   - *Defense:* Strict regex validation (`^[a-fA-F0-9_-]{8,128}$`) applied to all hash inputs before filesystem operations.
6. **Unverified Model Identity Spoofing:**
   - *Threat:* Deriving revision identifiers from display names or static string profiles allows models with altered weights to share the same namespace.
   - *Defense:* Genuine cross-request KV reuse requires a verified `ModelIdentityManifest` binding actual 64-character SHA-256 weight digests, tokenizer vocabularies, chat templates, and RoPE configurations. Unverified or display-name configurations are restricted to isolated `prototype_simulation` namespaces and barred from genuine tensor reuse.
7. **Storage Authentication Limitations:**
   - Storage authentication protects against unauthorized tampering with stored files on disk, but does not substitute for kernel-level OS access controls. If an attacker gains raw root or process memory inspection capabilities, memory contents and active keys are compromised.

---

## 2. Key Management Policy

1. **No Hardcoded Fallback Keys:**
   - The storage subsystem strictly contains no hardcoded, default, shared, or source-visible authentication keys.
   - Initializing `PagedKVStore` or `PagedKVManager` without an explicit `auth_key` or `PAGED_KV_STORAGE_KEY` environment variable fails closed with `ValueError`.
2. **Key Requirements:**
   - Must be at least 32 bytes (256 bits for HMAC-SHA256).
   - Accepted as `bytes` or `str` (hex-encoded 64 characters or raw string $\ge 32$ chars). Shorter or malformed keys raise `ValueError` / `TypeError`.
3. **Key Ownership:**
   - The key is owned by the node-level environment or secret vault (`PAGED_KV_STORAGE_KEY`).
   - Ephemeral test fixtures in automated suites generate independent random 256-bit keys (`os.urandom(32)`) isolated per test run.
4. **Rotation and Recovery Policy:**
   - When the storage key is rotated or if a node restarts with a new key, previously stored pages cannot be authenticated and will fail header HMAC checks.
   - Under this policy, unverifiable pages are treated as cache misses, triggering token recomputation rather than loading unverified cache data. Production caches are never automatically migrated with downgraded checks.

---

## 3. Model Identity Manifest & Execution Namespace

### `ModelIdentityManifest`
To establish immutable model identity for genuine KV reuse, the manifest binds:
- `model_name`: Display string (e.g. `qwen2.5:1.5b`)
- `weight_digest`: 64-character hexadecimal SHA-256 hash of immutable weights
- `tokenizer_digest`: 64-character hexadecimal SHA-256 hash of tokenizer vocabulary and config
- `chat_template_digest`: 64-character hexadecimal SHA-256 hash of chat template
- `kv_layout`: Memory layout & dtype identifier (e.g. `bshd_fp16`)
- `rope_config_digest`: 64-character hexadecimal SHA-256 hash of RoPE/positional configuration
- `runtime_compatibility`: Runtime architecture version (e.g. `qwen_collective_v2`)
- `tenant_id`: Tenant or security scope (default `tenant_local`)

### `ExecutionNamespace` Schema (`ExecutionNamespace/1.0`)
Deterministic JSON serialization (RFC 8785 principles: sorted keys, no whitespace, UTF-8):
```json
{
  "schema": "ExecutionNamespace/1.0",
  "fields": {
    "chat_template_hash": "<str: 1..256 chars>",
    "identity_scope": "verified_runtime | prototype_simulation",
    "kv_layout": "<str: 1..256 chars>",
    "model_name": "<str: 1..256 chars>",
    "model_revision": "<str: 1..256 chars>",
    "rope_config_hash": "<str: 1..256 chars>",
    "runtime_compatibility": "<str: 1..256 chars>",
    "tenant_id": "<str: 1..256 chars>",
    "tokenizer_id": "<str: 1..256 chars>"
  }
}
```

### Identity Scopes
- `verified_runtime`: Created via `ExecutionNamespace.from_manifest(manifest)`. Authorizes genuine cross-request KV reuse (`is_verified_for_reuse = True`).
- `prototype_simulation`: Created via `create_prototype_simulation` or unverified `create_canonical`. Bounded to simulated testing (`is_verified_for_reuse = False`). Produces distinct namespace digests from verified runtime namespaces.

---

## 4. Storage Envelope Binary Format (`QCKVP2`)

Fixed 186-byte authenticated header followed immediately by payload stream:

### Header Byte Layout (186 Bytes, Big-Endian)
| Offset | Field | Type | Size | Description |
|---|---|---|---|---|
| `0..5` | `magic` | `char[6]` | 6 bytes | Magic constant: `b"QCKVP2"` |
| `6..7` | `version` | `uint16` | 2 bytes | Envelope format version (`0x0002`) |
| `8..9` | `flags` | `uint16` | 2 bytes | Bit 0: `0x0001` = zlib compressed.<br>Bit 1: `0x0002` = HMAC authenticated (mandatory). |
| `10..73` | `stored_hash` | `char[64]` | 64 bytes | Complete block key identifier (ASCII, padded with `\x00`). |
| `74..105` | `stored_ns` | `byte[32]` | 32 bytes | Full 32-byte cryptographic execution namespace digest. |
| `106..113` | `orig_len` | `uint64` | 8 bytes | Uncompressed payload size in bytes. |
| `114..121` | `comp_len` | `uint64` | 8 bytes | Compressed payload size in bytes on disk. |
| `122..153` | `payload_sha` | `byte[32]` | 32 bytes | Authentic SHA-256 digest of uncompressed payload. |
| `154..185` | `auth_hmac` | `byte[32]` | 32 bytes | HMAC-SHA256 computed over offsets `0..153` using `auth_key`. |

### Payload Stream
- Offset `186..` contains exactly `comp_len` bytes.
- Total file size must equal exactly `186 + comp_len`. Truncated files or trailing bytes are rejected fail-closed.

---

## 5. Verification Protocol on Read

When `read_page(block_hash, expected_namespace_digest, expected_payload_sha)` is invoked:

1. **Path & Hash Validation:** `block_hash` validated against regex `^[a-fA-F0-9_-]{8,128}$`.
2. **Tier 1 RAM Check:**
   - If present in RAM cache:
     - Verify `entry.block_hash == block_hash`.
     - Verify `entry.namespace_digest == expected_namespace_digest` (if supplied).
     - Verify `hashlib.sha256(entry.tensor_bytes).digest() == entry.payload_sha`.
     - On mismatch: purge entry from RAM (`evict_from_ram`), adjust `current_ram_bytes`, and raise `ValueError` fail-closed.
3. **Tier 2 Disk Check:**
   - Read file bytes. Check file size $\ge 186$ bytes and $\le 64\text{ MB}$.
   - Read 186-byte header:
     - Inspect leading 6 bytes: if `b"QCKVP1"`, raise `ValueError("Deprecated or unsupported stored page version 1")`. If not `b"QCKVP2"`, raise `ValueError("Invalid magic bytes")`.
     - Unpack header fields. Check `version == 2`.
     - Check flags: verify `flags & FLAG_AUTHENTICATED != 0` and `flags & ~0x0003 == 0`.
     - **AUTHENTICATE IMMEDIATELY**: Compute HMAC-SHA256 over first 154 bytes using `auth_key`. Constant-time compare (`hmac.compare_digest`) with `stored_hmac`. Fail closed on mismatch before allocating or decompressing payload.
     - Verify block hash: compare `stored_hash == block_hash`. Fail closed on mismatch (`"Page substitution detected"`).
     - Verify namespace digest: if `expected_namespace_digest` is supplied, compare `stored_ns == expected_ns`. Fail closed on mismatch.
     - Verify payload bounds: check `orig_len <= 64 MB` and exact file size `== 186 + comp_len`.
     - Decompress payload (if compressed) with bounded buffer limit (`orig_len + 1`). Check decompressed size `== orig_len` and no unused trailing stream data.
     - Verify uncompressed payload SHA-256 against `payload_sha` via constant-time comparison.
     - Admit authenticated entry into Tier 1 RAM cache and return verified bytes.
   - If any verification check fails, no bytes are admitted into RAM cache.

---

## 6. Compatibility Policy

1. **Legacy V1 Invalidation:** Prototype `QCKVP1` pages lacked stored block keys, truncated namespace digests, and had no authentication tags. All `QCKVP1` envelopes are rejected fail-closed upon detection.
2. **Safe Cache Miss:** When a legacy, unauthenticated, or incompatible page is encountered, the storage engine rejects the page, treating it as an authentic cache miss.
3. **Directory Isolation:** Production caches are not modified or deleted. Development and isolated testing use dedicated disposable temporary directories.
