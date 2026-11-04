# Increment 17: Comprehensive Stress Test & Real Capability Evaluation Report

**Evaluation Date:** 2026-11-03  
**Repository Branch:** `feature/increment17-pipeline-parallelism-paged-kv`  
**Base Revision:** `origin/main` (`4be7f547f4919380a22abdd2122961d559d2f46f`)  
**Pre-Repair Feature Commit:** `12469444a1d86073915c099f6e0de8cd84123547`  
**Evaluation Scope:** Five-tier capability boundary classification, finite stress budgets, single-node vs multi-stage real transformer equivalence (`Qwen/Qwen2.5-0.5B-Instruct`), pipeline transport & scheduling stress, stage-boundary fault injection, paged KV cache ownership & integrity stress, clean-environment application behavior, warmed GPU/network benchmarks, and defect reproduction & repair (`F-22` through `F-25`).

---

## 1. Real Environment, Hardware & Source Hashes

### Actual Hardware & Runtime Inventory (Verified via Live Probes)
- **Desktop Controller / GPU Host (`localhost`)**:
  - OS: Windows 10 (`10.0.19045-SP0`)
  - CPU: `Intel(R) Xeon(R) CPU W3680 @ 3.33GHz` (6 physical cores, 6 logical processors)
  - RAM: `24.0 GB`
  - GPU: `NVIDIA GeForce GTX 1650 SUPER` (`4095 MiB` VRAM, Compute Capability `7.5`)
  - Python & ML Runtimes: Python `3.12.10`, `pytest 9.1.1`, `torch 2.5.1+cu121`, `transformers 4.45.2` (in `.venv-gpu`)
- **Remote Edge Node (`qwen-laptop` @ `100.101.187.115`)**:
  - OS: Ubuntu Linux `7.0.0-38-generic` (`x86_64`)
  - CPU: `Intel(R) Core(TM) i3-6100U CPU @ 2.30GHz` (2 cores, 4 threads)
  - RAM: `3.7 GiB` (`3,919 MB`)
  - Python Runtime: Standard-library Python `3.12.3` (`torch` and `transformers` **not installed**)
  - Active Production Services (Untouched): `qwen-worker.service` (`MainPID=1561866`), `ollama.service` (`MainPID=1432`), `qc-ollama-ts.service` (`MainPID=519545`)

> [!IMPORTANT]
> Earlier report revisions (`INCREMENT_17_REPAIR_AND_EVALUATION_REPORT.md`) listed inaccurate placeholder hardware specs (`AMD Ryzen 7 5700X`, `RTX 3060 12 GB`, `AMD 5800H 16 GB`) and left an uncleaned `stage1_worker.py` test daemon running on `qwen-laptop:8990`. Both have been corrected and cleaned up in this evaluation.

### Pre-Fix (`12469444a1d86073915c099f6e0de8cd84123547`) vs Post-Fix Source SHA-256 Hashes

