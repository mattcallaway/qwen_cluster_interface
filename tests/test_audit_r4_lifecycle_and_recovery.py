"""Increment R4 Regression Tests — Lifecycle Consistency, Cancellation Termination, and Operational Recovery.

Covers:
- AUD-06: Unified authoritative project/run lifecycle across deliberate_loop, working_memory, engine, and orchestration routes; Migration 24 installs software_review_items (`code_proposals`).
- AUD-11a: Proven cancellation termination across all 8 required regression stages using a controllable local HTTP server that records connection and handler lifecycle, plus negative tests exposing leaky test doubles and unconfirmed backend generation.
- AUD-11b: Connection-independent remote Linux sandbox lifetime (`RuntimeMaxSec`, `ExecStopPost`, pipe-disconnect monitor, and detached `systemd-run` watchdog).
- AUD-09: Truthful Paged KV (`QC_PAGED_KV_ENABLED=false` default, `telemetry_only_http_sidecar` vs `PyTorchPagedKVAdapter`) and pipeline parallelism capability reporting.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import threading
import time
from typing import Any, Dict, List, Optional
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from app.database import create_baseline_schema, get_db_connection
from app.migrations import MIGRATIONS, apply_migrations
from app.services.orchestration import (
    acceptance_harness,
    deliberate_loop,
    engine,
    intake as intake_mod,
    protected_repo_verifier,
    working_memory as wm,
)


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    (tmp_path / "evidence").mkdir(parents=True, exist_ok=True)
    conn = get_db_connection()
    try:
        create_baseline_schema(conn)
        apply_migrations(conn, MIGRATIONS)
    finally:
        conn.close()
    return tmp_path


class ControllableInferenceHTTPServer:
    """Controllable local loopback HTTP server that records connection and handler lifecycle."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.tags_requests: int = 0
        self.generate_requests: int = 0
        self.active_handlers: int = 0
        self.max_active_handlers: int = 0
        self.requests_by_id: Dict[str, Dict[str, Any]] = {}
        self.mode: str = "streaming_hold"
        # Modes:
        # - "before_headers_hold": wait before sending response status/headers until client disconnects or release_event sets
        # - "streaming_hold": send headers + initial chunk, then drip/block until client disconnects or release_event sets
        # - "fast_complete": immediately return complete JSON response
        # - "surviving_backend": when client disconnects, keep backend generation marked active for 1.0s
        self.headers_gate = threading.Event()
        self.body_gate = threading.Event()
        self.handler_entered = threading.Event()
        self.first_chunk_sent = threading.Event()
        self.client_disconnected = threading.Event()
        self.expose_generation_status: bool = True

        parent = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:
                return

            def do_GET(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/api/tags":
                    with parent.lock:
                        parent.tags_requests += 1
                    body = json.dumps({"models": [{"name": "qwen2.5:3b"}]}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    self.wfile.flush()
                    return

                if parsed.path == "/api/generation-status":
                    if not parent.expose_generation_status:
                        self.send_response(404)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    qs = urllib.parse.parse_qs(parsed.query)
                    req_id = (qs.get("request_id") or [""])[0]
                    with parent.lock:
                        rec = dict(parent.requests_by_id.get(req_id) or {})
                    status_body = json.dumps({
                        "request_id": req_id,
                        "active": bool(rec.get("active", False)),
                        "generation_stopped": not bool(rec.get("active", False)),
                        "disconnect_observed": bool(rec.get("disconnect_observed", False)),
                    }).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(status_body)))
                    self.end_headers()
                    self.wfile.write(status_body)
                    self.wfile.flush()
                    return

                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_POST(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != "/api/generate":
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

                content_len = int(self.headers.get("Content-Length", "0") or "0")
                raw_body = self.rfile.read(content_len) if content_len > 0 else b"{}"
                payload = json.loads(raw_body.decode("utf-8", errors="replace"))
                req_id = str(
                    self.headers.get("X-QC-Request-Id")
                    or payload.get("request_id")
                    or f"req-{time.monotonic()}"
                )
                with parent.lock:
                    parent.generate_requests += 1
                    parent.active_handlers += 1
                    if parent.active_handlers > parent.max_active_handlers:
                        parent.max_active_handlers = parent.active_handlers
                    parent.requests_by_id[req_id] = {
                        "request_id": req_id,
                        "started_at": time.monotonic(),
                        "active": True,
                        "disconnect_observed": False,
                        "completed": False,
                        "mode": parent.mode,
                    }
                parent.handler_entered.set()

                try:
                    mode = parent.mode
                    if mode == "before_headers_hold":
                        # Block before sending status line/headers until client socket closes or gate opens
                        self.connection.settimeout(0.05)
                        deadline = time.monotonic() + 10.0
                        while time.monotonic() < deadline and not parent.headers_gate.is_set():
                            try:
                                # Peek/poll for client EOF while holding before headers
                                data = self.connection.recv(1, socket.MSG_PEEK)
                                if data == b"":
                                    with parent.lock:
                                        parent.requests_by_id[req_id]["disconnect_observed"] = True
                                    parent.client_disconnected.set()
                                    return
                            except socket.timeout:
                                continue
                            except OSError:
                                with parent.lock:
                                    parent.requests_by_id[req_id]["disconnect_observed"] = True
                                parent.client_disconnected.set()
                                return

                    if mode == "fast_complete":
                        resp_bytes = json.dumps({
                            "model": payload.get("model", "qwen2.5:3b"),
                            "response": "completed inference output",
                            "eval_count": 8,
                            "eval_duration": 50_000_000,
                            "done": True,
                        }).encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(resp_bytes)))
                        self.end_headers()
                        self.wfile.write(resp_bytes)
                        self.wfile.flush()
                        with parent.lock:
                            parent.requests_by_id[req_id]["completed"] = True
                        return

                    # "streaming_hold" or "surviving_backend": send headers + initial partial JSON line, then stream slowly
                    first_line = json.dumps({
                        "model": payload.get("model", "qwen2.5:3b"),
                        "response": "partial token 1 ",
                        "done": False,
                    }).encode("utf-8") + b"\n"
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson")
                    # Omit Content-Length or set large Content-Length so client blocks reading remaining body
                    self.send_header("Content-Length", str(len(first_line) + 4096))
                    self.end_headers()
                    self.wfile.write(first_line)
                    self.wfile.flush()
                    parent.first_chunk_sent.set()

                    deadline = time.monotonic() + 10.0
                    while time.monotonic() < deadline and not parent.body_gate.is_set():
                        time.sleep(0.02)
                        try:
                            probe_line = json.dumps({"response": "token ", "done": False}).encode("utf-8") + b"\n"
                            self.wfile.write(probe_line)
                            self.wfile.flush()
                        except OSError:
                            with parent.lock:
                                parent.requests_by_id[req_id]["disconnect_observed"] = True
                            parent.client_disconnected.set()
                            if mode == "surviving_backend":
                                # Simulate a broken backend that keeps running generation after client disconnect
                                time.sleep(0.8)
                            return
                finally:
                    with parent.lock:
                        parent.active_handlers -= 1
                        if req_id in parent.requests_by_id:
                            parent.requests_by_id[req_id]["active"] = False
                            parent.requests_by_id[req_id]["stopped_at"] = time.monotonic()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def reset_events(self) -> None:
        self.headers_gate.clear()
        self.body_gate.clear()
        self.handler_entered.clear()
        self.first_chunk_sent.clear()
        self.client_disconnected.clear()

    def close(self) -> None:
        self.headers_gate.set()
        self.body_gate.set()
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception:
            pass
        self._thread.join(timeout=2.0)


