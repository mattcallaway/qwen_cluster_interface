"""Mock-worker harness for orchestrator tests (uses the real lease/result services; no network)."""
import base64
import hashlib
import json
import uuid
from datetime import datetime, timezone

from app.database import db_transaction, get_db_connection
from app.services import worker as W

HEALTH = {
    "schema": "qc.health.v1", "kernel": "6.8.0-test", "os": "TestOS 1", "arch": "x86_64", "cpu_count": 2,
    "loadavg": [0.1, 0.2, 0.3], "mem_total_mib": 1000, "mem_available_mib": 600, "swap_total_mib": 512,
    "swap_free_mib": 500, "uptime_seconds": 1234.5, "disk_root_total_gib": 20.0, "disk_root_free_gib": 10.0,
    "python": "3.12.0", "machine_id_sha16": "abcd1234abcd1234", "collected_utc": "2026-01-01T00:00:00+00:00",
}


def now():
    return datetime.now(timezone.utc).isoformat()


def register_node(node_id, role="vps_worker", models=None, runtimes=None, token=True, health="online", ram=2000, enabled=1):
    runtimes = runtimes if runtimes is not None else (["python-3.12", "ollama"] if models else ["python-3.12"])
    with db_transaction() as c:
        c.execute("DELETE FROM worker_auth_tokens WHERE node_id=?", (node_id,))
        c.execute("DELETE FROM infrastructure_nodes WHERE id=? AND id NOT IN (SELECT DISTINCT worker_id FROM worker_task_leases)", (node_id,))
        c.execute("""INSERT OR REPLACE INTO infrastructure_nodes (id, name, role, installed_runtimes_json, available_models_json,
                     health_status, last_contact_at, available_ram_mb, is_enabled, created_at, updated_at)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                  (node_id, node_id, role, json.dumps(runtimes), json.dumps(models or []), health, now(), ram, enabled, now(), now()))
        if token:
            c.execute("INSERT INTO worker_auth_tokens (id, node_id, token_hash, token_prefix, created_at, revoked) VALUES (?,?,?,?,?,0)",
                      (uuid.uuid4().hex, node_id, uuid.uuid4().hex, "tst", now()))


def set_health(node_id, health="online", age_seconds=0):
    from datetime import timedelta
    ts = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
    with db_transaction() as c:
        c.execute("UPDATE infrastructure_nodes SET health_status=?, last_contact_at=? WHERE id=?", (health, ts, node_id))


def disable_other_nodes(keep):
    with db_transaction() as c:
        c.execute(f"UPDATE infrastructure_nodes SET is_enabled=0 WHERE id NOT IN ({','.join('?' * len(keep))})", list(keep))
        for k in keep:
            c.execute("UPDATE infrastructure_nodes SET is_enabled=1 WHERE id=?", (k,))


def _art(name, data: bytes):
    sha = hashlib.sha256(data).hexdigest()
    return {"filename": name, "file_size": len(data), "sha256": sha, "expected_sha256": sha,
            "content_base64": base64.b64encode(data).decode()}


def worker_step(node_id, models=None, mode="ok", response="Node A has 600 MiB free memory and low load. Node B is similar.",
                health=None):
    """Lease one task as ``node_id`` and submit a result. Returns the lease dict or None."""
    lease = W.lease_task_for_worker(node_id, supported_categories=["python_execution", "inference", "general"],
                                    available_models=models or [], available_ram_mb=2000)
    if not lease:
        return None
    tt = lease["task_type"]
    if mode == "never":
        return lease
    if mode == "fail":
        W.submit_task_result(node_id, lease["task_id"], lease["lease_id"], lease["idempotency_key"], "error", exit_code=1,
                             stderr_summary="boom: execution failed", error_category="runtime_error",
                             result_payload={"execution_class": "Class B", "returncode": 1})
        return lease
    if tt == "inference":
        payload = {"execution_class": "Inference", "engine": "ollama", "model_requested": lease["input_data"]["model"],
                   "model_returned": lease["input_data"]["model"], "response": response, "gen_tokens": 40, "prompt_tokens": 100,
                   "total_duration_s": 3.2}
        W.submit_task_result(node_id, lease["task_id"], lease["lease_id"], lease["idempotency_key"], "success",
                             runtime_seconds=3.2, result_payload=payload)
        return lease
    data = dict(health or HEALTH)
    payload = {"execution_class": "Class B", "returncode": 0, "stdout": json.dumps(data), "stderr": "", **data}
    payload["artifacts"] = [_art("result.json", json.dumps(data).encode())]
    W.submit_task_result(node_id, lease["task_id"], lease["lease_id"], lease["idempotency_key"], "success",
                         runtime_seconds=0.5, result_payload=payload, artifacts=payload["artifacts"])
    return lease
