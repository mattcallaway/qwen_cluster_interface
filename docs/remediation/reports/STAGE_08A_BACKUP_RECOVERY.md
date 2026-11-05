# Qwen Collective — Stage 8A: Backup, Recovery & Data Integrity Report

**Date:** 2026-10-21  
**Status:** IMPLEMENTED AND VERIFIED (STAGE 8A COMPLETE — STOPPED FOR HUMAN REVIEW)  
**Active Branch:** `feature/unified-artifact-deliverables`  
**Git Checkpoint (HEAD):** `c98510b9a1569a113c762f083f5bba4395704531`  
**Working Tree Patch:** [`docs/remediation/evidence/working_tree_patch.diff`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/working_tree_patch.diff) (1,002,579 bytes, verified clean against disposable clone)  
**Execution Scope:** Authorized Workstream 8A (Backup, Recovery, and Data Integrity) only. Zero autonomous advancement into subsequent Stage 8 workstreams (8B/8C) or Stage 9. Zero production data migrations or overwrites executed.

---

## Executive Summary

Workstream 8A establishes a complete, atomic, point-in-time backup and disaster recovery architecture for the Qwen Collective. Prior to this workstream, the system possessed only fragmented mechanisms (a PowerShell script copying an uncheckpointed SQLite database file and markdown notes without artifact coverage, checksum manifests, or restore capabilities).

Under Stage 8A:
1. **Complete Recovery Unit:** Implemented an atomic recovery packaging subsystem capturing:
   - Consistent SQLite database snapshot using `sqlite3.Connection.backup()` (preventing torn writes and incomplete WAL state).
   - Content-Addressed Storage (CAS) artifact blobs and project deliverables.
   - Persistent Markdown Knowledge Vault files.
   - Research evidence, run ledgers, and citation audit logs.
   - A versioned manifest (`manifest.json`) recording component SHA-256 hashes, byte sizes, schema versions, application version, and table record counts.
2. **Fail-Closed Verification & Safe Restoration:** Implemented an independent restoration engine that:
   - Pre-verifies archive integrity and SHA-256 checksums fail-closed prior to touching destination disks.
   - Enforces strict path traversal defenses rejecting directory escape attacks.
   - Forbids restoration into active production directories (`settings.data_dir`) without explicit, multi-flag operator authorization (`allow_production_overwrite=True` and verified operator identity).
   - Validates restored database integrity via `PRAGMA integrity_check` and `PRAGMA foreign_key_check`.
   - Offers an operator dry-run simulation mode.
3. **Verification & Testing:**
   - **Targeted Suite:** 10 out of 10 targeted Stage 8A tests passed in 7.4 seconds (`tests/test_stage08a_backup_recovery.py`).
   - **Full Deterministic Regression Suite:** Ran once via `run_evidence_suite.py`: **690 collected, 689 passed, 0 failed, 1 skipped** (offline marker), exit code **`0`**.

---

## 1. Active Storage Locations & Database Configuration

During preflight inspection, the live runtime storage configuration was confirmed via `app/config.py` and environment inspection:

| Storage Component | Configuration Attribute | Active Filesystem Path | Operational Characteristics |
|---|---|---|---|
| **Primary SQLite DB** | `settings.db_path` | `C:\Users\Matt C\.qwen-collective\data\qwen_collective.db` | Active size ~46 MB, WAL mode (`journal_mode=WAL`), `foreign_keys=ON`, `user_version=18`. Note: root `qwen_collective.db` and `data/qwen_collective.db` are 0-byte repo placeholders. |
| **Artifact & CAS Store** | `settings.artifacts_dir` | `C:\Users\Matt C\.qwen-collective\data\artifacts` | Content-Addressed Storage (CAS) keyed by SHA-256 (`cas/{hash[:2]}/{hash}`) and project-scoped deliverables (`{project_id}/{artifact_id}/...`). |
| **Knowledge Vault** | `settings.knowledge_dir` | `C:\Users\Matt C\.qwen-collective\data\knowledge` | Persistent Markdown notes across `system/`, `projects/`, `research/`, `sources/`, `decisions/`, `journal/`, `inbox/`. |
| **Evidence & Ledgers** | `settings.evidence_dir` | `C:\Users\Matt C\.qwen-collective\data\evidence` | Orchestration run logs, citation audits, health reports, deliverables index. |
| **Research Sources** | N/A (data subdirectory) | `C:\Users\Matt C\.qwen-collective\data\research_sources` | Downloaded external research content and raw snapshots. |
| **Backups Directory** | Derived default | `C:\Users\Matt C\.qwen-collective\data\backups` | Isolated archive repository for timestamped snapshot archives. |

---

## 2. Backup Architecture & Snapshot Consistency Strategy

