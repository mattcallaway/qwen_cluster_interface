"""
Isolated, Self-Guarded Verification of Worker Artifact Upload Flaws (F-10).

Isolation Invariant:
- Must run in an isolated environment with QC_DATA_DIR set to a temporary folder.
- Hard guards inspect settings.data_dir, settings.db_path, and PRAGMA database_list.
- If ANY path points outside the temporary test root, the script immediately aborts before any DB init or HTTP request.
"""
import os
import sys
import tempfile
import base64
import hashlib
import json
from pathlib import Path

# 1. Create isolated temporary test root BEFORE importing any app modules
test_root = Path(tempfile.mkdtemp(prefix="qc_isolated_test_")).resolve()
os.environ["QC_DATA_DIR"] = str(test_root)
# Prevent loading persistent user env from ~/.qwen-collective/
fake_config = test_root / "isolated.env"
fake_config.write_text("# isolated test configuration\nQC_ENV=development\n", encoding="utf-8")
os.environ["QC_CONFIG_FILE"] = str(fake_config)

# Ensure repository root is on sys.path
repo_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(repo_root))

from app.config import settings
from app.database import init_db, get_db_connection

# 2. HARD ISOLATION GUARD: Verify effective paths before any operation
effective_data_dir = Path(settings.data_dir).resolve()
effective_db_path = Path(settings.db_path).resolve()
effective_evidence_dir = Path(settings.evidence_dir).resolve()
effective_artifacts_dir = Path(settings.artifacts_dir).resolve()

print(f"TEST ROOT: {test_root}")
print(f"EFFECTIVE DATA DIR: {effective_data_dir}")
print(f"EFFECTIVE DB PATH: {effective_db_path}")
print(f"EFFECTIVE EVIDENCE DIR: {effective_evidence_dir}")
print(f"EFFECTIVE ARTIFACTS DIR: {effective_artifacts_dir}")

def is_under_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False

if not (is_under_root(effective_data_dir, test_root) and
        is_under_root(effective_db_path, test_root) and
        is_under_root(effective_evidence_dir, test_root) and
        is_under_root(effective_artifacts_dir, test_root)):
    print("CRITICAL ISOLATION FAILURE: Effective paths point outside temporary test root! ABORTING.", file=sys.stderr)
    sys.exit(99)

# Initialize database in temporary environment
init_db()

# Verify active database via PRAGMA database_list
conn = get_db_connection()
db_list = conn.execute("PRAGMA database_list").fetchall()
conn.close()

print(f"PRAGMA database_list: {[dict(r) for r in db_list]}")
for row in db_list:
    opened_file = Path(row["file"]).resolve()
    if not is_under_root(opened_file, test_root):
        print(f"CRITICAL ISOLATION FAILURE: Database file {opened_file} is outside {test_root}! ABORTING.", file=sys.stderr)
        sys.exit(99)

print("ISOLATION VERIFIED: All storage destinations and databases are confirmed strictly inside temporary directory.")

# 3. Setup FastAPI TestClient
from starlette.testclient import TestClient
from app.main import app
from app.services import worker as worker_svc
from app.services import projects as ps

client = TestClient(app)

results = {
    "isolation_proof": {
        "test_root": str(test_root),
        "effective_db_path": str(effective_db_path),
        "effective_evidence_dir": str(effective_evidence_dir),
        "database_list": [dict(r) for r in db_list],
    },
    "cases": {}
}

# ---------------------------------------------------------------------------
# Case 1: Upload with No Credentials -> Expected 401
# ---------------------------------------------------------------------------
resp_no_auth = client.post("/api/v1/worker/tasks/task-1/artifacts", json={
    "filename": "test.txt",
    "content_base64": base64.b64encode(b"hello").decode(),
})
results["cases"]["1_no_credentials"] = {
    "expected_status": 401,
    "actual_status": resp_no_auth.status_code,
    "detail": resp_no_auth.json().get("detail") if resp_no_auth.status_code == 401 else resp_no_auth.text,
    "pass": resp_no_auth.status_code == 401,
    "finding_verdict": "AUTHENTICATION_REQUIRED_401"
}

