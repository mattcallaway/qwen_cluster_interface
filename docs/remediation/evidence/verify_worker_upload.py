"""
Isolated verification of worker artifact upload authentication, authorization, and integrity.
Tests:
1. No credentials -> Expect 401
2. Invalid token -> Expect 401
3. Valid worker token uploading to another worker's task -> Observed behavior
4. Expired/stale task lease -> Observed behavior
5. Invalid checksum -> Stored vs rejected
6. Repeated uploads -> Overwrite vs versioned immutability
"""
import os
import sys
import tempfile
import base64
import hashlib
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from starlette.testclient import TestClient

# Use temporary DB and evidence directories
tmpdir = tempfile.mkdtemp()
db_path = os.path.join(tmpdir, "test.db")
evidence_dir = os.path.join(tmpdir, "evidence")
artifacts_dir = os.path.join(tmpdir, "artifacts")

os.environ["DATABASE_PATH"] = db_path
os.environ["EVIDENCE_DIR"] = evidence_dir
os.environ["ARTIFACTS_DIR"] = artifacts_dir

from app.database import init_db, get_db_connection
from app.main import app
from app.services import worker as worker_svc

init_db()
client = TestClient(app)

results = {}

# 1. Test no credentials
resp_no_auth = client.post("/api/v1/worker/tasks/task-1/artifacts", json={
    "filename": "test.txt",
    "content_base64": base64.b64encode(b"hello").decode(),
})
results["1_no_credentials"] = {
    "status_code": resp_no_auth.status_code,
    "detail": resp_no_auth.json().get("detail") if resp_no_auth.status_code == 401 else resp_no_auth.text,
    "verdict": "REJECTED_401" if resp_no_auth.status_code == 401 else "ACCEPTED"
}

# 2. Test invalid credentials
resp_invalid_auth = client.post(
    "/api/v1/worker/tasks/task-1/artifacts",
    headers={"Authorization": "Bearer qcw_invalid_token_12345"},
    json={
        "filename": "test.txt",
        "content_base64": base64.b64encode(b"hello").decode(),
    }
)
results["2_invalid_credentials"] = {
    "status_code": resp_invalid_auth.status_code,
    "detail": resp_invalid_auth.json().get("detail") if resp_invalid_auth.status_code == 401 else resp_invalid_auth.text,
    "verdict": "REJECTED_401" if resp_invalid_auth.status_code == 401 else "ACCEPTED"
}

# Register Worker A
_, enr_a = worker_svc.create_enrollment_token(allowed_node_id="worker-A")
res_a = client.post("/api/v1/worker/register", json={
    "node_id": "worker-A",
    "name": "Worker Node A",
    "role": "vps_worker",
    "declared_capabilities": ["test_task"]
}, headers={"X-Enrollment-Token": enr_a})
token_a = res_a.json()["worker_token"]

# Register Worker B
_, enr_b = worker_svc.create_enrollment_token(allowed_node_id="worker-B")
res_b = client.post("/api/v1/worker/register", json={
    "node_id": "worker-B",
    "name": "Worker Node B",
    "role": "vps_worker",
    "declared_capabilities": ["test_task"]
}, headers={"X-Enrollment-Token": enr_b})
token_b = res_b.json()["worker_token"]

# Create a project and a task assigned to Worker A in DB
from uuid import uuid4
from app.services import projects as ps
proj = ps.create_project(f"Test Project {uuid4().hex[:6]}", owner="admin")
proj_id = proj["id"]

conn = get_db_connection()
conn.execute(
    "INSERT INTO tasks (id, project_id, title, status, assigned_worker, created_at, updated_at) VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
    ("task-assigned-to-A", proj_id, "Task A", "in_progress", "worker-A")
)
# Create an expired lease for Worker A
conn.execute(
    """
    INSERT INTO worker_task_leases (id, task_id, worker_id, idempotency_key, status, leased_at, lease_expires_at, heartbeat_at, attempt_count, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, datetime('now', '-2 hours'), datetime('now', '-1 hour'), datetime('now', '-2 hours'), 1, datetime('now', '-2 hours'), datetime('now', '-1 hour'))
    """,
    ("lease-expired", "task-assigned-to-A", "worker-A", "idemp-key-1", "expired")
)
conn.commit()
conn.close()

