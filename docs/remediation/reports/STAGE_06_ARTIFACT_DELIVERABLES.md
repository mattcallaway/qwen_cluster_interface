# Stage 6 — Canonical Immutable Artifact Subsystem Report

**Stage:** Stage 6 (Canonical Immutable Artifacts, One-Click Deliverables, and Cross-Project Reuse)  
**Date:** 2026-10-20  
**Status:** **ACCEPTED — COMPLETE**  
**Active Branch:** `feature/unified-artifact-deliverables`  
**Baseline Test Evidence:** 656 collected, 655 passed, 1 known preexisting baseline failure (`test_knowledge_page`), 0 skipped, 0 errors across 64 modules  
**Covered Findings:** **F-11** (FIXED), plus **F-10**, **F-12**, **F-16**, **F-17**, **F-18**  
**Enforced Invariants:** **I-04**, **I-05**, **I-08**, **I-09**, **I-10**, **I-14**  

---

## 1. Executive Summary

Stage 6 completes the comprehensive remediation of the artifact and deliverable architecture (**Finding F-11**), unifying legacy fragmented metadata storage with a hardened, content-addressed storage (CAS) subsystem and an immutable artifact lineage ledger.

Under Stage 6:
1. **Content-Addressed Storage (CAS) with Project Isolation (Finding F-11 / Invariant I-09):** All worker and controller artifacts are stored via deterministic SHA-256 content-addressing. Identical file contents uploaded across separate projects or tasks safely deduplicate at the physical storage layer without leaking permissions, metadata, or access across authorization boundaries.
2. **Distinct Artifact Identities for Identical Filenames (Invariant I-09):** Tasks or workers creating files with identical display names receive distinct primary keys, distinct storage paths, and distinct metadata records. Overwriting and colliding are strictly prevented.
3. **Task Retry & Attempt Preservation:** Retried tasks preserve prior attempt logs and outputs. Files produced in attempt $N > 1$ are scoped by attempt number (`att{N}_{filename}`), preventing task retries from clobbering diagnostic or failed attempt evidence.
4. **Producer Attempt Commit Verification:** Stale, superseding, or unapproved task attempts cannot commit artifacts into the deliverable stream. Only currently assigned and active attempts have commit authority.
5. **Worker Isolation & Authorization Enforcement (Finding F-10 / Invariant I-04):** Unauthorized workers cannot view, download, or reuse artifacts belonging to tasks assigned to other workers.
6. **Immutable Lineage & Authorized Cross-Project Reuse (Invariant I-10):** Artifact lineage records (`artifact_lineage`) capture exact root and derivative relationships with explicit transformation types (`derived_from`, `reused_in`, `referenced_by`). Reusing an artifact in another project creates an explicit downstream lineage node while preserving the immutability of the original source artifact.
7. **Safe Inline Previews & Script Disabling:** Previews for active web formats (`text/html`, `image/svg+xml`) disable active script execution and strip hazardous tags, returning sandboxed HTML previews with strict Content-Security-Policy headers.
8. **Deliverable Completeness & Physical Verification Gate (Invariant I-08 / Invariant I-14):** Orchestration runs verify both database metadata and physical storage/CAS availability of all required artifacts. If any required artifact file is physically missing or unreadable, the run transitions to `BLOCKED` with an actionable failure reason, strictly preventing illegitimate `COMPLETED` status.

---

## 2. Architecture & Implementation Changes

### 2.1 Schema & Migration 16 (`app/migrations.py`)
- **Canonical Artifact Tables:** Verified and synchronized tables:
  - `artifacts`: Core metadata, mime type, size, checksum, storage key, version number, project isolation.
  - `artifact_versions`: Immutable version records with producing run, task, worker, and attempt tracking.
  - `artifact_lineage`: Directed acyclic graph recording source artifact ID, target artifact ID, lineage type, and metadata.
  - `cas_blobs`: Content-addressed storage index mapping SHA-256 hashes to reference counts and storage locations.
- **Legacy Compatibility Synchronization:**
  - Extended `artifact_metadata` with `cas_hash` and `producer_attempt` columns.
  - Maintained bidirectional synchronization between `artifact_metadata` and canonical `artifacts`/`artifact_versions` tables during worker uploads and controller writes.

### 2.2 Storage Layer & CAS Unification (`app/services/artifacts/storage.py`)
- **Atomic CAS Writes:** `store_cas_blob(data)` stores blobs under `<artifacts_dir>/cas/ab/cd/abcdef...` using atomic staging files (`.tmp`) and renaming, guaranteeing zero partial writes.
- **Reference Counting:** Storing existing blobs increments reference counts without duplicate disk consumption.
- **Confinement & Anti-Traversal:** All storage operations enforce strict path confinement within `settings.evidence_dir` and `settings.artifacts_dir`. Path traversal attempts outside authorized roots raise immediate `NotFound` exceptions without falling back to CAS.

