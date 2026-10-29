# Increment 17: Storage Format, Cache Identity, and Security Specification
## Document Identifier: `SPEC-INC17-PAGED-KV-V2`
**Version:** 2.0  
**Status:** Implemented & Verified  
**Date:** 2026-10-28  
**Scope:** `app/services/paged_kv` subsystem  

---

## 1. Overview & Threat Model

The Searchable Paged KV Cache persists fixed-size token KV tensors across Tier 1 RAM and Tier 2 disk. To guarantee cache safety and prevent unauthorized cross-request or cross-model tensor leakage, the storage layer must defend against both accidental corruption and deliberate identity substitution.

### Threat Model & Boundaries
1. **Accidental File Corruption:**
   - *Threat:* Bit flips, truncated disk writes, partial downloads, OS filesystem crashes.
   - *Defense:* Uncompressed SHA-256 payload checksum, declared length checks, atomic write-then-rename (`os.replace`) publishing.
2. **Identity & Substitution Attacks:**
   - *Threat:* An adversary or rogue process swaps valid page files across storage directories (e.g. copying valid Page B over Page A's path), replaying an old page, or forging headers.
   - *Defense:* 
     - Complete block key (`stored_hash`) and full 32-byte execution namespace digest (`stored_ns`) are embedded into the envelope header.
     - The read API receives caller-trusted expected identities (`expected_block_hash`, `expected_namespace_digest`) and verifies them before exposing payload bytes.
     - The header is authenticated with an HMAC-SHA256 tag keyed by an internal secret storage key (`auth_key`). Any modification to header metadata (changing key or namespace) triggers immediate authentication rejection.
3. **Decompression Bombs (Zip Bombs):**
   - *Threat:* Highly compressed payload that expands to gigabytes when decompressed.
   - *Defense:* Enforce `max_length = orig_len + 1024` on streaming decompression, capped by `MAX_PAGE_BYTES = 64 MB`. Declared lengths exceeding `MAX_PAGE_BYTES` are rejected before allocation.
4. **Path Traversal & Injection:**
   - *Threat:* Malformed block hash containing `../` or `/` attempting directory traversal.
   - *Defense:* Strict regex validation (`^[a-fA-F0-9_-]{8,128}$`) applied to all hash inputs before filesystem operations.
5. **Limitations:**
   - Storage authentication protects against unauthorized tampering with stored files on disk, but does not substitute for kernel-level OS access controls. If an attacker gains raw root or memory inspection capabilities on the host running the process, memory contents and keys are compromised.

---

## 2. Execution Namespace Specification (`ExecutionNamespace/v1`)

To prevent ambiguous delimiter collisions (such as `model_name="a:b", model_revision="c"` vs `model_name="a", model_revision="b:c"`), namespaces are serialized using RFC 8785 JSON canonicalization principles (deterministic key order, no whitespace, unescaped UTF-8).

### Schema Definition
```json
{
  "schema": "ExecutionNamespace/1.0",
  "fields": {
    "chat_template_hash": "<str: 1..256 chars>",
    "kv_layout": "<str: 1..256 chars>",
    "model_name": "<str: 1..256 chars>",
    "model_revision": "<str: 1..256 chars>",
    "rope_config_hash": "<str: 1..256 chars>",
    "tenant_id": "<str: 1..256 chars>",
    "tokenizer_id": "<str: 1..256 chars>"
  }
}
```

### Validation Rules
1. Every field must be a non-null string. Non-string types raise `TypeError`.
2. Empty or whitespace-only strings raise `ValueError`.
3. The generic placeholder string `"default"` (case-insensitive) is strictly rejected (`ValueError`) for identity fields to ensure authentic model identities are supplied.
4. Field lengths must be between 1 and 256 characters. Values exceeding 256 characters raise `ValueError`.
5. Canonical bytes are UTF-8 encoded with sorted keys and no whitespace (`separators=(",", ":")`).
6. The namespace digest is the full 64-character hexadecimal SHA-256 digest of the canonical bytes (not truncated).

---

## 3. Storage Envelope Binary Format (`QCKVP2`)

Stored pages on Tier 2 disk use the versioned `QCKVP2` binary envelope. The envelope consists of a fixed 186-byte authenticated header followed immediately by the payload stream.

### Header Byte Layout (186 Bytes, Big-Endian)
| Offset | Field | Type | Size | Description |
|---|---|---|---|---|
| `0..5` | `magic` | `char[6]` | 6 bytes | Magic constant: `b"QCKVP2"` |
| `6..7` | `version` | `uint16` | 2 bytes | Envelope format version (`0x0002`) |
| `8..9` | `flags` | `uint16` | 2 bytes | Bit 0: `1` = zlib compressed, `0` = raw.<br>Bit 1: `2` = HMAC authenticated. |
| `10..73` | `stored_hash` | `char[64]` | 64 bytes | Complete block key identifier (ASCII, padded with `\x00`). |
| `74..105` | `stored_ns` | `byte[32]` | 32 bytes | Full 32-byte cryptographic execution namespace digest. |
| `106..113` | `orig_len` | `uint64` | 8 bytes | Uncompressed payload size in bytes. |
| `114..121` | `comp_len` | `uint64` | 8 bytes | Compressed payload size in bytes on disk. |
| `122..153` | `payload_sha` | `byte[32]` | 32 bytes | Authentic SHA-256 digest of uncompressed payload. |
| `154..185` | `auth_hmac` | `byte[32]` | 32 bytes | HMAC-SHA256 computed over offsets `0..153` using `auth_key`. |

### Payload Stream
- Offset `186..` contains exactly `comp_len` bytes.
- Any trailing data past `186 + comp_len` is rejected as an envelope mismatch error.

---

## 4. Verification Protocol on Read

When `read_page(block_hash, expected_namespace_digest, expected_payload_sha)` is invoked:

1. **Path & Hash Validation:** `block_hash` validated against regex `^[a-fA-F0-9_-]{8,128}$`.
2. **Tier 1 RAM Check:**
   - If present in RAM cache, verify that cached `RamCacheEntry.block_hash == block_hash`.
   - If `expected_namespace_digest` is supplied, verify that cached namespace matches expected namespace.
   - If `expected_payload_sha` is supplied, verify payload digest matches.
   - On mismatch: raise `ValueError` fail-closed (do not return bytes).
3. **Tier 2 Disk Check:**
   - Read file bytes. Check file size is $\ge 186$ bytes and $\le 64\text{ MB}$.
   - Inspect leading 6 bytes:
     - If `b"QCKVP1"`: raise `ValueError("Deprecated or unsupported stored page version 1")`.
     - If not `b"QCKVP2"`: raise `ValueError("Invalid magic bytes")`.
   - Unpack header fields. Check `version == 2`.
   - Verify header HMAC: compute HMAC-SHA256 over first 154 bytes using `auth_key`. Constant-time compare with `stored_hmac`. Fail closed on mismatch.
   - Verify block hash: compare `stored_hash == block_hash`. Fail closed on mismatch (`"Page substitution detected"`).
   - Verify namespace digest: if `expected_namespace_digest` is provided, compare `stored_ns == expected_ns`. Fail closed on mismatch (`"Page namespace mismatch"`).
   - Verify payload bounds: check `orig_len <= 64 MB` and `len(raw[186:]) == comp_len`. Fail closed on truncation or trailing bytes.
   - Decompress payload (if compressed) with bounded buffer limit. Check decompressed size `== orig_len`.
   - Verify uncompressed payload SHA-256 against `payload_sha`.
   - Admit authenticated entry into Tier 1 RAM cache and return verified bytes.

---

## 5. Compatibility Policy

1. **Legacy V1 Invalidation:** Prototype `QCKVP1` pages lacked stored block keys, truncated namespace digests, and had no authentication tags. All `QCKVP1` envelopes are rejected fail-closed upon detection.
2. **Safe Cache Miss:** When a legacy or incompatible page is encountered, the storage engine rejects the page, treating it as an authentic cache miss. The higher-level orchestrator or inference engine recomputes the necessary tokens rather than loading unverified cache data.
3. **Directory Isolation:** Production caches are not deleted. Development and isolated testing use dedicated disposable temporary directories.
