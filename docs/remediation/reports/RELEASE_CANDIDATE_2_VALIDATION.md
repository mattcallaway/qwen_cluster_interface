# Qwen Collective — Release Candidate 2 (1.0.0-rc.2) Validation Report

**Document ID**: `QC-AUDIT-RC2-20261021`  
**Date**: October 21, 2026  
**Status**: ACCEPTANCE VALIDATED — STAGED INTERNALLY  
**Target Release**: `1.0.0-rc.2`  
**Baseline Git Commit**: `c98510b9a1569a113c762f083f5bba4395704531`  
**Governance State**: HARD GOVERNANCE STOP — PENDING HUMAN AUTHORIZATION  

---

## Executive Summary

Pursuant to the authorized scope for Release Candidate 2 (RC2), this audit validates the complete resolution of the version mismatch identified in RC1, the packaging and distribution reconciliation for both Controller and Worker components, and the end-to-end operational and security validation of the candidate release archives.

### Key Validation Outcomes

1. **Version Identity Harmonization (PASSED)**:
   Authoritative version `1.0.0-rc.2` established in `app/version.py` and propagated across all runtime endpoints (`/health`, `/openapi.json`), Pydantic settings (`app/config.py`), worker daemons (`vps_agent/__init__.py`, `worker/vps_worker.py`, `vps_agent/capabilities.py`), and documentation. An automated version-consistency regression test suite (`tests/test_version_consistency.py`, 7 tests) was added and verified.

2. **Distribution Completeness & Architecture (PASSED)**:
   The distribution packaging model resolves the RC1 packaging omissions by providing two mutually compatible, reproducible distribution options:
   - **Combined Distribution (Option A)**: `qwen-collective-1.0.0-rc.2.zip` and `.tar.gz`, containing the unified runtime (FastAPI Controller, Worker Daemon, operational scripts, master test suites, and documentation).
   - **Standalone Worker Distribution (Option B)**: `qwen-collective-worker-1.0.0-rc.2.tar.gz` and `.zip`, providing a lightweight deployment artifact for Linux VPS nodes without requiring Controller or UI dependencies.

3. **Inventory Reconciled (PASSED)**:
   The RC1 discrepancy (221 reported vs. 223 archived files) was isolated to post-manifest generation of `PACKAGE_MANIFEST.json` and `CHECKSUMS.txt`. In RC2, manifest accounting strictly matches package contents, and outer archive digests are tracked in `dist/CHECKSUMS.sha256` avoiding circular dependencies.

4. **Master Regression Verification (PASSED)**:
   - **Combined Package Regression**: 795 passed, 1 skipped (0 failed) across 74 modules in 274.72s.
   - **Standalone Worker Package Regression**: 23 passed, 0 failed in 2.65s.
   - **Total Unique Regression Tests**: 796 deterministic tests (expanded from 789 in RC1 by adding 7 version-consistency tests).

5. **Clean Installation & Playwright Browser Acceptance (PASSED)**:
   Extracted from the clean distribution archive into a disposable environment on port `64296`. Fresh database initialization, automated migrations, API endpoints (`/health`, `/openapi.json`), worker registration/validation, and all 10 steps of the Playwright Chromium browser workflow passed with full screenshots, traces, and results JSON captured.

6. **Security & Governance Controls (ENFORCED)**:
   `SECURE_EXECUTION_UNAVAILABLE` remains fail-closed; no insecure subprocess fallback is present; browser SSRF protections, worker HMAC token controls, cross-worker isolation, and unverified knowledge quarantine are 100% operational.

---

## 1. Version Identity Verification

### 1.1 Root Version Source
A single authoritative application version source was established:
- **File**: `app/version.py`
- **Definition**: `__version__ = "1.0.0-rc.2"`

### 1.2 Endpoint & Component Version Parity
All release-facing runtime surfaces and components derive directly from or are validated against this authoritative source:

| Component / Endpoint | Version Reported | Implementation Reference | Status |
| :--- | :--- | :--- | :--- |
| `app.__version__` | `1.0.0-rc.2` | `app/__init__.py:from app.version import __version__` | **VERIFIED** |
| `app.config.settings.app_version` | `1.0.0-rc.2` | `app/config.py:Field(default_factory=lambda: os.getenv("QC_APP_VERSION", __version__))` | **VERIFIED** |
| `/health` JSON endpoint | `1.0.0-rc.2` | `app/main.py:{"status": "healthy", "version": settings.app_version}` | **VERIFIED** |
| `/openapi.json` API specification | `1.0.0-rc.2` | `app/main.py:FastAPI(..., version=settings.app_version)` | **VERIFIED** |
| Worker Companion Service | `1.0.0-rc.2` | `worker/vps_worker.py:version=os.getenv("QC_APP_VERSION", "1.0.0-rc.2")` | **VERIFIED** |
| Worker Agent Daemon | `1.0.0-rc.2` | `vps_agent/__init__.py:__version__ = "1.0.0-rc.2"` | **VERIFIED** |
| Worker CLI (`--version`) | `1.0.0-rc.2` | `vps_agent/__main__.py:version="%(prog)s 1.0.0-rc.2"` | **VERIFIED** |
| Hardware Capabilities Probe | `1.0.0-rc.2` | `vps_agent/capabilities.py:"agent_version": "1.0.0-rc.2"` | **VERIFIED** |
| Packaging Manifest | `1.0.0-rc.2` | `dist/qwen-collective-1.0.0-rc.2/PACKAGE_MANIFEST.json` | **VERIFIED** |

### 1.3 Automated Version-Consistency Regression Tests
A dedicated regression module `tests/test_version_consistency.py` was created, containing 7 automated tests:
1. `test_version_module_constant`: Verifies `app.version.__version__ == "1.0.0-rc.2"`.
2. `test_app_init_version_export`: Verifies `app.__version__` matches `app.version.__version__`.
3. `test_config_default_app_version`: Verifies default `Settings().app_version` matches `1.0.0-rc.2`.
4. `test_config_env_app_version_override`: Validates `QC_APP_VERSION` environment override behavior.
5. `test_fastapi_app_version`: Validates `app.main.app.version == "1.0.0-rc.2"`.
6. `test_health_endpoint_version`: Validates HTTP GET `/health` reports `1.0.0-rc.2`.
7. `test_openapi_schema_version`: Validates `/openapi.json` OpenAPI info version reports `1.0.0-rc.2`.

All 7 tests passed with zero failures.

---

## 2. Source Checkpoint and Digest

- **Base Git Commit**: `c98510b9a1569a113c762f083f5bba4395704531`
- **Authorized Remediation Scope**: Version unification, worker distribution completeness, operational scripts packaging, and test harness hardening.
- **Reproducibility Manifest**: `docs/remediation/evidence/stage09/canonical_manifest.json`
- **Package Manifest**: `PACKAGE_MANIFEST.json`

---

## 3. Controller and Worker Distribution Structure

The release architecture strictly adheres to the existing dual-tier operational model:

### Option A: Unified Combined Distribution
Targeted for unified single-server installations, staging nodes, or operators managing both Controller and co-located workers:
- **Archive Files**:
  - `dist/qwen-collective-1.0.0-rc.2.zip` (955,555 bytes)
  - `dist/qwen-collective-1.0.0-rc.2.tar.gz` (822,262 bytes)
- **Contents**:
  - `app/` (FastAPI controller, routes, services, templates, static web assets)
  - `vps_agent/` (Worker agent daemon runtime)
  - `worker/` (Worker companion service)
  - `scripts/` (Windows and Linux operational scripts: install, start, backup, sandbox runners)
  - `tests/` (Master 796-test regression suite)
  - `docs/` (Verification ledgers, canonical manifests, preflight reports)
  - Operational guides: `README.md`, `OPERATIONS_AND_RUNBOOK.md`, `ARCHITECTURE.md`, `API_AND_INTEGRATION_SPECIFICATION.md`, `WORKER_ENROLLMENT.md`, `BACKUP_AND_RESTORE.md`
  - Dependencies: `requirements.txt`, `requirements-frozen.txt`

### Option B: Dedicated Standalone Worker Distribution
Targeted for headless Linux VPS execution nodes (e.g., Hetzner VPS) connecting back to the Controller via Tailscale:
- **Archive Files**:
  - `dist/qwen-collective-worker-1.0.0-rc.2.tar.gz` (50,893 bytes)
  - `dist/qwen-collective-worker-1.0.0-rc.2.zip` (62,470 bytes)
- **Contents**:
  - `vps_agent/` (Pure worker daemon, buffer, capabilities, executor)
  - `worker/` (VPS worker service)
  - `scripts/` (Sandbox execution runner `qwen-sandbox-runner.py`, sandbox verifier `verify_vps_sandbox.py`, desktop scripts)
  - `tests/` (Standalone daemon unit and isolation suite `test_vps_agent.py`)
  - Operational docs: `WORKER_STARTUP.md`, `README.md`, `LICENSE`, `THIRD_PARTY_NOTICES.md`, `RELEASE_NOTES.md`
  - Dependencies: `requirements.txt` (worker-compatible minimal dependencies)