| File Path | Pre-Fix SHA-256 (`12469444a1...`) | Post-Fix SHA-256 |
|---|---|---|
| `app/services/paged_kv/__init__.py` | `4c599870251fb9f2b0d4ef8ca91c348fc98b099c09d0b1a46a7d392766bd3877` | `f829cf9812c9942514e83a38282ef5b7ff6f8a5fbbc62e4cbf97da8643453af2` |
| `app/services/paged_kv/hasher.py` | `d94cc3130d4f6cd5fd0d2da308831ace1a35a8bd60a9fc7a375ea98fd3eeaa21` | `d94cc3130d4f6cd5fd0d2da308831ace1a35a8bd60a9fc7a375ea98fd3eeaa21` |
| `app/services/paged_kv/identity.py` | `4f896359cb3af9b5263c9c3579a1a82e6255129919a94bfc25a37cbda463cc2b` | `3d3d8ed5a1110b51d34b9a2215b3084204ac609d66f5f79ebe3b517e9730874a` |
| `app/services/paged_kv/model_loader.py` | *(not present in tree)* | `d619be3f6b19791e8d49e34b48ca8b7af806de1c5e4f0cadf9030d8379919283` (LF: `47aeea18804bc15e33d31dce5e0ea6a8694942eaf3b554bf2a660ad14e318c63`) |
| `app/services/paged_kv/page_table.py` | `7a1b1bb2e66e02bafbc798b66478e26fd081f348bd17c2739bbbf2563803f24c` | `283970b59cf13ea8c63940ae51fa74d2dd24c8d68a1267908b8ae5dc0cefa0ed` |
| `app/services/paged_kv/store.py` | `00fa9c8be8098c93ea030479013f51fbbaadede46d76d2f2385151346604b628` | `a5280f1138293d3882da459fa1c80663d5f3a92565e2ed60af8a9ea581613837` |
| `app/services/paged_kv/manager.py` | `55e146f7a34ef240ac0b3da267589294c2662747c33fffb47733d3473978f3a4` | `b99565b5791dc7dd92e0c223b688d566be7419a311e8fe36dd9381b2c6562bd2` |
| `app/services/paged_kv/pytorch_adapter.py` | `1c7e5aa3cea8d9f9d57b4a2710f4085d0a02d65d5e0b01417ba152b9ec91a8b2` | `0c8a27ec96370e292cadd83425b3a85177964e5fdef4c2273704865d322b23d5` |
| `app/services/pipeline_parallel/__init__.py` | `b83a476bf636120c3b20abbe309eb534828ec9a49c9643d7b385a301431b651c` | `4f76e6080f0e0247a2230f815f81daf9b0a6be8fbe1682f2c21d1c238c880ba9` |
| `app/services/pipeline_parallel/activation_protocol.py` | `77f299667d76cc4c32838517fb7ca9e067b061d1f156f7377a792c8ae7bcd509` | `1113e0eb8f37ded3fcbb31de3fe3f53d88f5e2554727e1c19486540614426d29` |
| `app/services/pipeline_parallel/topology.py` | `f19d5b0c3eb12bdf8def548b434101dc775e5490bb94a19c79fbc93e1e926a6f` | `4e58d8fc985dcc4f1e2b27ab02072ea608e2322be91d5fd4139db3c61efa440f` |
| `app/services/pipeline_parallel/runner.py` | `da6f70714acb245cacd7f584b600044d0d677d2c3def83a05c65611aba71419b` | `c7b6141d471d82910d210fc6675ab0bd1ee22ee36d5dfdec891ac5000ed0b9e6` |
| `app/services/pipeline_parallel/socket_transport.py` | `fe3b74d9515be8d07b9922a9b94d366402534237e948e63129aadd9f3e3d9c06` | `4a94d042beb0740e24aeb203c28108140f8f2e494e2bf5fb2128c473201b7c2d` |
| `app/services/pipeline_parallel/stage1_server.py` | `e8bdd761ecd73469e93598cc55f9f2c28f28daab43891966ad25fb1a49c2a00f` | `e8866a9a4af2ca137600c2a7f7c3557399cdd16e072ceae70c0bc10125dd7189` |
| `app/services/pipeline_parallel/pytorch_split_engine.py` | *(not present in tree)* | `abffa3ec83b06d8b812ad2961196078b88d31e0890e3496d541d2e4eeed3e7cc` |
| `app/services/inference.py` | `8420ff7b40eccd62bd896851313e33f475f46cf3c02f457ea1960205b2be3ef0` | `8420ff7b40eccd62bd896851313e33f475f46cf3c02f457ea1960205b2be3ef0` |

### Pinned Local Model Artifact Hashes (`Qwen/Qwen2.5-0.5B-Instruct` @ `7ae557604adf67be50417f59c2c2f167def9a775`)

| Artifact Role | File Name | Byte Size | Streamed SHA-256 Digest |
|---|---|---:|---|
| `weights` | `model.safetensors` | `988,097,824` | `fdf756fa7fcbe7404d5c60e26bff1a0c8b8aa1f72ced49e7dd0210fe288fb7fe` |
| `tokenizer` | `tokenizer.json` | `7,031,645` | `c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539` |
| `chat_template` | `tokenizer_config.json` | `7,305` | `5b5d4f65d0acd3b2d56a35b56d374a36cbc1c8fa5cf3b3febbbfabf22f359583` |
| `rope_config` | `config.json` | `659` | `18e18afcaccafade98daf13a54092927904649e1dd4eba8299ab717d5d94ff45` |

---

## 2. Six-Category Capability Boundary Classification

