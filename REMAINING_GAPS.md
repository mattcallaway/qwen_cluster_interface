# Remaining Gaps & Operational Readiness Report

**Date:** October 14, 2026  
**Auditor & Implementation Agent:** Antigravity AI Pair Programmer  
**Branch:** `fix/integration-security-operational-readiness`  
**Test Suite Verification:** 262 passed, 0 failures, 1 warning (42.05s)

---

## 1. Executive Summary

This report provides an honest, evidence-based assessment of what has been verified, what remains mocked or isolated, and the explicit operational prerequisites required before deploying these changes to the live production Windows service and remote Linux VPS.

In accordance with strict operational safety controls:
- **No live production database was modified**.
- **The running Windows service (port 8000) was not stopped or restarted**.
- **No unapproved software was installed or configured on the production VPS**.

---

## 2. Verification Classification by Area

| Capability Area | Development State | Verification Classification | Production Deployment Status |
|---|---|---|---|
| **Worker Enrollment Tokens (SEC-01)** | Complete | Integration tested & isolated worker tested | Ready for migration approval |
| **Worker Scope & Ownership (SEC-03)** | Complete | Integration tested & negative tested | Ready for migration approval |
| **Class A & Class B Sandboxing (SEC-02)** | Complete | Tested against isolated worker | Ready for agent update |
| **Class C Container Sandboxing (SEC-02)** | Fails closed by design | Tested against isolated worker | Requires Docker/Podman on VPS host |
| **Task Dependency Graph (INT-02)** | Complete | Unit & Integration tested | Ready for merge |
| **Chat Project & Architecture Context (INT-01)** | Complete | Integration tested & mocked inference tested | Ready for merge |
| **Context Preview Diagnostic (DIAG-01)** | Complete | Integration tested & UI reviewed | Ready for merge |
| **Non-blocking Lease Heartbeat (REL-01)** | Complete | Tested against isolated worker | Ready for agent update |
| **Offline Buffering & Conflict Reconciliation** | Complete | Integration tested & offline simulated | Ready for merge |
| **Full Execution-to-Knowledge Loop (INT-03)** | Complete | Full 12-step acceptance tested | Ready for merge |

---

## 3. Detailed Gaps & Prerequisites

### A. Distinguishing Mocked Testing from Production Hardware Verification
- **Current Verification**:
  - The entire test suite (262 tests) ran against an in-process FastAPI application using SQLite databases and an isolated in-process `VPSWorkerAgent` simulating HTTP communication via Starlette TestClient.
  - Subprocess execution tests ran on the local Windows development machine using temporary directories and scrubbed environments.
- **Production Hardware Verification**:
  - Real execution over the Tailscale network mesh (`100.x.x.x`) to the Debian/Ubuntu Linux VPS has **not yet been triggered** with the new enrollment token mechanism, because doing so requires an explicit administrative deployment step.
  - Until the remote VPS agent is updated with the new `vps_agent` code and supplied with a valid enrollment token, the live deployment cannot be claimed as "hardware verified".

### B. Class C (Experimental / AI-Generated Code) Sandboxing
- **Current Behavior**:
  `vps_agent/executor.py` explicitly **fails closed** with `error_category: "isolation_unavailable"` when a task requests Class C (`experimental_execution` or `docker_execution`) if Docker is not available.
- **Prerequisite for Production Enablement**:
  If the operator wishes to run unreviewed AI-generated code on the VPS, Docker or Podman must be installed on the Linux VPS with an unprivileged runtime user (rootless container runtime). Process timeouts and Python-level filters alone must not be used as an OS-level sandbox.

### C. Live Production Worker Re-Enrollment
- **Security Requirement**:
  With Migration 8 applied, the controller rejects unauthenticated worker registration (`POST /api/v1/worker/register` returns 401 without an enrollment token or existing valid Bearer token).
- **Migration Procedure for Live VPS Worker**:
  To prevent worker disruption during deployment:
  1. The administrator will generate an enrollment token for the VPS node using the CLI or API:
     ```bash
     python -c "from app.services.worker import create_enrollment_token; print(create_enrollment_token('vps-production-worker-01', created_by='admin'))"
     ```
  2. Update `/etc/qwen-collective/agent.env` on the VPS to set:
     ```env
     QC_ENROLLMENT_TOKEN="qcwe_..."
     ```
  3. Restart the VPS agent service (`systemctl restart qwen-worker.service`).
  4. The agent will enroll once, obtain a persistent token, and save it locally in `agent_token.json`.

---

## 4. Rollback Plan

If any unexpected regression occurs during production cutover:

1. **Database Schema Rollback (Migration 8)**:
   - Migration 8 adds `worker_enrollment_tokens` table and `chat_conversations.project_id`.
   - To roll back schema:
     ```sql
     DROP TABLE IF EXISTS worker_enrollment_tokens;
     -- SQLite 3.35+ supports ALTER TABLE DROP COLUMN
     ALTER TABLE chat_conversations DROP COLUMN project_id;
     UPDATE schema_migrations SET applied_at = NULL WHERE version = 8;
     ```
2. **Git Rollback**:
   - Revert branch `fix/integration-security-operational-readiness` to the baseline commit `3ce27be`.
3. **VPS Agent Rollback**:
   - Restore previous `vps_agent` directory from backup.
   - Restart `qwen-worker.service`.

---

## 5. Deployment Authorization Checklist

Before deploying this branch to production:
- [x] All 262 automated tests passing without regressions.
- [x] Migration 8 tested for idempotency and schema compatibility.
- [x] Sandboxed subprocess execution verified to scrub secrets and restrict working directory.
- [x] Chat context packet builder verified against multi-task dependency graphs.
- [x] Context diagnostic inspector modal verified in web interface.
- [ ] Administrator approval for applying Migration 8 to production SQLite database.
- [ ] Administrator approval for updating `vps_agent` code and restarting the remote VPS service.