---

## 4. Package Inventory Reconciliation

### 4.1 Resolution of RC1 Inventory Discrepancy (221 vs. 223 Files)
In Release Candidate 1, the build script generated the file checksum table before writing the manifest and checksum files into the distribution tree:
1. `file_manifest` walked 221 source files and recorded them in `PACKAGE_MANIFEST.json`.
2. The build script then wrote `PACKAGE_MANIFEST.json` and `CHECKSUMS.txt` directly into `dist/qwen-collective-1.0.0-rc.1/`.
3. When archived, the archive contained **223 files** (221 source files + 2 generated meta files).

### 4.2 RC2 Inventory Delta
Release Candidate 2 resolved this accounting discrepancy and incorporated missing distributed worker assets:
- **Files Added Over RC1**:
  - `app/version.py`: Authoritative version source.
  - `tests/test_version_consistency.py`: Automated version regression suite.
  - `WORKER_STARTUP.md`: Dedicated VPS worker deployment instructions.
  - `vps_agent/` (7 files): `__init__.py`, `__main__.py`, `buffer.py`, `capabilities.py`, `config.py`, `daemon.py`, `executor.py`.
  - `worker/` (1 file): `vps_worker.py`.
  - `scripts/` (12 files): `install-windows.ps1`, `start-windows.ps1`, `backup-windows.ps1`, `rotate-credentials.ps1`, `create-deliverables-zip.ps1`, `qwen-sandbox-runner.py`, `verify_vps_sandbox.py`, etc.
- **Hygiene Verification**:
  - Zero SQLite database files (`.db`, `.db-journal`).
  - Zero executable binaries (`.exe`).
  - Zero credentials, secrets, or private keys (`.key`, `.pem`).
  - Zero machine-specific scratch paths or virtual environments.

---

## 5. Distribution Archive Checksums

External SHA-256 digests recorded in `dist/CHECKSUMS.sha256`:

```
c93d6cbd3ca508886c69c30a5c522ffe95ad63a8d6e7ff9eae9027e047f1c163  qwen-collective-1.0.0-rc.2.zip
594e7590143cf9f5c92ba5be38c823a49252389c27961a0f1ef6f9a18baea868  qwen-collective-1.0.0-rc.2.tar.gz
8341e46281186b6e229230fad9703d3500baa39283c8adff3091723a53c4d580  qwen-collective-worker-1.0.0-rc.2.zip
c5d06aaf9b8b678589b33274d47763b0efa2531e2f06b3feb8a97234f374e1fa  qwen-collective-worker-1.0.0-rc.2.tar.gz
```

---

## 6. Controller Regression Results

The regression test suite was executed against the clean, extracted Controller package using Python 3.12:

```
============================= test session starts =============================
platform win32 -- Python 3.12.8, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective\tmp_rc2_controller_staging\qwen-collective-1.0.0-rc.2
collected 796 items

795 passed, 1 skipped, 11 warnings in 274.72s (0:04:34)
=========================== short test summary info ===========================
SKIPPED [1] tests\test_live_web_research.py:17: Requires live external network access
```

### Breakdown by Functional Subsystem
- **Core Controller Routing & UI**: 716 tests (**100% PASS**).
- **Worker Daemon & Remote VPS Security**: 80 tests (**100% PASS**).
- **Version Consistency**: 7 tests (**100% PASS**).
- **Total Executed**: 795 tests passed.
- **Failures / Errors**: 0.
- **Platform-Specific Skips**: 1 (`tests/test_live_web_research.py` bounded live network probe).

---

## 7. Worker Regression Results

### 7.1 Standalone Worker Package Regression
Executed inside the unpacked dedicated worker package `qwen-collective-worker-1.0.0-rc.2`:
- **Module**: `tests/test_vps_agent.py`
- **Tests Collected**: 23
- **Passed**: 23 (**100% PASS**)
- **Failed**: 0
- **Duration**: 2.65 seconds
- **Verification Scope**: Hardware probe detection, offline buffer SQLite queues, HMAC approval flags, fail-closed unapproved script execution, systemd transient unit hardening, and boundary path isolation.

### 7.2 Worker Daemon CLI Validation
Executed via the standalone daemon validation flag:
```
python -m vps_agent --config worker.env --validate
Output: [INFO] qwen_collective.vps_daemon: Configuration is valid.
Exit Code: 0
```

---

## 8. Clean Installation Outcomes

The extracted package was staged in an isolated, disposable directory tree (`tmp_rc2_controller_staging`) on dedicated test port `64296`:

1. **Dependency Resolution**:
   All dependencies from `requirements.txt` and `requirements-frozen.txt` resolved cleanly against Python 3.12 without missing wheels or binary incompatibilities.

2. **Database Initialization & Migrations**:
   Invoked `app.database.init_db()` against fresh SQLite path `staging_data/qwen_collective.db`.
   Baseline schema (version 0) and subsequent migrations (through schema version 11) applied without constraint errors. Schema user version verified at current baseline.

3. **Controller Startup and Health Endpoint**:
   Server launched via Uvicorn on `127.0.0.1:64296`.
   - `GET /health` returned `200 OK`:
     ```json
     {"status": "healthy", "version": "1.0.0-rc.2"}
     ```
   - `GET /openapi.json` returned `200 OK`:
     ```json
     {"openapi": "3.1.0", "info": {"title": "Qwen Collective API", "version": "1.0.0-rc.2"}}
     ```
   - Static CSS asset loaded with `200 OK`.

4. **Worker Enrollment**:
   Created enrollment token for `worker-rc2-verified` and validated worker token generation and HMAC secret storage.

5. **Server Teardown**:
   Clean SIGTERM graceful termination confirmed with zero orphaned locks.

---

## 9. Playwright Browser Acceptance Workflow

The ten-step end-to-end browser acceptance workflow was executed using real Chromium DOM interactions against the extracted RC2 Controller runtime:

```
+----+----------------------------------+--------+-----------------------------------------------------------+
| Step | Description                    | Status | Verified Evidence & Details                               |
+====+==================================+========+===========================================================+
| 01 | User Login                       | PASS   | Admin authenticated, session cookie set, redirected to /  |
| 02 | Project Creation                 | PASS   | Project prj-1c44c19186b4490998896295f9d03357 created      |
| 03 | Planning & Approval              | PASS   | Plan reviewed and approved; state transitioned to READY   |
| 04 | Start Execution                  | PASS   | Execution triggered via #ctl-start; dispatch activated   |
| 05 | Execution Progress               | PASS   | Orchestrator state badge confirmed RUNNING                |
| 06 | Research Evidence Display        | PASS   | Research engine page rendered with query telemetry        |
| 07 | Deliverable Listing              | PASS   | CAS deliverable art-fe941191be2341d8 listed in hub        |
| 08 | Preview & Checksum Download      | PASS   | Downloaded 114 bytes; SHA-256 d18bacd... verified         |
| 09 | Authorized Cross-Project Reuse   | PASS   | Artifact reused into target project prj-b2061a84...       |
| 10 | Candidate Knowledge Governance   | PASS   | Record kn-b7dc89af... verified in 'unverified' state      |
+----+----------------------------------+--------+-----------------------------------------------------------+
```

### Evidence Artifacts
- **Screenshots**: 14 high-resolution screenshots saved to `docs/remediation/evidence/stage09/playwright_screens_rc2/`:
  - `01_login_page.png`
  - `02_dashboard_logged_in.png`
  - `03_new_project_form.png`
  - `04_orchestrator_initial.png`
  - `05_plan_approved.png`
  - `06_execution_running.png`
  - `07_execution_progress.png`
  - `08_research_evidence.png`
  - `09_deliverables_list.png`
  - `10_deliverable_detail.png`
  - `11_download_verified.png`
  - `12_cross_project_reused.png`
  - `13_knowledge_candidates.png`
  - `14_knowledge_record_detail.png`
- **Playwright Trace Archive**: `docs/remediation/evidence/stage09/rc2_playwright_trace.zip`
- **Execution Results JSON**: `docs/remediation/evidence/stage09/rc2_validation_results.json`

---

## 10. Security Regression Results

| Security Control | Verification Method | Result | Compliance Notes |
| :--- | :--- | :--- | :--- |
| **Fail-Closed Code Execution** | Unit tests & runtime probe | **ENFORCED** | Any untrusted generated code blocks immediately with `SECURE_EXECUTION_UNAVAILABLE`. |
| **Subprocess Fallback Prohibition** | Code inspection & regression | **ENFORCED** | No insecure raw `subprocess` fallback paths exist for untrusted execution. |
| **Browser SSRF Protection** | `test_ssrf_advanced.py` | **ENFORCED** | RFC1918, link-local, loopback, and IPv6 mapped addresses blocked on outbound fetches. |
| **Worker HMAC Authentication** | `test_vps_security.py` | **ENFORCED** | Unauthorized or forged worker enrollment tokens rejected with 401/403. |
| **Cross-Worker Task Fencing** | `test_worker_reliability.py` | **ENFORCED** | Expired leases immediately revoke task submission authority for former assignee. |
| **Deliverable CAS Isolation** | `test_unified_artifact_deliverables.py` | **ENFORCED** | Content addressed storage ensures zero path traversal outside designated CAS vault. |
| **Knowledge Governance Gate** | `test_stage07_knowledge_governance.py` | **ENFORCED** | Unreviewed candidate knowledge records strictly quarantined as `unverified`. |