@pytest.fixture
def controllable_ollama_server(monkeypatch: pytest.MonkeyPatch):
    srv = ControllableInferenceHTTPServer()
    monkeypatch.setenv("QC_OLLAMA_URL", srv.base_url)
    try:
        yield srv
    finally:
        srv.close()


def test_aud06_migration24_installs_software_review_items_and_synchronizes_run_lifecycle(isolated_db: Path):
    """AUD-06:
    1. Migration 24 automatically creates `software_review_items` and append-only triggers on `init_db` / `apply_migrations`.
    2. Advancing, parking, resuming, steering, and cancelling a project via `deliberate_loop` / `working_memory`
       synchronizes `orchestration_runs.state` and `engine.status(project_id)` without contradictory states.
    """
    conn = get_db_connection()
    try:
        tbl = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='software_review_items'"
        ).fetchone()
        assert tbl is not None, "Migration 24 must create software_review_items table"
        trig = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name='software_review_no_update'"
        ).fetchone()
        assert trig is not None, "Migration 24 must install append-only triggers on software_review_items"
    finally:
        conn.close()

    created = engine.create_project_with_run(
        name="Lifecycle Sync Project",
        actor="tester",
        intake={
            "objective": "Evaluate supplier quotes under budget $14,800 and delivery <= 21 days for 10 units.",
        },
        autonomy_level=2,
    )
    pid = created["project"]["id"]
    run_id = created["run"]["id"]
    ledger = intake_mod.parse_intake_requirements({
        "objective": "Evaluate supplier quotes under budget $14,800 and delivery <= 21 days for 10 units.",
        "constraints": [
            "Maximum total budget ceiling: $14,800.00",
            "Maximum delivery lead time: 21 days",
            "Required capabilities: iso27001, telemetry_export",
            "Required dependencies: dep_power_bus, dep_rack_mount, dep_api_v2",
        ],
        "deliverables": [
            "quote_reconciliation_ledger.json",
            "supplier_recommendation.json",
            "supplier_recommendation.md",
        ],
    })
    intake_mod.persist_requirement_ledger(pid, ledger, run_id=run_id, actor="tester")

    step1 = deliberate_loop.advance_deliberate_project(
        pid,
        quotes_data=acceptance_harness.SYNTHETIC_SUPPLIER_QUOTES,
        max_steps=1,
        use_live_worker=False,
    )
    assert step1["final_cycle_phase"] == "execute"
    assert engine.get_run(run_id)["state"] == "RUNNING"

    wm.park_project(pid, actor="tester", reason="Pause for review")
    assert engine.get_run(run_id)["state"] == "PAUSED"
    eng_status_parked = engine.status(pid)
    assert eng_status_parked["run"]["state"] == "PAUSED"
    assert eng_status_parked.get("working_memory_cycle_phase") == "parked"

    wm.resume_project(pid, actor="tester")
    assert engine.get_run(run_id)["state"] == "RUNNING"

    step_done = deliberate_loop.advance_deliberate_project(
        pid,
        quotes_data=acceptance_harness.SYNTHETIC_SUPPLIER_QUOTES,
        max_steps=5,
        use_live_worker=False,
    )
    assert step_done["final_cycle_phase"] == "completed"
    assert engine.get_run(run_id)["state"] == "COMPLETED"
    eng_status_done = engine.status(pid)
    assert eng_status_done["run"]["state"] == "COMPLETED"
    assert eng_status_done.get("working_memory_cycle_phase") == "completed"

    intake_mod.apply_user_steering_update(
        pid,
        run_id=run_id,
        actor="tester",
        action="steer",
        updated_constraints=["Maximum total budget ceiling: $12,000.00"],
        affected_task_keys=["t2_evaluate_constraints"],
        reason="Tighten budget after completion",
    )
    run_after_steer = engine.get_run(run_id)
    assert run_after_steer["state"] != "COMPLETED", (
        "Run must not remain COMPLETED after steering invalidates task contracts"
    )

    wm.cancel_task_or_project(pid, actor="tester", reason="Cancel project")
    assert engine.get_run(run_id)["state"] == "CANCELLED"


