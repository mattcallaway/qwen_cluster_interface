# Qwen Collective — Windows Control Server Deployment Guide

This guide documents the configuration, operation, and reliability hardening of **Qwen Collective** as an always-on Windows Control Server.

---

## 1. System Inventory & Environment

- **Operating System**: Microsoft Windows 10 Home (Build 19045, 64-bit)
- **Host Hardware**:
  - Memory: 25.76 GB Physical RAM (~24 GB usable)
  - Storage: Drive C: with ~331 GB free
  - Active IPv4 Interface: `192.168.4.69` (Ethernet)
- **Runtime Environment**:
  - Installed Python: Python 3.12.10
  - Dedicated Virtual Environment: `C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective\.venv`
  - Control Port: `127.0.0.1:8000` (Localhost only, verified free)
  - Persistent Data Directory: `C:\Users\Matt C\.qwen-collective\data` (outside source tree)
- **OpenSSH Tools**: Windows OpenSSH Client 9.5.5.2 installed (`C:\Windows\System32\OpenSSH\ssh.exe`)

---

## 2. Architecture & File Layout

```text
C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective\  <-- Application Source Tree
├── .venv\                                 <-- Dedicated Python 3.12 virtual environment
├── app\                                   <-- FastAPI application & route modules
├── scripts\
│   ├── install-windows.ps1               <-- Idempotent installer & secret generator
│   ├── start-windows.ps1                 <-- Console launcher with environment loader
│   └── backup-windows.ps1                <-- Atomic SQLite & knowledge vault backup
├── service\
│   ├── qwen-collective-service.exe       <-- WinSW v2.12.0 x64 wrapper binary
│   ├── qwen-collective-service.xml       <-- Service definition (crash restart, logs, env)
│   └── logs\                             <-- Rotating log output (roll-by-size, 10MB x 5)
└── tests\                                <-- 55 automated verification tests

C:\Users\Matt C\.qwen-collective\         <-- Persistent Data (SURVIVES CODE UPDATES)
├── qwen-collective.env                   <-- Production cryptographic secrets & credentials
├── data\
│   ├── qwen_collective.db                <-- SQLite WAL database with foreign keys & FTS5
│   └── knowledge\                        <-- Obsidian-compatible Markdown vault
│       ├── system\
│       ├── projects\
│       ├── research\
│       ├── sources\
│       ├── decisions\
│       ├── journal\
│       └── inbox\
└── backups\                              <-- Timestamped ZIP backups of DB & vault
```

---

## 3. Quickstart & Installation

### Step 1: Run the Automated Windows Setup Script

From an elevated or standard PowerShell prompt:
```powershell
cd "C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective"
powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
```

The script is **fully idempotent** and will:
1. Validate Python 3.11+.
2. Provision the dedicated `.venv` and install all dependencies.
3. Create the persistent data directory at `C:\Users\Matt C\.qwen-collective\data`.
4. Generate cryptographically random production secrets (`QC_SECRET_KEY` and `QC_ADMIN_PASSWORD`).
5. Restrict NTFS permissions on `%USERPROFILE%\.ssh` using `icacls`.
6. Download and configure the WinSW service wrapper.
7. Execute all 55 automated verification tests.

### Step 2: Interactive Console Execution (Development / Testing)

To run the application directly in a terminal:
```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-windows.ps1
```
Open **`http://127.0.0.1:8000`** in your browser. Sign in using `admin` and the generated password from `C:\Users\Matt C\.qwen-collective\qwen-collective.env`.

---

## 4. Running as a Persistent Windows Service (WinSW)

Qwen Collective uses **Windows Service Wrapper (WinSW v2.12.0)** to provide:
- **Automatic Startup**: Launches when Windows boots without requiring an active user login.
- **Automatic Failure Recovery**: Automatically restarts the server 5s, 15s, and 30s after a crash.
- **Log Rotation**: Logs are written to `service\logs\`, rolled at 10 MB, retaining 5 archive files.
- **Graceful Shutdown**: Sends a SIGINT/Ctrl+Break with a 15-second timeout for clean SQLite closure.

### Managing the Service

To install and run the service with Windows Administrator elevation, you can simply:
1. Double-click **[`service\register-service.bat`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/service/register-service.bat)** (which prompts for UAC elevation automatically).
2. Or run from an Administrator PowerShell prompt:

```powershell
cd "C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective\service"

# 1. Install the Windows Service
.\qwen-collective-service.exe install

# 2. Start the Service
.\qwen-collective-service.exe start

# 3. Check Service Status
.\qwen-collective-service.exe status

# 4. Stop or Restart
.\qwen-collective-service.exe stop
.\qwen-collective-service.exe restart

