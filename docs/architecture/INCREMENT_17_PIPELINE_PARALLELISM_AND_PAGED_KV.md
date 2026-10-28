# Architectural Specification: Increment 17
## Distributed Pipeline Parallelism & Searchable Paged KV Cache Architecture

**Document:** `docs/architecture/INCREMENT_17_PIPELINE_PARALLELISM_AND_PAGED_KV.md`  
**Date:** 2026-10-28  
**Status:** Approved Architecture Plan / Implementation in Progress  
**Base Commit:** `ab1b31498ec5c2e177431e51bc386e80b27b92bb`  
**Target Branch:** `feature/increment17-pipeline-parallelism-paged-kv`  

---

## 1. Problem Statement & Motivation

During multi-stage autonomous agent execution (multi-turn orchestrator reasoning, recursive code verification, citation extraction), the cluster encounters two fundamental scaling bottlenecks:

1. **Context Memory Bloat (RAM/VRAM Exhaustion):**  
   Attention Key-Value (KV) caches grow linearly with context length:
   $$\text{KV Size} = 2 \times L \times H \times D \times S \times \text{sizeof}(\text{dtype})$$
   For a 14B model at 32k context in FP16, the KV cache alone demands $\approx 9.2\text{ GB}$. On low-power compute nodes (such as `qwen-laptop` CPU or a 2GB VPS), monolithic memory allocation triggers out-of-memory (OOM) fatal aborts or severe OS memory swapping.
2. **Redundant Prompt Prefill Compute:**  
   Agent iterations continually re-send large common prefixes (system instructions, persona prompts, JSON schemas, tool contracts, workspace summaries). In monolithic setups, the model repeats the full quadratic prefill computation on every iteration, wasting CPU/GPU cycles and inducing latency.
3. **Monolithic Model Sizing Constraints:**  
   Larger models (e.g. Qwen2.5-7B, 14B, 32B) cannot run on individual low-power cluster nodes without extreme quantization (e.g. 2-bit) that breaks instruction-following and arithmetic precision.

---

## 2. Core Architectural Pillars

### Pillar A: Searchable Paged KV Cache Virtual Memory
Borrowing from virtual memory paging and PagedAttention principles:
1. **Paged Block Allocation:**  
   The continuous KV cache sequence is segmented into fixed-size **token pages** (default: $B = 16$ tokens per block).
2. **Deterministic Cryptographic Prefix Hashing:**  
   Each block page computes a deterministic content hash chaining from the preceding block:
   $$\text{PageHash}_0 = \text{SHA256}(\text{TokenBlock}_0)$$
   $$\text{PageHash}_i = \text{SHA256}(\text{PageHash}_{i-1} \parallel \text{TokenBlock}_i)$$
3. **Prefix Radix Tree & FTS Index:**  
   Pre-computed KV blocks are stored in a tiered storage hierarchy:
   - **Tier 1 (Hot):** Active generation in-memory buffers.
   - **Tier 2 (Warm):** Local NVMe SSD / memory-mapped (`mmap`) page files.
   - **Tier 3 (Cold / Shared):** Controller SQLite artifact vault (`kv_cache_pages`).
4. **Zero-Prefill Search & Attachment:**  
   When an inference prompt arrives, its token blocks are hashed and matched against the Radix index. Matching pages are attached directly to the virtual block table, **completely eliminating redundant prompt prefill forward passes**.
5. **LRU Eviction with Bounded RAM:**  
   Under memory pressure, idle pages are serialized and flushed to disk, releasing memory for active generation blocks.

### Pillar B: Distributed Pipeline Parallelism (PP) Across Mesh Nodes
1. **Layer Partitioning Scheme:**  
   Model transformer layers $0 \dots L-1$ are partitioned across cluster nodes based on verified hardware capabilities:
   - **Stage 0 (Ingress / Prefix Node, e.g. `qwen-laptop`):** Token embedding + Layers $0 \dots K-1$.
   - **Stage 1 (Intermediate Compute Node, e.g. `desktop-worker-01` or `qwen-vps`):** Layers $K \dots L-1$ + LayerNorm + LM Head projection.
2. **Activation Tensor Streaming over Tailscale:**  
   Inter-stage communication passes compact activation tensors ($1 \times \text{hidden\_dim}$ per token, ~3–7 KB) via authenticated HTTPS / UNIX sockets with HMAC integrity verification:
   - Frame protocol: `qc.pipeline_activation.v1`
   - Bounded timeout, backpressure control, and fail-closed disconnection handling.
