# Qwen Collective — VPS Operations Runbook

## 1. Operating Environment Overview

* **VPS Target Host:** Remote Linux Host (Ubuntu/Debian).
* **Network Mesh:** Tailscale interface `100.x.y.z`.
* **Controller:** Windows Service `QwenCollective` on `127.0.0.1:8000`.
* **Local Test Worker Port:** `8081` (isolated companion agent).

---

## 2. Operator Procedures

### 2.1 Verifying Infrastructure Health
From the controller web UI:
1. Navigate to `/infrastructure`.
2. Inspect the **Overview** stat widgets: Total Nodes, Online Workers, Verified Capabilities.
3. Review any active warnings in the **Reconciliation & Drift** panel.

### 2.2 Triggering Capability Re-Verification
1. Open `/infrastructure?tab=capabilities`.
2. Locate the target capability (e.g. `cap_vps_python_exec`).
3. Click **"Verify Capability"** or run the automated reconciliation probe.
4. Verify that status changes to `VERIFIED` and the audit log records the test details.

### 2.3 Inspecting Architecture Context
1. Open `/infrastructure?tab=architecture`.
2. Select desired token budget (e.g. 1000, 2000, 4000).
3. Confirm the live Architecture Context Packet correctly identifies verified vs unverified capabilities and renders omission notices as expected.

---

## 3. Troubleshooting & Failure Recovery

| Symptom | Diagnostic Step | Remediation |
| :--- | :--- | :--- |
| **Node shows OFFLINE** | Check Tailscale connectivity (`tailscale status`) | Ping node over Tailscale; restart worker agent service |
| **Capability fails verification** | Inspect `verification_error` in Capability Registry | Ensure required software (e.g. Python, systemd) is installed; check permissions |
| **Architecture Drift Alert** | Check Reconciliation tab | Review discrepancy; if change is approved, update registered capability; otherwise investigate host |
| **Worker Lease Expiration** | Check task log for timeout | Adjust execution timeout limit or inspect long-running worker process |

---

## 4. Status

* **Status:** Operational guide maintained throughout Milestones 1–6.
