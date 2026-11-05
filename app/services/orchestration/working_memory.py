"""Live Database-Backed Working Memory for Qwen Collective (Increment 18 / Stage 1-4).

Implements a standing operational source of project intent, progress, evidence,
decisions, task contracts, and next actions across three coordinated levels:
  - Level A: Resident working context (bounded in-RAM WorkingSetCache & model prompt packet
             with pinned intent/constraints that are never silently truncated).
  - Level B: Database-backed working state (SQLite WAL tables: project_requirement_items,
             project_requirement_revisions, project_task_contracts, project_epistemic_items,
             project_epistemic_fts, project_working_checkpoints, project_artifact_publication_journal).
  - Level C: Detailed artifacts in Content-Addressable Storage (CAS) paged into working
             context by exact artifact ID, version number, and SHA-256 digest.

Note: This is paging of logical project knowledge into task context. It does not replace
model attention/KV memory or claim distributed inference capability.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from app.database import db_transaction, get_db_connection
from app.services.orchestration import (
    artifact_manager,
    context_manager,
    deliberate_loop,
    intake as intake_mod,
    telemetry,
)
from app.services.orchestration.common import get_run_for_project, jdump, jload, new_id, now_iso

logger = logging.getLogger("qwen_collective.working_memory")

EPISTEMIC_CATEGORIES: Tuple[str, ...] = (
    "user_instruction_or_decision",
    "source_observation",
    "supported_claim",
    "derived_result",
    "hypothesis_or_estimate",
    "model_proposal",
    "verified_artifact",
    "verification_failure",
)

VALID_VERIFICATION_STATUSES: Tuple[str, ...] = (
    "unknown",
    "proposed",
    "observed",
    "supported",
    "verified_for_scope",
    "partial",
    "blocked",
    "failed",
    "stale",
    "superseded",
)

MAX_INTERMEDIATE_BUFFER_BYTES = 262144  # 256 KiB bounded intermediate result buffer

CLEANUP_LIFECYCLE_STATES: Tuple[str, ...] = (
    "none",
    "requested",
    "dispatch_stopped",
    "draining",
    "cleanup_confirmed",
    "cleanup_unknown",
    "cleanup_failed",
)

_PROCESS_REGISTRY_LOCK = threading.RLock()
_ACTIVE_PROCESS_REGISTRY: Dict[Tuple[str, str], Dict[str, Any]] = {}


def _controller_verification_key() -> bytes:
    from app.config import settings
    base_secret = str(getattr(settings, "secret_key", "qc-default-controller-secret"))
    return hashlib.sha256(f"qc-trusted-verifier-v1:{base_secret}".encode("utf-8")).digest()


def issue_trusted_verification_receipt(
    *,
    project_id: str,
    task_key: str,
    requirement_revision: int,
    contract_revision: int,
    verification_method: str,
    declared_scope: str,
    checks_executed: List[Dict[str, Any]],
    artifact_id: Optional[str] = None,
    artifact_version: Optional[int] = None,
    artifact_sha256: Optional[str] = None,
    verified_status: str = "verified_for_scope",
) -> Dict[str, Any]:
    """Issue a controller-signed TrustedVerificationReceipt bound to exact revisions and artifact digest."""
    import hmac as _hmac

    if not verification_method or not str(verification_method).strip():
        raise ValueError("verification_method is required for a trusted verification receipt")
    if not declared_scope or not str(declared_scope).strip():
        raise ValueError("declared_scope is required for a trusted verification receipt")
    if not isinstance(checks_executed, list) or len(checks_executed) == 0:
        raise ValueError("checks_executed must be a non-empty list of executed check results")

    all_passed = all(isinstance(c, dict) and c.get("passed") is True for c in checks_executed)
    has_conflict = any(isinstance(c, dict) and bool(c.get("conflict")) for c in checks_executed)
    overall_passed = bool(all_passed and not has_conflict)

    payload = {
        "schema": "qc.trusted_verification_receipt.v1",
        "project_id": str(project_id),
        "task_key": str(task_key),
        "requirement_revision": int(requirement_revision),
        "contract_revision": int(contract_revision),
        "artifact_id": str(artifact_id) if artifact_id else None,
        "artifact_version": int(artifact_version) if artifact_version is not None else (1 if artifact_id else None),
        "artifact_sha256": str(artifact_sha256) if artifact_sha256 else None,
        "verification_method": str(verification_method).strip(),
        "declared_scope": str(declared_scope).strip(),
        "checks_executed": checks_executed,
        "overall_passed": overall_passed,
        "verified_status": verified_status if overall_passed else "failed",
        "issued_at": now_iso(),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sig = _hmac.new(_controller_verification_key(), canonical, hashlib.sha256).hexdigest()
    return {**payload, "signature": sig}


def verify_trusted_receipt(
    receipt: Optional[Dict[str, Any]],
    *,
    project_id: str,
    task_key: str,
    current_requirement_revision: int,
    current_contract_revision: int,
    actual_artifact_id: Optional[str] = None,
    actual_artifact_version: Optional[int] = None,
    actual_artifact_sha256: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate that a verification receipt is controller-signed, non-stale, and matches exact artifact hashes."""
    import hmac as _hmac

    if not isinstance(receipt, dict):
        return {"valid": False, "reason": "missing_receipt"}
    sig = str(receipt.get("signature") or "")
    if not sig:
        return {"valid": False, "reason": "unsigned_receipt"}

    unsigned = {k: v for k, v in receipt.items() if k != "signature"}
    canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    expected_sig = _hmac.new(_controller_verification_key(), canonical, hashlib.sha256).hexdigest()
    if not _hmac.compare_digest(sig, expected_sig):
        return {"valid": False, "reason": "forged_or_tampered_signature"}

    if str(receipt.get("project_id")) != str(project_id):
        return {"valid": False, "reason": "project_id_mismatch"}
    if str(receipt.get("task_key")) != str(task_key):
        return {"valid": False, "reason": "task_key_mismatch"}
    if int(receipt.get("requirement_revision", -1)) != int(current_requirement_revision):
        return {
            "valid": False,
            "reason": f"stale_requirement_revision:{receipt.get('requirement_revision')}!={current_requirement_revision}",
        }
    if int(receipt.get("contract_revision", -1)) != int(current_contract_revision):
        return {
            "valid": False,
            "reason": f"stale_contract_revision:{receipt.get('contract_revision')}!={current_contract_revision}",
        }
    if not str(receipt.get("verification_method") or "").strip():
        return {"valid": False, "reason": "missing_verification_method"}
    if not str(receipt.get("declared_scope") or "").strip():
        return {"valid": False, "reason": "missing_declared_scope"}

    checks = receipt.get("checks_executed")
    if not isinstance(checks, list) or len(checks) == 0:
        return {"valid": False, "reason": "empty_checks_executed"}
    if not all(isinstance(c, dict) and c.get("passed") is True and not c.get("conflict") for c in checks):
        return {"valid": False, "reason": "failed_or_conflicting_checks"}
    if receipt.get("overall_passed") is not True:
        return {"valid": False, "reason": "overall_passed_false"}

    if actual_artifact_id or actual_artifact_sha256:
        if str(receipt.get("artifact_id") or "") != str(actual_artifact_id or ""):
            return {"valid": False, "reason": "artifact_id_mismatch"}
        if int(receipt.get("artifact_version") or 0) != int(actual_artifact_version or 1):
            return {"valid": False, "reason": "artifact_version_mismatch"}
        if str(receipt.get("artifact_sha256") or "") != str(actual_artifact_sha256 or ""):
            return {"valid": False, "reason": "artifact_sha256_mismatch"}

    return {
        "valid": True,
        "reason": "verified",
        "verified_status": str(receipt.get("verified_status") or "verified_for_scope"),
        "verification_method": str(receipt.get("verification_method")),
        "declared_scope": str(receipt.get("declared_scope")),
    }


def register_active_process(
    project_id: str,
    process_id: str,
    *,
    task_key: Optional[str] = None,
    os_pid: Optional[int] = None,
    description: str = "",
) -> Dict[str, Any]:
    """Register an active local subprocess or tool job in the controller process registry."""
    entry = {
        "project_id": project_id,
        "process_id": process_id,
        "task_key": task_key,
        "os_pid": os_pid,
        "description": description,
        "terminated_confirmed": False,
        "started_at": now_iso(),
        "terminated_at": None,
    }
    with _PROCESS_REGISTRY_LOCK:
        _ACTIVE_PROCESS_REGISTRY[(project_id, process_id)] = entry
    return dict(entry)


def confirm_process_terminated(project_id: str, process_id: str) -> bool:
    """Record authoritative confirmation that a local process has terminated."""
    with _PROCESS_REGISTRY_LOCK:
        entry = _ACTIVE_PROCESS_REGISTRY.get((project_id, process_id))
        if not entry:
            return False
        entry["terminated_confirmed"] = True
        entry["terminated_at"] = now_iso()
        return True


def clear_process_registry(project_id: Optional[str] = None) -> None:
    with _PROCESS_REGISTRY_LOCK:
        if project_id is None:
            _ACTIVE_PROCESS_REGISTRY.clear()
        else:
            for k in list(_ACTIVE_PROCESS_REGISTRY.keys()):
                if k[0] == project_id:
                    del _ACTIVE_PROCESS_REGISTRY[k]


def is_project_dispatch_stopped(project_id: str) -> bool:
    """Return True if project is currently stopping dispatch for parking or cancellation."""
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT dispatch_stopped, cleanup_lifecycle_state FROM project_memory_state WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        if not row:
            return False
        d = dict(row)
        return bool(int(d.get("dispatch_stopped") or 0) == 1)
    finally:
        conn.close()


def set_project_dispatch_stopped(project_id: str, stopped: bool = True) -> None:
    """Set or clear the dispatch_stopped flag in project_memory_state."""
    conn_chk = get_db_connection()
    try:
        has_mem = conn_chk.execute(
            "SELECT 1 FROM project_memory_state WHERE project_id = ?", (project_id,)
        ).fetchone()
    finally:
        conn_chk.close()
    if not has_mem:
        from app.services.orchestration import context_manager
        context_manager.save_project_memory_state(project_id)
    with db_transaction() as conn:
        conn.execute(
            "UPDATE project_memory_state SET dispatch_stopped = ?, updated_at = ? WHERE project_id = ?",
            (1 if stopped else 0, now_iso(), project_id),
        )


class WorkingMemoryError(Exception):
    """Base error for live operational working-memory violations."""


class StaleWorkerResultError(WorkingMemoryError):
    """Raised when a worker or tool attempts to commit against an outdated requirement or state revision."""


class ParallelConflictError(WorkingMemoryError):
    """Raised when parallel tasks attempt conflicting updates to shared fields or unowned keys."""


class ContextBudgetExceededError(WorkingMemoryError):
    """Raised when pinned essential intent/constraints cannot fit within the configured budget without splitting."""


class ParkOrCancelCleanupError(WorkingMemoryError):
    """Raised when attempting to mark a task/project parked or cancelled while process cleanup is unconfirmed."""


# ---------------------------------------------------------------------------
# Level A: Resident Working-Set Cache & Multi-Scope Memory Accounting
# ---------------------------------------------------------------------------

@dataclass
class CachedWorkingRecord:
    project_id: str
    record_type: str
    record_id: str
    revision: int
    payload: Dict[str, Any]
    byte_size: int
    reproducible: bool = True
    pinned: bool = False
    last_accessed: float = field(default_factory=time.monotonic)


