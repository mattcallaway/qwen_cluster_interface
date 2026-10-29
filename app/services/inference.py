import json
import logging
import uuid
import httpx
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from app.database import get_db_connection

logger = logging.getLogger("qwen_collective.inference")

import os
from app.config import settings
from app.services.network_security import validate_endpoint_url

_paged_kv_manager: Optional[Any] = None


def encode_text_to_tokens(text: str) -> List[int]:
    """
    Deterministic integer token encoding for prompt text.
    Encodes UTF-8 byte stream into integer token sequences.
    """
    return [int(b) for b in text.encode("utf-8")]


def get_paged_kv_manager() -> Any:
    global _paged_kv_manager
    if _paged_kv_manager is None:
        from app.services.paged_kv import PagedKVManager

        kv_dir = settings.data_dir / "kv_cache"
        _paged_kv_manager = PagedKVManager(
            storage_dir=kv_dir,
            max_ram_blocks=int(os.getenv("QC_PAGED_KV_MAX_RAM_BLOCKS", "128")),
            block_size=int(os.getenv("QC_PAGED_KV_BLOCK_SIZE", "16")),
            compress=True,
        )
    return _paged_kv_manager


def get_paged_kv_metrics() -> Dict[str, Any]:
    return get_paged_kv_manager().get_metrics()


async def test_inference_endpoint(backend_type: str, endpoint_url: str, api_key: Optional[str] = None) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Test reachability of an inference backend.
    Returns: (is_ready, model_name, error_details)
    """
    is_safe, reason = validate_endpoint_url(endpoint_url, allow_localhost=True)
    if not is_safe:
        return False, None, f"Endpoint rejected by security policy: {reason}"

    url = endpoint_url.rstrip("/")
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        
    try:
        async with httpx.AsyncClient(timeout=4.0, follow_redirects=False) as client:
            if backend_type == "ollama":
                # Ollama endpoint: /api/tags
                resp = await client.get(f"{url}/api/tags", headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    models = data.get("models", [])
                    model_names = [m.get("name") for m in models if m.get("name")]
                    first_model = model_names[0] if model_names else "Ollama (No models loaded)"
                    return True, first_model, None
                else:
                    return False, None, f"Ollama returned HTTP status {resp.status_code}"
            else:
                # llama.cpp server: /v1/models or /health
                try:
                    resp = await client.get(f"{url}/v1/models", headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        models = data.get("data", [])
                        model_name = models[0].get("id") if models else "llama.cpp model"
                        return True, model_name, None
                except Exception:
                    pass
                    
                resp = await client.get(f"{url}/health", headers=headers)
                if resp.status_code == 200:
                    return True, "llama-server (ready)", None
                return False, None, f"llama.cpp endpoint returned HTTP {resp.status_code}"
    except httpx.ConnectError:
        return False, None, f"Could not connect to {url}. Connection refused."
    except httpx.TimeoutException:
        return False, None, f"Connection to {url} timed out."
    except Exception as e:
        return False, None, f"Endpoint probe failed: {str(e)}"

def get_all_backends() -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM inference_backends ORDER BY priority ASC, created_at ASC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_active_backend() -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM inference_backends WHERE is_active = 1 AND status = 'ready' ORDER BY priority ASC, created_at ASC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_ordered_backends(target_tier: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Return active backends ordered by priority.
    If target_tier is specified, backends in that tier are placed first,
    followed by fallback backends in other tiers.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM inference_backends WHERE is_active = 1 ORDER BY priority ASC, created_at ASC")
    rows = cursor.fetchall()
    conn.close()
    all_backends = [dict(r) for r in rows]

    if not target_tier:
        return all_backends

    tier_matches = [b for b in all_backends if b.get("tier") == target_tier]
    other_tiers = [b for b in all_backends if b.get("tier") != target_tier]
    return tier_matches + other_tiers

def register_inference_backend(
    name: str,
    backend_type: str,
    endpoint_url: str,
    api_key: Optional[str] = None,
    context_window: int = 4096,
    tier: str = "tier_1_primary",
    priority: int = 10,
    timeout_seconds: float = 30.0,
    description: str = ""
) -> Dict[str, Any]:
    backend_id = f"inf-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO inference_backends (
        id, name, backend_type, endpoint_url, api_key,
        status, context_window, is_active, tier, priority,
        timeout_seconds, description, created_at
    ) VALUES (?, ?, ?, ?, ?, 'untested', ?, 1, ?, ?, ?, ?, ?)
    """, (
        backend_id, name.strip(), backend_type, endpoint_url.strip(), api_key or None,
        context_window, tier, priority, timeout_seconds, description.strip(), now
    ))
    conn.commit()
    conn.close()
    return {"id": backend_id, "name": name}