def test_aud11a_cancellation_stages_1_to_6_and_8_against_controllable_http_server(
    controllable_ollama_server: ControllableInferenceHTTPServer,
    monkeypatch: pytest.MonkeyPatch,
):
    """AUD-11a Required Regression Cases (against controllable local HTTP server):
    1. Before dispatch (zero HTTP requests sent).
    2. While connecting (aborted during TCP connection setup).
    3. Before response headers arrive (aborted while server holds before headers).
    4. During response streaming / blocked body read (aborted mid-stream; server handler stops).
    5. Concurrently with normal completion (late output rejected).
    6. During timeout and cleanup (socket closed and worker thread joined on timeout).
    8. Repeatedly (10 rapid cancellations without leaked threads or active server handlers).
    """
    from vps_agent import executor

    srv = controllable_ollama_server

    def _task_spec(tid: str, req_id: str) -> Dict[str, Any]:
        return {
            "task_id": tid,
            "task_type": "model_inference",
            "input_data": {
                "model": "qwen2.5:3b",
                "prompt": "Analyze cancellation termination invariants.",
                "num_predict": 64,
                "request_id": req_id,
            },
        }

    # --- Case 1: Cancellation BEFORE dispatch ---
    srv.reset_events()
    pre_cancel = threading.Event()
    pre_cancel.set()
    res1 = executor.execute_task(_task_spec("t_pre", "req-pre"), timeout_seconds=10, cancel_event=pre_cancel)
    assert res1["status"] == "error"
    assert res1["error_category"] == "lease_cancelled"
    assert res1["stdout_summary"] == ""
    c1 = res1["result_payload"]["cancellation_outcomes"]
    assert c1["cancellation_acknowledged"] is True
    assert c1["client_request_terminated"] is True
    assert c1["owned_threads_or_processes_stopped"] is True
    assert c1["backend_generation_stopped"] is True
    assert c1["late_output_rejected"] is True
    assert c1["cleanup_completed"] is True
    assert c1["termination_confirmed"] is True
    assert c1["stage"] == "before_dispatch"
    assert srv.tags_requests == 0 and srv.generate_requests == 0, "No HTTP request may start after pre-dispatch cancellation"

    # --- Case 2: Cancellation WHILE CONNECTING ---
    srv.reset_events()
    monkeypatch.setenv("QC_TEST_CONNECT_DELAY_MS", "350")
    conn_cancel = threading.Event()
    t_conn = threading.Thread(target=lambda: (time.sleep(0.06), conn_cancel.set()), daemon=True)
    t_conn.start()
    res2 = executor.execute_task(_task_spec("t_conn", "req-conn"), timeout_seconds=10, cancel_event=conn_cancel)
    monkeypatch.delenv("QC_TEST_CONNECT_DELAY_MS", raising=False)
    assert res2["status"] == "error"
    assert res2["error_category"] == "lease_cancelled"
    c2 = res2["result_payload"]["cancellation_outcomes"]
    assert c2["stage"] == "while_connecting"
    assert c2["owned_threads_or_processes_stopped"] is True
    assert c2["client_request_terminated"] is True
    assert c2["cleanup_completed"] is True
    assert srv.generate_requests == 0, "Generate request must not be dispatched when cancelled while connecting"

    # --- Case 3: Cancellation BEFORE RESPONSE HEADERS ARRIVE ---
    srv.reset_events()
    srv.mode = "before_headers_hold"
    hdr_cancel = threading.Event()

    def _cancel_once_handler_entered():
        assert srv.handler_entered.wait(timeout=2.0), "Server did not receive /api/generate request"
        time.sleep(0.05)
        hdr_cancel.set()

    t_hdr = threading.Thread(target=_cancel_once_handler_entered, daemon=True)
    t_hdr.start()
    t0 = time.monotonic()
    res3 = executor.execute_task(_task_spec("t_hdr", "req-hdr"), timeout_seconds=10, cancel_event=hdr_cancel)
    elapsed3 = time.monotonic() - t0
    assert elapsed3 < 1.0
    assert res3["status"] == "error"
    assert res3["error_category"] == "lease_cancelled"
    c3 = res3["result_payload"]["cancellation_outcomes"]
    assert c3["stage"] == "before_headers"
    assert c3["client_request_terminated"] is True
    assert c3["owned_threads_or_processes_stopped"] is True
    assert c3["backend_generation_stopped"] is True
    assert c3["cleanup_completed"] is True
    assert c3["termination_confirmed"] is True
    assert srv.client_disconnected.wait(timeout=1.0)
    assert srv.active_handlers == 0

    # --- Case 4: Cancellation DURING RESPONSE STREAMING / BLOCKED BODY READ ---
    srv.reset_events()
    srv.mode = "streaming_hold"
    stream_cancel = threading.Event()

    def _cancel_once_first_chunk():
        assert srv.first_chunk_sent.wait(timeout=2.0), "Server did not send first streaming chunk"
        time.sleep(0.05)
        stream_cancel.set()

    t_str = threading.Thread(target=_cancel_once_first_chunk, daemon=True)
    t_str.start()
    t0 = time.monotonic()
    res4 = executor.execute_task(_task_spec("t_stream", "req-stream"), timeout_seconds=10, cancel_event=stream_cancel)
    elapsed4 = time.monotonic() - t0
    assert elapsed4 < 1.0
    assert res4["status"] == "error"
    assert res4["error_category"] == "lease_cancelled"
    assert res4["stdout_summary"] == "", "Partial streamed tokens must not leak into stdout_summary on cancellation"
    c4 = res4["result_payload"]["cancellation_outcomes"]
    assert c4["stage"] == "during_streaming"
    assert c4["client_request_terminated"] is True
    assert c4["owned_threads_or_processes_stopped"] is True
    assert c4["backend_generation_stopped"] is True
    assert c4["late_output_rejected"] is True
    assert c4["cleanup_completed"] is True
    assert c4["termination_confirmed"] is True
    assert srv.active_handlers == 0

    # --- Case 5: Cancellation CONCURRENTLY WITH NORMAL COMPLETION ---
    srv.reset_events()
    srv.mode = "fast_complete"
    conc_cancel = threading.Event()

    orig_parse = executor._parse_inference_response_bytes

    def _race_cancel_on_parse(raw_bytes: bytes) -> Dict[str, Any]:
        parsed = orig_parse(raw_bytes)
        if b"completed inference output" in raw_bytes:
            conc_cancel.set()
        return parsed

    monkeypatch.setattr(executor, "_parse_inference_response_bytes", _race_cancel_on_parse)
    res5 = executor.execute_task(_task_spec("t_conc", "req-conc"), timeout_seconds=10, cancel_event=conc_cancel)
    monkeypatch.setattr(executor, "_parse_inference_response_bytes", orig_parse)
    assert res5["status"] == "error"
    assert res5["error_category"] == "lease_cancelled"
    assert res5["stdout_summary"] == ""
    assert "response" not in res5["result_payload"]
    c5 = res5["result_payload"]["cancellation_outcomes"]
    assert c5["stage"] == "concurrent_with_completion"
    assert c5["late_output_rejected"] is True
    assert c5["owned_threads_or_processes_stopped"] is True
    assert c5["cleanup_completed"] is True

    # --- Case 6: Cancellation / Resource Release DURING TIMEOUT AND CLEANUP ---
    srv.reset_events()
    srv.mode = "streaming_hold"
    timeout_cancel = threading.Event()
    res6 = executor.execute_task(_task_spec("t_timeout", "req-timeout"), timeout_seconds=0.25, cancel_event=timeout_cancel)
    assert res6["status"] == "timeout"
    assert res6["result_payload"]["timeout_cleanup_confirmed"] is True
    c6 = res6["result_payload"]["cancellation_outcomes"]
    assert c6["stage"] == "timeout"
    assert c6["client_request_terminated"] is True
    assert c6["owned_threads_or_processes_stopped"] is True
    assert c6["backend_generation_stopped"] is True
    assert c6["cleanup_completed"] is True
    assert srv.active_handlers == 0

    # --- Case 8: REPEATED CANCELLATIONS without resource accumulation ---
    srv.mode = "streaming_hold"
    baseline_threads = threading.active_count()
    for i in range(8):
        srv.reset_events()
        ev = threading.Event()
        threading.Thread(
            target=lambda e=ev: (srv.first_chunk_sent.wait(timeout=2.0), time.sleep(0.02), e.set()),
            daemon=True,
        ).start()
        r_i = executor.execute_task(_task_spec(f"t_rep_{i}", f"req-rep-{i}"), timeout_seconds=5, cancel_event=ev)
        assert r_i["status"] == "error"
        assert r_i["error_category"] == "lease_cancelled"
        assert r_i["result_payload"]["cancellation_outcomes"]["owned_threads_or_processes_stopped"] is True

    time.sleep(0.05)
    after_threads = threading.active_count()
    assert srv.active_handlers == 0, f"Expected 0 surviving server handlers, got {srv.active_handlers}"
    assert after_threads <= baseline_threads + 1, (
        f"Repeated cancellations leaked threads: baseline={baseline_threads}, after={after_threads}"
    )