| Category | Capability Path | Status | Implementation & Scope | Blockers & Numerical / Performance Constraints |
|---|---|---|---|---|
| **1** | **Local Simulated Stages** | **PASS** | `app/services/pipeline_parallel/runner.py` (`PipelineStageRunner`) | Deterministic mathematical transform (`_default_layer_transform`); enforces upstream `stage_index`, `hidden_dim`, and per-session `token_index` ordering/replay rejection (`F-22`). Does not establish transformer accuracy. |
| **2** | **Remote Transport of Synthetic Activations** | **PASS** | `app/services/pipeline_parallel/{activation_protocol,socket_transport,stage1_server}.py` | Authenticated `QCACT1` binary framing over TCP loopback (`396.5 steps/s` single session, `368.3–374.2 steps/s` across 4–16 concurrent sessions) and live Tailscale LAN to `qwen-laptop` (`173.6 steps/s`, `5.17 ms` p50 RTT). Enforces `max_concurrent_sessions` overload rejection (`rejected_overloaded`), `client_timeout_s`, and mid-frame truncation detection (`F-23`). **Synthetic frame steps/s only — never model generation tokens/s.** |
| **3** | **Local Split Execution Using Real Weights** | **PASS** | `app/services/pipeline_parallel/pytorch_split_engine.py` (`LocalSplitPipelineEngine`) | Partitions real `Qwen/Qwen2.5-0.5B-Instruct` (24 layers, `hidden_dim=896`, FP16/FP32) across 2-stage (`12/12`, `8/16`, `16/8`) and 3-stage (`8/8/8`) boundaries on `cuda:0` with `QCACT1` wire serialization at every boundary. Bit-exact (`0.000000`) intermediate activation and final logit parity; 100% 8-step greedy decode parity. **Local partition equivalence does not establish distributed speedup; `generate_greedy` recomputes the prefix with `use_cache=False` as incremental per-stage KV decode is not yet implemented.** |
| **4** | **Remote Split Execution Using Real Weights** | **NOT IMPLEMENTED / NOT TESTED** | N/A | **Blocker:** `qwen-laptop` (`100.101.187.115`) has 3.7 GiB RAM and Python 3.12.3 without `torch` or `transformers`. Installing new runtimes on remote nodes is prohibited under current operational rules. Prior `stage1_worker.py` on `qwen-laptop` executed simulated math (`_default_layer_transform`), not real Stage 1 transformer weights. |
| **5** | **Local Genuine KV-Tensor Reuse** | **PASS (Local PyTorch GPU)** | `app/services/paged_kv/{model_loader,identity,manager,pytorch_adapter}.py` (`TrustedLocalModelLoader` + `PyTorchPagedKVAdapter`) | Verifies pinned local `Qwen/Qwen2.5-0.5B-Instruct` artifacts via streaming SHA-256 before/after offline load, binds live in-memory model instance (`F-26`), and bridges 16-token `PagedKVManager` pages and `QCKVP2` disk envelopes with Hugging Face `DynamicCache` on `cuda:0`. Achieves `3.41x` (64-tok prefix) to `4.77x` (128-tok prefix) warmed RAM prefill speedup (`0.029297–0.050903` FP16 max logit diff within `<= 0.1` tolerance, `0.000031` in FP32, 100% greedy token agreement). **Warm-prefill gains do not establish end-to-end request gains.** |
| **6** | **Production Inference Integration** | **DISABLED BY DEFAULT / SIDECAR ONLY** | `app/services/inference.py` | Defaults to `QC_PAGED_KV_ENABLED=false` (no cache initialization or key required). When enabled, supports `fail_closed` vs `bypass` error policies (`F-18`) and records sidecar token-block metadata. Closed-binary production Ollama / `llama.cpp` HTTP servers do not expose internal KV tensor buffers for external page injection. |

---

## 3. Finite Stress Budgets

All diagnostic stress and benchmark runs executed within explicit finite budgets:
- **Concurrent Sessions & Requests**: Escalated `1 -> 4 -> 8 -> 16` concurrent TCP sessions (`max_concurrent_sessions=16`); overload rejection tested at `20` concurrent sessions.
- **Input/Context Lengths & Frame Sizes**: `0` (rejected), `4`, `16`, `32`, `40`, `64`, `128`, and `136` tokens; `QCACT1` wire frame size bounded to `DEFAULT_MAX_WIRE_FRAME_BYTES = 266,240` bytes (`1,950` bytes on wire for `Qwen2.5-0.5B` `hidden_dim=896` FP16).
- **CPU, RAM, GPU VRAM, Disk & Network Budgets**: Peak GPU VRAM `1,340.69 MiB` / `4,095 MiB` (32.7%); process RSS `954.40 MiB` / `24,576 MiB`; test RAM cache budget `4 KB – 2 MB`; test disk cache budget `2 KB – 20 MB` (`max_disk_bytes` enforced).
- **Per-Request & Total Test Duration**: Per-client socket timeout `0.3s – 5.0s`; dedicated subprocess kill/recovery `< 3.0s`; full 19-file Increment 17 suite `31.88s`; broader 10-file regression suite `56.92s`.
- **Fault-Injection Frequency & Retries**: Deterministic per-boundary fault matrix; client retry ceiling capped at `max_retries = 2` (3 attempts total).
- **Cleanup Deadlines & Stop Conditions**: `< 2.0s` socket/thread/subprocess teardown; verified `0` active sequences, `0` active sessions, `PORT_8990_CLOSED` on `qwen-laptop`, and unchanged production service `MainPID`s.

---

## 4. Defects Discovered on Commit `12469444a1d86073915c099f6e0de8cd84123547` & Verified Repairs

All 8 regression tests in [`tests/test_increment17_defect_reproductions.py`](../../tests/test_increment17_defect_reproductions.py) **failed on commit `12469444a1d86073915c099f6e0de8cd84123547` (`8 failed in 5.78s`)** before repairs and **passed (`8 passed`)** after repairs:

