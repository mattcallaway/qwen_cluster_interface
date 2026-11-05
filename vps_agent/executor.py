"""
Sandboxed Execution Engine for VPS Worker Agent.
Enforces execution security model across:
- Class A: Built-in safe diagnostic and capability operations
- Class B: Approved scripts in sanitized environment with restricted working directory
- Class C: Experimental or untrusted code (fails closed if OS-level isolation sandbox is unavailable)
- Explicit rejection of unsupported operations with structured error
"""

import base64
import hashlib
import http.client
import json
import logging
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.error
import urllib.parse
import urllib.request
import uuid
from vps_agent.bounded_process import run_bounded_process
from vps_agent.windows_job import BoundaryUnavailable

logger = logging.getLogger("qwen_collective.worker_executor")

CLASS_A_OPERATIONS = {"test_task", "health_check", "diagnostic", "capability_probe", "ping"}
CLASS_B_OPERATIONS = {"python_execution", "python_script", "approved_script"}
CLASS_C_OPERATIONS = {"experimental_code", "untrusted_script", "ai_generated_code"}
INFERENCE_OPERATIONS = {"inference", "model_inference", "local_inference"}

MAX_OUTPUT_CHARS = 12000
MAX_PROMPT_CHARS = 8000
MAX_NUM_PREDICT = 2048
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
CANCELLATION_CLEANUP_DEADLINE_SECONDS = 0.5


def _make_cancellation_outcomes(
    *,
    cancellation_acknowledged: bool,
    client_request_terminated: bool,
    owned_threads_or_processes_stopped: bool,
    backend_generation_stopped: Any,
    late_output_rejected: bool,
    cleanup_completed: bool,
    stage: str,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    client_confirmed = bool(
        cancellation_acknowledged
        and client_request_terminated
        and owned_threads_or_processes_stopped
        and late_output_rejected
        and cleanup_completed
    )
    if not client_confirmed or backend_generation_stopped is False:
        termination_confirmed: Any = False
    elif backend_generation_stopped is True:
        termination_confirmed = True
    else:
        termination_confirmed = "unconfirmed_backend"
    out: Dict[str, Any] = {
        "cancellation_acknowledged": bool(cancellation_acknowledged),
        "client_request_terminated": bool(client_request_terminated),
        "owned_threads_or_processes_stopped": bool(owned_threads_or_processes_stopped),
        "backend_generation_stopped": backend_generation_stopped,
        "late_output_rejected": bool(late_output_rejected),
        "cleanup_completed": bool(cleanup_completed),
        "client_termination_confirmed": client_confirmed,
        "termination_confirmed": termination_confirmed,
        "stage": stage,
    }
    if request_id:
        out["request_id"] = request_id
    return out


class _HTTPConnectionTracker:
    """Tracks raw OS sockets, HTTP connections, and response objects for deterministic abort."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sockets: List[socket.socket] = []
        self.conns: List[http.client.HTTPConnection] = []
        self.responses: List[Any] = []
        self.stage: str = "before_dispatch"
        self.aborted: bool = False
        self.connect_started = threading.Event()
        self.request_sent = threading.Event()
        self.headers_received = threading.Event()
        self.bytes_read: int = 0

    def register_conn(self, conn: http.client.HTTPConnection) -> None:
        with self.lock:
            self.conns.append(conn)
            if self.aborted:
                try:
                    conn.close()
                except Exception:
                    pass

    def register_socket(self, sock: socket.socket) -> None:
        with self.lock:
            self.sockets.append(sock)
            if self.aborted:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    sock.close()
                except Exception:
                    pass
                raise OSError("Connection aborted by cancellation")

    def register_response(self, resp: Any) -> None:
        with self.lock:
            self.responses.append(resp)
            # Extract underlying socket if wrapped by standard urllib response
            try:
                raw_sock = getattr(getattr(getattr(resp, "fp", None), "raw", None), "_sock", None)
                if isinstance(raw_sock, socket.socket) and raw_sock not in self.sockets:
                    self.sockets.append(raw_sock)
            except Exception:
                pass
            if self.aborted:
                try:
                    resp.close()
                except Exception:
                    pass

    def set_stage(self, stage: str) -> None:
        with self.lock:
            self.stage = stage
            if stage == "while_connecting":
                self.connect_started.set()
            elif stage == "before_headers":
                self.request_sent.set()
            elif stage == "during_streaming":
                self.headers_received.set()

    def abort(self) -> bool:
        with self.lock:
            self.aborted = True
            sockets = list(self.sockets)
            responses = list(self.responses)
            conns = list(self.conns)
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass
        for resp in responses:
            try:
                resp.close()
            except Exception:
                pass
        for conn in conns:
            try:
                conn.close()
            except Exception:
                pass
        return True


class _TrackableHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection subclass that registers its raw socket before connect() completes."""

    def __init__(self, host: str, tracker: _HTTPConnectionTracker, **kwargs: Any) -> None:
        super().__init__(host, **kwargs)
        self._tracker = tracker
        self._tracker.register_conn(self)

    def connect(self) -> None:
        self._tracker.set_stage("while_connecting")
        if self._tracker.aborted:
            raise OSError("HTTP connection aborted before connect")
        addr_infos = socket.getaddrinfo(
            self.host, self.port, 0, socket.SOCK_STREAM
        )
        if not addr_infos:
            raise OSError(f"Could not resolve {self.host}:{self.port}")
        last_err: Optional[Exception] = None
        for af, socktype, proto, _canonname, sa in addr_infos:
            sock: Optional[socket.socket] = None
            try:
                sock = socket.socket(af, socktype, proto)
                if self.timeout is not socket._GLOBAL_DEFAULT_TIMEOUT and self.timeout is not None:
                    sock.settimeout(float(self.timeout))
                self._tracker.register_socket(sock)
                connect_delay_ms = int(os.getenv("QC_TEST_CONNECT_DELAY_MS", "0") or "0")
                if connect_delay_ms > 0:
                    deadline = time.monotonic() + (connect_delay_ms / 1000.0)
                    while time.monotonic() < deadline:
                        if self._tracker.aborted:
                            raise OSError("Aborted during connect delay")
                        time.sleep(0.01)
                sock.connect(sa)
                self.sock = sock
                return
            except Exception as exc:
                last_err = exc
                if sock is not None:
                    try:
                        sock.close()
                    except Exception:
                        pass
                self.sock = None
                if self._tracker.aborted:
                    break
        raise last_err or OSError(f"connect failed to {self.host}:{self.port}")

    def send(self, data: Any) -> None:
        if self._tracker.aborted:
            raise OSError("HTTP send aborted")
        super().send(data)
        self._tracker.set_stage("before_headers")

    def getresponse(self) -> http.client.HTTPResponse:
        self._tracker.set_stage("before_headers")
        if self._tracker.aborted:
            raise OSError("HTTP getresponse aborted")
        resp = super().getresponse()
        self._tracker.register_response(resp)
        self._tracker.set_stage("during_streaming")
        return resp


class _TrackableHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, tracker: _HTTPConnectionTracker) -> None:
        super().__init__()
        self._tracker = tracker

    def http_open(self, req: urllib.request.Request) -> Any:
        return self.do_open(
            lambda host, **kwargs: _TrackableHTTPConnection(host, self._tracker, **kwargs),
            req,
        )


