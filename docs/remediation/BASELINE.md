# System Baseline & Environmental Audit

**Date:** 2026-10-18  
**Repository:** `https://github.com/mattcallaway/qwen_cluster_interface`  
**Working Directory:** `C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective`  
**Git Branch:** `feature/unified-artifact-deliverables`  
**Baseline Git Commit SHA:** `c98510b`  
**Git Working Tree Status:** Clean (no untracked or uncommitted functional changes prior to remediation)

---

## 1. Operating System & Controller Host Audit

| Property | Value / Assessment | Verification Method | Notes / Operational Risk |
|---|---|---|---|
| **Host Operating System** | Microsoft Windows 10 Pro (64-bit) | `[System.Environment]::OSVersion` | Privileged controller host |
| **Build & Revision** | Build 10.0.19045.0 (22H2) | `HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion` | Windows 10 Version 22H2 |
| **System Architecture** | AMD64 (x86_64) | `platform.machine()` | Native 64-bit |
| **Support Lifecycle Status** | **END OF SERVICING (EOS)** | Microsoft Lifecycle Policy | General mainstream support ended **October 14, 2025**. Local system date is **October 18, 2026** (~1 year post-EOS). |
| **Extended Security Updates (ESU)** | **UNCONFIRMED / OUTSTANDING RISK** | Non-privileged inspection | Microsoft provides an ESU program through October 2028. Verification of active ESU licensing requires administrative licensing queries. |
| **Environment Classification** | Local Development & Control Workstation | System context | The cluster controller process currently executes on this local Windows host. |
| **Migration Plan Requirement** | **MIGRATION REQUIRED FOR PRODUCTION** | Operational Policy | A supported OS migration plan (e.g. Windows 11 Enterprise, Windows Server 2022/2025, or Ubuntu 24.04 LTS / Debian 12) is required before production deployment. Tracked as **RSK-13**. |

---

## 2. Runtime Environment & Dependency Specifications

| Component | Specification / Version | Verification Command | Notes |
|---|---|---|---|
| **Python Runtime** | Python 3.12.10 (64-bit) | `.venv\Scripts\python.exe -V` | Active virtualenv at `.venv` |
| **SQLite Engine** | SQLite 3.49.1 | `sqlite3.sqlite_version` | WAL mode supported, JSON1 enabled |
| **FastAPI** | 0.143.0 | `fastapi.__version__` | ASGI framework |
| **Pydantic** | 2.14.1 | `pydantic.__version__` | V2 validation models |
| **Uvicorn** | 0.54.0 | `uvicorn.__version__` | ASGI server |
| **Pytest** | 9.1.1 | `pytest.__version__` | Test runner |
| **HTTPX** | 0.28.1 | `httpx.__version__` | Async/sync HTTP client |
| **BeautifulSoup4** | 4.15.0 | `bs4.__version__` | HTML parser |
| **Playwright** | 1.48.0 (Installed) | `import playwright` | Browser automation |
| **Trafilatura** | NOT INSTALLED | `import trafilatura` | Optional text extractor; BeautifulSoup fallback active |

---

## 3. Test Suite Baseline Execution & Complete Per-Module Breakdown

- **Exact Pytest Invocation:** `.venv/Scripts/python.exe -m pytest tests -q`
- **Working Directory:** `C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective`
- **Pytest Exit Code:** 1 (due to 2 failing assertions)
- **Total Collected:** 462
- **Total Passed:** 460
- **Total Failed:** 2
- **Total Skipped:** 0
- **Total XFail:** 0
- **Total Errors:** 0
- **Execution Duration:** 173.31s (~2m 53s)
- **Sanitized Evidence File:** [`docs/remediation/evidence/pytest_baseline_output.txt`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/pytest_baseline_output.txt)

### Complete 55-Module Test Breakdown Table