1. **Defect F-22 (`activation_protocol.py`, `runner.py`) — Missing Freshness, Dtype, Finite Float & Stage/Token Ordering Validation**:
   - **Pre-Fix Failure**: `ActivationProtocol.decode_frame` unpacked `ts` and ignored it (accepting 10-day-old replayed frames and 1-hour-future frames), mapped unknown `dtype_code=99` to `float32`, and accepted `NaN`/`Inf` floats (causing `PipelineStageRunner.process_egress` `argmax` to silently return token `0`). `PipelineStageRunner` accepted frames from wrong `stage_index`, wrong `hidden_dim` in `process_egress`, and duplicate/replayed/out-of-order `token_index` values.
   - **Fix**: Added `max_age_seconds=300.0` and `max_future_skew_seconds=30.0` freshness checks, strict `dtype_code in (1, 2)` validation, `math.isfinite()` enforcement on encode/decode, printable ASCII + null-padding validation on `session_id`, upstream `stage_index == self.config.stage_index - 1` and `hidden_dim` validation, and bounded LRU per-session `token_index` contiguous ordering/replay rejection.
2. **Defect F-23 (`socket_transport.py`, `stage1_server.py`) — Unbounded Server Concurrency, Stalled Client Hang & Silent Mid-Frame Truncation**:
   - **Pre-Fix Failure**: `Stage1SocketServer` had no `max_concurrent_sessions` or `client_timeout_s`, and `recv_framed_bytes` returned `None` (clean EOF) when a peer disconnected after sending 50 of 500 declared frame bytes.
   - **Fix**: Added `max_concurrent_sessions` (returning `{"status": "rejected_overloaded"}` when saturated), per-client `client_timeout_s`, session ordering state cleanup on client disconnect, `DEFAULT_MAX_WIRE_FRAME_BYTES` cap, and `ConnectionError` on truncated mid-frame EOF.
3. **Defect F-24 (`page_table.py`, `store.py`, `manager.py`) — Partial Ingest Block Leak, `remap_block` Ref Leak, Missing COW & Unbounded Disk Growth**:
   - **Pre-Fix Failure**: When `ingest_prompt` matched 2 prefix blocks and Block 1's disk file was corrupted (or a subsequent `allocate_block` raised `MemoryError`), Block 0 remained allocated (`free_blocks` dropped from 16 to 14, `active_sequences=1`, `total_matched_tokens` inflated by 32). `VirtualBlockTable.remap_block` overwrote an existing slot without decrementing the old block's `ref_count`, lacked `copy_on_write_block`, and did not track `PhysicalBlock.generation`. `PagedKVStore` had no `max_disk_bytes` enforcement.
   - **Fix**: Wrapped `ingest_prompt` block attachment/allocation in a transactional `try...except` that calls `self.release_sequence(sequence_id)` and rolls back counters on any error; released prior mappings if an active `sequence_id` is re-ingested; tracked `seq_generations` and released prior blocks in `remap_block`; added `copy_on_write_block`; enforced `max_disk_bytes` LRU eviction in `PagedKVStore`.
4. **Defect F-25 (`pytorch_adapter.py`) — Double-Ingest Accounting Corruption, Full-Prefix Empty Suffix Crash & FP32 Downcast**:
   - **Pre-Fix Failure**: Calling `prepare_prefill_context(seq_id, tokens)` followed by `extract_and_cache_sequence_kv(seq_id, tokens)` invoked `manager.ingest_prompt` twice on the same `seq_id`, duplicating `seq_tables[seq_id]` (`[0, 1, 0, 1]`), doubling `ref_count` to 2, and recording 32 false matched tokens on a cold run. On a 100% block-aligned cache hit (32 tokens = 2 blocks), `prepare_prefill_context` returned `input_ids=[]`, crashing `AutoModelForCausalLM.forward()`. `serialize_page_tensors` downcast FP32 tensors to FP16, and `deserialize_page_tensors` used `weights_only=False`.
   - **Fix**: Checked existing sequence block hashes in `extract_and_cache_sequence_kv` before calling `ingest_prompt`; capped `max_reusable_blocks = max(0, (len(token_ids) - 1) // 16)` in `prepare_prefill_context` so at least 1 suffix token is always passed to CausalLM `forward()`; preserved native tensor dtype on serialize; used `weights_only=True` on deserialize; registered `store.on_page_evicted` callback to evict `_ram_tensor_cache` entries when RAM pages are evicted or deleted.
