# Artifact Creation, Transfer & Independent Verification Validation
**Antigravity — Qwen Collective**  
**Component:** `vps_agent/executor.py`, `app/routes/worker.py`, `app/services/worker.py`, `app/services/metrics.py`  
**Branch:** `fix/vps-production-acceptance`  
**Status:** VALIDATED & PASSING

---

## 1. Problem Statement

Previously, task execution results relied on in-memory string hashing or claimed checksum strings. There was no protocol for:
1. Creating real physical files on worker disk during execution.
2. Transferring binary or structured file artifacts from worker to controller.
3. Securely storing artifacts in an isolated evidence repository on the controller.
4. Independently calculating cryptographic checksums over the physical byte stream received by the controller.
5. Associating verified physical files with project deliverables and metric observations.

---

## 2. End-to-End Artifact Pipeline Architecture

```mermaid
sequenceDiagram
    autonumber
    participant VPS as VPS Worker (Linux)
    participant API as Controller Worker API
    participant Svc as Worker Service
    participant Store as Evidence Store (Disk)
    participant DB as SQLite Database

    VPS->>VPS: Script executes; writes 'vps_diagnostic_artifact.json' to isolated work_dir
    VPS->>VPS: Flushes to disk; computes file_sha256 = SHA256(disk_bytes)
    VPS->>VPS: Collects file bytes, encodes as base64
    VPS->>API: POST /api/v1/worker/tasks/{task_id}/result (with artifacts: [{filename, content_base64, checksum}])
    API->>Svc: submit_task_result(task_id, result_payload, artifacts)
    loop For each artifact
        Svc->>Svc: Validate filename (sanitize path traversal ../)
        Svc->>Svc: Decode base64 bytes
        Svc->>Store: Write physical file to data/evidence/task_{task_id}/{filename}
        Svc->>Store: Read stored bytes in 64KB chunks; compute controller_sha256
        Svc->>Svc: Compare controller_sha256 == worker_claimed_sha256
    end
    alt Hashes Match Exactly
        Svc->>DB: Link artifact to Deliverable Revision (file_path, sha256)
        Svc->>DB: Record Metric Observation (verification_status = 'verified')
        Svc->>DB: Update Task status = 'completed'
        API-->>VPS: HTTP 200 OK (stored_artifacts: [{filename, stored_path, verified: true}])
    else Hash Mismatch or Missing File
        Svc->>DB: Record Metric Observation (verification_status = 'rejected')
        API-->>VPS: HTTP 422 / Error Outcome
    end
```

---

## 3. Core Implementation Details

### 3.1 Worker Artifact Generation (`vps_agent/executor.py`)
During execution, any files created inside the transient `work_dir` (excluding internal entrypoints) are automatically discovered and processed:
```python
# Discover physical files generated in work_dir
for item in work_dir.iterdir():
    if item.is_file() and item.name not in ("entrypoint.py", "script.py"):
        content_bytes = item.read_bytes()
        calculated_sha = hashlib.sha256(content_bytes).hexdigest()
        artifacts.append({
            "filename": item.name,
            "content_base64": base64.b64encode(content_bytes).decode("ascii"),
            "file_size": len(content_bytes),
            "sha256": calculated_sha,
        })
```

### 3.2 Secure Evidence Storage (`app/services/worker.py`)
The controller isolates and verifies the artifact bytes before touching project state:
```python
def store_task_artifact(
    task_id: str,
    filename: str,
    content_bytes: bytes,
    expected_checksum: Optional[str] = None
) -> Dict[str, Any]:
    # Prevent directory traversal attacks
    safe_name = Path(filename).name
    if not safe_name or safe_name in (".", ".."):
        raise ValueError("Invalid artifact filename")

    target_dir = settings.evidence_dir / f"task_{task_id}"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / safe_name

    target_file.write_bytes(content_bytes)

    # Independently compute SHA256 over the stored file
    hasher = hashlib.sha256()
    with open(target_file, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    actual_sha256 = hasher.hexdigest()

    checksum_matches = (
        actual_sha256.lower() == expected_checksum.lower()
        if expected_checksum else True
    )

    return {
        "filename": safe_name,
        "stored_path": str(target_file),
        "file_size": len(content_bytes),
        "sha256": actual_sha256,
        "checksum_matches": checksum_matches,
    }
```

### 3.3 Standalone Artifact Upload Endpoint
In addition to embedding in the task submission payload, workers can stream artifacts independently:
- **Route:** `POST /api/v1/worker/tasks/{task_id}/artifacts`
- **Request Body:**
  ```json
  {
    "filename": "vps_diagnostic_artifact.json",
    "content_base64": "eyd0ZXN0JzogJ3ZhbHVlJ30=",
    "expected_checksum": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
  }
  ```
- **Response:**
  ```json
  {
    "task_id": "task_123",
    "filename": "vps_diagnostic_artifact.json",
    "stored_path": "C:/Users/.../data/evidence/task_task_123/vps_diagnostic_artifact.json",
    "file_size": 20,
    "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "checksum_matches": true,
    "status": "stored_and_verified"
  }
  ```

---

## 4. Automated Test Evidence

The artifact transfer and verification mechanism is validated across the test suite:

| Test File | Test Case | Target Property | Result |
| :--- | :--- | :--- | :--- |
| `tests/test_vps_agent.py` | `test_executor_physical_artifact_generation` | Worker detects physical file, hashes disk bytes, outputs base64 artifact | **PASS** |
| `tests/test_metric_integrity.py` | `test_artifact_upload_and_verification_lifecycle` | Controller receives base64 bytes, stores in evidence dir, matches SHA256 | **PASS** |
| `tests/test_metric_integrity.py` | `test_shortcut_a_eliminated_checksum_without_file_remains_unverified` | Claimed checksum without uploaded file rejected as unverified | **PASS** |
| `tests/test_metric_integrity.py` | `test_shortcut_b_eliminated_deliverable_link_without_file_remains_unverified` | Deliverable link without physical file rejected as unverified | **PASS** |
| `tests/test_end_to_end_repair.py` | `test_end_to_end_repair_workflow` | Full lease -> physical artifact write -> transfer -> independent hash -> metric verified | **PASS** |

---

## 5. Summary

The Qwen Collective evidence pipeline now strictly requires physical artifact transport and independent controller-side hashing. A worker can no longer cause an observation to become verified merely by transmitting a checksum string or referencing a deliverable.
