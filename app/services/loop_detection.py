"""
Loop and Stall Telemetry Detection Service for Qwen Collective (Stage 8C.1).

Distinguishes:
A - Healthy long-running computation with continuing progress milestones.
B - Worker crash or lost heartbeat (stale worker / expired lease).
C - Active process repeatedly making identical tool calls without meaningful state or output changes (unproductive tool loop).
D - Repeated failures and retries without resolution (repeated failure cycle).
E - Legitimate repeated operations (pagination, chunking) with changing inputs or outputs.

Safeguards:
- Strictly diagnostic and alert-only: NEVER kills workers, restarts processes, or cancels tasks.
- SHA-256 fingerprinting of canonicalized arguments and results (no raw credentials, secrets, or full private payloads).
- Bounded in-memory per-task ring buffer (max 20 entries) and LRU task cache.
- False-positive resistance: long-running compute without frequent milestones remains in explicitly uncertain state.
- Warning recovery: tasks immediately clear warnings when meaningful progress resumes.
"""
from __future__ import annotations

import collections
import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger("qwen_collective.loop_detection")

# Configurable detection thresholds (conservative defaults)
LOOP_CONSECUTIVE_THRESHOLD = 5
STALL_PROGRESS_THRESHOLD_S = 600.0  # 10 minutes without meaningful progress
REPEATED_FAILURE_THRESHOLD = 2      # >=2 consecutive failures with same error class
WORKER_ONLINE_THRESHOLD_S = 60.0
WORKER_STALE_THRESHOLD_S = 300.0
MAX_TRACKED_TASKS = 1000
RING_BUFFER_CAPACITY = 20

# Sensitive keys to strip during argument canonicalization
_SENSITIVE_KEY_SUBSTRINGS = (
    "token", "secret", "password", "auth", "nonce", "credential", "private_key", "api_key"
)


class LoopCondition(str, Enum):
    HEALTHY = "HEALTHY"
    LONG_RUNNING_ACTIVE = "LONG_RUNNING_ACTIVE"
    LONG_RUNNING_UNCERTAIN = "LONG_RUNNING_UNCERTAIN"
    STALE_WORKER = "STALE_WORKER"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    UNPRODUCTIVE_TOOL_LOOP = "UNPRODUCTIVE_TOOL_LOOP"
    REPEATED_FAILURE_CYCLE = "REPEATED_FAILURE_CYCLE"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except Exception:
        return None


def canonicalize_payload(data: Any, max_depth: int = 5) -> Any:
    """
    Recursively normalize dictionaries and data structures for deterministic hashing.
    Strips known sensitive and volatile keys (passwords, tokens, credentials).
    """
    if max_depth <= 0:
        return "<depth_limit>"
    if isinstance(data, dict):
        cleaned = {}
        for k in sorted(data.keys()):
            k_str = str(k)
            k_lower = k_str.lower()
            if any(s in k_lower for s in _SENSITIVE_KEY_SUBSTRINGS):
                continue
            cleaned[k_str] = canonicalize_payload(data[k], max_depth - 1)
        return cleaned
    elif isinstance(data, (list, tuple)):
        return [canonicalize_payload(x, max_depth - 1) for x in data]
    elif isinstance(data, (int, float, bool)) or data is None:
        return data
    else:
        # String or other scalar type
        s = str(data)
        if len(s) > 1024:
            # Hash very large strings to preserve deterministic digest without memory bloat
            s = hashlib.sha256(s.encode("utf-8")).hexdigest()
        return s


