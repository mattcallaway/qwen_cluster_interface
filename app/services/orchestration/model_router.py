"""Capability-aware node/model selection and inference transport over the authenticated worker protocol.

The controller never calls a worker's Ollama directly. Inference is a normal worker task
(`task_type=inference`) pinned to a node, leased by that node's authenticated daemon, executed against its
*loopback* Ollama, and returned through the normal authenticated result path.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

from app.database import db_transaction, get_db_connection
from app.services.node_health import effective_health
from app.services.orchestration.common import iso_in, jload, jdump, new_id, now_dt, now_iso, parse_ts

logger = logging.getLogger("qc.orchestrator.router")

WORKER_ROLES = {"vps_worker", "inference_node", "compute_worker", "general_worker"}
MIN_FREE_RAM_FOR_INFERENCE_MB = 350  # 350 MiB accommodates resident quantized models on 4GB hosts with swap
MAX_PROMPT_CHARS = 8000  # matches the worker-side hard cap


# --- node inventory -------------------------------------------------------------------------------------

def node_inventory() -> List[Dict[str, Any]]:
    """Derive per-node capabilities from persisted facts (see docs: declared capabilities are not stored)."""
    conn = get_db_connection()
    try:
        nodes = [dict(r) for r in conn.execute(
            "SELECT * FROM infrastructure_nodes WHERE health_status != 'retired' AND notes NOT LIKE '[RETIRED%'"
        ).fetchall()]
        tokens = {r["node_id"] for r in conn.execute(
            "SELECT DISTINCT node_id FROM worker_auth_tokens WHERE revoked = 0").fetchall()}
        load: Dict[str, int] = {}
        for r in conn.execute(
                """SELECT assigned_worker, COUNT(*) c FROM tasks
                   WHERE status IN ('queued','running','awaiting_approval') AND assigned_worker IS NOT NULL
                   GROUP BY assigned_worker""").fetchall():
            load[r["assigned_worker"]] = r["c"]
    finally:
        conn.close()
    out = []
    for n in nodes:
        runtimes = [str(x).lower() for x in jload(n.get("installed_runtimes_json"), [])]
        models = [m if isinstance(m, str) else str(m.get("name", "")) for m in jload(n.get("available_models_json"), [])]
        health = effective_health(n["role"], n["health_status"], n.get("last_contact_at"))
        caps = set()
        if any(r.startswith("python") for r in runtimes):
            caps.add("python_execution")
        if "ollama" in runtimes and models:
            caps.add("inference")
        out.append({
            "id": n["id"], "name": n["name"], "role": n["role"], "enabled": bool(n["is_enabled"]),
            "health": health, "authenticated": n["id"] in tokens, "is_worker": n["role"] in WORKER_ROLES,
            "models": [m for m in models if m], "runtimes": runtimes, "capabilities": sorted(caps),
            "available_ram_mb": n.get("available_ram_mb"), "last_contact_at": n.get("last_contact_at"),
            "restrictions": jload(n.get("capability_restrictions_json"), {}), "load": load.get(n["id"], 0),
        })
    return out


def _model_matches(requested: str, available: List[str]) -> Optional[str]:
    if requested in available:
        return requested
    base = requested.split(":")[0]
    if ":" not in requested:
        for m in available:
            if m.split(":")[0] == base:
                return m
    return None


def eligible_nodes(task: Dict[str, Any], nodes: List[Dict[str, Any]],
                   require_fresh: bool = True,
                   enforce_verified: bool = True) -> Dict[str, Any]:
    """Return ``{"eligible": [(node, model|None)...], "reasons": [...]}`` for a plan/orchestration task."""
    action = task.get("action") if "action" in task else jload(task.get("action_json"))
    typ = action.get("type")
    allowed = task.get("allowed_nodes")
    if allowed is None:
        allowed = jload(task.get("allowed_nodes_json"), [])
    preferred = task.get("preferred_models")
    if preferred is None:
        preferred = jload(task.get("preferred_models_json"), [])
    wanted_models = [m for m in ([action.get("model")] + list(preferred)) if m]
    eligible: List[Tuple[Dict[str, Any], Optional[str]]] = []
    reasons: List[str] = []

    if typ == "controller_step":
        for n in nodes:
            if n.get("role") == "controller" and n.get("enabled", True):
                return {"eligible": [(n, None)], "reasons": []}
        return {"eligible": [({"id": "controller-local", "role": "controller", "load": 0}, None)], "reasons": []}

    for n in nodes:
        why = None
        if not n["is_worker"]:
            why = "not a worker node"
        elif allowed and n["id"] not in allowed:
            why = "not in allowed_nodes"
        elif not n["enabled"]:
            why = "disabled"
        elif not n["authenticated"]:
            why = "no active worker credential"
        elif require_fresh and n["health"] != "online":
            why = f"health={n['health']} (stale/unavailable)"
        elif typ in (n.get("restrictions") or {}).get("blocked_task_types", []):
            why = "task type restricted on this node"
        model = None
        if why is None:
            if typ == "library_script":
                if "python_execution" not in n["capabilities"]:
                    why = "no python runtime"
            elif typ=='research_fetch':
                restrictions=n.get('restrictions') or {}
                if (restrictions.get('allow_web_fetch') is not True
                        or typ not in restrictions.get('allowed_task_types',[])
                        or restrictions.get('operational_hold',False)):
                    why='web retrieval not explicitly authorized'
            elif typ == "inference":
                if "inference" not in n["capabilities"]:
                    why = "no local inference runtime/models"
                else:
                    cands = wanted_models or n["models"][:1]
                    for m in cands:
                        model = _model_matches(m, n["models"])
                        if model:
                            break
                    if not model:
                        why = f"model not available (wants {cands}, has {n['models']})"
                    elif n.get("available_ram_mb") is not None and n.get("available_ram_mb") < MIN_FREE_RAM_FOR_INFERENCE_MB:
                        why = f"insufficient free RAM ({n.get('available_ram_mb')} MiB)"
                    elif enforce_verified:
                        # Finding F-18: Verify persistent capability probe in database
                        conn = get_db_connection()
                        try:
                            verif = conn.execute(
                                """SELECT result_status FROM infrastructure_verifications
                                   WHERE node_id = ? AND verification_test = 'model_inference_probe'
                                   ORDER BY verified_at DESC LIMIT 1""",
                                (n["id"],)
                            ).fetchone()
                            if not verif or verif["result_status"] != "passed":
                                status_str = verif["result_status"] if verif else "probe_missing"
                                why = (f"unverified model capability on node '{n['id']}' for model '{model}': "
                                       f"status={status_str} (run verify_node_model_capability to verify)")
                        finally:
                            conn.close()
            else:
                why = "unsupported action type"
        if why:
            reasons.append(f"{n['id']}: {why}")
        else:
            eligible.append((n, model))
    return {"eligible": eligible, "reasons": reasons}


def verify_node_model_capability(
    node_id: str,
    model: str,
    verified_by: str = "cluster_health_probe",
    passed: bool = True,
    details: str = "",
) -> Dict[str, Any]:
    """Verify and audit a node's model inference capability in persistent storage (Finding F-18)."""
    conn = get_db_connection()
    try:
        node = conn.execute("SELECT * FROM infrastructure_nodes WHERE id = ?", (node_id,)).fetchone()
        if not node:
            raise ValueError(f"Node '{node_id}' not found.")
    finally:
        conn.close()

    now = now_iso()
    cap_id = f"cap-{node_id}-inference"
    verif_id = new_id("ver")

    with db_transaction() as conn:
        conn.execute(
            """INSERT INTO infrastructure_capabilities (
                id, node_id, name, category, required_software, discovery_status,
                discovery_method, verification_status, verification_method, last_verified_at,
                verification_error, created_at, updated_at
            ) VALUES (?, ?, 'model_inference', 'ai_inference', 'ollama', 'discovered', 'agent_probe',
                      ?, 'health_check', ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                verification_status = excluded.verification_status,
                last_verified_at = excluded.last_verified_at,
                verification_error = excluded.verification_error,
                updated_at = excluded.updated_at""",
            (
                cap_id, node_id, "verified" if passed else "failed", now,
                details if not passed else "", now, now
            )
        )
        conn.execute(
            """INSERT INTO infrastructure_verifications (
                id, capability_id, node_id, verification_test, result_status, details, verified_at, verified_by
            ) VALUES (?, ?, ?, 'model_inference_probe', ?, ?, ?, ?)""",
            (
                verif_id, cap_id, node_id, "passed" if passed else "failed",
                f"Model '{model}': {details or ('Probe passed successfully' if passed else 'Probe failed')}",
                now, verified_by
            )
        )

    logger.info("Node %s model inference capability probe for %s recorded: passed=%s", node_id, model, passed)
    return {
        "verification_id": verif_id,
        "node_id": node_id,
        "model": model,
        "verified": passed,
        "verified_at": now,
        "verified_by": verified_by,
        "details": details
    }


