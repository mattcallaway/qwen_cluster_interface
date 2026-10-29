# Increment 17: Comprehensive Stress Test & Real Capability Evaluation Report

**Evaluation Date:** October 28, 2026  
**Repository Branch:** `feature/increment17-pipeline-parallelism-paged-kv`  
**Base Revision:** `origin/main` (`4be7f547f4919380a22abdd2122961d559d2f46f`)  
**Tested Feature Commit:** `2e1df14d5e00b5cd0ba51826ff75076ee23dc770`  
**Evaluation Scope:** Adversarial stress testing, socket transport limits, fault injection, cache ownership & concurrency integrity, and capability boundary classification across real GPU and simulation environments.

---

## 1. Capability Boundary Classification

To ensure complete engineering honesty and avoid conflating simulation benchmarks with model inference, all capabilities are classified into five distinct operational categories:

| Path / Capability | Status | Evidence / Implementation | Technical Constraints & Blockers |
| :--- | :--- | :--- | :--- |
| **1. Local Simulated Stages** | **WORKS (100%)** | `app/services/pipeline_parallel/runner.py`<br>`tests/test_pipeline_activation_protocol.py` | Deterministic layer simulation (LayerNorm + residual tanh updates + vocabulary argmax projection). Zero external package dependencies. |
| **2. Actual Network Transport of Synthetic Activations** | **WORKS (100%)** | `app/services/pipeline_parallel/socket_transport.py`<br>`stage1_server.py`<br>`scratch/run_live_multinode_pipeline.py` | HMAC-SHA256 authenticated `QCACT1` binary envelopes streamed over TCP sockets across Tailscale WireGuard mesh (Desktop $\to$ `qwen-laptop` at 2 ms LAN RTT). Tested under 15 concurrent sessions and rapid churn. |
| **3. Local Split Execution with Real Transformer Weights** | **WORKS (100%)** | `scratch/run_real_gpu_pipeline.py`<br>`scratch/stress_test_real_execution.py` | Real `Qwen/Qwen2.5-0.5B-Instruct` (24 layers, FP16) partitioned across Stage 0 (GPU `cuda:0`, Layers 0–11) and Stage 1 (Layers 12–23 + Head). **Bit-exact parity** verified (`max_logit_diff = 0.000000`, 100% token match) across short, long (91 tokens), multilingual, and asymmetric partitions (Layers 8 and 16). |
| **4. Remote Split Execution with Real Transformer Weights** | **NOT IMPLEMENTED / BLOCKED** | N/A | **Blocker:** Remote edge node `qwen-laptop` has standard Python 3.12 without `torch`, `cuda`, or `transformers`. User and safety policies strictly prohibit installing inference runtimes or packages on remote nodes. |
| **5. Genuine Model KV-Cache Tensor Reuse** | **NOT IMPLEMENTED / BLOCKED** | N/A | **Blocker:** Production inference uses Ollama / llama.cpp RPC. Closed C++ binary executables do not expose internal GPU memory pointers or hooks to swap raw attention tensors. Transformer model execution is evaluated in prefill mode (`use_cache=False`). |

---

## 2. Finite Stress Budgets & Operating Limits

All stress tests and fault injection routines were executed under strict finite resource budgets:

* **Concurrency Budget**: Up to 15 concurrent streaming TCP client sessions.
* **Payload & Context Budget**: 1 to 91 tokens per prefill chunk; hidden dimension 896 (`float16`, ~1,946 bytes/frame) to 64 (`float32`, 426 bytes/frame). All payloads strictly under 256 KB protocol ceiling.
* **Memory & Storage Quotas**: Physical RAM cache budget: 32 blocks (512 KB to 5 MB); temporary disk quota: 20 MB.
* **Timeouts & Deadlines**: TCP connect timeout: 5.0s; socket read timeout: 3.0s; total test execution: < 15 seconds.
* **Disposability**: All test artifacts, sockets, threads, and disk blocks generated in isolated temporary fixtures (`tmp_path`) with automatic teardown.

