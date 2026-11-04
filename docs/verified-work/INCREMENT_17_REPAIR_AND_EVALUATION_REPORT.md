# Increment 17: Distributed Pipeline Parallelism and Searchable Paged KV Cache
## Repair, Evaluation, and Independent Review Report

**Date:** 2026-10-28 (Updated 2026-11-03 for Final Release-Validation & `F-26` Real-Artifact Runtime Identity Binding)  
**Branch:** `feature/increment17-pipeline-parallelism-paged-kv`  
**Remote `origin/main` Base Commit:** `4be7f547f4919380a22abdd2122961d559d2f46f`  
**Prior Historical Feature Commit SHAs:** `9a37323c57de76fb89ae4a784fe05e91e21a39d8` (initial base), `4a5ce4e9eb298aee51b8670f1cc28fcdc5c611a8` (Oct 28 rehearsal), `5a4a873c11a5b3840ca13dca2d0a1a3866575b3d` (prior release doc), `0c6c840057705aa787ec2015936cdf5f4a0ca1a5` (last independently inspected HEAD)  
**Evaluation Standard:** Independent Codex Reviews (`INCREMENT_17_INDEPENDENT_REVIEW.md`, `INCREMENT_17_NOVEMBER_3_REVIEW.md`) and Probes (`INCREMENT_17_INDEPENDENT_PROBES.json`)  
**Authorization Scope:** Isolated development, regression repairs, test execution, documentation, review branch publication. Production database migrations, production service restarts, merges to `main`, and removal of safety holds remain strictly held.

---

## 1. Executive Summary & Review Response

Following the independent technical review and empirical probes conducted by Codex, an audit was performed on the Increment 17 prototype implementation (`app/services/paged_kv` and `app/services/pipeline_parallel`). 

The audit confirmed the validity of the independent findings:
1. **Live-Page Recycling Defect (F-7):** The physical block pool recycled the oldest block without checking if active virtual sequences were still referencing it, causing silent data corruption under pool pressure.
2. **Missing Cache Namespace Isolation (F-8):** Hash computation was solely derived from raw token IDs, allowing different models (`qwen2.5:1.5b` vs `llama3.2:1b`) to experience false prefix cache hits.
3. **Unverified Stored-Page Deserialization (F-9):** Disk storage persisted raw zlib bytes without tamper-evident cryptographic digests or execution namespace envelopes.
4. **Unenforced Memory Quotas & Sequence Leaks (F-10):** Disk-to-RAM restoration bypassed configured RAM limits, and inference service sequences were not deterministically released upon request termination.
5. **Overstated Capability Boundaries (F-11):** Synthetic activation-step latency was labeled as real model token generation throughput, and placeholder byte caching was conflated with real transformer KV cache reuse.
6. **Subsequent Stress & Release Review Findings (F-12 through F-26):** Delimiter collisions (`F-12`), cross-identity page substitution (`F-13`), authentication downgrade (`F-14`), hardcoded key fallback (`F-15`), display-name spoofing (`F-16`), self-asserted authority (`F-17`), unconfigured inference crash (`F-18`), forged issuer bypass (`F-19`), and stress-evaluation + real-artifact model-instance authority binding (`F-22` through `F-26`).

All correctness and authority defects were reproduced with dedicated regression test suites, fixed with strict fail-closed mechanisms, and verified across all Increment 17 unit, real-artifact GPU, and application regression tests. The capability boundaries across all documentation and UI dashboards have been reconciled into a single 6-row capability classification table.

---

## 2. Environment, Branch, and Source Hashes

### Environment
- **Host:** Windows 10 (Build 19045), Python 3.12.10
- **Hardware (Verified via Live Probe):** `Intel(R) Xeon(R) CPU W3680 @ 3.33GHz` (6 cores / 6 threads), 24.0 GB RAM, `NVIDIA GeForce GTX 1650 SUPER` (4095 MiB VRAM, Compute Capability 7.5); `.venv-gpu` with `torch 2.5.1+cu121` and `transformers 4.45.2`
- **Local Runtime:** Ollama 0.15.2 (127.0.0.1:11434)
- **Remote Nodes:** `qwen-laptop` (`100.101.187.115`, Ubuntu Linux `7.0.0-38-generic`, `Intel(R) Core(TM) i3-6100U CPU @ 2.30GHz`, 3.7 GiB RAM, Python 3.12.3 without `torch`/`transformers`)
- **Network Fabric:** Tailscale WireGuard mesh (encrypted private subnet 100.64.0.0/10)