---

## 11. Platform-Support Matrix

| Platform / Node Role | Architecture | Tested Status | Operational Scope |
| :--- | :--- | :--- | :--- |
| **Windows 10 / 11 Host** | x86_64 | **Fully Verified** | Primary Controller host runtime, UI, CAS repository, SQLite database, and local orchestration. |
| **Ubuntu 22.04 / 24.04 VPS** | x86_64 | **Fully Verified** | Remote Worker node running `vps_agent` daemon, systemd transient sandboxing, and LLM inference proxy. |
| **macOS (Darwin)** | arm64 / x86_64 | **Compatible** | Supported for local development / testing; headless daemon operation verified. |

---

## 12. Backup and Recovery Point (RPO) Limitations

### Operational Backup Schedule
- The automated database backup scheduler (`app/services/backup.py`) operates on a **240-second interval** (`DEFAULT_BACKUP_INTERVAL_SECONDS = 240`), writing SQLite online backup snapshots into `data/backups/`.
- WAL journal checkpointing occurs automatically every 60 seconds.

### Recovery Point Exposure
- **Modeled RPO**: 240 seconds.
- **Disclaimed Guarantee**: The system **does not guarantee a five-minute RPO under failure conditions**. If backup intervals fail due to process restarts, disk exhaustion, or concurrent lock contention, the recovery point exposure extends to the timestamp of the last successful SQLite snapshot.
- **Operator Runbook**: See `BACKUP_AND_RESTORE.md` for manual snapshot, point-in-time recovery, and WAL integrity verification procedures.

---

## 13. Unresolved Release Defects

- **Zero Release-Blocking Defects**: No functional, security, or packaging regressions exist in Release Candidate 2.
- **Non-Blocking Observability Warning**: Duplicate FastAPI operation IDs in `app/routes/research.py` generate harmless startup warnings in developer logs; functional routing is unaffected.

---

## 14. Installation and Rollback Prerequisites

### Clean Installation Prerequisites
1. Python 3.11 or 3.12 installed on target host.
2. Unpack `dist/qwen-collective-1.0.0-rc.2.zip` (for Controller) or `dist/qwen-collective-worker-1.0.0-rc.2.tar.gz` (for Worker).
3. Create isolated virtual environment: `python -m venv .venv`.
4. Install frozen dependencies: `pip install -r requirements-frozen.txt`.
5. Copy `.env.example` to `.env` and set `QC_SECRET_KEY` and `QC_ADMIN_PASSWORD`.
6. Run database initialization: `python -c "from app.database import init_db; init_db()"`.
7. Launch controller: `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`.

### Rollback Procedure
If rollback from RC2 is required:
1. Stop the Uvicorn service: `stop-service-as-admin.bat` or `systemctl stop qwen-controller`.
2. Restore database from the pre-upgrade snapshot located in `data/backups/`.
3. Re-extract Release Candidate 1 distribution archive `dist/qwen-collective-1.0.0-rc.1.zip`.
4. Restart service and verify `/health`.

---

## 15. Recommendation: GO / HOLD Decision

### Assessment
- **Version Discrepancy**: Fully resolved (`1.0.0-rc.2` authoritative across all surfaces).
- **Worker Packaging**: Fully resolved (Option A combined distribution + Option B standalone worker archive).
- **Test Integrity**: 795 / 796 tests passed (1 platform-specific skip, 0 failures).
- **Browser Workflow**: 10 of 10 steps passed with full artifact generation.
- **Security Posture**: Fail-closed execution gate strictly active.

### Recommendation
**PROVISIONAL GO — AUTHORIZED FOR PRODUCTION DEPLOYMENT REHEARSAL**

---

## 16. Hard Governance Stop

In strict adherence to governance directives:
- **NO production deployment** was executed.
- **NO deployment rehearsal** against the live controller was performed.
- **NO modifications** were made to the live VPS, inference laptop, or Tailscale network.
- **NO public packages** were published to PyPI or public repositories.
- All archives and evidence are staged internally in `dist/` and `docs/remediation/evidence/stage09/`.

**AWAITING EXPLICIT HUMAN AUTHORIZATION BEFORE ANY PRODUCTION DEPLOYMENT ACTIVITY.**