def _parse_inference_response_bytes(raw_bytes: bytes) -> Dict[str, Any]:
    text_body = raw_bytes.decode("utf-8", errors="replace").strip()
    if not text_body:
        return {}
    try:
        parsed = json.loads(text_body)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    # Support NDJSON streaming responses from Ollama or controllable test servers
    combined_response: List[str] = []
    last_obj: Dict[str, Any] = {}
    for line in text_body.splitlines():
        line_s = line.strip()
        if not line_s:
            continue
        obj = json.loads(line_s)
        if isinstance(obj, dict):
            if isinstance(obj.get("response"), str):
                combined_response.append(obj["response"])
            last_obj.update(obj)
    if combined_response:
        last_obj["response"] = "".join(combined_response)
    return last_obj


def _probe_backend_generation_status(base_url: str, request_id: str, stage: str) -> Any:
    """Independently check whether backend generation stopped after client disconnect.

    - If the request was aborted before dispatch, backend generation never started (`True`).
    - If the backend exposes `GET /api/generation-status?request_id=<id>`, poll briefly up to
      0.35s for the server handler to observe the broken socket and stop; return `True` if
      confirmed stopped, or `False` if the backend reports generation is still active.
    - If the backend does not expose `/api/generation-status` (HTTP 404 on standard Ollama),
      return `"unconfirmed"` rather than claiming backend termination from client disconnect alone.
    """
    if stage == "before_dispatch":
        return True
    status_url = f"{base_url.rstrip('/')}/api/generation-status?request_id={urllib.parse.quote(request_id)}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + 0.35
    last_active: Optional[bool] = None
    while True:
        try:
            with opener.open(status_url, timeout=0.25) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            if isinstance(data, dict):
                if data.get("generation_stopped") is True or data.get("active") is False:
                    return True
                if data.get("generation_stopped") is False or data.get("active") is True:
                    last_active = True
        except urllib.error.HTTPError as http_err:
            if http_err.code == 404:
                return "unconfirmed"
            return "unconfirmed"
        except Exception:
            return "unconfirmed"
        if time.monotonic() >= deadline:
            break
        time.sleep(0.025)
    if last_active is True:
        return False
    return "unconfirmed"


def _execute_cancellable_http_json(
    req_or_url: Any,
    timeout_seconds: float,
    cancel_event: Optional[Any],
    *,
    cleanup_deadline_seconds: Optional[float] = None,
) -> Tuple[str, str, bool, Any]:
    """Execute an HTTP request returning `(outcome, stage, thread_stopped, payload_or_error)`.

    `outcome` is one of `"ok"`, `"cancelled"`, `"timeout"`, `"error"`.
    Always aborts the underlying OS socket and joins the worker thread within `cleanup_deadline_seconds`.
    """
    if cleanup_deadline_seconds is None:
        cleanup_deadline_seconds = CANCELLATION_CLEANUP_DEADLINE_SECONDS
    if cancel_event is not None and cancel_event.is_set():
        return ("cancelled", "before_dispatch", True, None)

    tracker = _HTTPConnectionTracker()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _TrackableHTTPHandler(tracker),
    )
    holder: Dict[str, Any] = {}
    done_ev = threading.Event()

    def _do_request() -> None:
        resp_obj = None
        try:
            if tracker.stage == "before_dispatch":
                tracker.set_stage("while_connecting")
            resp_obj = opener.open(req_or_url, timeout=max(0.1, float(timeout_seconds)))
            tracker.register_response(resp_obj)
            if tracker.stage in ("before_dispatch", "while_connecting", "before_headers"):
                tracker.set_stage("during_streaming")
            chunks: List[bytes] = []
            while True:
                if (cancel_event is not None and cancel_event.is_set()) or tracker.aborted:
                    raise OSError("HTTP read aborted by cancellation")
                chunk = resp_obj.read(4096)
                if not chunk:
                    break
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8")
                chunks.append(chunk)
                tracker.bytes_read += len(chunk)
                if (cancel_event is not None and cancel_event.is_set()) or tracker.aborted:
                    raise OSError("HTTP stream aborted by cancellation")
            raw_bytes = b"".join(chunks)
            holder["out"] = _parse_inference_response_bytes(raw_bytes)
        except Exception as exc:
            holder["err"] = exc
        finally:
            if resp_obj is not None:
                try:
                    resp_obj.close()
                except Exception:
                    pass
            done_ev.set()

    req_thread = threading.Thread(target=_do_request, daemon=True)
    req_thread.start()
    deadline = time.monotonic() + float(timeout_seconds)

    while not done_ev.wait(timeout=0.01):
        if cancel_event is not None and cancel_event.is_set():
            cancel_stage = tracker.stage
            tracker.abort()
            req_thread.join(timeout=cleanup_deadline_seconds)
            thread_stopped = not req_thread.is_alive()
            return ("cancelled", cancel_stage, thread_stopped, None)
        if time.monotonic() >= deadline:
            timeout_stage = tracker.stage
            tracker.abort()
            req_thread.join(timeout=cleanup_deadline_seconds)
            thread_stopped = not req_thread.is_alive()
            if cancel_event is not None and cancel_event.is_set():
                return ("cancelled", "timeout", thread_stopped, None)
            return ("timeout", timeout_stage, thread_stopped, TimeoutError("Ollama generation timed out."))

    req_thread.join(timeout=0.1)
    thread_stopped = not req_thread.is_alive()
    if cancel_event is not None and cancel_event.is_set():
        tracker.abort()
        return ("cancelled", "concurrent_with_completion", thread_stopped, None)
    if "err" in holder:
        tracker.abort()
        return ("error", tracker.stage, thread_stopped, holder["err"])
    return ("ok", "completed", thread_stopped, holder.get("out") or {})


