"""Deterministic acceptance evaluation. "Process exited 0" is NOT acceptance; the output must pass checks.

AI review is never used here as an authority on facts. Model output is validated structurally and labelled.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings
from app.database import get_db_connection
from app.services.orchestration import script_library
from app.services.orchestration.common import jload
from app.services.orchestration.task_graph import scan_text_for_injection

_ENVELOPE = {"execution_class", "isolation_mechanism", "stdout", "stderr", "returncode", "artifacts", "exit_code"}


def _check(name: str, passed: bool, detail: str = "") -> Dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail[:300]}


def extract_payload(result_data: Dict[str, Any]) -> Dict[str, Any]:
    """The structured JSON a script printed (merged into the result by the worker)."""
    if not isinstance(result_data, dict):
        return {}
    if "schema" in result_data:
        return {k: v for k, v in result_data.items() if k not in _ENVELOPE}
    for line in str(result_data.get("stdout", "")).splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                d = json.loads(line)
                if isinstance(d, dict):
                    return d
            except ValueError:
                pass
    return {}


def executing_node(task_id: str) -> Optional[str]:
    """Which authenticated worker actually held the (completed) lease for this task."""
    c = get_db_connection()
    try:
        row = c.execute("""SELECT worker_id FROM worker_task_leases WHERE task_id = ?
                           ORDER BY (status='completed') DESC, leased_at DESC LIMIT 1""", (task_id,)).fetchone()
        return row["worker_id"] if row else None
    finally:
        c.close()


def evaluate_script_task(orch: Dict[str, Any], task: Dict[str, Any]) -> Dict[str, Any]:
    action = jload(orch["action_json"])
    script_id = action.get("script_id")
    rd = task.get("result_data") or {}
    checks: List[Dict[str, Any]] = []
    checks.append(_check("task_completed", task["status"] == "completed", f"status={task['status']}"))
    checks.append(_check("exit_code_zero", rd.get("returncode", 0) == 0, f"returncode={rd.get('returncode')}"))
    node = executing_node(task["id"])
    checks.append(_check("executed_on_expected_node", node is not None and node == orch["dispatched_node"],
                         f"leased_by={node} expected={orch['dispatched_node']}"))
    payload = extract_payload(rd)
    checks.append(_check("structured_output_present", bool(payload), "JSON result parsed" if payload else "no JSON found"))
    ok, problems = (False, ["no payload"])
    if payload and script_id in script_library.LIBRARY:
        ok, problems = script_library.validator_for(script_id)(payload)
    checks.append(_check("schema_valid", ok, "; ".join(problems)))
    # Artifact corroboration: result.json stored by the worker pipeline must exist and match
    art = Path(settings.evidence_dir) / f"task_{task['id']}" / "result.json"
    art_ok = False
    if art.is_file():
        try:
            art_ok = json.loads(art.read_text("utf-8")).get("schema") == payload.get("schema")
        except (ValueError, OSError):
            art_ok = False
    checks.append(_check("artifact_result_json_matches", art_ok, str(art.name)))
    accepted = all(c["passed"] for c in checks)
    return {"accepted": accepted, "checks": checks, "node": node,
            "result": {"kind": "worker_telemetry", "node": node, "schema": payload.get("schema"), "data": payload}
            if accepted else {}}


def evaluate_inference(orch: Dict[str, Any], req: Dict[str, Any], prompt: str) -> Dict[str, Any]:
    acc = jload(orch["acceptance_json"])
    text = (req.get("response") or "").strip()
    checks = [
        _check("inference_completed", req.get("status") == "completed", f"status={req.get('status')}"),
        _check("non_empty", bool(text)),
        _check("min_length", len(text) >= int(acc.get("min_chars", 20)), f"{len(text)} chars"),
        _check("not_prompt_echo", text[:200] != prompt.strip()[:200]),
        _check("executed_on_expected_node", req.get("node_id") == orch["dispatched_node"],
               f"node={req.get('node_id')} expected={orch['dispatched_node']}"),
    ]
    suspicious = scan_text_for_injection(text)
    accepted = all(c["passed"] for c in checks)
    return {"accepted": accepted, "checks": checks, "node": req.get("node_id"),
            "suspicious_output": bool(suspicious),
            "result": {"kind": "model_output", "label": "AI-generated, unverified", "text": text[:6000],
                       "model": req.get("model"), "node": req.get("node_id"), "gen_tokens": req.get("gen_tokens"),
                       "prompt_tokens": req.get("prompt_tokens"), "latency_s": req.get("latency_s"),
                       "flagged_instruction_like_content": bool(suspicious)} if accepted else {}}


def evaluate_report(md: str, required_sections: List[str]) -> Dict[str, Any]:
    missing = [s for s in required_sections if s not in md]
    checks = [_check("sections_present", not missing, f"missing={missing}"),
              _check("labels_present", "Measured" in md and "AI-generated" in md,
                     "telemetry and AI content must be labelled separately")]
    return {"accepted": all(c["passed"] for c in checks), "checks": checks}