```
                                      +---------------------------------------------+
                                      |             Live System State               |
                                      |  (SQLite WAL, CAS Blobs, Vault, Evidence)   |
                                      +---------------------------------------------+
                                                             |
                                           [Read-Only Atomic Snapshot Stage]
                                                             |
                 +-----------------------+-------------------+-----------------------+
                 |                       |                   |                       |
                 v                       v                   v                       v
        +------------------+    +------------------+  +--------------+     +-------------------+
        |  SQLite Online   |    |    CAS Blobs     |  |  Knowledge   |     | Research Evidence |
        |  Backup API      |    |  & Deliverables  |  |  Vault Notes |     | & Citation Audits |
        | (conn.backup())  |    |  (artifacts/)    |  | (knowledge/) |     |   (evidence/)     |
        +------------------+    +------------------+  +--------------+     +-------------------+
                 |                       |                   |                       |
                 +-----------------------+-------------------+-----------------------+
                                                             |
                                      +---------------------------------------------+
                                      |          Compute SHA-256 Checksums          |
                                      |           & Query Record Counts             |
                                      +---------------------------------------------+
                                                             |
                                      +---------------------------------------------+
                                      |             Build manifest.json             |
                                      | (snapshot_id, schema_version, file hashes)  |
                                      +---------------------------------------------+
                                                             |
                                      +---------------------------------------------+
                                      |       Package: .tar.gz Atomic Rename        |
                                      |    qwen_collective_backup_<id>.tar.gz       |
                                      +---------------------------------------------+
```

### Snapshot Consistency Strategy
- **SQLite WAL Mode Consistency:** Because SQLite operates in Write-Ahead Logging mode (`WAL`), copying the raw `.db` file from the filesystem risks capturing partial transactions or omitting uncommitted frames residing in `-wal`. The backup subsystem strictly invokes the SQLite Online Backup API (`src_conn.backup(dst_conn)`) with `PRAGMA foreign_keys = ON`.
- **Ephemeral File Elimination:** Before archiving, the staged snapshot runs `PRAGMA integrity_check` and `PRAGMA journal_mode = DELETE` to produce a completely consolidated, standalone database file, deleting any temporary `-wal` or `-shm` helper files.
- **Atomic File Generation:** Snapshots are written to a `.part` staging filename (`qwen_collective_backup_<snapshot_id>.tar.gz.part`). Only upon full archive creation and verification is the file atomically renamed to `.tar.gz`. If an error occurs, staging files and partial archives are immediately purged.
- **Immutability:** Existing snapshots are never overwritten; duplicate requests or timestamp collisions generate unique snapshot IDs (`snp-<timestamp>-<uuid>`).

---

## 3. Exact Implementation Changes

1. **[`app/services/backup.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/backup.py) (NEW CORE SERVICE):**
   - `create_backup()`: Performs read-only SQLite backup, collects CAS blobs, knowledge notes, and evidence files, builds versioned `manifest.json` with SHA-256 hashes, and writes atomic `.tar.gz` archive.
   - `inspect_backup()`: Inspects archive metadata and reads `manifest.json` without extracting to disk.
   - `verify_backup()`: Fail-closed verification checking path traversal safety, schema version, table integrity, and SHA-256 checksums across all members.
   - `restore_backup()`: Performs dry-run simulation or full isolated restoration. Safeguards production directory against accidental overwrites (`allow_production_overwrite` required). Performs post-restore validation on restored destination.
   - `list_backups()`: Enumerates and parses all recovery points in a backup directory.
   - CLI Interface: Provides full `argparse` operator command line (`create`, `list`, `inspect`, `verify`, `restore`).

2. **[`scripts/backup-windows.ps1`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/scripts/backup-windows.ps1) (MODIFIED):**
   - Replaced legacy incomplete zip creation with direct delegation to `python -m app.services.backup create`.
   - Updated retention policy to prune `.tar.gz` recovery units older than configured `$RetentionDays`.

3. **[`app/templates/settings.html`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/templates/settings.html) (MODIFIED):**
   - Modernized the "Backup & Durability" operator card to document Stage 8A CLI operations (`create`, `list`, `inspect`, `verify`, `restore`).

4. **[`tests/test_stage08a_backup_recovery.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage08a_backup_recovery.py) (NEW TEST SUITE):**
   - 10 targeted positive, negative, and security tests proving end-to-end recovery, trust-tier preservation, checksum integrity, and fail-closed defenses.

---

## 4. Operator Experience & Recovery Procedure

