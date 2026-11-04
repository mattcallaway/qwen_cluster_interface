"""Central artifact metadata, controller-side artifact writing and safe retrieval."""
from __future__ import annotations

import hashlib
import mimetypes
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings
from app.database import db_transaction, get_db_connection
from app.services.orchestration.common import NotFound, new_id, now_iso

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._\-]")
MAX_ARTIFACT_BYTES = 25 * 1024 * 1024


def _safe_name(name: str) -> str:
    base = _SAFE_NAME.sub("_", Path(name).name)[:120].strip(".")
    return base or f"artifact_{new_id('f')}"


def _mime(name: str) -> str:
    return mimetypes.guess_type(name)[0] or {"json": "application/json", "md": "text/markdown"}.get(
        name.rsplit(".", 1)[-1], "application/octet-stream")


def _insert(run_id: Optional[str], project_id: str, task_id: Optional[str], orch_task_id: Optional[str],
            worker_id: Optional[str], path: Path, provenance: str,
            producer_attempt: int = 1, cas_hash: Optional[str] = None) -> Optional[str]:
    data = path.read_bytes()
    computed_sha = hashlib.sha256(data).hexdigest()
    aid = new_id("art")

    # If cas_hash not supplied, store in CAS automatically
    if not cas_hash:
        try:
            from app.services.artifacts.storage import storage_backend
            cas_res = storage_backend.store_cas_blob(data)
            cas_hash = cas_res["cas_hash"]
        except Exception:
            cas_hash = computed_sha

    allowed_prov = {'worker_telemetry', 'model_output', 'controller_generated', 'user_supplied'}
    if provenance not in allowed_prov:
        provenance = 'controller_generated'

    with db_transaction() as c:
        cur = c.execute(
            """INSERT OR IGNORE INTO artifact_metadata (id, run_id, project_id, task_id, orch_task_id, worker_id,
                   filename, mime_type, size_bytes, sha256, storage_path, cas_hash, producer_attempt, provenance, validation_state, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'pending', ?)""",
            (aid, run_id, project_id, task_id, orch_task_id, worker_id, path.name, _mime(path.name), len(data),
             computed_sha, str(path.resolve()), cas_hash, producer_attempt, provenance, now_iso()))
        if cur.rowcount != 1:
            row = c.execute("SELECT id FROM artifact_metadata WHERE storage_path = ?", (str(path.resolve()),)).fetchone()
            if row:
                aid = row["id"]
                c.execute(
                    """UPDATE artifact_metadata
                       SET run_id = COALESCE(?, run_id),
                           project_id = ?,
                           task_id = COALESCE(?, task_id),
                           orch_task_id = COALESCE(?, orch_task_id),
                           worker_id = COALESCE(?, worker_id),
                           size_bytes = ?,
                           sha256 = ?,
                           cas_hash = COALESCE(?, cas_hash),
                           producer_attempt = ?,
                           provenance = ?,
                           created_at = ?
                       WHERE id = ?""",
                    (run_id, project_id, task_id, orch_task_id, worker_id, len(data),
                     computed_sha, cas_hash, producer_attempt, provenance, now_iso(), aid))
                return aid
            return None
    if run_id:
        from app.services.orchestration.telemetry import record_usage
        record_usage(run_id, storage_bytes=len(data))

    # Sync with canonical artifacts tables
    try:
        with db_transaction() as c:
            c.execute(
                """
                INSERT OR IGNORE INTO artifacts (
                    id, display_name, description, artifact_type, mime_type, file_extension,
                    size_bytes, checksum_sha256, storage_backend, storage_key, current_version_number,
                    status, validation_status, validation_detail, knowledge_status, visibility,
                    retention_policy, origin_project_id, origin_run_id, origin_task_id, origin_worker_id,
                    producer_attempt, cas_hash, created_by, created_at, updated_at
                ) VALUES (?, ?, 'Task output artifact', 'document', ?, ?, ?, ?, 'local_filesystem', ?, 1,
                          'available', 'valid', '', 'not_processed', 'private', 'standard', ?, ?, ?, ?, ?, ?, 'orchestrator', ?, ?)
                """,
                (
                    aid, path.name, _mime(path.name), Path(path.name).suffix.lower(),
                    len(data), computed_sha, str(path.resolve()),
                    project_id, run_id, task_id, worker_id, producer_attempt, cas_hash, now_iso(), now_iso(),
                ),
            )
            c.execute(
                """
                INSERT OR IGNORE INTO artifact_versions (
                    id, artifact_id, version_number, storage_backend, storage_key,
                    size_bytes, checksum_sha256, change_description, producing_run_id, producing_task_id,
                    producing_worker_id, producer_attempt, cas_hash, validation_status, created_by, created_at
                ) VALUES (?, ?, 1, 'local_filesystem', ?, ?, ?, 'Orchestration task output', ?, ?, ?, ?, ?, 'valid', 'orchestrator', ?)
                """,
                (
                    f"artver-{aid[4:] if aid.startswith('art-') else aid}",
                    aid, str(path.resolve()), len(data), computed_sha,
                    run_id, task_id, worker_id, producer_attempt, cas_hash, now_iso(),
                ),
            )
            c.execute(
                """
                INSERT OR IGNORE INTO artifact_project_links (id, artifact_id, project_id, role, added_at, added_by)
                VALUES (?, ?, ?, 'output', ?, 'orchestrator')
                """,
                (f"apl-{aid[4:] if aid.startswith('art-') else aid}", aid, project_id, now_iso()),
            )
    except Exception:
        # Non-fatal sync
        pass

    return aid