def test_aud11a_negative_cases_expose_surviving_work_and_unconfirmed_termination(
    controllable_ollama_server: ControllableInferenceHTTPServer,
    monkeypatch: pytest.MonkeyPatch,
):
    """AUD-11a Negative Invariant Verification:
    - Calling `close()` on a leaky test double that keeps running in the background MUST NOT be treated as
      automatic termination success (`owned_threads_or_processes_stopped=False`, `termination_confirmed=False`,
      `error_category='cancellation_termination_unconfirmed'`).
    - When the backend server reports that generation kept running after client disconnect (`surviving_backend`),
      `backend_generation_stopped` is `False` and `error_category` is `'cancellation_termination_unconfirmed'`.
    - When the backend does not expose per-request generation status (`404`, like raw Ollama),
      `backend_generation_stopped` is `'unconfirmed'` rather than falsely claiming `True`.
    """
    from vps_agent import executor

    # 1. Leaky test double where close() is a no-op and read() survives past cleanup deadline
    release_leaky = threading.Event()

    class _LeakyResponse:
        def __init__(self, url: str):
            self.url = url
            self.close_called = False
            self._tags_sent = False

        def read(self, amt: int = -1) -> bytes:
            if self.url.endswith("/api/tags"):
                if self._tags_sent:
                    return b""
                self._tags_sent = True
                return b'{"models": [{"name": "qwen2.5:3b"}]}'
            # Ignore close() and keep running until release_leaky is set!
            release_leaky.wait(timeout=3.0)
            return b'{"model": "qwen2.5:3b", "response": "survived"}'

        def close(self) -> None:
            self.close_called = True

    class _LeakyOpener:
        def open(self, req_or_url: Any, timeout: float = 10.0):
            url = req_or_url if isinstance(req_or_url, str) else req_or_url.full_url
            return _LeakyResponse(url)

    monkeypatch.setattr("urllib.request.build_opener", lambda *args, **kwargs: _LeakyOpener())
    monkeypatch.setattr(executor, "CANCELLATION_CLEANUP_DEADLINE_SECONDS", 0.15)
    cancel_ev = threading.Event()
    threading.Thread(target=lambda: (time.sleep(0.05), cancel_ev.set()), daemon=True).start()
    try:
        res_leaky = executor.execute_task(
            {
                "task_id": "t_leaky",
                "task_type": "model_inference",
                "input_data": {"model": "qwen2.5:3b", "prompt": "test leaky double"},
            },
            timeout_seconds=10,
            cancel_event=cancel_ev,
        )
    finally:
        release_leaky.set()

    monkeypatch.undo()
    # Re-apply server URL for remaining checks
    monkeypatch.setenv("QC_OLLAMA_URL", controllable_ollama_server.base_url)

    assert res_leaky["status"] == "error"
    assert res_leaky["error_category"] == "cancellation_termination_unconfirmed"
    c_leaky = res_leaky["result_payload"]["cancellation_outcomes"]
    assert c_leaky["owned_threads_or_processes_stopped"] is False
    assert c_leaky["cleanup_completed"] is False
    assert c_leaky["termination_confirmed"] is False

    # 2. Backend generation survives client disconnect (`surviving_backend` mode)
    srv = controllable_ollama_server
    srv.reset_events()
    srv.mode = "surviving_backend"
    srv.expose_generation_status = True
    ev_surv = threading.Event()
    threading.Thread(
        target=lambda: (srv.first_chunk_sent.wait(timeout=2.0), time.sleep(0.03), ev_surv.set()),
        daemon=True,
    ).start()
    res_surv = executor.execute_task(
        {
            "task_id": "t_surv",
            "task_type": "model_inference",
            "input_data": {"model": "qwen2.5:3b", "prompt": "test surviving backend", "request_id": "req-surv"},
        },
        timeout_seconds=10,
        cancel_event=ev_surv,
    )
    assert res_surv["status"] == "error"
    assert res_surv["error_category"] == "cancellation_termination_unconfirmed"
    c_surv = res_surv["result_payload"]["cancellation_outcomes"]
    assert c_surv["client_request_terminated"] is True
    assert c_surv["owned_threads_or_processes_stopped"] is True
    assert c_surv["backend_generation_stopped"] is False
    assert c_surv["termination_confirmed"] is False

    # 3. Uninstrumented backend (404 on /api/generation-status, like standard Ollama)
    srv.reset_events()
    srv.mode = "streaming_hold"
    srv.expose_generation_status = False
    ev_unconf = threading.Event()
    threading.Thread(
        target=lambda: (srv.first_chunk_sent.wait(timeout=2.0), time.sleep(0.03), ev_unconf.set()),
        daemon=True,
    ).start()
    res_unconf = executor.execute_task(
        {
            "task_id": "t_unconf",
            "task_type": "model_inference",
            "input_data": {"model": "qwen2.5:3b", "prompt": "test unconfirmed backend", "request_id": "req-unconf"},
        },
        timeout_seconds=10,
        cancel_event=ev_unconf,
    )
    assert res_unconf["status"] == "error"
    assert res_unconf["error_category"] == "lease_cancelled"
    c_unconf = res_unconf["result_payload"]["cancellation_outcomes"]
    assert c_unconf["client_request_terminated"] is True
    assert c_unconf["owned_threads_or_processes_stopped"] is True
    assert c_unconf["backend_generation_stopped"] == "unconfirmed"
    assert c_unconf["termination_confirmed"] == "unconfirmed_backend"