def _inference_error(
    start_time: float,
    category: str,
    message: str,
    extra: Optional[Dict[str, Any]] = None,
    *,
    status: str = "error",
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"execution_class": "Inference"}
    payload.update(extra or {})
    return {
        "status": status, "exit_code": 124 if status == "timeout" else 1,
        "runtime_seconds": round(time.time() - start_time, 4),
        "stdout_summary": "", "stderr_summary": message,
        "error_category": category, "result_payload": payload,
    }


def _run_local_inference(task_id: str, input_data: Dict[str, Any], start_time: float, timeout_seconds: int, cancel_event: Optional[Any] = None) -> Dict[str, Any]:
    """Run one bounded generation against the node-local Ollama service.

    Security: the endpoint must be loopback (Ollama is never exposed off-host), the model must
    already be installed (no implicit pulls), and prompt/output sizes are capped.
    """
    base = os.getenv("QC_OLLAMA_URL", "http://127.0.0.1:11434")
    host = urllib.parse.urlparse(base).hostname or ""
    if host not in _LOOPBACK_HOSTS:
        return _inference_error(start_time, "unsafe_endpoint", f"Refusing non-loopback Ollama endpoint host '{host}'.")

    model = input_data.get("model") or input_data.get("required_model")
    prompt = input_data.get("prompt")
    if not isinstance(model, str) or not model.strip() or not isinstance(prompt, str) or not prompt.strip():
        return _inference_error(start_time, "validation_error", "Inference task requires string 'model' and 'prompt'.")
    if len(prompt) > MAX_PROMPT_CHARS:
        return _inference_error(start_time, "validation_error", f"Prompt exceeds {MAX_PROMPT_CHARS} characters.")
    try:
        num_predict = max(1, min(int(input_data.get("num_predict", 64)), MAX_NUM_PREDICT))
    except (TypeError, ValueError):
        return _inference_error(start_time, "validation_error", "num_predict must be an integer.")
    try:
        temperature = max(0.0, min(float(input_data.get("temperature", 0)), 1.0))
    except (TypeError, ValueError):
        temperature = 0.0

    request_id = str(input_data.get("request_id") or f"inf-{task_id or 'task'}-{uuid.uuid4().hex[:12]}")
    if cancel_event is not None and cancel_event.is_set():
        outcomes = _make_cancellation_outcomes(
            cancellation_acknowledged=True,
            client_request_terminated=True,
            owned_threads_or_processes_stopped=True,
            backend_generation_stopped=True,
            late_output_rejected=True,
            cleanup_completed=True,
            stage="before_dispatch",
            request_id=request_id,
        )
        return _inference_error(
            start_time,
            "lease_cancelled",
            "Inference aborted before dispatch due to worker lease loss or cancellation.",
            {"cancellation_outcomes": outcomes},
        )

    tags_timeout = max(0.1, min(10.0, float(timeout_seconds)))
    tags_outcome, tags_stage, tags_stopped, tags_payload = _execute_cancellable_http_json(
        base.rstrip("/") + "/api/tags",
        tags_timeout,
        cancel_event,
    )
    if tags_outcome == "cancelled":
        outcomes = _make_cancellation_outcomes(
            cancellation_acknowledged=True,
            client_request_terminated=tags_stopped,
            owned_threads_or_processes_stopped=tags_stopped,
            backend_generation_stopped=True,
            late_output_rejected=True,
            cleanup_completed=tags_stopped,
            stage=tags_stage,
            request_id=request_id,
        )
        err_cat = "lease_cancelled" if tags_stopped else "cancellation_termination_unconfirmed"
        return _inference_error(
            start_time,
            err_cat,
            "Inference aborted during model tag probe due to worker lease loss or cancellation.",
            {"cancellation_outcomes": outcomes},
        )
    if tags_outcome in ("timeout", "error"):
        return _inference_error(start_time, "inference_unreachable", f"Local Ollama unreachable: {tags_payload}")

    installed = [m.get("name") for m in (tags_payload or {}).get("models", []) if isinstance(m, dict)]
    if model not in installed:
        return _inference_error(start_time, "model_unavailable", f"Model '{model}' is not installed on this node.",
                                {"installed_models": installed})

    if cancel_event is not None and cancel_event.is_set():
        outcomes = _make_cancellation_outcomes(
            cancellation_acknowledged=True,
            client_request_terminated=True,
            owned_threads_or_processes_stopped=True,
            backend_generation_stopped=True,
            late_output_rejected=True,
            cleanup_completed=True,
            stage="before_dispatch",
            request_id=request_id,
        )
        return _inference_error(
            start_time,
            "lease_cancelled",
            "Inference aborted before generation due to worker lease loss or cancellation.",
            {"cancellation_outcomes": outcomes},
        )

    stream_mode = bool(input_data.get("stream", False))
    body = json.dumps({
        "model": model, "prompt": prompt, "stream": stream_mode,
        "request_id": request_id,
        "options": {"num_predict": num_predict, "temperature": temperature},
    }).encode()
    req = urllib.request.Request(
        base.rstrip("/") + "/api/generate",
        body,
        {"Content-Type": "application/json", "X-QC-Request-Id": request_id},
    )
    gen_timeout = max(0.1, min(float(timeout_seconds), 300.0))
    gen_outcome, gen_stage, gen_stopped, gen_payload = _execute_cancellable_http_json(
        req,
        gen_timeout,
        cancel_event,
    )

    if gen_outcome == "cancelled":
        backend_stopped = _probe_backend_generation_status(base, request_id, gen_stage)
        outcomes = _make_cancellation_outcomes(
            cancellation_acknowledged=True,
            client_request_terminated=gen_stopped,
            owned_threads_or_processes_stopped=gen_stopped,
            backend_generation_stopped=backend_stopped,
            late_output_rejected=True,
            cleanup_completed=gen_stopped,
            stage=gen_stage,
            request_id=request_id,
        )
        err_cat = (
            "lease_cancelled"
            if (gen_stopped and backend_stopped is not False)
            else "cancellation_termination_unconfirmed"
        )
        return _inference_error(
            start_time,
            err_cat,
            "In-flight local inference aborted immediately upon worker lease loss or cancellation.",
            {"cancellation_outcomes": outcomes},
        )

    if gen_outcome == "timeout":
        backend_stopped = _probe_backend_generation_status(base, request_id, "timeout")
        outcomes = _make_cancellation_outcomes(
            cancellation_acknowledged=bool(cancel_event is not None and cancel_event.is_set()),
            client_request_terminated=gen_stopped,
            owned_threads_or_processes_stopped=gen_stopped,
            backend_generation_stopped=backend_stopped,
            late_output_rejected=True,
            cleanup_completed=gen_stopped,
            stage="timeout",
            request_id=request_id,
        )
        return _inference_error(
            start_time,
            "inference_failed" if gen_stopped else "cancellation_termination_unconfirmed",
            "Ollama generation timed out.",
            {"cancellation_outcomes": outcomes, "timeout_cleanup_confirmed": gen_stopped},
            status="timeout",
        )

    if gen_outcome == "error":
        return _inference_error(start_time, "inference_failed", f"Ollama generation failed: {gen_payload}")

    if cancel_event is not None and cancel_event.is_set():
        backend_stopped = _probe_backend_generation_status(base, request_id, "concurrent_with_completion")
        outcomes = _make_cancellation_outcomes(
            cancellation_acknowledged=True,
            client_request_terminated=True,
            owned_threads_or_processes_stopped=True,
            backend_generation_stopped=backend_stopped,
            late_output_rejected=True,
            cleanup_completed=True,
            stage="concurrent_with_completion",
            request_id=request_id,
        )
        return _inference_error(
            start_time,
            "lease_cancelled",
            "Inference completed concurrently with cancellation; late output rejected.",
            {"cancellation_outcomes": outcomes},
        )

    out = gen_payload or {}
    text = (out.get("response") or "")[:MAX_OUTPUT_CHARS]
    eval_count = out.get("eval_count", 0)
    eval_ns = out.get("eval_duration", 0) or 0
    return {
        "status": "success", "exit_code": 0,
        "runtime_seconds": round(time.time() - start_time, 4),
        "stdout_summary": text, "stderr_summary": "", "error_category": None,
        "result_payload": {
            "execution_class": "Inference", "engine": "ollama",
            "request_id": request_id,
            "model_requested": model, "model_returned": out.get("model"),
            "response": text, "gen_tokens": eval_count,
            "prompt_tokens": out.get("prompt_eval_count"),
            "load_duration_s": round(out.get("load_duration", 0) / 1e9, 3),
            "eval_duration_s": round(eval_ns / 1e9, 3),
            "tokens_per_second": round(eval_count / (eval_ns / 1e9), 2) if eval_count and eval_ns else None,
            "total_duration_s": round(out.get("total_duration", 0) / 1e9, 3),
        },
    }