3. **Disaggregated KV Cache per Stage:**  
   Each node maintains paged KV blocks strictly for its assigned subset of layers ($0 \dots K-1$ vs $K \dots L-1$), halving the per-node memory requirement.

---

## 3. Database Schema Progression (Migration 19)

We define **Migration 19** in `app/migrations.py` to register and track paged KV cache blocks and pipeline cluster topologies:

```sql
-- Migration 19: Paged KV Cache Storage and Pipeline Cluster Topology
CREATE TABLE IF NOT EXISTS kv_cache_pages (
    page_hash TEXT PRIMARY KEY,
    parent_page_hash TEXT,
    model_name TEXT NOT NULL,
    layer_start INTEGER NOT NULL,
    layer_end INTEGER NOT NULL,
    token_count INTEGER NOT NULL CHECK (token_count BETWEEN 1 AND 64),
    tokens_json TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    tensor_dtype TEXT NOT NULL DEFAULT 'float16',
    compression TEXT NOT NULL DEFAULT 'none',
    access_count INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_accessed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_kv_parent_hash ON kv_cache_pages(parent_page_hash);
CREATE INDEX IF NOT EXISTS idx_kv_model_layers ON kv_cache_pages(model_name, layer_start, layer_end);
CREATE INDEX IF NOT EXISTS idx_kv_last_accessed ON kv_cache_pages(last_accessed_at);

CREATE TABLE IF NOT EXISTS pipeline_cluster_stages (
    stage_id TEXT PRIMARY KEY,
    pipeline_name TEXT NOT NULL,
    stage_index INTEGER NOT NULL,
    node_id TEXT NOT NULL REFERENCES machines(id),
    model_name TEXT NOT NULL,
    layer_start INTEGER NOT NULL,
    layer_end INTEGER NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    endpoint_url TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(pipeline_name, stage_index)
);
```

---

## 4. Implementation Phasing & Work Breakdown

```
Phase 1: Paged KV Virtual Block Engine & Prefix Hasher
  ├── app/services/inference/paged_kv/hasher.py (Chunking, Prefix Hashing, Radix Lookup)
  ├── app/services/inference/paged_kv/page_table.py (Virtual Block Table, Logical-to-Physical Map)
  └── Unit test suite: tests/test_paged_kv_prefix_search.py

Phase 2: Tiered Storage, Serialization & LRU Eviction
  ├── app/services/inference/paged_kv/store.py (Disk/mmap binary store, zero-copy read, zstd/raw)
  ├── app/services/inference/paged_kv/manager.py (Memory boundary enforcement, LRU eviction)
  └── Unit test suite: tests/test_paged_kv_store_and_eviction.py

Phase 3: Pipeline Parallelism Activation Protocol & Stage Runner
  ├── app/services/inference/pipeline_parallel/activation_protocol.py (Frame format, HMAC, bounds)
  ├── app/services/inference/pipeline_parallel/topology.py (Stage definitions, layer allocations)
  ├── app/services/inference/pipeline_parallel/runner.py (Stage forward propagation, streaming)
  └── Unit test suite: tests/test_pipeline_activation_protocol.py

Phase 4: Migration 19 & In-Tree Engine Integration
  ├── app/migrations.py (Add Migration 19 with online backup test)
  ├── app/services/inference.py (Expose paged KV and pipeline routes)
  └── End-to-end integration test suite

Phase 5: Documentation & Map Updates
  ├── TECHMAP.md (Add Layer 2 Distributed Pipeline Parallelism & Paged KV Architecture)
  └── README.md (Update System Architecture diagram and capability badges)
```

---

## 5. Verification & Acceptance Criteria

1. **Deterministic Hashing:** 100% reproducible hash chains across arbitrary string and token sequences; distinct prefixes yield distinct hashes.
2. **Zero-Copy / Bounded Storage:** Paged KV store limits working RAM to operator-configured quota ($M$ MB) and safely evicts cold blocks to disk without data loss.
3. **Prefix Hit Rate & Speedup:** Pre-computed prompts achieve 100% prefix hit rate on identical headers, reducing prefill token processing from $O(N)$ to $O(1)$ disk page attachment.
4. **Activation Streaming Robustness:** Network disconnections, truncated tensors, or mismatched shapes during pipeline forwarding trigger immediate fail-closed error recovery without hang or memory leak.
5. **Database Integrity:** Migration 19 applies cleanly with SQLite online backup verification; 100% backward compatibility preserved.