5. **Defect F-26 (`model_loader.py`, `identity.py`, `manager.py`, `pytorch_adapter.py`) — Real-Artifact Loader & Live Model-Instance Authority Binding**:
   - **Pre-Fix Gap**: `test_sec3_real_kv_cache_cold_warm_partial_full_evict_restore_and_concurrent` previously issued `IssuedReuseAuthority` over 40-byte synthetic marker files while separately loading `Qwen/Qwen2.5-0.5B-Instruct` via `AutoModelForCausalLM.from_pretrained` without pinned snapshot revision or live model-instance binding.
   - **Fix**: Implemented `TrustedLocalModelLoader.load_verified_model` requiring an explicit 40-char hex snapshot revision (`7ae557604adf67be50417f59c2c2f167def9a775`), streaming SHA-256 verification (`stream_file_sha256`) of the actual `model.safetensors`, `tokenizer.json`, `tokenizer_config.json`, and `config.json` before and after offline loading (preventing TOCTOU replacement), live in-memory parameter/config fingerprinting (`compute_live_model_fingerprint`, `verify_live_model_instance`), and `require_instance_binding=True` enforced across `IdentityIssuanceAuthority`, `PagedKVManager`, and `PyTorchPagedKVAdapter`. Verified across all 7 real-artifact authority binding cases in [`tests/test_paged_kv_real_model_authority.py`](../../tests/test_paged_kv_real_model_authority.py).

---

## 5. Warmed Performance Benchmarks (Section 8)

All GPU benchmarks were collected on `NVIDIA GeForce GTX 1650 SUPER` (`4095 MiB`, `Qwen/Qwen2.5-0.5B-Instruct`, `float16`) after **5 explicit CUDA warmup iterations** (`n=12` measured iterations per point).

### A. Category 2: Synthetic `QCACT1` Framing & Transport Steps/Sec (`hidden_dim=896`, `float16`, `1,950` wire bytes/frame)

| Transport Path & Concurrency | Mean Latency (ms) | p50 (ms) | p95 (ms) | p99 (ms) | Aggregate Synthetic Steps/Sec | Error / Rejection Rate |
|---|---:|---:|---:|---:|---:|---|
| Loopback (`127.0.0.1`), 1 session | 2.522 | 2.503 | 2.664 | 2.858 | **396.5 steps/s** | 0% (0/100) |
| Loopback (`127.0.0.1`), 4 sessions | 10.170 | 10.316 | 18.255 | 21.078 | **372.0 steps/s** | 0% (0/160) |
| Loopback (`127.0.0.1`), 8 sessions | 20.512 | 19.694 | 34.811 | 46.850 | **374.2 steps/s** | 0% (0/320) |
| Loopback (`127.0.0.1`), 16 sessions | 40.658 | 38.441 | 73.733 | 96.688 | **368.3 steps/s** | 0% (0/640) |
| Loopback Overload (`20` sessions vs `max=16`) | N/A | N/A | N/A | N/A | N/A | **4 sessions explicitly rejected (`rejected_overloaded`)**, 16 completed |
| Live Tailscale LAN (`Desktop -> qwen-laptop:8990`), 1 session | 5.762 | 5.168 | 8.796 | 11.946 | **173.6 steps/s** | 0% (0/50) |

*(Note: Synthetic framing/transport steps per second measure `QCACT1` wire serialization + HMAC-SHA256 + simulated stage callback only and must never be cited as model token generation throughput.)*

### B. Category 3: Real `Qwen/Qwen2.5-0.5B-Instruct` Single-Node vs Multi-Stage Split Execution on GTX 1650 SUPER

| Workload | Execution Topology | Mean (ms) | p50 (ms) | p95 (ms) | p99 (ms) | Throughput | Wire Bytes | Max Logit Diff |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| **Prefill (16 tokens)** | Single-Node GPU Reference | 65.172 | 65.151 | 67.664 | 67.917 | **245.5 tok/s** | 0 B | 0.0 (ref) |
| **Prefill (16 tokens)** | 2-Stage Split (`0..11`, `12..23`) | 76.989 | 77.877 | 79.658 | 79.910 | **207.8 tok/s** (-15.4%) | 31,136 B | `0.000000` |
| **Prefill (16 tokens)** | 3-Stage Split (`0..7`, `8..15`, `16..23`) | 86.752 | 86.943 | 89.367 | 90.533 | **184.4 tok/s** (-24.9%) | 62,272 B | `0.000000` |
| **Prefill (64 tokens)** | Single-Node GPU Reference | 140.690 | 141.093 | 141.628 | 141.732 | **454.9 tok/s** | 0 B | 0.0 (ref) |
| **Prefill (64 tokens)** | 2-Stage Split (`0..11`, `12..23`) | 171.571 | 171.787 | 173.514 | 173.859 | **373.0 tok/s** (-18.0%) | 124,544 B | `0.000000` |
| **Prefill (64 tokens)** | 3-Stage Split (`0..7`, `8..15`, `16..23`) | 201.992 | 202.000 | 203.015 | 203.455 | **316.8 tok/s** (-30.4%) | 249,088 B | `0.000000` |
| **Prefill (128 tokens)** | Single-Node GPU Reference | 277.011 | 278.173 | 279.508 | 280.358 | **462.1 tok/s** | 0 B | 0.0 (ref) |
| **Prefill (128 tokens)** | 2-Stage Split (`0..11`, `12..23`) | 339.598 | 333.764 | 339.670 | 400.557 | **376.9 tok/s** (-18.4%) | 249,088 B | `0.000000` |
| **Prefill (128 tokens)** | 3-Stage Split (`0..7`, `8..15`, `16..23`) | 392.816 | 392.496 | 396.797 | 399.462 | **325.9 tok/s** (-29.5%) | 498,176 B | `0.000000` |
| **Greedy Decode (8 steps)** | Single-Node GPU (no KV cache) | 74.384 / step | 75.636 | 78.729 | 78.729 | **13.44 tok/s** | 0 B | 100% token match |
| **Greedy Decode (8 steps)** | 2-Stage Split (`0..11`, `12..23`) | 89.115 / step | 91.277 | 93.910 | 93.910 | **11.22 tok/s** (-16.5%) | 303,576 B | 100% token match |

