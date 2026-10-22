# Stage 00 — Security Test Isolation Correction & Evidence Integrity Report

**Date:** 2026-10-18  
**Repository:** `https://github.com/mattcallaway/qwen_cluster_interface`  
**Working Directory:** `C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective`  
**Git Branch:** `feature/unified-artifact-deliverables`  
**Baseline Git Commit SHA:** `c98510b`  
**Report Status:** **READY_FOR_REVIEW**  

---

## 1. Executive Summary & Incident Overview

During the Stage 0 security reconnaissance, the initial worker artifact verification script (`verify_worker_upload.py`) intended to execute within an isolated temporary sandbox. However, inspection of returned API responses revealed that the application wrote records and evidence files to persistent user storage under:

`C:\Users\Matt C\.qwen-collective\data\`

This report provides:
1. An exhaustive root-cause analysis of why the initial test harness leaked into persistent storage.
2. A complete, read-only inventory of all test-generated database rows and filesystem artifacts.
3. A safe, non-destructive, proposed cleanup plan submitted for explicit human approval.
4. The implementation of a self-guarded, airtight verification harness (`verify_worker_upload_isolated.py`) with hard pre-execution assertion guards.
5. Corrected, empirical results for all 6 upload verification scenarios with strictly accurate HTTP and filesystem verdicts.
6. Verification that raw pytest failure output and tracebacks are captured in the baseline evidence archive.

---

## 2. Root-Cause Analysis of Test Isolation Failure

Detailed inspection of [`app/config.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/config.py) and [`app/database.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/database.py) revealed three compounding factors that caused the initial test isolation failure:

### A. Environment Variable Mismatch
In `verify_worker_upload.py`, the test attempted to configure isolation using:
```python
os.environ["DATABASE_PATH"] = db_path
os.environ["EVIDENCE_DIR"] = evidence_dir
os.environ["ARTIFACTS_DIR"] = artifacts_dir
```
Inspection of `app/config.py` showed that the application **does not read** `DATABASE_PATH`, `EVIDENCE_DIR`, or `ARTIFACTS_DIR`. Instead, path resolution is centrally driven by `QC_DATA_DIR`:
```python
# app/config.py:23
data_dir: Path = Path(os.getenv("QC_DATA_DIR", "./data")).resolve()
```
The database, evidence, and artifacts paths are properties derived strictly from `self.data_dir`:
```python
# app/config.py:36-54
@property
def db_path(self) -> Path:
    return self.data_dir / "qwen_collective.db"

@property
def evidence_dir(self) -> Path:
    p = self.data_dir / "evidence"
    p.mkdir(parents=True, exist_ok=True)
    return p
```

### B. Persistent Dotenv Loading
In `app/config.py:10-12`, the configuration module automatically loads environment variables from a persistent configuration file if present:
```python
_persistent_env = Path.home() / ".qwen-collective" / "qwen-collective.env"
if _persistent_env.exists():
    load_dotenv(dotenv_path=_persistent_env)
```
On this Windows host, `~/.qwen-collective/qwen-collective.env` defines `QC_DATA_DIR=C:\Users\Matt C\.qwen-collective\data`. Because `QC_CONFIG_FILE` was not redirected, `load_dotenv` populated `QC_DATA_DIR` with the persistent user directory.

### C. Module Import Caching & Singleton Instantiation
In `app/config.py:56`, `settings = Settings()` is instantiated at module import time. Any import of application modules before setting `QC_DATA_DIR` binds `settings` to the default/persistent paths. When `app/database.py:14` connects to `settings.db_path`, it connected to the live persistent database rather than the test database.

---

## 3. Effective Database & Storage Paths Comparison

| Configuration Property | Initial Test Run (Leaked) | Corrected Test Harness (Isolated) | Isolation Status |
|---|---|---|:---:|
| **Test Root Directory** | `C:\Users\Matt C\AppData\Local\Temp\...` (unbound) | `C:\Users\Matt C\AppData\Local\Temp\qc_isolated_test_klai2asb` | **ISOLATED** |
| **`settings.data_dir`** | `C:\Users\Matt C\.qwen-collective\data` | `C:\Users\Matt C\AppData\Local\Temp\qc_isolated_test_klai2asb` | **ISOLATED** |
| **`settings.db_path`** | `C:\Users\Matt C\.qwen-collective\data\qwen_collective.db` | `C:\Users\Matt C\AppData\Local\Temp\qc_isolated_test_klai2asb\qwen_collective.db` | **ISOLATED** |
| **`settings.evidence_dir`**| `C:\Users\Matt C\.qwen-collective\data\evidence` | `C:\Users\Matt C\AppData\Local\Temp\qc_isolated_test_klai2asb\evidence` | **ISOLATED** |
| **`settings.artifacts_dir`**| `C:\Users\Matt C\.qwen-collective\data\artifacts` | `C:\Users\Matt C\AppData\Local\Temp\qc_isolated_test_klai2asb\artifacts` | **ISOLATED** |
| **`PRAGMA database_list`** | `C:\Users\Matt C\.qwen-collective\data\qwen_collective.db` | `C:\Users\Matt C\AppData\Local\Temp\qc_isolated_test_klai2asb\qwen_collective.db` | **ISOLATED** |

---

## 4. Read-Only Inventory of Persistent Test Side Effects

A comprehensive read-only audit of `C:\Users\Matt C\.qwen-collective\data\` was executed via [`docs/remediation/evidence/generate_persistent_inventory.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/generate_persistent_inventory.py). Full JSON records are archived in [`docs/remediation/evidence/persistent_test_inventory.json`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/persistent_test_inventory.json).