def compute_tool_fingerprint(tool_name: str, arguments: Any) -> str:
    """Compute deterministic SHA-256 fingerprint for tool identifier and canonical arguments."""
    norm_name = str(tool_name or "").strip().lower()
    canonical_args = canonicalize_payload(arguments)
    serialized = json.dumps(canonical_args, sort_keys=True, separators=(",", ":"))
    raw = f"{norm_name}:{serialized}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def compute_result_fingerprint(result: Any) -> str:
    """Compute deterministic SHA-256 fingerprint of output state or result summary."""
    canonical_res = canonicalize_payload(result)
    serialized = json.dumps(canonical_res, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


@dataclass
class OperationTelemetryRecord:
    task_id: str
    attempt: int
    tool_name: str
    arg_fingerprint: str
    result_fingerprint: str
    ts: str
    graph_node: Optional[str] = None
    error_category: Optional[str] = None
    is_meaningful_progress: bool = False


@dataclass
class TaskTelemetryState:
    task_id: str
    created_at: str
    last_heartbeat_at: Optional[str] = None
    last_progress_at: Optional[str] = None
    progress_counter: int = 0
    current_attempt: int = 1
    current_graph_node: Optional[str] = None
    ring_buffer: Deque[OperationTelemetryRecord] = field(
        default_factory=lambda: collections.deque(maxlen=RING_BUFFER_CAPACITY)
    )
    consecutive_identical_count: int = 0
    last_fingerprint: Optional[str] = None
    last_result_fingerprint: Optional[str] = None
    last_tool_name: Optional[str] = None
    repeated_error_counts: Dict[str, int] = field(default_factory=dict)
    last_error_category: Optional[str] = None


class LoopDetectorManager:
    """Thread-safe telemetry registry and loop detection engine."""

    def __init__(self, max_tasks: int = MAX_TRACKED_TASKS) -> None:
        self._max_tasks = max_tasks
        self._tasks: Dict[str, TaskTelemetryState] = collections.OrderedDict()
        self._lock = threading.Lock()

    def record_heartbeat(self, task_id: str, heartbeat_at: Optional[str] = None) -> None:
        """Update last recorded heartbeat for a task."""
        now_str = heartbeat_at or _now_iso()
        with self._lock:
            state = self._get_or_create_state(task_id, now_str)
            state.last_heartbeat_at = now_str

    def record_operation(
        self,
        task_id: str,
        tool_name: str,
        arguments: Any = None,
        result: Any = None,
        attempt: int = 1,
        graph_node: Optional[str] = None,
        error_category: Optional[str] = None,
        explicit_progress: Optional[bool] = None,
        operation_ts: Optional[str] = None,
    ) -> OperationTelemetryRecord:
        """
        Record a bounded operation event at execution boundaries.
        Evaluates meaningful progress vs repetitive execution.
        """
        now_str = operation_ts or _now_iso()
        arg_fp = compute_tool_fingerprint(tool_name, arguments)
        res_fp = compute_result_fingerprint(result)

        with self._lock:
            state = self._get_or_create_state(task_id, now_str)
            state.current_attempt = attempt
            if graph_node:
                state.current_graph_node = graph_node

            # Evaluate meaningful progress
            is_progress = False
            if error_category:
                # Error encountered: does not constitute forward progress
                state.last_error_category = error_category
                state.repeated_error_counts[error_category] = (
                    state.repeated_error_counts.get(error_category, 0) + 1
                )
                is_progress = False
            elif explicit_progress is True:
                is_progress = True
            elif explicit_progress is False:
                is_progress = False
            else:
                # Inferred progress: changed tool, changed argument fingerprint, or changed result fingerprint
                is_identical = (
                    state.last_fingerprint == arg_fp and
                    state.last_result_fingerprint == res_fp and
                    state.last_tool_name == tool_name
                )
                if is_identical:
                    is_progress = False
                    state.consecutive_identical_count += 1
                else:
                    is_progress = True
                    state.consecutive_identical_count = 1

            if is_progress:
                state.last_progress_at = now_str
                state.progress_counter += 1
                # Meaningful progress clears repetitive error state
                state.repeated_error_counts.clear()
                state.last_error_category = None

            state.last_fingerprint = arg_fp
            state.last_result_fingerprint = res_fp
            state.last_tool_name = tool_name

            record = OperationTelemetryRecord(
                task_id=task_id,
                attempt=attempt,
                tool_name=tool_name,
                arg_fingerprint=arg_fp,
                result_fingerprint=res_fp,
                ts=now_str,
                graph_node=graph_node,
                error_category=error_category,
                is_meaningful_progress=is_progress,
            )
            state.ring_buffer.append(record)
            return record

    def evaluate_task(
        self,
        task_id: str,
        task_status: str,
        duration_seconds: float,
        heartbeat_at: Optional[str] = None,
        lease_expires_at: Optional[str] = None,
        attempt_count: int = 1,
        max_retries: int = 3,
        now_dt: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Evaluate single task execution telemetry and classify into Scenarios A-E.
        Diagnostic and non-destructive.
        """
        now = now_dt or datetime.now(timezone.utc)
        now_str = now.isoformat()

        with self._lock:
            state = self._tasks.get(task_id)
            if state is None:
                # Synthesize state for uninstrumented or newly polled task
                state = TaskTelemetryState(
                    task_id=task_id,
                    created_at=now_str,
                    last_heartbeat_at=heartbeat_at,
                    last_progress_at=now_str if duration_seconds < STALL_PROGRESS_THRESHOLD_S else None,
                    current_attempt=attempt_count,
                )

        hb_str = heartbeat_at or state.last_heartbeat_at
        hb_dt = _parse_iso(hb_str)
        hb_age_s = (now - hb_dt).total_seconds() if hb_dt else None

        lease_exp_dt = _parse_iso(lease_expires_at)
        is_lease_expired = (lease_exp_dt is not None and now > lease_exp_dt)

        last_prog_dt = _parse_iso(state.last_progress_at)
        time_since_prog_s = (now - last_prog_dt).total_seconds() if last_prog_dt else duration_seconds

        # -------------------------------------------------------------------
        # Scenario B: Worker crash or lost heartbeat (Expired Lease / Stale Worker)
        # -------------------------------------------------------------------
        if is_lease_expired or (hb_age_s is not None and hb_age_s > WORKER_STALE_THRESHOLD_S) or (hb_age_s is None and duration_seconds > WORKER_ONLINE_THRESHOLD_S):
            cond = LoopCondition.LEASE_EXPIRED if is_lease_expired else LoopCondition.STALE_WORKER
            return {
                "task_id": task_id,
                "condition": cond.value,
                "severity": "error",
                "heartbeat_status": "expired" if is_lease_expired else "stale",
                "progress_status": "stagnant",
                "heartbeat_age_seconds": round(hb_age_s, 1) if hb_age_s is not None else None,
                "last_heartbeat": hb_str,
                "last_meaningful_progress": state.last_progress_at,
                "time_since_last_progress_seconds": round(time_since_prog_s, 1),
                "repetition_count": state.consecutive_identical_count,
                "current_task": task_id,
                "current_attempt": attempt_count,
                "human_readable_explanation": (
                    f"Worker heartbeat is stale ({round(hb_age_s, 1)}s) or lease has expired."
                    if hb_age_s is not None else "Task lease has expired with no recent heartbeat."
                ),
                "suggested_operator_action": "Check worker process health or allow scheduler lease reclamation.",
                "action_taken": "none_diagnostic_only",
            }

        # Worker is actively heartbeating
        has_active_heartbeat = (hb_age_s is not None and hb_age_s <= WORKER_ONLINE_THRESHOLD_S)

        # -------------------------------------------------------------------
        # Scenario C: Active-heartbeat infinite loop
        # (Consecutive identical operations >= threshold or duration exceeded with stagnant progress)
        # -------------------------------------------------------------------
        is_tool_loop = (
            has_active_heartbeat and
            state.consecutive_identical_count >= LOOP_CONSECUTIVE_THRESHOLD
        )
        is_stagnant_active = (
            has_active_heartbeat and
            duration_seconds > STALL_PROGRESS_THRESHOLD_S and
            time_since_prog_s > STALL_PROGRESS_THRESHOLD_S and
            state.progress_counter == 0 and
            len(state.ring_buffer) > 0
        )

        if is_tool_loop or is_stagnant_active:
            tool_name = state.last_tool_name or "unknown_operation"
            rep_count = state.consecutive_identical_count or len(state.ring_buffer)
            return {
                "task_id": task_id,
                "condition": LoopCondition.UNPRODUCTIVE_TOOL_LOOP.value,
                "severity": "warning",
                "heartbeat_status": "online",
                "progress_status": "looping",
                "heartbeat_age_seconds": round(hb_age_s, 1) if hb_age_s is not None else 0.0,
                "last_heartbeat": hb_str,
                "last_meaningful_progress": state.last_progress_at,
                "time_since_last_progress_seconds": round(time_since_prog_s, 1),
                "repetition_count": rep_count,
                "relevant_fingerprint": state.last_fingerprint,
                "tool_name": tool_name,
                "current_task": task_id,
                "current_attempt": attempt_count,
                "human_readable_explanation": (
                    f"Worker is repeatedly executing identical operations ('{tool_name}') "
                    f"({rep_count} times) without state advancement or output changes despite active heartbeats."
                ),
                "suggested_operator_action": (
                    "Review task instructions; verify worker is not trapped in an unproductive loop. "
                    "Cancel or replan manually if the loop persists."
                ),
                "action_taken": "none_diagnostic_only",
            }

        # -------------------------------------------------------------------
        # Scenario D: Repeated failure cycle
        # (Task failing repeatedly with same error category across retry attempts)
        # -------------------------------------------------------------------
        has_repeated_error = any(count >= REPEATED_FAILURE_THRESHOLD for count in state.repeated_error_counts.values())
        if (attempt_count > 1 and has_repeated_error) or (attempt_count >= max_retries and state.last_error_category):
            err_cat = state.last_error_category or "unknown_error"
            err_count = state.repeated_error_counts.get(err_cat, attempt_count)
            return {
                "task_id": task_id,
                "condition": LoopCondition.REPEATED_FAILURE_CYCLE.value,
                "severity": "warning" if attempt_count < max_retries else "error",
                "heartbeat_status": "online" if has_active_heartbeat else "stale",
                "progress_status": "failing",
                "heartbeat_age_seconds": round(hb_age_s, 1) if hb_age_s is not None else None,
                "last_heartbeat": hb_str,
                "last_meaningful_progress": state.last_progress_at,
                "time_since_last_progress_seconds": round(time_since_prog_s, 1),
                "repetition_count": err_count,
                "error_category": err_cat,
                "current_task": task_id,
                "current_attempt": attempt_count,
                "human_readable_explanation": (
                    f"Task has repeatedly failed with error classification '{err_cat}' "
                    f"across {err_count} attempts without meaningful advancement."
                ),
                "suggested_operator_action": "Investigate recurring failure cause before exhausting remaining retries.",
                "action_taken": "none_diagnostic_only",
            }

        # -------------------------------------------------------------------
        # Scenario A: Healthy long-running computation
        # (Duration > threshold, but active heartbeats AND recent meaningful progress)
        # -------------------------------------------------------------------
        if duration_seconds > STALL_PROGRESS_THRESHOLD_S and has_active_heartbeat:
            has_recent_progress = (time_since_prog_s <= STALL_PROGRESS_THRESHOLD_S or state.progress_counter > 0)
            if has_recent_progress:
                return {
                    "task_id": task_id,
                    "condition": LoopCondition.LONG_RUNNING_ACTIVE.value,
                    "severity": "info",
                    "heartbeat_status": "online",
                    "progress_status": "advancing",
                    "heartbeat_age_seconds": round(hb_age_s, 1),
                    "last_heartbeat": hb_str,
                    "last_meaningful_progress": state.last_progress_at,
                    "time_since_last_progress_seconds": round(time_since_prog_s, 1),
                    "repetition_count": state.consecutive_identical_count,
                    "progress_counter": state.progress_counter,
                    "current_task": task_id,
                    "current_attempt": attempt_count,
                    "human_readable_explanation": (
                        f"Task has run for {round(duration_seconds, 1)}s with active heartbeats and verified continuing progress."
                    ),
                    "suggested_operator_action": "Normal operation. No action required.",
                    "action_taken": "none_diagnostic_only",
                }
            else:
                # Long running without frequent milestones: explicitly uncertain state
                return {
                    "task_id": task_id,
                    "condition": LoopCondition.LONG_RUNNING_UNCERTAIN.value,
                    "severity": "info",
                    "heartbeat_status": "online",
                    "progress_status": "uncertain",
                    "heartbeat_age_seconds": round(hb_age_s, 1),
                    "last_heartbeat": hb_str,
                    "last_meaningful_progress": state.last_progress_at,
                    "time_since_last_progress_seconds": round(time_since_prog_s, 1),
                    "repetition_count": state.consecutive_identical_count,
                    "current_task": task_id,
                    "current_attempt": attempt_count,
                    "human_readable_explanation": (
                        f"Task has run for {round(duration_seconds, 1)}s with active heartbeats but infrequent milestones."
                    ),
                    "suggested_operator_action": "Monitor progress; allow computation to continue without interruption.",
                    "action_taken": "none_diagnostic_only",
                }

        # -------------------------------------------------------------------
        # Scenario E: Legitimate repetitive operations or standard healthy work
        # (Pagination, chunked processing, or standard execution with varying inputs/progress)
        # -------------------------------------------------------------------
        return {
            "task_id": task_id,
            "condition": LoopCondition.HEALTHY.value,
            "severity": "normal",
            "heartbeat_status": "online" if has_active_heartbeat else "unknown",
            "progress_status": "advancing",
            "heartbeat_age_seconds": round(hb_age_s, 1) if hb_age_s is not None else None,
            "last_heartbeat": hb_str,
            "last_meaningful_progress": state.last_progress_at,
            "time_since_last_progress_seconds": round(time_since_prog_s, 1),
            "repetition_count": state.consecutive_identical_count,
            "progress_counter": state.progress_counter,
            "current_task": task_id,
            "current_attempt": attempt_count,
            "human_readable_explanation": "Task is actively computing with normal execution and verified progress.",
            "suggested_operator_action": "Normal operation. No action required.",
            "action_taken": "none_diagnostic_only",
        }

    def _get_or_create_state(self, task_id: str, now_str: str) -> TaskTelemetryState:
        """Helper to get or initialize task state with LRU capacity pruning."""
        if task_id in self._tasks:
            self._tasks.move_to_end(task_id)
            return self._tasks[task_id]

        if len(self._tasks) >= self._max_tasks:
            # Evict oldest entry to bound memory
            self._tasks.popitem(last=False)

        state = TaskTelemetryState(
            task_id=task_id,
            created_at=now_str,
            last_heartbeat_at=now_str,
            last_progress_at=now_str,
        )
        self._tasks[task_id] = state
        return state

    def clear(self) -> None:
        """Clear all in-memory telemetry states (used in test resets)."""
        with self._lock:
            self._tasks.clear()

    def get_state(self, task_id: str) -> Optional[TaskTelemetryState]:
        """Retrieve state snapshot for testing and verification."""
        with self._lock:
            return self._tasks.get(task_id)


# Global singleton detector instance
detector = LoopDetectorManager()