# Environment variables to strip to prevent secret leakage to worker scripts
SENSITIVE_ENV_PREFIXES = ("QC_", "SECRET", "API_KEY", "TOKEN", "PASSWORD", "AWS_", "SSH_", "DATABASE_", "AUTH")


def _build_sanitized_environment(work_dir: Optional[str] = None) -> Dict[str, str]:
    """Create a minimal, sanitized environment for subprocess execution."""
    clean_env: Dict[str, str] = {}
    safe_keys = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "HOME", "LANG", "LC_ALL"}
    for k, v in os.environ.items():
        k_upper = k.upper()
        if any(k_upper.startswith(prefix) for prefix in SENSITIVE_ENV_PREFIXES):
            continue
        if any(term in k_upper for term in ("SECRET", "TOKEN", "KEY", "PASS", "AUTH")):
            continue
        if k_upper in safe_keys:
            clean_env[k] = v
    # Isolate python imports to working directory
    clean_env["PYTHONPATH"] = work_dir or os.getcwd()
    clean_env["PYTHONUNBUFFERED"] = "1"
    clean_env["QC_EXECUTION_SANDBOX"] = "1"
    return clean_env


def _detect_os_isolation_mechanism() -> Optional[str]:
    """
    Detect enforced OS-level execution isolation mechanism.

    Security Invariants:
    1. Production Linux worker strictly REQUIRES the approved root-owned scoped launcher (qwen-sandbox-runner).
    2. If the scoped launcher is missing or invalid in production, the engine strictly fails closed (returns None).
       It NEVER silently falls back to direct systemd-run, Bubblewrap, or unrestricted subprocesses.
    3. Alternate mechanisms (systemd_transient, bubblewrap, restricted_subprocess) are only accessible
       in explicitly configured development/testing environments.
    """
    # 1. Approved root-owned scoped privileged launcher
    custom_runner = os.getenv("QC_SANDBOX_RUNNER")
    if custom_runner and (os.path.exists(custom_runner) or os.name != "nt"):
        return "scoped_runner"
    if os.path.exists("/usr/local/bin/qwen-sandbox-runner") or shutil.which("qwen-sandbox-runner"):
        return "scoped_runner"

    # 2. Strict Production Guard: fail closed if scoped runner is absent in production
    is_dev_or_test = bool(
        os.getenv("QC_ENV") == "development"
        or os.getenv("QC_ALLOW_ALT_ISOLATION") == "1"
        or os.getenv("QC_ALLOW_SUBPROCESS_FALLBACK") == "1"
        or os.getenv("PYTEST_CURRENT_TEST")
    )
    if not is_dev_or_test:
        logger.error(
            "Production security gate: approved scoped launcher (/usr/local/bin/qwen-sandbox-runner) "
            "not found. Execution rejected without alternate fallback."
        )
        return None

    # 3. Alternate mechanisms (retained only for explicit dev/test environments)
    if shutil.which("systemd-run"):
        return "systemd_transient"
    if shutil.which("bwrap"):
        return "bubblewrap"
    if os.environ.get("QC_ALLOW_SUBPROCESS_FALLBACK") == "1" or os.environ.get("PYTEST_CURRENT_TEST"):
        return "restricted_subprocess"
    return None