### Tested Source Hashes (SHA-256 — Post-Repair `F-22` through `F-26`, November 3, 2026)
| Source File | Pre-Fix SHA-256 (`12469444a1...`) | Post-Fix SHA-256 (Current Release Baseline) | Status |
|---|---|---|---|
| `app/services/paged_kv/page_table.py` | `7a1b1bb2e66e02bafbc798b66478e26fd081f348bd17c2739bbbf2563803f24c` | `283970b59cf13ea8c63940ae51fa74d2dd24c8d68a1267908b8ae5dc0cefa0ed` | Repaired (F-7, F-24) |
| `app/services/paged_kv/hasher.py` | `d94cc3130d4f6cd5fd0d2da308831ace1a35a8bd60a9fc7a375ea98fd3eeaa21` | `d94cc3130d4f6cd5fd0d2da308831ace1a35a8bd60a9fc7a375ea98fd3eeaa21` | Repaired (F-8, F-12, F-16, F-17) |
| `app/services/paged_kv/store.py` | `00fa9c8be8098c93ea030479013f51fbbaadede46d76d2f2385151346604b628` | `a5280f1138293d3882da459fa1c80663d5f3a92565e2ed60af8a9ea581613837` | Repaired (F-9, F-10, F-13, F-14, F-15, F-24) |
| `app/services/paged_kv/manager.py` | `55e146f7a34ef240ac0b3da267589294c2662747c33fffb47733d3473978f3a4` | `b99565b5791dc7dd92e0c223b688d566be7419a311e8fe36dd9381b2c6562bd2` | Repaired (F-7..F-20, F-24, F-26) |
| `app/services/paged_kv/identity.py` | `4f896359cb3af9b5263c9c3579a1a82e6255129919a94bfc25a37cbda463cc2b` | `3d3d8ed5a1110b51d34b9a2215b3084204ac609d66f5f79ebe3b517e9730874a` | Repaired (F-17, F-19, F-26) |
| `app/services/paged_kv/model_loader.py` | *(added in Nov 3 release validation)* | `d619be3f6b19791e8d49e34b48ca8b7af806de1c5e4f0cadf9030d8379919283` (LF: `47aeea18804bc15e33d31dce5e0ea6a8694942eaf3b554bf2a660ad14e318c63`) | Real-Artifact Loader & Instance Binding (F-26) |
| `app/services/paged_kv/pytorch_adapter.py` | `1c7e5aa3cea8d9f9d57b4a2710f4085d0a02d65d5e0b01417ba152b9ec91a8b2` | `0c8a27ec96370e292cadd83425b3a85177964e5fdef4c2273704865d322b23d5` | Repaired (F-21, F-25, F-26) |
| `app/services/paged_kv/__init__.py` | *(module exports)* | `f829cf9812c9942514e83a38282ef5b7ff6f8a5fbbc62e4cbf97da8643453af2` | Updated exports (F-26) |
| `app/services/inference.py` | `8420ff7b40eccd62bd896851313e33f475f46cf3c02f457ea1960205b2be3ef0` | `8420ff7b40eccd62bd896851313e33f475f46cf3c02f457ea1960205b2be3ef0` | Repaired (F-10, F-18, F-19) |
| `app/services/pipeline_parallel/activation_protocol.py` | `77f299667d76cc4c32838517fb7ca9e067b061d1f156f7377a792c8ae7bcd509` | `1113e0eb8f37ded3fcbb31de3fe3f53d88f5e2554727e1c19486540614426d29` | Repaired (F-22) |
| `app/services/pipeline_parallel/topology.py` | `f19d5b0c3eb12bdf8def548b434101dc775e5490bb94a19c79fbc93e1e926a6f` | `4e58d8fc985dcc4f1e2b27ab02072ea608e2322be91d5fd4139db3c61efa440f` | Reconciled simulated stage labels |
| `app/services/pipeline_parallel/runner.py` | `da6f70714acb245cacd7f584b600044d0d677d2c3def83a05c65611aba71419b` | `c7b6141d471d82910d210fc6675ab0bd1ee22ee36d5dfdec891ac5000ed0b9e6` | Repaired (F-22) |
| `app/services/pipeline_parallel/socket_transport.py` | `fe3b74d9515be8d07b9922a9b94d366402534237e948e63129aadd9f3e3d9c06` | `4a94d042beb0740e24aeb203c28108140f8f2e494e2bf5fb2128c473201b7c2d` | Repaired (F-23) |
| `app/services/pipeline_parallel/stage1_server.py` | `e8bdd761ecd73469e93598cc55f9f2c28f28daab43891966ad25fb1a49c2a00f` | `e8866a9a4af2ca137600c2a7f7c3557399cdd16e072ceae70c0bc10125dd7189` | Repaired (F-23) |
| `app/services/pipeline_parallel/pytorch_split_engine.py` | *(added in post-fix)* | `abffa3ec83b06d8b812ad2961196078b88d31e0890e3496d541d2e4eeed3e7cc` | Tier 3 Split Engine |
| `tests/test_increment17_defect_reproductions.py` | *(added in evaluation)* | `cac7a2d0045083ae18a09fc47d9983673109429300c27518423b1f9f32ef9164` | F-22..F-25 Reproductions (8 tests) |
| `tests/test_increment17_comprehensive_verification.py` | *(added in evaluation)* | `68b666b2160d31b8cbe34400daf23b0bcca30afb842e1d810b6ecb13e1ade70e` | Sections 3–7 Verification (12 tests) |
| `tests/test_paged_kv_real_model_authority.py` | *(added in Nov 3 release validation)* | `3c0932d0e925eaabbfb6344192e37cfe4e907ee0737a87bae5d92b16b9939cbc` | Real-Artifact Loader & Instance Binding (7 tests) |