# 3. Test Valid Worker B uploading to Worker A's task
content_bytes_b = b"content from worker B"
resp_cross_worker = client.post(
    "/api/v1/worker/tasks/task-assigned-to-A/artifacts",
    headers={"Authorization": f"Bearer {token_b}"},
    json={
        "filename": "worker_b_file.txt",
        "content_base64": base64.b64encode(content_bytes_b).decode(),
        "expected_sha256": hashlib.sha256(content_bytes_b).hexdigest()
    }
)
results["3_cross_worker_upload"] = {
    "status_code": resp_cross_worker.status_code,
    "response": resp_cross_worker.json() if resp_cross_worker.status_code == 200 else resp_cross_worker.text,
    "verdict": "PERMITTED_CROSS_WORKER" if resp_cross_worker.status_code == 200 else "BLOCKED_403"
}

# 4. Test upload with expired lease (Worker A on task with expired lease)
content_bytes_a = b"content from worker A on expired lease"
resp_expired_lease = client.post(
    "/api/v1/worker/tasks/task-assigned-to-A/artifacts",
    headers={"Authorization": f"Bearer {token_a}"},
    json={
        "filename": "worker_a_file.txt",
        "content_base64": base64.b64encode(content_bytes_a).decode(),
        "expected_sha256": hashlib.sha256(content_bytes_a).hexdigest()
    }
)
results["4_expired_lease_upload"] = {
    "status_code": resp_expired_lease.status_code,
    "response": resp_expired_lease.json() if resp_expired_lease.status_code == 200 else resp_expired_lease.text,
    "verdict": "PERMITTED_ON_EXPIRED_LEASE" if resp_expired_lease.status_code == 200 else "BLOCKED_409"
}

# 5. Test invalid checksum
corrupted_bytes = b"real bytes"
bad_sha = "0000000000000000000000000000000000000000000000000000000000000000"
resp_bad_checksum = client.post(
    "/api/v1/worker/tasks/task-assigned-to-A/artifacts",
    headers={"Authorization": f"Bearer {token_a}"},
    json={
        "filename": "bad_checksum.txt",
        "content_base64": base64.b64encode(corrupted_bytes).decode(),
        "expected_sha256": bad_sha
    }
)
dest_path = os.path.join(evidence_dir, "task_task-assigned-to-A", "bad_checksum.txt")
file_written = os.path.exists(dest_path)
results["5_invalid_checksum"] = {
    "status_code": resp_bad_checksum.status_code,
    "file_written_to_disk": file_written,
    "response": resp_bad_checksum.json() if resp_bad_checksum.status_code == 200 else resp_bad_checksum.text,
    "verdict": "STORED_DESPITE_MISMATCH" if file_written and resp_bad_checksum.status_code == 200 else "REJECTED_400"
}

# 6. Test repeated uploads / file overwrite
first_content = b"version 1 content"
resp_upload_1 = client.post(
    "/api/v1/worker/tasks/task-assigned-to-A/artifacts",
    headers={"Authorization": f"Bearer {token_a}"},
    json={
        "filename": "overwrite_test.txt",
        "content_base64": base64.b64encode(first_content).decode(),
        "expected_sha256": hashlib.sha256(first_content).hexdigest()
    }
)
second_content = b"version 2 modified content"
resp_upload_2 = client.post(
    "/api/v1/worker/tasks/task-assigned-to-A/artifacts",
    headers={"Authorization": f"Bearer {token_a}"},
    json={
        "filename": "overwrite_test.txt",
        "content_base64": base64.b64encode(second_content).decode(),
        "expected_sha256": hashlib.sha256(second_content).hexdigest()
    }
)
overwrite_dest_path = os.path.join(evidence_dir, "task_task-assigned-to-A", "overwrite_test.txt")
final_content = open(overwrite_dest_path, "rb").read() if os.path.exists(overwrite_dest_path) else b""
results["6_repeated_upload_overwrite"] = {
    "status_code_1": resp_upload_1.status_code,
    "status_code_2": resp_upload_2.status_code,
    "final_file_content": final_content.decode(errors="ignore"),
    "overwritten": final_content == second_content,
    "verdict": "MUTABLY_OVERWRITTEN" if final_content == second_content else "IMMUTABLE_VERSIONED"
}

print(json.dumps(results, indent=2))
with open("docs/remediation/evidence/worker_upload_verification.json", "w") as f:
    json.dump(results, f, indent=2)
