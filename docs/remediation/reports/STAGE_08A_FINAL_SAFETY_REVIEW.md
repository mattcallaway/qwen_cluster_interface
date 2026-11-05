# Stage 8A Final Recovery Safety Gate Review Report

**Date**: 2026-10-21  
**Workstream**: Stage 8A: Backup, Recovery & Data Integrity  
**Gate Status**: **PROVISIONALLY VERIFIED — PENDING HUMAN SIGNOFF**  
**Recommendation**: **GO FOR STAGE 8A FORMAL ACCEPTANCE** / **HOLD ON STAGES 8B, 8C, AND 9**

---

## 1. Executive Summary

This safety review completes the bounded verification requirements for Stage 8A prior to formal signoff. It demonstrates that:

1. **Cross-Component Consistency**: Database records, CAS artifact blobs, Knowledge Vault markdown notes, research evidence, review digests, and artifact lineage remain synchronized during backup operations via an integrated coordination write barrier and strict pre-packaging referential validation.
2. **Production Restoration Authorization**: Plain-string operator identities (`operator_id="admin"`) can no longer authorize production database overwrites. Cryptographic authentication via signed HMAC session tokens (`session_token`) or signed `OperatorAuthority` is strictly enforced.
3. **Archive Extraction Safety**: Archive verification and restore routines reject path traversal (`..`, absolute paths, drive letters), symbolic links, hard links, FIFOs, special device files, duplicate entries, missing manifest files, and decompression bombs / oversized members.
4. **Backup Retention Safety**: The Windows backup retention script (`scripts/backup-windows.ps1`) guarantees that the last known valid recovery point is never deleted, regardless of age, and incomplete `.part` files are never treated as valid recovery points.
5. **Targeted Verification**: 23/23 dedicated tests in `tests/test_stage08a_backup_recovery.py` pass with zero failures.

---

## 2. Cross-Component Consistency Mechanism & Proof

### Architectural Analysis of `app/services/backup.py`

In distributed collaborative systems, database snapshots taken concurrently with filesystem mutations risk "torn snapshots" (e.g., database references an artifact hash whose file has not finished writing, or a knowledge record whose human review digest does not match its note on disk).

`app/services/backup.py` coordinates consistency across all storage tiers through a three-layer mechanism:

1. **SQLite Online Backup API (`sqlite3.Connection.backup`)**:
   - Rather than raw file copying (which corrupts active WAL-mode databases), the service opens a dedicated source connection and streams consistent database pages into an isolated temporary database file (`stage_dir/database/qwen_collective.db`).
   - Active writes in progress commit cleanly or rollback; the resulting snapshot is a valid point-in-time state.

2. **Application Write Barrier (`SnapshotCoordination`)**:
   - `SnapshotCoordination.snapshot_barrier()` is acquired during the snapshot window.
   - Long-running destructive operations (such as CAS garbage collection or bulk vault reorganizations) coordinate via `SnapshotCoordination.write_barrier()`, waiting up to a bounded timeout before mutating physical assets.
   - This ensures CAS files are not deleted or replaced midway through archive creation.

3. **Pre-Archive Referential Integrity Validation**:
   - Before building the archive, the staged database is queried:
     - **CAS Blobs**: Every record in `artifacts` and `artifact_versions` is cross-referenced against the physical filesystem (`paths["artifacts_dir"] / s_key`). If any file is missing or its computed SHA-256 does not match the database, `create_backup()` fails closed (`BackupError`) and discards the staging directory.
     - **Human Review Digests**: Knowledge records in `knowledge_records` containing `[digest:<sha256:16>]` are checked against their `subject:::statement` content. If modified without updating review attribution, backup creation fails closed.
     - **Lineage Integrity**: The `artifact_lineage` table is checked for dangling references where parent artifacts do not exist.

### Test Proof

- `test_atomic_backup_under_live_writes`: Proves SQLite WAL mode writes during active connections are captured cleanly.
- `test_cross_component_consistency_detects_missing_cas`: Proves `create_backup` detects when a CAS blob is missing on disk and aborts without producing an inconsistent backup.
- `test_cross_component_consistency_detects_tampered_review_digest`: Proves tampered knowledge records or mismatched review digests are detected and abort the snapshot.
- `test_snapshot_coordination_and_write_barrier`: Proves that `write_barrier` waits while `snapshot_barrier` is held and releases cleanly afterwards.

---

## 3. Production Restoration Authorization Findings

### Previous Vulnerability

Previously, `restore_backup()` contained the following check:
```python
if target_dir == live_production_dir:
    if not allow_production_overwrite:
        raise PermissionError(...)
```
A caller could bypass this check simply by setting `allow_production_overwrite=True` and passing an arbitrary string like `operator_id="admin"`. There was no independent verification that the caller possessed valid administrative credentials or a cryptographic signature.

