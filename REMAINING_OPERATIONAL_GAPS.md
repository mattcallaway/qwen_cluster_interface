# Remaining Operational Gaps & Risk Analysis
**Antigravity — Qwen Collective**  
**Date:** October 14, 2026  
**Status:** AUDITED FOLLOWING PHASE ONE REPAIRS

---

## 1. Overview
The targeted security and reliability repairs resolved the three critical vulnerabilities identified in the audit:
- Class B Python execution isolation and tamper-detection via SHA256 checksums.
- Offline buffer acknowledgment semantics, conflict non-overwriting, and quarantined payload isolation.
- Controller-controlled metric verification preventing worker self-verification.

This document identifies remaining operational gaps and long-term architectural recommendations.

---

## 2. Identified Operational Gaps

### GAP-01: Bare-Host Subprocess User Identity (Least-Privilege UID)
- **Current State:** On bare-metal / virtual Linux hosts without container isolation, Class B Python scripts execute as subprocesses under the user account of the running `vps_agent` process. Although `PYTHONPATH` is scrubbed, file permissions are set to `0700`, and environment secrets are purged, the subprocess shares the OS user identity.
- **Risk:** If an approved script contains an unintended vulnerability or attempts to read files owned by the same user, the OS will allow access.
- **Remediation Recommendation:** Configure the Linux systemd service for `vps_agent` to launch execution subprocesses under a dedicated unprivileged user (e.g., `nobody` or `qwen-worker`) using `setuid` or `runuser -u qwen-worker`.

### GAP-02: Class C Container Isolation Runtime Provisioning
- **Current State:** Experimental/unreviewed AI-generated code (`task_type = 'experimental_code'`) requires container isolation (`_check_container_isolation_available()`). On systems without Docker or Podman, Class C tasks fail closed with `isolation_unavailable`.
- **Risk:** Low security risk (fails closed safely), but operational limitation: experimental workloads cannot execute until a lightweight container engine (e.g. rootless Podman) is provisioned on the worker.
- **Remediation Recommendation:** Install and configure rootless Podman on `hvm-chicago` with restricted capability sets (`--cap-drop=ALL --net=none`) specifically for Class C tasks. Do not expose the Docker socket to the agent.

### GAP-03: Quarantined Submissions Monitoring & Alerting
- **Current State:** Malformed or schema-invalid submissions (HTTP 400/422) are quarantined in the local `pending_submissions` table (`status = 'quarantined'`) with an error reason so they do not block the active FIFO queue.
- **Risk:** If a worker repeatedly encounters schema mismatches, quarantined items accumulate in SQLite without proactive operator alerting unless queried via `get_quarantined_submissions()`.
- **Remediation Recommendation:** Add a `quarantined_submissions_count` field to the worker health heartbeat payload so the controller's infrastructure dashboard displays a warning banner whenever a worker node has quarantined submissions.

### GAP-04: Offline SQLite Database Maintenance & Log Pruning
- **Current State:** Successfully delivered items are removed from the database via `DELETE FROM pending_submissions WHERE id = ?`. Over long operating cycles, SQLite does not automatically shrink its file size unless `VACUUM` is executed.
- **Risk:** Minor disk space retention over months of continuous operation.
- **Remediation Recommendation:** Add an automatic `PRAGMA auto_vacuum = INCREMENTAL;` or monthly maintenance hook in `vps_agent/buffer.py`.

---

## 3. Summary Assessment
None of these remaining gaps compromise the core security invariants or prevent production acceptance testing. The system fails closed safely in all untested or unprivileged paths.