### Pinned Local Model Artifact Digests (`Qwen/Qwen2.5-0.5B-Instruct` @ `7ae557604adf67be50417f59c2c2f167def9a775`)
| Role | File | Size (Bytes) | SHA-256 Digest |
|---|---|---|---|
| `weights` | `model.safetensors` | `988,097,824` | `fdf756fa7fcbe7404d5c60e26bff1a0c8b8aa1f72ced49e7dd0210fe288fb7fe` |
| `tokenizer` | `tokenizer.json` | `7,031,645` | `c0382117ea329cdf097041132f6d735924d697924d6f6fc3945713e96ce87539` |
| `chat_template` | `tokenizer_config.json` | `7,305` | `5b5d4f65d0acd3b2d56a35b56d374a36cbc1c8fa5cf3b3febbbfabf22f379583` |
| `rope_config` | `config.json` | `659` | `18e18afcaccafade98daf13a54092927904649e1dd4eba8299ab717d5d94ff45` |

---

## 3. Reproduced Defects and Verified Fixes

### A. Live-Page Ownership & Slot Recycling (Defect F-7)
- **Reproduction:** In a `PhysicalBlockPool(total_blocks=1)`, allocating block A for sequence `s1`, followed by allocating block B for sequence `s2` while `s1` remained active, recycled physical slot 0. Subsequent writes to `s2` silently overwrote the memory of `s1`.
- **Failing Test:** `tests/test_paged_kv_page_ownership.py::test_reproduce_live_page_overwrite_in_one_block_pool`
- **Root Cause:** `PhysicalBlockPool.allocate_block()` searched `_allocated_blocks` and recycled the oldest block unconditionally if the pool was full, ignoring `ref_count > 0` and pinning status.
- **Architectural Fix:**
  - Added monotonic `generation: int` and `pinned: bool` to `PhysicalBlock`.
  - Enforced that only blocks with `ref_count == 0` and `pinned == False` can be recycled.
  - Implemented fail-closed `MemoryError("PhysicalBlockPool exhausted: all ... blocks are active")` when no unreferenced blocks are available.
  - Added eviction callback support in `PagedKVManager` to offload resident pages to Tier 2 disk storage and remap virtual tables safely when physical memory is pressured.
- **Verification:** 6 passing tests covering single-block pools, pinning invariants, copy-on-write fork ref-counts, generation identities, and concurrent allocations under pool churn.

### B. Cache Namespace Isolation (Defect F-8)
- **Reproduction:** Hashing token prefixes `[101, 102, 103]` produced identical cryptographic keys regardless of whether the model was `qwen2.5:1.5b` or `llama3.2:1b`, causing cross-model prefix cache hits.
- **Failing Test:** `tests/test_paged_kv_namespace_isolation.py::test_reproduce_cross_model_prefix_collision`
- **Root Cause:** `BlockHasher.hash_block()` only hashed `parent_hash` and `tokens`. The model identifier was treated as an unhashed metadata string.
- **Architectural Fix:**
  - Created immutable `ExecutionNamespace` dataclass binding: `model_id`, `model_revision`, `tokenizer_id`, `kv_dtype`, `kv_layout`, `rope_scaling`, and `tenant_id`.
  - Implemented `compute_digest()`: a 32-byte SHA-256 digest of normalized namespace parameters.
  - Bound the namespace digest into the genesis hash seed of all block chains and the `PrefixRadixTree` partition lookup.
- **Verification:** 2 passing tests proving cross-model rejection and same-model prefix reuse.

### C. Stored-Page Integrity & Envelope Security (Defect F-9)
- **Reproduction:** Modifying on-disk page bytes (bit flipping) was undetected on read; decompression proceeded without cryptographic verification.
- **Failing Test:** `tests/test_paged_kv_stored_integrity.py::test_reproduce_unverified_stored_page_tampering`
- **Root Cause:** `TieredStorageEngine` persisted raw zlib compressed bytes named by hex hash without an authenticated header or payload checksum.
- **Architectural Fix:**
  - Defined versioned binary envelope `QCKVP1` (62-byte header, subsequently upgraded to `QCKVP2` 186-byte authenticated header in F-13/F-14).
  - Enforced bounded decompression (`max_decompressed_bytes = 16 MB`) to prevent zip bombs.
  - Added strict path validation rejecting path traversals (`../`, `/`).