### 4.1 Creating a Backup
```powershell
python -m app.services.backup create --operator-id admin --comment "Pre-maintenance recovery unit"
```
Output:
```json
{
  "status": "success",
  "snapshot_id": "snp-20261021_070000-abcdef12",
  "archive_path": "C:\\Users\\Matt C\\.qwen-collective\\data\\backups\\qwen_collective_backup_snp-20261021_070000-abcdef12.tar.gz",
  "archive_filename": "qwen_collective_backup_snp-20261021_070000-abcdef12.tar.gz",
  "size_bytes": 48291044,
  "schema_version": 18,
  "operator_id": "admin",
  "component_count": 34
}
```

### 4.2 Inspecting Recovery Points
```powershell
# List available recovery archives
python -m app.services.backup list

# Inspect single archive manifest without extraction
python -m app.services.backup inspect path/to/archive.tar.gz
```

### 4.3 Validating a Recovery Unit
```powershell
python -m app.services.backup verify path/to/archive.tar.gz
```
Output:
```json
{
  "valid": true,
  "archive_path": "C:\\path\\to\\archive.tar.gz",
  "snapshot_id": "snp-20261021_070000-abcdef12",
  "schema_version": 18,
  "component_count": 34,
  "errors": [],
  "warnings": []
}
```

### 4.4 Dry-Run Restore Simulation
```powershell
python -m app.services.backup restore path/to/archive.tar.gz --target-dir C:\staging\restore_target --dry-run
```
Validates checksums, schema compatibility, and destination path, and outputs the detailed file deployment plan without altering any files on disk.

### 4.5 Executing an Isolated Restoration
```powershell
# Restore into an isolated destination
python -m app.services.backup restore path/to/archive.tar.gz --target-dir C:\staging\restore_target

# Restore into active production directory (Strict Operator Authorization Required)
python -m app.services.backup restore path/to/archive.tar.gz --target-dir "C:\Users\Matt C\.qwen-collective\data" --allow-production-overwrite --operator-id admin
```

---

## 5. Positive and Negative Test Results

### Targeted Stage 8A Test Suite (`tests/test_stage08a_backup_recovery.py`)

| Test ID | Category | Objective & Conditions | Result |
|---|---|---|:---:|
| `test_backup_creation_and_manifest` | Positive | Verifies snapshot packaging, manifest schema, SHA-256 calculation, and record counts. | **PASSED** |
| `test_backup_inspect_and_list` | Positive | Verifies `list_backups()` and `inspect_backup()` parse archive without disk extraction. | **PASSED** |
| `test_backup_verification_positive` | Positive | Confirms uncorrupted archive passes all checksum, schema, and SQLite checks. | **PASSED** |
| `test_restore_dry_run_does_not_modify_destination` | Safety | Confirms dry-run validation succeeds without creating or modifying target directory. | **PASSED** |
| `test_restore_live_production_safeguard` | Security Guard | Refuses restore into `settings.data_dir` without `allow_production_overwrite=True`. | **PASSED** |
| `test_restore_into_isolated_environment_end_to_end` | E2E Positive | Restores populated system into isolated empty environment; verifies full integrity. | **PASSED** |
| `test_corrupted_backup_rejected_fail_closed` | Tamper Defense | Injects corrupted bytes into CAS blob; verifies fail-closed rejection without writes. | **PASSED** |
| `test_missing_cas_blob_detected_fail_closed` | Tamper Defense | Deletes manifested CAS file; verifies fail-closed missing component detection. | **PASSED** |
| `test_path_traversal_rejection_fail_closed` | Security Guard | Injects `../../outside_target.txt` into archive; verifies rejection with zero escape. | **PASSED** |
| `test_atomic_backup_under_live_writes` | Concurrency | Executes concurrent writes under SQLite WAL mode; verifies consistent snapshot. | **PASSED** |

**Targeted Suite Result: 10 passed, 0 failed, 0 errors in 7.40s.**

---

## 6. Evidence of Independent Restoration & Integrity Verification

In `test_restore_into_isolated_environment_end_to_end`, a disposable environment was populated with:
- Multiple project records (`Primary Alpha Project`, `Secondary Beta Project`) and tasks.
- Immutable CAS artifacts with version history (v1: 52 bytes, v2: 66 bytes).
- Cross-project grants (`artifact_access_grants`) allowing Project Beta to read Project Alpha's artifact.
- Artifact lineage records (`artifact_lineage`) tracing derivation relationships.
- Knowledge records across multiple trust tiers:
  - `reviewed` tier with verified human review attribution (`reviewed_by='lead_architect'`).
  - `candidate` tier awaiting review.
  - `reusable` tier in collective scope (`scope='collective'`).
- Research runs, sources (`https://pola.rs/benchmarks`), claims, evidence, and citation records.
- Markdown Knowledge Vault notes and orchestration run evidence ledgers.