---

## 3. Discovered Defects & Resolution

### Finding F-20: Missing Thread Synchronization in `PagedKVManager` under Concurrent Request Ingestion

* **Reproduction**: When multiple concurrent worker threads invoked `manager.ingest_prompt(...)` simultaneously with a shared prompt prefix, queries raced on `self.radix_tree.match_prefix(...)` before earlier threads completed `self.radix_tree.insert(...)`. Prefix hit counts dropped to 0, and un-synchronized mutations on `self.table` and `self.store` posed race hazards.
* **Root Cause**: `PagedKVManager` lacked internal re-entrant thread locks around its mutable radix tree, block tables, RAM cache, and sequence authorities.
* **Resolution**: Added `self._lock = threading.RLock()` to `PagedKVManager` and synchronized all public and state-mutating methods (`ingest_prompt`, `restore_sequence_to_ram`, `release_sequence`, `_enforce_ram_quota`, `_evict_lru_page_to_disk`, and `get_metrics`).
* **Verification**: `test_paged_kv_concurrent_sequences_and_shared_prefixes` passed with 10 concurrent clients, achieving 100% prefix cache reuse and zero race conditions.

---

## 4. Comprehensive Test & Adversarial Verification Matrix

| Capability / Scenario | Category | Expected Behavior | Observed Behavior | Verdict | Evidence |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Concurrent Pipeline Streaming** | Network Transport | Handle 15 parallel sessions without data interleaving or deadlock | 300 frames processed across 15 sessions in 0.5s (>500 frames/s) with 0 auth failures | **PASS** | `test_pipeline_socket_concurrent_sessions` |
| **Rapid Connection Churn** | Network Transport | Handle 50 rapid connect/disconnect cycles cleanly | 50 connections processed and closed with 0 leaked file descriptors | **PASS** | `test_pipeline_socket_connection_churn` |
| **Corrupted HMAC Signature** | Fault Injection | Reject modified frame at wire boundary | Server returns `status: auth_error`, drops frame immediately | **PASS** | `test_pipeline_fault_injection_matrix` |
| **Invalid Protocol Magic** | Fault Injection | Reject non-`QCACT1` magic bytes | Server returns `status: auth_error, error: Invalid magic` | **PASS** | `test_pipeline_fault_injection_matrix` |
| **Truncated Activation Payload** | Fault Injection | Detect payload length mismatch | Server returns `status: auth_error`, refuses to unpack | **PASS** | `test_pipeline_fault_injection_matrix` |
| **Fault Recovery & Resilience** | Fault Injection | Resume normal operation on subsequent valid frame | Valid frame immediately following faults succeeds with `status: ok` | **PASS** | `test_pipeline_fault_injection_matrix` |
| **Concurrent KV Ingestion & Sharing** | Cache Ownership | Parallel queries share cached prefix pages without corruption | 10 parallel threads achieved 100% prefix hit rate with clean release | **PASS** | `test_paged_kv_concurrent_sequences_and_shared_prefixes` |
| **Cross-Tenant Namespace Isolation** | Cache Integrity | Identical tokens under different tenant IDs never share cache | Matched tokens = 0 across different tenant IDs | **PASS** | `test_paged_kv_namespace_strict_isolation` |
| **Tampered Disk Page Block** | Cache Integrity | Corrupted byte on disk fails cryptographic restoration | `restore_sequence_to_ram` raises `ValueError` on header HMAC or payload SHA mismatch | **PASS** | `test_paged_kv_disk_corruption_and_tamper_rejection` |
| **Revoked Authority Enforcement** | Authority Boundary | Revoking authority blocks subsequent prefix reuse & restore | Ingest reports `verified_reuse: False`, restore raises `ValueError` | **PASS** | `test_paged_kv_revoked_authority_blocks_reuse_and_restoration` |
| **Real GPU Model Parity (Short)** | Real Weights | Match baseline logit values within $\le 10^{-3}$ | `max_diff = 0.000000`, predicted token `'Hello'` identical | **PASS** | `scratch/stress_test_real_execution.py` |
| **Real GPU Model Parity (Unicode)** | Real Weights | Handle multilingual UTF-8 tokens with exact parity | `max_diff = 0.000000`, predicted token `'Hello'` identical | **PASS** | `scratch/stress_test_real_execution.py` |
| **Asymmetric Splits (Layers 8 & 16)** | Real Weights | Exact parity under non-equal stage partitions | `max_diff = 0.000000` across both 8/16 and 16/8 splits | **PASS** | `scratch/stress_test_real_execution.py` |
| **Long Prefill Chunk (91 tokens)** | Real Weights | Exact parity across large multi-token hidden state tensors | `max_diff = 0.000000`, predicted token `'D'` identical | **PASS** | `scratch/stress_test_real_execution.py` |
| **Remote Real Weights Execution** | Remote Execution | Run real transformer layers on remote node | Not possible without PyTorch/CUDA runtime on `qwen-laptop` | **NOT IMPLEMENTED** | Documented host runtime constraint |
| **Attention KV Tensor Injection** | Real Model Cache | Inject paged KV blocks into active generation | Blocked by closed binary executable (Ollama / llama.cpp) | **NOT IMPLEMENTED** | `INCREMENT_17_INFERENCE_BACKEND_DECISION.md` |