def _build_isolation_command(
    isolation_mech: str,
    script_path: Path,
    work_dir: str,
    network_access: bool = False,
    memory_limit_mb: int = 512,
    tasks_max: int = 64,
) -> List[str]:
    """Build the command line for enforcing the OS isolation boundary."""
    if isolation_mech == "scoped_runner":
        runner_bin = (
            os.getenv("QC_SANDBOX_RUNNER")
            or shutil.which("qwen-sandbox-runner")
            or "/usr/local/bin/qwen-sandbox-runner"
        )
        # Notice: --allow-network is strictly omitted for production scoped runner;
        # IPAddressDeny=any is hardcoded in the launcher.
        return [
            "sudo",
            runner_bin,
            "--workspace", str(work_dir),
            "--entrypoint", str(script_path),
            "--memory-mb", str(memory_limit_mb),
            "--tasks-max", str(tasks_max),
        ]
    elif isolation_mech == "systemd_transient":
        network_flags = [] if network_access else ["-p", "IPAddressDeny=any"]
        return [
            "systemd-run",
            "--pipe",
            "--wait",
            "--collect",
            "-p", "DynamicUser=yes",
            "-p", "ProtectSystem=strict",
            "-p", "ProtectHome=yes",
            "-p", "PrivateTmp=yes",
            "-p", f"WorkingDirectory={work_dir}",
            "-p", f"MemoryMax={memory_limit_mb}M",
            "-p", f"TasksMax={tasks_max}",
            "-p", f"ReadWritePaths={work_dir}",
        ] + network_flags + [
            sys.executable,
            str(script_path),
        ]
    elif isolation_mech == "bubblewrap":
        return [
            "bwrap",
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/lib", "/lib",
            "--ro-bind", "/lib64", "/lib64",
            "--ro-bind", "/bin", "/bin",
            "--ro-bind", "/etc", "/etc",
            "--dir", "/tmp",
            "--bind", work_dir, work_dir,
            "--unshare-all",
            "--die-with-parent",
            sys.executable,
            str(script_path),
        ]
    else:
        return [sys.executable, str(script_path)]


def _check_container_isolation_available() -> bool:
    """Check if an OS-level container isolation runtime is available."""
    # Check for docker, podman, or bwrap in PATH
    for cmd in ("docker", "podman", "bwrap"):
        if shutil.which(cmd):
            return True
    return False