### Post-Restoration Verifications in the Isolated Target:
1. **SQLite Database:**
   - `PRAGMA integrity_check` $\rightarrow$ returned `"ok"`.
   - `PRAGMA foreign_key_check` $\rightarrow$ returned 0 violations.
   - `PRAGMA user_version` $\rightarrow$ matched active schema version 18.
2. **CAS Blob Byte Matching:**
   - Restored files `artifacts/cas/{hash[:2]}/{hash}` matched original payload bytes and SHA-256 hashes identically.
3. **Lineage and Grants:**
   - Active cross-project grant record intact (`source_project_id=p1_id`, `target_project_id=p2_id`, `status='active'`).
   - Lineage relationship intact (`relationship_type='derived_from'`).
4. **Knowledge & Trust-Tier Preservation:**
   - `reviewed` record retained `trust_tier='reviewed'`, `reviewed_by='lead_architect'`, and audit digest `[digest:...]`.
   - `candidate` record remained strictly in `candidate` tier (zero unauthorized elevation).
   - `reusable` record remained in `reusable` tier and `collective` scope.
5. **Autonomy:**
   - Restored environment was completely independent of the original data directories.

---

## 7. Full Regression Suite Results

Following successful targeted recovery tests, the full deterministic regression suite was executed via `python run_evidence_suite.py`:

```
Executing test evidence generation in C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective...
Running collection: pytest --collect-only -q
Total collected tests: 690
Running test suite: pytest -v --junitxml=docs\remediation\evidence\junit_report.xml
Pytest exited with code 0 in 322.1s
Arithmetic validation PASSED: 690 collected = 689 passed + 0 failed + 1 skipped + 0 errors across 67 modules.
Generated test_ids_inventory.json and test_ids_inventory.md (690 individual tests).
[TEST SUITE SUCCESS] All tests passed with exit code 0.
```

- **Collected Tests:** **690** (previous baseline 680 + 10 new Stage 8A tests)
- **Passed Tests:** **689** (100% of runnable deterministic tests)
- **Failed Tests:** **0**
- **Skipped Tests:** **1** (`tests/test_live_web_research.py::test_live_web_research_cycle` — transparently excluded via `@pytest.mark.live_network`)
- **Errors:** **0**
- **Duration:** 322.1 seconds
- **Test Runner Exit Code:** **`0`** (Success)

---

## 8. Data-Loss or Corruption Risks

1. **Storage Quotas for Large CAS Archives:** In high-throughput environments producing multi-gigabyte models or datasets, full backup archives will consume disk space proportionally. Operator runbooks must configure `$RetentionDays` in `scripts/backup-windows.ps1` to prevent disk exhaustion.
2. **Production Restoration Requires Clean Shutdown:** If an operator executes an authorized restoration into live production (`settings.data_dir`), the `QwenCollective` controller service should be stopped prior to restoration to prevent open file handle locks on Windows.

---

## 9. Source Checkpoint & Working-Tree Status

- **Git HEAD:** `c98510b9a1569a113c762f083f5bba4395704531`
- **Working Tree Patch:** [`docs/remediation/evidence/working_tree_patch.diff`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/working_tree_patch.diff)
  - Size: **1,002,579 bytes** (binary UTF-8).
  - Captures all 47 modified tracked files and all 21 untracked implementation/test files (including `app/services/backup.py` and `tests/test_stage08a_backup_recovery.py`).
  - **Clean Checkout Verification:** Cloned a disposable clean Git checkout from `c98510b`, executed `git apply --check`, and executed `git apply`.
    - `git apply --check` return code: **`0`**
    - `git apply` return code: **`0`**
- Working tree remains clean and uncommitted.

---

## 10. Remaining Stage 8 Workstreams

Stage 8 is partitioned into three distinct workstreams:
- **Workstream 8A:** Backup, Recovery, and Data Integrity — **IMPLEMENTED AND VERIFIED** (Pending Human Review).
- **Workstream 8B:** High-Availability, Controller Failover, and Health Monitoring — **NOT STARTED / UNAUTHORIZED**.
- **Workstream 8C:** Final Security Hardening and Deployment Acceptance Verification — **NOT STARTED / UNAUTHORIZED**.

---

## 11. Governance & Hold

- **STAGE 6:** FUNCTIONALLY ACCEPTED, RELEASE VALIDATION PENDING.
- **STAGE 7:** REGRESSION GATE PASSED (0 failures across 680 tests).
- **STAGE 8A:** **IMPLEMENTED AND VERIFIED**.
- **STAGE 8B / 8C / STAGE 9:** **STRICTLY ON HOLD AND NOT AUTHORIZED.**

**EXECUTION COMPLETE. STOPPED FOR HUMAN REVIEW.**