def update_backend_tier_priority(
    backend_id: str,
    tier: str,
    priority: int,
    is_active: int = 1,
    timeout_seconds: float = 30.0,
    description: Optional[str] = None
) -> None:
    conn = get_db_connection()
    conn.execute("""
    UPDATE inference_backends
    SET tier = ?,
        priority = ?,
        is_active = ?,
        timeout_seconds = ?,
        description = COALESCE(?, description)
    WHERE id = ?
    """, (tier, priority, is_active, timeout_seconds, description, backend_id))
    conn.commit()
    conn.close()

def update_backend_status(backend_id: str, status: str, model_name: Optional[str] = None, error_details: Optional[str] = None):
    conn = get_db_connection()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
    UPDATE inference_backends SET
        status = ?,
        model_name = COALESCE(?, model_name),
        error_details = ?,
        last_checked_at = ?
    WHERE id = ?
    """, (status, model_name, error_details, now, backend_id))
    conn.commit()
    conn.close()

def get_all_distributed_groups() -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM distributed_groups ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    results = []
    for r in rows:
        d = dict(r)
        try:
            d["rpc_endpoints"] = json.loads(d.get("rpc_endpoints_json", "[]"))
        except Exception:
            d["rpc_endpoints"] = []
        results.append(d)
    return results

def get_distributed_group_by_id(group_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM distributed_groups WHERE id = ?", (group_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    try:
        d["rpc_endpoints"] = json.loads(d.get("rpc_endpoints_json", "[]"))
    except Exception:
        d["rpc_endpoints"] = []
    return d

def create_distributed_group(
    name: str,
    coordinator_machine_id: str,
    model_file: str,
    rpc_endpoints: List[str],
    context_window: int = 4096,
    runtime_parameters: str = "",
    notes: str = ""
) -> str:
    group_id = f"dist-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO distributed_groups (
        id, name, coordinator_machine_id, backend_type,
        model_file, context_window, runtime_parameters,
        rpc_endpoints_json, status, notes, created_at, updated_at
    ) VALUES (?, ?, ?, 'llama_cpp_rpc', ?, ?, ?, ?, 'ready_for_testing', ?, ?, ?)
    """, (
        group_id, name.strip(), coordinator_machine_id, model_file.strip(),
        context_window, runtime_parameters.strip(), json.dumps(rpc_endpoints),
        notes.strip(), now, now
    ))
    conn.commit()
    conn.close()
    return group_id

def generate_distributed_launch_command(group: Dict[str, Any]) -> str:
    """Generate the exact llama-server command for manual execution on the coordinator."""
    model_path = group.get("model_file", "models/qwen2.5-7b-instruct-q4_k_m.gguf")
    ctx = group.get("context_window", 4096)
    rpc_list = group.get("rpc_endpoints", [])
    extra_params = group.get("runtime_parameters", "").strip()
    
    rpc_arg = f"--rpc {','.join(rpc_list)}" if rpc_list else ""
    
    cmd_parts = [
        "./build/bin/llama-server",
        f"-m {model_path}",
        f"-c {ctx}",
        "--host 127.0.0.1",
        "--port 8080"
    ]
    if rpc_arg:
        cmd_parts.append(rpc_arg)
    if extra_params:
        cmd_parts.append(extra_params)
        
    return " \\\n  ".join(cmd_parts)

