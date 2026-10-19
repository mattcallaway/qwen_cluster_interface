# Qwen Collective — Artifact Database & Storage Migration Plan

## 1. Executive Summary

This document describes the schema evolution, historical data ingestion, and integrity verification plan that introduced the canonical Artifact Subsystem into Qwen Collective via **Migration 13** in [`app/migrations.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/migrations.py).

### Core Goals:
1. Ensure 100% backward compatibility with all historical projects, runs, and tasks.
2. Zero data loss: every previously generated file in `data/artifacts/` and `data/evidence/` is indexed and given a canonical identity.
3. Retrofit existing `deliverables` records with `primary_artifact_id` links.
4. Establish SQLite FTS5 full-text indexing without requiring heavy external dependencies.

---

## 2. Migration 13 Implementation Details

Migration 13 executes within an atomic SQLite transaction (`BEGIN IMMEDIATE`):

### 2.1 Table Additions
1. `artifacts`: Core canonical entity table.
2. `artifact_versions`: Historical version ledger with SHA-256 and change descriptions.
3. `artifact_lineage`: Directed Acyclic Graph tracking derivations, revisions, and analyses.
4. `artifact_project_links`: Multi-project membership and roles (`primary_output`, `imported`, `referenced`).
5. `artifact_task_links`: Input/output linkage between artifacts and execution tasks.
6. `artifact_access_grants`: Explicit cross-project security permissions.
7. `artifact_knowledge_links`: Traceability links to extracted knowledge records.
8. `artifacts_fts`: SQLite FTS5 virtual table for fast full-text searching across names, descriptions, and content.
9. `deliverables` altered: Added columns `primary_artifact_id` (TEXT) and `primary_artifact_version` (INTEGER).

### 2.2 Historical Data Ingestion & Backfill
During migration execution:
1. All 21 pre-existing records in `artifact_metadata` were queried.
2. Physical file paths were inspected. For existing files, byte sizes and SHA-256 checksums were validated.
3. Canonical records were inserted into `artifacts` and `artifact_versions`.
4. Initial `artifact_project_links` were established with `role = 'primary_output'`.
5. Existing records in `deliverables` referencing legacy artifact filenames or storage paths were matched and updated with `primary_artifact_id`.

---

## 3. Pre-Migration Safety & Rollback Procedures

### 3.1 Pre-Migration Backup
Before running migrations, the controller executes `backup_before_migration(conn, db_path)`:
- Creates a timestamped SQLite database copy in `data/backups/qwen_collective_pre_m13_<timestamp>.db`.
- Verifies database file integrity (`PRAGMA integrity_check`) prior to migration.

### 3.2 Rollback Procedure
If Migration 13 fails during execution:
1. SQLite automatically issues `ROLLBACK`, restoring previous state.
2. To restore manually from pre-migration backup:
   ```bash
   cp data/backups/qwen_collective_pre_m13_*.db data/qwen_collective.db
   ```
3. No historical artifact files in `data/artifacts/` or `data/evidence/` are modified or deleted during migration.
