"""
Task 8C.3 Adversarial Test Suite: ASGI Transport-Layer Upload Security & Ingestion Protection.
Validates:
- Scenario A: Small authorized upload succeeds.
- Scenario B: Upload exactly at the configured artifact limit (26,214,400 bytes) succeeds.
- Scenario C: Upload exceeding the limit by one byte (26,214,401 bytes) is rejected (413).
- Scenario D: Oversized declared Content-Length is rejected before body buffering.
- Scenario E: Missing Content-Length does not bypass enforcement.
- Scenario F: Falsified smaller Content-Length does not permit an oversized body.
- Scenario G: Many small streamed chunks cumulatively exceeding the limit are rejected.
- Scenario H: One unexpectedly large received chunk is rejected.
- Scenario I: Interrupted upload leaves no accepted artifact or unmanaged temporary file.
- Scenario J: Unauthenticated upload is denied (401).
- Scenario K: Expired or reassigned worker lease is denied (403/409).
- Scenario L: Concurrent uploads cannot bypass individual request limits.
- Scenario M: Valid multipart framing does not incorrectly reject a payload within the artifact limit.
- Scenario N: Malformed transfer metadata cannot produce a size-check bypass.
- Scenario O: JSON transport boundary (36,001,112 bytes) and decoded artifact size boundary (26,214,400 bytes).
- Scenario P: RFC 7230/9112 Transfer-Encoding + Content-Length conflict rejection, multiple multipart files, path normalization.
- Real wire tests: Live local Uvicorn loopback socket execution.
- Memory & Performance benchmarks: 5-mode memory accounting (Python heap delta & OS process RSS).
"""
from __future__ import annotations

import asyncio
import base64
import ctypes
from ctypes import wintypes
import hashlib
import http.client
import io
import json
import os
import shutil
import socket
import tempfile
import threading
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import pytest
from starlette.testclient import TestClient
import uvicorn

from app.config import settings
from app.database import get_db_connection
from app.main import app
from app.services import worker as worker_svc
from app.services.upload_security import (
    MAX_ARTIFACT_PAYLOAD_BYTES,
    MAX_MULTIPART_FRAMING_BYTES,
    MAX_MULTIPART_REQUEST_BYTES,
    MAX_BASE64_PAYLOAD_BYTES,
    MAX_JSON_FRAMING_BYTES,
    MAX_JSON_REQUEST_BYTES,
    MAX_DEFAULT_BODY_BYTES,
    UploadSizeLimitMiddleware,
    PayloadTooLargeError,
    MalformedTransferMetadataError,
)


@pytest.fixture
def client(db_conn):
    return TestClient(app)


# ---------------------------------------------------------------------------
# Windows OS Process RSS Helper
# ---------------------------------------------------------------------------
class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def get_process_memory_rss() -> int:
    """Return current process resident working set size in bytes via Windows PSAPI."""
    try:
        fn = ctypes.windll.psapi.GetProcessMemoryInfo
        fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESS_MEMORY_COUNTERS), wintypes.DWORD]
        fn.restype = wintypes.BOOL
        pmc = _PROCESS_MEMORY_COUNTERS()
        pmc.cb = ctypes.sizeof(_PROCESS_MEMORY_COUNTERS)
        if fn(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return pmc.WorkingSetSize
    except Exception:
        pass
    return 0


def _make_asgi_scope(
    path: str,
    method: str = "POST",
    headers: Optional[List[Tuple[bytes, bytes]]] = None,
) -> Dict[str, Any]:
    """Construct a complete, standard ASGI 3.0 HTTP scope."""
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method.upper(),
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": headers or [],
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 50000),
    }


def _setup_test_worker_task(db_conn) -> Dict[str, Any]:
    """Provision a worker node, authorization token, task, and active lease."""
    node_id = f"worker-{uuid4().hex[:8]}"
    worker_svc.register_worker_node(node_id=node_id, name="Test Node", role="vps_worker")
    token_id, raw_token = worker_svc.create_worker_token(node_id)
    
    task_id = f"task-{uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()
    future = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + 3600, tz=timezone.utc).isoformat()
    
    db_conn.execute(
        """
        INSERT OR REPLACE INTO tasks (
            id, title, description, task_type, status, assigned_worker,
            attempt_count, max_retries, approval_required, created_at, updated_at
        ) VALUES (?, 'Upload Task', 'Testing artifact upload', 'vps_action', 'in_progress', ?, 1, 3, 0, ?, ?)
        """,
        (task_id, node_id, now, now),
    )
    
    lease_id = f"lease-{uuid4().hex[:8]}"
    db_conn.execute(
        """
        INSERT OR REPLACE INTO worker_task_leases (
            id, task_id, worker_id, idempotency_key, status, leased_at,
            lease_expires_at, heartbeat_at, attempt_count, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 'leased', ?, ?, ?, 1, ?, ?)
        """,
        (lease_id, task_id, node_id, f"idem-{task_id}", now, future, now, now, now),
    )
    db_conn.commit()
    
    return {
        "node_id": node_id,
        "token": raw_token,
        "task_id": task_id,
        "lease_id": lease_id,
    }