def select_node(task: Dict[str, Any], nodes: List[Dict[str, Any]],
                busy: Optional[Dict[str, int]] = None) -> Tuple[Optional[Dict[str, Any]], Optional[str], List[str]]:
    """Choose the least-loaded eligible node (one in-flight work item per node to protect small hosts)."""
    res = eligible_nodes(task, nodes, enforce_verified=True)
    busy = busy or {}
    free = [(n, m) for n, m in res["eligible"] if busy.get(n["id"], 0) + n.get("load", 0) < 1
            or n.get("role") == "controller"]
    if not free:
        reasons = res["reasons"] or ["all eligible nodes are busy"]
        return None, None, reasons
    free.sort(key=lambda nm: (busy.get(nm[0]["id"], 0) + nm[0].get("load", 0),
                              -(nm[0].get("available_ram_mb") or 0), nm[0]["id"]))
    return free[0][0], free[0][1], []


def model_health() -> List[Dict[str, Any]]:
    """Per-model availability across fresh, authenticated nodes, with recorded performance metrics."""
    conn = get_db_connection()
    try:
        perf = {(r["node_id"], r["model"]): dict(r) for r in conn.execute(
            """SELECT node_id, model, COUNT(*) n, SUM(status='completed') ok,
                      AVG(latency_s) avg_latency_s, AVG(CASE WHEN latency_s > 0 THEN gen_tokens / latency_s END) tps
               FROM model_inference_requests GROUP BY node_id, model""").fetchall()}
    finally:
        conn.close()
    out = []
    for n in node_inventory():
        for m in n["models"]:
            p = perf.get((n["id"], m), {})
            out.append({"node": n["id"], "model": m,
                        "ready": n["enabled"] and n["authenticated"] and n["health"] == "online",
                        "health": n["health"], "authenticated": n["authenticated"],
                        "requests": p.get("n", 0), "successes": p.get("ok") or 0,
                        "avg_latency_s": p.get("avg_latency_s"), "tokens_per_second": p.get("tps")})
    return out