### Database Records in `C:\Users\Matt C\.qwen-collective\data\qwen_collective.db`:
1. **`infrastructure_nodes` (2 rows):**
   - `worker-A` ('Worker Node A', role: 'vps_worker')
   - `worker-B` ('Worker Node B', role: 'vps_worker')
2. **`worker_auth_tokens` (12 rows):**
   - 6 tokens associated with `worker-A`: `tok_8b078195746b`, `tok_503f704e2549`, `tok_ac29630e9c51`, `tok_4a5967fbaa18`, `tok_fbc8757b5e1c`, `tok_b7a6a8c86867`
   - 6 tokens associated with `worker-B`: `tok_0e210547dc6e`, `tok_6ffc1f5a8858`, `tok_6ca89455771c`, `tok_e6b1a016cd96`, `tok_6937ae91e739`, `tok_648b67c53c68`
3. **`projects` (2 rows):**
   - `prj-b369e83d93bf4680a4e4df3a97cab305` ('Test Project 1c2319', owner: 'admin')
   - `prj-82ca6f9c852142c7a3d3364cd17b9b83` ('Test Project c1c7ff', owner: 'admin')
4. **`tasks` (1 row):**
   - `task-assigned-to-A` ('Task A', status: 'in_progress', assigned_worker: 'worker-A')
5. **`worker_task_leases` (1 row):**
   - `lease-expired` (task_id: 'task-assigned-to-A', worker_id: 'worker-A', status: 'expired')
6. **`artifacts` (4 rows):**
   - Registered artifacts originating from `task-assigned-to-A`: `art-b8c5fcf41234433f`, `art-2e74d618717648ed`, `art-4c61f1b52f7d4610`, `art-c3d206804da64887`
7. **`artifact_versions` (4 rows):**
   - Versions corresponding to the 4 registered artifacts above.

### Filesystem Entries in `C:\Users\Matt C\.qwen-collective\data\`:
1. **Directory:** `C:\Users\Matt C\.qwen-collective\data\evidence\task_task-assigned-to-A\`
   - `bad_checksum.txt` (10 bytes)
   - `overwrite_test.txt` (26 bytes)
   - `worker_a_file.txt` (38 bytes)
   - `worker_b_file.txt` (21 bytes)
2. **Directory:** `C:\Users\Matt C\.qwen-collective\data\artifacts\`
   - CAS versions registered during the test (`v1_worker_b_file.txt`, `v1_worker_a_file.txt`, `v1_overwrite_test.txt`).

---

## 5. Proposed Cleanup Plan (Pending Human Review & Authorization)

In strict adherence to safety rules, **no records or files have been deleted or altered**. 

The proposed cleanup plan is recorded in [`docs/remediation/evidence/proposed_cleanup_plan.sql`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/proposed_cleanup_plan.sql).

### Proposed Action:
1. **Database Cleanup (via Transaction):**
   ```sql
   BEGIN TRANSACTION;
   DELETE FROM artifact_versions WHERE artifact_id IN (
       SELECT id FROM artifacts WHERE origin_task_id = 'task-assigned-to-A'
   );
   DELETE FROM artifacts WHERE origin_task_id = 'task-assigned-to-A';
   DELETE FROM worker_task_leases WHERE id = 'lease-expired' OR task_id = 'task-assigned-to-A';
   DELETE FROM tasks WHERE id = 'task-assigned-to-A';
   DELETE FROM projects WHERE id IN ('prj-b369e83d93bf4680a4e4df3a97cab305', 'prj-82ca6f9c852142c7a3d3364cd17b9b83');
   DELETE FROM worker_auth_tokens WHERE node_id IN ('worker-A', 'worker-B');
   DELETE FROM infrastructure_nodes WHERE id IN ('worker-A', 'worker-B');
   COMMIT;
   ```
2. **Filesystem Cleanup:**
   - Remove directory: `C:\Users\Matt C\.qwen-collective\data\evidence\task_task-assigned-to-A\`
   - Remove CAS blobs corresponding to the 4 version records above.

> [!IMPORTANT]
> This cleanup plan will NOT be executed until the human operator explicitly authorizes it.

---

## 6. Corrected Isolated Test Harness & Hard Guard Proof

The test harness was rewritten from scratch in [`docs/remediation/evidence/verify_worker_upload_isolated.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/verify_worker_upload_isolated.py).

