# Audit Baseline: Source of Truth (AUDIT_BASELINE.md)

**Date**: October 14, 2026  
**Host Machine**: `desktop-44h6qvp` (Windows 10 Home, Build 19045, 64-bit)  
**Remediation Branch**: `fix/security-reliability-audit`  

---

## 1. Git State & Commit Verification

| Parameter | Value |
| :--- | :--- |
| **Local Head Commit** | `330125c46e335cc69c93bb4d3898c6bc3a9504dd` |
| **Local Branch** | `fix/security-reliability-audit` (branched from `main` at `330125c`) |
| **Remote Tracking** | `origin/main` at `330125c46e335cc69c93bb4d3898c6bc3a9504dd` |
| **Working Tree Cleanliness** | Clean (working directory synced with remote origin) |
| **Repository URL** | `https://github.com/mattcallaway/qwen_cluster_interface.git` |

---

## 2. Running Deployment & Environment State

| Component | Status | Evidence |
| :--- | :--- | :--- |
| **Running Service** | Active daemon process (PID 10176) | Bound strictly to `127.0.0.1:8000` via Uvicorn |
| **Windows Service (SCM)** | Installed, `StartMode: Auto`, `State: Stopped` | `PathName: C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective\service\qwen-collective-service.exe` |
| **Remote Tailscale Access** | Active via Tailscale Serve | `desktop-44h6qvp.tailb5d90b.ts.net` proxying to `127.0.0.1:8000` (`(tailnet only)`, Funnel disabled) |
| **Application Version** | `0.1.0` | `app/config.py` |
| **Python Environment** | Python 3.12.10 | Dedicated virtual environment at `.venv/` |
| **Persistent Data Directory** | Active outside source tree | Configured at `%USERPROFILE%\.qwen-collective\data` |
| **External Configuration** | Active outside git | `%USERPROFILE%\.qwen-collective\qwen-collective.env` with restricted NTFS ACLs |

---

## 3. Source vs. Deployment Discrepancies

1. **Running Execution Mode**:
   - The active instance handling requests on `127.0.0.1:8000` is running under the interactive user session via `scripts/start-windows.ps1` (PID 10176) rather than directly under WinSW SCM service (`QwenCollective` is `Stopped` in SCM to prevent port collision during interactive development).
2. **WinSW SCM Service Target**:
   - WinSW is configured for `LocalSystem` pointing directly to `.venv\Scripts\python.exe` and `app.main:app`. Elevation is required to start it.
3. **Untracked Deployment Files**:
   - `TAILSCALE_DIAGNOSTIC_REPORT.md` (ignored by git, preserved locally).
   - `DEPLOYMENT_REPORT.md` (ignored by git, preserved locally).

---

## 4. Test Suite Baseline

- **Total Baseline Tests**: 62 tests
- **Passing**: 62
- **Failing**: 0
- **Execution Time**: ~4.25 seconds
- **Command Executed**: `.\.venv\Scripts\python.exe -m pytest tests\ -q`