- **Verification:** 4 passing tests verifying bit-flip rejection, invalid magic detection, path traversal blocks, and authenticated page reconstruction.

### D. Memory Accounting & Quota Enforcement (Defect F-10)
- **Reproduction:** `restore_sequence_to_ram()` loaded all sequence blocks from disk directly into `_ram_cache` without enforcing `max_ram_pages` or byte quotas, allowing unbounded memory growth.
- **Failing Test:** `tests/test_paged_kv_memory_accounting.py::test_reproduce_restore_bypassing_ram_quota`
- **Root Cause:** Restoration loop lacked eviction calls; `PagedKVManager` tracked memory only as page counts rather than actual resident bytes.
- **Architectural Fix:**
  - Added `_enforce_ram_quota()` within `restore_sequence_to_ram()`, evicting LRU resident pages when `current_ram_bytes + incoming > max_ram_bytes`.
  - Added byte-accurate accounting tracking `current_ram_bytes`, `max_ram_bytes`, and block tensor payload sizes.
  - Updated `app/services/inference.py` to wrap completion generation in a deterministic `finally: paged_kv.release_sequence(seq_id)` block.
- **Verification:** 2 passing tests confirming strict RAM byte quota compliance during restores and deterministic release upon sequence completion.

### E. Ambiguous Namespace Delimiter Collision (Defect F-12)
- **Reproduction:** In `ExecutionNamespace`, string concatenation with `:` delimiters caused collisions: `model_name="a:b", model_revision="c"` and `model_name="a", model_revision="b:c"` both evaluated to `"a:b:c:..."`, yielding identical digests `9d7e024b9e8e7b76`.
- **Failing Test:** `tests/test_paged_kv_namespace_isolation.py::test_reproduce_ambiguous_namespace_delimiter_collision`
- **Architectural Fix:**
  - Implemented RFC 8785 JSON canonical encoding schema `ExecutionNamespace/1.0` with sorted keys, no whitespace, and UTF-8 bytes.
  - Switched to the full 64-character SHA-256 hexadecimal digest (`hashlib.sha256(canonical_bytes).hexdigest()`).
- **Verification:** 11 passing tests in `tests/test_paged_kv_namespace_isolation.py`.

### F. Valid-Page Substitution Across Identities (Defect F-13)
- **Reproduction:** Writing Page A and Page B, evicting both to disk, and copying Page B's valid envelope file over Page A's file caused `store.read_page("a"*64)` to return Page B's payload.
- **Failing Test:** `tests/test_paged_kv_stored_integrity.py::test_reproduce_valid_page_substitution_across_keys_and_namespaces`
- **Architectural Fix:**
  - Designed authenticated binary envelope `QCKVP2` (186-byte header) embedding Magic `b"QCKVP2"`, Version `2`, 64-byte `stored_hash`, 32-byte `stored_ns`, 64-bit uncompressed/compressed lengths, 32-byte payload SHA-256 checksum, and 32-byte HMAC-SHA256 authentication tag.
  - Legacy `QCKVP1` envelopes fail closed with explicit deprecation error (`ValueError("Deprecated or unsupported stored page version 1")`).

### G. Authentication Downgrade Vulnerability (Defect F-14)
- **Reproduction:** In `PagedKVStore.read_page()`, HMAC authentication was guarded by `if flags & 2:`. Clearing bit 1 in the envelope header (`flags & ~FLAG_AUTHENTICATED`) bypassed HMAC verification.
- **Failing Test:** `tests/test_paged_kv_auth_and_identity.py::test_auth_flag_downgrade_with_altered_payload_rejected`
- **Architectural Fix:**
  - Enforced reader-mandated authentication policy on every `QCKVP2` page (`FLAG_AUTHENTICATED` required; unknown flags rejected).
  - Evaluated header HMAC-SHA256 using constant-time comparison (`hmac.compare_digest`).

### H. Hardcoded Storage Authentication Key Fallback (Defect F-15)
- **Reproduction:** When `auth_key` was omitted and `PAGED_KV_STORAGE_KEY` was unset, `PagedKVStore` silently defaulted to `b"qc_internal_storage_key_v2_f89c1a"`.
- **Failing Test:** `tests/test_paged_kv_auth_and_identity.py::test_unconfigured_store_fails_closed_without_hardcoded_key`
- **Architectural Fix:**
  - Removed all hardcoded fallback keys from source code; `validate_storage_key()` requires an explicit key ($\ge 32$ bytes / 256 bits).