> [!NOTE]
> **Where Parallelism Is Slower:** When a model fits on a single GPU (`Qwen2.5-0.5B` uses `1.34 GiB` of `4.0 GiB` VRAM), splitting stages introduces `6.9 ms` (16 tokens) to `60.0 ms` (128 tokens) of CPU-GPU synchronization and `QCACT1` struct/HMAC framing overhead per forward pass (`15.4%` to `30.4%` slower than single-node execution). Pipeline parallelism only provides a latency/capacity advantage when model weights exceed single-node VRAM.

### C. Category 5: Genuine `PyTorchPagedKVAdapter` KV-Cache Reuse on GTX 1650 SUPER (Warmed CUDA)

| Workload | Cache Mode | Mean (ms) | p50 (ms) | p95 (ms) | p99 (ms) | Speedup vs Warmed Cold | Max Logit Diff (FP16) | Top-1 Token Match |
|---|---|---:|---:|---:|---:|---:|---:|---|
| **64-token prefix + 8-token suffix** | Cold Full Prefill (72 tokens) | 276.064 | 276.034 | 278.447 | 278.975 | 1.00x | 0.0 (ref) | Reference |
| **64-token prefix + 8-token suffix** | Warm Tier 1 RAM KV Reuse (4 pages) | 80.949 | 80.962 | 83.201 | 85.705 | **3.41x** (-70.7%) | `0.029297` (`0.000031` in FP32) | **100% Match** |
| **64-token prefix + 8-token suffix** | Warm Tier 2 Disk `QCKVP2` Restore | 160.121 | 160.075 | 163.744 | 165.494 | **1.72x** (-42.0%) | `0.029297` | **100% Match** |
| **128-token prefix + 8-token suffix** | Cold Full Prefill (136 tokens) | 405.562 | 406.019 | 407.178 | 407.727 | 1.00x | 0.0 (ref) | Reference |
| **128-token prefix + 8-token suffix** | Warm Tier 1 RAM KV Reuse (8 pages) | 84.941 | 84.603 | 88.526 | 91.310 | **4.77x** (-79.1%) | `0.050903` (`0.000033` in FP32) | **100% Match** |
| **128-token prefix + 8-token suffix** | Warm Tier 2 Disk `QCKVP2` Restore | 248.207 | 244.314 | 247.755 | 318.519 | **1.63x** (-38.8%) | `0.050903` | **100% Match** |

---

## 6. Works / Fails / Unproven Matrix (Section 10 Deliverable)