# ---------------------------------------------------------------------------
# Case 2: Upload with Invalid Token -> Expected 401
# ---------------------------------------------------------------------------
resp_invalid_auth = client.post(
    "/api/v1/worker/tasks/task-1/artifacts",
    headers={"Authorization": "Bearer qcw_invalid_token_12345"},
    json={
        "filename": "test.txt",
        "content_base64": base64.b64encode(b"hello").decode(),
    }
)
results["cases"]["2_invalid_credentials"] = {
    "expected_status": 401,
    "actual_status": resp_invalid_auth.status_code,
    "detail": resp_invalid_auth.json().get("detail") if resp_invalid_auth.status_code == 401 else resp_invalid_auth.text,
    "pass": resp_invalid_auth.status_code == 401,
    "finding_verdict": "INVALID_TOKEN_REJECTED_401"
}

# Register Worker A and Worker B in the isolated test environment
_, enr_a = worker_svc.create_enrollment_token(allowed_node_id="worker-A")
res_a = client.post("/api/v1/worker/register", json={
    "node_id": "worker-A",
    "name": "Worker Node A",
    "role": "vps_worker",
    "declared_capabilities": ["test_task"]
}, headers={"X-Enrollment-Token": enr_a})
token_a = res_a.json()["worker_token"]

_, enr_b = worker_svc.create_enrollment_token(allowed_node_id="worker-B")
res_b = client.post("/api/v1/worker/register", json={
    "node_id": "worker-B",
    "name": "Worker Node B",
    "role": "vps_worker",
    "declared_capabilities": ["test_task"]
}, headers={"X-Enrollment-Token": enr_b})
token_b = res_b.json()["worker_token"]

# Create an isolated project and a task assigned to Worker A
proj = ps.create_project("Isolated Test Project", owner="admin")
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

# ---------------------------------------------------------------------------
# Case 3: Valid Worker B uploads to Worker A's task -> Vulnerability Test
# Expected Secure Behavior: 403 Forbidden
# Observed Vulnerable Behavior: 200 OK (Missing task authorization check)
# ---------------------------------------------------------------------------
content_bytes_b = b"payload from worker B to task assigned to worker A"
resp_cross_worker = client.post(
    "/api/v1/worker/tasks/task-assigned-to-A/artifacts",
    headers={"Authorization": f"Bearer {token_b}"},
    json={
        "filename": "cross_worker_file.txt",
        "content_base64": base64.b64encode(content_bytes_b).decode(),
        "expected_sha256": hashlib.sha256(content_bytes_b).hexdigest()
    }
)
results["cases"]["3_cross_worker_authorization"] = {
    "desired_secure_status": 403,
    "actual_status": resp_cross_worker.status_code,
    "vulnerability_demonstrated": resp_cross_worker.status_code == 200,
    "response_payload": resp_cross_worker.json() if resp_cross_worker.status_code == 200 else resp_cross_worker.text,
    "finding_verdict": "VULNERABLE_CROSS_WORKER_UPLOAD_PERMITTED_200" if resp_cross_worker.status_code == 200 else "SECURE"
}

# ---------------------------------------------------------------------------
# Case 4: Upload to task with expired lease -> Vulnerability Test
# Expected Secure Behavior: 409 Conflict
# Observed Vulnerable Behavior: 200 OK (Missing lease fencing check)
# ---------------------------------------------------------------------------
content_bytes_expired = b"payload uploaded after task lease expired"
resp_expired_lease = client.post(
    "/api/v1/worker/tasks/task-assigned-to-A/artifacts",
    headers={"Authorization": f"Bearer {token_a}"},
    json={
        "filename": "expired_lease_file.txt",
        "content_base64": base64.b64encode(content_bytes_expired).decode(),
        "expected_sha256": hashlib.sha256(content_bytes_expired).hexdigest()
    }
)
results["cases"]["4_expired_lease_fencing"] = {
    "desired_secure_status": 409,
    "actual_status": resp_expired_lease.status_code,
    "vulnerability_demonstrated": resp_expired_lease.status_code == 200,
    "response_payload": resp_expired_lease.json() if resp_expired_lease.status_code == 200 else resp_expired_lease.text,
    "finding_verdict": "VULNERABLE_EXPIRED_LEASE_UPLOAD_PERMITTED_200" if resp_expired_lease.status_code == 200 else "SECURE"
}

