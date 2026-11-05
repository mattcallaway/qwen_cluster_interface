# Increment 17 Inference Backend Implementation Decision & Blocker Analysis

**Document:** `docs/architecture/INCREMENT_17_INFERENCE_BACKEND_DECISION.md`  
**Date:** 2026-10-28  
**Scope:** Technical evaluation of existing inference backends, transformer layer partitioning feasibility, KV cache tensor management, operational blockers, and proposed provisioning plan.  
**Status:** Architecture Decision Record (ADR) & Operational Gate  

---

## 1. Executive Verdict & Current Capability Boundary

Independent review confirmed that Increment 17's initial implementation operated as an architectural prototype with simulated computation:
- `PipelineRunner` and benchmark harnesses utilized synthetic activation vectors transformed via sinusoidal/tanh approximations rather than loaded transformer weight matrices.
- `PagedKVManager` stored token-derived placeholder strings (`TENSOR:<model>:<hash>:<tokens>`) rather than authentic per-layer Key/Value attention state tensors.
- The `inference.py` sidecar hook performed prefix indexing on UTF-8 byte tokens but still forwarded full prompt messages to the backend, resulting in zero prefill compute elimination at the model level.

**Core Decision:** We must not disguise placeholder tensors or simulated transforms as a real backend adapter. Monolithic chat-completion APIs (such as Ollama `/api/chat`) cannot satisfy the layer-partitioning or external KV-state requirements of this architecture. Genuine layer-partitioned execution requires an inference engine that exposes embedding layers, discrete transformer blocks, intermediate tensor handoffs, and tensor-level KV cache manipulation. Because such runtimes (e.g. PyTorch/Transformers) are not currently provisioned in the local environment, real model layer execution is documented as **blocked**, and this document establishes the concrete provisioning plan and acceptance criteria.

---

## 2. Inspection of Installed Cluster Backends

An exhaustive probe of cluster nodes and local Python environments was conducted on 2026-10-28:

| Node | OS & Python | Installed Inference Runtimes | Loaded Models | Layer Partitioning Exposed? | KV Cache Manipulation Exposed? |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Desktop Controller** (`100.122.228.19`) | Windows 10, Python 3.12.10 | Ollama v0.x (`http://127.0.0.1:11434`) | `qwen2.5:3b`, `qwen2.5:1.5b` | **NO** (monolithic black box) | **NO** (internal llama.cpp cache only) |
| **Desktop Python Env** | `C:\Users\Matt C\AppData\Local\Programs\Python\Python312` | Standard library, httpx, fastapi, pytest | None (`torch` and `transformers` are **NOT** installed) | **NO** | **NO** |
| **Qwen Laptop Worker** (`100.101.187.115`) | Ubuntu Linux, Python 3.10 | Ollama v0.x (`http://100.101.187.115:11434`) | `qwen2.5:1.5b`, `llama3.2:1b` | **NO** (monolithic black box) | **NO** (internal llama.cpp cache only) |
| **Qwen VPS Worker** (`100.71.194.68`) | Debian Linux, Python 3.10 | VPS Agent daemon | None (no local LLM runner) | **NO** | **NO** |
| **Pi Brain Edge** (`100.111.178.12`) | Debian Linux, Python 3.11 | Ollama v0.x (`http://100.111.178.12:11434`) | `qwen2.5:0.5b` | **NO** (monolithic black box) | **NO** (internal llama.cpp cache only) |