| Capability/scenario | Execution category | Expected behavior | Observed behavior | PASS / FAIL / NOT TESTED / NOT IMPLEMENTED | Evidence |
|---|---|---|---|---|---|
| **3-Stage Local Simulated Pipeline (`PipelineStageRunner`)** | 1. Local simulated stages | Deterministic layer math (`_default_layer_transform`); reject wrong `stage_index`, wrong `hidden_dim`, and replayed/out-of-order `token_index` | Exact mathematical match; wrong stage, wrong `hidden_dim`, duplicate `token_index=0`, and skipped `token_index=2` all raise `ValueError` | **PASS** | `tests/test_increment17_defect_reproductions.py::test_f22_stage_runner_rejects_wrong_stage_hidden_dim_replay_and_reordering` |
| **Loopback & Multi-Session `QCACT1` Socket Streaming (`1..16` sessions)** | 2. Actual network transport of synthetic activations | Stream authenticated `QCACT1` frames concurrently with 0 errors and fair progress between short (4-frame) and long (30-frame) sessions | `396.5 steps/s` (1 session), `368.3–374.2 steps/s` (4–16 sessions), `0` errors; short sessions complete in `<25%` of long session wall time | **PASS** | `tests/test_increment17_comprehensive_verification.py::test_sec4_mixed_short_long_and_slow_stage_backpressure` |
| **Live Tailscale LAN `QCACT1` Transport (`Desktop -> qwen-laptop:8990`)** | 2. Remote transport of synthetic activations | Stream `QCACT1` FP16 frames (`1,950` bytes) over WireGuard to `100.101.187.115:8990` with HMAC verification and clean post-test teardown | `50/50` frames verified (`p50 = 5.168 ms`, `p95 = 8.796 ms`, `173.6` synthetic steps/s); ephemeral daemon terminated and `PORT_8990_CLOSED` confirmed | **PASS** | `docs/verified-work/evidence/increment17-benchmark-and-cleanup-20261103.json` (`lan_qwen_laptop_1_session`) |
| **Overload Rejection & Stalled Client Isolation (`Stage1SocketServer`)** | 2. Actual network transport of synthetic activations | Reject connections above `max_concurrent_sessions` with `rejected_overloaded`; drop stalled clients after `client_timeout_s` | Excess clients immediately receive `status="rejected_overloaded"`; stalled client times out in `0.3s` and healthy client succeeds | **PASS** | `tests/test_increment17_defect_reproductions.py::test_f23_stage1_server_overload_rejection_and_stalled_client_isolation` |
| **Stage-Boundary Fault Injection & Subprocess Crash Recovery** | 2. Actual network transport of synthetic activations | Reject corrupt HMAC/magic/version/dtype/length, stale/future timestamps, `NaN`/`Inf`, mid-frame truncation; bound retries on process kill | All 14 wire fault classes rejected fail-closed (`ValueError` / `ConnectionError`); dedicated subprocess kill detected with bounded 2-retry limit | **PASS** | `tests/test_increment17_comprehensive_verification.py::test_sec5_fragmented_tcp_and_midframe_truncation`, `test_sec5_dedicated_subprocess_stage_failure_and_bounded_retry` |
| **Local Split Execution (`2-stage` & `3-stage`) with `Qwen/Qwen2.5-0.5B-Instruct`** | 3. Local split execution with real transformer weights | Bit-exact (`<= 1e-3`) intermediate boundary activations and final logits across `12/12`, `8/16`, `16/8`, and `8/8/8` splits; empty input rejected | `max_hidden_diff = 0.000000` and `max_logit_diff = 0.000000` across all 4 partitions, short/Unicode/136-token inputs, and 8-step greedy decode | **PASS** | `tests/test_increment17_comprehensive_verification.py::test_sec3_split_pipeline_stage_partitions_and_intermediate_activations`, `test_sec3_split_pipeline_short_unicode_long_and_multistep_decode` |
| **Remote Multi-Node Split Execution with Real Transformer Weights** | 4. Remote split execution with real weights | Execute Stage 1 transformer layers with real weights on `qwen-laptop` (`100.101.187.115`) | `qwen-laptop` (`i3-6100U`, `3.7 GiB` RAM) lacks `torch`/`transformers`; installing new runtimes on remote nodes is prohibited | **NOT IMPLEMENTED** | Host runtime blocker documented; requires operator approval to provision CPU PyTorch or `llama.cpp` RPC on `qwen-laptop` |
| **Genuine GPU KV-Cache Reuse (`TrustedLocalModelLoader` + `PyTorchPagedKVAdapter` + `Qwen2.5-0.5B`)** | 5. Local genuine KV-tensor reuse | Verify pinned local artifacts (`7ae557604adf67be50417f59c2c2f167def9a775`), bind live model instance, and reuse 16-token `DynamicCache` pages across cold, warm partial-prefix, 100% block-aligned prefix, and Tier 2 disk-restored prefill | `3.41x` (64-tok) to `4.77x` (128-tok) warmed RAM prefill speedup; `0.000031` FP32 / `0.029–0.051` FP16 max logit diff (within `<= 0.1` tolerance); 100% greedy token agreement | **PASS** | `tests/test_paged_kv_real_model_authority.py`, `tests/test_increment17_comprehensive_verification.py::test_sec3_real_kv_cache_cold_warm_partial_full_evict_restore_and_concurrent` |
| **Closed-Binary Production Ollama / `llama.cpp` External KV Injection** | 6. Production inference integration | Inject external `PagedKVStore` pages into running `ollama.service` HTTP backend | Ollama `/api/chat` and `/api/generate` do not expose internal KV tensor buffers; `app/services/inference.py` defaults to `QC_PAGED_KV_ENABLED=false` (sidecar accounting only when enabled) | **NOT IMPLEMENTED** | Architectural blocker documented in `docs/architecture/INCREMENT_17_INFERENCE_BACKEND_DECISION.md` |
| **Paged KV Ownership, COW Fork, Transactional Rollback & Disk/RAM Quotas** | Cache Ownership & Integrity (Section 6) | Preserve live pinned pages, unshare COW forks, roll back partial ingests on corrupt/exhausted blocks, enforce `max_ram_bytes` & `max_disk_bytes` | Live blocks never overwritten; COW fork unshares without mutating parent; failed ingest rolls back `free_blocks` to 16; disk/RAM quotas enforced | **PASS** | `tests/test_increment17_defect_reproductions.py::test_f24_*`, `tests/test_paged_kv_page_ownership.py` |
| **9-Field `ExecutionNamespace/1.0`, `QCKVP2` Disk Integrity & Authority Boundary** | Cache Ownership & Integrity (Section 6) | Mutating any of the 9 namespace fields prevents reuse; reject tampered/substituted/v1/wrong-key disk pages and forged/revoked authorities | `0` cross-namespace hits across all 9 field mutations; key rotation, bit-flips, and forged/foreign/revoked authorities rejected fail-closed | **PASS** | `tests/test_increment17_comprehensive_verification.py::test_sec6_*`, `tests/test_paged_kv_authority_and_app_behavior.py` |
| **Clean-Environment Application Modes (`QC_PAGED_KV_ENABLED=false` default)** | Controlled Application Behavior (Section 7) | Disabled by default without keys or disk init; `fail_closed` vs `bypass` honored when enabled; truthful health/metrics/UI | Ordinary inference succeeds with no key when disabled; no cache directory created; `fail_closed` returns structured error; `bypass` reports `verified_reuse_authorized=False` | **PASS** | `tests/test_paged_kv_authority_and_app_behavior.py` (23 passed) |