class WorkingSetCache:
    """Bounded in-RAM working-set cache with version staleness checking and preferential eviction."""

    def __init__(
        self,
        max_cached_records: int = 128,
        max_cache_bytes: int = 524288,
        max_total_chars: int = 5200,
        pinned_budget_chars: int = 1800,
        reserved_output_chars: int = 1200,
    ) -> None:
        self.max_cached_records = int(max_cached_records)
        self.max_cache_bytes = int(max_cache_bytes)
        self.max_total_chars = int(max_total_chars)
        self.pinned_budget_chars = int(pinned_budget_chars)
        self.reserved_output_chars = int(reserved_output_chars)
        self._lock = threading.RLock()
        self._entries: Dict[Tuple[str, str, str], CachedWorkingRecord] = {}
        self._current_bytes: int = 0
        self._intermediate_buffers: Dict[str, int] = {}
        self._evictions_count: int = 0
        self._stale_refreshes_count: int = 0

    def put(
        self,
        project_id: str,
        record_type: str,
        record_id: str,
        revision: int,
        payload: Dict[str, Any],
        *,
        reproducible: bool = True,
        pinned: bool = False,
    ) -> CachedWorkingRecord:
        serialized = json.dumps(payload, sort_keys=True, default=str)
        b_size = len(serialized.encode("utf-8"))
        key = (project_id, record_type, record_id)
        with self._lock:
            if key in self._entries:
                self._current_bytes -= self._entries[key].byte_size
                del self._entries[key]

            self._evict_until_fits(b_size)
            rec = CachedWorkingRecord(
                project_id=project_id,
                record_type=record_type,
                record_id=record_id,
                revision=int(revision),
                payload=payload,
                byte_size=b_size,
                reproducible=bool(reproducible),
                pinned=bool(pinned),
                last_accessed=time.monotonic(),
            )
            self._entries[key] = rec
            self._current_bytes += b_size
            return rec

    def get_fresh(
        self,
        project_id: str,
        record_type: str,
        record_id: str,
        live_revision_fn: Callable[[], Tuple[int, Dict[str, Any]]],
        *,
        reproducible: bool = True,
        pinned: bool = False,
    ) -> Dict[str, Any]:
        """Return cached payload if its revision matches live DB revision; otherwise refresh before use."""
        key = (project_id, record_type, record_id)
        live_rev, live_payload = live_revision_fn()
        with self._lock:
            existing = self._entries.get(key)
            if existing is not None and existing.revision == int(live_rev):
                existing.last_accessed = time.monotonic()
                return dict(existing.payload)
            if existing is not None and existing.revision != int(live_rev):
                self._stale_refreshes_count += 1
            self.put(
                project_id,
                record_type,
                record_id,
                int(live_rev),
                live_payload,
                reproducible=reproducible,
                pinned=pinned,
            )
            return dict(live_payload)

    def evict_project(self, project_id: str, include_pinned: bool = True) -> int:
        """Release resident working-set cache entries for a parked or completed project."""
        removed = 0
        with self._lock:
            for k in list(self._entries.keys()):
                if k[0] == project_id:
                    rec = self._entries[k]
                    if not include_pinned and rec.pinned:
                        continue
                    self._current_bytes -= rec.byte_size
                    del self._entries[k]
                    removed += 1
            return removed

    def _evict_until_fits(self, incoming_bytes: int) -> None:
        with self._lock:
            # Preferentially evict persisted, reproducible, unpinned material first (LRU order)
            while (
                len(self._entries) >= self.max_cached_records
                or (self._current_bytes + incoming_bytes > self.max_cache_bytes and self._entries)
            ):
                candidates = sorted(
                    self._entries.items(),
                    key=lambda kv: (
                        0 if (kv[1].reproducible and not kv[1].pinned) else (1 if not kv[1].pinned else 2),
                        kv[1].last_accessed,
                    ),
                )
                if not candidates:
                    break
                victim_key, victim_rec = candidates[0]
                self._current_bytes -= victim_rec.byte_size
                del self._entries[victim_key]
                self._evictions_count += 1

    def register_intermediate_buffer(self, buffer_id: str, byte_size: int) -> None:
        if byte_size > MAX_INTERMEDIATE_BUFFER_BYTES:
            raise WorkingMemoryError(
                f"Intermediate buffer '{buffer_id}' ({byte_size} bytes) exceeds MAX_INTERMEDIATE_BUFFER_BYTES ({MAX_INTERMEDIATE_BUFFER_BYTES})."
            )
        with self._lock:
            self._intermediate_buffers[buffer_id] = int(byte_size)

    def release_intermediate_buffer(self, buffer_id: str) -> None:
        with self._lock:
            self._intermediate_buffers.pop(buffer_id, None)

    def stats(self, project_id: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            entries = [
                e for e in self._entries.values()
                if project_id is None or e.project_id == project_id
            ]
            return {
                "cached_records_count": len(entries),
                "total_cached_records_global": len(self._entries),
                "resident_cache_bytes_project": sum(e.byte_size for e in entries),
                "resident_cache_bytes_global": self._current_bytes,
                "max_cache_bytes": self.max_cache_bytes,
                "max_cached_records": self.max_cached_records,
                "evictions_count": self._evictions_count,
                "stale_refreshes_count": self._stale_refreshes_count,
                "pending_intermediate_buffers_bytes": sum(self._intermediate_buffers.values()),
            }


GLOBAL_WORKING_SET_CACHE = WorkingSetCache()


def get_memory_accounting_report(
    project_id: Optional[str] = None,
    cache: Optional[WorkingSetCache] = None,
) -> Dict[str, Any]:
    """Report multi-scope memory accounting across working-set cache, SQLite pages, queues, buffers, and model context.

    Explicitly discloses the scope of accounting rather than claiming all process memory is bounded by one counter.
    """
    ws_cache = cache or GLOBAL_WORKING_SET_CACHE
    cache_stats = ws_cache.stats(project_id=project_id)

    conn = get_db_connection()
    try:
        page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
        page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
        freelist_count = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
        queued_tasks = int(
            conn.execute("SELECT COUNT(*) FROM tasks WHERE status IN ('queued', 'pending', 'approved', 'in_progress', 'running')").fetchone()[0]
        )
        active_leases = int(
            conn.execute("SELECT COUNT(*) FROM worker_task_leases WHERE status IN ('leased', 'active')").fetchone()[0]
        )
        epistemic_rows = int(
            conn.execute(
                "SELECT COUNT(*) FROM project_epistemic_items WHERE (? IS NULL OR project_id = ?)",
                (project_id, project_id),
            ).fetchone()[0]
        )
        contract_rows = int(
            conn.execute(
                "SELECT COUNT(*) FROM project_task_contracts WHERE (? IS NULL OR project_id = ?)",
                (project_id, project_id),
            ).fetchone()[0]
        )
    finally:
        conn.close()

    return {
        "schema": "qc.working_memory_accounting.v1",
        "project_id": project_id,
        "resident_working_set": cache_stats,
        "sqlite_storage_and_indexes": {
            "page_count": page_count,
            "page_size_bytes": page_size,
            "total_db_bytes": page_count * page_size,
            "active_db_bytes": (page_count - freelist_count) * page_size,
            "epistemic_item_rows": epistemic_rows,
            "task_contract_rows": contract_rows,
        },
        "queues_and_pending_work": {
            "active_or_queued_tasks": queued_tasks,
            "active_worker_leases": active_leases,
            "pending_intermediate_buffers_bytes": cache_stats["pending_intermediate_buffers_bytes"],
            "max_intermediate_buffer_bytes": MAX_INTERMEDIATE_BUFFER_BYTES,
        },
        "model_context_budget": {
            "max_total_prompt_chars": ws_cache.max_total_chars,
            "pinned_intent_budget_chars": ws_cache.pinned_budget_chars,
            "reserved_output_chars": ws_cache.reserved_output_chars,
            "coordinated_max_num_predict_tokens": 2048,
            "coordinated_max_output_chars": 12000,
        },
        "accounting_scope_disclosure": (
            "Measures application WorkingSetCache entries, pending intermediate execution buffers, "
            "SQLite database page allocations, active task/lease queue depths, and model prompt/output character budgets. "
            "Does not claim to measure total Python interpreter heap fragmentation, OS kernel file cache, or external Ollama process RSS."
        ),
    }


# ---------------------------------------------------------------------------
# Epistemic Items & Qualifier-Preserving Recording
# ---------------------------------------------------------------------------

def record_epistemic_item(
    project_id: str,
    *,
    epistemic_category: str,
    statement: str,
    verification_status: Optional[str] = "unknown",
    run_id: Optional[str] = None,
    task_key: Optional[str] = None,
    requirement_ids: Optional[List[str]] = None,
    source_refs: Optional[List[str]] = None,
    artifact_refs: Optional[List[str]] = None,
    depends_on_item_ids: Optional[List[str]] = None,
    qualifiers: Optional[Dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
    source_origin: str = "controller",
    verification_receipt: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Record an epistemic item in SQLite and index it in FTS5.

    Enforces:
    - Strict separation of the 8 epistemic categories.
    - Missing or blank `verification_status` always remains `'unknown'` (never promoted to `'supported'`).
    - Untrusted source observations are scanned and stripped of directive injections; they cannot record `user_instruction_or_decision`.
    - Model/worker/callback outputs cannot self-certify as `'supported'` or `'verified_for_scope'` without a controller-signed receipt.
    - Idempotent deduplication when `idempotency_key` is provided (rejecting stale/superseded items).
    """
    if epistemic_category not in EPISTEMIC_CATEGORIES:
        raise ValueError(f"Invalid epistemic_category '{epistemic_category}'. Allowed: {EPISTEMIC_CATEGORIES}")

    if source_origin == "untrusted_document" and epistemic_category == "user_instruction_or_decision":
        raise PermissionError("Untrusted document sources cannot record user instructions or decisions.")

    clean_statement = str(statement or "").strip()
    inj_scan = intake_mod.inspect_untrusted_source_for_directive_injection(clean_statement)
    has_inj = bool(inj_scan.get("contains_injection_attempt") or inj_scan.get("injection_detected"))
    if source_origin == "untrusted_document" and has_inj:
        clean_statement = inj_scan["sanitized_text"]

    # Missing verification status stays 'unknown' (never 'supported')
    raw_status = str(verification_status or "").strip().lower()
    if not raw_status or raw_status not in VALID_VERIFICATION_STATUSES:
        norm_status = "unknown"
    else:
        norm_status = raw_status

    # Model proposals and untrusted/worker/callback sources cannot self-certify as 'supported' or 'verified_for_scope'
    if epistemic_category == "model_proposal" and norm_status in ("supported", "verified_for_scope"):
        norm_status = "proposed"
    elif source_origin in ("model", "worker", "callback", "untrusted_document") and norm_status in ("supported", "verified_for_scope"):
        has_valid_receipt = False
        if verification_receipt and task_key:
            led = intake_mod.load_durable_requirement_ledger(project_id)
            tc = get_task_contract(project_id, task_key)
            chk = verify_trusted_receipt(
                verification_receipt,
                project_id=project_id,
                task_key=task_key,
                current_requirement_revision=int(led.revision_number),
                current_contract_revision=int(tc["contract_revision"]) if tc else 1,
            )
            has_valid_receipt = bool(chk.get("valid"))
        if not has_valid_receipt:
            norm_status = "unknown"

    quals = dict(qualifiers or {})
    low_stmt = clean_statement.lower()
    if "not " in low_stmt or "never " in low_stmt or "cannot " in low_stmt or "unverified" in low_stmt:
        quals.setdefault("contains_negation", True)
    if has_inj:
        quals["stripped_directive_injection"] = True

    now = now_iso()
    with db_transaction() as c:
        if idempotency_key:
            existing = c.execute(
                "SELECT * FROM project_epistemic_items WHERE project_id = ? AND idempotency_key = ?",
                (project_id, idempotency_key),
            ).fetchone()
            if existing:
                if existing["verification_status"] in ("stale", "superseded"):
                    raise StaleWorkerResultError(
                        f"Idempotent epistemic record '{idempotency_key}' was invalidated ({existing['verification_status']}: {existing['stale_reason']}) and cannot be reused."
                    )
                return _row_to_epistemic_dict(existing, deduplicated=True)

        item_id = new_id("epi")
        c.execute(
            """INSERT INTO project_epistemic_items (
                id, project_id, run_id, task_key, epistemic_category, statement,
                verification_status, requirement_ids_json, source_refs_json,
                artifact_refs_json, depends_on_item_ids_json, qualifiers_json,
                revision_number, stale_reason, idempotency_key, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, '', ?, ?, ?)""",
            (
                item_id,
                project_id,
                run_id,
                task_key,
                epistemic_category,
                clean_statement,
                norm_status,
                jdump(requirement_ids or []),
                jdump(source_refs or []),
                jdump(artifact_refs or []),
                jdump(depends_on_item_ids or []),
                jdump(quals),
                idempotency_key,
                now,
                now,
            ),
        )
        c.execute(
            """INSERT INTO project_epistemic_fts (
                item_id, project_id, epistemic_category, statement, qualifiers_text
            ) VALUES (?, ?, ?, ?, ?)""",
            (item_id, project_id, epistemic_category, clean_statement, jdump(quals)),
        )
        row = c.execute("SELECT * FROM project_epistemic_items WHERE id = ?", (item_id,)).fetchone()
        return _row_to_epistemic_dict(row, deduplicated=False)


def _row_to_epistemic_dict(row: Any, deduplicated: bool = False) -> Dict[str, Any]:
    d = dict(row)
    return {
        "id": d["id"],
        "project_id": d["project_id"],
        "run_id": d.get("run_id"),
        "task_key": d.get("task_key"),
        "epistemic_category": d["epistemic_category"],
        "statement": d["statement"],
        "verification_status": d["verification_status"] or "unknown",
        "requirement_ids": jload(d.get("requirement_ids_json"), []),
        "source_refs": jload(d.get("source_refs_json"), []),
        "artifact_refs": jload(d.get("artifact_refs_json"), []),
        "depends_on_item_ids": jload(d.get("depends_on_item_ids_json"), []),
        "qualifiers": jload(d.get("qualifiers_json"), {}),
        "revision_number": int(d.get("revision_number", 1)),
        "superseded_by_id": d.get("superseded_by_id"),
        "stale_reason": d.get("stale_reason", ""),
        "idempotency_key": d.get("idempotency_key"),
        "deduplicated": deduplicated,
        "created_at": d.get("created_at"),
        "updated_at": d.get("updated_at"),
    }


# ---------------------------------------------------------------------------
# Section 5: Explicit Task Contracts & Project-Level Integration Checks
# ---------------------------------------------------------------------------

def register_task_contract(
    project_id: str,
    *,
    task_key: str,
    purpose: str,
    expected_output: str,
    bound_objective: Optional[str] = None,
    bound_requirement_revision: Optional[int] = None,
    bound_requirement_ids: Optional[List[str]] = None,
    input_artifact_versions: Optional[List[Dict[str, Any]]] = None,
    input_evidence_ids: Optional[List[str]] = None,
    constraints: Optional[List[str]] = None,
    exclusions: Optional[List[str]] = None,
    depends_on_task_keys: Optional[List[str]] = None,
    interfaces: Optional[Dict[str, Any]] = None,
    acceptance_checks: Optional[List[str]] = None,
    resource_budget: Optional[Dict[str, Any]] = None,
    max_retries: int = 2,
    partial_result_definition: str = "Persist verified intermediate observations and report remaining unverified requirements.",
    blocker_conditions: Optional[List[str]] = None,
    owned_fields: Optional[List[str]] = None,
    run_id: Optional[str] = None,
    status: str = "ready",
    next_action: str = "execute_task",
    selected_tool: Optional[str] = None,
    validated_arguments: Optional[Dict[str, Any]] = None,
    expected_observation: Optional[str] = None,
    verification_method: Optional[str] = None,
    verifier_id: Optional[str] = None,
    authority_scope: Optional[str] = None,
    permitted_effects: Optional[List[str]] = None,
    checkpoint_refs: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Create or update an explicit persisted work-item/action contract bound to project objective and requirement revision."""
    ledger = intake_mod.load_durable_requirement_ledger(project_id)
    obj = bound_objective if bound_objective is not None else ledger.objective
    req_rev = int(bound_requirement_revision if bound_requirement_revision is not None else ledger.revision_number)
    req_ids = bound_requirement_ids if bound_requirement_ids is not None else [
        r.req_id for r in ledger.items if r.status == "active"
    ]
    hard_constraints = constraints if constraints is not None else [r.statement for r in ledger.hard_constraints()]
    excl_list = exclusions if exclusions is not None else [
        r.statement for r in ledger.items if r.category == "exclusion" and r.status == "active"
    ]

    now = now_iso()
    with db_transaction() as c:
        existing = c.execute(
            "SELECT * FROM project_task_contracts WHERE project_id = ? AND task_key = ?",
            (project_id, task_key),
        ).fetchone()
        if existing:
            ex_d = dict(existing)
            new_rev = int(ex_d["contract_revision"]) + 1
            eff_tool = selected_tool if selected_tool is not None else ex_d.get("selected_tool", "")
            eff_args = validated_arguments if validated_arguments is not None else jload(ex_d.get("validated_arguments_json"), {})
            eff_obs = expected_observation if expected_observation is not None else ex_d.get("expected_observation", "")
            eff_vm = verification_method if verification_method is not None else ex_d.get("verification_method", "")
            eff_vid = verifier_id if verifier_id is not None else ex_d.get("verifier_id", "")
            eff_auth = authority_scope if authority_scope is not None else (ex_d.get("authority_scope") or "controller_validated")
            eff_effects = permitted_effects if permitted_effects is not None else jload(ex_d.get("permitted_effects_json"), ["read_only"])
            eff_ckpts = checkpoint_refs if checkpoint_refs is not None else jload(ex_d.get("checkpoint_refs_json"), [])
            c.execute(
                """UPDATE project_task_contracts
                   SET run_id = COALESCE(?, run_id),
                       bound_objective = ?,
                       bound_requirement_revision = ?,
                       bound_requirement_ids_json = ?,
                       purpose = ?,
                       expected_output = ?,
                       input_artifact_versions_json = ?,
                       input_evidence_ids_json = ?,
                       constraints_json = ?,
                       exclusions_json = ?,
                       depends_on_task_keys_json = ?,
                       interfaces_json = ?,
                       acceptance_checks_json = ?,
                       resource_budget_json = ?,
                       max_retries = ?,
                       status = ?,
                       next_action = ?,
                       partial_result_definition = ?,
                       blocker_conditions_json = ?,
                       owned_fields_json = ?,
                       selected_tool = ?,
                       validated_arguments_json = ?,
                       expected_observation = ?,
                       verification_method = ?,
                       verifier_id = ?,
                       authority_scope = ?,
                       permitted_effects_json = ?,
                       checkpoint_refs_json = ?,
                       stale_reason = '',
                       contract_revision = ?,
                       updated_at = ?
                 WHERE id = ?""",
                (
                    run_id,
                    obj,
                    req_rev,
                    jdump(req_ids),
                    purpose,
                    expected_output,
                    jdump(input_artifact_versions or []),
                    jdump(input_evidence_ids or []),
                    jdump(hard_constraints),
                    jdump(excl_list),
                    jdump(depends_on_task_keys or []),
                    jdump(interfaces or {}),
                    jdump(acceptance_checks or []),
                    jdump(resource_budget or {"max_tokens": 2048, "timeout_seconds": 30}),
                    int(max_retries),
                    status,
                    next_action,
                    partial_result_definition,
                    jdump(blocker_conditions or []),
                    jdump(owned_fields or []),
                    str(eff_tool),
                    jdump(eff_args),
                    str(eff_obs),
                    str(eff_vm),
                    str(eff_vid),
                    str(eff_auth),
                    jdump(eff_effects),
                    jdump(eff_ckpts),
                    new_rev,
                    now,
                    ex_d["id"],
                ),
            )
            cid = ex_d["id"]
        else:
            cid = new_id("tcon")
            c.execute(
                """INSERT INTO project_task_contracts (
                    id, project_id, run_id, task_key, bound_objective, bound_requirement_revision,
                    bound_requirement_ids_json, purpose, expected_output, input_artifact_versions_json,
                    input_evidence_ids_json, constraints_json, exclusions_json, depends_on_task_keys_json,
                    interfaces_json, acceptance_checks_json, resource_budget_json, max_retries,
                    attempts_used, status, next_action, partial_result_definition, blocker_conditions_json,
                    output_summary_json, output_artifact_ids_json, owned_fields_json,
                    selected_tool, validated_arguments_json, expected_observation, verification_method,
                    verifier_id, authority_scope, permitted_effects_json, checkpoint_refs_json,
                    stale_reason, contract_revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, '{}', '[]', ?, ?, ?, ?, ?, ?, ?, ?, ?, '', 1, ?, ?)""",
                (
                    cid,
                    project_id,
                    run_id,
                    task_key,
                    obj,
                    req_rev,
                    jdump(req_ids),
                    purpose,
                    expected_output,
                    jdump(input_artifact_versions or []),
                    jdump(input_evidence_ids or []),
                    jdump(hard_constraints),
                    jdump(excl_list),
                    jdump(depends_on_task_keys or []),
                    jdump(interfaces or {}),
                    jdump(acceptance_checks or []),
                    jdump(resource_budget or {"max_tokens": 2048, "timeout_seconds": 30}),
                    int(max_retries),
                    status,
                    next_action,
                    partial_result_definition,
                    jdump(blocker_conditions or []),
                    jdump(owned_fields or []),
                    str(selected_tool or ""),
                    jdump(validated_arguments or {}),
                    str(expected_observation or ""),
                    str(verification_method or "independent_controller_verifier"),
                    str(verifier_id or ""),
                    str(authority_scope or "controller_validated"),
                    jdump(permitted_effects or ["read_only"]),
                    jdump(checkpoint_refs or []),
                    now,
                    now,
                ),
            )
        row = c.execute("SELECT * FROM project_task_contracts WHERE id = ?", (cid,)).fetchone()
        return _row_to_contract_dict(row)


def _row_to_contract_dict(row: Any) -> Dict[str, Any]:
    d = dict(row)
    return {
        "id": d["id"],
        "project_id": d["project_id"],
        "run_id": d.get("run_id"),
        "task_key": d["task_key"],
        "bound_objective": d["bound_objective"],
        "bound_requirement_revision": int(d["bound_requirement_revision"]),
        "bound_requirement_ids": jload(d.get("bound_requirement_ids_json"), []),
        "purpose": d["purpose"],
        "expected_output": d["expected_output"],
        "input_artifact_versions": jload(d.get("input_artifact_versions_json"), []),
        "input_evidence_ids": jload(d.get("input_evidence_ids_json"), []),
        "constraints": jload(d.get("constraints_json"), []),
        "exclusions": jload(d.get("exclusions_json"), []),
        "depends_on_task_keys": jload(d.get("depends_on_task_keys_json"), []),
        "interfaces": jload(d.get("interfaces_json"), {}),
        "acceptance_checks": jload(d.get("acceptance_checks_json"), []),
        "resource_budget": jload(d.get("resource_budget_json"), {}),
        "max_retries": int(d.get("max_retries", 2)),
        "attempts_used": int(d.get("attempts_used", 0)),
        "status": d["status"],
        "next_action": d.get("next_action", ""),
        "partial_result_definition": d.get("partial_result_definition", ""),
        "blocker_conditions": jload(d.get("blocker_conditions_json"), []),
        "output_summary": jload(d.get("output_summary_json"), {}),
        "output_artifact_ids": jload(d.get("output_artifact_ids_json"), []),
        "owned_fields": jload(d.get("owned_fields_json"), []),
        "selected_tool": d.get("selected_tool", ""),
        "validated_arguments": jload(d.get("validated_arguments_json"), {}),
        "expected_observation": d.get("expected_observation", ""),
        "verification_method": d.get("verification_method", ""),
        "verifier_id": d.get("verifier_id", ""),
        "authority_scope": d.get("authority_scope", "controller_validated"),
        "permitted_effects": jload(d.get("permitted_effects_json"), ["read_only"]),
        "checkpoint_refs": jload(d.get("checkpoint_refs_json"), []),
        "verification_receipt": jload(d.get("verification_receipt_json"), {}),
        "stale_reason": d.get("stale_reason", ""),
        "contract_revision": int(d.get("contract_revision", 1)),
        "created_at": d.get("created_at"),
        "updated_at": d.get("updated_at"),
    }


def get_task_contract(project_id: str, task_key: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT * FROM project_task_contracts WHERE project_id = ? AND task_key = ?",
            (project_id, task_key),
        ).fetchone()
        return _row_to_contract_dict(row) if row else None
    finally:
        conn.close()


def list_task_contracts(project_id: str) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM project_task_contracts WHERE project_id = ? ORDER BY created_at ASC",
            (project_id,),
        ).fetchall()
        return [_row_to_contract_dict(r) for r in rows]
    finally:
        conn.close()


def verify_project_task_integration(project_id: str) -> Dict[str, Any]:
    """Verify cross-task output compatibility and active requirement coverage.

    Ensures a task succeeding locally cannot conceal a project-level contradiction
    or an uncovered requirement.
    """
    ledger = intake_mod.load_durable_requirement_ledger(project_id)
    contracts = list_task_contracts(project_id)
    active_reqs = [
        r for r in ledger.items
        if r.status == "active"
        and (
            r.kind.value in ("objective", "deliverable", "success_criterion", "acceptance_criterion")
            or (r.kind.value == "constraint" and r.hardness.value == "must")
        )
    ]
    revised_req_ids = {r.req_id for r in ledger.items if r.status == "active" and int(r.version_number) > 1}

    covered_req_ids: Set[str] = set()
    stale_tasks: List[str] = []
    interface_values: Dict[str, Tuple[str, Any]] = {}
    contradictions: List[Dict[str, Any]] = []

    for tc in contracts:
        if tc["status"] == "stale":
            stale_tasks.append(tc["task_key"])
        elif tc["status"] == "verified":
            if (
                int(tc["bound_requirement_revision"]) < int(ledger.revision_number)
                and revised_req_ids.intersection(tc["bound_requirement_ids"])
            ):
                stale_tasks.append(tc["task_key"])
                contradictions.append({
                    "type": "stale_task_revision",
                    "task_key": tc["task_key"],
                    "task_bound_revision": tc["bound_requirement_revision"],
                    "current_project_revision": ledger.revision_number,
                    "detail": f"Task '{tc['task_key']}' was verified against revision {tc['bound_requirement_revision']}, but bound requirement(s) changed in revision {ledger.revision_number}.",
                })
            else:
                covered_req_ids.update(tc["bound_requirement_ids"])

            # Check cross-task output compatibility on shared interface keys
            out_sum = tc.get("output_summary") or {}
            for k, val in out_sum.items():
                if k in ("status", "notes", "timestamp", "decision_summary", "lease_id", "producing_actor"):
                    continue
                if k in interface_values:
                    prev_task, prev_val = interface_values[k]
                    if prev_val != val:
                        contradictions.append({
                            "type": "cross_task_interface_conflict",
                            "interface_key": k,
                            "task_a": prev_task,
                            "value_a": prev_val,
                            "task_b": tc["task_key"],
                            "value_b": val,
                            "detail": f"Task '{tc['task_key']}' output {k}={val!r} contradicts task '{prev_task}' output {k}={prev_val!r}.",
                        })
                else:
                    interface_values[k] = (tc["task_key"], val)

    uncovered_reqs = [
        {"req_id": r.req_id, "category": r.kind.value, "statement": r.text}
        for r in active_reqs
        if r.req_id not in covered_req_ids
    ]

    passed = len(contradictions) == 0 and len(uncovered_reqs) == 0 and len(stale_tasks) == 0
    return {
        "schema": "qc.project_task_integration_check.v1",
        "project_id": project_id,
        "requirement_revision": ledger.revision_number,
        "integration_verified": passed,
        "status": "verified_for_scope" if passed else ("blocked" if contradictions else "partial"),
        "active_requirements_count": len(active_reqs),
        "covered_requirements_count": len(active_reqs) - len(uncovered_reqs),
        "uncovered_requirements": uncovered_reqs,
        "stale_tasks": stale_tasks,
        "contradictions": contradictions,
    }


# ---------------------------------------------------------------------------
# Section 4: One Authoritative Project State
# ---------------------------------------------------------------------------

def derive_authoritative_project_state(project_id: str) -> Dict[str, Any]:
    """Derive the authoritative current-project view directly from SQLite tables.

    Answers:
      1. What did the user ask for?
      2. Which requirement revision is current?
      3. What is complete and independently checked?
      4. What is proposed or unverified?
      5. What is blocked, contradictory, or missing?
      6. Which task is active?
      7. What should happen next?
      8. What changed since the last checkpoint?

    Separates all 8 epistemic categories without letting a model narrative become the source of truth.
    """
    ledger = intake_mod.load_durable_requirement_ledger(project_id, include_superseded=True)
    mem = context_manager.get_project_memory_state(project_id)
    contracts = list_task_contracts(project_id)
    arts = artifact_manager.list_artifacts(project_id)
    run = get_run_for_project(project_id)

    conn = get_db_connection()
    try:
        epi_rows = conn.execute(
            "SELECT * FROM project_epistemic_items WHERE project_id = ? ORDER BY created_at ASC",
            (project_id,),
        ).fetchall()
        last_ckpt_row = conn.execute(
            "SELECT * FROM project_working_checkpoints WHERE project_id = ? ORDER BY checkpoint_seq DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        latest_rev_row = conn.execute(
            "SELECT * FROM project_requirement_revisions WHERE project_id = ? ORDER BY revision_number DESC LIMIT 1",
            (project_id,),
        ).fetchone()
    finally:
        conn.close()

    epistemic_by_category: Dict[str, List[Dict[str, Any]]] = {cat: [] for cat in EPISTEMIC_CATEGORIES}
    for r in epi_rows:
        item = _row_to_epistemic_dict(r)
        epistemic_by_category[item["epistemic_category"]].append(item)

    verified_tasks = [tc for tc in contracts if tc["status"] == "verified"]
    active_tasks = [tc for tc in contracts if tc["status"] in ("running", "ready")]
    blocked_tasks = [tc for tc in contracts if tc["status"] in ("blocked", "failed", "cancel_pending_cleanup_unknown")]
    stale_tasks = [tc for tc in contracts if tc["status"] == "stale"]

    verified_artifacts = [a for a in arts if (a.get("validation_state") or a.get("validation_status")) == "valid"]
    unverified_artifacts = [a for a in arts if (a.get("validation_state") or a.get("validation_status")) in ("pending", "unverified")]

    conflict_info = ledger.detect_conflicts_and_ambiguities()
    all_contradictions = list(conflict_info["conflicts"]) + list(mem.get("contradictions", []))
    all_missing = list(conflict_info["material_gaps"]) + list(mem.get("unresolved_questions", []))

    # Compute delta since last checkpoint
    last_ckpt: Optional[Dict[str, Any]] = None
    delta_since_last_checkpoint: Dict[str, Any] = {
        "has_previous_checkpoint": False,
        "requirement_revision_delta": 0,
        "new_verified_tasks": [t["task_key"] for t in verified_tasks],
        "new_verified_artifacts": [a["id"] for a in verified_artifacts],
        "new_epistemic_items_count": len(epi_rows),
    }
    if last_ckpt_row:
        d_ck = dict(last_ckpt_row)
        prev_completed = set(jload(d_ck.get("completed_task_keys_json"), []))
        prev_art_vers = {a.get("artifact_id") for a in jload(d_ck.get("verified_artifact_versions_json"), []) if isinstance(a, dict)}
        ckpt_ts = d_ck.get("created_at") or ""
        last_ckpt = {
            "checkpoint_id": d_ck["id"],
            "checkpoint_seq": int(d_ck["checkpoint_seq"]),
            "boundary_type": d_ck["boundary_type"],
            "requirement_revision": int(d_ck["requirement_revision"]),
            "state_revision": int(d_ck["state_revision"]),
            "created_at": ckpt_ts,
        }
        delta_since_last_checkpoint = {
            "has_previous_checkpoint": True,
            "previous_checkpoint_seq": int(d_ck["checkpoint_seq"]),
            "previous_boundary_type": d_ck["boundary_type"],
            "requirement_revision_delta": int(ledger.revision_number) - int(d_ck["requirement_revision"]),
            "new_verified_tasks": [t["task_key"] for t in verified_tasks if t["task_key"] not in prev_completed],
            "newly_stale_tasks": [t["task_key"] for t in stale_tasks],
            "new_verified_artifacts": [a["id"] for a in verified_artifacts if a["id"] not in prev_art_vers],
            "new_epistemic_items_count": sum(1 for r in epi_rows if (r["created_at"] or "") > ckpt_ts),
        }

    is_parked = (
        (mem.get("next_action") or {}).get("status") == "parked"
        or (last_ckpt is not None and last_ckpt.get("boundary_type") == "park")
    )

    # Determine deterministic next action
    if is_parked:
        next_act = {"action": "resume_project", "detail": "Project is currently parked at a durable checkpoint."}
    elif conflict_info["has_material_gaps"] or conflict_info["has_conflicts"]:
        next_act = {"action": "clarify_material_gaps", "gaps": conflict_info["material_gaps"], "conflicts": conflict_info["conflicts"]}
    elif stale_tasks:
        next_act = {"action": "revalidate_stale_tasks", "task_keys": [t["task_key"] for t in stale_tasks]}
    elif active_tasks:
        next_act = {"action": "execute_task", "task_key": active_tasks[0]["task_key"], "purpose": active_tasks[0]["purpose"]}
    elif blocked_tasks:
        next_act = {"action": "resolve_blockers", "blocked_task_keys": [t["task_key"] for t in blocked_tasks]}
    else:
        next_act = mem.get("next_action") or {"action": "verify_and_deliver"}

    return {
        "schema": "qc.authoritative_project_state.v1",
        "project_id": project_id,
        "run_id": run["id"] if run else None,
        "cycle_phase": "parked" if is_parked else mem.get("current_cycle_phase", "understand"),
        "state_revision": int(mem.get("checkpoint_version", 1)),
        # 1. What did the user ask for?
        "what_user_asked_for": {
            "objective": ledger.objective,
            "deliverables": [r.to_dict() for r in ledger.deliverables()],
            "acceptance_criteria": [r.to_dict() for r in ledger.acceptance_criteria()],
            "hard_constraints": [r.to_dict() for r in ledger.hard_constraints()],
            "soft_preferences": [r.to_dict() for r in ledger.soft_preferences()],
            "exclusions": [r.to_dict() for r in ledger.items if r.kind.value == "exclusion" and r.status == "active"],
        },
        # 2. Which requirement revision is current?
        "current_requirement_revision": {
            "revision_number": ledger.revision_number,
            "change_type": dict(latest_rev_row).get("change_type") if latest_rev_row else "initial_intake",
            "change_reason": dict(latest_rev_row).get("summary") if latest_rev_row else "Initial intake",
            "updated_at": dict(latest_rev_row).get("created_at") if latest_rev_row else None,
        },
        # 3. What is complete and independently checked?
        "completed_and_independently_checked": {
            "verified_tasks": [
                {"task_key": t["task_key"], "purpose": t["purpose"], "bound_revision": t["bound_requirement_revision"], "output_summary": t["output_summary"]}
                for t in verified_tasks
            ],
            "verified_artifacts": [
                {"artifact_id": a["id"], "filename": a["filename"], "sha256": a["sha256"], "version": a.get("version_number", 1)}
                for a in verified_artifacts
            ],
            "verified_epistemic_results": [
                e for e in (epistemic_by_category["derived_result"] + epistemic_by_category["supported_claim"] + epistemic_by_category["verified_artifact"])
                if e["verification_status"] in ("supported", "verified_for_scope")
            ],
        },
        # 4. What is proposed or unverified?
        "proposed_or_unverified": {
            "model_proposals": epistemic_by_category["model_proposal"],
            "hypotheses_and_estimates": epistemic_by_category["hypothesis_or_estimate"],
            "unknown_status_items": [
                e for cat_items in epistemic_by_category.values() for e in cat_items
                if e["verification_status"] in ("unknown", "proposed")
            ],
            "unverified_artifacts": [
                {"artifact_id": a["id"], "filename": a["filename"], "validation_status": a.get("validation_status")}
                for a in unverified_artifacts
            ],
            "stale_tasks_needing_revalidation": [
                {"task_key": t["task_key"], "stale_reason": t["stale_reason"]} for t in stale_tasks
            ],
        },
        # 5. What is blocked, contradictory, or missing?
        "blocked_contradictory_or_missing": {
            "blocked_tasks": [
                {"task_key": t["task_key"], "status": t["status"], "blocker_conditions": t["blocker_conditions"]}
                for t in blocked_tasks
            ],
            "verification_failures": epistemic_by_category["verification_failure"],
            "contradictions": all_contradictions,
            "material_missing_info": all_missing,
        },
        # 6. Which task is active?
        "active_task": (
            {"task_key": active_tasks[0]["task_key"], "purpose": active_tasks[0]["purpose"], "status": active_tasks[0]["status"]}
            if active_tasks
            else None
        ),
        # 7. What should happen next?
        "next_action": next_act,
        # 8. What changed since the last checkpoint?
        "last_checkpoint": last_ckpt,
        "delta_since_last_checkpoint": delta_since_last_checkpoint,
        # Strict 8-way epistemic separation
        "epistemic_separation": epistemic_by_category,
    }


# ---------------------------------------------------------------------------
# Section 6 & 8: Scoped Work Packet Assembly, Transactional Work Boundary,
#                Optimistic Revision Fencing, Crash Journal & Dependency Invalidation
# ---------------------------------------------------------------------------

def assemble_scoped_work_packet(
    project_id: str,
    task_key: str,
    *,
    max_total_chars: int = 5200,
    pinned_budget_chars: int = 1800,
    reserved_output_chars: int = 1200,
    cache: Optional[WorkingSetCache] = None,
) -> Dict[str, Any]:
    """Assemble a bounded Resident Working Context packet for a task.

    Enforces:
    - Essential project intent (objective, current requirement revision, hard constraints,
      exclusions, acceptance criteria) is PINNED and NEVER silently truncated.
    - If pinned intent exceeds available context (`max_total_chars - reserved_output_chars`),
      raises `ContextBudgetExceededError` recommending task division rather than truncating constraints.
    - Refreshes cached records against live SQLite revisions before use.
    """
    ws_cache = cache or GLOBAL_WORKING_SET_CACHE

    def _load_live_ledger() -> Tuple[int, Dict[str, Any]]:
        led = intake_mod.load_durable_requirement_ledger(project_id)
        return led.revision_number, {
            "revision_number": led.revision_number,
            "objective": led.objective,
            "items": [r.to_dict() for r in led.items],
        }

    ledger_dict = ws_cache.get_fresh(
        project_id, "requirement_ledger", "active", _load_live_ledger, reproducible=True, pinned=True,
    )
    contract = get_task_contract(project_id, task_key)
    if not contract:
        raise WorkingMemoryError(f"Task contract '{task_key}' not found in project '{project_id}'.")

    pinned_section = {
        "project_id": project_id,
        "requirement_revision": ledger_dict["revision_number"],
        "objective": ledger_dict["objective"],
        "hard_constraints": [
            i["statement"] for i in ledger_dict["items"]
            if i["kind"] == "constraint" and i["hardness"] == "must" and i["status"] == "active"
        ],
        "exclusions": [i["statement"] for i in ledger_dict["items"] if i["kind"] == "exclusion" and i["status"] == "active"],
        "acceptance_criteria": [
            i["statement"] for i in ledger_dict["items"]
            if i["kind"] in ("success_criterion", "acceptance_criterion") and i["status"] == "active"
        ],
        "task_contract": {
            "task_key": contract["task_key"],
            "purpose": contract["purpose"],
            "expected_output": contract["expected_output"],
            "bound_requirement_ids": contract["bound_requirement_ids"],
        },
    }
    pinned_json = json.dumps(pinned_section, ensure_ascii=False)
    usable_prompt_chars = int(max_total_chars) - int(reserved_output_chars)
    if len(pinned_json) > usable_prompt_chars or len(pinned_json) > int(pinned_budget_chars):
        raise ContextBudgetExceededError(
            f"Pinned intent and constraints ({len(pinned_json)} chars) exceed pinned/usable budget "
            f"({min(usable_prompt_chars, int(pinned_budget_chars))} chars). Divide the task rather than truncating constraints."
        )

    remaining_for_evidence = usable_prompt_chars - len(pinned_json)

    # Gather upstream dependency outputs and relevant epistemic observations within remaining budget
    all_contracts = {tc["task_key"]: tc for tc in list_task_contracts(project_id)}
    upstream_outputs: List[Dict[str, Any]] = []
    for dep_key in contract["depends_on_task_keys"]:
        dep_tc = all_contracts.get(dep_key)
        if dep_tc:
            upstream_outputs.append({
                "task_key": dep_key,
                "status": dep_tc["status"],
                "output_summary": dep_tc["output_summary"],
            })

    conn = get_db_connection()
    try:
        epi_rows = conn.execute(
            """SELECT * FROM project_epistemic_items
               WHERE project_id = ? AND verification_status NOT IN ('superseded', 'stale')
               ORDER BY created_at DESC LIMIT 20""",
            (project_id,),
        ).fetchall()
    finally:
        conn.close()

    selected_observations: List[Dict[str, Any]] = []
    used_chars = len(json.dumps(upstream_outputs, ensure_ascii=False))
    omitted_observations_count = 0
    for r in epi_rows:
        ed = _row_to_epistemic_dict(r)
        compact_stmt = context_manager._smart_truncate_preserving_qualifiers(ed["statement"], 360)
        entry = {
            "id": ed["id"],
            "category": ed["epistemic_category"],
            "verification_status": ed["verification_status"],
            "statement": compact_stmt,
            "qualifiers": ed["qualifiers"],
        }
        entry_len = len(json.dumps(entry, ensure_ascii=False))
        if used_chars + entry_len <= remaining_for_evidence:
            selected_observations.append(entry)
            used_chars += entry_len
        else:
            omitted_observations_count += 1

    return {
        "schema": "qc.scoped_work_packet.v1",
        "project_id": project_id,
        "task_key": task_key,
        "requirement_revision": int(ledger_dict["revision_number"]),
        "pinned_context": pinned_section,
        "pinned_truncated": False,
        "upstream_dependency_outputs": upstream_outputs,
        "selected_observations": selected_observations,
        "omitted_observations_count": omitted_observations_count,
        "budget_accounting": {
            "max_total_chars": max_total_chars,
            "reserved_output_chars": reserved_output_chars,
            "pinned_chars": len(pinned_json),
            "evidence_and_deps_chars": used_chars,
            "total_assembled_chars": len(pinned_json) + used_chars,
        },
    }


def create_working_checkpoint(
    project_id: str,
    *,
    boundary_type: str = "task_boundary",
    active_task_key: Optional[str] = None,
    next_action: Optional[Dict[str, Any]] = None,
    delta_summary: Optional[Dict[str, Any]] = None,
    created_by: str = "orchestrator",
) -> Dict[str, Any]:
    """Persist an immutable checkpoint row in `project_working_checkpoints`."""
    ledger = intake_mod.load_durable_requirement_ledger(project_id)
    mem = context_manager.get_project_memory_state(project_id)
    contracts = list_task_contracts(project_id)
    arts = artifact_manager.list_artifacts(project_id)
    run = get_run_for_project(project_id)

    completed_keys = [t["task_key"] for t in contracts if t["status"] == "verified"]
    verified_art_vers = [
        {"artifact_id": a["id"], "filename": a["filename"], "sha256": a["sha256"], "version": a.get("version_number", 1)}
        for a in arts if (a.get("validation_state") or a.get("validation_status")) == "valid"
    ]
    decisions = [r.to_dict() for r in ledger.items if r.kind.value == "user_decision" and r.status == "active"]
    open_q = list(mem.get("unresolved_questions", []))
    blockers = [t["task_key"] for t in contracts if t["status"] == "blocked"]

    now = now_iso()
    with db_transaction() as c:
        row = c.execute(
            "SELECT COALESCE(MAX(checkpoint_seq), 0) AS max_seq FROM project_working_checkpoints WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        next_seq = int(row["max_seq"]) + 1
        ckpt_id = new_id("wckpt")
        c.execute(
            """INSERT INTO project_working_checkpoints (
                id, project_id, run_id, checkpoint_seq, boundary_type,
                requirement_revision, state_revision, active_task_key,
                completed_task_keys_json, verified_artifact_versions_json,
                decisions_snapshot_json, open_questions_json, blockers_json,
                next_action_json, remaining_budget_json, delta_summary_json,
                created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ckpt_id,
                project_id,
                run["id"] if run else None,
                next_seq,
                boundary_type,
                int(ledger.revision_number),
                int(mem.get("checkpoint_version", 1)),
                active_task_key,
                jdump(completed_keys),
                jdump(verified_art_vers),
                jdump(decisions),
                jdump(open_q),
                jdump(blockers),
                jdump(next_action or mem.get("next_action") or {}),
                jdump(mem.get("remaining_budget") or {}),
                jdump(delta_summary or {}),
                created_by,
                now,
            ),
        )
        return {
            "checkpoint_id": ckpt_id,
            "project_id": project_id,
            "checkpoint_seq": next_seq,
            "boundary_type": boundary_type,
            "requirement_revision": int(ledger.revision_number),
            "state_revision": int(mem.get("checkpoint_version", 1)),
            "completed_task_keys": completed_keys,
            "verified_artifact_versions": verified_art_vers,
            "created_at": now,
        }


def _snapshot_fence_inputs(
    conn: Any,
    project_id: str,
    task_key: str,
) -> Dict[str, Any]:
    """Read live revision fence state from SQLite."""
    rev_row = conn.execute(
        "SELECT COALESCE(MAX(revision_number), 1) AS rev FROM project_requirement_revisions WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    req_rev = int(rev_row["rev"]) if rev_row else 1

    mem_row = conn.execute(
        "SELECT checkpoint_version, dispatch_stopped, cleanup_lifecycle_state, completed_work_json FROM project_memory_state WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    state_rev = int(mem_row["checkpoint_version"]) if mem_row else 1
    dispatch_stopped = int(dict(mem_row).get("dispatch_stopped") or 0) if mem_row else 0
    completed_work = jload(dict(mem_row).get("completed_work_json"), []) if mem_row else []

    tc_row = conn.execute(
        "SELECT * FROM project_task_contracts WHERE project_id = ? AND task_key = ?",
        (project_id, task_key),
    ).fetchone()
    if not tc_row:
        raise WorkingMemoryError(f"Task contract '{task_key}' not found in project '{project_id}'.")
    tc = _row_to_contract_dict(tc_row)

    bound_req_map: Dict[str, Dict[str, Any]] = {}
    for req_code in tc["bound_requirement_ids"]:
        r_row = conn.execute(
            """SELECT req_code, version_number, status, statement
               FROM project_requirement_items
               WHERE project_id = ? AND req_code = ?
               ORDER BY version_number DESC LIMIT 1""",
            (project_id, req_code),
        ).fetchone()
        if r_row:
            bound_req_map[req_code] = {
                "version_number": int(r_row["version_number"]),
                "status": str(r_row["status"]),
                "statement": str(r_row["statement"]),
            }

    input_art_map: List[Dict[str, Any]] = []
    for inp in tc["input_artifact_versions"]:
        if not isinstance(inp, dict) or not inp.get("artifact_id"):
            continue
        aid = str(inp["artifact_id"])
        a_row = conn.execute(
            "SELECT id, sha256, validation_state FROM artifact_metadata WHERE id = ?",
            (aid,),
        ).fetchone()
        if not a_row:
            a_row = conn.execute(
                "SELECT id, checksum_sha256 AS sha256, validation_status AS validation_state FROM artifacts WHERE id = ?",
                (aid,),
            ).fetchone()
        input_art_map.append({
            "artifact_id": aid,
            "expected_version": int(inp.get("version") or inp.get("version_number") or 1),
            "expected_sha256": str(inp.get("sha256") or (a_row["sha256"] if a_row else "")),
            "live_sha256": str(a_row["sha256"]) if a_row else None,
            "validation_state": str(a_row["validation_state"]) if a_row else "missing",
        })

    return {
        "requirement_revision": req_rev,
        "state_revision": state_rev,
        "dispatch_stopped": dispatch_stopped,
        "completed_work": completed_work,
        "contract": tc,
        "contract_revision": int(tc["contract_revision"]),
        "bound_requirements": bound_req_map,
        "input_artifacts": input_art_map,
    }


def _evaluate_fence_against_snapshot(
    conn: Any,
    project_id: str,
    task_key: str,
    before: Dict[str, Any],
    *,
    lease_id: Optional[str] = None,
) -> Optional[str]:
    """Compare live SQLite state inside a transaction against the pre-work snapshot.

    Returns None if the fence holds, or a diagnostic failure reason string if stale/invalid.
    """
    after = _snapshot_fence_inputs(conn, project_id, task_key)

    if after["dispatch_stopped"] != 0:
        return f"Project '{project_id}' dispatch is stopped for park/cancel lifecycle."

    if after["requirement_revision"] != before["requirement_revision"]:
        return (
            f"Stale worker result rejected for task '{task_key}': bound to requirement revision "
            f"{before['requirement_revision']}, but project is now at revision {after['requirement_revision']}."
        )

    if after["contract_revision"] != before["contract_revision"]:
        return (
            f"Stale worker result rejected for task '{task_key}': bound to contract revision "
            f"{before['contract_revision']}, but contract is now at revision {after['contract_revision']}."
        )

    if after["contract"]["status"] in ("stale", "cancelled", "cancel_pending_cleanup_unknown"):
        return (
            f"Stale worker result rejected for task '{task_key}': contract status became "
            f"'{after['contract']['status']}' ({after['contract'].get('stale_reason') or 'invalidated'})."
        )

    for req_code, snap_req in before["bound_requirements"].items():
        live_req = after["bound_requirements"].get(req_code)
        if not live_req or live_req["version_number"] != snap_req["version_number"] or live_req["status"] != snap_req["status"]:
            return (
                f"Stale worker result rejected for task '{task_key}': bound requirement '{req_code}' "
                f"changed from v{snap_req['version_number']} ({snap_req['status']}) to "
                f"{live_req if live_req else 'missing'}."
            )

    for snap_art in before["input_artifacts"]:
        aid = snap_art["artifact_id"]
        a_row = conn.execute(
            "SELECT id, sha256, validation_state FROM artifact_metadata WHERE id = ?",
            (aid,),
        ).fetchone()
        if not a_row:
            a_row = conn.execute(
                "SELECT id, checksum_sha256 AS sha256, validation_status AS validation_state FROM artifacts WHERE id = ?",
                (aid,),
            ).fetchone()
        if not a_row:
            return f"Stale worker result rejected for task '{task_key}': input artifact '{aid}' is missing."
        if str(a_row["sha256"]) != str(snap_art["expected_sha256"]):
            return f"Stale worker result rejected for task '{task_key}': input artifact '{aid}' SHA-256 changed."
        if str(a_row["validation_state"]) == "invalid":
            return f"Stale worker result rejected for task '{task_key}': input artifact '{aid}' was marked invalid."

    if lease_id:
        lease_row = conn.execute(
            "SELECT id, status, lease_expires_at FROM worker_task_leases WHERE id = ?",
            (lease_id,),
        ).fetchone()
        if not lease_row:
            return f"Lease authority check failed for task '{task_key}': lease '{lease_id}' does not exist."
        if str(lease_row["status"]) in ("expired", "cancelled", "revoked"):
            return f"Lease authority check failed for task '{task_key}': lease '{lease_id}' has status '{lease_row['status']}'."

    return None


def execute_checkpointed_work_boundary(
    project_id: str,
    task_key: str,
    work_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
    *,
    verifier_fn: Optional[Callable[[Dict[str, Any], Dict[str, Any], Dict[str, Any]], Dict[str, Any]]] = None,
    verification_receipt: Optional[Dict[str, Any]] = None,
    pre_commit_barrier: Optional[Callable[[], None]] = None,
    idempotency_key: Optional[str] = None,
    simulate_crash_stage: Optional[str] = None,
    cache: Optional[WorkingSetCache] = None,
) -> Dict[str, Any]:
    """Execute one meaningful work boundary following the atomic 7-step transactional protocol:
      1. Read current state, contract revision, requirement revision, and input artifact hashes.
      2. Assemble bounded scoped work packet.
      3. Execute one useful native model/tool action into a bounded intermediate buffer.
      4. Stage any artifact bytes as `'pending'` (unaccepted) and run trusted controller verification.
      5. Invoke optional controlled `pre_commit_barrier` (for deterministic race testing).
      6. Open a single `BEGIN IMMEDIATE` transaction that atomically validates the revision fence
         (requirement revision, bound requirement versions, contract revision, input artifact hashes,
         dispatch_stopped flag, and lease authority) before publishing authoritative references,
         epistemic items, task status, memory state, and checkpoint.
      7. If the fence fails, mark staged artifacts `'invalid'`, record raw observation only as an
         unaccepted `'model_proposal'` (`'superseded'`), keep the task `'stale'`, and raise `StaleWorkerResultError`.
    """
    ws_cache = cache or GLOBAL_WORKING_SET_CACHE

    # 1. Snapshot fence inputs and check idempotency (short read, connection closed before work_fn)
    conn = get_db_connection()
    try:
        before_snap = _snapshot_fence_inputs(conn, project_id, task_key)
        if before_snap["dispatch_stopped"] != 0:
            raise ParkOrCancelCleanupError(
                f"Cannot dispatch work boundary for task '{task_key}': project '{project_id}' dispatch is stopped."
            )
        if idempotency_key:
            existing_epi = conn.execute(
                "SELECT * FROM project_epistemic_items WHERE project_id = ? AND idempotency_key = ?",
                (project_id, idempotency_key),
            ).fetchone()
            if existing_epi:
                if (
                    existing_epi["verification_status"] in ("stale", "superseded")
                    or before_snap["contract"]["status"] == "stale"
                    or int(before_snap["contract"]["bound_requirement_revision"]) != int(before_snap["requirement_revision"])
                ):
                    raise StaleWorkerResultError(
                        f"Idempotent result '{idempotency_key}' for task '{task_key}' is stale against current revision {before_snap['requirement_revision']}."
                    )
                return {
                    "ok": True,
                    "deduplicated": True,
                    "project_id": project_id,
                    "task_key": task_key,
                    "task_status": before_snap["contract"]["status"],
                    "epistemic_item": _row_to_epistemic_dict(existing_epi, deduplicated=True),
                }
    finally:
        conn.close()

    expected_req_rev = before_snap["requirement_revision"]
    expected_contract_rev = before_snap["contract_revision"]
    expected_state_rev = before_snap["state_revision"]

    # 2. Assemble bounded work packet
    work_packet = assemble_scoped_work_packet(project_id, task_key, cache=ws_cache)

    # 3. Execute work_fn outside any DB transaction, tracking bounded intermediate buffer
    buf_id = f"buf:{project_id}:{task_key}:{idempotency_key or time.monotonic()}"
    raw_result = work_fn(work_packet)
    serialized_res = json.dumps(raw_result, default=str).encode("utf-8")
    ws_cache.register_intermediate_buffer(buf_id, len(serialized_res))

    try:
        # Check for truncated model output
        raw_text = str(raw_result.get("response_text") or raw_result.get("statement") or "")
        if raw_result.get("check_truncation") and raw_text:
            trunc_check = deliberate_loop.detect_generation_truncation(
                raw_text,
                gen_tokens=int(raw_result.get("gen_tokens", 0)),
                num_predict=int(raw_result.get("num_predict", 2048)),
                expect_json=bool(raw_result.get("expect_json", False)),
            )
            if trunc_check["truncated"]:
                raise WorkingMemoryError(f"Truncated output rejected at work boundary: {trunc_check['reasons']}")

        run = get_run_for_project(project_id)
        lease_id: Optional[str] = raw_result.get("lease_id")

        # 4. Stage artifact bytes in CAS with validation_state='pending' (never 'valid' before fence + verification!)
        published_artifact_id: Optional[str] = None
        staged_sha256: Optional[str] = None
        staged_filename: Optional[str] = None
        art_spec = raw_result.get("publish_artifact")
        journal_id: Optional[str] = None

        if isinstance(art_spec, dict) and art_spec.get("filename") and art_spec.get("content_bytes") is not None:
            content_bytes: bytes = (
                art_spec["content_bytes"]
                if isinstance(art_spec["content_bytes"], bytes)
                else str(art_spec["content_bytes"]).encode("utf-8")
            )
            staged_sha256 = hashlib.sha256(content_bytes).hexdigest()
            staged_filename = str(art_spec["filename"])
            j_idem = idempotency_key or f"pub:{project_id}:{task_key}:{staged_sha256[:16]}"
            journal_id = new_id("pjour")
            now = now_iso()
            with db_transaction() as c:
                c.execute(
                    """INSERT OR REPLACE INTO project_artifact_publication_journal (
                        id, project_id, run_id, task_key, idempotency_key,
                        intended_filename, expected_sha256, journal_state,
                        state_revision_before, requirement_revision_before,
                        contract_revision_before, bound_requirements_snapshot_json,
                        input_artifacts_snapshot_json, verification_receipt_json,
                        lease_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'intent_logged', ?, ?, ?, ?, ?, '{}', ?, ?, ?)""",
                    (
                        journal_id,
                        project_id,
                        run["id"] if run else None,
                        task_key,
                        j_idem,
                        staged_filename,
                        staged_sha256,
                        expected_state_rev,
                        expected_req_rev,
                        expected_contract_rev,
                        jdump(before_snap["bound_requirements"]),
                        jdump(before_snap["input_artifacts"]),
                        lease_id,
                        now,
                        now,
                    ),
                )

            if simulate_crash_stage == "after_intent_before_artifact":
                raise RuntimeError("SIMULATED_CRASH: after_intent_before_artifact")

            if run:
                published_artifact_id = artifact_manager.write_controller_artifact(
                    run,
                    None,
                    staged_filename,
                    content_bytes,
                    provenance="controller_generated",
                )
                artifact_manager.set_validation(
                    published_artifact_id,
                    "pending",
                    f"Staged pending verification and atomic revision fence for task {task_key}",
                )
            else:
                from app.services.artifacts import artifact_service
                reg = artifact_service.register_artifact(
                    name=staged_filename,
                    data=content_bytes,
                    project_id=project_id,
                    description=f"Staged by task {task_key}",
                    created_by="working_memory",
                    expected_sha256=staged_sha256,
                    status="validating",
                    validation_status="pending",
                )
                published_artifact_id = reg["id"]

        # Resolve target artifact metadata for verification binding (either newly staged or referenced)
        target_art_id = published_artifact_id
        target_art_ver: Optional[int] = 1 if published_artifact_id else None
        target_art_sha256 = staged_sha256
        if not target_art_id and raw_result.get("artifact_refs"):
            first_ref = str(raw_result["artifact_refs"][0])
            conn_a = get_db_connection()
            try:
                arow = conn_a.execute("SELECT id, sha256 FROM artifact_metadata WHERE id = ?", (first_ref,)).fetchone()
                if not arow:
                    arow = conn_a.execute("SELECT id, checksum_sha256 AS sha256 FROM artifacts WHERE id = ?", (first_ref,)).fetchone()
                if arow:
                    target_art_id = str(arow["id"])
                    target_art_ver = 1
                    target_art_sha256 = str(arow["sha256"])
            finally:
                conn_a.close()

        # 4b. Run trusted controller verification (raw callback verified=True NEVER confers authority!)
        active_receipt: Optional[Dict[str, Any]] = verification_receipt
        verifier_explicitly_failed = False
        if verifier_fn is not None:
            staged_info = {
                "artifact_id": target_art_id,
                "artifact_version": target_art_ver,
                "artifact_sha256": target_art_sha256,
                "filename": staged_filename,
            }
            try:
                v_out = verifier_fn(work_packet, raw_result, staged_info)
            except TypeError:
                v_out = verifier_fn(raw_result, staged_info)
            if isinstance(v_out, dict) and v_out.get("passed") is True and v_out.get("checks"):
                active_receipt = issue_trusted_verification_receipt(
                    project_id=project_id,
                    task_key=task_key,
                    requirement_revision=expected_req_rev,
                    contract_revision=expected_contract_rev,
                    verification_method=str(v_out.get("verification_method") or "controller_contract_verifier"),
                    declared_scope=str(v_out.get("declared_scope") or f"task:{task_key}"),
                    checks_executed=list(v_out["checks"]),
                    artifact_id=target_art_id,
                    artifact_version=target_art_ver,
                    artifact_sha256=target_art_sha256,
                    verified_status=str(v_out.get("verified_status") or "verified_for_scope"),
                )
            else:
                verifier_explicitly_failed = True
                active_receipt = None

        if journal_id and published_artifact_id:
            with db_transaction() as c:
                c.execute(
                    """UPDATE project_artifact_publication_journal
                       SET artifact_id = ?,
                           artifact_version = 1,
                           verification_receipt_json = ?,
                           journal_state = 'artifact_written',
                           updated_at = ?
                     WHERE id = ?""",
                    (published_artifact_id, jdump(active_receipt or {}), now_iso(), journal_id),
                )

            if simulate_crash_stage == "after_artifact_before_checkpoint":
                raise RuntimeError("SIMULATED_CRASH: after_artifact_before_checkpoint")

        # 5. Optional controlled concurrency barrier immediately before the atomic commit transaction
        if pre_commit_barrier is not None:
            pre_commit_barrier()

        # 6. Single Atomic BEGIN IMMEDIATE Commit Transaction with Compare-And-Swap Revision Fence
        epi_cat = str(raw_result.get("epistemic_category") or "derived_result")
        if epi_cat not in EPISTEMIC_CATEGORIES:
            epi_cat = "derived_result"
        stmt = str(raw_result.get("statement") or f"Completed task {task_key}")
        clean_stmt = stmt.strip()
        inj_scan = intake_mod.inspect_untrusted_source_for_directive_injection(clean_stmt)
        has_inj = bool(inj_scan.get("contains_injection_attempt") or inj_scan.get("injection_detected"))
        quals = dict(raw_result.get("qualifiers") or {})
        low_stmt = clean_stmt.lower()
        if "not " in low_stmt or "never " in low_stmt or "cannot " in low_stmt or "unverified" in low_stmt:
            quals.setdefault("contains_negation", True)
        if has_inj:
            quals["stripped_directive_injection"] = True

        req_ids = list(before_snap["contract"]["bound_requirement_ids"])
        art_refs = [published_artifact_id] if published_artifact_id else list(raw_result.get("artifact_refs") or [])
        out_summary = dict(raw_result.get("output_summary") or {})

        fence_err_to_raise: Optional[str] = None
        with db_transaction() as c:
            fence_err = _evaluate_fence_against_snapshot(
                c, project_id, task_key, before_snap, lease_id=lease_id
            )
            now = now_iso()
            if fence_err is not None:
                # Fence failed: reject stale output, mark staged artifact invalid, preserve only as superseded proposal
                if published_artifact_id:
                    c.execute(
                        "UPDATE artifact_metadata SET validation_state = 'invalid', validation_detail = ? WHERE id = ?",
                        (f"Rejected by atomic revision fence: {fence_err}", published_artifact_id),
                    )
                    c.execute(
                        "UPDATE artifacts SET validation_status = 'invalid', status = 'failed', validation_detail = ? WHERE id = ?",
                        (f"Rejected by atomic revision fence: {fence_err}", published_artifact_id),
                    )
                if journal_id:
                    c.execute(
                        "UPDATE project_artifact_publication_journal SET journal_state = 'rejected_stale_revision', updated_at = ? WHERE id = ?",
                        (now, journal_id),
                    )
                # Preserve raw observation only as unaccepted/superseded model_proposal provenance
                sup_id = new_id("epi")
                sup_quals = {**quals, "rejected_by_revision_fence": True}
                c.execute(
                    """INSERT INTO project_epistemic_items (
                        id, project_id, run_id, task_key, epistemic_category, statement,
                        verification_status, requirement_ids_json, source_refs_json,
                        artifact_refs_json, depends_on_item_ids_json, qualifiers_json,
                        revision_number, stale_reason, idempotency_key, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'model_proposal', ?, 'superseded', ?, ?, ?, '[]', ?, ?, ?, NULL, ?, ?)""",
                    (
                        sup_id,
                        project_id,
                        run["id"] if run else None,
                        task_key,
                        clean_stmt,
                        jdump(req_ids),
                        jdump(raw_result.get("source_refs") or []),
                        jdump(art_refs),
                        jdump(sup_quals),
                        expected_req_rev,
                        fence_err,
                        now,
                        now,
                    ),
                )
                c.execute(
                    """INSERT INTO project_epistemic_fts (
                        item_id, project_id, epistemic_category, statement, qualifiers_text
                    ) VALUES (?, ?, ?, ?, ?)""",
                    (sup_id, project_id, "model_proposal", clean_stmt, jdump(sup_quals)),
                )
                c.execute(
                    """UPDATE project_task_contracts
                       SET status = 'stale',
                           stale_reason = ?,
                           next_action = 'revalidate_task',
                           updated_at = ?
                     WHERE project_id = ? AND task_key = ?""",
                    (fence_err, now, project_id, task_key),
                )
                fence_err_to_raise = fence_err

        if fence_err_to_raise is not None:
            raise StaleWorkerResultError(fence_err_to_raise)

        with db_transaction() as c:
            now = now_iso()
            # Fence holds! Now validate trusted verification receipt against live revisions & artifact hash
            receipt_validation = verify_trusted_receipt(
                active_receipt,
                project_id=project_id,
                task_key=task_key,
                current_requirement_revision=expected_req_rev,
                current_contract_revision=expected_contract_rev,
                actual_artifact_id=target_art_id,
                actual_artifact_version=target_art_ver,
                actual_artifact_sha256=target_art_sha256,
            )

            raw_req_status = str(raw_result.get("verification_status") or "").strip().lower()
            if receipt_validation["valid"] and epi_cat != "model_proposal":
                final_verif_status = receipt_validation["verified_status"]
                if final_verif_status not in ("verified_for_scope", "supported"):
                    final_verif_status = "verified_for_scope"
                new_task_status = "verified"
                quals["verification_method"] = receipt_validation["verification_method"]
                quals["declared_scope"] = receipt_validation["declared_scope"]
            elif verifier_explicitly_failed or raw_req_status == "failed":
                final_verif_status = "failed"
                new_task_status = "failed"
            elif raw_req_status == "blocked":
                final_verif_status = "blocked"
                new_task_status = "blocked"
            elif raw_req_status == "partial":
                final_verif_status = "partial"
                new_task_status = "partial"
            else:
                # Without a trusted controller verification receipt, NEVER allow verified_for_scope/supported/verified!
                final_verif_status = "proposed" if epi_cat == "model_proposal" else (
                    "observed" if raw_req_status == "observed" else "unknown"
                )
                new_task_status = "completed"
                if raw_result.get("verified") is True or raw_req_status in ("verified_for_scope", "supported"):
                    quals["unverified_self_certification_stripped"] = True
                    quals["receipt_rejection_reason"] = receipt_validation["reason"]

            if target_art_id:
                if new_task_status == "verified":
                    c.execute(
                        "UPDATE artifact_metadata SET validation_state = 'valid', validation_detail = ? WHERE id = ?",
                        (f"Verified via trusted receipt ({receipt_validation['verification_method']}) at task {task_key}", target_art_id),
                    )
                    c.execute(
                        "UPDATE artifacts SET validation_status = 'valid', status = 'available', validation_detail = ? WHERE id = ?",
                        (f"Verified via trusted receipt ({receipt_validation['verification_method']}) at task {task_key}", target_art_id),
                    )
                elif new_task_status == "failed":
                    c.execute(
                        "UPDATE artifact_metadata SET validation_state = 'invalid', validation_detail = ? WHERE id = ?",
                        (f"Verification failed at task {task_key}", target_art_id),
                    )
                    c.execute(
                        "UPDATE artifacts SET validation_status = 'invalid', status = 'failed', validation_detail = ? WHERE id = ?",
                        (f"Verification failed at task {task_key}", target_art_id),
                    )
                else:
                    c.execute(
                        "UPDATE artifact_metadata SET validation_state = 'pending', validation_detail = ? WHERE id = ?",
                        (f"Unverified output (no trusted controller receipt) at task {task_key}", target_art_id),
                    )
                    c.execute(
                        "UPDATE artifacts SET validation_status = 'pending', status = 'needs_review', validation_detail = ? WHERE id = ?",
                        (f"Unverified output (no trusted controller receipt) at task {task_key}", target_art_id),
                    )

            # Insert epistemic item atomically inside the same transaction
            item_id = new_id("epi")
            c.execute(
                """INSERT INTO project_epistemic_items (
                    id, project_id, run_id, task_key, epistemic_category, statement,
                    verification_status, requirement_ids_json, source_refs_json,
                    artifact_refs_json, depends_on_item_ids_json, qualifiers_json,
                    revision_number, stale_reason, idempotency_key, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, '', ?, ?, ?)""",
                (
                    item_id,
                    project_id,
                    run["id"] if run else None,
                    task_key,
                    epi_cat,
                    clean_stmt,
                    final_verif_status,
                    jdump(req_ids),
                    jdump(raw_result.get("source_refs") or []),
                    jdump(art_refs),
                    jdump(quals),
                    expected_req_rev,
                    idempotency_key,
                    now,
                    now,
                ),
            )
            c.execute(
                """INSERT INTO project_epistemic_fts (
                    item_id, project_id, epistemic_category, statement, qualifiers_text
                ) VALUES (?, ?, ?, ?, ?)""",
                (item_id, project_id, epi_cat, clean_stmt, jdump(quals)),
            )
            epi_row = c.execute("SELECT * FROM project_epistemic_items WHERE id = ?", (item_id,)).fetchone()
            epi_item = _row_to_epistemic_dict(epi_row, deduplicated=False)

            # Atomic compare-and-swap update on project_task_contracts
            next_act_str = str(raw_result.get("next_action") or "proceed_to_next_task")
            upd_cur = c.execute(
                """UPDATE project_task_contracts
                   SET status = ?,
                       bound_requirement_revision = ?,
                       attempts_used = attempts_used + 1,
                       output_summary_json = ?,
                       output_artifact_ids_json = ?,
                       verification_receipt_json = ?,
                       next_action = ?,
                       stale_reason = '',
                       contract_revision = contract_revision + 1,
                       updated_at = ?
                 WHERE project_id = ? AND task_key = ? AND contract_revision = ?""",
                (
                    new_task_status,
                    expected_req_rev,
                    jdump(out_summary),
                    jdump(art_refs),
                    jdump(active_receipt if receipt_validation["valid"] else {}),
                    next_act_str,
                    now,
                    project_id,
                    task_key,
                    expected_contract_rev,
                ),
            )
            if upd_cur.rowcount != 1:
                raise StaleWorkerResultError(
                    f"Atomic compare-and-swap failed on task contract '{task_key}' (expected contract_revision={expected_contract_rev})."
                )

            new_state_rev = expected_state_rev + 1
            if journal_id:
                c.execute(
                    "UPDATE project_artifact_publication_journal SET journal_state = 'state_committed', state_revision_after = ?, updated_at = ? WHERE id = ?",
                    (new_state_rev, now, journal_id),
                )

            # Update project_memory_state atomically in the same transaction
            new_completed_work = list(before_snap["completed_work"]) + [
                {"task_key": task_key, "status": new_task_status, "artifact_ids": art_refs}
            ]
            c.execute(
                """UPDATE project_memory_state
                   SET current_cycle_phase = ?,
                       completed_work_json = ?,
                       next_action_json = ?,
                       checkpoint_version = ?,
                       updated_at = ?
                 WHERE project_id = ?""",
                (
                    "verify" if new_task_status == "verified" else "execute",
                    jdump(new_completed_work),
                    jdump({"action": next_act_str, "after_task": task_key}),
                    new_state_rev,
                    now,
                    project_id,
                ),
            )

            # Insert working checkpoint atomically in the same transaction
            seq_row = c.execute(
                "SELECT COALESCE(MAX(checkpoint_seq), 0) AS max_seq FROM project_working_checkpoints WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            next_seq = int(seq_row["max_seq"]) + 1
            ckpt_id = new_id("wckpt")
            all_tcs = c.execute(
                "SELECT task_key, status FROM project_task_contracts WHERE project_id = ?",
                (project_id,),
            ).fetchall()
            completed_keys = [r["task_key"] for r in all_tcs if r["status"] == "verified"]
            blockers_keys = [r["task_key"] for r in all_tcs if r["status"] == "blocked"]
            v_arts_rows = c.execute(
                "SELECT id, filename, sha256 FROM artifact_metadata WHERE project_id = ? AND validation_state = 'valid'",
                (project_id,),
            ).fetchall()
            verified_art_vers = [
                {"artifact_id": r["id"], "filename": r["filename"], "sha256": r["sha256"], "version": 1}
                for r in v_arts_rows
            ]
            c.execute(
                """INSERT INTO project_working_checkpoints (
                    id, project_id, run_id, checkpoint_seq, boundary_type,
                    requirement_revision, state_revision, active_task_key,
                    completed_task_keys_json, verified_artifact_versions_json,
                    decisions_snapshot_json, open_questions_json, blockers_json,
                    next_action_json, remaining_budget_json, delta_summary_json,
                    created_by, created_at
                ) VALUES (?, ?, ?, ?, 'task_boundary', ?, ?, ?, ?, ?, '[]', '[]', ?, ?, '{}', ?, 'orchestrator', ?)""",
                (
                    ckpt_id,
                    project_id,
                    run["id"] if run else None,
                    next_seq,
                    expected_req_rev,
                    new_state_rev,
                    task_key,
                    jdump(completed_keys),
                    jdump(verified_art_vers),
                    jdump(blockers_keys),
                    jdump({"action": next_act_str}),
                    jdump({"completed_task": task_key, "published_artifact_id": published_artifact_id, "task_status": new_task_status}),
                    now,
                ),
            )
            prev_ckpts = list(before_snap["contract"].get("checkpoint_refs") or [])
            if ckpt_id not in prev_ckpts:
                prev_ckpts.append(ckpt_id)
            c.execute(
                "UPDATE project_task_contracts SET checkpoint_refs_json = ? WHERE project_id = ? AND task_key = ?",
                (jdump(prev_ckpts), project_id, task_key),
            )
            ckpt = {
                "checkpoint_id": ckpt_id,
                "project_id": project_id,
                "checkpoint_seq": next_seq,
                "boundary_type": "task_boundary",
                "requirement_revision": expected_req_rev,
                "state_revision": new_state_rev,
                "completed_task_keys": completed_keys,
                "verified_artifact_versions": verified_art_vers,
                "created_at": now,
            }

        ws_cache.evict_project(project_id, include_pinned=False)
        return {
            "ok": True,
            "deduplicated": False,
            "project_id": project_id,
            "task_key": task_key,
            "task_status": new_task_status,
            "verification_receipt_valid": receipt_validation["valid"],
            "published_artifact_id": published_artifact_id,
            "epistemic_item": epi_item,
            "checkpoint": ckpt,
        }
    finally:
        ws_cache.release_intermediate_buffer(buf_id)


def reconcile_crash_publication_journal(project_id: str) -> Dict[str, Any]:
    """Reconcile any uncommitted artifact publication journal entries after an interruption or crash.

    Enforces the exact same revision fencing and trusted verification receipt rules as normal execution:
    - Entries in `'intent_logged'` (crashed before artifact write completed) are rolled back (`'rolled_back'`).
    - Entries in `'artifact_written'` are checked against live `requirement_revision`, `contract_revision`,
      `bound_requirements_snapshot_json`, `input_artifacts_snapshot_json`, and `verification_receipt_json`.
      - If the revision fence fails, the staged artifact is marked `'invalid'`, the journal entry is marked
        `'rejected_stale_revision'`, and the task contract is marked `'stale'`.
      - If the revision fence holds AND a valid controller-signed verification receipt matches the stored
        artifact SHA-256, the artifact is marked `'valid'`, the task contract is marked `'verified'`, and
        the journal entry is marked `'reconciled_after_crash'`.
      - If the revision fence holds but there is no valid verification receipt, the journal entry is marked
        `'rejected_unverified'` and the task/artifact remain unverified (`'completed'` / `'pending'`).
    """
    reconciled: List[str] = []
    rolled_back: List[str] = []
    rejected_stale: List[str] = []
    rejected_unverified: List[str] = []
    now = now_iso()

    with db_transaction() as c:
        rows = c.execute(
            "SELECT * FROM project_artifact_publication_journal WHERE project_id = ? AND journal_state IN ('intent_logged', 'artifact_written')",
            (project_id,),
        ).fetchall()
        for r in rows:
            d = dict(r)
            jid = d["id"]
            tkey = d["task_key"]
            aid = d.get("artifact_id")
            if d["journal_state"] == "intent_logged" or not aid:
                c.execute(
                    "UPDATE project_artifact_publication_journal SET journal_state = 'rolled_back', updated_at = ? WHERE id = ?",
                    (now, jid),
                )
                rolled_back.append(jid)
                continue

            art_row = c.execute("SELECT id, sha256 FROM artifact_metadata WHERE id = ?", (aid,)).fetchone()
            if not art_row:
                art_row = c.execute("SELECT id, checksum_sha256 AS sha256 FROM artifacts WHERE id = ?", (aid,)).fetchone()
            if not art_row or str(art_row["sha256"]) != str(d["expected_sha256"]):
                c.execute(
                    "UPDATE project_artifact_publication_journal SET journal_state = 'rolled_back', updated_at = ? WHERE id = ?",
                    (now, jid),
                )
                rolled_back.append(jid)
                continue

            # Reconstruct snapshot to check revision fence
            live_snap = _snapshot_fence_inputs(c, project_id, tkey)
            req_rev_before = int(d.get("requirement_revision_before") or 1)
            con_rev_before = int(d.get("contract_revision_before") or 1)
            bound_snap = jload(d.get("bound_requirements_snapshot_json"), {})
            input_snap = jload(d.get("input_artifacts_snapshot_json"), [])
            synthetic_before = {
                "requirement_revision": req_rev_before,
                "contract_revision": con_rev_before,
                "bound_requirements": bound_snap if isinstance(bound_snap, dict) else {},
                "input_artifacts": input_snap if isinstance(input_snap, list) else [],
            }
            fence_err = _evaluate_fence_against_snapshot(
                c, project_id, tkey, synthetic_before, lease_id=d.get("lease_id")
            )
            if fence_err is not None:
                c.execute(
                    "UPDATE artifact_metadata SET validation_state = 'invalid', validation_detail = ? WHERE id = ?",
                    (f"Crash recovery rejected stale revision: {fence_err}", aid),
                )
                c.execute(
                    "UPDATE artifacts SET validation_status = 'invalid', status = 'failed', validation_detail = ? WHERE id = ?",
                    (f"Crash recovery rejected stale revision: {fence_err}", aid),
                )
                c.execute(
                    "UPDATE project_task_contracts SET status = 'stale', stale_reason = ?, next_action = 'revalidate_task', updated_at = ? WHERE project_id = ? AND task_key = ?",
                    (fence_err, now, project_id, tkey),
                )
                c.execute(
                    "UPDATE project_artifact_publication_journal SET journal_state = 'rejected_stale_revision', updated_at = ? WHERE id = ?",
                    (now, jid),
                )
                rejected_stale.append(jid)
                rolled_back.append(jid)
                continue

            # Revision fence holds: now check trusted verification receipt
            receipt = jload(d.get("verification_receipt_json"), {})
            receipt_chk = verify_trusted_receipt(
                receipt,
                project_id=project_id,
                task_key=tkey,
                current_requirement_revision=live_snap["requirement_revision"],
                current_contract_revision=live_snap["contract_revision"],
                actual_artifact_id=aid,
                actual_artifact_version=int(d.get("artifact_version") or 1),
                actual_artifact_sha256=str(art_row["sha256"]),
            )
            if receipt_chk["valid"]:
                c.execute(
                    "UPDATE artifact_metadata SET validation_state = 'valid', validation_detail = ? WHERE id = ?",
                    (f"Reconciled after crash with valid receipt ({receipt_chk['verification_method']})", aid),
                )
                c.execute(
                    "UPDATE artifacts SET validation_status = 'valid', status = 'available', validation_detail = ? WHERE id = ?",
                    (f"Reconciled after crash with valid receipt ({receipt_chk['verification_method']})", aid),
                )
                c.execute(
                    """UPDATE project_task_contracts
                       SET status = 'verified',
                           output_artifact_ids_json = ?,
                           verification_receipt_json = ?,
                           updated_at = ?
                     WHERE project_id = ? AND task_key = ?""",
                    (jdump([aid]), jdump(receipt), now, project_id, tkey),
                )
                c.execute(
                    "UPDATE project_artifact_publication_journal SET journal_state = 'reconciled_after_crash', updated_at = ? WHERE id = ?",
                    (now, jid),
                )
                reconciled.append(jid)
            else:
                c.execute(
                    "UPDATE artifact_metadata SET validation_state = 'pending', validation_detail = ? WHERE id = ?",
                    (f"Crash recovery: unverified ({receipt_chk['reason']})", aid),
                )
                c.execute(
                    "UPDATE project_task_contracts SET status = 'completed', output_artifact_ids_json = ?, updated_at = ? WHERE project_id = ? AND task_key = ?",
                    (jdump([aid]), now, project_id, tkey),
                )
                c.execute(
                    "UPDATE project_artifact_publication_journal SET journal_state = 'rejected_unverified', updated_at = ? WHERE id = ?",
                    (now, jid),
                )
                rejected_unverified.append(jid)

    if reconciled:
        create_working_checkpoint(
            project_id,
            boundary_type="manual",
            delta_summary={
                "crash_reconciled_journal_ids": reconciled,
                "rolled_back_journal_ids": rolled_back,
                "rejected_stale_journal_ids": rejected_stale,
                "rejected_unverified_journal_ids": rejected_unverified,
            },
            created_by="crash_recovery",
        )
    return {
        "project_id": project_id,
        "reconciled": len(reconciled),
        "rolled_back": len(rolled_back),
        "reconciled_count": len(reconciled),
        "rolled_back_count": len(rolled_back),
        "rejected_stale_count": len(rejected_stale),
        "rejected_unverified_count": len(rejected_unverified),
        "reconciled_journal_ids": reconciled,
        "rolled_back_journal_ids": rolled_back,
        "rejected_stale_journal_ids": rejected_stale,
        "rejected_stale_revision": rejected_stale,
        "rejected_unverified_journal_ids": rejected_unverified,
    }


def invalidate_dependency_closure(
    project_id: str,
    *,
    changed_requirement_ids: Optional[List[str]] = None,
    changed_task_keys: Optional[List[str]] = None,
    changed_artifact_ids: Optional[List[str]] = None,
    reason: str = "Upstream requirement or input changed",
    cache: Optional[WorkingSetCache] = None,
) -> Dict[str, Any]:
    """Compute the transitive dependency closure of changed requirements/tasks/artifacts and mark only affected items stale.

    Preserves unaffected completed tasks and verified artifacts!
    """
    ws_cache = cache or GLOBAL_WORKING_SET_CACHE
    req_set: Set[str] = set(changed_requirement_ids or [])
    task_set: Set[str] = set(changed_task_keys or [])
    art_set: Set[str] = set(changed_artifact_ids or [])

    contracts = list_task_contracts(project_id)

    # 1. Seed affected tasks from changed requirements or input artifacts
    for tc in contracts:
        if req_set.intersection(tc["bound_requirement_ids"]):
            task_set.add(tc["task_key"])
        tc_in_arts = {a.get("artifact_id") for a in tc["input_artifact_versions"] if isinstance(a, dict)}
        if art_set.intersection(tc_in_arts):
            task_set.add(tc["task_key"])

    # 2. Propagate transitive closure along task dependency edges (`depends_on_task_keys`)
    changed = True
    while changed:
        changed = False
        for tc in contracts:
            if tc["task_key"] not in task_set:
                if task_set.intersection(tc["depends_on_task_keys"]):
                    task_set.add(tc["task_key"])
                    art_set.update(tc["output_artifact_ids"])
                    changed = True

    for tc in contracts:
        if tc["task_key"] in task_set:
            art_set.update(tc["output_artifact_ids"])

    preserved_tasks: List[str] = []
    invalidated_tasks: List[str] = []
    invalidated_epistemic_ids: List[str] = []
    now = now_iso()

    with db_transaction() as c:
        for tc in contracts:
            tkey = tc["task_key"]
            if tkey in task_set:
                c.execute(
                    "UPDATE project_task_contracts SET status = 'stale', stale_reason = ?, next_action = 'revalidate_task', updated_at = ? WHERE project_id = ? AND task_key = ?",
                    (reason, now, project_id, tkey),
                )
                invalidated_tasks.append(tkey)
            elif tc["status"] in ("verified", "completed"):
                preserved_tasks.append(tkey)

        epi_rows = c.execute("SELECT * FROM project_epistemic_items WHERE project_id = ?", (project_id,)).fetchall()
        for r in epi_rows:
            ed = _row_to_epistemic_dict(r)
            if (
                (ed["task_key"] and ed["task_key"] in task_set)
                or req_set.intersection(ed["requirement_ids"])
                or art_set.intersection(ed["artifact_refs"])
            ):
                if ed["epistemic_category"] != "user_instruction_or_decision":
                    stale_quals = dict(ed.get("qualifiers") or {})
                    stale_quals["stale_reason"] = reason
                    c.execute(
                        "UPDATE project_epistemic_items SET verification_status = 'stale', stale_reason = ?, qualifiers_json = ?, updated_at = ? WHERE id = ?",
                        (reason, jdump(stale_quals), now, ed["id"]),
                    )
                    c.execute(
                        "DELETE FROM project_epistemic_fts WHERE item_id = ? AND project_id = ?",
                        (ed["id"], project_id),
                    )
                    c.execute(
                        """INSERT INTO project_epistemic_fts (
                            item_id, project_id, epistemic_category, statement, qualifiers_text
                        ) VALUES (?, ?, ?, ?, ?)""",
                        (ed["id"], project_id, ed["epistemic_category"], ed["statement"], jdump(stale_quals)),
                    )
                    invalidated_epistemic_ids.append(ed["id"])

        for aid in art_set:
            c.execute(
                "UPDATE artifact_metadata SET validation_state = 'invalid', validation_detail = ? WHERE id = ? AND project_id = ?",
                (f"Stale via dependency closure: {reason}", aid, project_id),
            )
            c.execute(
                "UPDATE artifacts SET validation_status = 'invalid', validation_detail = ? WHERE id = ? AND origin_project_id = ?",
                (f"Stale via dependency closure: {reason}", aid, project_id),
            )

    ws_cache.evict_project(project_id, include_pinned=True)
    return {
        "project_id": project_id,
        "reason": reason,
        "invalidated_tasks": sorted(invalidated_tasks),
        "invalidated_task_keys": sorted(invalidated_tasks),
        "preserved_tasks": sorted(preserved_tasks),
        "invalidated_epistemic_ids": invalidated_epistemic_ids,
        "invalidated_artifact_ids": sorted(art_set),
    }


# ---------------------------------------------------------------------------
# Section 7: Active Working-Memory Retrieval (`WorkingMemoryRetriever`)
# ---------------------------------------------------------------------------

class WorkingMemoryRetriever:
    """Executes structured + FTS5 operational working-memory queries with SQL project isolation and pagination."""

    @staticmethod
    def query(
        project_id: str,
        query_type: str,
        *,
        task_key: Optional[str] = None,
        req_id: Optional[str] = None,
        conclusion_id: Optional[str] = None,
        search_text: Optional[str] = None,
        since_checkpoint_seq: Optional[int] = None,
        limit: int = 10,
        offset: int = 0,
    ) -> Dict[str, Any]:
        bounded_limit = max(1, min(int(limit), 50))
        bounded_offset = max(0, int(offset))

        if query_type == "needed_for_task":
            if not task_key:
                raise ValueError("query_type='needed_for_task' requires task_key")
            tc = get_task_contract(project_id, task_key)
            if not tc:
                return {"query_type": query_type, "found": False, "items": []}
            conn = get_db_connection()
            try:
                placeholders = ",".join("?" for _ in (tc["bound_requirement_ids"] or [""]))
                req_rows = conn.execute(
                    f"""SELECT req_code, kind AS category, statement, hardness, verification_status
                        FROM project_requirement_items
                        WHERE project_id = ? AND status = 'active' AND req_code IN ({placeholders})
                        ORDER BY req_code ASC LIMIT ? OFFSET ?""",
                    [project_id, *(tc["bound_requirement_ids"] or [""]), bounded_limit, bounded_offset],
                ).fetchall()
                dep_placeholders = ",".join("?" for _ in (tc["depends_on_task_keys"] or [""]))
                dep_rows = conn.execute(
                    f"""SELECT task_key, status, output_summary_json, output_artifact_ids_json
                        FROM project_task_contracts
                        WHERE project_id = ? AND task_key IN ({dep_placeholders})
                        ORDER BY task_key ASC LIMIT ? OFFSET ?""",
                    [project_id, *(tc["depends_on_task_keys"] or [""]), bounded_limit, bounded_offset],
                ).fetchall()
            finally:
                conn.close()
            return {
                "query_type": query_type,
                "project_id": project_id,
                "task_key": task_key,
                "purpose": tc["purpose"],
                "bound_requirements": [dict(r) for r in req_rows],
                "input_artifact_versions": tc["input_artifact_versions"],
                "upstream_dependencies": [
                    {
                        "task_key": r["task_key"],
                        "status": r["status"],
                        "output_summary": jload(r["output_summary_json"], {}),
                        "output_artifact_ids": jload(r["output_artifact_ids_json"], []),
                    }
                    for r in dep_rows
                ],
                "pagination": {"limit": bounded_limit, "offset": bounded_offset},
            }

        if query_type == "why_approach_selected":
            conn = get_db_connection()
            try:
                rows = conn.execute(
                    """SELECT * FROM project_epistemic_items
                       WHERE project_id = ?
                         AND epistemic_category IN ('user_instruction_or_decision', 'derived_result')
                         AND (? IS NULL OR task_key = ?)
                       ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                    (project_id, task_key, task_key, bounded_limit, bounded_offset),
                ).fetchall()
            finally:
                conn.close()
            return {
                "query_type": query_type,
                "project_id": project_id,
                "decisions_and_rationale": [_row_to_epistemic_dict(r) for r in rows],
                "pagination": {"limit": bounded_limit, "offset": bounded_offset},
            }

        if query_type == "depends_on_requirement":
            if not req_id:
                raise ValueError("query_type='depends_on_requirement' requires req_id")
            like_pat = f'%"{req_id}"%'
            conn = get_db_connection()
            try:
                t_rows = conn.execute(
                    """SELECT task_key, purpose, status, bound_requirement_revision, output_artifact_ids_json
                       FROM project_task_contracts
                       WHERE project_id = ? AND bound_requirement_ids_json LIKE ?
                       ORDER BY task_key ASC LIMIT ? OFFSET ?""",
                    (project_id, like_pat, bounded_limit, bounded_offset),
                ).fetchall()
                e_rows = conn.execute(
                    """SELECT * FROM project_epistemic_items
                       WHERE project_id = ? AND requirement_ids_json LIKE ?
                       ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                    (project_id, like_pat, bounded_limit, bounded_offset),
                ).fetchall()
            finally:
                conn.close()
            return {
                "query_type": query_type,
                "project_id": project_id,
                "req_id": req_id,
                "dependent_tasks": [
                    {
                        "task_key": r["task_key"],
                        "purpose": r["purpose"],
                        "status": r["status"],
                        "bound_requirement_revision": r["bound_requirement_revision"],
                        "output_artifact_ids": jload(r["output_artifact_ids_json"], []),
                    }
                    for r in t_rows
                ],
                "dependent_epistemic_items": [_row_to_epistemic_dict(r) for r in e_rows],
                "pagination": {"limit": bounded_limit, "offset": bounded_offset},
            }

        if query_type == "evidence_for_conclusion":
            conn = get_db_connection()
            try:
                if conclusion_id:
                    target = conn.execute(
                        "SELECT * FROM project_epistemic_items WHERE project_id = ? AND id = ?",
                        (project_id, conclusion_id),
                    ).fetchone()
                else:
                    target = conn.execute(
                        """SELECT * FROM project_epistemic_items
                           WHERE project_id = ? AND epistemic_category IN ('derived_result', 'supported_claim')
                           ORDER BY created_at DESC LIMIT 1""",
                        (project_id,),
                    ).fetchone()

                supporting_obs = conn.execute(
                    """SELECT * FROM project_epistemic_items
                       WHERE project_id = ? AND epistemic_category IN ('source_observation', 'supported_claim')
                       ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                    (project_id, bounded_limit, bounded_offset),
                ).fetchall()
                contradictions_or_superseded = conn.execute(
                    """SELECT * FROM project_epistemic_items
                       WHERE project_id = ? AND (verification_status IN ('stale', 'superseded', 'failed') OR qualifiers_json LIKE '%contradict%')
                       ORDER BY created_at DESC LIMIT ?""",
                    (project_id, bounded_limit),
                ).fetchall()
            finally:
                conn.close()
            return {
                "query_type": query_type,
                "project_id": project_id,
                "conclusion": _row_to_epistemic_dict(target) if target else None,
                "supporting_evidence": [_row_to_epistemic_dict(r) for r in supporting_obs],
                "contradictions_and_superseded": [_row_to_epistemic_dict(r) for r in contradictions_or_superseded],
                "pagination": {"limit": bounded_limit, "offset": bounded_offset},
            }

        if query_type == "changed_since_pass":
            seq_cutoff = int(since_checkpoint_seq or 1)
            conn = get_db_connection()
            try:
                ckpts = conn.execute(
                    """SELECT * FROM project_working_checkpoints
                       WHERE project_id = ? AND checkpoint_seq > ?
                       ORDER BY checkpoint_seq ASC LIMIT ? OFFSET ?""",
                    (project_id, seq_cutoff, bounded_limit, bounded_offset),
                ).fetchall()
                revs = conn.execute(
                    """SELECT * FROM project_requirement_revisions
                       WHERE project_id = ?
                       ORDER BY revision_number DESC LIMIT ?""",
                    (project_id, bounded_limit),
                ).fetchall()
            finally:
                conn.close()
            return {
                "query_type": query_type,
                "project_id": project_id,
                "since_checkpoint_seq": seq_cutoff,
                "checkpoints_after": [dict(r) for r in ckpts],
                "requirement_revisions": [dict(r) for r in revs],
                "pagination": {"limit": bounded_limit, "offset": bounded_offset},
            }

        if query_type == "proceed_while_blocked":
            contracts = list_task_contracts(project_id)
            blocked_keys: Set[str] = {
                tc["task_key"] for tc in contracts if tc["status"] in ("blocked", "failed", "cancel_pending_cleanup_unknown")
            }
            if task_key:
                blocked_keys.add(task_key)
            # Propagate transitive dependents of blocked tasks
            transitively_blocked = set(blocked_keys)
            changed = True
            while changed:
                changed = False
                for tc in contracts:
                    if tc["task_key"] not in transitively_blocked:
                        if transitively_blocked.intersection(tc["depends_on_task_keys"]):
                            transitively_blocked.add(tc["task_key"])
                            changed = True

            runnable = [
                tc for tc in contracts
                if tc["task_key"] not in transitively_blocked
                and tc["status"] in ("pending", "ready", "stale")
            ]
            return {
                "query_type": query_type,
                "project_id": project_id,
                "blocked_task_keys": sorted(blocked_keys),
                "transitively_blocked_task_keys": sorted(transitively_blocked),
                "can_proceed_tasks": runnable[bounded_offset: bounded_offset + bounded_limit],
                "pagination": {"limit": bounded_limit, "offset": bounded_offset},
            }

        if query_type == "remaining_for_completion":
            integ = verify_project_task_integration(project_id)
            contracts = list_task_contracts(project_id)
            incomplete_tasks = [
                {"task_key": tc["task_key"], "status": tc["status"], "purpose": tc["purpose"], "stale_reason": tc["stale_reason"]}
                for tc in contracts if tc["status"] != "verified"
            ]
            return {
                "query_type": query_type,
                "project_id": project_id,
                "ready_for_completion": integ["integration_verified"] and len(incomplete_tasks) == 0,
                "incomplete_or_stale_tasks": incomplete_tasks[bounded_offset: bounded_offset + bounded_limit],
                "uncovered_requirements": integ["uncovered_requirements"],
                "contradictions": integ["contradictions"],
                "pagination": {"limit": bounded_limit, "offset": bounded_offset},
            }

        if query_type == "fts_search":
            return WorkingMemoryRetriever.search_project_epistemic_fts(
                project_id, search_text or "", limit=bounded_limit, offset=bounded_offset,
            )

        raise ValueError(f"Unsupported working-memory query_type: '{query_type}'")

    @staticmethod
    def search_project_epistemic_fts(
        project_id: str,
        query_text: str,
        *,
        limit: int = 10,
        offset: int = 0,
        include_historical: bool = False,
    ) -> Dict[str, Any]:
        """Full-text search over `project_epistemic_fts` strictly scoped to `project_id` in SQL.

        Prevents private knowledge from another project from ever appearing in search results.
        By default (`include_historical=False`), excludes `stale` and `superseded` epistemic items.
        """
        bounded_limit = max(1, min(int(limit), 50))
        bounded_offset = max(0, int(offset))
        tokens = [
            "".join(ch for ch in tok if ch.isalnum() or ch in ("_", "-"))
            for tok in str(query_text or "").split()
        ]
        tokens = [t for t in tokens if t]
        if not tokens:
            return {"query_type": "fts_search", "project_id": project_id, "items": [], "pagination": {"limit": bounded_limit, "offset": bounded_offset}}

        fts_expr = " OR ".join(f'"{t}"' for t in tokens[:8])
        conn = get_db_connection()
        try:
            if include_historical:
                rows = conn.execute(
                    """SELECT e.*
                       FROM project_epistemic_fts f
                       JOIN project_epistemic_items e ON e.id = f.item_id
                       WHERE f.project_id = ?
                         AND e.project_id = ?
                         AND project_epistemic_fts MATCH ?
                       ORDER BY bm25(project_epistemic_fts) ASC
                       LIMIT ? OFFSET ?""",
                    (project_id, project_id, fts_expr, bounded_limit, bounded_offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT e.*
                       FROM project_epistemic_fts f
                       JOIN project_epistemic_items e ON e.id = f.item_id
                       WHERE f.project_id = ?
                         AND e.project_id = ?
                         AND e.verification_status NOT IN ('stale', 'superseded')
                         AND project_epistemic_fts MATCH ?
                       ORDER BY bm25(project_epistemic_fts) ASC
                       LIMIT ? OFFSET ?""",
                    (project_id, project_id, fts_expr, bounded_limit, bounded_offset),
                ).fetchall()
        finally:
            conn.close()

        items = [_row_to_epistemic_dict(r) for r in rows]
        # Surface any superseded or contradictory items explicitly alongside matches
        superseded_count = sum(1 for i in items if i["verification_status"] in ("superseded", "stale"))
        return {
            "query_type": "fts_search",
            "project_id": project_id,
            "query_text": query_text,
            "include_historical": bool(include_historical),
            "items": items,
            "includes_superseded_or_stale_count": superseded_count,
            "pagination": {"limit": bounded_limit, "offset": bounded_offset},
        }


def rebuild_epistemic_fts_index(project_id: Optional[str] = None) -> Dict[str, Any]:
    """Rebuild `project_epistemic_fts` from authoritative `project_epistemic_items` rows."""
    with db_transaction() as c:
        if project_id:
            c.execute("DELETE FROM project_epistemic_fts WHERE project_id = ?", (project_id,))
            rows = c.execute("SELECT * FROM project_epistemic_items WHERE project_id = ?", (project_id,)).fetchall()
        else:
            c.execute("DELETE FROM project_epistemic_fts")
            rows = c.execute("SELECT * FROM project_epistemic_items").fetchall()

        for r in rows:
            c.execute(
                """INSERT INTO project_epistemic_fts (
                    item_id, project_id, epistemic_category, statement, qualifiers_text
                ) VALUES (?, ?, ?, ?, ?)""",
                (r["id"], r["project_id"], r["epistemic_category"], r["statement"], r["qualifiers_json"] or "{}"),
            )
    return {"ok": True, "reindexed_rows": len(rows), "project_id": project_id}


# ---------------------------------------------------------------------------
# Section 9: Park, Resume, Cancellation & Parallel Work Merge Rules
# ---------------------------------------------------------------------------

def inspect_authoritative_execution_cleanup(
    project_id: str,
    *,
    task_key: Optional[str] = None,
    cache: Optional[WorkingSetCache] = None,
) -> Dict[str, Any]:
    """Derive authoritative cleanup confirmation from controller task state, outstanding leases,
    worker acknowledgments, active futures, intermediate buffers, and local process registry.

    Never trusts caller-supplied booleans. Treats an expired lease as `cleanup_unknown` until
    the worker acknowledges termination (via terminal execution submission or post-expiry
    heartbeat with `running_jobs_count == 0`).
    """
    ws_cache = cache or GLOBAL_WORKING_SET_CACHE
    blockers: List[Dict[str, Any]] = []
    has_unknown = False
    has_failed = False

    # 1. Check active local process registry
    with _PROCESS_REGISTRY_LOCK:
        for (pid, proc_id), entry in _ACTIVE_PROCESS_REGISTRY.items():
            if pid != project_id:
                continue
            if task_key and entry.get("task_key") and entry.get("task_key") != task_key:
                continue
            if not entry.get("terminated_confirmed"):
                has_unknown = True
                blockers.append({
                    "type": "unconfirmed_local_process",
                    "process_id": proc_id,
                    "task_key": entry.get("task_key"),
                    "os_pid": entry.get("os_pid"),
                    "detail": f"Local process '{proc_id}' has not confirmed termination.",
                })

    # 2. Check resident intermediate buffers
    with ws_cache._lock:
        prefix = f"buf:{project_id}:{task_key}:" if task_key else f"buf:{project_id}:"
        for buf_key, b_size in ws_cache._intermediate_buffers.items():
            if buf_key.startswith(prefix):
                blockers.append({
                    "type": "active_intermediate_buffer",
                    "buffer_id": buf_key,
                    "byte_size": b_size,
                    "detail": f"In-flight work boundary buffer '{buf_key}' is still active.",
                })

    # 3. Check in-flight controller futures in engine
    try:
        from app.services.orchestration import engine as _eng
        with _eng._FUTURES_LOCK:
            for ot_id, fut in list(_eng._ACTIVE_FUTURES.items()):
                meta = _eng._ACTIVE_METADATA.get(ot_id) or {}
                if not fut.done():
                    conn_f = get_db_connection()
                    try:
                        r_row = conn_f.execute(
                            "SELECT project_id FROM orchestration_runs WHERE id = ?",
                            (meta.get("run_id"),),
                        ).fetchone()
                        if r_row and str(r_row["project_id"]) == str(project_id):
                            blockers.append({
                                "type": "active_controller_future",
                                "orch_task_id": ot_id,
                                "detail": f"Controller thread future for orchestration task '{ot_id}' is still running.",
                            })
                    finally:
                        conn_f.close()
    except Exception:
        pass

    # 4. Inspect SQLite project_task_contracts, controller tasks, and worker_task_leases
    conn = get_db_connection()
    try:
        if task_key:
            tc_rows = conn.execute(
                "SELECT task_key, status, worker_task_id FROM project_task_contracts WHERE project_id = ? AND task_key = ?",
                (project_id, task_key),
            ).fetchall()
        else:
            tc_rows = conn.execute(
                "SELECT task_key, status, worker_task_id FROM project_task_contracts WHERE project_id = ?",
                (project_id,),
            ).fetchall()

        for r in tc_rows:
            if r["status"] == "running":
                blockers.append({
                    "type": "running_task_contract",
                    "task_key": r["task_key"],
                    "status": r["status"],
                    "detail": f"Task contract '{r['task_key']}' is currently in 'running' state.",
                })

        # Controller tasks for this project
        t_rows = conn.execute(
            "SELECT id, title, status, assigned_worker FROM tasks WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        project_task_ids = [str(r["id"]) for r in t_rows]
        for r in t_rows:
            if r["status"] in ("in_progress", "running"):
                blockers.append({
                    "type": "active_controller_task",
                    "task_id": r["id"],
                    "status": r["status"],
                    "assigned_worker": r["assigned_worker"],
                    "detail": f"Controller task '{r['id']}' has active status '{r['status']}'.",
                })

        # Worker task leases for this project's tasks
        if project_task_ids:
            placeholders = ",".join("?" for _ in project_task_ids)
            lease_rows = conn.execute(
                f"""SELECT l.*, n.last_contact_at AS worker_last_seen_at,
                           (SELECT COUNT(*) FROM worker_task_leases l2
                            WHERE l2.worker_id = l.worker_id AND l2.status IN ('leased', 'active')) AS worker_running_jobs
                    FROM worker_task_leases l
                    LEFT JOIN infrastructure_nodes n ON n.id = l.worker_id
                    WHERE l.task_id IN ({placeholders})""",
                project_task_ids,
            ).fetchall()
            for lr in lease_rows:
                ld = dict(lr)
                l_status = str(ld.get("status") or "")
                l_id = str(ld["id"])
                t_id = str(ld["task_id"])
                w_id = str(ld["worker_id"])
                if l_status in ("leased", "active"):
                    blockers.append({
                        "type": "active_worker_lease",
                        "lease_id": l_id,
                        "task_id": t_id,
                        "worker_id": w_id,
                        "status": l_status,
                        "lease_expires_at": ld.get("lease_expires_at"),
                        "detail": f"Worker lease '{l_id}' on worker '{w_id}' is still '{l_status}'.",
                    })
                elif l_status == "expired":
                    # Expired lease is UNKNOWN until worker acknowledges termination or sends post-expiry idle heartbeat
                    exp_at = str(ld.get("lease_expires_at") or "")
                    exec_ack = conn.execute(
                        """SELECT id, status, completed_at FROM worker_executions
                           WHERE task_id = ? AND worker_id = ?
                             AND status IN ('cancelled', 'error', 'success')
                             AND completed_at >= ?
                           LIMIT 1""",
                        (t_id, w_id, exp_at),
                    ).fetchone()
                    w_seen = str(ld.get("worker_last_seen_at") or "")
                    w_jobs = ld.get("worker_running_jobs")
                    post_expiry_idle_hb = bool(w_seen and exp_at and w_seen > exp_at and w_jobs is not None and int(w_jobs) == 0)
                    if not exec_ack and not post_expiry_idle_hb:
                        has_unknown = True
                        blockers.append({
                            "type": "expired_lease_worker_state_unknown",
                            "lease_id": l_id,
                            "task_id": t_id,
                            "worker_id": w_id,
                            "status": "expired",
                            "lease_expires_at": exp_at,
                            "detail": (
                                f"Lease '{l_id}' for task '{t_id}' on worker '{w_id}' expired at {exp_at} "
                                "without worker termination acknowledgment or post-expiry idle heartbeat."
                            ),
                        })
    finally:
        conn.close()

    cleanup_confirmed = len(blockers) == 0
    if cleanup_confirmed:
        lifecycle_state = "cleanup_confirmed"
    elif has_failed:
        lifecycle_state = "cleanup_failed"
    elif has_unknown:
        lifecycle_state = "cleanup_unknown"
    else:
        lifecycle_state = "draining"

    return {
        "project_id": project_id,
        "task_key": task_key,
        "cleanup_confirmed": cleanup_confirmed,
        "lifecycle_state": lifecycle_state,
        "blockers": blockers,
    }


def _stop_project_dispatch_and_cancel_queued(
    project_id: str,
    *,
    task_key: Optional[str] = None,
) -> List[str]:
    """Transition lifecycle `requested -> dispatch_stopped` and cancel unleased queued tasks."""
    now = now_iso()
    conn_chk = get_db_connection()
    try:
        has_mem = conn_chk.execute(
            "SELECT 1 FROM project_memory_state WHERE project_id = ?", (project_id,)
        ).fetchone()
    finally:
        conn_chk.close()
    if not has_mem:
        context_manager.save_project_memory_state(project_id)
    cancelled_queued_task_ids: List[str] = []
    with db_transaction() as c:
        if task_key is None:
            c.execute(
                """UPDATE project_memory_state
                   SET dispatch_stopped = 1,
                       cleanup_lifecycle_state = 'dispatch_stopped',
                       updated_at = ?
                 WHERE project_id = ?""",
                (now, project_id),
            )
            q_rows = c.execute(
                """SELECT id FROM tasks
                   WHERE project_id = ?
                     AND status IN ('queued', 'pending', 'approved', 'awaiting_approval')
                     AND id NOT IN (
                         SELECT task_id FROM worker_task_leases WHERE status IN ('leased', 'active')
                     )""",
                (project_id,),
            ).fetchall()
            for qr in q_rows:
                c.execute(
                    "UPDATE tasks SET status = 'cancelled', updated_at = ? WHERE id = ?",
                    (now, qr["id"]),
                )
                cancelled_queued_task_ids.append(str(qr["id"]))
        else:
            c.execute(
                """UPDATE project_task_contracts
                   SET cleanup_lifecycle_state = 'dispatch_stopped',
                       updated_at = ?
                 WHERE project_id = ? AND task_key = ?""",
                (now, project_id, task_key),
            )
    return cancelled_queued_task_ids


def park_project(
    project_id: str,
    *,
    actor: str = "operator",
    reason: str = "User requested project park",
    active_process_cleanup_confirmed: Optional[bool] = None,
    cache: Optional[WorkingSetCache] = None,
) -> Dict[str, Any]:
    """Park a project at a durable checkpoint and release resident working-set cache.

    Enforces explicit lifecycle: `requested -> dispatch_stopped -> draining -> cleanup_confirmed`
    (with honest `cleanup_unknown` / `cleanup_failed` states when active work cannot be confirmed terminated).
    Caller-supplied `active_process_cleanup_confirmed=True` is NEVER trusted as authority over active
    tasks, outstanding leases, or unacknowledged expired leases.
    """
    ws_cache = cache or GLOBAL_WORKING_SET_CACHE
    now = now_iso()

    # Step 1: requested -> dispatch_stopped (prevent any new task dispatch while parking)
    cancelled_queued = _stop_project_dispatch_and_cancel_queued(project_id)

    # Step 2: Authoritative cleanup inspection (never trusting caller True flag)
    cleanup_report = inspect_authoritative_execution_cleanup(project_id, cache=ws_cache)
    if active_process_cleanup_confirmed is False:
        cleanup_report["cleanup_confirmed"] = False
        cleanup_report["lifecycle_state"] = "cleanup_unknown"
        cleanup_report["blockers"].append({
            "type": "caller_reported_unconfirmed_process",
            "detail": "Caller explicitly reported unconfirmed active process cleanup.",
        })

    if not cleanup_report["cleanup_confirmed"]:
        honest_state = cleanup_report["lifecycle_state"]
        with db_transaction() as c:
            c.execute(
                """UPDATE project_memory_state
                   SET cleanup_lifecycle_state = ?,
                       updated_at = ?
                 WHERE project_id = ?""",
                (honest_state, now, project_id),
            )
            c.execute(
                """UPDATE project_task_contracts
                   SET cleanup_lifecycle_state = ?,
                       updated_at = ?
                 WHERE project_id = ? AND status = 'running'""",
                (honest_state, now, project_id),
            )
        blocker_summary = "; ".join(b["detail"] for b in cleanup_report["blockers"])
        raise ParkOrCancelCleanupError(
            f"Cannot mark project '{project_id}' parked while active process cleanup is unconfirmed "
            f"(lifecycle_state='{honest_state}', blockers=[{blocker_summary}])."
        )

    # Step 3: draining -> cleanup_confirmed
    run = get_run_for_project(project_id)
    context_manager.save_project_memory_state(
        project_id,
        run_id=run["id"] if run else None,
        current_cycle_phase="blocked",
        next_action={
            "action": "resume_from_checkpoint",
            "status": "parked",
            "cleanup_lifecycle_state": "cleanup_confirmed",
            "parked_by": actor,
            "reason": reason,
        },
    )
    with db_transaction() as c:
        c.execute(
            """UPDATE project_memory_state
               SET dispatch_stopped = 1,
                   cleanup_lifecycle_state = 'cleanup_confirmed',
                   updated_at = ?
             WHERE project_id = ?""",
            (now, project_id),
        )
    ckpt = create_working_checkpoint(
        project_id,
        boundary_type="park",
        next_action={"action": "resume_from_checkpoint", "reason": reason},
        delta_summary={
            "park_reason": reason,
            "actor": actor,
            "lifecycle_history": ["requested", "dispatch_stopped", "draining", "cleanup_confirmed"],
            "cancelled_queued_tasks": cancelled_queued,
        },
        created_by=actor,
    )
    evicted = ws_cache.evict_project(project_id, include_pinned=True)
    return {
        "ok": True,
        "status": "parked",
        "cleanup_lifecycle_state": "cleanup_confirmed",
        "lifecycle_history": ["requested", "dispatch_stopped", "draining", "cleanup_confirmed"],
        "cleanup_confirmed": True,
        "cleanup_report": cleanup_report,
        "project_id": project_id,
        "checkpoint": ckpt,
        "evicted_resident_cache_records": evicted,
    }


def resume_project(
    project_id: str,
    *,
    actor: str = "operator",
    cache: Optional[WorkingSetCache] = None,
) -> Dict[str, Any]:
    """Resume a parked or interrupted project from its durable SQLite state.

    - Reconciles any crash-interrupted artifact publications.
    - Detects whether requirement revisions or input artifacts changed since the park checkpoint.
    - Invalidates only the affected dependency closure while preserving completed verified work.
    - Re-enables dispatch (`dispatch_stopped = 0`) and does not replay the entire conversation.
    """
    ws_cache = cache or GLOBAL_WORKING_SET_CACHE
    with db_transaction() as c:
        c.execute(
            """UPDATE project_memory_state
               SET dispatch_stopped = 0,
                   cleanup_lifecycle_state = 'none',
                   updated_at = ?
             WHERE project_id = ?""",
            (now_iso(), project_id),
        )
    crash_rec = reconcile_crash_publication_journal(project_id)
    ledger = intake_mod.load_durable_requirement_ledger(project_id)
    contracts = list_task_contracts(project_id)

    conn = get_db_connection()
    try:
        park_ckpt = conn.execute(
            "SELECT * FROM project_working_checkpoints WHERE project_id = ? ORDER BY checkpoint_seq DESC LIMIT 1",
            (project_id,),
        ).fetchone()
    finally:
        conn.close()

    revised_req_ids = {r.req_id for r in ledger.items if r.status == "active" and int(r.version_number) > 1}
    stale_detected: List[str] = []
    for tc in contracts:
        if tc["status"] == "stale":
            stale_detected.append(tc["task_key"])
        elif (
            tc["status"] == "verified"
            and int(tc["bound_requirement_revision"]) < int(ledger.revision_number)
            and revised_req_ids.intersection(tc["bound_requirement_ids"])
        ):
            stale_detected.append(tc["task_key"])

    if stale_detected:
        invalidate_dependency_closure(
            project_id,
            changed_task_keys=stale_detected,
            reason=f"Requirement revision advanced to {ledger.revision_number} before resume",
            cache=ws_cache,
        )

    refreshed_contracts = list_task_contracts(project_id)
    preserved_verified = [tc["task_key"] for tc in refreshed_contracts if tc["status"] == "verified"]
    pending_or_stale = [tc["task_key"] for tc in refreshed_contracts if tc["status"] in ("pending", "ready", "stale")]

    next_task_key = pending_or_stale[0] if pending_or_stale else None
    run = get_run_for_project(project_id)
    context_manager.save_project_memory_state(
        project_id,
        run_id=run["id"] if run else None,
        current_cycle_phase="execute" if next_task_key else "deliver",
        next_action={"action": "execute_task", "task_key": next_task_key} if next_task_key else {"action": "verify_and_deliver"},
    )
    resume_ckpt = create_working_checkpoint(
        project_id,
        boundary_type="resume",
        active_task_key=next_task_key,
        next_action={"action": "execute_task", "task_key": next_task_key} if next_task_key else {"action": "verify_and_deliver"},
        delta_summary={
            "resumed_from_checkpoint_seq": int(park_ckpt["checkpoint_seq"]) if park_ckpt else 0,
            "preserved_verified_tasks": preserved_verified,
            "stale_tasks_flagged": stale_detected,
        },
        created_by=actor,
    )
    return {
        "ok": True,
        "status": "resumed",
        "project_id": project_id,
        "requirement_revision": ledger.revision_number,
        "resumed_from_checkpoint_seq": int(park_ckpt["checkpoint_seq"]) if park_ckpt else 0,
        "crash_recovery": crash_rec,
        "preserved_verified_tasks": preserved_verified,
        "stale_tasks_requiring_revalidation": stale_detected,
        "next_task_key": next_task_key,
        "replayed_full_conversation": False,
        "restarted_from_scratch": False,
        "resume_checkpoint": resume_ckpt,
    }


def cancel_task_or_project(
    project_id: str,
    *,
    task_key: Optional[str] = None,
    actor: str = "operator",
    reason: str = "User requested cancellation",
    process_cleanup_confirmed: Optional[bool] = None,
) -> Dict[str, Any]:
    """Cancel a specific task or the entire project using authoritative cleanup confirmation.

    Enforces explicit lifecycle: `requested -> dispatch_stopped -> draining -> cleanup_confirmed`
    (with honest `cleanup_unknown` / `cleanup_failed` states when active work cannot be confirmed terminated).
    Caller-supplied `process_cleanup_confirmed=True` is NEVER trusted as authority over active
    tasks, outstanding leases, or unacknowledged expired leases.
    """
    now = now_iso()

    # Step 1: requested -> dispatch_stopped
    cancelled_queued = _stop_project_dispatch_and_cancel_queued(project_id, task_key=task_key)

    # Step 2: Authoritative cleanup inspection
    cleanup_report = inspect_authoritative_execution_cleanup(project_id, task_key=task_key)
    if process_cleanup_confirmed is False:
        cleanup_report["cleanup_confirmed"] = False
        cleanup_report["lifecycle_state"] = "cleanup_unknown"
        cleanup_report["blockers"].append({
            "type": "caller_reported_unconfirmed_process",
            "detail": "Caller explicitly reported unconfirmed active process cleanup.",
        })

    if not cleanup_report["cleanup_confirmed"]:
        honest_state = cleanup_report["lifecycle_state"]
        with db_transaction() as c:
            if task_key:
                c.execute(
                    """UPDATE project_task_contracts
                       SET status = 'cancel_pending_cleanup_unknown',
                           cleanup_lifecycle_state = ?,
                           next_action = 'verify_process_cleanup',
                           updated_at = ?
                     WHERE project_id = ? AND task_key = ?""",
                    (honest_state, now, project_id, task_key),
                )
            else:
                c.execute(
                    """UPDATE project_memory_state
                       SET cleanup_lifecycle_state = ?,
                           updated_at = ?
                     WHERE project_id = ?""",
                    (honest_state, now, project_id),
                )
                c.execute(
                    """UPDATE project_task_contracts
                       SET status = 'cancel_pending_cleanup_unknown',
                           cleanup_lifecycle_state = ?,
                           next_action = 'verify_process_cleanup',
                           updated_at = ?
                     WHERE project_id = ? AND status = 'running'""",
                    (honest_state, now, project_id),
                )
        blocker_summary = "; ".join(b["detail"] for b in cleanup_report["blockers"])
        raise ParkOrCancelCleanupError(
            f"Cannot mark {'task ' + task_key if task_key else 'project ' + project_id} as cancelled "
            f"while process cleanup is unconfirmed (lifecycle_state='{honest_state}', blockers=[{blocker_summary}])."
        )

    # Step 3: draining -> cleanup_confirmed
    if task_key:
        with db_transaction() as c:
            c.execute(
                """UPDATE project_task_contracts
                   SET status = 'cancelled',
                       cleanup_lifecycle_state = 'cleanup_confirmed',
                       next_action = 'none',
                       updated_at = ?
                 WHERE project_id = ? AND task_key = ?""",
                (now, project_id, task_key),
            )
        return {
            "ok": True,
            "project_id": project_id,
            "task_key": task_key,
            "status": "cancelled",
            "cleanup_lifecycle_state": "cleanup_confirmed",
            "lifecycle_history": ["requested", "dispatch_stopped", "draining", "cleanup_confirmed"],
            "cleanup_confirmed": True,
            "cleanup_report": cleanup_report,
        }

    run = get_run_for_project(project_id)
    steer_res = intake_mod.apply_user_steering_update(
        project_id,
        run_id=run["id"] if run else None,
        actor=actor,
        action="cancel",
        reason=reason,
    )
    with db_transaction() as c:
        c.execute(
            """UPDATE project_memory_state
               SET dispatch_stopped = 1,
                   cleanup_lifecycle_state = 'cleanup_confirmed',
                   current_cycle_phase = 'cancelled',
                   updated_at = ?
             WHERE project_id = ?""",
            (now, project_id),
        )
        c.execute(
            """UPDATE project_task_contracts
               SET status = 'cancelled',
                   cleanup_lifecycle_state = 'cleanup_confirmed',
                   next_action = 'none',
                   updated_at = ?
             WHERE project_id = ? AND status IN ('pending', 'ready', 'running', 'stale', 'cancel_pending_cleanup_unknown', 'park_draining', 'cancel_draining')""",
            (now, project_id),
        )
    create_working_checkpoint(
        project_id,
        boundary_type="cancel",
        next_action={"action": "none", "reason": reason},
        delta_summary={
            "cancelled_by": actor,
            "reason": reason,
            "lifecycle_history": ["requested", "dispatch_stopped", "draining", "cleanup_confirmed"],
            "cancelled_queued_tasks": cancelled_queued,
        },
        created_by=actor,
    )
    GLOBAL_WORKING_SET_CACHE.evict_project(project_id, include_pinned=True)
    return {
        "ok": True,
        "project_id": project_id,
        "status": "cancelled",
        "cleanup_lifecycle_state": "cleanup_confirmed",
        "lifecycle_history": ["requested", "dispatch_stopped", "draining", "cleanup_confirmed"],
        "cleanup_confirmed": True,
        "cleanup_report": cleanup_report,
        "steering_result": steer_res,
    }


def commit_parallel_task_update(
    project_id: str,
    task_key: str,
    *,
    proposed_updates: Dict[str, Any],
    expected_contract_revision: Optional[int] = None,
) -> Dict[str, Any]:
    """Enforce ownership and merge rules for parallel tasks updating shared project state.

    - Tasks may only write keys declared in their `owned_fields` (if `owned_fields` is non-empty).
    - If another parallel task already wrote a conflicting value to a shared key, raises `ParallelConflictError`
      while preserving independent non-conflicting progress.
    """
    tc = get_task_contract(project_id, task_key)
    if not tc:
        raise WorkingMemoryError(f"Task contract '{task_key}' not found.")

    if expected_contract_revision is not None and int(tc["contract_revision"]) != int(expected_contract_revision):
        raise ParallelConflictError(
            f"Stale contract revision on task '{task_key}': expected {expected_contract_revision}, actual {tc['contract_revision']}."
        )

    owned = set(tc["owned_fields"])
    if owned:
        unowned_keys = set(proposed_updates.keys()) - owned
        if unowned_keys:
            raise ParallelConflictError(
                f"Task '{task_key}' attempted to update unowned field(s) {sorted(unowned_keys)}; owned_fields={sorted(owned)}."
            )

    # Check if any other parallel task owns or already wrote a conflicting value to these keys
    other_contracts = [c for c in list_task_contracts(project_id) if c["task_key"] != task_key]
    for other in other_contracts:
        other_out = other.get("output_summary") or {}
        for k, v in proposed_updates.items():
            if k in other_out and other_out[k] != v:
                raise ParallelConflictError(
                    f"Parallel update conflict on field '{k}': task '{task_key}' proposed {v!r}, "
                    f"which conflicts with task '{other['task_key']}' committed value {other_out[k]!r}."
                )

    merged_out = dict(tc.get("output_summary") or {})
    merged_out.update(proposed_updates)
    now = now_iso()
    with db_transaction() as c:
        c.execute(
            """UPDATE project_task_contracts
               SET output_summary_json = ?,
                   contract_revision = contract_revision + 1,
                   status = 'verified',
                   updated_at = ?
             WHERE project_id = ? AND task_key = ?""",
            (jdump(merged_out), now, project_id, task_key),
        )
    return {
        "ok": True,
        "project_id": project_id,
        "task_key": task_key,
        "committed_fields": list(proposed_updates.keys()),
        "contract_revision": int(tc["contract_revision"]) + 1,
    }