# ---------------------------------------------------------------------------
# Case 5: Invalid Checksum -> Integrity Check
# Expected Secure Behavior: 400 Bad Request, file rejected and purged from disk
# Observed Vulnerable Behavior: 200 OK with checksum_matches: false; bytes written to evidence path!
# ---------------------------------------------------------------------------
corrupted_bytes = b"authentic byte payload"
mismatched_sha = "0000000000000000000000000000000000000000000000000000000000000000"
resp_bad_checksum = client.post(
    "/api/v1/worker/tasks/task-assigned-to-A/artifacts",
    headers={"Authorization": f"Bearer {token_a}"},
    json={
        "filename": "bad_checksum.txt",
        "content_base64": base64.b64encode(corrupted_bytes).decode(),
        "expected_sha256": mismatched_sha
    }
)
evidence_file_path = effective_evidence_dir / "task_task-assigned-to-A" / "bad_checksum.txt"
file_persisted_to_disk = evidence_file_path.exists()
persisted_bytes = evidence_file_path.read_bytes() if file_persisted_to_disk else b""

results["cases"]["5_checksum_integrity_and_rejection"] = {
    "desired_secure_status": 400,
    "actual_status": resp_bad_checksum.status_code,
    "file_persisted_to_evidence_dir": file_persisted_to_disk,
    "persisted_file_path": str(evidence_file_path) if file_persisted_to_disk else None,
    "persisted_sha256": hashlib.sha256(persisted_bytes).hexdigest() if file_persisted_to_disk else None,
    "response_payload": resp_bad_checksum.json() if resp_bad_checksum.status_code == 200 else resp_bad_checksum.text,
    "vulnerability_demonstrated": resp_bad_checksum.status_code == 200 and file_persisted_to_disk,
    "finding_verdict": "VULNERABLE_CHECKSUM_MISMATCH_STORED_WITH_200" if (resp_bad_checksum.status_code == 200 and file_persisted_to_disk) else "SECURE_REJECTED_400"
}

# ---------------------------------------------------------------------------
# Case 6: Repeated Upload / Overwrite -> Immutability Check
# Expected Secure Behavior: Immutable version created, zero destructive overwrite
# Observed Vulnerable Behavior: File in evidence directory is mutably overwritten in-place!
# ---------------------------------------------------------------------------
content_v1 = b"version 1 initial file content"
sha_v1 = hashlib.sha256(content_v1).hexdigest()
resp_up1 = client.post(
    "/api/v1/worker/tasks/task-assigned-to-A/artifacts",
    headers={"Authorization": f"Bearer {token_a}"},
    json={
        "filename": "repeat_test.txt",
        "content_base64": base64.b64encode(content_v1).decode(),
        "expected_sha256": sha_v1
    }
)
dest_path = effective_evidence_dir / "task_task-assigned-to-A" / "repeat_test.txt"
exists_after_v1 = dest_path.exists()
bytes_after_v1 = dest_path.read_bytes() if exists_after_v1 else b""

content_v2 = b"version 2 modified replacement content"
sha_v2 = hashlib.sha256(content_v2).hexdigest()
resp_up2 = client.post(
    "/api/v1/worker/tasks/task-assigned-to-A/artifacts",
    headers={"Authorization": f"Bearer {token_a}"},
    json={
        "filename": "repeat_test.txt",
        "content_base64": base64.b64encode(content_v2).decode(),
        "expected_sha256": sha_v2
    }
)
exists_after_v2 = dest_path.exists()
bytes_after_v2 = dest_path.read_bytes() if exists_after_v2 else b""

mutably_overwritten = (bytes_after_v1 == content_v1) and (bytes_after_v2 == content_v2)

results["cases"]["6_repeated_upload_immutability"] = {
    "status_code_v1": resp_up1.status_code,
    "status_code_v2": resp_up2.status_code,
    "evidence_file_path": str(dest_path),
    "bytes_v1_matches": bytes_after_v1 == content_v1,
    "bytes_v2_matches": bytes_after_v2 == content_v2,
    "mutably_overwritten_in_place": mutably_overwritten,
    "vulnerability_demonstrated": mutably_overwritten,
    "finding_verdict": "VULNERABLE_MUTABLY_OVERWRITTEN_IN_PLACE" if mutably_overwritten else "IMMUTABLE_VERSIONED"
}

# Write results
output_path = repo_root / "docs" / "remediation" / "evidence" / "worker_upload_verification_isolated.json"
output_path.parent.mkdir(parents=True, exist_ok=True)
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2)

print("\n" + "="*80)
print("ISOLATED VERIFICATION COMPLETED SAFELY")
print(json.dumps(results["cases"], indent=2))
print("="*80)