# 5. Uninstall the Service (preserves data directory)
.\qwen-collective-service.exe uninstall
```
*(To uninstall via batch script, run **[`service\unregister-service.bat`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/service/unregister-service.bat)**).*

### Service Account & Permissions

To restrict the service identity, open `service\qwen-collective-service.xml` and specify an unprivileged local account:
```xml
<serviceaccount>
  <domain>.</domain>
  <user>qwen-service</user>
  <password>YourStrongPassword</password>
</serviceaccount>
```
Ensure the account has NTFS Read/Write permissions on `C:\Users\Matt C\.qwen-collective` and Read permissions on the project directory.

---

## 5. SSH Configuration & Hardware Discovery on Windows

Hardware probing over SSH connects to older Linux laptops using read-only diagnostic commands (`uname -srm`, `lscpu`, `free -m`, `lsblk`, `ip -brief addr`).

To maintain strict security without disabling host-key verification:

### 1. Known Hosts Population
Strict `RejectPolicy` is enforced. Pre-seed compute nodes into `%USERPROFILE%\.ssh\known_hosts`:
```powershell
ssh-keyscan -H 192.168.4.101 >> "$env:USERPROFILE\.ssh\known_hosts"
ssh-keyscan -H 192.168.4.102 >> "$env:USERPROFILE\.ssh\known_hosts"
```

### 2. SSH Key Permissions
Windows OpenSSH requires restrictive permissions on private keys. The installer automatically runs:
```powershell
icacls "$env:USERPROFILE\.ssh" /inheritance:r /grant:r "$($env:USERNAME):(OI)(CI)F" "SYSTEM:(OI)(CI)F"
```

---

## 6. Remote Access via Tailscale Serve (No Public Exposure)

The control server binds strictly to `127.0.0.1:8000`. Public port exposure and router port-forwarding are prohibited.

### Current Status: Tailscale 1.104.1 Installed & Running
Tailscale has been installed on this machine (`C:\Program Files\Tailscale\tailscale.exe`) and the Windows Tailscale service is running.

### Enabling Authenticated Private Access:

1. **Authenticate into your Tailscale network (tailnet)**:
   - Launch Tailscale from the Windows Start menu or run:
     ```powershell
     & "C:\Program Files\Tailscale\tailscale.exe" up
     ```
   - Complete the browser login to connect this machine to your tailnet.

2. **Enable Tailscale Serve**:
   ```powershell
   & "C:\Program Files\Tailscale\tailscale.exe" serve --bg 8000
   ```
3. Access the web interface securely over HTTPS on any authorized device in your tailnet:
   `https://<your-machine-name>.<tailnet-name>.ts.net`

*Security Note:* Do **NOT** enable `tailscale funnel`, which would publish the control interface to the public internet. Tailscale Serve keeps traffic strictly within your authenticated private tailnet.

---

## 7. Atomic Backups & Disaster Recovery

Run the backup script manually or via Task Scheduler:
```powershell
powershell -ExecutionPolicy Bypass -File scripts\backup-windows.ps1
```

The script:
1. Performs an online atomic SQLite snapshot (`sqlite3.backup()`).
2. Archives the Markdown knowledge repository (`data\knowledge`).
3. Produces a timestamped ZIP archive in `C:\Users\Matt C\.qwen-collective\backups\`.
4. Automatically prunes backups older than 14 days.

### Automating Daily Backups in Windows Task Scheduler

Run in an Administrator PowerShell prompt:
```powershell
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File `\"C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective\scripts\backup-windows.ps1`\""
$Trigger = New-ScheduledTaskTrigger -Daily -At 3:00AM
Register-ScheduledTask -TaskName "QwenCollective-DailyBackup" -Action $Action -Trigger $Trigger -Description "Daily atomic backup of Qwen Collective database and Markdown vault"
```

---

---

## 8. Cryptographic Credential Rotation

If credentials need to be changed or if an accidental exposure occurs:

1. Run the automated rotation script:
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\rotate-credentials.ps1
   ```
2. The script:
   * Generates a new 256-bit `QC_SECRET_KEY` and random `QC_ADMIN_PASSWORD`.
   * Updates `%USERPROFILE%\.qwen-collective\qwen-collective.env`.
   * Enforces restrictive NTFS ACLs (`icacls` granting only current user and SYSTEM).
   * Updates `qwen_collective.db` with the new hashed admin credentials.
   * Restarts the background service, instantly invalidating all active sessions.

---

## 9. Safe Git History Remediation Procedure

If secret values were previously committed to Git:

1. **Rotate Credentials Immediately**: Follow Section 8 to invalidate old tokens. Old credentials must be considered dead.
2. **Amend Local Commit (Single Commit History)**:
   ```powershell
   git add .
   git commit --amend -m "Clean commit without secrets"
   git push --force origin main
   ```
3. **Multi-Commit History Cleansing (Using git-filter-repo)**:
   ```bash
   pip install git-filter-repo
   git filter-repo --replace-text <(echo "COMPROMISED_SECRET_KEY==>[REDACTED]")
   git push --force --all origin
   ```