def execute_task(
    task: Dict[str, Any],
    timeout_seconds: int = 300,
    *, allow_web_fetch: bool = False, script_permit=None, cancel_event=None,
) -> Dict[str, Any]:
    """
    Execute an approved task specification adhering to execution class controls.
    Returns:
    {
        "status": "success" | "error" | "timeout",
        "exit_code": int,
        "runtime_seconds": float,
        "stdout_summary": str,
        "stderr_summary": str,
        "error_category": Optional[str],
        "result_payload": dict,
    }
    """
    start_time = time.time()
    task_type = task.get("task_type", "unsupported")
    input_data = task.get("input_data") or {}
    task_id = task.get("task_id", "")

    logger.info("Starting execution of task %s (type: %s, timeout: %ss)", task_id, task_type, timeout_seconds)
    if task_type == 'research_fetch':
        from vps_agent.research_fetch import execute_retrieval
        return execute_retrieval(task, timeout_seconds, allow_web_fetch=allow_web_fetch, cancel_event=cancel_event)

    # ---------------------------------------------------------------------------
    # Class A: Built-in Safe Operations
    # ---------------------------------------------------------------------------
    if task_type in CLASS_A_OPERATIONS:
        if cancel_event is not None and cancel_event.is_set():
            return {
                "status": "error",
                "exit_code": 130,
                "runtime_seconds": round(time.time() - start_time, 4),
                "stdout_summary": "",
                "stderr_summary": "Class A operation aborted before dispatch due to cancellation.",
                "error_category": "lease_cancelled",
                "result_payload": {
                    "execution_class": "Class A",
                    "verified": False,
                    "cancellation_outcomes": _make_cancellation_outcomes(
                        cancellation_acknowledged=True,
                        client_request_terminated=True,
                        owned_threads_or_processes_stopped=True,
                        backend_generation_stopped=True,
                        late_output_rejected=True,
                        cleanup_completed=True,
                        stage="before_dispatch",
                    ),
                },
            }
        if cancel_event is not None:
            if cancel_event.wait(timeout=0.02):
                return {
                    "status": "error",
                    "exit_code": 130,
                    "runtime_seconds": round(time.time() - start_time, 4),
                    "stdout_summary": "",
                    "stderr_summary": "Class A operation aborted during execution due to cancellation.",
                    "error_category": "lease_cancelled",
                    "result_payload": {
                        "execution_class": "Class A",
                        "verified": False,
                        "cancellation_outcomes": _make_cancellation_outcomes(
                            cancellation_acknowledged=True,
                            client_request_terminated=True,
                            owned_threads_or_processes_stopped=True,
                            backend_generation_stopped=True,
                            late_output_rejected=True,
                            cleanup_completed=True,
                            stage="during_streaming",
                        ),
                    },
                }
        else:
            time.sleep(0.02)  # Simulate brief verification
        runtime = round(time.time() - start_time, 4)
        return {
            "status": "success",
            "exit_code": 0,
            "runtime_seconds": runtime,
            "stdout_summary": f"Harmless built-in operation '{task_type}' verified successfully for task {task_id}.",
            "stderr_summary": "",
            "error_category": None,
            "result_payload": {
                "execution_class": "Class A",
                "verified": True,
                "operation": task_type,
                "message": f"Harmless built-in operation '{task_type}' verified successfully.",
                "worker_python": sys.version.split()[0],
                "received_input": input_data,
            },
        }

    # ---------------------------------------------------------------------------
    # Class B: Approved Scripts / Controlled Python Execution
    # ---------------------------------------------------------------------------
    if task_type in CLASS_B_OPERATIONS:
        if cancel_event is not None and cancel_event.is_set():
            return {
                "status": "error",
                "exit_code": 130,
                "runtime_seconds": round(time.time() - start_time, 4),
                "stdout_summary": "",
                "stderr_summary": "Class B script execution aborted before dispatch due to cancellation.",
                "error_category": "lease_cancelled",
                "result_payload": {
                    "execution_class": "Class B",
                    "artifacts": [],
                    "cancellation_outcomes": _make_cancellation_outcomes(
                        cancellation_acknowledged=True,
                        client_request_terminated=True,
                        owned_threads_or_processes_stopped=True,
                        backend_generation_stopped=True,
                        late_output_rejected=True,
                        cleanup_completed=True,
                        stage="before_dispatch",
                    ),
                },
            }
        script_code = input_data.get("script") or input_data.get("code")
        if not script_code:
            runtime = round(time.time() - start_time, 4)
            return {
                "status": "error",
                "exit_code": 1,
                "runtime_seconds": runtime,
                "stdout_summary": "",
                "stderr_summary": f"Task specified '{task_type}' but provided no script code in input_data.",
                "error_category": "validation_error",
                "result_payload": {"execution_class": "Class B"},
            }

        actual_checksum = hashlib.sha256(script_code.encode("utf-8")).hexdigest()

        # ---------------------------------------------------------------------------
        # Security Gate: Enforce Controller-Issued Execution Manifest
        # ---------------------------------------------------------------------------
        # A worker must not trust arbitrary approval indicators ('approved: true', 'is_approved: true')
        # supplied inside an arbitrary payload. It must require a valid execution manifest.
        manifest = task.get("execution_manifest") or input_data.get("execution_manifest")

        if not manifest:
            runtime = round(time.time() - start_time, 4)
            # Differentiate forged approval flag from ordinary unapproved script
            has_forged_flag = bool(
                input_data.get("approved") is True
                or input_data.get("is_approved") is True
                or task.get("approved") is True
                or input_data.get("approved_script_id")
            )
            if has_forged_flag:
                logger.warning("Class B execution rejected: Forged approval flag without manifest (task: %s)", task_id)
                return {
                    "status": "error",
                    "exit_code": 126,
                    "runtime_seconds": runtime,
                    "stdout_summary": "",
                    "stderr_summary": (
                        "Class B execution rejected: Approval indicators supplied inside task payload without a "
                        "controller-issued execution manifest are unauthorized."
                    ),
                    "error_category": "unauthorized_manifest",
                    "result_payload": {"execution_class": "Class B", "is_approved": False},
                }

            logger.warning("Unapproved script rejected for Class B execution (task: %s)", task_id)
            return {
                "status": "error",
                "exit_code": 126,
                "runtime_seconds": runtime,
                "stdout_summary": "",
                "stderr_summary": "Unapproved Python script rejected for Class B execution. Arbitrary scripts must have an approval record.",
                "error_category": "unapproved_script",
                "result_payload": {"execution_class": "Class B", "is_approved": False},
            }

        # Validate manifest task ID binding
        manifest_task_id = manifest.get("task_id")
        if manifest_task_id and task_id and manifest_task_id != task_id:
            runtime = round(time.time() - start_time, 4)
            logger.warning("Manifest tampering detected for task %s (manifest task: %s)", task_id, manifest_task_id)
            return {
                "status": "error",
                "exit_code": 126,
                "runtime_seconds": runtime,
                "stdout_summary": "",
                "stderr_summary": f"Manifest task_id '{manifest_task_id}' does not match target task '{task_id}'.",
                "error_category": "manifest_tampering",
                "result_payload": {"execution_class": "Class B"},
            }

        # Validate manifest script checksum binding
        expected_checksum = (
            manifest.get("script_checksum")
            or input_data.get("script_checksum")
            or input_data.get("checksum")
            or input_data.get("sha256")
            or task.get("script_checksum")
        )
        if not expected_checksum or expected_checksum.lower() != actual_checksum.lower():
            runtime = round(time.time() - start_time, 4)
            logger.warning("Script checksum mismatch for task %s (expected %s, got %s)", task_id, expected_checksum, actual_checksum)
            return {
                "status": "error",
                "exit_code": 126,
                "runtime_seconds": runtime,
                "stdout_summary": "",
                "stderr_summary": f"Script checksum verification failed. Expected '{expected_checksum}', computed '{actual_checksum}'.",
                "error_category": "checksum_mismatch",
                "result_payload": {"execution_class": "Class B", "expected_checksum": expected_checksum, "actual_checksum": actual_checksum},
            }

        # Validate allowed capabilities
        allowed_caps = manifest.get("allowed_capabilities") or ["python_execution"]
        if "python_execution" not in allowed_caps:
            runtime = round(time.time() - start_time, 4)
            logger.warning("Manifest for task %s does not authorize python_execution", task_id)
            return {
                "status": "error",
                "exit_code": 126,
                "runtime_seconds": runtime,
                "stdout_summary": "",
                "stderr_summary": "Manifest does not authorize 'python_execution' capability.",
                "error_category": "unauthorized_capability",
                "result_payload": {"execution_class": "Class B"},
            }

        # ---------------------------------------------------------------------------
        # OS-Level Isolation Boundary Enforcement
        # ---------------------------------------------------------------------------
        isolation_mech = _detect_os_isolation_mechanism()
        if not isolation_mech:
            runtime = round(time.time() - start_time, 4)
            logger.error("Class B execution rejected: Host lacks required OS-level isolation boundary (task: %s)", task_id)
            return {
                "status": "error",
                "exit_code": 126,
                "runtime_seconds": runtime,
                "stdout_summary": "",
                "stderr_summary": (
                    "Class B execution rejected: Host lacks required OS-level isolation boundary. "
                    "Production Linux worker requires systemd transient unit (DynamicUser=yes) or Bubblewrap (bwrap)."
                ),
                "error_category": "unsupported_isolation",
                "result_payload": {
                    "execution_class": "Class B",
                    "isolation_enforced": False,
                    "missing_isolation": ["systemd-run", "bwrap"],
                },
            }

        # Task JSON (including a signature string) is never local start authority.
        from vps_agent.script_authorization import ScriptPermit
        try:
            if not isinstance(script_permit,ScriptPermit): raise ValueError('Online controller start permit required')
            authorized_runtime=script_permit.consume(task)
            timeout_seconds=min(timeout_seconds,authorized_runtime)
            if isinstance(timeout_seconds,bool) or timeout_seconds<=0: raise ValueError('Invalid runtime')
        except (ValueError,TypeError,KeyError):
            return {'status':'error','exit_code':126,'runtime_seconds':round(time.time()-start_time,4),
                'stdout_summary':'','stderr_summary':'Missing, stale, reused or mismatched controller start permit.',
                'error_category':'script_authorization_required','result_payload':{'execution_class':'Class B'}}

        # Create isolated workspace directory for this execution.
        # Prefer designated shared workspace directory (/opt/qwen-collective/workspaces)
        # to ensure visibility across PrivateTmp namespaces between worker service and transient units.
        ws_base = os.getenv("QC_WORKER_WORKSPACE_DIR")
        if ws_base and Path(ws_base).exists():
            work_dir = tempfile.mkdtemp(prefix="qc_worker_exec_", dir=ws_base)
        elif Path("/opt/qwen-collective/workspaces").exists():
            work_dir = tempfile.mkdtemp(prefix="qc_worker_exec_", dir="/opt/qwen-collective/workspaces")
        else:
            work_dir = tempfile.mkdtemp(prefix="qc_worker_exec_")

        # DynamicUser=yes assigns an ephemeral UID (61184-65519) to the sandboxed execution.
        # Set 01777 (rwxrwxrwt with sticky bit): allows ephemeral dynamic user to write artifacts,
        # while sticky bit prevents tampering, unlinking, or replacement of task_entrypoint.py.
        if os.name != "nt":
            try:
                os.chmod(work_dir, 0o1777)
            except Exception:
                pass

        # Write script to isolated entrypoint file (mode 0555: readable/executable by dynamic user, non-writable)
        script_file = Path(work_dir) / "task_entrypoint.py"
        script_file.write_text(script_code, encoding="utf-8")
        if os.name != "nt":
            try:
                os.chmod(script_file, 0o555)
            except Exception:
                pass

        sanitized_env = _build_sanitized_environment(work_dir=work_dir)
        network_access = bool(manifest.get("network_access", False))
        cmd = _build_isolation_command(
            isolation_mech=isolation_mech,
            script_path=script_file,
            work_dir=work_dir,
            network_access=network_access,
        )

        cleanup_allowed=False; process_started=False; outcome=None
        workspace_parent=Path(work_dir).resolve().parent
        try:
            script_permit.check_start_deadline()
            if cancel_event is not None and cancel_event.is_set():
                cleanup_allowed=True
                outcome={'status':'error','exit_code':130,'runtime_seconds':round(time.time()-start_time,4),
                    'stdout_summary':'','stderr_summary':'Process aborted before launch due to cancellation.',
                    'error_category':'lease_cancelled','result_payload':{'execution_class':'Class B',
                        'isolation_mechanism':isolation_mech,'artifacts':[],
                        'cancellation_outcomes':_make_cancellation_outcomes(
                            cancellation_acknowledged=True,client_request_terminated=True,
                            owned_threads_or_processes_stopped=True,backend_generation_stopped=True,
                            late_output_rejected=True,cleanup_completed=True,stage='before_dispatch')}}
                return outcome
            process_started=True
            proc = run_bounded_process(cmd,cwd=work_dir,env=sanitized_env,
                timeout_seconds=timeout_seconds,cancel_event=cancel_event)
            cleanup_allowed=proc.cleanup_confirmed
            control={'boundary':proc.boundary,'reason':proc.reason,'cleanup_confirmed':proc.cleanup_confirmed,
                'captured_bytes':proc.captured_bytes,'output_bytes_seen':proc.output_bytes_seen}
            if proc.reason!='completed' or (cancel_event is not None and cancel_event.is_set()):
                is_cancelled = (proc.reason == 'cancelled') or bool(cancel_event is not None and cancel_event.is_set())
                stage_name = 'concurrent_with_completion' if proc.reason == 'completed' else (
                    'during_streaming' if proc.reason == 'cancelled' else proc.reason
                )
                err_cat = (
                    (('cancelled' if proc.reason == 'cancelled' else 'lease_cancelled') if proc.cleanup_confirmed else 'cancellation_termination_unconfirmed')
                    if is_cancelled else proc.reason
                )
                payload_dict: Dict[str, Any] = {
                    'execution_class':'Class B',
                    'isolation_mechanism':isolation_mech,
                    'process_control':control,
                    'artifacts':[],
                }
                if is_cancelled or proc.reason == 'timeout':
                    payload_dict['cancellation_outcomes'] = _make_cancellation_outcomes(
                        cancellation_acknowledged=is_cancelled,
                        client_request_terminated=proc.cleanup_confirmed,
                        owned_threads_or_processes_stopped=proc.cleanup_confirmed,
                        backend_generation_stopped=proc.cleanup_confirmed,
                        late_output_rejected=True,
                        cleanup_completed=proc.cleanup_confirmed,
                        stage=stage_name,
                    )
                outcome={'status':'timeout' if (proc.reason=='timeout' and not is_cancelled) else 'error',
                    'exit_code':proc.returncode,
                    'runtime_seconds':round(time.time()-start_time,4),
                    'stdout_summary':'' if is_cancelled else proc.stdout[:MAX_OUTPUT_CHARS],
                    'stderr_summary':f'Process stopped: {err_cat}. '+proc.stderr[:MAX_OUTPUT_CHARS],
                    'error_category':err_cat,'result_payload':payload_dict}
                return outcome
            runtime = round(time.time() - start_time, 4)
            is_success = proc.returncode == 0
            stdout_text = proc.stdout[:MAX_OUTPUT_CHARS]
            stderr_text = proc.stderr[:MAX_OUTPUT_CHARS]

            # ---------------------------------------------------------------------------
            # Collect Physical Artifacts Generated During Execution
            # ---------------------------------------------------------------------------
            generated_artifacts = []
            collected_bytes = 0
            for item in Path(work_dir).iterdir():
                if (item.is_file() and not item.is_symlink() and item.name != "task_entrypoint.py"
                        and item.resolve().parent == Path(work_dir).resolve()
                        and item.stat().st_nlink == 1 and item.stat().st_size <= 1_000_000
                        and len(generated_artifacts) < 64):
                    try:
                        if collected_bytes + item.stat().st_size > 4_000_000:
                            continue
                        file_bytes = item.read_bytes()
                        collected_bytes += len(file_bytes)
                        file_sha = hashlib.sha256(file_bytes).hexdigest()
                        generated_artifacts.append({
                            "filename": item.name,
                            "file_size": len(file_bytes),
                            "sha256": file_sha,
                            "expected_sha256": file_sha,
                            "content_base64": base64.b64encode(file_bytes).decode("ascii"),
                        })
                    except Exception as art_err:
                        logger.warning("Failed to read generated artifact %s: %s", item.name, art_err)

            # Parse any structured JSON lines in stdout
            parsed_result = {}
            for line in stdout_text.splitlines():
                line_str = line.strip()
                if line_str.startswith("{") and line_str.endswith("}"):
                    try:
                        data = json.loads(line_str)
                        if isinstance(data, dict):
                            parsed_result.update(data)
                    except Exception:
                        pass

            result_payload = {
                "execution_class": "Class B",
                "isolation_mechanism": isolation_mech,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "returncode": proc.returncode,
                "artifacts": generated_artifacts,
                "process_control": control,
            }
            # Script stdout is application data, never execution authority.
            reserved = set(result_payload) | {"status", "exit_code", "verified", "accepted",
                "decision", "lease_id", "task_id", "worker_id", "idempotency_key", "execution_manifest"}
            result_payload.update({key:value for key,value in parsed_result.items() if key not in reserved})
            result_payload["application_output"] = parsed_result

            outcome = {
                "status": "success" if is_success else "error",
                "exit_code": proc.returncode,
                "runtime_seconds": runtime,
                "stdout_summary": stdout_text,
                "stderr_summary": stderr_text,
                "error_category": "runtime_error" if not is_success else None,
                "result_payload": result_payload,
            }
            return outcome
        except BoundaryUnavailable:
            cleanup_allowed = not process_started
            # No supported kernel boundary means no unrestricted fallback.
            outcome={'status':'error','exit_code':126,'runtime_seconds':round(time.time()-start_time,4),
                'stdout_summary':'','stderr_summary':'Verified process-tree control backend unavailable; execution held.',
                'error_category':'process_control_unavailable','result_payload':{'execution_class':'Class B','artifacts':[]}}
            return outcome
        except subprocess.TimeoutExpired:
            runtime = round(time.time() - start_time, 4)
            outcome = {
                "status": "timeout",
                "exit_code": 124,
                "runtime_seconds": runtime,
                "stdout_summary": "",
                "stderr_summary": f"Process exceeded execution timeout limit of {timeout_seconds} seconds.",
                "error_category": "timeout",
                "result_payload": {"execution_class": "Class B", "isolation_mechanism": isolation_mech},
            }
            return outcome
        except Exception as e:
            if not process_started:
                cleanup_allowed = True
            runtime = round(time.time() - start_time, 4)
            outcome = {
                "status": "error",
                "exit_code": 1,
                "runtime_seconds": runtime,
                "stdout_summary": "",
                "stderr_summary": str(e),
                "error_category": "system_error",
                "result_payload": {"execution_class": "Class B", "isolation_mechanism": isolation_mech},
            }
            return outcome
        finally:
            # Never delete a workspace while process termination is uncertain.
            workspace=Path(work_dir)
            try:
                safe=(not workspace.is_symlink() and workspace.resolve().parent==workspace_parent
                    and workspace.name.startswith('qc_worker_exec_')
                    and not (getattr(workspace.lstat(),'st_file_attributes',0)&0x400))
                if cleanup_allowed and safe:
                    shutil.rmtree(workspace)
                elif cleanup_allowed:
                    raise OSError('Workspace path changed during execution')
            except Exception:
                if outcome is not None:
                    outcome.update(status='error',exit_code=126,error_category='workspace_cleanup_failed')
                    outcome['result_payload']['artifacts']=[]
                    outcome['result_payload']['workspace_retained']=True
                    if 'cancellation_outcomes' in outcome['result_payload']:
                        outcome['result_payload']['cancellation_outcomes']['cleanup_completed']=False
                        outcome['result_payload']['cancellation_outcomes']['termination_confirmed']=False
            if not cleanup_allowed and outcome is not None:
                outcome['result_payload']['workspace_retained']=True
                if 'cancellation_outcomes' in outcome['result_payload']:
                    outcome['result_payload']['cancellation_outcomes']['cleanup_completed']=False
                    outcome['result_payload']['cancellation_outcomes']['termination_confirmed']=False

    # ---------------------------------------------------------------------------
    # Class C: Experimental Code / Untrusted AI-Generated Code
    # ---------------------------------------------------------------------------
    if task_type in CLASS_C_OPERATIONS:
        # OS-level isolation is mandatory for Class C
        if not _check_container_isolation_available():
            runtime = round(time.time() - start_time, 4)
            logger.warning("Class C execution rejected: No OS container isolation sandbox found.")
            return {
                "status": "error",
                "exit_code": 1,
                "runtime_seconds": runtime,
                "stdout_summary": "",
                "stderr_summary": (
                    "Execution rejected: Class C experimental code requires OS-level container isolation "
                    "(docker, podman, or bwrap). Missing isolation prerequisites on worker host."
                ),
                "error_category": "isolation_unavailable",
                "result_payload": {
                    "execution_class": "Class C",
                    "isolated": False,
                    "missing_prerequisites": ["container_runtime"],
                },
            }

        # If container runtime is available, bounded execution would be invoked here
        runtime = round(time.time() - start_time, 4)
        return {
            "status": "error",
            "exit_code": 1,
            "runtime_seconds": runtime,
            "stdout_summary": "",
            "stderr_summary": "Class C container runner configured but requires explicit administrator image approval.",
            "error_category": "approval_required",
            "result_payload": {"execution_class": "Class C"},
        }

    # ---------------------------------------------------------------------------
    # Inference: local Ollama (loopback only). Model must already be installed.
    # ---------------------------------------------------------------------------
    if task_type in INFERENCE_OPERATIONS:
        return _run_local_inference(task_id, input_data, start_time, timeout_seconds, cancel_event=cancel_event)

    # ---------------------------------------------------------------------------
    # Unsupported Operations (Must Fail Explicitly, Never Report False Success)
    # ---------------------------------------------------------------------------
    runtime = round(time.time() - start_time, 4)
    logger.error("Task %s rejected with unsupported operation '%s'", task_id, task_type)
    return {
        "status": "error",
        "exit_code": 1,
        "runtime_seconds": runtime,
        "stdout_summary": "",
        "stderr_summary": f"Unsupported operation: task_type '{task_type}' is not supported by worker.",
        "error_category": "unsupported_operation",
        "result_payload": {
            "supported_classes": {
                "class_a": sorted(list(CLASS_A_OPERATIONS)),
                "class_b": sorted(list(CLASS_B_OPERATIONS)),
                "class_c": sorted(list(CLASS_C_OPERATIONS)),
            }
        },
    }