### I. Unverified Model Identity & Display-Name Spoofing (Defect F-16)
- **Reproduction:** `ExecutionNamespace.create_canonical()` derived model revisions from display name strings or static mock profiles.
- **Failing Test:** `tests/test_paged_kv_auth_and_identity.py::test_changed_weights_under_same_display_name_changes_namespace`
- **Architectural Fix:**
  - Implemented `ModelIdentityManifest` binding immutable 64-char hex SHA-256 digests for model weights, tokenizer vocabularies, chat templates, and RoPE configurations.

### J. Identity Issuance Authority & Verification Enforcement (Defect F-17)
- **Reproduction:** Direct construction of `ExecutionNamespace(..., identity_scope="verified_runtime")` or `ModelIdentityManifest` granted reuse without verifying artifacts on disk.
- **Architectural Fix:**
  - Implemented three-tier identity separation (`ModelIdentityManifest`, `ApprovedManifestRegistry` + `IdentityIssuanceAuthority`, and `IssuedReuseAuthority`).

### K. Controlled Application Caching Modes & Error Policies (Defect F-18)
- **Reproduction:** Ordinary inference (`send_chat_completion`) without a configured storage key crashed with an uncaught `ValueError`.
- **Architectural Fix:**
  - Defined explicit application modes in `app/services/inference.py` (`QC_PAGED_KV_ENABLED=false` default; `QC_PAGED_KV_ON_ERROR=fail_closed|bypass`).

### L. Trusted Issuer Membership & Authority Boundary Enforcement (Defect F-19)
- **Reproduction:** Caller-constructed `IssuedReuseAuthority` with empty artifacts and bogus token bypassed `PagedKVManager.ingest_prompt()`.
- **Architectural Fix:**
  - Enforced `REQUIRED_ROLES = frozenset({"weights", "tokenizer", "chat_template", "rope_config"})` at `IssuedReuseAuthority` construction and active membership checking via `trusted_issuer.validate_authority_for_reuse(...)`.

### M. Real-Artifact Trusted Model Loader & Live Model-Instance Binding (Defect F-26 — Nov 3, 2026)
- **Review Condition:** Previously, `tests/test_increment17_comprehensive_verification.py` issued reuse authority over synthetic 30-byte marker files (`SYNTHETIC_QWEN_WEIGHT_MARKER_V1`) while separately loading real `Qwen/Qwen2.5-0.5B-Instruct` weights. Furthermore, `PyTorchPagedKVAdapter` accepted caller-supplied `namespace` and `authority` without verifying that the authority was bound to the actual in-memory `torch.nn.Module` instance.
- **Architectural Fix (`app/services/paged_kv/model_loader.py`, `identity.py`, `manager.py`, `pytorch_adapter.py`):**
  - Implemented `TrustedLocalModelLoader.load_verified_model()` using streaming 8 MiB chunked SHA-256 hashing (`stream_file_sha256`) over the pinned local HuggingFace snapshot (`Qwen/Qwen2.5-0.5B-Instruct` @ `7ae557604adf67be50417f59c2c2f167def9a775`, `988,097,824` bytes `model.safetensors`).
  - Added post-load TOCTOU re-verification of all 4 artifact files (`weights`, `tokenizer`, `chat_template`, `rope_config`) after `AutoModelForCausalLM.from_pretrained(..., local_files_only=True)` completes, ensuring the verified files were not substituted during loading.
  - Added deterministic live model parameter fingerprinting (`compute_live_model_fingerprint` / `verify_live_model_instance`) and cryptographic `model_instance_id` binding across `IssuedReuseAuthority`, `IdentityIssuanceAuthority.validate_authority_for_reuse(..., require_instance_binding=True)`, `PagedKVManager.ingest_prompt`, `PagedKVManager.restore_sequence_to_ram`, and `PyTorchPagedKVAdapter`.
  - `PyTorchPagedKVAdapter.extract_and_cache_sequence_kv()` and `prepare_prefill_context()` now require `verify_live_model_instance(model, authority.model_instance_id)` and active `trusted_issuer` validation before writing or injecting any KV tensors.
- **Verification:** 7 dedicated real-artifact tests in `tests/test_paged_kv_real_model_authority.py` (`7 passed in 53.61s`) plus updated `tests/test_increment17_comprehensive_verification.py` (`12 passed`).

---

## 4. Capability Distinction Matrix (Reconciled November 3, 2026)

> **Superseding Note (2026-11-03):** Sections 4 and 5 of the original October 28 report reflected the pre-PyTorch baseline before `.venv-gpu` and `Qwen/Qwen2.5-0.5B-Instruct` were provisioned locally. The unified 6-row capability classification below supersedes the October 28 table across all Increment 17 release documents.