| # | Test Module Path | Collected | Passed | Failed | Skipped | Error | Module Status |
|:---:|---|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | `tests/test_audit_repairs.py` | 17 | 17 | 0 | 0 | 0 | PASS |
| 2 | `tests/test_audit_security.py` | 19 | 19 | 0 | 0 | 0 | PASS |
| 3 | `tests/test_auth.py` | 2 | 2 | 0 | 0 | 0 | PASS |
| 4 | `tests/test_auth_ratelimit.py` | 5 | 5 | 0 | 0 | 0 | PASS |
| 5 | `tests/test_chat_isolation.py` | 4 | 4 | 0 | 0 | 0 | PASS |
| 6 | `tests/test_chat_project_integration.py` | 3 | 3 | 0 | 0 | 0 | PASS |
| 7 | `tests/test_continuous_research_knowledge.py` | 8 | 8 | 0 | 0 | 0 | PASS |
| 8 | `tests/test_deliverable_artifact_resolution.py` | 2 | 2 | 0 | 0 | 0 | PASS |
| 9 | `tests/test_distributed_artifact_lifecycle.py` | 1 | 1 | 0 | 0 | 0 | PASS |
| 10 | `tests/test_end_to_end_repair.py` | 1 | 1 | 0 | 0 | 0 | PASS |
| 11 | `tests/test_inference.py` | 3 | 3 | 0 | 0 | 0 | PASS |
| 12 | `tests/test_infrastructure.py` | 7 | 7 | 0 | 0 | 0 | PASS |
| 13 | `tests/test_infrastructure_routes.py` | 9 | 9 | 0 | 0 | 0 | PASS |
| 14 | `tests/test_knowledge.py` | 4 | 4 | 0 | 0 | 0 | PASS |
| 15 | `tests/test_knowledge_meta.py` | 19 | 19 | 0 | 0 | 0 | PASS |
| 16 | `tests/test_live_multistage_research_integration.py` | 1 | 1 | 0 | 0 | 0 | PASS |
| 17 | `tests/test_live_web_research.py` | 1 | 1 | 0 | 0 | 0 | PASS |
| 18 | `tests/test_machines.py` | 5 | 5 | 0 | 0 | 0 | PASS |
| 19 | `tests/test_markdown_safety.py` | 3 | 3 | 0 | 0 | 0 | PASS |
| 20 | `tests/test_metric_integrity.py` | 11 | 11 | 0 | 0 | 0 | PASS |
| 21 | `tests/test_migration_compatibility.py` | 3 | 3 | 0 | 0 | 0 | PASS |
| 22 | `tests/test_migrations.py` | 8 | 8 | 0 | 0 | 0 | PASS |
| 23 | `tests/test_milestone2.py` | 8 | 8 | 0 | 0 | 0 | PASS |
| 24 | `tests/test_milestone2_routes.py` | 6 | 6 | 0 | 0 | 0 | PASS |
| 25 | `tests/test_milestone3.py` | 9 | 9 | 0 | 0 | 0 | PASS |
| 26 | `tests/test_milestone3_routes.py` | 6 | 6 | 0 | 0 | 0 | PASS |
| 27 | `tests/test_milestone4.py` | 6 | 6 | 0 | 0 | 0 | PASS |
| 28 | `tests/test_milestone4_routes.py` | 7 | 7 | 0 | 0 | 0 | PASS |
| 29 | `tests/test_milestone5.py` | 5 | 5 | 0 | 0 | 0 | PASS |
| 30 | `tests/test_milestone5_routes.py` | 5 | 5 | 0 | 0 | 0 | PASS |
| 31 | `tests/test_milestone_2_acceptance.py` | 1 | 1 | 0 | 0 | 0 | PASS |
| 32 | `tests/test_model_discovery.py` | 4 | 4 | 0 | 0 | 0 | PASS |
| 33 | `tests/test_observatory.py` | 9 | 9 | 0 | 0 | 0 | PASS |
| 34 | `tests/test_orchestrator_core.py` | 1 | 1 | 0 | 0 | 0 | PASS |
| 35 | `tests/test_orchestrator_project_isolation.py` | 8 | 8 | 0 | 0 | 0 | PASS |
| 36 | `tests/test_orchestrator_routes.py` | 7 | 7 | 0 | 0 | 0 | PASS |
| 37 | `tests/test_orchestrator_units.py` | 67 | 66 | **1** | 0 | 0 | **FAIL** |
| 38 | `tests/test_probe_parser.py` | 9 | 9 | 0 | 0 | 0 | PASS |
| 39 | `tests/test_projects_m1.py` | 30 | 30 | 0 | 0 | 0 | PASS |
| 40 | `tests/test_projects_routes.py` | 19 | 19 | 0 | 0 | 0 | PASS |
| 41 | `tests/test_research_engine.py` | 7 | 7 | 0 | 0 | 0 | PASS |
| 42 | `tests/test_research_security.py` | 6 | 6 | 0 | 0 | 0 | PASS |
| 43 | `tests/test_ssrf_advanced.py` | 9 | 9 | 0 | 0 | 0 | PASS |
| 44 | `tests/test_tasks.py` | 4 | 4 | 0 | 0 | 0 | PASS |
| 45 | `tests/test_ui_states.py` | 3 | 3 | 0 | 0 | 0 | PASS |
| 46 | `tests/test_unified_artifact_deliverables.py` | 9 | 9 | 0 | 0 | 0 | PASS |
| 47 | `tests/test_vps.py` | 1 | 1 | 0 | 0 | 0 | PASS |
| 48 | `tests/test_vps_agent.py` | 23 | 23 | 0 | 0 | 0 | PASS |
| 49 | `tests/test_vps_daemon.py` | 16 | 16 | 0 | 0 | 0 | PASS |
| 50 | `tests/test_vps_security.py` | 6 | 6 | 0 | 0 | 0 | PASS |
| 51 | `tests/test_vps_worker.py` | 5 | 5 | 0 | 0 | 0 | PASS |
| 52 | `tests/test_web_routes.py` | 7 | 6 | **1** | 0 | 0 | **FAIL** |
| 53 | `tests/test_worker_api.py` | 10 | 10 | 0 | 0 | 0 | PASS |
| 54 | `tests/test_worker_reliability.py` | 7 | 7 | 0 | 0 | 0 | PASS |
| 55 | `tests/test_worker_service.py` | 6 | 6 | 0 | 0 | 0 | PASS |
| **Total** | **55 Modules** | **462** | **460** | **2** | **0** | **0** | **Baseline Captured** |