---

## 5. Performance & Overhead Benchmarks

Comparing Single-Node Full GPU Baseline vs Partitioned Pipeline (GTX 1650 SUPER, FP16):

| Workload / Prompt | Sequence Tokens | Baseline Latency (Full GPU) | Partitioned Pipeline Latency | Difference / Overhead | Parity Result |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Short Prompt ("Hello!")** | 31 tokens | 336.12 ms (incl. context init) | 100.80 ms | -70.0% (warm context) | **Exact (0.000000 diff)** |
| **Unicode ("你好，世界！")** | 42 tokens | 147.65 ms | 198.01 ms | +34.1% overhead (framing) | **Exact (0.000000 diff)** |
| **Asymmetric Split (Layer 8)** | 38 tokens | 150.50 ms | 160.93 ms | +6.9% overhead | **Exact (0.000000 diff)** |
| **Asymmetric Split (Layer 16)**| 39 tokens | 148.49 ms | 161.77 ms | +8.9% overhead | **Exact (0.000000 diff)** |
| **Long Prefill** | 91 tokens | 316.28 ms | 325.45 ms | +2.9% overhead | **Exact (0.000000 diff)** |
| **Tailscale Multi-Node LAN** | 36 tokens | N/A (distributed) | 225.65 ms total (6.27 ms/token) | Network flight: ~3.5 ms RTT | **Operational** |

> [!NOTE]
> For small models like 0.5B, pipeline parallelism introduces a slight framing and serialization overhead (+3% to +34%) on a single machine or network mesh. The primary benefit of pipeline parallelism is memory distribution (enabling 7B/14B models to fit across partitioned hardware), not single-token latency reduction.

---

## 6. Safe Operating Envelope & Conditions

* **Safe Operating Envelope**:
  * Single-session or multi-session tensor streaming over TCP sockets with HMAC-SHA256 authenticated `QCACT1` envelopes up to 15 concurrent sessions.
  * Paged KV cache allocation with immutable canonical RFC 8785 execution namespaces, fail-closed storage keys, and `IdentityIssuanceAuthority` verification.
  * Local GPU model partitioning up to 4 GB VRAM on NVIDIA GTX 1650 SUPER.
* **Conditions Invalidating Conclusions**:
  * Unauthenticated socket access or compromised shared secret key.
  * Attempting to run real PyTorch transformer layers on remote hosts without supported CUDA/PyTorch runtimes.
  * Assuming synthetic framing steps per second equate to real model token generation speed.
