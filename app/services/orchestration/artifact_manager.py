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
            worker_id: Optional[str], path: Path, provenance: str) -> Optional[str]:
    data = path.read_bytes()
    aid = new_id("art")
    with db_transaction() as c:
        cur = c.execute(
            """INSERT OR IGNORE INTO artifact_metadata (id, run_id, project_id, task_id, orch_task_id, worker_id,
                   filename, mime_type, size_bytes, sha256, storage_path, provenance, validation_state, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'pending', ?)""",
            (aid, run_id, project_id, task_id, orch_task_id, worker_id, path.name, _mime(path.name), len(data),
             hashlib.sha256(data).hexdigest(), str(path.resolve()), provenance, now_iso()))
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
                           provenance = ?,
                           created_at = ?
                       WHERE id = ?""",
                    (run_id, project_id, task_id, orch_task_id, worker_id, len(data),
                     hashlib.sha256(data).hexdigest(), provenance, now_iso(), aid))
                return aid
            return None
    if run_id:
        from app.services.orchestration.telemetry import record_usage
        record_usage(run_id, storage_bytes=len(data))
    return aid


def register_task_artifacts(run: Dict[str, Any], orch_task_id: str, task_id: str, worker_id: Optional[str],
                            provenance: str) -> List[str]:
    """Index files the worker result pipeline stored under evidence/task_<id>/ (idempotent)."""
    d = settings.evidence_dir / f"task_{task_id}"
    ids: List[str] = []
    if d.is_dir():
        for f in sorted(d.iterdir()):
            if f.is_file() and f.stat().st_size <= MAX_ARTIFACT_BYTES:
                aid = _insert(run["id"], run["project_id"], task_id, orch_task_id, worker_id, f, provenance)
                if aid:
                    ids.append(aid)
    return ids


def write_controller_artifact(run: Dict[str, Any], orch_task_id: Optional[str], filename: str, content: bytes,
                              provenance: str = "controller_generated") -> str:
    if len(content) > MAX_ARTIFACT_BYTES:
        raise ValueError("Artifact too large.")
    d = settings.evidence_dir / f"orch_{run['id']}"
    d.mkdir(parents=True, exist_ok=True)
    p = d / _safe_name(filename)
    p.write_bytes(content)
    aid = _insert(run["id"], run["project_id"], None, orch_task_id, "controller-local", p, provenance)
    assert aid
    return aid


def set_validation(artifact_id: str, state: str, detail: str = "") -> None:
    with db_transaction() as c:
        c.execute("UPDATE artifact_metadata SET validation_state = ?, validation_detail = ? WHERE id = ?",
                  (state, detail[:1000], artifact_id))


def list_artifacts(project_id: str) -> List[Dict[str, Any]]:
    c = get_db_connection()
    try:
        rows = c.execute(
            """SELECT a.id, a.task_id, a.orch_task_id, a.worker_id, a.filename, a.mime_type, a.size_bytes, a.sha256,
                      a.provenance, a.validation_state, a.validation_detail, a.created_at, t.plan_key
               FROM artifact_metadata a LEFT JOIN orchestration_tasks t ON t.id = a.orch_task_id
               WHERE a.project_id = ? ORDER BY a.created_at""", (project_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def resolve_for_download(project_id: str, artifact_id: str) -> Dict[str, Any]:
    """Return metadata + verified path. Rejects anything outside the evidence directory (no traversal)."""
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
    base = settings.evidence_dir.resolve()
    if not p.is_relative_to(base) or not p.is_file():
        raise NotFound("Artifact file is unavailable.")
    if hashlib.sha256(p.read_bytes()).hexdigest() != meta["sha256"]:
        raise NotFound("Artifact failed integrity verification (checksum mismatch).")
    meta["path"] = p
    return meta