---

## 4. Root-Cause Analysis of Preexisting Baseline Failures

### Failure 1: `test_extract_json_object_handles_prose_and_fences`
- **Location:** `tests/test_orchestrator_units.py:187`
- **Assertion:** `assert extract_json_object(txt) == {"a": {"b": "}"}, "c": [1, 2]}`
- **Observed Behavior:** `extract_json_object` returns `None`.
- **Root Cause:** In `app/services/orchestration/planner.py`, `extract_json_object` filters candidate JSON objects using a planning-key heuristic (`overview`, `objective`, `tasks`, etc.) to prevent matching conversational model chatter. The unit test passed a generic test payload lacking these keys.
- **Resolution Plan:** Parameterize `extract_json_object(txt, required_keys=None)` in Stage 2.

### Failure 2: `test_knowledge_page`
- **Location:** `tests/test_web_routes.py:36`
- **Assertion:** `assert "Persistent Knowledge Repository" in response.text`
- **Observed Behavior:** HTTP 200 returned, but text contains `"Collective Knowledge & Evidence Repository"`.
- **Root Cause:** The template `app/templates/knowledge/index.html` was updated in a recent commit. The test was not updated to match the new UI copy.
- **Resolution Plan:** Update test assertion in Stage 8 to reflect authoritative template copy.