### 2.3 Artifact Service & Lineage Tracking (`app/services/artifacts/service.py`)
- **Scoped Registration:** `register_artifact` accepts bytes or streams, hashes content, checks size limits (25MB maximum), stores into CAS, creates version 1 records, and returns normalized artifact dictionaries.
- **Authorization Enforcement:** `resolve_download` and `get_artifact` verify project ownership (`project_id`) and worker assignment. Unauthorized cross-project or cross-worker requests raise `Forbidden` or `NotFound`.
- **Lineage Creation:** `record_lineage` logs explicit derivative edges (`parent_artifact_id` $\rightarrow$ `child_artifact_id`).
- **Graph Inspection:** `get_lineage_graph` returns roots, derivatives, and topological relationships for any artifact.
- **Safe Preview Generator:** `generate_preview` limits preview size (100KB), parses HTML/SVG to strip `<script>`, `onload`, `onclick`, and external references, and serves pre-sanitized content.

### 2.4 Deliverable Evaluation & Storage Integrity Gate (`app/services/orchestration/engine.py`)
- **Storage Existence Check:** During `_evaluate_deliverables`, candidate artifacts for all required or terminal deliverables must verify physical file presence in filesystem storage or CAS.
- **Actionable Blocking:** If physical storage is missing or corrupted, the orchestrator sets `state = 'BLOCKED'` with detail `Required artifact for deliverable '<name>' is missing from storage.`, preventing run completion.

---

## 3. Mandatory Integration Test Evidence (Tests A through L)

### 3.1 Subsystem Tests (`tests/test_stage06_artifact_subsystem.py`)

All 12 integration test scenarios in [`tests/test_stage06_artifact_subsystem.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage06_artifact_subsystem.py) passed 100%:

| Test ID | Test Method Name | Invariant / Requirement Verified | Result |
|:---:|---|---|:---:|
| **Test A** | `test_a_identical_filenames_different_content_independently_downloadable` | **Invariant I-09:** Two distinct files with the same filename (`script.py`) uploaded by different workers/tasks receive independent artifact IDs and storage paths, and download independently. | **PASSED** |
| **Test B** | `test_b_duplicate_content_deduplicates_safely_without_merging_permissions` | **Finding F-11 / Invariant I-09:** Identical content uploaded across separate projects deduplicates at the CAS layer while maintaining strict project-scoped authorization. | **PASSED** |
| **Test C** | `test_c_worker_b_cannot_view_or_reuse_worker_a_unauthorized_artifact` | **Finding F-10 / Invariant I-04:** Worker B is forbidden from accessing or reusing artifacts produced by Worker A on unassigned tasks. | **PASSED** |
| **Test D** | `test_d_stale_or_unapproved_producer_attempts_cannot_commit_artifacts` | **Invariant I-05:** Stale or superseded task attempt numbers cannot commit artifacts to orchestration tasks. | **PASSED** |
| **Test E** | `test_e_task_level_retry_preserves_previous_attempt_outputs` | **Invariant I-09:** Retrying a task with the same filename scopes outputs by attempt number (`att{N}_{filename}`), preserving attempt 1 logs without clobbering. | **PASSED** |
| **Test F** | `test_f_deliverable_with_multiple_supporting_files_produces_complete_index` | **Deliverable Completeness:** A deliverable bundle indexing multiple supporting files produces a comprehensive manifest of valid artifacts. | **PASSED** |
| **Test G** | `test_g_reused_artifact_creates_lineage_and_preserves_original` | **Invariant I-10:** Reusing an artifact in another project creates an explicit downstream lineage edge without mutating or reattributing the source artifact. | **PASSED** |
| **Test H** | `test_h_oversized_artifact_rejected` | **Storage Protection:** Artifacts exceeding the 25MB size limit are rejected with an explicit validation error. | **PASSED** |
| **Test I** | `test_i_large_artifact_download_supports_streaming` | **Performance / Streaming:** Artifact downloads support chunked streaming iterators for efficient memory usage. | **PASSED** |
| **Test J** | `test_j_inline_preview_of_html_or_svg_disables_active_scripts` | **Security / Sandbox:** Previewing HTML or SVG files disables active scripts, event handlers, and javascript URIs. | **PASSED** |
| **Test K** | `test_k_artifact_lineage_graph_identifies_root_and_derivative_outputs` | **Invariant I-10:** Lineage graph accurately resolves root source artifacts and derivative downstream outputs. | **PASSED** |
| **Test L** | `test_l_missing_required_artifacts_block_completed_status` | **Invariant I-08 / Invariant I-14:** Deliverables referencing non-existent physical files block run completion and transition to `BLOCKED`. | **PASSED** |

### 3.2 Unified Lifecycle Tests (`tests/test_stage06_unified_artifacts.py`)

All 10 integration test scenarios in [`tests/test_stage06_unified_artifacts.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage06_unified_artifacts.py) passed 100%:

