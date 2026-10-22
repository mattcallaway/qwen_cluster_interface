# Remediation Open Issues & Deferred Work

This document tracks known blockers, preexisting failures, hardware limitations, and deferred engineering items.

---

## Active Issues & Tracking

### 1. Preexisting Unit Test Failure: `test_extract_json_object_handles_prose_and_fences`
- **Component:** `app/services/orchestration/planner.py`
- **Impact:** Unit test fails when testing generic JSON payload extraction.
- **Details:** `extract_json_object` filters candidate JSON objects by checking for the presence of planning keys (`overview`, `objective`, `tasks`, etc.) to prevent false matches from conversational prose. The unit test in `test_orchestrator_units.py` passes `{"plan": True}`, which lacks these keys and gets rejected.
- **Planned Resolution:** In Stage 3, introduce an optional `required_keys=None` parameter in `extract_json_object`. When called from general extraction, require valid JSON; when called from planning, supply planning keys.
- **Status:** OPEN (Scheduled for Stage 3).

---

### 2. Preexisting Web Route Test Failure: `test_knowledge_page`
- **Component:** `app/templates/knowledge/index.html` & `tests/test_web_routes.py`
- **Impact:** Test fails on assertion `assert "Persistent Knowledge Repository" in response.text`.
- **Details:** The HTML template header was modified in a recent commit to `"Collective Knowledge & Evidence Repository"`, causing the old string assertion to fail.
- **Planned Resolution:** In Stage 8 (Documentation & Alignment), update the test assertion to match the authoritative template header or reconcile UI copy.
- **Status:** OPEN (Scheduled for Stage 8).

---

### 3. Missing Dependency: `trafilatura` Text Extractor
- **Component:** `app/services/research/retrieval.py`
- **Impact:** Web crawler falls back to BeautifulSoup text extraction, which is less adept at stripping boilerplates, navigation bars, and cookie notices.
- **Details:** `trafilatura` is listed as an optional dependency but is not installed in the current Python environment.
- **Planned Resolution:** Ensure the BeautifulSoup fallback is robust, preserves core article text, and does not crash if `trafilatura` remains absent.
- **Status:** OPEN (Mitigated via fallback).

---

### 4. Fleet & Remote Worker Live Probing Unavailable Offline
- **Component:** `app/services/worker.py` & Remote Worker Fleet
- **Impact:** Live integration testing against remote Linux nodes or GPU backends cannot be executed offline on the local controller.
- **Details:** To avoid touching live production services or disrupting remote workers without explicit human approval, all worker interactions during Stages 1-7 must rely on local test mocks and ephemeral loopback fixtures.
- **Planned Resolution:** Comprehensive mock fixtures simulating node leases, heartbeats, and task execution will be used in test suites. Live fleet testing will occur only in Stage 9 under human supervision.
- **Status:** DEFERRED TO STAGE 9.

---

### 5. Playwright Browser Egress Sandboxing & Staging Verification Gate (F-14)
- **Component:** `app/services/research/browser.py` & `app/services/research/browser_proxy.py`
- **Impact:** While `OutboundFilteringProxy` with socket-level IP pinning, CONNECT tunneling, and fail-closed 503 fallback is implemented and verified with 7 adversarial tests, actual Chromium/Playwright network isolation has not been validated against live production container environments.
- **Mandatory Production-Staging Gate:** F-14 is preserved as `PARTIALLY_FIXED`. In any production or deployment environment where the secure proxy enforcement cannot be established (or where Chromium flags are ignored or loopback is blocked), browser research MUST fail closed (returning structured HTTP 503). Final certification to `FIXED` requires successful end-to-end containerized execution in Stage 9.
- **Status:** PARTIALLY_FIXED (Mandatory Staging Gate Active).

---

### 6. Upload Request-Body Limit Enforced Before Full Payload Allocation
- **Component:** `app/routes/worker.py` & Worker Ingestion Pipeline
- **Impact:** Currently, the 25MB artifact cap in `store_task_artifact` evaluates bytes in memory after FastAPI/Starlette has parsed the JSON request body. An oversized upload (e.g. 100MB) can cause transient heap memory spikes on memory-constrained controller nodes before being rejected.
- **Planned Resolution:** In Stage 8 (Operations & API Hardening), implement an ASGI streaming middleware or Content-Length check on `/api/v1/worker/tasks/{id}/artifacts` that aborts with HTTP 413 Payload Too Large at the transport layer before full JSON and base64 memory buffers are allocated. Add follow-up test validating streaming abort.
- **Status:** OPEN (Scheduled for Stage 8).