def register_task_artifacts(run: Dict[str, Any], orch_task_id: str, task_id: str, worker_id: Optional[str],
                            provenance: str, producer_attempt: int = 1) -> List[str]:
    """Index files the worker result pipeline stored under evidence/task_<id>/ (idempotent)."""
    d = settings.evidence_dir / f"task_{task_id}"
    ids: List[str] = []
    if d.is_dir():
        for f in sorted(d.iterdir()):
            if f.is_file() and f.stat().st_size <= MAX_ARTIFACT_BYTES:
                aid = _insert(run["id"], run["project_id"], task_id, orch_task_id, worker_id, f, provenance,
                              producer_attempt=producer_attempt)
                if aid:
                    ids.append(aid)
    return ids


def write_controller_artifact(run: Dict[str, Any], orch_task_id: Optional[str], filename: str, content: bytes,
                              provenance: str = "controller_generated", producer_attempt: int = 1) -> str:
    if len(content) > MAX_ARTIFACT_BYTES:
        raise ValueError("Artifact too large.")
    d = settings.evidence_dir / f"orch_{run['id']}"
    d.mkdir(parents=True, exist_ok=True)
    base_name = _safe_name(filename)
    if producer_attempt > 1:
        p = d / f"att{producer_attempt}_{base_name}"
    else:
        p = d / base_name
    p.write_bytes(content)

    # Store in CAS
    from app.services.artifacts.storage import storage_backend
    cas_res = storage_backend.store_cas_blob(content)
    cas_hash = cas_res["cas_hash"]

    aid = _insert(run["id"], run["project_id"], None, orch_task_id, "controller-local", p, provenance,
                  producer_attempt=producer_attempt, cas_hash=cas_hash)
    assert aid
    return aid


def set_validation(artifact_id: str, state: str, detail: str = "") -> None:
    with db_transaction() as c:
        c.execute("UPDATE artifact_metadata SET validation_state = ?, validation_detail = ? WHERE id = ?",
                  (state, detail[:1000], artifact_id))
        c.execute("UPDATE artifacts SET validation_status = ?, validation_detail = ?, updated_at = ? WHERE id = ?",
                  (state, detail[:1000], now_iso(), artifact_id))


def list_artifacts(project_id: str) -> List[Dict[str, Any]]:
    c = get_db_connection()
    try:
        rows = c.execute(
            """SELECT a.id, a.task_id, a.orch_task_id, a.worker_id, a.filename, a.mime_type, a.size_bytes, a.sha256,
                      a.storage_path, a.cas_hash, a.producer_attempt, a.provenance, a.validation_state, a.validation_detail, a.created_at, t.plan_key
               FROM artifact_metadata a LEFT JOIN orchestration_tasks t ON t.id = a.orch_task_id
               WHERE a.project_id = ? ORDER BY a.created_at""", (project_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def resolve_for_download(project_id: str, artifact_id: str) -> Dict[str, Any]:
    """Return metadata + verified path. Rejects anything outside authorized directories (no traversal)."""
    c = get_db_connection()
    try:
        row = c.execute("SELECT * FROM artifact_metadata WHERE id = ? AND project_id = ?",
                        (artifact_id, project_id)).fetchone()
    finally:
        c.close()
    if not row:
        raise NotFound("Artifact not found.")
    meta = dict(row)
    p = Path(meta["storage_path"]).resolve()
    allowed_roots = [settings.evidence_dir.resolve(), settings.artifacts_dir.resolve()]
    is_confined = any(p == root or p.is_relative_to(root) for root in allowed_roots)
    if not is_confined:
        raise NotFound("Artifact file is unavailable.")
    if not p.is_file():
        # Fallback to CAS blob lookup
        if meta.get("cas_hash"):
            from app.services.artifacts.storage import storage_backend
            cas_path = storage_backend.get_cas_path(meta["cas_hash"])
            if cas_path.is_file():
                p = cas_path
            else:
                raise NotFound("Artifact file is unavailable.")
        else:
            raise NotFound("Artifact file is unavailable.")
    if hashlib.sha256(p.read_bytes()).hexdigest() != meta["sha256"]:
        raise NotFound("Artifact failed integrity verification (checksum mismatch).")
    meta["path"] = p
    return meta