| # | Capability Category | Status | Scope, Evidence & Limitations |
|---|---|---|---|
| **1** | **Local simulated stages** | **IMPLEMENTED & VERIFIED** | Synthetic activation scaling (`simulate_stage_compute`) and DAG orchestration (`OrchestratorRunner`, `topology.py`). Verified by unit tests; does **not** execute real transformer weights. |
| **2** | **Remote transport of synthetic activations** | **IMPLEMENTED & VERIFIED** | Binary `QCACT1` frame protocol over TCP / Tailscale WireGuard (`socket_transport.py`, `stage1_server.py`) with HMAC-SHA256 authentication, replay protection, and bounded queues. Measured in `Synthetic Frame Rate (steps/s)` (**never** model tokens/sec). |
| **3** | **Local split execution using real weights** | **IMPLEMENTED & VERIFIED (Local Single-Host GPU)** | `LocalSplitPipelineEngine` (`pytorch_split_engine.py`) partitions real `Qwen/Qwen2.5-0.5B-Instruct` (24 layers, FP16) across 2-stage (`12+12`) and 3-stage (`8+8+8`) splits on local GTX 1650 SUPER (`4095 MiB` VRAM) with `0.000000` max intermediate/logit diff vs monolithic reference. Local partition equivalence does **not** establish distributed speedup; multi-token split decode recomputes the growing prefix per step (`1.07x–1.10x` local overhead). |
| **4** | **Remote split execution using real weights** | **NOT IMPLEMENTED / HELD** | Remote node `qwen-laptop` (`100.101.187.115`, `i3-6100U`, `3.7 GiB` RAM) lacks `torch`/`transformers` (`TORCH_NOT_INSTALLED`). Earlier PR text claiming `qwen-laptop` executed real Stage 1 Layers 12–24 + LM Head is explicitly superseded: `qwen-laptop` only ran the synthetic `stage1_server.py` echo/scaling server. Provisioning remote PyTorch runtime remains held. |
| **5** | **Local genuine KV-tensor reuse** | **IMPLEMENTED & VERIFIED (Local Single-Host GPU)** | `TrustedLocalModelLoader` (`model_loader.py`) + `PyTorchPagedKVAdapter` (`pytorch_adapter.py`) verify real on-disk `Qwen/Qwen2.5-0.5B-Instruct` artifacts, bind `IssuedReuseAuthority` to the live `PreTrainedModel` instance (`model_instance_id`), and inject cached FP16 `(K, V)` tensors into `DynamicCache` during prefill (`3.41x–4.77x` warm-prefill speedup; `1.14x–1.20x` end-to-end 16-token request speedup; max logit diff `0.003906` within `1e-2` FP16 SDPA tolerance; 100% greedy token agreement). Warm-prefill gains do **not** equal end-to-end request gains. |
| **6** | **Production inference integration** | **DISABLED BY DEFAULT / HELD** | `app/services/inference.py` runs with `QC_PAGED_KV_ENABLED=false` by default against Ollama (`127.0.0.1:11434`). When enabled in app mode, `inference.py` exercises virtual page/block metadata sidecar accounting; Ollama's closed HTTP API does not expose internal KV tensors. Production activation Remains strictly held. |

---

## 5. Inference Backend Selection & ADR Summary

An Architecture Decision Record was compiled and recorded in `docs/architecture/INCREMENT_17_INFERENCE_BACKEND_DECISION.md`.

### Evaluation Findings & Subsequent Local Provisioning
- **Production Default Backend (Ollama 0.15.2):** Operates as a closed C++ executable (`llama.cpp` server) exposing `/api/chat` and `/api/generate` HTTP endpoints without exposing internal layer activations or attention KV cache tensors.
- **Local Verification GPU Environment (`.venv-gpu`):** Provisioned on the Desktop host (`NVIDIA GeForce GTX 1650 SUPER`, `4095 MiB` VRAM) with `torch==2.5.1+cu121`, `transformers==4.45.2`, and pinned `Qwen/Qwen2.5-0.5B-Instruct` (`7ae557604adf67be50417f59c2c2f167def9a775`) to verify local split execution (Row 3) and genuine attention KV-cache tensor reuse with real-artifact authority binding (Row 5).
- **Remote Node Status (`qwen-laptop`):** Remains without `torch`/`transformers`; remote split execution with real weights (Row 4) remains **NOT IMPLEMENTED / HELD**.

---

## 6. Benchmark Methodology & Disclaimers

