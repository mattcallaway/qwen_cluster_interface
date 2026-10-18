import json
import logging
import uuid
import httpx
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from app.database import get_db_connection

logger = logging.getLogger("qwen_collective.inference")

from app.services.network_security import validate_endpoint_url

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
    cursor.execute("SELECT * FROM inference_backends ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_active_backend() -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM inference_backends WHERE is_active = 1 AND status = 'ready' LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def register_inference_backend(
    name: str,
    backend_type: str,
    endpoint_url: str,
    api_key: Optional[str] = None,
    context_window: int = 4096
) -> Dict[str, Any]:
    backend_id = f"inf-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO inference_backends (
        id, name, backend_type, endpoint_url, api_key,
        status, context_window, is_active, created_at
    ) VALUES (?, ?, ?, ?, ?, 'untested', ?, 1, ?)
    """, (backend_id, name.strip(), backend_type, endpoint_url.strip(), api_key or None, context_window, now))
    conn.commit()
    conn.close()
    return {"id": backend_id, "name": name}

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

async def send_chat_completion(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """Execute a chat query against the active ready backend."""
    backend = get_active_backend()
    if not backend:
        return {
            "success": False,
            "error": "No verified inference backend is currently active and ready. Configure a backend in Machines -> Inference."
        }
        
    url = backend["endpoint_url"].rstrip("/")
    headers = {}
    if backend.get("api_key"):
        headers["Authorization"] = f"Bearer {backend['api_key']}"
        
    try:
        async with httpx.AsyncClient(timeout=180.0, follow_redirects=False) as client:
            if backend["backend_type"] == "ollama":
                payload = {
                    "model": backend.get("model_name", "qwen2.5:3b"),
                    "messages": messages,
                    "options": {"num_ctx": 8192},
                    "stream": False
                }
                resp = await client.post(f"{url}/api/chat", json=payload, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    content = data.get("message", {}).get("content", "")
                    return {
                        "success": True,
                        "content": content,
                        "model": backend.get("model_name"),
                        "backend": backend["name"]
                    }
                else:
                    return {"success": False, "error": f"Ollama error (HTTP {resp.status_code}): {resp.text}"}
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
                    return {
                        "success": True,
                        "content": content,
                        "model": backend.get("model_name"),
                        "backend": backend["name"]
                    }
                else:
                    return {"success": False, "error": f"llama-server error (HTTP {resp.status_code}): {resp.text}"}
    except httpx.ConnectError:
        return {"success": False, "error": f"Inference backend at {url} is unreachable."}
    except httpx.TimeoutException:
        return {"success": False, "error": "Inference request timed out."}
    except Exception as e:
        return {"success": False, "error": f"Inference error: {str(e)}"}