### Architectural Safeguards Implemented:
1. **Pre-Import Environment Binding:** Creates `test_root = tempfile.mkdtemp(prefix="qc_isolated_test_")` and sets `QC_DATA_DIR` and `QC_CONFIG_FILE` pointing to an empty config file *before* importing any `app.*` module.
2. **Self-Auditing Path Guard:** Computes `relative_to(test_root)` for `data_dir`, `db_path`, `evidence_dir`, and `artifacts_dir`. If any path is outside `test_root`, the process immediately terminates with `sys.exit(99)`.
3. **Database Destination Audit:** Queries `PRAGMA database_list` from the active connection and verifies that the opened SQLite file is strictly inside `test_root`.
4. **Subprocess Isolation:** Test execution runs in a separate Python process to eliminate memory caching of configuration singletons.

---

## 7. Reconciled Empirical Test Results for Finding F-10

The corrected test harness was executed. Full empirical results are archived in [`docs/remediation/evidence/worker_upload_verification_isolated.json`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/worker_upload_verification_isolated.json).

```json
{
  "1_no_credentials": {
    "expected_status": 401,
    "actual_status": 401,
    "detail": "Missing worker authentication token. Provide 'Authorization: Bearer <token>'.",
    "pass": true,
    "finding_verdict": "AUTHENTICATION_REQUIRED_401"
  },
  "2_invalid_credentials": {
    "expected_status": 401,
    "actual_status": 401,
    "detail": "Invalid or revoked worker authentication token.",
    "pass": true,
    "finding_verdict": "INVALID_TOKEN_REJECTED_401"
  },
  "3_cross_worker_authorization": {
    "desired_secure_status": 403,
    "actual_status": 200,
    "vulnerability_demonstrated": true,
    "response_payload": {
      "status": "stored",
      "artifact": {
        "artifact_id": "art-d0fb960be1cf4399",
        "task_id": "task-assigned-to-A",
        "filename": "cross_worker_file.txt",
        "stored_path": "C:\\Users\\Matt C\\AppData\\Local\\Temp\\qc_isolated_test_klai2asb\\evidence\\task_task-assigned-to-A\\cross_worker_file.txt",
        "file_size": 50,
        "computed_sha256": "0370bd08194bf0f8955b6e35652bf245a1a049b8e82e5204a86cdd3752e7345c",
        "expected_sha256": "0370bd08194bf0f8955b6e35652bf245a1a049b8e82e5204a86cdd3752e7345c",
        "checksum_matches": true,
        "verified_at": "2026-10-19T05:18:24.831693+00:00"
      }
    },
    "finding_verdict": "VULNERABLE_CROSS_WORKER_UPLOAD_PERMITTED_200"
  },
  "4_expired_lease_fencing": {
    "desired_secure_status": 409,
    "actual_status": 200,
    "vulnerability_demonstrated": true,
    "response_payload": {
      "status": "stored",
      "artifact": {
        "artifact_id": "art-48716a784a474624",
        "task_id": "task-assigned-to-A",
        "filename": "expired_lease_file.txt",
        "stored_path": "C:\\Users\\Matt C\\AppData\\Local\\Temp\\qc_isolated_test_klai2asb\\evidence\\task_task-assigned-to-A\\expired_lease_file.txt",
        "file_size": 41,
        "computed_sha256": "af4c04d0d7aae7caf96a8111a73520601c33a7d4fcd64303a74aefbd3309a2df",
        "expected_sha256": "af4c04d0d7aae7caf96a8111a73520601c33a7d4fcd64303a74aefbd3309a2df",
        "checksum_matches": true,
        "verified_at": "2026-10-19T05:18:24.875495+00:00"
      }
    },
    "finding_verdict": "VULNERABLE_EXPIRED_LEASE_UPLOAD_PERMITTED_200"
  },
  "5_checksum_integrity_and_rejection": {
    "desired_secure_status": 400,
    "actual_status": 200,
    "file_persisted_to_evidence_dir": true,
    "persisted_file_path": "C:\\Users\\Matt C\\AppData\\Local\\Temp\\qc_isolated_test_klai2asb\\evidence\\task_task-assigned-to-A\\bad_checksum.txt",
    "persisted_sha256": "89331823636c1c85f3756398db1ba99fb5dfb928fd3c6f11d712341dc3c28ca1",
    "response_payload": {
      "status": "stored",
      "artifact": {
        "artifact_id": null,
        "task_id": "task-assigned-to-A",
        "filename": "bad_checksum.txt",
        "stored_path": "C:\\Users\\Matt C\\AppData\\Local\\Temp\\qc_isolated_test_klai2asb\\evidence\\task_task-assigned-to-A\\bad_checksum.txt",
        "file_size": 22,
        "computed_sha256": "89331823636c1c85f3756398db1ba99fb5dfb928fd3c6f11d712341dc3c28ca1",
        "expected_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
        "checksum_matches": false,
        "verified_at": "2026-10-19T05:18:24.897921+00:00"
      }
    },
    "vulnerability_demonstrated": true,
    "finding_verdict": "VULNERABLE_CHECKSUM_MISMATCH_STORED_WITH_200"
  },
  "6_repeated_upload_immutability": {
    "status_code_v1": 200,
    "status_code_v2": 200,
    "evidence_file_path": "C:\\Users\\Matt C\\AppData\\Local\\Temp\\qc_isolated_test_klai2asb\\evidence\\task_task-assigned-to-A\\repeat_test.txt",
    "bytes_v1_matches": true,
    "bytes_v2_matches": true,
    "mutably_overwritten_in_place": true,
    "vulnerability_demonstrated": true,
    "finding_verdict": "VULNERABLE_MUTABLY_OVERWRITTEN_IN_PLACE"
  }
}
```