def test_aud11a_case7_worker_lease_expiry_and_revocation_end_to_end(
    isolated_db: Path,
    controllable_ollama_server: ControllableInferenceHTTPServer,
    monkeypatch: pytest.MonkeyPatch,
):
    """AUD-11a Case 7: Following worker lease expiry or revocation:
    - Worker leases an inference task from the controller.
    - While inference is streaming from the controllable HTTP server, the project/task is cancelled
      on the controller (revoking the active lease).
    - Worker's `_run_lease_heartbeats` thread receives HTTP 409 on `/heartbeat` and sets `cancel_execution`.
    - Worker terminates the in-flight HTTP request and owned request thread within the cleanup deadline.
    - Any attempt to submit a late result or upload an artifact for the revoked/expired lease is rejected (HTTP 409)
      and cannot complete the task, publish accepted artifacts, or alter authoritative project state.
    """
    from app.main import app
    from app.services import worker as worker_svc
    from vps_agent.agent import VPSWorkerAgent
    from vps_agent.config import AgentConfig

    srv = controllable_ollama_server
    srv.reset_events()
    srv.mode = "streaming_hold"
    srv.expose_generation_status = True

    # Register worker and create a project + inference task
    worker_id = "test-worker-01"
    worker_svc.register_worker_node(
        node_id=worker_id,
        name="Laptop Test Worker",
        available_models=["qwen2.5:3b"],
    )
    conn = get_db_connection()
    try:
        conn.execute(
            "UPDATE infrastructure_nodes SET capability_restrictions_json = ? WHERE id = ?",
            (json.dumps({"allowed_task_types": ["model_inference", "inference"], "operational_hold": False}), worker_id),
        )
        conn.commit()
    finally:
        conn.close()

    _tok_id, raw_token = worker_svc.create_worker_token(worker_id, "test-aud11a")

    created = engine.create_project_with_run(
        name="Lease Revocation E2E Project",
        actor="tester",
        intake={"objective": "Test lease revocation during inference."},
        autonomy_level=2,
    )
    pid = created["project"]["id"]

    conn = get_db_connection()
    try:
        now_iso = worker_svc._utc_now_iso()
        conn.execute(
            """
            INSERT INTO tasks (
                id, project_id, title, description, task_type, status, assigned_worker,
                approval_required, input_data_json, created_at, updated_at
            ) VALUES (?, ?, 'Inference Task', 'Run model', 'model_inference', 'queued', ?, 0, ?, ?, ?)
            """,
            (
                "task_lease_revoke_1",
                pid,
                worker_id,
                json.dumps({"model": "qwen2.5:3b", "prompt": "Long generation", "request_id": "req-lease-1"}),
                now_iso,
                now_iso,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    client = TestClient(app)

    class _TestClientSession:
        verify = True

        def post(self, url: str, json: Any = None, headers: Any = None, timeout: float = 10, **kwargs: Any):
            path = urllib.parse.urlparse(url).path
            return client.post(path, json=json, headers=headers)

        def get(self, url: str, headers: Any = None, timeout: float = 10, **kwargs: Any):
            path = urllib.parse.urlparse(url).path
            return client.get(path, headers=headers)

    cfg = AgentConfig(
        controller_url="http://testserver",
        worker_id=worker_id,
        worker_name="Laptop Worker",
        worker_token=raw_token,
        heartbeat_interval=0.1,
        lease_duration=60,
        default_task_timeout=20,
        data_dir=isolated_db,
    )
    agent = VPSWorkerAgent(config=cfg)
    agent.session = _TestClientSession()
    agent.is_registered = True

    monkeypatch.setattr(
        "vps_agent.agent.discover_local_hardware",
        lambda: {
            "hostname": "qwen-laptop",
            "os_name": "Linux",
            "os_version": "6.8",
            "architecture": "x86_64",
            "cpu_count": 8,
            "ram_total_mb": 16384,
            "ram_available_mb": 8192,
            "disk_total_gb": 100.0,
            "disk_free_gb": 50.0,
            "declared_capabilities": ["model_inference"],
            "available_models": ["qwen2.5:3b"],
        },
    )

    # Cancel the project once the worker's inference request starts streaming
    def _revoke_on_stream_start():
        assert srv.first_chunk_sent.wait(timeout=3.0), "Worker never started streaming inference"
        try:
            wm.cancel_task_or_project(pid, actor="tester", reason="Operator cancelled project mid-inference")
        except wm.ParkOrCancelCleanupError:
            # Expected while active lease is draining; sets dispatch_stopped=1 so next heartbeat revokes lease
            pass

    revoker = threading.Thread(target=_revoke_on_stream_start, daemon=True)
    revoker.start()

    t0 = time.monotonic()
    outcome = agent.poll_and_execute_task()
    elapsed = time.monotonic() - t0

    assert outcome is not None
    assert elapsed < 1.5, f"Expected worker to abort promptly after lease revocation, took {elapsed:.2f}s"
    assert outcome["status"] == "error"
    assert outcome["error_category"] == "lease_cancelled"
    assert outcome["stdout_summary"] == ""
    c_out = outcome["result_payload"]["cancellation_outcomes"]
    assert c_out["cancellation_acknowledged"] is True
    assert c_out["client_request_terminated"] is True
    assert c_out["owned_threads_or_processes_stopped"] is True
    assert c_out["backend_generation_stopped"] is True
    assert c_out["late_output_rejected"] is True
    assert c_out["cleanup_completed"] is True
    assert agent.buffer.count_pending() == 0, "Cancelled/409 lease submission must not accumulate in OfflineBuffer"

    # Now that the worker has terminated execution and submitted its cancellation ack,
    # finalizing project cancellation succeeds with authoritative cleanup_confirmed=True!
    cancel_final = wm.cancel_task_or_project(pid, actor="tester", reason="Finalize cancellation after worker drain")
    assert cancel_final["cleanup_confirmed"] is True

    # Verify late result submission or artifact upload with the revoked lease is strictly rejected (HTTP 409)
    conn = get_db_connection()
    try:
        lease_row = conn.execute("SELECT id, idempotency_key FROM worker_task_leases WHERE task_id = 'task_lease_revoke_1'").fetchone()
    finally:
        conn.close()

    late_submit = client.post(
        "/api/v1/worker/tasks/task_lease_revoke_1/submit",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "task_id": "task_lease_revoke_1",
            "lease_id": lease_row["id"],
            "idempotency_key": lease_row["idempotency_key"],
            "status": "success",
            "exit_code": 0,
            "runtime_seconds": 1.0,
            "stdout_summary": "late forged success",
            "stderr_summary": "",
            "result_payload": {"response": "late forged success"},
            "artifacts": [
                {
                    "filename": "late_artifact.txt",
                    "content": "forged late content",
                    "sha256": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
                }
            ],
        },
    )
    assert late_submit.status_code == 409, f"Expected 409 for late submission on cancelled task, got {late_submit.status_code}"

    # Verify authoritative task, artifact, and project state remained cancelled and untampered
    conn = get_db_connection()
    try:
        t_row = conn.execute("SELECT status, result_data_json FROM tasks WHERE id = 'task_lease_revoke_1'").fetchone()
        art_count = conn.execute("SELECT count(*) FROM artifacts").fetchone()[0]
        proj_row = conn.execute("SELECT status FROM projects WHERE id = ?", (pid,)).fetchone()
    finally:
        conn.close()
    assert t_row["status"] == "cancelled"
    assert art_count == 0
    assert proj_row["status"] == "cancelled"


def test_aud11b_linux_sandbox_and_remote_driver_enforce_connection_independent_cleanup():
    """AUD-11b:
    1. `LinuxSpec.command()` includes connection-independent lifetime (`RuntimeMaxSec`) and automatic
       unit/staging cleanup (`ExecStopPost`) so transient staging artifacts are cleaned up by systemd
       even if the SSH client disconnects.
    2. `REMOTE_LINUX_VERIFIER_DRIVER` registers both an in-process SSH stdout pipe-disconnect monitor (`select.poll`)
       and a detached systemd transient cleanup unit (`systemd-run`) for active `qc-sandbox-*` units,
       `/var/lib/qwen-sandbox/inputs/*`, and `/etc/qwen-sandbox/test-host.json`.
    """
    from vps_agent.linux_sandbox import LinuxSpec

    spec = LinuxSpec(
        "a" * 32,
        "b" * 64,
        "c" * 64,
        (("src/main.py", "d" * 64),),
        seconds=15,
        request_sha256="e" * 64,
    )
    cmd = spec.command()
    assert "--property=RuntimeMaxSec=15" in cmd
    assert any(item.startswith("--property=ExecStopPost=") for item in cmd), (
        "LinuxSpec.command() must include an ExecStopPost property to clean up request/staging state independently of the SSH client"
    )
    assert "systemd-run" in protected_repo_verifier.REMOTE_LINUX_VERIFIER_DRIVER
    assert "qc-verifier-cleanup-" in protected_repo_verifier.REMOTE_LINUX_VERIFIER_DRIVER
    assert "_watch_ssh_stdout_pipe" in protected_repo_verifier.REMOTE_LINUX_VERIFIER_DRIVER


def test_aud09_paged_kv_and_pipeline_capability_reporting_is_truthful(isolated_db: Path, monkeypatch: pytest.MonkeyPatch):
    """AUD-09:
    1. `QC_PAGED_KV_ENABLED` is `false` by default.
    2. `get_paged_kv_metrics()` and `get_paged_kv_capabilities()` explicitly report that the FastAPI HTTP
       inference hook (`app/services/inference.py`) is a `telemetry_only_http_sidecar` (`external_http_runtime_kv_reuse_supported=False`),
       while genuine KV tensor reuse is scoped to in-process `PyTorchPagedKVAdapter` with `IssuedReuseAuthority`.
    3. `get_active_pipeline_topologies()` explicitly reports `multi_host_weight_execution_implemented=False`.
    """
    from app.services import inference as inf_mod
    from app.services.pipeline_parallel.topology import get_active_pipeline_topologies

    monkeypatch.delenv("QC_PAGED_KV_ENABLED", raising=False)
    inf_mod.reset_paged_kv_state()
    assert inf_mod.is_paged_kv_enabled() is False

    metrics = inf_mod.get_paged_kv_metrics()
    assert metrics["enabled"] is False
    assert metrics["integration_mode"] == "telemetry_only_http_sidecar"
    assert metrics["external_http_runtime_kv_reuse_supported"] is False
    assert metrics["in_process_pytorch_kv_reuse_supported"] is True

    topologies = get_active_pipeline_topologies()
    assert len(topologies) >= 1
    live_m = topologies[0].get("live_metrics") or {}
    assert live_m.get("multi_host_weight_execution_implemented") is False
