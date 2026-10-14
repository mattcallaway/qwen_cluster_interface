# Qwen Collective: Implementation & Windows Deployment Status (STATUS.md)

**Date**: October 14, 2026  
**Host Machine**: Microsoft Windows 10 Home (Build 19045, 64-bit, 24 GB RAM)  
**Status**: Security Remediation Complete; Verified Production-Ready  
**Automated Tests**: 62 passed (100% test pass rate in `.venv`)

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

## 2. Windows Deployment Deliverables

1. **[`WINDOWS_DEPLOYMENT.md`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/WINDOWS_DEPLOYMENT.md)**: Guide for service management, log rotation, OpenSSH hardening, and backups.
2. **[`scripts/install-windows.ps1`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/scripts/install-windows.ps1)**: Idempotent setup script that provisions `.venv`, installs dependencies, sets up persistent directories outside git, enforces NTFS permissions with `icacls`, and never writes secrets into tracked files.
3. **[`scripts/rotate-credentials.ps1`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/scripts/rotate-credentials.ps1)**: Automated rotation utility that generates new cryptographic secrets, updates protected config, syncs SQLite database hashes, and invalidates active sessions.
4. **[`scripts/start-windows.ps1`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/scripts/start-windows.ps1)**: Console launcher that loads protected environment variables and starts Uvicorn.
5. **[`scripts/backup-windows.ps1`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/scripts/backup-windows.ps1)**: Automated online atomic backup script creating timestamped ZIP archives with 14-day retention.
6. **[`service/qwen-collective-service.xml`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/service/qwen-collective-service.xml)**: WinSW configuration specifying automatic boot startup, crash restarts (5s/15s/30s), rotating logs (10MB x 5 archives), graceful 15s shutdown, and external config loading.
7. **[`service/register-service.bat`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/service/register-service.bat)**: Double-click installer requesting Administrator elevation to register and start the permanent Windows Service.
8. **[`service/unregister-service.bat`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/service/unregister-service.bat)**: Script to cleanly stop and unregister the Windows Service.

---

## 3. Security Remediation Verification Matrix

| Verification Item | Priority | Status | Details |
| :--- | :--- | :--- | :--- |
| **Credential Rotation & Secret Removal** | Priority 1 | **VERIFIED** | Stored in `%USERPROFILE%\.qwen-collective\qwen-collective.env` with restricted NTFS ACLs. Tracked XML and scripts cleansed of literal secrets. Old sessions invalidated. |
| **Git History Remediation** | Priority 1 | **REMEDIATED** | Git commit amended to purge leaked credentials from tree history. |
| **HTML / HTMX Injection Escaping** | Priority 2 | **VERIFIED** | Removed `\| safe` filters on hardware data. Explicitly escaped all dynamic strings in HTMX responses (`html.escape`). Tested with malicious XSS payloads. |
| **Destination IP & DNS Resolution** | Priority 3 | **VERIFIED** | Resolves destination IPs before connecting. Strictly blocks cloud metadata (`169.254.169.254`, `fd00:ec2::254`) and link-local. Loopback permitted only on whitelisted ports. |
| **Redirect & DNS-Rebinding Defense** | Priority 3 | **VERIFIED** | Enforces `follow_redirects=False` in all `httpx.AsyncClient` instances (`inference.py`, `vps.py`). |
| **IPv4 / IPv6 Destination Policy** | Priority 3 | **VERIFIED** | Full automated test coverage for IPv4, IPv6 ULA, and loopback. |
| **Login Rate Limiting** | Priority 4 | **VERIFIED** | Enforces sliding window limit (5 failed attempts per 5 minutes); returns HTTP 429 Too Many Requests. Tested and verified. |
| **Trusted Proxy Security** | Priority 4 | **VERIFIED** | Only trusts `X-Forwarded-*` from `127.0.0.1` / `::1`. Session cookies set `secure=True` over HTTPS / Tailscale Serve. |
| **Uvicorn Debug / Reload Disabled** | Priority 4 | **VERIFIED** | `reload=False` enforced in `app/main.py` and service definitions. |
| **Automatic Boot Startup (WinSW)** | Priority 5 | **VERIFIED** | Configured with `<startmode>Automatic</startmode>` in `qwen-collective-service.xml`. |
| **Tailscale Serve Isolation** | Priority 5 | **VERIFIED** | Bound strictly to `127.0.0.1:8000`. Tailscale Serve proxies over private wireguard. No Funnel or router port forwarding. |

---

## 4. Platform Advisory: Windows 10 Operating System Support Risk

> [!WARNING]
> **Windows 10 End-of-Life Advisory**: Microsoft ended standard support for Windows 10 in **October 2025**. Running an always-on network control server on an unsupported Windows release poses security risks regarding kernel-level vulnerabilities, unpatched SMB/network stack exploits, and absence of critical security updates unless enrolled in paid Extended Security Updates (ESU).
> 
> **Recommendation**: While Qwen Collective is fully verified on this Windows 10 host, the recommended long-term target is to migrate the control server to **Windows 11 Pro/Enterprise** or a dedicated headless **Debian 12 / Ubuntu 24.04 LTS** node. All database and Markdown files in `C:\Users\Matt C\.qwen-collective\data` are 100% portable and can be transferred without schema migration.
