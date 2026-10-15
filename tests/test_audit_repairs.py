"""Regression tests for the three-node audit repairs (unit level; NOT live acceptance evidence)."""
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.database import get_db_connection
from app.services import infrastructure as infra_svc
from app.services.node_health import HEARTBEAT_STALE_SECONDS, effective_health
from vps_agent import executor


def _iso(seconds_ago):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


# ---------------------------------------------------------------- heartbeat staleness
@pytest.mark.parametrize("role,recorded,age,expected", [
    ("vps_worker", "online", 5, "online"),
    ("inference_node", "online", HEARTBEAT_STALE_SECONDS + 5, "offline"),   # dead worker demoted
    ("inference_node", "offline", 1, "offline"),                            # never promoted
    ("vps_worker", "unknown", 1, "unknown"),
    ("controller", "online", 99999, "online"),                              # controller exempt
])
def test_effective_health_rules(role, recorded, age, expected):
    assert effective_health(role, recorded, _iso(age)) == expected


def test_no_contact_timestamp_left_as_recorded():
    assert effective_health("vps_worker", "online", None) == "online"


def test_registry_demotes_stale_worker_and_keeps_recorded_value():
    with get_db_connection() as conn:
        conn.execute("DELETE FROM infrastructure_nodes WHERE id IN ('t-stale','t-fresh')")
        conn.commit()
    infra_svc.register_or_update_node("t-stale", "Stale", "inference_node", health_status="online")
    infra_svc.register_or_update_node("t-fresh", "Fresh", "inference_node", health_status="online")
    with get_db_connection() as conn:
        conn.execute("UPDATE infrastructure_nodes SET last_contact_at=? WHERE id='t-stale'", (_iso(600),))
        conn.execute("UPDATE infrastructure_nodes SET last_contact_at=? WHERE id='t-fresh'", (_iso(3),))
        conn.commit()
    stale, fresh = infra_svc.get_node("t-stale"), infra_svc.get_node("t-fresh")
    assert stale["health_status"] == "offline" and stale["health_status_recorded"] == "online"
    assert fresh["health_status"] == "online"
    assert {n["id"]: n["health_status"] for n in infra_svc.list_nodes()}["t-stale"] == "offline"
    with get_db_connection() as conn:
        conn.execute("DELETE FROM infrastructure_nodes WHERE id IN ('t-stale','t-fresh')")
        conn.commit()


# ---------------------------------------------------------------- inference handler
class _FakeOllama(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, obj):
        b = json.dumps(obj).encode(); self.send_response(200)
        self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        self._send({"models": [{"name": "qwen2.5:1.5b"}]})
    def do_POST(self):
        n = int(self.headers["Content-Length"]); req = json.loads(self.rfile.read(n))
        _FakeOllama.last = req
        self._send({"model": req["model"], "response": "READY 42", "eval_count": 4, "eval_duration": 4_000_000_000,
                    "prompt_eval_count": 12, "load_duration": 1_000_000_000, "total_duration": 6_000_000_000})


@pytest.fixture()
def fake_ollama(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), _FakeOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("QC_OLLAMA_URL", f"http://127.0.0.1:{srv.server_port}")
    yield srv
    srv.shutdown()


def _task(**inp):
    return {"task_id": "QC_TEST_1", "task_type": "inference", "input_data": inp}


def test_inference_success_returns_engine_metrics(fake_ollama):
    r = executor.execute_task(_task(model="qwen2.5:1.5b", prompt="hi", num_predict=9999))
    assert r["status"] == "success"
    p = r["result_payload"]
    assert p["response"] == "READY 42" and p["model_returned"] == "qwen2.5:1.5b" and p["tokens_per_second"] == 1.0
    assert _FakeOllama.last["options"]["num_predict"] == executor.MAX_NUM_PREDICT  # clamped


def test_inference_rejects_uninstalled_model_without_pulling(fake_ollama):
    r = executor.execute_task(_task(model="llama3.2:1b", prompt="hi"))
    assert r["status"] == "error" and r["error_category"] == "model_unavailable"


def test_inference_rejects_non_loopback_endpoint(monkeypatch):
    monkeypatch.setenv("QC_OLLAMA_URL", "http://100.101.187.115:11434")
    r = executor.execute_task(_task(model="qwen2.5:1.5b", prompt="hi"))
    assert r["error_category"] == "unsafe_endpoint"


@pytest.mark.parametrize("inp", [{}, {"model": "x"}, {"prompt": "x"}, {"model": "x", "prompt": "y" * 9000}])
def test_inference_validation(inp, fake_ollama):
    assert executor.execute_task(_task(**inp))["error_category"] == "validation_error"


def test_inference_unreachable_fails_closed(monkeypatch):
    monkeypatch.setenv("QC_OLLAMA_URL", "http://127.0.0.1:1")
    assert executor.execute_task(_task(model="m", prompt="p"))["error_category"] == "inference_unreachable"


# ---------------------------------------------------------------- RAM constraint fail-closed (audit finding F4)
@pytest.fixture
def _cleanup_ram_tasks():
    yield
    with get_db_connection() as conn:
        conn.execute("UPDATE tasks SET status='cancelled' WHERE title='ram-test'")
        conn.commit()


def _insert_task(tid, input_json):
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO tasks (id, title, task_type, status, approval_required, input_data_json, created_at, updated_at) "
            "VALUES (?, 'ram-test', 'general', 'queued', 0, ?, '2026-10-15T00:00:00Z', '2026-10-15T00:00:00Z')",
            (tid, input_json),
        )
        conn.commit()


def test_ram_constraint_uses_recorded_heartbeat_ram_when_worker_omits_it(_cleanup_ram_tasks):
    """A worker that does not self-report RAM in lease polls must be judged by its last authenticated heartbeat RAM."""
    from app.services import worker as worker_svc
    with get_db_connection() as conn:
        conn.execute("UPDATE tasks SET status='cancelled' WHERE title='ram-test'")
        conn.commit()
    small, big = "t-ram-small", "t-ram-big"
    infra_svc.register_or_update_node(small, "Small", "vps_worker", health_status="online", available_ram_mb=700)
    infra_svc.register_or_update_node(big, "Big", "inference_node", health_status="online", available_ram_mb=2500)
    _insert_task("t-ram-task-1500", '{"min_ram_mb": 1500}')
    assert worker_svc.lease_task_for_worker(worker_id=small, supported_categories=["general"]) is None
    lease = worker_svc.lease_task_for_worker(worker_id=big, supported_categories=["general"])
    assert lease is not None and lease["task_id"] == "t-ram-task-1500"


def test_ram_constraint_fails_closed_when_ram_unknown(_cleanup_ram_tasks):
    from app.services import worker as worker_svc
    with get_db_connection() as conn:
        conn.execute("UPDATE tasks SET status='cancelled' WHERE title='ram-test'")
        conn.commit()
    infra_svc.register_or_update_node("t-ram-unknown", "Unknown", "vps_worker", health_status="online")
    with get_db_connection() as conn:
        conn.execute("UPDATE infrastructure_nodes SET available_ram_mb=NULL WHERE id='t-ram-unknown'")
        conn.commit()
    _insert_task("t-ram-task-unknown", '{"min_ram_mb": 512}')
    assert worker_svc.lease_task_for_worker(worker_id="t-ram-unknown", supported_categories=["general"]) is None