| Test ID | Test Method Name | Invariant / Requirement Verified | Result |
|:---:|---|---|:---:|
| **Test A** | `test_a_file_deliverable_registered_with_metadata_and_checksum` | Full metadata, CAS checksum, version 1 indexing, download integrity. | **PASSED** |
| **Test B** | `test_b_structured_data_deliverable_registered` | Structured JSON deliverable storage, structured preview parsing (`json_data`). | **PASSED** |
| **Test C** | `test_c_combined_artifact_package_with_component_links` | Multi-component release package manifest and DB relationships. | **PASSED** |
| **Test D** | `test_d_task_rerun_creates_new_version_preserving_previous` | Task rerun creates version 2 while preserving version 1 and exact checksums. | **PASSED** |
| **Test E** | `test_e_artifact_reused_in_project_logged_with_lineage` | In-project reuse logging in `artifact_reuses` with explicit `action_type`. | **PASSED** |
| **Test F** | `test_f_cross_project_sharing_and_access_grant` | Cross-project access grants (`artifact_access_grants`) and project linkage. | **PASSED** |
| **Test G** | `test_g_active_markup_cannot_execute_script_in_origin` | Script execution blocked; attachment forced or text/plain served with sandbox CSP. | **PASSED** |
| **Test H** | `test_h_large_artifact_preview_truncation` | 64KB preview boundary enforcement with full un-truncated download streaming. | **PASSED** |
| **Test I** | `test_i_knowledge_extracted_creates_candidate_records_with_links` | Factual knowledge extraction with provenance link to artifact & version. | **PASSED** |
| **Test J** | `test_j_revoked_artifact_cannot_be_downloaded_previewed_or_reused` | Revocation enforcement: access checks, downloads, previews, and reuses rejected. | **PASSED** |

---

## 4. Full Regression Test Evidence

The automated regression suite runner (`run_evidence_suite.py`) was executed across all 64 test modules in the repository:

- **Total Collected Tests:** 656
- **Passed:** 655
- **Failed:** 1 (`tests.test_web_routes.test_knowledge_page` — the known preexisting baseline failure)
- **Skipped:** 0
- **Errors:** 0
- **Regressions:** 0
- **Execution Time:** 193.4 seconds
- **JUnit Report Artifact:** [`docs/remediation/evidence/junit_report.xml`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/junit_report.xml)
- **Test Inventory Artifact:** [`docs/remediation/evidence/test_ids_inventory.md`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/test_ids_inventory.md)
- **Checkpoint Manifest:** [`docs/remediation/checkpoints/stage06_final_checkpoint_manifest.json`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/checkpoints/stage06_final_checkpoint_manifest.json) (3,855 files verified)

---

## 5. Traceability to Invariants and Audit Findings

| Finding / Invariant | Title | Resolution Description | Verification Reference |
|---|---|---|---|
| **F-11** | Artifact Storage Fragmentation & Missing CAS | Fixed. Centralized CAS storage backend with SHA-256 deduplication, immutable version records, and reference counting. | `tests/test_stage06_artifact_subsystem.py::test_b_duplicate_content_deduplicates_safely_without_merging_permissions` |
| **I-04** | Authenticated and Authorized Execution | Enforced. Cross-worker and cross-project artifact downloads and reuses strictly rejected. | `tests/test_stage06_artifact_subsystem.py::test_c_worker_b_cannot_view_or_reuse_worker_a_unauthorized_artifact` |
| **I-05** | At-least-once Processing with Effectively-once Recording | Enforced. Stale or superseded attempt numbers rejected on artifact commit. | `tests/test_stage06_artifact_subsystem.py::test_d_stale_or_unapproved_producer_attempts_cannot_commit_artifacts` |
| **I-08** | Honest Completion | Enforced. Orchestration runs cannot complete if required deliverable artifacts are missing from storage. | `tests/test_stage06_artifact_subsystem.py::test_l_missing_required_artifacts_block_completed_status` |
| **I-09** | Immutable Outputs | Enforced. Unique artifact records, attempt-scoped retry filenames, zero clobbering, and CAS content addressing. | `tests/test_stage06_artifact_subsystem.py::test_a_identical_filenames_different_content_independently_downloadable`, `test_e_task_level_retry_preserves_previous_attempt_outputs` |
| **I-10** | Full Lineage and Explicit Reuse | Enforced. Immutable `artifact_lineage` tracking roots, derivatives, and cross-project reuses. | `tests/test_stage06_artifact_subsystem.py::test_g_reused_artifact_creates_lineage_and_preserves_original`, `test_k_artifact_lineage_graph_identifies_root_and_derivative_outputs` |
| **I-14** | Explicit Degraded States | Enforced. Missing physical artifacts trigger explicit `BLOCKED` status with clear error messaging. | `tests/test_stage06_artifact_subsystem.py::test_l_missing_required_artifacts_block_completed_status` |