def add_benchmark_result(
    group_or_backend_name: str,
    model_name: str,
    quantization: str,
    context_length: int,
    machines_involved: str,
    prompt_tokens_sec: float,
    gen_tokens_sec: float,
    memory_usage_mb: Optional[float] = None,
    notes: str = ""
):
    """Store genuine benchmark results."""
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO benchmarks (
        group_or_backend_name, model_name, quantization,
        context_length, machines_involved, prompt_tokens_sec,
        gen_tokens_sec, memory_usage_mb, notes, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        group_or_backend_name, model_name, quantization,
        context_length, machines_involved, prompt_tokens_sec,
        gen_tokens_sec, memory_usage_mb, notes, now
    ))
    conn.commit()
    conn.close()

def get_benchmarks() -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM benchmarks ORDER BY created_at DESC LIMIT 50")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

async def send_chat_completion(
    messages: List[Dict[str, str]],
    target_tier: Optional[str] = None,
    enable_fallback: bool = True
) -> Dict[str, Any]:
    """
    Execute a chat query against ordered inference backends with automatic multi-tier fallback.
    Cascades through candidate backends in priority order until one succeeds.
    """
    candidates = get_ordered_backends(target_tier=target_tier)
    if not candidates:
        return {
            "success": False,
            "error": "No active inference backends are configured in the collective. Configure a backend in Machines -> Inference."
        }

    if not enable_fallback and target_tier:
        candidates = [b for b in candidates if b.get("tier") == target_tier]
        if not candidates:
            return {
                "success": False,
                "error": f"No active backends available in target tier '{target_tier}'."
            }

    attempts_log = []

    # Sidecar Paged KV Prefix Search Hook
    paged_kv = get_paged_kv_manager()
    prompt_str = "\n".join(f"{m.get('role', '')}:{m.get('content', '')}" for m in messages)
    tokens = encode_text_to_tokens(prompt_str)
    seq_id = f"inf-seq-{uuid.uuid4().hex[:8]}"
    default_model_name = candidates[0].get("model_name") or "qwen2.5"

    paged_kv_report = paged_kv.ingest_prompt(
        sequence_id=seq_id,
        tokens=tokens,
        model_name=default_model_name,
    )

    try:
        for idx, backend in enumerate(candidates):
            backend_id = backend["id"]
            backend_name = backend["name"]
            tier = backend.get("tier", "tier_1_primary")
            url = backend["endpoint_url"].rstrip("/")
            timeout_sec = float(backend.get("timeout_seconds") or 45.0)
            headers = {}
            if backend.get("api_key"):
                headers["Authorization"] = f"Bearer {backend['api_key']}"

            logger.info(
                "Attempting inference on backend %s [%s] (priority %s, tier %s, timeout %.1fs)...",
                backend_name, url, backend.get("priority"), tier, timeout_sec
            )

            try:
                async with httpx.AsyncClient(timeout=timeout_sec, follow_redirects=False) as client:
                    if backend["backend_type"] == "ollama":
                        payload = {
                            "model": backend.get("model_name", "qwen2.5:3b"),
                            "messages": messages,
                            "options": {"num_ctx": min(backend.get("context_window") or 8192, 8192)},
                            "stream": False
                        }
                        resp = await client.post(f"{url}/api/chat", json=payload, headers=headers)
                        if resp.status_code == 200:
                            data = resp.json()
                            content = data.get("message", {}).get("content", "")
                            if not content:
                                raise ValueError("Ollama returned empty response content.")

                            update_backend_status(backend_id, "ready", error_details=None)

                            # Cache completion continuation in paged KV store
                            full_tokens = tokens + encode_text_to_tokens(content)
                            reply_seq = f"{seq_id}-reply"
                            paged_kv.ingest_prompt(
                                sequence_id=reply_seq,
                                tokens=full_tokens,
                                model_name=backend.get("model_name") or default_model_name,
                            )
                            # Release transient reply sequence mapping from table
                            paged_kv.release_sequence(reply_seq)

                            return {
                                "success": True,
                                "content": content,
                                "model": backend.get("model_name"),
                                "backend": backend_name,
                                "backend_id": backend_id,
                                "tier": tier,
                                "fallback_occurred": (idx > 0),
                                "attempts": attempts_log,
                                "paged_kv": paged_kv_report,
                            }
                        else:
                            err_msg = f"HTTP {resp.status_code} from {backend_name}: {resp.text[:200]}"
                            logger.warning("Backend %s failed with status %d: %s", backend_name, resp.status_code, err_msg)
                            update_backend_status(backend_id, "error", error_details=err_msg)
                            attempts_log.append({"backend": backend_name, "error": err_msg, "tier": tier})

                    else:
                        # llama.cpp server / OpenAI API
                        payload = {
                            "messages": messages,
                            "temperature": 0.7,
                            "max_tokens": 1024,
                            "stream": False
                        }
                        resp = await client.post(f"{url}/v1/chat/completions", json=payload, headers=headers)
                        if resp.status_code == 200:
                            data = resp.json()
                            choices = data.get("choices", [])
                            content = choices[0].get("message", {}).get("content", "") if choices else ""
                            if not content:
                                raise ValueError("llama-server returned empty choices.")

                            update_backend_status(backend_id, "ready", error_details=None)

                            # Cache completion continuation in paged KV store
                            full_tokens = tokens + encode_text_to_tokens(content)
                            reply_seq = f"{seq_id}-reply"
                            paged_kv.ingest_prompt(
                                sequence_id=reply_seq,
                                tokens=full_tokens,
                                model_name=backend.get("model_name") or default_model_name,
                            )
                            # Release transient reply sequence mapping from table
                            paged_kv.release_sequence(reply_seq)

                            return {
                                "success": True,
                                "content": content,
                                "model": backend.get("model_name"),
                                "backend": backend_name,
                                "backend_id": backend_id,
                                "tier": tier,
                                "fallback_occurred": (idx > 0),
                                "attempts": attempts_log,
                                "paged_kv": paged_kv_report,
                            }
                        else:
                            err_msg = f"HTTP {resp.status_code} from {backend_name}: {resp.text[:200]}"
                            logger.warning("Backend %s failed: %s", backend_name, err_msg)
                            update_backend_status(backend_id, "error", error_details=err_msg)
                            attempts_log.append({"backend": backend_name, "error": err_msg, "tier": tier})

            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                err_msg = f"Unreachable: {str(e)}"
                logger.warning("Backend %s unreachable, cascading to fallback: %s", backend_name, err_msg)
                update_backend_status(backend_id, "offline", error_details=err_msg)
                attempts_log.append({"backend": backend_name, "error": err_msg, "tier": tier})
            except httpx.TimeoutException as e:
                err_msg = f"Timed out after {timeout_sec}s: {str(e)}"
                logger.warning("Backend %s timed out, cascading to fallback: %s", backend_name, err_msg)
                update_backend_status(backend_id, "error", error_details=err_msg)
                attempts_log.append({"backend": backend_name, "error": err_msg, "tier": tier})
            except Exception as e:
                err_msg = f"Execution error: {str(e)}"
                logger.warning("Backend %s encountered exception, cascading to fallback: %s", backend_name, err_msg)
                update_backend_status(backend_id, "error", error_details=err_msg)
                attempts_log.append({"backend": backend_name, "error": err_msg, "tier": tier})

        # All candidates exhausted
        return {
            "success": False,
            "error": f"All {len(candidates)} candidate inference backend(s) failed. Failover summary: {json.dumps(attempts_log)}",
            "attempts": attempts_log
        }
    finally:
        # Deterministically clean up the transient prompt sequence table entry
        paged_kv.release_sequence(seq_id)