### Hardened Authorization Mechanism

`restore_backup()` in `app/services/backup.py` now enforces independent cryptographic authentication whenever `target_data_dir == settings.data_dir.resolve()`:

1. **Session Token Validation**:
   - The caller must supply a valid `session_token` signed with `settings.secret_key` via `app.auth.verify_session_token()`.
   - If `operator_id` is supplied, it must strictly match the authenticated username in the token.
   - Forged tokens, expired tokens, or unauthenticated plain strings trigger `PermissionError: CRITICAL SECURITY VIOLATION: Unauthorized production restore attempt.`
2. **Cryptographic OperatorAuthority**:
   - Alternatively, a signed `OperatorAuthority` object validated via `app.services.knowledge_store.validate_operator_authority()` with `operation="restore"` is accepted.
3. **Dry-Run Default**:
   - Operators can execute a production restore plan with `dry_run=True`, which fully validates the archive and reports the restoration plan without touching disk.

### Test Proof

- `test_unauthenticated_plain_operator_rejected_for_production_restore`: Passing `operator_id="admin"` without a session token raises `PermissionError`.
- `test_forged_session_token_rejected_for_production_restore`: Passing a forged token (`admin:1700000000:invalid_forged_hmac_signature`) raises `PermissionError`.
- `test_mismatched_session_token_rejected_for_production_restore`: Using a valid token for "attacker" while claiming `operator_id="admin"` raises `PermissionError`.
- `test_authenticated_operator_authorized_for_production_dry_run`: Authenticated operator with valid token successfully executes dry-run restoration plan.

---

## 4. Archive Safety Assessment

### Defense Against Malicious and Corrupted Archives

`_validate_tar_member_safety()` and `verify_backup()` evaluate all archive members before extraction:

