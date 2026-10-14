"""
Offline Task & Result Buffering for VPS Worker Agent.
Allows safe operation and result persistence during temporary network dropouts.
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("qwen_collective.worker_buffer")


class OfflineBuffer:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_submissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    lease_id TEXT NOT NULL,
                    idempotency_key TEXT UNIQUE NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_attempt_at TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    quarantine_reason TEXT
                )
                """
            )
            # Add columns if migrating an existing database
            cursor = conn.execute("PRAGMA table_info(pending_submissions)")
            cols = [col[1] for col in cursor.fetchall()]
            if "status" not in cols:
                conn.execute("ALTER TABLE pending_submissions ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'")
            if "quarantine_reason" not in cols:
                conn.execute("ALTER TABLE pending_submissions ADD COLUMN quarantine_reason TEXT")
            conn.commit()

    def buffer_submission(
        self,
        task_id: str,
        lease_id: str,
        idempotency_key: str,
        submission_payload: Dict[str, Any],
    ):
        """Save a task result locally to submit once the controller is reachable."""
        now = datetime.now(timezone.utc).isoformat()
        payload_str = json.dumps(submission_payload)
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO pending_submissions (
                    task_id, lease_id, idempotency_key, payload_json, created_at, attempts, status
                ) VALUES (?, ?, ?, ?, ?, 0, 'pending')
                ON CONFLICT(idempotency_key) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    attempts = attempts + 1,
                    status = 'pending'
                """,
                (task_id, lease_id, idempotency_key, payload_str, now),
            )
            conn.commit()
            logger.info("Buffered submission locally for task %s (idempotency key: %s)", task_id, idempotency_key)

    def get_pending_submissions(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Retrieve un-flushed submissions in FIFO order."""
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, task_id, lease_id, idempotency_key, payload_json, created_at, attempts
                FROM pending_submissions
                WHERE status = 'pending'
                ORDER BY id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

            results = []
            for r in rows:
                try:
                    payload = json.loads(r["payload_json"])
                except Exception:
                    payload = {}
                results.append({
                    "id": r["id"],
                    "task_id": r["task_id"],
                    "lease_id": r["lease_id"],
                    "idempotency_key": r["idempotency_key"],
                    "payload": payload,
                    "created_at": r["created_at"],
                    "attempts": r["attempts"],
                })
            return results

    def quarantine_submission(self, submission_id: int, reason: str):
        """Quarantine an invalid or unprocessable submission so it doesn't block the pending queue."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE pending_submissions
                SET status = 'quarantined',
                    quarantine_reason = ?,
                    last_attempt_at = ?
                WHERE id = ?
                """,
                (reason, now, submission_id),
            )
            conn.commit()
            logger.error("Quarantined buffer submission #%s (Reason: %s)", submission_id, reason)

    def get_quarantined_submissions(self) -> List[Dict[str, Any]]:
        """Retrieve quarantined submissions for diagnostic review."""
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, task_id, lease_id, idempotency_key, payload_json, created_at, attempts, quarantine_reason
                FROM pending_submissions
                WHERE status = 'quarantined'
                ORDER BY id ASC
                """
            ).fetchall()

            results = []
            for r in rows:
                try:
                    payload = json.loads(r["payload_json"])
                except Exception:
                    payload = {}
                results.append({
                    "id": r["id"],
                    "task_id": r["task_id"],
                    "lease_id": r["lease_id"],
                    "idempotency_key": r["idempotency_key"],
                    "payload": payload,
                    "created_at": r["created_at"],
                    "attempts": r["attempts"],
                    "quarantine_reason": r["quarantine_reason"],
                })
            return results

    def remove_submission(self, submission_id: int):
        """Delete successfully synced submission from local buffer."""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM pending_submissions WHERE id = ?", (submission_id,))
            conn.commit()
            logger.info("Cleared synced buffer submission #%s", submission_id)

    def count_pending(self) -> int:
        """Count remaining buffered pending submissions."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT count(*) FROM pending_submissions WHERE status = 'pending'").fetchone()
            return int(row[0]) if row else 0

    def count_quarantined(self) -> int:
        """Count quarantined submissions."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT count(*) FROM pending_submissions WHERE status = 'quarantined'").fetchone()
            return int(row[0]) if row else 0