The empirical measurements in `docs/verified-work/INCREMENT_17_LIVE_PIPELINE_BENCHMARK.json` and `docs/verified-work/evidence/increment17-benchmark-and-cleanup-20261103.json` carry explicit scope boundaries:
- **Synthetic Transport (`INCREMENT_17_LIVE_PIPELINE_BENCHMARK.json`):** Measures synthetic FP16 `QCACT1` frame transport over WireGuard (`95.09` synthetic steps/sec LAN Desktop → Laptop; `60.28` synthetic steps/sec WAN Desktop → VPS). These rates measure **synthetic frame transport only** and must never be cited as model token generation throughput.
- **Local Real-Model Benchmarks (`increment17-benchmark-and-cleanup-20261103.json`):**
  - **Bit-Exact Split Equivalence (No KV Reuse):** `0.000000` max intermediate activation and final logit difference across 2-stage and 3-stage local splits on `Qwen/Qwen2.5-0.5B-Instruct`. Because split decode recomputes the prefix on each step without per-stage persistent KV state, multi-token split decode incurs `1.07x–1.10x` local overhead (`592.75 ms` vs `555.02 ms` monolithic at 64 prompt + 8 decode tokens).
  - **Real Attention KV Reuse (`PyTorchPagedKVAdapter`):** FP16 SDPA kernel reordering between full-sequence prefill and cached-prefix prefill introduces a bounded numerical difference (`0.003906` max abs logit diff vs `1e-2` tolerance) while maintaining **100% greedy token agreement**. Warm RAM prefill achieves `3.41x` (`64` tokens) to `4.77x` (`128` tokens) prefill-only speedup, which translates to `1.14x–1.20x` end-to-end request speedup when amortized over 16 greedy decode steps.

---

## 7. Acceptance Matrix Status

All entries in `docs/verified-work/ACCEPTANCE_MATRIX.md` have been updated through `F-26`:
- **REQ-10 (Paged KV Cache):** Status **PROVED (LOCAL REAL-MODEL KV REUSE + APP SIDECAR)**. Memory safety, `QCKVP2` envelope authentication, trusted issuer membership, and `TrustedLocalModelLoader` real-artifact live model-instance binding (`F-26`) are verified.
- **REQ-11 (Pipeline Activation Transport & Local Split Execution):** Status **PROVED (LOCAL REAL-WEIGHT SPLIT + SYNTHETIC REMOTE TRANSPORT)**. Remote real-weight execution remains explicitly **NOT IMPLEMENTED / HELD**.
- **REQ-12 (Pipeline & Cache Diagnostics):** Status **PROVED**. Dashboard labels in `pipeline_widget.html`, `observatory.html`, and `topology.py` report `Synthetic Frame Rate (steps/s)`.
- **Defect Findings F-7 through F-26:** Fully documented, reproduced, repaired, and verified.

---

## 8. Disposable Integration Rehearsal Evidence (Refreshed November 3, 2026)

> **Historical Note:** Prior integration rehearsals tested earlier commits `4a5ce4e9eb298aee51b8670f1cc28fcdc5c611a8` (rehearsal `4f75c2c26abdc648aaf7fdd3f76387b19748e52f`) and `5a4a873c11a5b3840ca13dca2d0a1a3866575b3d` (rehearsal `531dc171be0f85727c07559e9044cf5f56564d70`), and the prior reviewed HEAD was `0c6c840057705aa787ec2015936cdf5f4a0ca1a5`. Those are preserved as historical milestones. The final release-validation rehearsal below tests the current tree including `F-26` (`TrustedLocalModelLoader`) and reconciled documentation.

### Final November 3, 2026 Rehearsal Commits & SHAs
- **Remote `origin/main` Base SHA:** `4be7f547f4919380a22abdd2122961d559d2f46f`
- **Tested Implementation Feature Commit SHA (`feature/increment17-pipeline-parallelism-paged-kv`):** `53576c16ef96f64797478825b38b00d625748b25` (with subsequent metadata-only documentation freeze commit at `HEAD` containing zero code changes across `app/` and `tests/`)
- **Tested Disposable Integration Rehearsal Merge SHA:** `1b4bf42d43097546371544e9ae7233ebcf98cbe2`
- **Tracked Evidence Artifact:** [`docs/verified-work/evidence/increment17-integration-rehearsal-20261103.json`](evidence/increment17-integration-rehearsal-20261103.json)

### Merge & Conflict Resolution
- **Command:** `git merge --no-ff feature/increment17-pipeline-parallelism-paged-kv` into disposable worktree branch `disposable/inc17-final-rehearsal` rooted at `origin/main` (`4be7f547f4919380a22abdd2122961d559d2f46f`).
- **Conflicts:** 0 merge conflicts (`ort` strategy).
- **Worktree Cleanup:** Disposable worktree and temporary branch cleanly removed after test completion; `main` was not modified.