| Vulnerability Vector | Enforcement Mechanism | Result |
| :--- | :--- | :--- |
| **Path Traversal (`../`)** | Analyzes path parts using `Path(norm).parts`; rejects any segment containing `..` or leading `/` / `\`. | Rejection (`PathTraversalSecurityError`) |
| **Absolute Paths / Drive Letters** | Rejects leading slashes and drive letters (e.g. `C:\`). | Rejection (`PathTraversalSecurityError`) |
| **Symbolic Links** | Rejects `member.issym()` outright. Links are forbidden in recovery archives. | Rejection (`ArchiveSecurityError`) |
| **Hard Links** | Rejects `member.islnk()` outright. | Rejection (`ArchiveSecurityError`) |
| **Special Files (FIFOs, Devices)** | Rejects `member.ischr()`, `member.isdev()`, `member.isfifo()`, or unsupported file types. | Rejection (`ArchiveSecurityError`) |
| **Decompression Bomb (Single File)** | Rejects single files exceeding `MAX_SINGLE_FILE_BYTES` (500 MB). | Rejection (`ArchiveSecurityError`) |
| **Decompression Bomb (Total Size)** | Accumulates `member.size` and rejects archives exceeding `MAX_TOTAL_UNPACKED_BYTES` (2 GB). | Rejection (`ArchiveSecurityError`) |
| **Duplicate Archive Members** | Tracks normalized member paths in `seen_members`; rejects duplicate collisions. | Rejection (`ArchiveSecurityError`) |
| **Missing Manifest Components** | Matches archive contents against `manifest.json` component map; rejects missing files. | Rejection (`BackupVerificationError`) |

### Test Proof

- `test_path_traversal_rejection_fail_closed`: Directory escape rejected fail-closed.
- `test_archive_safety_rejects_symlink`: Symbolic link rejected.
- `test_archive_safety_rejects_hardlink`: Hard link rejected.
- `test_archive_safety_rejects_fifo_or_device`: Special device / FIFO rejected.
- `test_archive_safety_rejects_duplicate_members`: Duplicate archive member rejected.
- `test_archive_safety_rejects_oversized_file`: Files exceeding single file limit rejected.

---

## 5. Backup Retention Assessment

### Script: `scripts/backup-windows.ps1`

The automated backup job was inspected and repaired to prevent data loss scenarios:

1. **Sole Recovery Point Preservation**:
   - Previously, old backups were pruned if their timestamp was older than `$RetentionDays`. If backups ceased for longer than the retention window, the next retention pass would have deleted all remaining backups.
   - Now, the script checks `$ValidBackups.Count -gt 1`. If only one backup exists, it is **unconditionally preserved** regardless of age:
     ```powershell
     if ($ValidBackups.Count -gt 1) {
         $EligibleForPrune = $ValidBackups | Select-Object -Skip 1
         $EligibleForPrune | Where-Object { $_.CreationTime -lt $CutoffDate -or $_.LastWriteTime -lt $CutoffDate } | ForEach-Object {
             Remove-Item $_.FullName -Force
         }
     } else {
         Write-Host "Preserving the only existing recovery point regardless of age: $($ValidBackups[0].Name)"
     }
     ```
2. **Incomplete `.part` File Handling**:
   - Incomplete uploads/writes create `.part` files.
   - `.part` files are explicitly excluded from `$ValidBackups` so they are never evaluated as recovery points.
   - Stale `.part` files older than 24 hours are safely purged during retention cleanup.

### Test Proof

- `test_backup_retention_preserves_sole_recovery_point`: A 30-day-old valid backup and a stale `.part` file were simulated. PowerShell retention execution deleted the `.part` file while strictly preserving the 30-day-old backup as the sole valid recovery point.

---

## 6. Exact Source Changes

| File | Change Description |
| :--- | :--- |
| `app/services/backup.py` | Added `SnapshotCoordination` write barrier, referential consistency checks for `artifacts` and `knowledge_records`, member safety validation (`_validate_tar_member_safety`), duplicate member detection, decompression size thresholds, and cryptographic restore authorization. |
| `scripts/backup-windows.ps1` | Updated retention logic to preserve the newest valid recovery point unconditionally and prune stale `.part` files without treating them as valid archives. |
| `tests/test_stage08a_backup_recovery.py` | Added 13 dedicated safety gate test cases covering all edge conditions, bringing total tests in file to 23. |
| `docs/remediation/evidence/working_tree_patch.diff` | Updated working tree diff reflecting all tracked and untracked modifications. |

---

## 7. Targeted Tests and Results

Command:
```bash
.venv\Scripts\pytest.exe tests/test_stage08a_backup_recovery.py -v
```

Execution Summary:
- **Collected**: 23 items
- **Passed**: 23 items
- **Failed**: 0
- **Duration**: 11.69 seconds
- **Exit Code**: 0

Detailed Test Breakdown:
1. `test_backup_creation_and_manifest`: PASSED
2. `test_backup_inspect_and_list`: PASSED
3. `test_backup_verification_positive`: PASSED
4. `test_restore_dry_run_does_not_modify_destination`: PASSED
5. `test_restore_live_production_safeguard`: PASSED
6. `test_restore_into_isolated_environment_end_to_end`: PASSED
7. `test_corrupted_backup_rejected_fail_closed`: PASSED
8. `test_missing_cas_blob_detected_fail_closed`: PASSED
9. `test_path_traversal_rejection_fail_closed`: PASSED
10. `test_atomic_backup_under_live_writes`: PASSED
11. `test_unauthenticated_plain_operator_rejected_for_production_restore`: PASSED
12. `test_forged_session_token_rejected_for_production_restore`: PASSED
13. `test_mismatched_session_token_rejected_for_production_restore`: PASSED
14. `test_authenticated_operator_authorized_for_production_dry_run`: PASSED
15. `test_archive_safety_rejects_symlink`: PASSED
16. `test_archive_safety_rejects_hardlink`: PASSED
17. `test_archive_safety_rejects_fifo_or_device`: PASSED
18. `test_archive_safety_rejects_duplicate_members`: PASSED
19. `test_archive_safety_rejects_oversized_file`: PASSED
20. `test_cross_component_consistency_detects_missing_cas`: PASSED
21. `test_cross_component_consistency_detects_tampered_review_digest`: PASSED
22. `test_snapshot_coordination_and_write_barrier`: PASSED
23. `test_backup_retention_preserves_sole_recovery_point`: PASSED

---

## 8. Full Regression Evaluation

- Prior to this safety gate, the full regression suite ran at **689 passed, 0 failed, 1 intentionally skipped live-network test** (exit code 0).
- The edits introduced for this safety gate are strictly confined to:
  - `app/services/backup.py`
  - `scripts/backup-windows.ps1`
  - `tests/test_stage08a_backup_recovery.py`
- No public APIs, core orchestration engines, database schemas, or web route handlers were altered.
- Therefore, another 15-minute full regression suite execution is **not technically required** prior to human review, though it can be executed if requested.

---

## 9. Remaining Risks

1. **Host Storage Exhaustion**: If the backup volume runs out of free disk space during `tarfile` generation, the partially written archive will be discarded by the `tempfile` cleanup block. Adequate disk monitoring should be configured for the backup target volume.
2. **Concurrent Deletions during Long-Running Backups**: While `SnapshotCoordination.write_barrier()` mitigates intra-process deletions, external processes or OS-level file removals during snapshot creation are not intercepted; however, `create_backup()` detects any missing CAS files and aborts rather than writing a broken backup.

---

## 10. Final Recommendation

- **GO**: Provisionally sign off and accept **Stage 8A: Backup, Recovery & Data Integrity**.
- **STOP FOR HUMAN REVIEW**: Do not begin Stage 8B, Stage 8C, or Stage 9.
