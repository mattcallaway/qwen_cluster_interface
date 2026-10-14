# Claim Reconciliation Matrix (CLAIM_RECONCILIATION.md)

**Audit Date:** October 14, 2026  
**Audited Commit:** `330125c`  
**Remediation Branch:** `fix/security-reliability-audit`  

---

## 1. Findings Matrix

| # | Security / Capability Claim | Actual Implementation | File & Line Reference | Verification Method | Disposition |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1** | **DNS Resolution Safeguards** | All hostnames resolve via `socket.getaddrinfo`; all returned IP addresses (IPv4 & IPv6) are validated against network policy. If any returned address is prohibited, target is blocked. | [`app/services/network_security.py:72-90`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/network_security.py#L72-L90) | Automated: `test_mixed_dns_answers_rejected` | **Confirmed** |
| **2** | **DNS-Rebinding Prevention** | `safe_execute_http` enforces `follow_redirects=False` and revalidates every redirect hop before following. `normalize_ip` unmaps `::ffff:...` representations. | [`app/services/network_security.py:228-258`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/network_security.py#L228-L258) | Automated: `test_safe_execute_http_blocks_redirect_to_metadata` | **Corrected** |
| **3** | **SSRF Destination Authorization** | Prohibits cloud metadata (`169.254.169.254`, `fd00:ec2::254`, `metadata.google.internal`), link-local (`169.254.0.0/16`, `fe80::/10`), user-info syntax, and restricts global/external connections to authorized service ports (`80, 443, 8000, 8080, 8081, 11434`). | [`app/services/network_security.py:108-160`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/network_security.py#L108-L160) | Automated: `test_ssrf_advanced.py` (8 test cases) | **Corrected** |
| **4** | **HTML / HTMX Injection Protection** | Dynamic output in HTMX responses is escaped using `html.escape()`. Untrusted command outputs are sanitized. Jinja2 autoescaping is active on all templates. | [`app/routes/machines.py:145-180`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/routes/machines.py#L145-L180) | Automated: `test_html_injection_prevention_in_htmx_ping` | **Confirmed** |
| **5** | **Login Rate Limiting** | Dual-keyed rate limiting on both client IP address and account identity (`username`). Persisted in SQLite `login_attempts` table so limits survive server restarts. Storage strictly bounded to 1,000 records. Anti-spoofing via `TRUSTED_PROXIES`. | [`app/routes/auth.py:40-98`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/routes/auth.py#L40-L98) | Automated: `tests/test_auth_ratelimit.py` (5 test cases) | **Corrected** |
| **6** | **Production Reload Disabled** | `app/main.py` explicitly disables reload (`should_reload = False`). Requires `QC_DEV_RELOAD=1` in `development` mode. WinSW and startup scripts omit `--reload`. | [`app/main.py:61-68`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/main.py#L61-L68) | Automated: `test_uvicorn_reload_disabled_in_production` | **Corrected** |
| **7** | **Token & Credential Isolation** | Production credentials (`QC_SECRET_KEY`, `QC_ADMIN_PASSWORD`) stored outside git in `%USERPROFILE%\.qwen-collective\qwen-collective.env` with restricted NTFS ACLs. Excluded from tracked XML, templates, and logs. `worker/vps_worker.py` enforces minimum 32-character tokens with constant-time verification. | [`worker/vps_worker.py:30-45`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/worker/vps_worker.py#L30-L45) | Automated: `tests/test_vps_security.py` | **Corrected** |
| **8** | **Tailscale Serve Deployment** | Tailscale Serve configured in background (`tailscale serve --bg 8000`). Let's Encrypt TLS certificate provisioned and validated via Schannel. Funnel confirmed disabled (`(tailnet only)`). | `tailscale serve status` | Manual / CLI: `curl.exe -v https://desktop-44h6qvp...` | **Confirmed** |
| **9** | **Windows Service Startup** | WinSW service wrapper registered in SCM as `QwenCollective` (`StartMode: Auto`). Points to virtualenv Python. Starts on boot under `LocalSystem`. | [`service/qwen-collective-service.xml:1-55`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/service/qwen-collective-service.xml#L1-L55) | CLI: `Get-CimInstance Win32_Service` | **Confirmed** |
| **10** | **Distributed Inference Capability** | Supports manual execution coordinates for `llama.cpp` RPC. Documentation and command generation revised: default coordinator host is loopback (`127.0.0.1`), not `0.0.0.0`. Baselines emphasize small models (0.5B-3B) on 4GB laptops; 7B treated as experimental. | [`app/services/inference.py:182-195`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/inference.py#L182-L195) | Code audit & test suite | **Corrected** |
| **11** | **VPS Research Capability** | Previous documentation advertised research, web retrieval, and browser automation. Audited worker code only implemented `echo_ping` and `network_probe`. Capabilities list corrected to reflect only genuine handlers. Phantom capabilities removed. | [`worker/vps_worker.py:61-68`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/worker/vps_worker.py#L61-L68) | Automated: `test_vps_worker_auth_endpoints` | **Corrected** |
| **12** | **Persistent Conversational Memory** | Hardcoded `default-session` and query freezing after 20 messages replaced with persistent `chat_conversations` table, user isolation, sliding context budgeting (10,000 chars), and strictly chronological context ordering to model. | [`app/routes/chat.py:60-105`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/routes/chat.py#L60-L105) | Automated: `tests/test_chat_isolation.py` (4 test cases) | **Corrected** |

---

## 2. Analysis of Past Overstated Claims

1. **Claim: "SSRF prevention completely solves outbound security"**
   - *Reality before remediation:* `is_global` treated any public IP as valid. IPv4-mapped IPv6 literals (`::ffff:169.254.169.254`) bypassed IP string checks. Redirects were not re-validated.
   - *Remediation:* Implemented `normalize_ip()` and `safe_execute_http()`, restricted global port access, and added adversarial test cases.
2. **Claim: "Distributed 7B model inference operational on two 4GB laptops"**
   - *Reality before remediation:* No genuine token-per-second benchmark or RPC coordination had been run on physical 4GB hardware. Running 7B models split across 4GB machines with context windows causes heavy swapping.
   - *Remediation:* Corrected documentation to set realistic expectations (0.5B-3B baselines) and warned against public unauthenticated RPC listeners.
3. **Claim: "Autonomous VPS research inbox operational"**
   - *Reality before remediation:* The worker endpoint returned simulated capabilities (`page_retrieval_sandbox`, `structured_research_inbox`) while only implementing `echo_ping` and `network_probe`.
   - *Remediation:* Removed phantom capability advertisement to reflect actual implementation.