# --- provider interface ----------------------------------------------------------------------------------

class InferenceProvider(ABC):
    """Extension point for additional local or remote providers (cloud credentials never go in prompts)."""
    name = "abstract"

    @abstractmethod
    def submit(self, **kw: Any) -> str: ...

    @abstractmethod
    def poll(self, request_id: str) -> Dict[str, Any]: ...

    @abstractmethod
    def cancel(self, request_id: str) -> None: ...


class WorkerOllamaProvider(InferenceProvider):
    """Inference through an authenticated worker's loopback Ollama (the only provider implemented)."""
    name = "worker_ollama"

    def submit(self, *, run_id: Optional[str], orch_task_id: Optional[str], purpose: str, node_id: str,
               model: str, prompt: str, max_tokens: int = 200, timeout_seconds: int = 300,
               project_ctx: Optional[Dict[str, Any]] = None, attempt_key: Optional[str] = None) -> str:
        from app.services import tasks as T
        if len(prompt) > MAX_PROMPT_CHARS:
            prompt = prompt[:MAX_PROMPT_CHARS]
        inp = {"model": model, "prompt": prompt, "num_predict": int(max_tokens), "label": f"orch:{purpose}",
               "min_ram_mb": MIN_FREE_RAM_FOR_INFERENCE_MB, "timeout_seconds": int(timeout_seconds)}
        title = f"Orchestrator {purpose} inference"
        if project_ctx:
            t = T.create_task(title=title, description=project_ctx.get("description", title), task_type="inference",
                              assigned_worker=node_id, input_data=inp, idempotency_key=attempt_key,
                              project_id=project_ctx["project_id"], path_id=project_ctx["path_id"],
                              stage_id=project_ctx["stage_id"], purpose=project_ctx["purpose"],
                              expected_output=project_ctx["expected_output"],
                              completion_criteria=project_ctx["completion_criteria"])
            if t["status"] == "awaiting_approval":
                T.approve_task(t["id"])
        else:
            t = T.create_task(title=title, description=title, task_type="inference", assigned_worker=node_id,
                              input_data=inp, idempotency_key=attempt_key)
        rid = new_id("inf")
        with db_transaction() as c:
            c.execute("""INSERT INTO model_inference_requests (id, run_id, orch_task_id, purpose, node_id, model,
                         task_id, status, prompt_chars, created_at) VALUES (?,?,?,?,?,?,?,'submitted',?,?)""",
                      (rid, run_id, orch_task_id, purpose, node_id, model, t["id"], len(prompt), now_iso()))
        return rid

    def poll(self, request_id: str, timeout_seconds: int = 300, queue_timeout_seconds: int = 120) -> Dict[str, Any]:
        from app.services import tasks as T
        conn = get_db_connection()
        try:
            req = conn.execute("SELECT * FROM model_inference_requests WHERE id = ?", (request_id,)).fetchone()
        finally:
            conn.close()
        if not req:
            return {"status": "failed", "error_class": "unknown_request"}
        req = dict(req)
        if req["status"] != "submitted":
            return {"status": req["status"], "response": req["response_text"], "error_class": req["error_class"],
                    "gen_tokens": req["gen_tokens"], "prompt_tokens": req["prompt_tokens"],
                    "latency_s": req["latency_s"], "node_id": req["node_id"], "model": req["model"], "task_id": req["task_id"]}
        t = T.get_task_by_id(req["task_id"])
        if not t:
            return self._finish(req, "failed", error_class="network_interruption")
        st = t["status"]
        if st == "completed":
            r = t.get("result_data") or {}
            text = r.get("response")
            if not isinstance(text, str) or not text.strip():
                return self._finish(req, "failed", error_class="invalid_inference_output")
            return self._finish(req, "completed", response=text, gen_tokens=r.get("gen_tokens"),
                                prompt_tokens=r.get("prompt_tokens"),
                                latency_s=r.get("total_duration_s"), model_returned=r.get("model_returned"))
        if st in ("failed", "cancelled"):
            from app.services.orchestration.recovery import classify_task_failure
            return self._finish(req, "cancelled" if st == "cancelled" else "failed",
                                error_class=classify_task_failure(t))
        now = now_dt()
        created = parse_ts(req["created_at"])
        updated = parse_ts(t.get("updated_at")) or created
        if st == "running":
            if updated and (now - updated).total_seconds() > timeout_seconds:
                self.cancel(request_id)
                return self._finish(req, "timeout", error_class="model_timeout", timeout_type="execution_timeout")
        else:
            if created and (now - created).total_seconds() > queue_timeout_seconds:
                self.cancel(request_id)
                return self._finish(req, "timeout", error_class="model_timeout", timeout_type="queue_timeout")
        return {"status": "submitted", "task_status": st, "node_id": req["node_id"], "model": req["model"],
                "task_id": req["task_id"]}

    def cancel(self, request_id: str) -> None:
        from app.services import tasks as T
        conn = get_db_connection()
        try:
            req = conn.execute("SELECT task_id, status FROM model_inference_requests WHERE id = ?",
                               (request_id,)).fetchone()
        finally:
            conn.close()
        if req and req["status"] == "submitted":
            if req["task_id"]:
                try:
                    T.cancel_task(req["task_id"])
                except Exception as e:  # task may already be finished/leased
                    logger.info("cancel_task(%s) not applied: %s", req["task_id"], e)
            with db_transaction() as c:
                c.execute("UPDATE model_inference_requests SET status = 'cancelled', completed_at = ? WHERE id = ? AND status = 'submitted'",
                          (now_iso(), request_id))

    def _finish(self, req: Dict[str, Any], status: str, response: Optional[str] = None, error_class: Optional[str] = None,
                gen_tokens: Optional[int] = None, prompt_tokens: Optional[int] = None,
                latency_s: Optional[float] = None, model_returned: Optional[str] = None,
                timeout_type: Optional[str] = None) -> Dict[str, Any]:
        with db_transaction() as c:
            cur = c.execute("""UPDATE model_inference_requests SET status=?, response_text=?, error_class=?, gen_tokens=?,
                       prompt_tokens=?, latency_s=?, completed_at=? WHERE id=? AND status='submitted'""",
                            (status, response, error_class, gen_tokens, prompt_tokens, latency_s, now_iso(), req["id"]))
            fresh = cur.rowcount == 1
        if fresh and status == "completed":
            from app.services.orchestration.telemetry import record_usage
            if req.get("run_id"):
                record_usage(req["run_id"], inference_requests=1, tokens=(gen_tokens or 0) + (prompt_tokens or 0))
        return {"status": status, "response": response, "error_class": error_class, "timeout_type": timeout_type,
                "gen_tokens": gen_tokens, "prompt_tokens": prompt_tokens, "latency_s": latency_s,
                "node_id": req["node_id"], "model": model_returned or req["model"], "task_id": req["task_id"]}


_provider = WorkerOllamaProvider()


def get_provider() -> WorkerOllamaProvider:
    return _provider