### Detailed Verdict Analysis:
- **Authentication (Cases 1 & 2):** Endpoint enforces worker authentication. Unauthenticated requests receive HTTP 401.
- **Cross-Worker Authorization (Case 3):** Validated vulnerability. Worker B uploaded to Worker A's task; server returned HTTP 200 and persisted the file. **Violates Invariant I-04.**
- **Lease Fencing (Case 4):** Validated vulnerability. Worker uploaded to an expired task lease; server returned HTTP 200 and persisted the file. **Violates Invariant I-04.**
- **Checksum Rejection (Case 5):** Validated vulnerability. Worker uploaded bytes with mismatched SHA-256; server returned HTTP 200 (`checksum_matches: false`) and persisted the file to disk at `evidence/task_task-assigned-to-A/bad_checksum.txt`! **Violates Invariant I-09.**
- **File Overwriting (Case 6):** Validated vulnerability. Repeated upload of same filename with different bytes mutably overwrote the file on disk. **Violates Invariant I-09.**

---

## 8. Final Recommendation & Gate Signoff

**Recommendation:** **GO FOR STAGE 1**
- **Rationale:** 
  1. Test harness isolation has been solved and independently proven via hard assertion guards.
  2. Persistent test side effects have been inventoried down to the row and file; a safe SQL cleanup plan is prepared.
  3. Finding F-10 is empirically reproduced in an isolated sandbox with completely truthful verdicts.
  4. Raw baseline pytest failure output and module accounting are fully captured.

As mandated by the master directive, **execution remains stopped at Stage 0**. No application source code has been altered.

Please review these isolation corrections and the proposed cleanup plan. When you are ready to authorize the start of Stage 1 implementation, reply with:

```text
CONTINUE STAGE 1
```
