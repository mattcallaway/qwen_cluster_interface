"""Resource budgets and usage accounting for orchestration runs."""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from app.database import db_transaction, get_db_connection
from app.services.orchestration.common import now_iso

LIMIT_FIELDS = ("max_tasks", "max_concurrent", "max_inference_requests", "max_tokens", "max_runtime_seconds",
                "max_retries_total", "max_storage_bytes")
HARD_CAPS = {"max_tasks": 200, "max_concurrent": 4, "max_inference_requests": 500, "max_tokens": 1_000_000,
             "max_runtime_seconds": 86400, "max_retries_total": 100, "max_storage_bytes": 2 * 1024 ** 3}


def ensure_budget(conn, run_id: str) -> None:
    conn.execute("INSERT OR IGNORE INTO resource_budgets (run_id, updated_at) VALUES (?, ?)", (run_id, now_iso()))


def get_budget(run_id: str) -> Dict[str, Any]:
    with db_transaction() as c:
        ensure_budget(c, run_id)
    conn = get_db_connection()
    try:
        return dict(conn.execute("SELECT * FROM resource_budgets WHERE run_id = ?", (run_id,)).fetchone())
    finally:
        conn.close()


def set_limits(run_id: str, limits: Dict[str, Any]) -> Dict[str, Any]:
    """Change limits (callers must have obtained explicit authorisation; capped by absolute hard limits)."""
    clean = {}
    for k, v in limits.items():
        if k in LIMIT_FIELDS and isinstance(v, int) and v >= 1:
            clean[k] = min(v, HARD_CAPS[k])
    if clean:
        with db_transaction() as c:
            ensure_budget(c, run_id)
            sets = ", ".join(f"{k} = ?" for k in clean)
            c.execute(f"UPDATE resource_budgets SET {sets}, updated_at = ? WHERE run_id = ?",
                      (*clean.values(), now_iso(), run_id))
    return get_budget(run_id)


def record_usage(run_id: str, tasks: int = 0, inference_requests: int = 0, tokens: int = 0, retries: int = 0,
                 storage_bytes: int = 0, runtime_seconds: float = 0.0) -> None:
    with db_transaction() as c:
        ensure_budget(c, run_id)
        c.execute("""UPDATE resource_budgets SET used_tasks = used_tasks + ?, used_inference_requests = used_inference_requests + ?,
                     used_tokens = used_tokens + ?, used_retries = used_retries + ?, used_storage_bytes = used_storage_bytes + ?,
                     used_runtime_seconds = used_runtime_seconds + ?, updated_at = ? WHERE run_id = ?""",
                  (tasks, inference_requests, tokens, retries, storage_bytes, runtime_seconds, now_iso(), run_id))


def check_budget(run_id: str, will_add_task: bool = False, will_add_inference: bool = False,
                 elapsed_runtime_seconds: Optional[float] = None) -> Tuple[bool, str]:
    b = get_budget(run_id)
    if will_add_task and b["used_tasks"] >= b["max_tasks"]:
        return False, f"task budget exhausted ({b['used_tasks']}/{b['max_tasks']})"
    if will_add_inference and b["used_inference_requests"] >= b["max_inference_requests"]:
        return False, f"inference-request budget exhausted ({b['used_inference_requests']}/{b['max_inference_requests']})"
    if b["used_tokens"] >= b["max_tokens"]:
        return False, f"token budget exhausted ({b['used_tokens']}/{b['max_tokens']})"
    if b["used_retries"] > b["max_retries_total"]:
        return False, f"retry budget exhausted ({b['used_retries']}/{b['max_retries_total']})"
    if b["used_storage_bytes"] > b["max_storage_bytes"]:
        return False, "storage budget exhausted"
    rt = elapsed_runtime_seconds if elapsed_runtime_seconds is not None else b["used_runtime_seconds"]
    if rt > b["max_runtime_seconds"]:
        return False, f"runtime budget exhausted ({int(rt)}s/{b['max_runtime_seconds']}s)"
    return True, ""