### Why Ollama / llama-server Chat APIs Cannot Support This Design
1. **Monolithic Forward Pass**: Ollama wraps `llama.cpp` behind a REST API (`/api/chat`, `/api/generate`). Invoking `/api/chat` passes prompt text into the engine, which executes embedding, all $N$ transformer layers, RMSNorm, and the LM head synchronously in C++. There is no mechanism over HTTP to pause after Layer 13, read the intermediate activation tensor $[\text{batch}, \text{seq}, d_{\text{model}}]$, transmit it over WireGuard, and resume execution on another host.
2. **Opaque KV State**: Ollama maintains internal slot-based KV caches within `llama.cpp` memory. It does not expose an API to extract, inject, or serialize individual 16-token physical KV page blocks across distinct execution nodes.
3. **Byte Tokens vs Model Vocabulary**: The UTF-8 byte encoding (`[int(b) for b in text.encode('utf-8')]`) used in the prototype does not align with the BPE tokenizer vocabulary (e.g., Qwen2.5's 151,643 token vocab). Authentic prefix cache matching requires exact model tokenizer IDs.

---

## 3. Real Inference Integration Specification

To advance from the repaired prototype to authentic model execution, the cluster must integrate a dedicated transformer runtime.

### 3.1. Selected Reference Model: `Qwen/Qwen2.5-0.5B-Instruct`
- **Architecture**: Decoder-only Dense Transformer
- **Parameter Count**: 490M parameters (pinned commit SHA-256)
- **Layers**: 24 Transformer blocks ($L = 24$)
- **Hidden Dimension ($d_{\text{model}}$)**: 896
- **Attention Heads**: 14 Query heads, 2 Key/Value heads (Grouped Query Attention, GQA)
- **Head Dimension**: 64
- **Vocabulary Size**: 151,936 tokens
- **Weights Size**: $\approx 1.0\text{ GB}$ in FP16 / BF16
- **Rationale**: Sufficiently small to run isolated CPU/GPU tests on Desktop and Laptop without memory exhaustion, while exercising authentic GQA KV cache tensors and deep transformer blocks.

### 3.2. Architecture Component Mapping

```
[ Input Tokens: (B, S) ]
       │
       ▼
 [ Embed Tokens ] ── (model.model.embed_tokens)
       │
       ▼
 ┌────────────────────────────────────────────────────────┐
 │ Stage 0 (Ingress Node, e.g. Laptop): Layers 0 .. 11    │
 │ - Computes Hidden State: Act_11 of shape (B, S, 896)   │
 │ - Appends KV Cache Pages (Layers 0..11) to PagedKVStore│
 └────────────────────────────┬───────────────────────────┘
                              │
                              ▼ [ QCACT1 Binary WireGuard Frame ]
                              │ - IEEE 754 Float16 (B, S, 896)
                              │ - HMAC-SHA256 Signed
                              ▼
 ┌────────────────────────────────────────────────────────┐
 │ Stage 1 (Egress Node, e.g. Desktop): Layers 12 .. 23   │
 │ - Ingests Act_11 as input to Layer 12                  │
 │ - Computes Layers 12 .. 23                             │
 │ - Final RMSNorm (model.model.norm)                     │
 │ - LM Head Projection (model.lm_head) -> Logits (B, S, V)│
 │ - Appends KV Cache Pages (Layers 12..23)               │
 └────────────────────────────┬───────────────────────────┘
                              │
                              ▼
                   [ Next Token Sampling ]
```

### 3.3. Authentic Paged KV Cache Tensor Format
For each 16-token physical block:
- **Tensor Shape per Layer**:
  - Key: $[B, N_{kv}, \text{block\_size}, D_{head}] = [1, 2, 16, 64]$
  - Value: $[B, N_{kv}, \text{block\_size}, D_{head}] = [1, 2, 16, 64]$
- **Element Count per Block per Layer**: $2 \times 1 \times 2 \times 16 \times 64 = 4,096$ elements
- **Byte Size per Layer (FP16)**: $4,096 \times 2\text{ bytes} = 8,192\text{ bytes} = 8\text{ KB}$
- **Total Block Payload (Stage 0, 12 layers)**: $12 \times 8\text{ KB} = 96\text{ KB}$
- **Total Block Payload (Full Model, 24 layers)**: $24 \times 8\text{ KB} = 192\text{ KB}$

### 3.4. Numerical Tolerances for Distributed Equivalence
When comparing single-node execution against 2-stage split pipeline execution on identical inputs:
- Intermediate activation tensor $L_{11}$ difference: $\max |A_{\text{single}} - A_{\text{split}}| \le 10^{-4}$ (FP32) or $\le 10^{-3}$ (FP16).
- Final output logits difference: $\max |\text{Logits}_{\text{single}} - \text{Logits}_{\text{split}}| \le 10^{-3}$ (FP32) or $\le 5 \times 10^{-3}$ (FP16).
- Top-1 greedy generated token sequence: **100% exact match** across all generated positions.

---

## 4. Blocker Statement & Proposed Provisioning Plan

### Current Blockers
1. **Missing Local PyTorch / Transformers Environment**:
   - The Windows Desktop Python environment has no `torch` or `transformers` installed.
   - Installing PyTorch on Windows requires a $\approx 2.5\text{ GB}$ pip wheel and runtime dependencies, which exceeds current isolated-test authorization.
2. **Production Safety Holds Intact**:
   - Cluster production services (`qwen-worker.service`, `ollama.service`, `qc-ollama-ts.service`) are actively running and gated under strict safety holds.
   - Modifying remote host packages or running unverified external code on worker hosts is held pending explicit approval.

### Proposed Multi-Stage Provisioning Plan (Subject to Approval)
1. **Gate 1: Authorization for Isolated Virtualenv**:
   - Request approval to provision an isolated Python virtual environment (`.venv-transformers`) on the Controller with `torch>=2.2.0`, `transformers>=4.40.0`, and `accelerate`.
   - Download and pin model weights for `Qwen/Qwen2.5-0.5B-Instruct` into `data/models/qwen2.5-0.5b` with explicit SHA-256 verification.
2. **Gate 2: Local 2-Stage Reference Harness**:
   - Implement `tests/reference_harness.py` executing `Qwen2.5-0.5B` locally in single-process mode vs split-stage mode (Stage 0: layers 0–11, Stage 1: layers 12–23).
   - Validate numerical tolerances and deterministic token generation equivalence.
   - Connect `PagedKVManager` to genuine layer KV tensors; measure actual reduced prefill compute time on cache hits.
3. **Gate 3: Remote Isolated Non-Production Stage Execution**:
   - Rehearse 2-stage execution across Desktop $\to$ Laptop over Tailscale using the audited `QCACT1` protocol with session nonces, replay rejection, and deadline cancellation.
4. **Gate 4: Independent Benchmark & Certification Review**:
   - Measure real TTFT (Time to First Token), decode tokens/second, network overhead, and cache hit latency reduction. Submit findings for independent review.
