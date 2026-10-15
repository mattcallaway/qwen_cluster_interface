"""Failure classification, bounded retry policy and deterministic recovery diagnosis."""
from __future__ import annotations

from typing import Any, Dict, List

FAILURE_CLASSES = (
    "worker_unavailable", "auth_failure", "unsupported_capability", "model_timeout", "model_oom",
    "invalid_inference_output", "task_execution_failure", "sandbox_rejection", "dependency_failure",
    "artifact_validation_failure", "network_interruption", "controller_restart",
)

# Classes where retrying the same thing cannot help: escalate to a human immediately.
NON_RETRYABLE = {"auth_failure", "unsupported_capability", "sandbox_rejection", "dependency_failure"}
BACKOFF_BASE_SECONDS = 20
BACKOFF_CAP_SECONDS = 600


def backoff_seconds(attempt_number: int) -> int:
    """Exponential backoff after the Nth failed attempt (1 -> 20 s, 2 -> 40 s, ... capped)."""
    return min(BACKOFF_BASE_SECONDS * (2 ** max(attempt_number - 1, 0)), BACKOFF_CAP_SECONDS)


def classify_task_failure(task: Dict[str, Any]) -> str:
    """Classify a failed/cancelled underlying task from its recorded error and result payload."""
    text = " ".join(str(x) for x in (task.get("error_details"), (task.get("result_data") or {}).get("stderr"),
                                     (task.get("result_data") or {}).get("error"),
                                     (task.get("result_data") or {}).get("error_category"))
                    if x).lower()
    if any(k in text for k in ("out of memory", "oom", "cannot allocate", "memory allocation", "killed")):
        return "model_oom" if task.get("task_type") == "inference" else "task_execution_failure"
    if "timeout" in text or "timed out" in text or "exceeded execution" in text:
        return "model_timeout" if task.get("task_type") == "inference" else "task_execution_failure"
    if any(k in text for k in ("manifest", "sandbox", "signature", "allowlist", "not permitted", "rejected")):
        return "sandbox_rejection"
    if any(k in text for k in ("401", "403", "unauthor", "forbidden", "token")):
        return "auth_failure"
    if any(k in text for k in ("connection", "unreachable", "refused", "network", "econn")):
        return "network_interruption"
    if any(k in text for k in ("lease expired", "expired", "worker offline", "no heartbeat")):
        return "worker_unavailable"
    if task.get("task_type") == "inference" and not (task.get("result_data") or {}).get("response"):
        return "invalid_inference_output"
    return "task_execution_failure"


def decide(failure_class: str, attempts_made: int, max_attempts: int, idempotent: bool,
           retries_used: int, retries_budget: int) -> Dict[str, Any]:
    """Return ``{action: retry|block, delay_seconds, reason}``; never an unbounded retry."""
    if failure_class in NON_RETRYABLE:
        return {"action": "block", "delay_seconds": 0, "reason": f"{failure_class} cannot be fixed by retrying"}
    if not idempotent:
        return {"action": "block", "delay_seconds": 0, "reason": "non-idempotent action is never retried automatically"}
    if attempts_made >= max_attempts:
        return {"action": "block", "delay_seconds": 0,
                "reason": f"max attempts reached ({attempts_made}/{max_attempts})"}
    if retries_used >= retries_budget:
        return {"action": "block", "delay_seconds": 0,
                "reason": f"project retry budget exhausted ({retries_used}/{retries_budget})"}
    if failure_class == "model_oom" and attempts_made >= 2:
        return {"action": "block", "delay_seconds": 0, "reason": "repeated out-of-memory on the model host"}
    return {"action": "retry", "delay_seconds": backoff_seconds(attempts_made),
            "reason": f"{failure_class}; retrying with backoff"}


def diagnose(failure_class: str, last_error: str) -> Dict[str, Any]:
    """Recovery Agent (deterministic rules): propose corrective actions; a human decides."""
    advice: Dict[str, List[str]] = {
        "worker_unavailable": ["Check the node is online (heartbeat) and its worker service is running.",
                               "Restrict allowed_nodes to another compatible node, or retry later."],
        "auth_failure": ["Re-enroll or rotate the worker credential for this node."],
        "unsupported_capability": ["Install the required runtime/model on a node, or edit the task's requirements."],
        "model_timeout": ["Retry with a shorter prompt/lower max_tokens, or a smaller model; check laptop memory pressure."],
        "model_oom": ["Free memory on the inference node or select a smaller model; avoid concurrent inference."],
        "invalid_inference_output": ["Retry; if repeated, simplify the instruction or use a stronger model."],
        "task_execution_failure": ["Inspect the task's stderr in the project's history and edit or replace the task."],
        "sandbox_rejection": ["The sandbox refused this task; review its permissions before any change."],
        "dependency_failure": ["Resolve the failed prerequisite task first."],
        "artifact_validation_failure": ["Review the produced artifact against acceptance criteria; retry or edit the task."],
        "network_interruption": ["Verify Tailscale connectivity between controller and worker, then retry."],
        "controller_restart": ["The task was in flight during a restart; it will be reconciled automatically."],
    }
    return {"failure_class": failure_class, "last_error": (last_error or "")[:500],
            "proposed_actions": advice.get(failure_class, ["Review the task and decide manually."])}
