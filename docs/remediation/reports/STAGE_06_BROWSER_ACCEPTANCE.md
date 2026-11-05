# Stage 6 User Workflow Browser Acceptance Test Report

**Governance Status:**  
- **STAGE 6 — FUNCTIONALLY ACCEPTED, RELEASE VALIDATION PENDING**  
- **Stage 7: HOLD (Pending Final Integration Review)**  
- **Stage 8: HOLD (Unauthorized)**  
- **Stage 9: HOLD (Unauthorized)**  

**Report Date:** 2026-10-20  
**Test Environment:** Disposable local development instance (`data/tmp_stage06_browser_acceptance`)  
**Automation Engine:** Playwright Chromium (Headless) via Python `.venv`  
**Playwright Trace:** [`docs/remediation/reports/stage06_browser_trace.zip`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/reports/stage06_browser_trace.zip) (Size: 5.46 MB, Timestamp: 2026-10-20 22:12:42 UTC-7)  

---

## 1. Executive Summary

This report documents the final repair of two identified user interface defects and the successful browser acceptance verification for **Stage 6: Unified Deliverables and Artifact Lifecycle**.

All **8 core acceptance workflows** were verified end-to-end through the real browser interface using Playwright Chromium.

Both identified UI defects were corrected:
1. **FIX 1:** Corrected the Starlette-compatible `TemplateResponse` calling convention in [`app/routes/deliverables.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/routes/deliverables.py), resolving HTTP 500 errors on `/deliverables` and `/deliverables/{artifact_id}`.
2. **FIX 2:** Added an accessible, visible **Reuse** action dropdown with CSRF-protected submission and destination project selector directly into [`app/templates/projects/detail.html`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/templates/projects/detail.html) (both Deliverables and Output Assets tables).

Targeted test suite execution: **11/11 PASSED** in `tests/test_stage06_unified_artifacts.py`.  
Browser acceptance workflows: **8/8 PASSED** in Playwright UI automation.

---

## 2. Original Failures & Root Causes

| Defect | Route / Component | Original Failure | Root Cause |
|---|---|---|---|
| **Defect 1** | `GET /deliverables`<br>`GET /deliverables/{id}` | `HTTP 500 Internal Server Error` (`TypeError: unhashable type: 'dict'`) | `templates.TemplateResponse` was called with positional arguments `TemplateResponse("name", context_dict)`. In Starlette >= 0.28, the first positional argument is interpreted as `request`, causing Jinja2 to treat `context_dict` as the template name string and fail template caching. In addition, the route form handlers assumed `get_current_user` returned a `dict`, whereas `app.auth` returns a `str` username. |
| **Defect 2** | `app/templates/projects/detail.html` | Missing direct "Reuse" action button in the Project Deliverables UI | The Project Deliverables tab only provided "Open / Preview" and "Download" buttons. Users could not initiate artifact reuse directly from within a project without navigating to the artifact detail page. |

---

## 3. Exact Code Changes

### A. FIX 1 — `app/routes/deliverables.py`
Updated calling convention for `TemplateResponse` to use standard keyword arguments (`request=request, name=..., context={...}`) and made username extraction robust for string or dictionary types:

```python
# Lines 80-84: Global Deliverables Hub
return templates.TemplateResponse(
    request=request,
    name="deliverables/index.html",
    context={
        "user": user,
        "csrf_token": get_csrf_token(request),
        ...
    },
)

# Lines 142-146: Deliverable Detail View
return templates.TemplateResponse(
    request=request,
    name="deliverables/detail.html",
    context={
        "user": user,
        "csrf_token": get_csrf_token(request),
        ...
    },
)

# Lines 180-250: Form handler username extraction
actor = user if isinstance(user, str) else (user.get("username", "admin") if user else "admin")
created_by = user if isinstance(user, str) else (user.get("username", "admin") if user else "admin")
```

### B. FIX 2 — `app/templates/projects/detail.html`
1. **Project Deliverables Table (Lines 558-605):** Added a `<details class="dropdown">` containing a "Reuse ▾" button alongside Open and Download. The dropdown displays the artifact name and ID, destination project selector (current project + candidate projects), action selector (`analyze`, `revise`, `expand`, `summarize`, `convert`, `extract_data`), instructions input, and CSRF-protected form submitting to `/deliverables/{{ d.primary_artifact_id }}/reuse`.
2. **Project Output Assets Table (Lines 720-760):** Added matching "Reuse ▾" dropdown submitting to `/deliverables/{{ art.id }}/reuse`.
3. **Tasks Table Indicator (Lines 400-435):** Added "Artifact Reuse" action badge and "Input Artifact Linked" badge to task rows to clearly display bound input artifacts when returned to the destination project's task tab.

### C. Test Harness — `tests/test_stage06_unified_artifacts.py`
Added targeted regression test `test_k_deliverables_routes_render_ok_and_deny_unauthenticated` asserting that `/deliverables` and `/deliverables/{artifact_id}` return HTTP 200 with HTML content for authenticated users, while unauthenticated requests are redirected or denied.

---

## 4. Targeted Test Suite Results

Command:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_stage06_unified_artifacts.py -v
```

Output:
```text
============================= test session starts =============================
platform win32 -- Python 3.12.10, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective
collecting ... collected 11 items

tests/test_stage06_unified_artifacts.py::test_a_file_deliverable_registered_with_metadata_and_checksum PASSED [  9%]
tests/test_stage06_unified_artifacts.py::test_b_structured_data_deliverable_registered PASSED [ 18%]
tests/test_stage06_unified_artifacts.py::test_c_combined_artifact_package_with_component_links PASSED [ 27%]
tests/test_stage06_unified_artifacts.py::test_d_task_rerun_creates_new_version_preserving_previous PASSED [ 36%]
tests/test_stage06_unified_artifacts.py::test_e_artifact_reused_in_project_logged_with_lineage PASSED [ 45%]
tests/test_stage06_unified_artifacts.py::test_f_cross_project_sharing_and_access_grant PASSED [ 54%]
tests/test_stage06_unified_artifacts.py::test_g_active_markup_cannot_execute_script_in_origin PASSED [ 63%]
tests/test_stage06_unified_artifacts.py::test_h_large_artifact_preview_truncation PASSED [ 72%]
tests/test_stage06_unified_artifacts.py::test_i_knowledge_extracted_creates_candidate_records_with_links PASSED [ 81%]
tests/test_stage06_unified_artifacts.py::test_j_revoked_artifact_cannot_be_downloaded_previewed_or_reused PASSED [ 90%]
tests/test_stage06_unified_artifacts.py::test_k_deliverables_routes_render_ok_and_deny_unauthenticated PASSED [100%]

======================== 11 passed, 1 warning in 3.26s ========================
```

---

## 5. Updated 8-Workflow Acceptance Matrix

All 8 workflows executed against the live browser interface (Playwright Chromium) in the isolated test environment:

| # | User Action | Status | Exact Route | UI vs API | Observed Result | Evidence Screenshot |
|---|-------------|--------|-------------|-----------|-----------------|---------------------|
| **1** | Open an existing project | **PASS** | `/projects/{project_id}` | **UI** | Successfully opened project "Quantum Dynamics Simulation". All navigation tabs rendered cleanly. | [`01_open_project.png`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/reports/screenshots/01_open_project.png) |
| **2** | Locate the project's generated deliverables | **PASS** | `/projects/{project_id}?tab=deliverables`<br>`/deliverables` | **UI** | Located deliverables and output assets in project tab. Navigated to Global Hub (`/deliverables`), which returned HTTP 200 with full asset catalog and search filtering. | [`02_locate_deliverables_project_tab.png`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/reports/screenshots/02_locate_deliverables_project_tab.png)<br>[`02_locate_deliverables_hub.png`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/reports/screenshots/02_locate_deliverables_hub.png) |
| **3** | Preview a deliverable directly in the interface | **PASS** | `/deliverables/{artifact_id}` | **UI** | Opened detail page in browser. Safe File Preview panel rendered formatted Markdown directly in UI. Active SVG rendered as inert code block; script execution remained inert (`window.XSS_EXECUTED` was undefined). | [`03_preview_deliverable_ui.png`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/reports/screenshots/03_preview_deliverable_ui.png) |
| **4** | Download its original immutable version with one click | **PASS** | `/projects/{project_id}?tab=deliverables`<br>`/api/v1/artifacts/{id}/download` | **UI** | Clicked "Download" in Project Deliverables table. Browser captured file download (437 bytes). Computed SHA-256 (`028e925b22df...`) matches recorded checksum exactly. | [`04_download_deliverable.png`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/reports/screenshots/04_download_deliverable.png) |
| **5** | View provenance, version, producing task, and verification status | **PASS** | `/deliverables/{artifact_id}` | **UI** | Detail page displays Version `v1`, SHA-256 prefix, producing task (`task-sim-report`), producing run (`run-sim-alpha-101`), and recorded verification results. | [`05_provenance_and_metadata.png`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/reports/screenshots/05_provenance_and_metadata.png) |
| **6** | Reuse the deliverable as an input to the same project | **PASS** | `/projects/{alpha_id}?tab=deliverables` → `/deliverables/{id}/reuse` → `/projects/{alpha_id}?tab=tasks` | **UI** | Opened "Reuse ▾" dropdown in Deliverables table, selected Current Project and "analyze" action, submitted form with CSRF token. Redirected to Tasks tab showing new task "Analyze artifact: quantum_dynamics_benchmark.md" with "Input Artifact Linked". | [`06_reuse_same_project.png`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/reports/screenshots/06_reuse_same_project.png) |
| **7** | Reuse the deliverable as an input to a different authorized project | **PASS** | `/projects/{alpha_id}?tab=deliverables` → `/deliverables/{id}/reuse` → `/projects/{beta_id}?tab=tasks` | **UI** | Opened "Reuse ▾" dropdown in Deliverables table, selected Project Beta and "expand" action, submitted form. Authorized cross-project grant created, redirected to Project Beta Tasks tab showing new task with "Input Artifact Linked". | [`07_reuse_cross_project.png`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/reports/screenshots/07_reuse_cross_project.png) |
| **8** | Verify unauthorized user or project cannot access or reuse it | **PASS** | `/projects/{id}` (unauthenticated)<br>`/api/v1/artifacts/{id}/download?project_id={gamma}` | **UI & API** | Unauthenticated browser access redirected to `/auth/login`. Scoped download request for unauthorized project (Project Gamma) returned `HTTP 403 Forbidden`. | [`08_unauthorized_access_denied.png`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/reports/screenshots/08_unauthorized_access_denied.png) |

---

## 6. Screenshots & Trace Evidence

- **Trace Archive:** [`docs/remediation/reports/stage06_browser_trace.zip`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/reports/stage06_browser_trace.zip) (Playwright tracing recorded all page events, network requests, DOM snapshots, and console outputs).
- **Screenshots Directory:** [`docs/remediation/reports/screenshots/`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/reports/screenshots/)
  - `01_open_project.png`
  - `02_locate_deliverables_project_tab.png`
  - `02_locate_deliverables_hub.png`
  - `03_preview_deliverable_ui.png`
  - `04_download_deliverable.png`
  - `05_provenance_and_metadata.png`
  - `06_reuse_same_project.png`
  - `07_reuse_cross_project.png`
  - `08_unauthorized_access_denied.png`

---

## 7. Remaining Defects

**Zero remaining defects identified in Stage 6 deliverables UI or backend lifecycle.**
Both reported UI issues have been resolved, verified with regression tests, and confirmed passing in the end-to-end browser acceptance run.

---

## 8. Git Working-Tree Status & Data Confirmation

- **Active Branch:** `feature/unified-artifact-deliverables`
- **Tracked files modified for this repair:**
  - `app/routes/deliverables.py`
  - `app/templates/projects/detail.html`
  - `tests/test_stage06_unified_artifacts.py`
  - `test_stage06_workflow_clean.py`
  - `docs/remediation/reports/STAGE_06_BROWSER_ACCEPTANCE.md`
- **Live User Data Confirmation:**
  - No live user databases or persistent stores were modified.
  - Live database files (`data/collective.db`, `data/qwen_collective.db`) remain unmodified (0 bytes, untouched).
  - All tests and browser runs operated exclusively inside disposable temporary test directories (`data/tmp_stage06_browser_acceptance`), which were purged following execution.

---

## 9. Narrowly Scoped Authorization Verification

A dedicated security verification test was performed using a disposable isolated fixture to confirm that the server strictly denies unauthorized cross-project reuse attempts without side effects:
- **Scenario:** Caller `eve` (unauthorized user, not owner and not administrator) submitted a cross-project reuse request targeting Project B using an artifact originating in Project A.
- **Server Response:** Request denied with `HTTP 303 Redirect` carrying `error=Actor 'eve' is not authorized to grant cross-project reuse of artifact...` (Service raised `PermissionError`).
- **Side Effect Audit:**
  - Tasks created in Project B: **0**
  - Access grants created in `artifact_access_grants`: **0**
  - Project links created in `artifact_project_links`: **0**
  - Reuse records created in `artifact_reuses`: **0**
- **Deprecation Warning Recorded:**
  `StarletteDeprecationWarning: Using 'httpx' with 'starlette.testclient' is deprecated; install 'httpx2' instead.`
  *(Tracked for dependency modernization in Stage 8/9).*

---

## 10. Governance Status & Acceptance Record

- **STAGE 6 — FUNCTIONALLY ACCEPTED, RELEASE VALIDATION PENDING**
- **Acceptance Basis:** All 8 user workflows passing 100% in browser UI automation with cryptographic and authorization integrity confirmed.
- **Scope Limit:** Functional acceptance only. Disaster recovery (Stage 8) and production release gates (Stage 9) are **NOT** certified and remain on formal HOLD.
- **Next Stage:** Awaiting explicit human approval before Stage 8 authorization.

