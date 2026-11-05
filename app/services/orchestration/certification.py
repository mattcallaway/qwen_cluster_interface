"""Controller-record provenance gate. Never accepts caller-supplied receipts.

This is an incremental containment gate, not an authorization boundary against
an administrator who can modify the controller database or Python process.
"""
import hashlib
import sqlite3


def native_output_provenance(conn, project_id, run_id, content):
    return native_artifact_digest(conn, project_id, run_id,
        hashlib.sha256(content.encode('utf-8')).hexdigest())


def native_artifact_digest(conn, project_id, run_id, digest):
    if not project_id or not run_id:
        return False
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        return False
    try:
        row = conn.execute('''
            SELECT 1 FROM orchestration_runs r
            JOIN orchestration_tasks o ON o.run_id=r.id
            JOIN tasks t ON t.id=o.current_task_id AND t.project_id=r.project_id
            JOIN worker_task_leases l ON l.task_id=t.id
            JOIN worker_executions e ON e.task_id=t.id AND e.lease_id=l.id
                AND e.worker_id=l.worker_id AND e.idempotency_key=l.idempotency_key
            JOIN artifacts a ON a.origin_task_id=t.id
                AND a.origin_project_id=r.project_id AND a.origin_run_id=r.id
                AND a.origin_worker_id=e.worker_id
            WHERE r.id=? AND r.project_id=?
                AND t.status='completed' AND l.status='completed'
                AND e.status='success' AND e.exit_code=0
                AND o.dispatched_node=e.worker_id
                AND a.validation_status='valid' AND a.checksum_sha256=?
                AND a.status IN ('available','accepted')
            LIMIT 1
        ''', (run_id, project_id, digest)).fetchone()
        return row is not None
    except sqlite3.Error:
        return False


def completed_native_inference(conn, project_id, run_id):
    import json
    try:
        rows=conn.execute('''SELECT o.action_json FROM orchestration_runs r
            JOIN orchestration_tasks o ON o.run_id=r.id
            JOIN tasks t ON t.id=o.current_task_id AND t.project_id=r.project_id
            JOIN worker_task_leases l ON l.task_id=t.id
            JOIN worker_executions e ON e.task_id=t.id AND e.lease_id=l.id
                AND e.worker_id=l.worker_id AND e.idempotency_key=l.idempotency_key
            WHERE r.id=? AND r.project_id=? AND t.status='completed'
                AND l.status='completed' AND e.status='success' AND e.exit_code=0
                AND o.dispatched_node=e.worker_id''',(run_id,project_id)).fetchall()
        for row in rows:
            try:
                if json.loads(row[0]).get('type') == 'inference': return True
            except (ValueError,TypeError,AttributeError):
                continue
        return False
    except sqlite3.Error:
        return False
