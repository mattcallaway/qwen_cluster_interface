# Remaining Operational Gaps & Risk Analysis
**Antigravity — Qwen Collective**  
**Date:** October 14, 2026  
**Status:** AUDITED FOLLOWING FINAL CORRECTIONS PASS  
**Branch:** `fix/vps-production-acceptance`

---

## 1. Overview & Audit Context

The final corrections pass addressed four critical architectural and security concerns:
1. **OS-Level Class B Execution Boundary:** Enforced transient `systemd-run` isolation with `DynamicUser=yes`, `ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes`, cgroup limits, mode `0500` entrypoint scripts, and HMAC-signed execution manifests.
2. **Metric Evidence Integrity:** Eliminated Shortcuts A and B. Checksums without physical artifact files and deliverable links without evidence now remain `unverified`. Independent controller byte hashing is enforced.
3. **Real Artifact Acceptance Protocol:** Rebuilt the acceptance plan around physical disk file generation (`vps_diagnostic_artifact.json`), base64 transport, and controller verification.
4. **Database Migration Safety:** Verified that Version 7 upgrades cleanly to Version 8 with zero foreign key violations, and verified the backup restoration rollback procedure.

---

## 2. Status of Identified Operational Gaps

### GAP-01: Bare-Host Subprocess User Identity & Isolation Boundary
- **Previous Status:** High Concern. Subprocesses ran under the same UID as the VPS agent daemon with no kernel isolation boundary.
- **Current Status:** **RESOLVED AT OS LEVEL**.
  - `vps_agent/executor.py` automatically detects and uses `systemd-run` transient units with `DynamicUser=yes`.
  - The script runs under a dynamically generated, unprivileged UID/GID with no access to `/home`, `/root`, or worker credentials.
  - Filesystem write access is restricted solely to the disposable `work_dir` (`ReadWritePaths`).
  - Network is locked down via `IPAddressDeny=any` unless explicitly allowed.
  - Subprocess fallback is strictly disabled in production unless `QC_ALLOW_SUBPROCESS_FALLBACK=1` is explicitly set (used only for development/unit test harnesses).

### GAP-02: Class C Container Isolation Runtime Provisioning
- **Previous Status:** Operational Limitation.
- **Current Status:** **MAINTAINED FAIL-CLOSED INVARIANT**.
  - Class C tasks (`experimental_code` or unreviewed generative code) strictly mandate an unprivileged container runtime (`podman` or `docker` with `--cap-drop=ALL` and `--net=none`).
  - If container isolation is not detected on the VPS host, Class C tasks immediately fail closed with `error_category = "isolation_unavailable"`, exit code 126.
  - **Operational Recommendation:** When experimental workloads are ready to be scheduled, install rootless Podman on `hvm-chicago` (`apt-get install -y podman`). Do not expose the Docker socket to the agent.

### GAP-03: Metric Verification Shortcuts (Shortcuts A and B)
- **Previous Status:** High Concern. Format-only checksums or deliverable links without artifacts were marked `verified`.
- **Current Status:** **RESOLVED & ELIMINATED**.
  - If no physical artifact bytes are delivered or found on the controller, observations are classified as `unverified` (Reported state).
  - Artifacts are stored in `data/evidence/task_{task_id}/` and independently hashed by the controller.
  - Observations only reach `verified` when physical bytes match the expected checksum.

### GAP-04: Quarantined Submissions Alerting
- **Current Status:** **LOW RISK / FUNCTIONAL**.
  - Malformed submissions (HTTP 400/422) are quarantined in the local `pending_submissions` table (`status = 'quarantined'`) to preserve the FIFO queue.
  - **Future Enhancement:** Surface `quarantined_submissions_count` in the worker heartbeat payload for UI visibility in the infrastructure dashboard.

---

## 3. Operational Requirements for Production Cutover (Phase P2)

Before executing Phase P2 cutover and Phase P3 acceptance:
1. **Administrator Approval:** Obtain explicit written authorization to restart the Windows service and update the VPS agent.
2. **Database Backup:** Run `VACUUM INTO` backup of `C:\Users\Matt C\.qwen-collective\data\qwen_collective.db`.
3. **VPS Agent Permissions:** Ensure the systemd service running `vps_agent` on `hvm-chicago` has permissions to invoke `systemd-run` (standard for systemd services or user lingering).
4. **Tailscale Connection:** Verify continued Tailscale connectivity between `100.122.228.19` (Windows Controller) and `100.97.174.81` (Linux VPS).