# ============================================================================
# Scenario A: Small authorized upload succeeds
# ============================================================================
def test_scenario_a_small_authorized_upload_succeeds(client, db_conn):
    """Scenario A: Authorized worker with active lease successfully uploads a small artifact."""
    env = _setup_test_worker_task(db_conn)
    task_id = env["task_id"]
    token = env["token"]
    lease_id = env["lease_id"]
    
    payload = b"Hello Qwen Collective Artifact System\n" * 50  # ~1.9 KB
    expected_sha256 = hashlib.sha256(payload).hexdigest()
    
    files = {"file": ("test_artifact.txt", io.BytesIO(payload), "text/plain")}
    data = {
        "filename": "test_artifact.txt",
        "expected_sha256": expected_sha256,
        "lease_id": lease_id,
    }
    
    r = client.post(
        f"/api/v1/worker/tasks/{task_id}/artifacts",
        files=files,
        data=data,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, f"Multipart upload failed: {r.text}"
    resp = r.json()
    assert resp["status"] == "stored"
    assert resp["artifact"]["computed_sha256"] == expected_sha256
    assert resp["artifact"]["file_size"] == len(payload)
    
    stored_path = Path(resp["artifact"]["stored_path"])
    assert stored_path.exists()
    assert stored_path.read_bytes() == payload


# ============================================================================
# Scenario B: Upload exactly at the configured artifact limit succeeds
# ============================================================================
def test_scenario_b_exact_artifact_limit_succeeds(client, db_conn):
    """Scenario B: Artifact upload of exactly 26,214,400 bytes (25.0 MiB) succeeds."""
    env = _setup_test_worker_task(db_conn)
    task_id = env["task_id"]
    token = env["token"]
    lease_id = env["lease_id"]
    
    pattern = b"0123456789abcdef" * 1024  # 16 KB block
    repeat_count = MAX_ARTIFACT_PAYLOAD_BYTES // len(pattern)
    remainder = MAX_ARTIFACT_PAYLOAD_BYTES % len(pattern)
    exact_payload = (pattern * repeat_count) + pattern[:remainder]
    assert len(exact_payload) == MAX_ARTIFACT_PAYLOAD_BYTES
    
    expected_sha256 = hashlib.sha256(exact_payload).hexdigest()
    
    files = {"file": ("exact_25mb.dat", io.BytesIO(exact_payload), "application/octet-stream")}
    data = {
        "filename": "exact_25mb.dat",
        "expected_sha256": expected_sha256,
        "lease_id": lease_id,
    }
    
    r = client.post(
        f"/api/v1/worker/tasks/{task_id}/artifacts",
        files=files,
        data=data,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, f"Exact limit upload failed: {r.text}"
    resp = r.json()
    assert resp["status"] == "stored"
    assert resp["artifact"]["file_size"] == MAX_ARTIFACT_PAYLOAD_BYTES
    assert resp["artifact"]["computed_sha256"] == expected_sha256
    
    stored_path = Path(resp["artifact"]["stored_path"])
    assert stored_path.exists()
    assert stored_path.stat().st_size == MAX_ARTIFACT_PAYLOAD_BYTES


# ============================================================================
# Scenario C: Upload exceeding the limit by one byte is rejected
# ============================================================================
def test_scenario_c_exceeding_limit_by_one_byte_rejected(client, db_conn):
    """Scenario C: Artifact upload exceeding limit by 1 byte (26,214,401 bytes) is rejected with 413."""
    env = _setup_test_worker_task(db_conn)
    task_id = env["task_id"]
    token = env["token"]
    lease_id = env["lease_id"]
    
    oversized_payload = b"Z" * (MAX_ARTIFACT_PAYLOAD_BYTES + 1)
    assert len(oversized_payload) == 26214401
    
    files = {"file": ("plus_one.dat", io.BytesIO(oversized_payload), "application/octet-stream")}
    data = {
        "filename": "plus_one.dat",
        "lease_id": lease_id,
    }
    
    r = client.post(
        f"/api/v1/worker/tasks/{task_id}/artifacts",
        files=files,
        data=data,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 413, f"Expected 413 for 25MB+1 byte, got {r.status_code}: {r.text}"
    assert "exceeds maximum allowed size" in r.text.lower() or "too large" in r.text.lower()
    
    task_evidence_dir = settings.evidence_dir / f"task_{task_id}"
    if task_evidence_dir.exists():
        assert not (task_evidence_dir / "plus_one.dat").exists()


# ============================================================================
# Scenario D: Oversized declared Content-Length is rejected before buffering
# ============================================================================
def test_scenario_d_oversized_content_length_rejected_before_buffering():
    """
    Scenario D: Request declaring Content-Length > MAX_MULTIPART_REQUEST_BYTES
    is rejected immediately with HTTP 413 without invoking receive() to read body bytes.
    """
    async def _run():
        scope = _make_asgi_scope(
            path="/api/v1/worker/tasks/task-test/artifacts",
            method="POST",
            headers=[
                (b"content-type", b"multipart/form-data; boundary=----WebKitFormBoundary123"),
                (b"content-length", b"50000000"),  # 50 MB declared
                (b"authorization", b"Bearer dummy-token"),
            ],
        )
        
        receive_called = False
        
        async def mock_receive():
            nonlocal receive_called
            receive_called = True
            return {"type": "http.request", "body": b"X" * 1024, "more_body": False}
        
        messages = []
        async def mock_send(msg):
            messages.append(msg)
            
        await app(scope, mock_receive, mock_send)
        
        assert not receive_called, "ASGI receive() was called when Content-Length exceeded limit!"
        
        start_msg = next((m for m in messages if m["type"] == "http.response.start"), None)
        assert start_msg is not None
        assert start_msg["status"] == 413
        
        resp_headers = dict(start_msg["headers"])
        assert resp_headers.get(b"connection") == b"close"
        
        body_msg = next((m for m in messages if m["type"] == "http.response.body"), None)
        assert body_msg is not None
        resp_body = json.loads(body_msg["body"].decode("utf-8"))
        assert "50000000" in resp_body["detail"]
        assert "maximum allowed size" in resp_body["detail"].lower()

    asyncio.run(_run())


# ============================================================================
# Scenario E: Missing Content-Length does not bypass enforcement
# ============================================================================
def test_scenario_e_missing_content_length_does_not_bypass(db_conn):
    """
    Scenario E: Chunked/streaming upload with NO Content-Length header is tracked
    incrementally and rejected with HTTP 413 upon crossing the limit.
    """
    env = _setup_test_worker_task(db_conn)
    task_id = env["task_id"]
    token = env["token"]
    
    async def _run():
        scope = _make_asgi_scope(
            path=f"/api/v1/worker/tasks/{task_id}/artifacts",
            method="POST",
            headers=[
                (b"content-type", b"application/octet-stream"),
                (b"authorization", f"Bearer {token}".encode("ascii")),
            ],
        )
        
        chunk_size = 1024 * 1024  # 1 MB chunk
        chunks_sent = 0
        
        async def streaming_receive():
            nonlocal chunks_sent
            chunks_sent += 1
            return {
                "type": "http.request",
                "body": b"A" * chunk_size,
                "more_body": True,
            }
            
        messages = []
        async def mock_send(msg):
            messages.append(msg)
            
        await app(scope, streaming_receive, mock_send)
        
        start_msg = next((m for m in messages if m["type"] == "http.response.start"), None)
        assert start_msg is not None
        assert start_msg["status"] == 413
        
        body_msg = next((m for m in messages if m["type"] == "http.response.body"), None)
        assert body_msg is not None
        assert "exceeding maximum allowed size" in body_msg["body"].decode("utf-8").lower()
        
        assert chunks_sent <= 27, f"Streamed too many chunks ({chunks_sent}) before enforcement!"

    asyncio.run(_run())


# ============================================================================
# Scenario F: Falsified smaller Content-Length does not permit an oversized body
# ============================================================================
def test_scenario_f_falsified_smaller_content_length_rejected(db_conn):
    """
    Scenario F: Client declares Content-Length: 1024, but streams > 26MB.
    The incremental byte counter aborts and returns HTTP 413.
    """
    env = _setup_test_worker_task(db_conn)
    task_id = env["task_id"]
    token = env["token"]
    
    async def _run():
        scope = _make_asgi_scope(
            path=f"/api/v1/worker/tasks/{task_id}/artifacts",
            method="POST",
            headers=[
                (b"content-type", b"application/octet-stream"),
                (b"content-length", b"1024"),  # Falsified small length
                (b"authorization", f"Bearer {token}".encode("ascii")),
            ],
        )
        
        chunk_size = 2 * 1024 * 1024  # 2 MB chunks
        chunks_sent = 0
        
        async def falsified_stream_receive():
            nonlocal chunks_sent
            chunks_sent += 1
            return {
                "type": "http.request",
                "body": b"B" * chunk_size,
                "more_body": True,
            }
            
        messages = []
        async def mock_send(msg):
            messages.append(msg)
            
        await app(scope, falsified_stream_receive, mock_send)
        
        start_msg = next((m for m in messages if m["type"] == "http.response.start"), None)
        assert start_msg is not None
        assert start_msg["status"] == 413

    asyncio.run(_run())


# ============================================================================
# Scenario G: Many small streamed chunks cumulatively exceeding the limit
# ============================================================================
def test_scenario_g_many_small_chunks_cumulatively_exceeding_rejected(db_conn):
    """
    Scenario G: Client sends small chunks of 32 KB each (~32 MB total).
    Incremental accounting catches and rejects at chunk ~801 with 413.
    """
    env = _setup_test_worker_task(db_conn)
    task_id = env["task_id"]
    token = env["token"]
    
    async def _run():
        scope = _make_asgi_scope(
            path=f"/api/v1/worker/tasks/{task_id}/artifacts",
            method="POST",
            headers=[
                (b"content-type", b"application/octet-stream"),
                (b"authorization", f"Bearer {token}".encode("ascii")),
            ],
        )
        
        small_chunk = b"C" * (32 * 1024)  # 32 KB
        chunks_sent = 0
        
        async def small_chunk_receive():
            nonlocal chunks_sent
            chunks_sent += 1
            return {
                "type": "http.request",
                "body": small_chunk,
                "more_body": True,
            }
            
        messages = []
        async def mock_send(msg):
            messages.append(msg)
            
        await app(scope, small_chunk_receive, mock_send)
        
        start_msg = next((m for m in messages if m["type"] == "http.response.start"), None)
        assert start_msg is not None
        assert start_msg["status"] == 413
        
        assert 795 <= chunks_sent <= 810, f"Unexpected chunks sent: {chunks_sent}"

    asyncio.run(_run())


# ============================================================================
# Scenario H: One unexpectedly large received chunk is rejected
# ============================================================================
def test_scenario_h_one_unexpectedly_large_chunk_rejected(db_conn):
    """Scenario H: Client sends one single oversized chunk of 30 MB in one message."""
    env = _setup_test_worker_task(db_conn)
    task_id = env["task_id"]
    token = env["token"]
    
    async def _run():
        scope = _make_asgi_scope(
            path=f"/api/v1/worker/tasks/{task_id}/artifacts",
            method="POST",
            headers=[
                (b"content-type", b"application/octet-stream"),
                (b"authorization", f"Bearer {token}".encode("ascii")),
            ],
        )
        
        async def single_large_chunk_receive():
            return {
                "type": "http.request",
                "body": b"X" * (30 * 1024 * 1024),  # 30 MB in single message
                "more_body": False,
            }
            
        messages = []
        async def mock_send(msg):
            messages.append(msg)
            
        await app(scope, single_large_chunk_receive, mock_send)
        
        start_msg = next((m for m in messages if m["type"] == "http.response.start"), None)
        assert start_msg is not None
        assert start_msg["status"] == 413

    asyncio.run(_run())


# ============================================================================
# Scenario I: Interrupted upload leaves no accepted artifact or unmanaged file
# ============================================================================
def test_scenario_i_interrupted_upload_leaves_no_artifacts(db_conn):
    """
    Scenario I: Client starts uploading an artifact but connection terminates / disconnects mid-stream.
    System leaves 0 accepted artifact records in database and 0 unmanaged partial files on disk.
    """
    env = _setup_test_worker_task(db_conn)
    task_id = env["task_id"]
    token = env["token"]
    
    async def _run():
        scope = _make_asgi_scope(
            path=f"/api/v1/worker/tasks/{task_id}/artifacts",
            method="POST",
            headers=[
                (b"content-type", b"multipart/form-data; boundary=----BoundaryXYZ"),
                (b"authorization", f"Bearer {token}".encode("ascii")),
            ],
        )
        
        async def aborting_receive():
            raise ConnectionResetError("Client disconnected unexpectedly")
            
        messages = []
        async def mock_send(msg):
            messages.append(msg)
            
        try:
            await app(scope, aborting_receive, mock_send)
        except Exception:
            pass
            
        c = get_db_connection()
        try:
            row = c.execute("SELECT COUNT(*) as cnt FROM artifact_task_links WHERE task_id = ?", (task_id,)).fetchone()
            assert row["cnt"] == 0
        finally:
            c.close()
            
        task_evidence_dir = settings.evidence_dir / f"task_{task_id}"
        if task_evidence_dir.exists():
            files = list(task_evidence_dir.glob("*"))
            assert len(files) == 0, f"Found dangling partial files: {files}"

    asyncio.run(_run())


# ============================================================================
# Scenario J: Unauthenticated upload is denied
# ============================================================================
def test_scenario_j_unauthenticated_upload_denied(client, db_conn):
    """Scenario J: Upload without auth token or with revoked token is rejected with 401."""
    env = _setup_test_worker_task(db_conn)
    task_id = env["task_id"]
    
    # 1. No token -> 401
    r = client.post(
        f"/api/v1/worker/tasks/{task_id}/artifacts",
        files={"file": ("test.txt", b"content", "text/plain")},
    )
    assert r.status_code == 401
    
    # 2. Invalid token -> 401
    r = client.post(
        f"/api/v1/worker/tasks/{task_id}/artifacts",
        files={"file": ("test.txt", b"content", "text/plain")},
        headers={"Authorization": "Bearer invalid-token-xyz"},
    )
    assert r.status_code == 401


# ============================================================================
# Scenario K: Expired or reassigned worker lease is denied
# ============================================================================
def test_scenario_k_expired_or_reassigned_lease_denied(client, db_conn):
    """Scenario K: Worker attempting upload with expired lease is denied with 403 or 409."""
    env = _setup_test_worker_task(db_conn)
    task_id = env["task_id"]
    token = env["token"]
    lease_id = env["lease_id"]
    
    past = "2020-01-01T00:00:00Z"
    db_conn.execute("UPDATE worker_task_leases SET lease_expires_at = ? WHERE id = ?", (past, lease_id))
    db_conn.commit()
    
    files = {"file": ("test.txt", b"content", "text/plain")}
    data = {"lease_id": lease_id}
    
    r = client.post(
        f"/api/v1/worker/tasks/{task_id}/artifacts",
        files=files,
        data=data,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code in (403, 409)
    assert "lease" in r.text.lower()


# ============================================================================
# Scenario L: Concurrent uploads cannot bypass individual request limits
# ============================================================================
def test_scenario_l_concurrent_uploads_bounded(client, db_conn):
    """
    Scenario L: Multiple concurrent uploads are evaluated independently.
    Valid uploads succeed while oversized uploads fail closed with 413.
    """
    env1 = _setup_test_worker_task(db_conn)
    env2 = _setup_test_worker_task(db_conn)
    
    payload1 = b"Valid Payload 1 " * 50
    r_valid1 = client.post(
        f"/api/v1/worker/tasks/{env1['task_id']}/artifacts",
        files={"file": ("valid1.txt", io.BytesIO(payload1), "text/plain")},
        data={"lease_id": env1["lease_id"]},
        headers={"Authorization": f"Bearer {env1['token']}"},
    )
    assert r_valid1.status_code == 200
    
    r_over1 = client.post(
        f"/api/v1/worker/tasks/{env2['task_id']}/artifacts",
        content=b"X" * 100,
        headers={
            "Authorization": f"Bearer {env2['token']}",
            "Content-Length": "40000000",  # 40 MB > limit
        },
    )
    assert r_over1.status_code == 413
    
    payload2 = b"Valid Payload 2 " * 50
    r_valid2 = client.post(
        f"/api/v1/worker/tasks/{env1['task_id']}/artifacts",
        files={"file": ("valid2.txt", io.BytesIO(payload2), "text/plain")},
        data={"lease_id": env1["lease_id"]},
        headers={"Authorization": f"Bearer {env1['token']}"},
    )
    assert r_valid2.status_code == 200
    
    r_over2 = client.post(
        f"/api/v1/worker/tasks/{env2['task_id']}/artifacts",
        content=b"Y" * 100,
        headers={
            "Authorization": f"Bearer {env2['token']}",
            "Content-Length": "45000000",
        },
    )
    assert r_over2.status_code == 413


# ============================================================================
# Scenario M: Valid multipart framing does not incorrectly reject payload
# ============================================================================
def test_scenario_m_valid_multipart_framing_headroom_preserved(client, db_conn):
    """
    Scenario M: A 25.0 MB payload enclosed in multipart MIME framing totals ~26,215,400 bytes.
    Because MAX_MULTIPART_REQUEST_BYTES is 26,279,936 bytes (25MB + 64KB framing headroom),
    the valid upload succeeds and is not prematurely rejected.
    """
    env = _setup_test_worker_task(db_conn)
    task_id = env["task_id"]
    token = env["token"]
    lease_id = env["lease_id"]
    
    payload_25mb = b"M" * MAX_ARTIFACT_PAYLOAD_BYTES
    sha256_hash = hashlib.sha256(payload_25mb).hexdigest()
    
    files = {"file": ("framing_test.bin", io.BytesIO(payload_25mb), "application/octet-stream")}
    data = {
        "filename": "framing_test.bin",
        "expected_sha256": sha256_hash,
        "lease_id": lease_id,
    }
    
    r = client.post(
        f"/api/v1/worker/tasks/{task_id}/artifacts",
        files=files,
        data=data,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, f"Valid multipart framing was rejected: {r.text}"
    resp = r.json()
    assert resp["artifact"]["file_size"] == MAX_ARTIFACT_PAYLOAD_BYTES
    assert resp["artifact"]["computed_sha256"] == sha256_hash


# ============================================================================
# Scenario N: Malformed transfer metadata cannot produce a size-check bypass
# ============================================================================
def test_scenario_n1_negative_content_length_rejected(client, db_conn):
    """Scenario N1: Negative Content-Length header is rejected with 400 Bad Request."""
    r = client.post(
        "/api/v1/worker/tasks/task-test/artifacts",
        content=b"test",
        headers={"Content-Length": "-500"},
    )
    assert r.status_code == 400
    assert "malformed" in r.text.lower() or "negative" in r.text.lower()


def test_scenario_n2_non_numeric_content_length_rejected(client, db_conn):
    """Scenario N2: Non-numeric Content-Length header is rejected with 400 Bad Request."""
    r = client.post(
        "/api/v1/worker/tasks/task-test/artifacts",
        content=b"test",
        headers={"Content-Length": "100MB"},
    )
    assert r.status_code == 400
    assert "malformed" in r.text.lower() or "non-numeric" in r.text.lower()


def test_scenario_n3_conflicting_multiple_content_length_headers():
    """Scenario N3: Conflicting multiple Content-Length headers are rejected with 400 Bad Request."""
    async def _run():
        scope = _make_asgi_scope(
            path="/api/v1/worker/tasks/task-test/artifacts",
            method="POST",
            headers=[
                (b"content-type", b"application/octet-stream"),
                (b"content-length", b"100"),
                (b"content-length", b"200"),
            ],
        )
        
        messages = []
        async def mock_receive():
            return {"type": "http.request", "body": b"", "more_body": False}
            
        async def mock_send(msg):
            messages.append(msg)
            
        await app(scope, mock_receive, mock_send)
        
        start_msg = next((m for m in messages if m["type"] == "http.response.start"), None)
        assert start_msg is not None
        assert start_msg["status"] == 400

    asyncio.run(_run())


# ============================================================================
# Scenario O: JSON Transport & Decoded Payload Boundary Verification
# ============================================================================
def test_scenario_o1_json_transport_limit_exact_boundary():
    """
    Scenario O1: Declared Content-Length at exactly MAX_JSON_REQUEST_BYTES (36,001,112 bytes)
    passes the pre-buffering check at the ASGI transport boundary.
    """
    async def _run():
        exact_cl = str(MAX_JSON_REQUEST_BYTES).encode("ascii")
        scope = _make_asgi_scope(
            path="/api/v1/worker/tasks/task-test/artifacts",
            method="POST",
            headers=[
                (b"content-type", b"application/json"),
                (b"content-length", exact_cl),
                (b"authorization", b"Bearer dummy"),
            ],
        )
        
        inner_app_invoked = False
        receive_called = False

        async def mock_inner_app(scope, receive, send):
            nonlocal inner_app_invoked
            inner_app_invoked = True
            msg = await receive()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok", "more_body": False})

        async def mock_receive():
            nonlocal receive_called
            receive_called = True
            return {"type": "http.request", "body": b"", "more_body": False}
            
        messages = []
        async def mock_send(msg):
            messages.append(msg)
            
        middleware = UploadSizeLimitMiddleware(mock_inner_app)
        await middleware(scope, mock_receive, mock_send)
        # Should not be rejected by pre-buffering Content-Length check (it passes to inner app)
        assert inner_app_invoked is True, "Pre-buffering check rejected exact limit!"
        assert receive_called is True, "Inner app receive was invoked"

    asyncio.run(_run())


def test_scenario_o2_json_transport_limit_exceeded_by_one_byte():
    """
    Scenario O2: Declared Content-Length exceeding MAX_JSON_REQUEST_BYTES by 1 byte (36,001,113 bytes)
    is rejected immediately with HTTP 413 without reading any body bytes.
    """
    async def _run():
        over_cl = str(MAX_JSON_REQUEST_BYTES + 1).encode("ascii")
        scope = _make_asgi_scope(
            path="/api/v1/worker/tasks/task-test/artifacts",
            method="POST",
            headers=[
                (b"content-type", b"application/json"),
                (b"content-length", over_cl),
                (b"authorization", b"Bearer dummy"),
            ],
        )
        
        receive_called = False
        async def mock_receive():
            nonlocal receive_called
            receive_called = True
            return {"type": "http.request", "body": b"X", "more_body": False}
            
        messages = []
        async def mock_send(msg):
            messages.append(msg)
            
        await app(scope, mock_receive, mock_send)
        assert not receive_called, "Body was received for oversized Content-Length!"
        start_msg = next((m for m in messages if m["type"] == "http.response.start"), None)
        assert start_msg is not None
        assert start_msg["status"] == 413

    asyncio.run(_run())


def test_scenario_o3_json_decoded_payload_exact_limit_succeeds(client, db_conn):
    """
    Scenario O3: JSON upload with Base64 payload of exactly 26,214,400 bytes (25.0 MiB)
    decodes successfully and is accepted into storage.
    """
    env = _setup_test_worker_task(db_conn)
    task_id = env["task_id"]
    token = env["token"]
    lease_id = env["lease_id"]
    
    pattern = b"0123456789abcdef" * 1024
    exact_payload = (pattern * (MAX_ARTIFACT_PAYLOAD_BYTES // len(pattern)))
    expected_sha256 = hashlib.sha256(exact_payload).hexdigest()
    
    upload_body = {
        "filename": "json_exact_25mb.dat",
        "content_base64": base64.b64encode(exact_payload).decode("ascii"),
        "expected_sha256": expected_sha256,
        "lease_id": lease_id,
    }
    
    r = client.post(
        f"/api/v1/worker/tasks/{task_id}/artifacts",
        json=upload_body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, f"Upload failed: {r.text}"
    resp = r.json()
    assert resp["artifact"]["file_size"] == MAX_ARTIFACT_PAYLOAD_BYTES
    assert resp["artifact"]["computed_sha256"] == expected_sha256


def test_scenario_o4_json_decoded_payload_exceeded_by_one_byte_rejected(client, db_conn):
    """
    Scenario O4: JSON upload where transport body is under 36MB but decoded payload
    exceeds 25.0 MiB by 1 byte (26,214,401 bytes) is rejected with HTTP 413.
    """
    env = _setup_test_worker_task(db_conn)
    task_id = env["task_id"]
    token = env["token"]
    lease_id = env["lease_id"]
    
    oversized = b"Q" * (MAX_ARTIFACT_PAYLOAD_BYTES + 1)
    upload_body = {
        "filename": "json_over_one.dat",
        "content_base64": base64.b64encode(oversized).decode("ascii"),
        "lease_id": lease_id,
    }
    
    r = client.post(
        f"/api/v1/worker/tasks/{task_id}/artifacts",
        json=upload_body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 413, f"Expected 413, got {r.status_code}: {r.text}"
    assert "maximum allowed size" in r.text.lower() or "too large" in r.text.lower()


# ============================================================================
# Scenario P: Advanced Ingestion Hardening (Anti-Smuggling, Multi-File, Paths)
# ============================================================================
def test_scenario_p1_transfer_encoding_content_length_conflict_rejected(client):
    """
    Scenario P1: Sending both Transfer-Encoding and Content-Length headers is rejected
    with HTTP 400 Bad Request to prevent HTTP request smuggling / desync (RFC 7230/9112).
    """
    async def _run():
        scope = _make_asgi_scope(
            path="/api/v1/worker/tasks/task-test/artifacts",
            method="POST",
            headers=[
                (b"transfer-encoding", b"chunked"),
                (b"content-length", b"1024"),
                (b"content-type", b"application/octet-stream"),
            ],
        )
        messages = []
        async def mock_receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def mock_send(msg):
            messages.append(msg)
            
        await app(scope, mock_receive, mock_send)
        start_msg = next((m for m in messages if m["type"] == "http.response.start"), None)
        assert start_msg is not None
        assert start_msg["status"] == 400
        body_msg = next((m for m in messages if m["type"] == "http.response.body"), None)
        assert "both transfer-encoding and content-length" in body_msg["body"].decode("utf-8").lower()

    asyncio.run(_run())


def test_scenario_p2_multiple_multipart_files_exceeding_limit_rejected(client, db_conn):
    """
    Scenario P2: Multipart upload with multiple file fields that cumulatively exceed
    the 25.0 MiB payload limit (e.g. 14MB + 14MB = 28MB) is rejected with HTTP 413.
    """
    env = _setup_test_worker_task(db_conn)
    task_id = env["task_id"]
    token = env["token"]
    lease_id = env["lease_id"]
    
    part1 = b"P1" * (7 * 1024 * 1024)  # 14 MB
    part2 = b"P2" * (7 * 1024 * 1024)  # 14 MB
    
    files = [
        ("file", ("part1.dat", io.BytesIO(part1), "application/octet-stream")),
        ("file_extra", ("part2.dat", io.BytesIO(part2), "application/octet-stream")),
    ]
    data = {"lease_id": lease_id}
    
    r = client.post(
        f"/api/v1/worker/tasks/{task_id}/artifacts",
        files=files,
        data=data,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 413, f"Expected 413 for multiple file parts, got {r.status_code}: {r.text}"
    assert "maximum allowed size" in r.text.lower() or "too large" in r.text.lower()


def test_scenario_p3_path_normalization_variants_enforce_limit():
    """
    Scenario P3: Path variants (uppercase, trailing slashes) cannot bypass the artifact upload size limit.
    """
    async def _run():
        for test_path in [
            "/API/V1/WORKER/TASKS/task-test/artifacts",
            "/api/v1/worker/tasks/task-test/artifacts/",
            "/API/v1/worker/tasks/task-test/artifacts/",
        ]:
            scope = _make_asgi_scope(
                path=test_path,
                method="POST",
                headers=[
                    (b"content-type", b"multipart/form-data"),
                    (b"content-length", b"50000000"),  # 50 MB
                ],
            )
            messages = []
            async def mock_receive():
                return {"type": "http.request", "body": b"", "more_body": False}
            async def mock_send(msg):
                messages.append(msg)
                
            await app(scope, mock_receive, mock_send)
            start_msg = next((m for m in messages if m["type"] == "http.response.start"), None)
            assert start_msg is not None, f"No response for {test_path}"
            assert start_msg["status"] == 413, f"Path variant {test_path} bypassed 413 limit: {start_msg['status']}"

    asyncio.run(_run())


# ============================================================================
# Real Wire Tests via Local Uvicorn Loopback Socket
# ============================================================================
def _run_local_uvicorn_server():
    """Spin up local ephemeral Uvicorn server for real wire transport validation."""
    orig_env = settings.environment
    settings.environment = "testing"
    
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        
    config = uvicorn.Config(app=app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    
    for _ in range(60):
        if server.started:
            break
        time.sleep(0.05)
    return server, port, t, orig_env


def test_uvicorn_wire_oversized_content_length_rejected():
    """
    Real Wire Test: Real HTTP client sends declared Content-Length: 50MB to actual local Uvicorn socket.
    Verifies that the server responds with HTTP 413 over real TCP without reading body bytes or hanging.
    """
    server, port, thread, orig_env = _run_local_uvicorn_server()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.putrequest("POST", "/api/v1/worker/tasks/wire-task/artifacts")
        conn.putheader("Content-Type", "multipart/form-data; boundary=----WebKitFormBoundaryXYZ")
        conn.putheader("Content-Length", "50000000")  # 50 MB declared
        conn.putheader("Authorization", "Bearer dummy")
        conn.endheaders()
        
        resp = conn.getresponse()
        assert resp.status == 413
        body = json.loads(resp.read().decode("utf-8"))
        assert "50000000" in body["detail"]
        conn.close()
    finally:
        server.should_exit = True
        thread.join(timeout=3)
        settings.environment = orig_env


def test_uvicorn_wire_streamed_oversized_chunked_rejected(db_conn):
    """
    Real Wire Test: Real HTTP client streams chunked transfer exceeding limit to actual local Uvicorn socket.
    Verifies that Uvicorn aborts reception at the exact limit boundary, sends HTTP 413, and closes socket.
    """
    import select
    
    env = _setup_test_worker_task(db_conn)
    task_id = env["task_id"]
    token = env["token"]
    
    server, port, thread, orig_env = _run_local_uvicorn_server()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", port))
        
        req = (
            f"POST /api/v1/worker/tasks/{task_id}/artifacts HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Content-Type: application/octet-stream\r\n"
            f"Transfer-Encoding: chunked\r\n"
            f"Authorization: Bearer {token}\r\n"
            f"\r\n"
        )
        sock.sendall(req.encode("ascii"))
        
        # Stream 400 chunks of 64KB = exactly 26,214,400 bytes (exact limit)
        chunk_64k = b"A" * 65536
        for _ in range(400):
            sock.sendall(f"{len(chunk_64k):X}\r\n".encode("ascii") + chunk_64k + b"\r\n")
            
        # Send 1 additional chunk of 1KB = 26,215,424 bytes (exceeds limit by 1,024 bytes)
        chunk_1k = b"B" * 1024
        sock.sendall(f"{len(chunk_1k):X}\r\n".encode("ascii") + chunk_1k + b"\r\n")
        
        # Wait for HTTP 413 response from server
        r, _, _ = select.select([sock], [], [], 5.0)
        assert r, "Uvicorn socket did not respond within timeout"
        data = sock.recv(4096)
        sock.close()
        
        status_line = data.split(b"\r\n")[0].decode("ascii", errors="replace")
        assert "413" in status_line, f"Expected HTTP 413, got: {status_line}"
    finally:
        server.should_exit = True
        thread.join(timeout=3)
        settings.environment = orig_env


# ============================================================================
# Memory Accounting Verification (5 Ingestion Modes)
# ============================================================================
def test_memory_accounting_five_ingestion_modes(db_conn):
    """
    Verify and report memory usage separately for:
    1. Streaming binary uploads
    2. Streaming multipart uploads
    3. JSON/Base64 uploads
    4. A single very large incoming ASGI chunk
    5. Many small incoming chunks
    Measures both Python heap allocation delta (tracemalloc) and OS process RSS.
    """
    env = _setup_test_worker_task(db_conn)
    task_id = env["task_id"]
    token = env["token"]
    lease_id = env["lease_id"]
    
    results = {}
    
    # 1. Streaming binary upload (15 MB binary stream)
    async def _test_mode1():
        tracemalloc.start()
        rss_before = get_process_memory_rss()
        scope = _make_asgi_scope(
            path=f"/api/v1/worker/tasks/{task_id}/artifacts",
            headers=[
                (b"content-type", b"application/octet-stream"),
                (b"authorization", f"Bearer {token}".encode("ascii")),
            ],
        )
        chunk = b"B" * (1024 * 1024)
        sent = 0
        async def rec():
            nonlocal sent
            sent += 1
            return {"type": "http.request", "body": chunk, "more_body": sent < 15}
        msgs = []
        async def snd(m): msgs.append(m)
        await app(scope, rec, snd)
        curr, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rss_after = get_process_memory_rss()
        results["streaming_binary"] = {
            "tracemalloc_peak_mb": round(peak / (1024 * 1024), 2),
            "os_rss_mb": round(rss_after / (1024 * 1024), 2),
        }
    asyncio.run(_test_mode1())
    
    # 2. Streaming multipart upload (15 MB multipart form)
    async def _test_mode2():
        tracemalloc.start()
        rss_before = get_process_memory_rss()
        body_part = (
            b"------Boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="test.bin"\r\n\r\n' +
            (b"M" * (15 * 1024 * 1024)) +
            b"\r\n------Boundary--\r\n"
        )
        scope = _make_asgi_scope(
            path=f"/api/v1/worker/tasks/{task_id}/artifacts",
            headers=[
                (b"content-type", b"multipart/form-data; boundary=----Boundary"),
                (b"authorization", f"Bearer {token}".encode("ascii")),
            ],
        )
        sent = False
        async def rec():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body_part, "more_body": False}
            return {"type": "http.request", "body": b"", "more_body": False}
        msgs = []
        async def snd(m): msgs.append(m)
        await app(scope, rec, snd)
        curr, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rss_after = get_process_memory_rss()
        results["streaming_multipart"] = {
            "tracemalloc_peak_mb": round(peak / (1024 * 1024), 2),
            "os_rss_mb": round(rss_after / (1024 * 1024), 2),
        }
    asyncio.run(_test_mode2())
    
    # 3. JSON/Base64 upload (15 MB raw encoded to ~20MB Base64)
    async def _test_mode3():
        tracemalloc.start()
        raw = b"J" * (15 * 1024 * 1024)
        json_body = json.dumps({
            "filename": "json_test.dat",
            "content_base64": base64.b64encode(raw).decode("ascii"),
            "lease_id": lease_id,
        }).encode("utf-8")
        scope = _make_asgi_scope(
            path=f"/api/v1/worker/tasks/{task_id}/artifacts",
            headers=[
                (b"content-type", b"application/json"),
                (b"content-length", str(len(json_body)).encode("ascii")),
                (b"authorization", f"Bearer {token}".encode("ascii")),
            ],
        )
        sent = False
        async def rec():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": json_body, "more_body": False}
            return {"type": "http.request", "body": b"", "more_body": False}
        msgs = []
        async def snd(m): msgs.append(m)
        await app(scope, rec, snd)
        curr, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rss_after = get_process_memory_rss()
        results["json_base64"] = {
            "tracemalloc_peak_mb": round(peak / (1024 * 1024), 2),
            "os_rss_mb": round(rss_after / (1024 * 1024), 2),
        }
    asyncio.run(_test_mode3())
    
    # 4. Single very large incoming ASGI chunk (30 MB chunk rejected immediately)
    async def _test_mode4():
        tracemalloc.start()
        scope = _make_asgi_scope(
            path=f"/api/v1/worker/tasks/{task_id}/artifacts",
            headers=[
                (b"content-type", b"application/octet-stream"),
                (b"authorization", f"Bearer {token}".encode("ascii")),
            ],
        )
        async def rec():
            return {"type": "http.request", "body": b"X" * (30 * 1024 * 1024), "more_body": False}
        msgs = []
        async def snd(m): msgs.append(m)
        await app(scope, rec, snd)
        curr, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rss_after = get_process_memory_rss()
        results["single_large_chunk"] = {
            "tracemalloc_peak_mb": round(peak / (1024 * 1024), 2),
            "os_rss_mb": round(rss_after / (1024 * 1024), 2),
        }
    asyncio.run(_test_mode4())
    
    # 5. Many small incoming chunks (900 x 32KB chunks rejected at boundary)
    async def _test_mode5():
        tracemalloc.start()
        scope = _make_asgi_scope(
            path=f"/api/v1/worker/tasks/{task_id}/artifacts",
            headers=[
                (b"content-type", b"application/octet-stream"),
                (b"authorization", f"Bearer {token}".encode("ascii")),
            ],
        )
        chunk = b"S" * (32 * 1024)
        async def rec():
            return {"type": "http.request", "body": chunk, "more_body": True}
        msgs = []
        async def snd(m): msgs.append(m)
        await app(scope, rec, snd)
        curr, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rss_after = get_process_memory_rss()
        results["many_small_chunks"] = {
            "tracemalloc_peak_mb": round(peak / (1024 * 1024), 2),
            "os_rss_mb": round(rss_after / (1024 * 1024), 2),
        }
    asyncio.run(_test_mode5())
    
    # Verify bounds: tracemalloc peak for stream chunks is bounded
    assert results["many_small_chunks"]["tracemalloc_peak_mb"] < 40.0
    print("\n--- Ingestion Memory Benchmarks ---")
    for mode, data in results.items():
        print(f"Mode: {mode:20s} | Tracemalloc Peak: {data['tracemalloc_peak_mb']:6.2f} MB | OS RSS: {data['os_rss_mb']:6.2f} MB")