4. **Invalidate External Sessions**: All web session cookies expire immediately because HMAC verification fails with the rotated secret.

---

## 10. Private HTTPS Remote Access with Tailscale Serve

To access the Qwen Collective control interface securely from other devices (e.g., phones, tablets, laptops) on your private network without exposing the machine to the public internet:

### Architecture
- The application binds strictly to loopback: `127.0.0.1:8000`.
- Tailscale runs on the host and uses **Tailscale Serve** to proxy incoming requests within the private tailnet.
- Tailscale handles TLS termination automatically using Let's Encrypt certificates provisioned for your machine's MagicDNS name (`https://<node-name>.<tailnet>.ts.net/`).
- **Tailscale Funnel is kept strictly DISABLED** to prevent public internet access.

### Configuration Steps
1. **Enable Unattended Mode**:
   Allows Tailscale to remain connected in the background even when no user is logged in to Windows:
   ```powershell
   & "C:\Program Files\Tailscale\tailscale.exe" set --unattended
   ```
2. **Authorize Tailscale Serve**:
   If your tailnet requires node approval, follow the authorization URL displayed upon first setup or enable Serve capability in your Tailscale admin console ACLs.
3. **Start Background Serve Proxy**:
   Proxy incoming HTTPS requests to the local Uvicorn instance on port 8000:
   ```powershell
   & "C:\Program Files\Tailscale\tailscale.exe" serve --bg 8000
   ```
4. **Verify Configuration**:
   ```powershell
   & "C:\Program Files\Tailscale\tailscale.exe" serve status
   ```
   Expected output:
   ```text
   https://<your-node>.<your-tailnet>.ts.net/
   |-- proxy http://127.0.0.1:8000
   ```
5. **Verify Funnel is Disabled**:
   ```powershell
   & "C:\Program Files\Tailscale\tailscale.exe" funnel status
   ```
   Must display `(tailnet only)` to confirm zero public internet exposure.

---

## 11. Platform Advisory: Windows 10 Support & End-of-Life Risk

> [!WARNING]
> Microsoft ended standard mainstream support for **Windows 10** in **October 2025**. Running an always-on network control server on Windows 10 carries operational risk without Extended Security Updates (ESU).
>
> While Qwen Collective operates cleanly on Windows 10 (Build 19045), we recommend planning a migration to **Windows 11 Pro/Enterprise** or a dedicated headless **Debian 12 / Ubuntu 24.04 LTS** node. All application data in `%USERPROFILE%\.qwen-collective\data` is portable and can be moved without data loss.

---

## 12. Verification & Reliability Audit Summary

| Reliability Item | Status | Verification Method |
| :--- | :--- | :--- |
| **Fresh Application Startup** | **VERIFIED** | Validated via dedicated `.venv` and Python 3.12 lifespan hooks |
| **Database & Knowledge Persistence** | **VERIFIED** | Verified with persistent directory outside source tree (`test_persistence_and_backup_restore`) |
| **Protected External Credentials** | **VERIFIED** | Secrets stored outside git with restricted NTFS permissions (`test_service_xml_contains_no_hardcoded_secrets`) |
| **HTML / HTMX Injection Escaping** | **VERIFIED** | Escapes all untrusted hardware/probe outputs (`test_html_injection_prevention_in_htmx_ping/ssh`) |
| **Destination IP & DNS Policy** | **VERIFIED** | DNS-resolved, blocks metadata & link-local (`test_network_security_ipv4_and_ipv6`) |
| **Redirect & DNS-Rebinding Defense** | **VERIFIED** | `follow_redirects=False` enforced across all HTTP client instances |
| **Login Rate Limiting** | **VERIFIED** | 5 failed attempts per 5-minute window; returns HTTP 429 (`test_login_rate_limiting`) |
| **Trusted Proxy & Secure Cookies** | **VERIFIED** | Validated proxy check and HTTPS flag (`test_trusted_proxy_handling`) |
| **Crash Recovery & Auto-Restart** | **CONFIGURED** | WinSW configured with 5s/15s/30s restart thresholds |
| **Rotating Service Logs** | **CONFIGURED** | WinSW roll-by-size configured (10MB limit, 5 archives) |
| **SSH Host-Key Verification on Windows** | **VERIFIED** | Enforces `RejectPolicy` and loads `%USERPROFILE%\.ssh\known_hosts` |
| **Zero Machine / Offline State Handling** | **VERIFIED** | Tested explicit onboarding and offline warning badges (`test_ui_states.py`) |
| **Atomic Backup & Restore** | **VERIFIED** | Tested via `backup-windows.ps1` producing consistent ZIP snapshot |
| **Control Server Background Daemon** | **ACTIVE** | Live on `127.0.0.1:8000`, 200 OK verified |
| **Tailscale Serve (Private HTTPS)** | **ACTIVE** | Tested over HTTPS: valid TLS cert, secure session cookie, Funnel disabled |

