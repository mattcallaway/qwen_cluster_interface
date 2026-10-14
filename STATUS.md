# Qwen Collective: Implementation & Deployment Status (STATUS.md)

**Date**: October 14, 2026  
**Host Machine**: Microsoft Windows 10 Home (Build 19045, 64-bit, 24 GB RAM)  
**Status**: Comprehensive Security, Reliability & Quality Remediation Complete  
**Automated Tests**: 85 passed (100% test pass rate in `.venv`)  
**Remediation Branch**: `fix/security-reliability-audit`  

---

## 1. Windows Host Environment & Readiness

| Component | Status | Details |
| :--- | :--- | :--- |
| **Operating System** | Warning | Windows 10 Home (10.0.19045), x64, 24 GB RAM (See OS Support Risk below) |
| **Python Environment** | Ready | Python 3.12.10; dedicated virtual environment created at `.venv/` |
| **Control Server Port** | Ready | Bound strictly to `127.0.0.1:8000` (port verified unoccupied) |
| **Persistent Data Directory** | Ready | Stored outside source tree at `%USERPROFILE%\.qwen-collective\data` |
| **OpenSSH Tools** | Ready | Windows OpenSSH 9.5.5.2 installed; `%USERPROFILE%\.ssh` secured via `icacls` |
| **WinSW Service Wrapper** | Ready | WinSW v2.12.0 staged; literal secrets removed from tracked XML |
| **Tailscale Serve** | Active | Authenticated; Unattended mode active; Private HTTPS reverse-proxy active via Tailscale Serve (`--bg 8000`) |
| **Background Server Daemon** | Active | Running on `http://127.0.0.1:8000` with rotated credentials |

---

## 2. Deliverables & Documentation

1. **[`SECURITY_AUDIT.md`](SECURITY_AUDIT.md)**: Comprehensive vulnerability audit, root causes, remediations, and remaining risks.
2. **[`CLAIM_RECONCILIATION.md`](CLAIM_RECONCILIATION.md)**: Findings matrix reconciling past Antigravity assertions against actual code.
3. **[`REMEDIATION_CHANGELOG.md`](REMEDIATION_CHANGELOG.md)**: Complete file-by-file log of all code, schema, and configuration updates.
4. **[`DEPLOYMENT_READINESS.md`](DEPLOYMENT_READINESS.md)**: 4-tier deployment classification across localhost, Tailscale, VPS, and consequential actions.
5. **[`AUDIT_BASELINE.md`](AUDIT_BASELINE.md)**: Pre-remediation Git state, commit hashes, and test baseline.
6. **[`requirements-lock.txt`](requirements-lock.txt)**: Reproducible production dependency lockfile with 40 pinned packages.
7. **[`WINDOWS_DEPLOYMENT.md`](WINDOWS_DEPLOYMENT.md)**: Service management, log rotation, OpenSSH hardening, and backups.
8. **[`service/qwen-collective-service.xml`](service/qwen-collective-service.xml)**: Automatic boot startup, crash restarts, rotating logs, and external config loading.

---

## 3. Verified Security & Reliability Controls Matrix

| Verification Item | Scope | Status | Verified Implementation |
| :--- | :--- | :--- | :--- |
| **VPS Worker Token Enforcement** | VPS Companion | **VERIFIED** | Enforces minimum 32-character token; fails startup on weak/default tokens (`test_vps_security.py`). |
| **VPS Constant-Time Compare** | VPS Companion | **VERIFIED** | Constant-time authentication via `secrets.compare_digest()` to eliminate timing attacks. |
| **VPS Network Binding Policy** | VPS Companion | **VERIFIED** | Defaults strictly to `127.0.0.1`; public `0.0.0.0` binding requires explicit `QC_ALLOW_PUBLIC_BIND=1`. |
| **VPS Capability Reflection** | VPS Companion | **VERIFIED** | Advertised capabilities strictly match implemented handlers (`echo_ping`, `network_probe`). Phantom tasks purged. |
| **IPv4-Mapped IPv6 Unmapping** | Network Policy | **VERIFIED** | `normalize_ip()` extracts and unmaps `::ffff:...` literals before destination inspection (`test_ssrf_advanced.py`). |
| **Cloud Metadata & Link-Local CIDR** | Network Policy | **VERIFIED** | Blocks `169.254.0.0/16`, `fe80::/10`, and cloud provider metadata hostnames. |
| **URL User-Info Rejection** | Network Policy | **VERIFIED** | Blocks embedded credentials (`http://user:pass@host/`) and invalid characters. |
| **Redirect Revalidation** | Network Policy | **VERIFIED** | `safe_execute_http()` enforces `follow_redirects=False` and revalidates every hop before following. |
| **Global Destination Port Restriction** | Network Policy | **VERIFIED** | Public/global traffic restricted to authorized service ports (`80, 443, 8000, 8080, 8081, 11434`). |
| **Dual-Keyed Rate Limiting** | Authentication | **VERIFIED** | Limits both client IP and account identity (`username`) to defeat rotating-IP brute-force attacks. |
| **Persistent Rate Limiting** | Authentication | **VERIFIED** | Stored in SQLite `login_attempts` table so limits survive server restarts (`test_auth_ratelimit.py`). |
| **Bounded Throttle Storage** | Authentication | **VERIFIED** | Auto-pruning on 300s window; table strictly capped at 1,000 records to prevent storage DoS. |
| **Proxy Header Anti-Spoofing** | Authentication | **VERIFIED** | `X-Forwarded-For` only honored from `TRUSTED_PROXIES` (`127.0.0.1`, `::1`). |
| **Chat Conversation Isolation** | Core UI / Chat | **VERIFIED** | Multi-conversation support (`conv-<uuid>`); cross-user access rejected with HTTP 403 (`test_chat_isolation.py`). |
| **Sliding Context Budgeting** | Core UI / Chat | **VERIFIED** | `build_context_messages()` selects newest turns within character budget in strictly chronological order. |
| **Production Reload Disabled** | Core Server | **VERIFIED** | `should_reload = False` by default in `app/main.py`; requires explicit `QC_DEV_RELOAD=1` in dev mode. |
| **Realistic Distributed Model Guidance** | Documentation | **VERIFIED** | Documentation corrected to prioritize 0.5B-3B quantized models on 4GB laptops; RPC public exposure warned. |

---

## 4. Platform Advisory: Windows 10 Operating System Support Risk

> [!WARNING]
> **Windows 10 End-of-Life Advisory**: Microsoft ended standard support for Windows 10 in **October 2025**. Running an always-on network control server on an unsupported Windows release poses security risks regarding kernel-level vulnerabilities and unpatched SMB/network stack exploits unless enrolled in Extended Security Updates (ESU).
>
> While Qwen Collective operates cleanly on Windows 10 (Build 19045), we recommend planning a migration to **Windows 11 Pro/Enterprise** or a dedicated headless **Debian 12 / Ubuntu 24.04 LTS** node. All application data in `%USERPROFILE%\.qwen-collective\data` is portable and can be moved without data loss.
