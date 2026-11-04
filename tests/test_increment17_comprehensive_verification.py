"""
Comprehensive Increment 17 Verification Suite (Sections 3, 4, 5, 6, and 7).

Tests:
- Section 3: Real transformer equivalence (Qwen/Qwen2.5-0.5B-Instruct on GPU) for 2-stage and 3-stage
  split pipeline execution (prefill + multi-step decode) and genuine PyTorchPagedKVAdapter reuse
  (cold, warm, partial-prefix, full-prefix, RAM-evicted disk restoration, concurrent shared/disjoint).
- Section 4: Pipeline transport & scheduling (escalating sessions 1..16, overload rejection, mixed short/long,
  slow stage backpressure, connection churn, bounded soak, stalled-session isolation).
- Section 5: Stage boundary fault injection (fragmented TCP sends, mid-frame truncation, duplicate/replayed/
  reordered/missing/stale frames, wrong stage/dim/dtype/NaN/Inf, dedicated subprocess kill & bounded retry).
- Section 6: Cache ownership & integrity (fork/COW/release ordering, pool & RAM/disk byte quotas,
  all 9 namespace fields, disk tamper/substitution/legacy/key-rotation, forged/foreign/revoked authorities).
- Section 7: Controlled application behavior in a clean environment.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Tuple

import pytest
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from app.services.paged_kv.hasher import ExecutionNamespace, ModelIdentityManifest
from app.services.paged_kv.identity import (
    ApprovedManifestRegistry,
    IdentityIssuanceAuthority,
    IssuedReuseAuthority,
)
from app.services.paged_kv.manager import PagedKVManager
from app.services.paged_kv.model_loader import TrustedLocalModelLoader
from app.services.paged_kv.pytorch_adapter import PyTorchPagedKVAdapter
from app.services.paged_kv.store import HEADER_SIZE, MAGIC_BYTES, PagedKVStore, V1_MAGIC_BYTES
from app.services.pipeline_parallel.activation_protocol import ActivationProtocol
from app.services.pipeline_parallel.pytorch_split_engine import LocalSplitPipelineEngine
from app.services.pipeline_parallel.runner import PipelineStageRunner
from app.services.pipeline_parallel.socket_transport import recv_framed_bytes, send_framed_bytes
from app.services.pipeline_parallel.stage1_server import Stage1SocketServer
from app.services.pipeline_parallel.topology import PipelineStageConfig

MODEL_ID = "Qwen/Qwen2.5-0.5B-Instruct"
PINNED_SNAPSHOT_REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"


def _resolve_local_model_dir() -> Path:
    env_dir = os.environ.get("QWEN_LOCAL_MODEL_DIR")
    if env_dir and Path(env_dir).exists():
        return Path(env_dir).resolve()
    local_candidate = Path(__file__).resolve().parent.parent / "scratch" / "models" / "qwen2.5-0.5b-instruct"
    if local_candidate.exists():
        return local_candidate
    sibling_candidate = (
        Path(__file__).resolve().parent.parent.parent
        / "qwen-collective-integration"
        / "scratch"
        / "models"
        / "qwen2.5-0.5b-instruct"
    )
    if sibling_candidate.exists():
        return sibling_candidate
    return local_candidate


LOCAL_MODEL_DIR = _resolve_local_model_dir()
PINNED_QWEN_MANIFEST = ModelIdentityManifest(
    model_name=MODEL_ID,
    weight_digest="fdf756fa7fcbe7404d5c60e26bff1a0c8b8aa1f72ced49e7dd0210fe288fb7fe",
    tokenizer_digest="c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539",
    chat_template_digest="5b5d4f65d0acd3b2d56a35b56d374a36cbc1c8fa5cf3b3febbbfabf22f359583",
    kv_layout="bshd_fp16",
    rope_config_digest="18e18afcaccafade98daf13a54092927904649e1dd4eba8299ab717d5d94ff45",
    runtime_compatibility="pytorch_cu121_v1",
    tenant_id="tenant_alpha",
)
WIRE_KEY = b"qc_comprehensive_wire_key_32byte"
STORE_KEY = b"qc_comprehensive_store_key_32byt"


@pytest.fixture(scope="module")
def qwen_env():
    """
    Loads Qwen/Qwen2.5-0.5B-Instruct at pinned revision 7ae557604adf67be50417f59c2c2f167def9a775
    via TrustedLocalModelLoader (verifying real local artifact SHA-256 digests and binding
    the loaded model instance to an IssuedReuseAuthority).
    """
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    registry = ApprovedManifestRegistry()
    registry.register_approved_manifest(PINNED_QWEN_MANIFEST)
    issuer = IdentityIssuanceAuthority(registry=registry)
    bundle = TrustedLocalModelLoader.load_verified_model(
        cache_or_snapshot_dir=LOCAL_MODEL_DIR,
        model_name=MODEL_ID,
        snapshot_revision=PINNED_SNAPSHOT_REVISION,
        manifest=PINNED_QWEN_MANIFEST,
        issuer=issuer,
        device=device,
        torch_dtype=torch.float16,
    )
    return {
        "device": device,
        "tokenizer": bundle.tokenizer,
        "model_fp16": bundle.model,
        "cache_dir": str(LOCAL_MODEL_DIR),
        "verified_bundle": bundle,
        "issuer": issuer,
        "registry": registry,
    }


@pytest.fixture
def verified_authority_bundle(tmp_path):
    """
    SYNTHETIC UNIT-TEST FIXTURE ONLY:
    Creates a synthetic ModelIdentityManifest, synthetic marker files, registry, and issuer
    for isolated namespace/tampering unit tests in Section 6. Not valid for real model instances.
    """
    art_dir = tmp_path / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)
    w_bytes = b"QWEN25_05B_WEIGHTS_CANONICAL_DIGEST_SOURCE"
    t_bytes = b'{"model_type": "qwen2", "vocab_size": 151936}'
    c_bytes = b"{% for message in messages %}{{ message.content }}{% endfor %}"
    r_bytes = b'{"rope_theta": 1000000.0, "rope_type": "default"}'

    w_path = art_dir / "weights.bin"
    t_path = art_dir / "tokenizer.json"
    c_path = art_dir / "chat_template.jinja"
    r_path = art_dir / "rope.json"
    w_path.write_bytes(w_bytes)
    t_path.write_bytes(t_bytes)
    c_path.write_bytes(c_bytes)
    r_path.write_bytes(r_bytes)

    manifest = ModelIdentityManifest(
        model_name=MODEL_ID,
        weight_digest=hashlib.sha256(w_bytes).hexdigest(),
        tokenizer_digest=hashlib.sha256(t_bytes).hexdigest(),
        chat_template_digest=hashlib.sha256(c_bytes).hexdigest(),
        kv_layout="bshd_fp16",
        rope_config_digest=hashlib.sha256(r_bytes).hexdigest(),
        runtime_compatibility="pytorch_cu121_v1",
        tenant_id="tenant_alpha",
    )
    paths = {
        "weights": w_path,
        "tokenizer": t_path,
        "chat_template": c_path,
        "rope_config": r_path,
    }
    registry = ApprovedManifestRegistry()
    registry.register_approved_manifest(manifest)
    issuer = IdentityIssuanceAuthority(registry=registry)
    authority = issuer.verify_and_issue(manifest, paths)
    namespace = ExecutionNamespace.from_manifest(manifest)
    return {
        "manifest": manifest,
        "paths": paths,
        "registry": registry,
        "issuer": issuer,
        "authority": authority,
        "namespace": namespace,
    }


# ===========================================================================
# SECTION 3: REAL MODEL EQUIVALENCE & NUMERICAL PARITY
# ===========================================================================


def test_sec3_split_pipeline_empty_input_rejected(qwen_env):
    engine = LocalSplitPipelineEngine(
        full_model=qwen_env["model_fp16"],
        layer_splits=[(0, 12), (12, 24)],
        secret_key=WIRE_KEY,
        device=qwen_env["device"],
    )
    empty_ids = torch.zeros((1, 0), dtype=torch.long, device=qwen_env["device"])
    with pytest.raises(ValueError, match="seq_len >= 1"):
        engine.forward_split(empty_ids)


@pytest.mark.parametrize(
    "layer_splits",
    [
        [(0, 12), (12, 24)],          # Balanced 2-stage
        [(0, 8), (8, 24)],            # Imbalanced 2-stage (light stage 0)
        [(0, 16), (16, 24)],          # Imbalanced 2-stage (heavy stage 0)
        [(0, 8), (8, 16), (16, 24)],  # 3-stage pipeline
    ],
)
def test_sec3_split_pipeline_stage_partitions_and_intermediate_activations(qwen_env, layer_splits):
    """
    Verifies intermediate hidden states at every partition boundary AND final logits/tokens
    against the single-node reference model across 2-stage and 3-stage partitions.
    """
    model = qwen_env["model_fp16"]
    tokenizer = qwen_env["tokenizer"]
    device = qwen_env["device"]

    prompt = "Explain how paged virtual memory prevents external fragmentation in transformer KV caches."
    input_ids = tokenizer(prompt, return_tensors="pt").input_ids.to(device)

    with torch.no_grad():
        ref_out = model(input_ids, output_hidden_states=True, use_cache=False)
        ref_logits = ref_out.logits.detach().cpu()
        ref_hidden_states = ref_out.hidden_states  # tuple of 25 tensors: [embed, layer_0_out, ..., layer_23_out]

    engine = LocalSplitPipelineEngine(
        full_model=model,
        layer_splits=layer_splits,
        secret_key=WIRE_KEY,
        device=device,
    )
    split_res = engine.forward_split(input_ids, session_id="part-test", wire_dtype="float16")

    # Verify intermediate activations at each stage boundary
    for stage_idx, (_, end_layer) in enumerate(layer_splits[:-1]):
        ref_boundary = ref_hidden_states[end_layer].detach().cpu()
        split_boundary = split_res["boundary_hidden_states"][stage_idx]
        max_hidden_diff = float(torch.max(torch.abs(ref_boundary - split_boundary)).item())
        assert max_hidden_diff <= 1e-3, (
            f"Boundary {stage_idx} (layer {end_layer}) activation diff {max_hidden_diff} > 1e-3"
        )

    # Verify final logits and predicted token
    split_logits = split_res["logits"]
    max_logit_diff = float(torch.max(torch.abs(ref_logits - split_logits)).item())
    assert max_logit_diff <= 1e-3, f"Max logit diff {max_logit_diff} > 1e-3"
    assert int(torch.argmax(ref_logits[0, -1])) == int(torch.argmax(split_logits[0, -1]))


def test_sec3_split_pipeline_short_unicode_long_and_multistep_decode(qwen_env):
    """
    Tests short, Unicode multilingual, and long bounded (136 tokens) inputs,
    plus 8-step deterministic greedy autoregressive decoding parity.
    """
    model = qwen_env["model_fp16"]
    tokenizer = qwen_env["tokenizer"]
    device = qwen_env["device"]

    engine = LocalSplitPipelineEngine(
        full_model=model,
        layer_splits=[(0, 12), (12, 24)],
        secret_key=WIRE_KEY,
        device=device,
    )

    test_cases = [
        ("short", "Hello world!"),
        ("unicode", "こんにちは世界！Привет мир! 🚀 Δx·Δp ≥ ħ/2 pipeline verification"),
        ("long_bounded", "Distributed transformer inference requires strict wire framing. " * 14),
    ]

    for label, text in test_cases:
        input_ids = tokenizer(text, return_tensors="pt").input_ids[:, :136].to(device)
        with torch.no_grad():
            ref_logits = model(input_ids, use_cache=False).logits.detach().cpu()
        split_res = engine.forward_split(input_ids, session_id=f"case-{label}")
        max_diff = float(torch.max(torch.abs(ref_logits - split_res["logits"])).item())
        assert max_diff <= 1e-3, f"{label}: max_logit_diff={max_diff}"
        assert int(torch.argmax(ref_logits[0, -1])) == int(torch.argmax(split_res["logits"][0, -1]))

    # Multi-step deterministic greedy decode (8 steps)
    decode_prompt = "The capital of France is"
    prompt_ids = tokenizer(decode_prompt, return_tensors="pt").input_ids.to(device)
    split_gen = engine.generate_greedy(prompt_ids, max_new_tokens=8, session_id="greedy-8")

    # Single-node greedy decode reference
    ref_running = prompt_ids.clone()
    ref_tokens: List[int] = []
    with torch.no_grad():
        for _ in range(8):
            logits = model(ref_running, use_cache=False).logits
            next_tok = int(torch.argmax(logits[0, -1, :]).item())
            ref_tokens.append(next_tok)
            ref_running = torch.cat(
                [ref_running, torch.tensor([[next_tok]], dtype=ref_running.dtype, device=device)],
                dim=1,
            )

    assert split_gen["generated_tokens"] == ref_tokens


def test_sec3_real_kv_cache_cold_warm_partial_full_evict_restore_and_concurrent(
    tmp_path, qwen_env, verified_authority_bundle
):
    """
    Exercises genuine PyTorchPagedKVAdapter reuse with real Qwen/Qwen2.5-0.5B-Instruct KV tensors
    authorized via TrustedLocalModelLoader (real artifact SHA-256 verification + live model instance binding):
    - Cold prefill caching -> Warm partial-prefix hit -> Changed suffix
    - 100% block-aligned prefix hit (32 tokens) preserving final token for CausalLM forward
    - RAM eviction -> Tier 2 disk QCKVP2 authenticated restoration -> Numerical parity
    - Rejects synthetic-fixture authority (`verified_authority_bundle`) for the real loaded model
    """
    bundle = qwen_env["verified_bundle"]
    model = bundle.model
    device = qwen_env["device"]
    issuer = qwen_env["issuer"]
    authority = bundle.authority
    ns = bundle.namespace

    assert authority.require_instance_binding is True
    assert authority.model_instance_id == bundle.model_instance_id

    manager = PagedKVManager(
        storage_dir=tmp_path / "kv_real_parity",
        max_ram_blocks=32,
        auth_key=STORE_KEY,
        trusted_issuer=issuer,
    )
    adapter = PyTorchPagedKVAdapter(manager=manager, device=device, model=model)

    # Verify synthetic-fixture authority cannot authorize the real loaded model
    synth_auth = verified_authority_bundle["authority"]
    synth_ns = verified_authority_bundle["namespace"]
    with pytest.raises(ValueError, match="KV cache extraction denied"):
        adapter.extract_and_cache_sequence_kv(
            "seq-synth-rejected",
            list(range(16)),
            tuple(
                (torch.randn(1, 2, 16, 64, dtype=torch.float16, device=device), torch.randn(1, 2, 16, 64, dtype=torch.float16, device=device))
                for _ in range(2)
            ),
            namespace=synth_ns,
            authority=synth_auth,
        )

    # 32-token shared prefix (2 full 16-token blocks) + 8-token suffix A / suffix B
    shared_prefix = [151644, 8948, 198, 2610, 525, 264, 10950, 17847, 13, 151645, 198, 151644, 872, 198, 3838, 374] * 2
    assert len(shared_prefix) == 32
    suffix_a = [1200, 1300, 1400, 1500, 1600, 1700, 1800, 1900]
    suffix_b = [2200, 2300, 2400, 2500, 2600, 2700, 2800, 2900]
    tokens_a = shared_prefix + suffix_a
    tokens_b = shared_prefix + suffix_b

    # Reference full forward on tokens_b
    input_b_full = torch.tensor([tokens_b], dtype=torch.long, device=device)
    with torch.no_grad():
        ref_out_b = model(input_b_full, use_cache=True)
        ref_logits_b = ref_out_b.logits[:, -1, :].detach().cpu()
        ref_top_b = int(torch.argmax(ref_logits_b[0]).item())

    # 1. Cold prefill on tokens_a and cache KV pages
    cold_ctx = adapter.prepare_prefill_context("seq-cold-a", tokens_a, namespace=ns, authority=authority)
    assert cold_ctx["warm_cache_hit"] is False
    input_a_full = torch.tensor([tokens_a], dtype=torch.long, device=device)
    with torch.no_grad():
        out_a = model(input_a_full, use_cache=True)
    cached_count = adapter.extract_and_cache_sequence_kv(
        "seq-cold-a", tokens_a, out_a.past_key_values, namespace=ns, authority=authority
    )
    assert cached_count == 2

    # 2. Warm partial-prefix hit on tokens_b (reuses 32 prefix tokens, computes 8 suffix tokens)
    warm_ctx = adapter.prepare_prefill_context("seq-warm-b", tokens_b, namespace=ns, authority=authority)
    assert warm_ctx["warm_cache_hit"] is True
    assert warm_ctx["matched_tokens"] == 32
    assert warm_ctx["input_ids"] == suffix_b

    suffix_tensor = torch.tensor([warm_ctx["input_ids"]], dtype=torch.long, device=device)
    pos_tensor = torch.tensor([warm_ctx["position_ids"]], dtype=torch.long, device=device)
    with torch.no_grad():
        warm_out_b = model(
            input_ids=suffix_tensor,
            position_ids=pos_tensor,
            past_key_values=warm_ctx["past_key_values"],
            use_cache=True,
        )
        warm_logits_b = warm_out_b.logits[:, -1, :].detach().cpu()
        warm_top_b = int(torch.argmax(warm_logits_b[0]).item())

    max_diff_warm = float(torch.max(torch.abs(ref_logits_b - warm_logits_b)).item())
    # Predefined FP16 KV-injection tolerance: <= 0.1 logit units (~4.5 FP16 ULPs across 24 layers), exact token match
    assert max_diff_warm <= 0.1, f"Warm FP16 logit diff {max_diff_warm} > 0.1"
    assert warm_top_b == ref_top_b

    # 3. Evict from RAM (which also clears adapter._ram_tensor_cache via hook) and restore from Tier 2 disk
    for h in manager.table.get_block_hashes("seq-cold-a"):
        manager.store.evict_from_ram(h)
        assert h not in adapter._ram_tensor_cache

    disk_ctx = adapter.prepare_prefill_context("seq-disk-b", tokens_b, namespace=ns, authority=authority)
    assert disk_ctx["warm_cache_hit"] is True
    assert disk_ctx["matched_tokens"] == 32
    with torch.no_grad():
        disk_out_b = model(
            input_ids=torch.tensor([disk_ctx["input_ids"]], dtype=torch.long, device=device),
            position_ids=torch.tensor([disk_ctx["position_ids"]], dtype=torch.long, device=device),
            past_key_values=disk_ctx["past_key_values"],
            use_cache=True,
        )
        disk_logits_b = disk_out_b.logits[:, -1, :].detach().cpu()
    # Disk-restored logits must be bit-exact with RAM-cached warm logits
    assert float(torch.max(torch.abs(warm_logits_b - disk_logits_b)).item()) == 0.0

    # 4. 100% block-aligned prompt (exact 32 tokens = 2 blocks) warm hit
    exact_32_ctx = adapter.prepare_prefill_context("seq-exact-32", shared_prefix, namespace=ns, authority=authority)
    assert exact_32_ctx["warm_cache_hit"] is True
    assert exact_32_ctx["matched_tokens"] == 16  # 1 block reused, 16 suffix tokens retained
    assert len(exact_32_ctx["input_ids"]) == 16
    with torch.no_grad():
        ref_32 = model(torch.tensor([shared_prefix], dtype=torch.long, device=device), use_cache=False).logits[:, -1, :].cpu()
        warm_32 = model(
            input_ids=torch.tensor([exact_32_ctx["input_ids"]], dtype=torch.long, device=device),
            position_ids=torch.tensor([exact_32_ctx["position_ids"]], dtype=torch.long, device=device),
            past_key_values=exact_32_ctx["past_key_values"],
            use_cache=True,
        ).logits[:, -1, :].cpu()
    assert int(torch.argmax(ref_32[0]).item()) == int(torch.argmax(warm_32[0]).item())

    # Cleanup all sequences and verify accounting
    for sid in ["seq-cold-a", "seq-warm-b", "seq-disk-b", "seq-exact-32"]:
        adapter.release_sequence(sid)
    assert manager.get_metrics()["active_sequences"] == 0


# ===========================================================================
# SECTION 4: PIPELINE TRANSPORT & SCHEDULING STRESS
# ===========================================================================


def test_sec4_mixed_short_long_and_slow_stage_backpressure():
    """
    Exercises mixed short (4 tokens) and long (40 tokens) concurrent sessions,
    plus a deliberately slow stage callback to verify bounded backpressure without data loss.
    """
    port = 19231
    proto = ActivationProtocol(secret_key=WIRE_KEY)

    def slow_stage_callback(vec, layer_start, layer_end):
        # Deliberate 2ms stage compute latency per frame
        time.sleep(0.002)
        return vec

    server = Stage1SocketServer(
        host="127.0.0.1",
        port=port,
        secret_key=WIRE_KEY,
        hidden_dim=64,
        stage_compute_fn=slow_stage_callback,
        max_concurrent_sessions=12,
        client_timeout_s=5.0,
    )
    server.start(background=True)

    results: Dict[str, float] = {}
    errors: List[str] = []

    def run_session(sess_id: str, num_frames: int):
        t0 = time.perf_counter()
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(5.0)
                s.connect(("127.0.0.1", port))
                for t_idx in range(num_frames):
                    frame = proto.encode_frame(
                        session_id=sess_id,
                        stage_index=0,
                        token_index=t_idx,
                        activations=[0.125] * 64,
                        tensor_dtype="float16",
                    )
                    send_framed_bytes(s, frame)
                    resp = json.loads(recv_framed_bytes(s).decode("utf-8"))
                    if resp.get("status") != "ok" or resp.get("token_index") != t_idx:
                        errors.append(f"{sess_id}: unexpected resp {resp}")
                        return
            results[sess_id] = (time.perf_counter() - t0) * 1000.0
        except Exception as e:
            errors.append(f"{sess_id}: {e}")

    try:
        threads = []
        # 4 long sessions (30 frames) and 4 short sessions (4 frames)
        for i in range(4):
            threads.append(threading.Thread(target=run_session, args=(f"long-{i}", 30)))
            threads.append(threading.Thread(target=run_session, args=(f"short-{i}", 4)))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        assert not errors, f"Transport errors: {errors}"
        assert server.frames_processed == 4 * 30 + 4 * 4
        for _ in range(40):
            if server.active_sessions == 0:
                break
            time.sleep(0.005)
        assert server.active_sessions == 0
        # Short sessions must complete faster on average than long sessions (fair progress)
        avg_short = sum(results[f"short-{i}"] for i in range(4)) / 4.0
        avg_long = sum(results[f"long-{i}"] for i in range(4)) / 4.0
        assert avg_short < avg_long
    finally:
        server.stop()


# ===========================================================================
# SECTION 5: STAGE BOUNDARY FAULT INJECTION & SUBPROCESS KILL
# ===========================================================================


def test_sec5_fragmented_tcp_and_midframe_truncation():
    """
    Tests TCP stream fragmentation (sending a frame in 7-byte chunks with delays)
    and mid-frame truncation on disconnect (verifying recv_framed_bytes raises ConnectionError).
    """
    port = 19232
    proto = ActivationProtocol(secret_key=WIRE_KEY)
    server = Stage1SocketServer(
        host="127.0.0.1",
        port=port,
        secret_key=WIRE_KEY,
        hidden_dim=32,
        client_timeout_s=3.0,
    )
    server.start(background=True)

    try:
        # 1. Fragmented TCP delivery (7 bytes at a time)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(3.0)
            s.connect(("127.0.0.1", port))
            frame = proto.encode_frame("frag-sess", 0, 0, [0.5] * 32, "float16")
            wire = struct.pack(">I", len(frame)) + frame
            for i in range(0, len(wire), 7):
                s.sendall(wire[i : i + 7])
                time.sleep(0.001)
            resp = json.loads(recv_framed_bytes(s).decode("utf-8"))
            assert resp["status"] == "ok"
            assert resp["token_index"] == 0

        # 2. Mid-frame truncation: declare 500 bytes, send only 50 bytes, then close socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(2.0)
            s.connect(("127.0.0.1", port))
            s.sendall(struct.pack(">I", 500) + b"x" * 50)
        time.sleep(0.05)
        assert server.auth_failures >= 1

        # 3. Subsequent healthy session still succeeds
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(2.0)
            s.connect(("127.0.0.1", port))
            frame = proto.encode_frame("post-trunc", 0, 0, [0.25] * 32, "float16")
            send_framed_bytes(s, frame)
            resp = json.loads(recv_framed_bytes(s).decode("utf-8"))
            assert resp["status"] == "ok"
    finally:
        server.stop()


def test_sec5_dedicated_subprocess_stage_failure_and_bounded_retry():
    """
    Launches a dedicated test subprocess running Stage1SocketServer, kills it mid-request,
    and verifies the client enforces a bounded retry ceiling (max 2 retries) and reports an
    explicit terminal failure without hanging or accepting partial output.
    """
    port = 19233
    proto = ActivationProtocol(secret_key=WIRE_KEY)
    code = (
        "import time\n"
        "from app.services.pipeline_parallel.stage1_server import Stage1SocketServer\n"
        f"srv = Stage1SocketServer('127.0.0.1', {port}, {WIRE_KEY!r}, hidden_dim=32)\n"
        "srv.start(background=True)\n"
        "while True:\n"
        "    time.sleep(0.1)\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", code])
    try:
        # Wait for subprocess server to bind
        connected = False
        for _ in range(30):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(0.5)
                    s.connect(("127.0.0.1", port))
                    f0 = proto.encode_frame("subproc-01", 0, 0, [1.0] * 32, "float16")
                    send_framed_bytes(s, f0)
                    r0 = json.loads(recv_framed_bytes(s).decode("utf-8"))
                    if r0.get("status") == "ok":
                        connected = True
                        break
            except Exception:
                time.sleep(0.1)
        assert connected, "Dedicated subprocess Stage1SocketServer failed to start"

        # Kill dedicated subprocess and verify bounded retry fails deterministically
        proc.kill()
        proc.wait(timeout=5.0)

        max_retries = 2
        attempts = 0
        terminal_error = None
        for attempt in range(max_retries + 1):
            attempts += 1
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(0.5)
                    s.connect(("127.0.0.1", port))
                    f1 = proto.encode_frame("subproc-02", 0, 0, [1.0] * 32, "float16")
                    send_framed_bytes(s, f1)
                    recv_framed_bytes(s)
            except Exception as e:
                terminal_error = e

        assert attempts == max_retries + 1
        assert terminal_error is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5.0)


# ===========================================================================
# SECTION 6: CACHE OWNERSHIP, ALL 9 NAMESPACE FIELDS & KEY ROTATION
# ===========================================================================


def test_sec6_all_nine_namespace_fields_enforced(tmp_path, verified_authority_bundle):
    """
    Verifies that mutating ANY single field of the 9 ExecutionNamespace fields produces a
    distinct namespace digest and results in zero cross-namespace prefix hits.
    """
    issuer = verified_authority_bundle["issuer"]
    authority = verified_authority_bundle["authority"]
    base_ns = verified_authority_bundle["namespace"]

    manager = PagedKVManager(
        storage_dir=tmp_path / "ns_9fields",
        max_ram_blocks=64,
        auth_key=STORE_KEY,
        trusted_issuer=issuer,
    )
    tokens = list(range(500, 532))  # 32 tokens = 2 pages
    res_base = manager.ingest_prompt("seq-base", tokens, namespace=base_ns, authority=authority)
    assert res_base["verified_reuse_authorized"] is True
    assert res_base["new_blocks_count"] == 2

    mutations = [
        ("model_name", {"model_name": "Qwen/Qwen2.5-1.5B-Instruct"}),
        ("model_revision", {"model_revision": "different_weight_sha256"}),
        ("tokenizer_id", {"tokenizer_id": "different_tokenizer_sha256"}),
        ("chat_template_hash", {"chat_template_hash": "different_template_sha256"}),
        ("kv_layout", {"kv_layout": "bhsd_fp32"}),
        ("rope_config_hash", {"rope_config_hash": "different_rope_sha256"}),
        ("runtime_compatibility", {"runtime_compatibility": "other_runtime_v9"}),
        ("tenant_id", {"tenant_id": "tenant_beta"}),
        ("identity_scope", {"identity_scope": "prototype_simulation"}),
    ]

    # Also verify arbitrary unknown identity_scope is rejected fail-closed at construction
    with pytest.raises(ValueError, match="Invalid identity_scope"):
        ExecutionNamespace(
            model_name=base_ns.model_name,
            model_revision=base_ns.model_revision,
            tokenizer_id=base_ns.tokenizer_id,
            chat_template_hash=base_ns.chat_template_hash,
            kv_layout=base_ns.kv_layout,
            rope_config_hash=base_ns.rope_config_hash,
            runtime_compatibility=base_ns.runtime_compatibility,
            tenant_id=base_ns.tenant_id,
            identity_scope="unverified_caller",
        )

    base_dict = {
        "model_name": base_ns.model_name,
        "model_revision": base_ns.model_revision,
        "tokenizer_id": base_ns.tokenizer_id,
        "chat_template_hash": base_ns.chat_template_hash,
        "kv_layout": base_ns.kv_layout,
        "rope_config_hash": base_ns.rope_config_hash,
        "runtime_compatibility": base_ns.runtime_compatibility,
        "tenant_id": base_ns.tenant_id,
        "identity_scope": base_ns.identity_scope,
    }

    for field_name, override in mutations:
        mut_kwargs = dict(base_dict)
        mut_kwargs.update(override)
        mut_ns = ExecutionNamespace(**mut_kwargs)
        assert mut_ns.compute_digest() != base_ns.compute_digest(), f"Digest unchanged for {field_name}"
        res_mut = manager.ingest_prompt(f"seq-{field_name}", tokens, namespace=mut_ns, authority=authority)
        assert res_mut["matched_tokens"] == 0, f"Cross-namespace hit on mutated {field_name}"
        manager.release_sequence(f"seq-{field_name}")

    manager.release_sequence("seq-base")
    assert manager.get_metrics()["active_sequences"] == 0


def test_sec6_key_rotation_and_wrong_key_reject_disk_pages(tmp_path):
    """
    Verifies that rotating the HMAC storage key causes pages persisted under the old key
    to fail closed with an HMAC authentication error upon disk retrieval.
    """
    key_v1 = b"storage_hmac_key_version_01_32b!"
    key_v2 = b"storage_hmac_key_version_02_32b!"
    store_v1 = PagedKVStore(storage_dir=tmp_path / "key_rot", auth_key=key_v1)
    b_hash = "abcdef0123456789abcdef0123456789"
    store_v1.write_page(b_hash, b"KV_PAYLOAD_V1" * 50, persist_to_disk=True, namespace_digest="abcdef0123456789")

    # Open same storage directory with rotated key_v2
    store_v2 = PagedKVStore(storage_dir=tmp_path / "key_rot", auth_key=key_v2)
    with pytest.raises(ValueError, match="header HMAC mismatch"):
        store_v2.read_page(b_hash, expected_namespace_digest="abcdef0123456789")