---

## 7. Host Integrity, Safe Operating Envelope & Next Steps

### Cleanup & Host Integrity Verification
- **Remote Node (`qwen-laptop` @ `100.101.187.115`)**:
  - Ephemeral `stage1_worker.py` and `/tmp/ephemeral_stage1_bench.py` processes terminated (`PORT_8990_CLOSED`) and temporary files deleted (`NO_TEST_FILES`).
  - Production services verified active with unchanged PIDs: `qwen-worker.service` (`MainPID=1561866`), `ollama.service` (`MainPID=1432`), `qc-ollama-ts.service` (`MainPID=519545`).
- **Local Desktop Repository**:
  - All temporary scratch scripts (`scratch/*.py`, `scratch/tmp_paged_kv_tier5`) removed.
  - All 19 Increment 17 + inference/orchestrator test suites (`140 passed`) and 10 broader regression suites (`132 passed`) verified clean (`272 passed, 0 failed`).

### Safe Operating Envelope Demonstrated by Testing
- **Pipeline Socket Transport (`Stage1SocketServer`)**: Up to `16` concurrent streaming sessions (`max_concurrent_sessions=16`), `5.0s` per-client socket timeout, frame payloads `<= 256 KB` (`DEFAULT_MAX_WIRE_FRAME_BYTES = 266,240`), timestamp freshness within `[-30s, +300s]`.
- **Local Split GPU Execution & `PyTorchPagedKVAdapter`**: Models fitting within `4 GiB` VRAM on `NVIDIA GeForce GTX 1650 SUPER` (`Qwen/Qwen2.5-0.5B-Instruct` uses `1.34 GiB` VRAM), prompt lengths up to `136+` tokens, with Tier 1 RAM and Tier 2 `QCKVP2` disk quotas explicitly configured.
- **Production Controller**: Keep `QC_PAGED_KV_ENABLED=false` (default) in production until a PyTorch-backed worker route is formally promoted.

### Ranked Next Steps
1. **Scoped Operator Proposal for Tier 4 (Remote Split Execution with Real Weights)**: Approve installing a CPU-only PyTorch wheel (`torch==2.5.1+cpu`, `transformers==4.45.2`) inside an isolated virtualenv (`/home/emceemcee/.venv-stage1`) on `qwen-laptop` (or provisioning `llama.cpp` `rpc-server`) so `Stage1SocketServer` can run `TransformerStagePartition` with real Stage 1 weights over Tailscale.
2. **Vectorized Tensor Framing in `ActivationProtocol`**: Replace per-element Python `struct.pack`/`unpack` loops in `LocalSplitPipelineEngine` with buffer-protocol (`memoryview` / `numpy.frombuffer` / `torch.frombuffer`) zero-copy byte views to reduce `QCACT1` framing overhead from `~0.43 ms/token` to `<0.05 ms/token`.
3. **Incremental Decode KV-Cache Support in `LocalSplitPipelineEngine`**: Extend `TransformerStagePartition.forward_stage` to maintain per-stage `DynamicCache` instances across autoregressive decode steps so multi-stage greedy decoding scales $O(1)$ per step instead of recomputing prefix tokens.