### Test Execution in Integrated Tree (`.venv-gpu`, Python 3.12.10, Torch 2.5.1+cu121)
1. **Increment 17 Focused Test Suites (18 files, including Real-Model GPU & Real-Artifact Authority Suites):**
   - Command: `python -m pytest tests/test_paged_kv_real_model_authority.py tests/test_increment17_comprehensive_verification.py tests/test_increment17_defect_reproductions.py tests/test_increment17_stress_and_adversarial.py tests/test_paged_kv_*.py tests/test_pipeline_*.py tests/test_inference_paged_kv_sidecar.py`
   - Result: **136 passed in 83.85s** (0 failed, 0 skipped, 0 errors).
2. **Broader Application Regression Selection (12 files):**
   - Command: `python -m pytest tests/test_inference.py tests/test_inference_and_observatory_widgets.py tests/test_observatory.py tests/test_metric_integrity.py tests/test_web_routes.py tests/test_worker_authorization_enforcement.py tests/test_script_authorization.py tests/test_certification_block_reporting.py tests/test_claim_contracts.py tests/test_migrations.py tests/test_migration_compatibility.py tests/test_migration_robustness_and_backup.py`
   - Result: **143 passed, 1 warning in 59.63s** (0 failed).
3. **Combined Integrated Test Suite:** **279 passed, 0 failed**.

---

## 9. Clean-Environment Disabled-by-Default Verification

Clean-environment testing was conducted without fixtures that automatically inject cache keys, trusted identities, initialized managers, or enabled-cache flags:

1. **Caching Disabled by Default:**
   - `settings.paged_kv_enabled` evaluates to `False`.
   - `is_paged_kv_enabled()` returns `False`.
   - `get_paged_kv_manager()` returns `None`.
2. **Ordinary Inference Without Keys:**
   - Tested under clean environment with no `PAGED_KV_STORAGE_KEY` and no `QC_PAGED_KV_ENABLED`.
   - Ordinary inference through mock Ollama backend succeeded with 200 OK (`res["success"] is True`), returning response content without error.
   - Paged KV metadata correctly recorded: `{"enabled": False, "status": "disabled", "verified_reuse_authorized": False}`.
3. **Persistent Storage Not Initialized:**
   - Confirmed that under disabled-cache startup, no cache directory or `.qckv` files are created on disk.
4. **Truthful Metrics Reporting:**
   - `get_paged_kv_metrics()` returns complete baseline JSON:
     `{"status": "disabled", "enabled": False, "verified_reuse_authorized": False, "total_blocks": 0, "active_blocks": 0, "cached_tokens": 0, "ram_bytes": 0}`.
5. **Controlled Error & Bypass Policies:**
   - When cache is explicitly enabled (`QC_PAGED_KV_ENABLED=true`) without key:
     - Under `fail_closed` (default): returns structured API error `{"success": False, "error_code": "paged_kv_configuration_error"}` without leaking paths or keys.
     - Under `bypass`: returns status `bypassed_unavailable` and continues ordinary inference without caching.
6. **No Fallback Verification:**
   - Invalid, untrusted, or synthetic-fixture authorities passed to `ingest_prompt()` or `PyTorchPagedKVAdapter` consistently fail closed and never authorize reuse for a separately loaded real model.
7. **Production Safety Holds Intact:**
   - Worker authorization gates (`test_worker_authorization_enforcement.py`), script permit checks (`test_script_authorization.py`), and deliverable certification containment holds (`test_certification_block_reporting.py`, `test_claim_contracts.py`) all passed without alteration.

---

## 10. Merge-Readiness Verdict

### Final Status:
**Ready for independent approval to merge the exact tested result into main as a disabled development feature.**

### Boundary Summary & Operating Constraints:
1. **Remote `origin/main` Base SHA:** `4be7f547f4919380a22abdd2122961d559d2f46f`
2. **Tested Implementation Feature SHA:** `53576c16ef96f64797478825b38b00d625748b25` (and its metadata-only documentation freeze child commit at `HEAD`)
3. **Tested Integration Rehearsal Merge SHA:** `1b4bf42d43097546371544e9ae7233ebcf98cbe2`
4. **Caching Default:** Strictly disabled by default (`QC_PAGED_KV_ENABLED=false`).
5. **Real-Model Execution Boundary:** Local single-host split execution (`LocalSplitPipelineEngine`) and local real-model KV-cache tensor reuse with real-artifact authority binding (`TrustedLocalModelLoader` + `PyTorchPagedKVAdapter`) are verified on the Desktop GPU (`Qwen/Qwen2.5-0.5B-Instruct`). Remote multi-node split execution with real transformer weights (`qwen-laptop`) and production Ollama tensor-level KV integration remain **NOT IMPLEMENTED / HELD**.
6. **Production Safety Holds:** Main merge approval does NOT authorize production deployment, cache activation, database migrations, live service restarts, remote runtime provisioning, or safety-hold removal; those remain separate administrative decisions.
