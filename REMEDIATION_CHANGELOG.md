# Remediation Changelog (REMEDIATION_CHANGELOG.md)

**Audit Date:** October 14, 2026  
**Remediation Branch:** `fix/security-reliability-audit`  

---

## Modified and Added Files Summary

| File | Type | Rationale for Modification |
| :--- | :--- | :--- |
| [`worker/vps_worker.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/worker/vps_worker.py) | Modified | Removed fallback authentication token (`default-vps-secret-token`). Enforced minimum 32-character token. Replaced `!=` with `secrets.compare_digest()`. Defaulted binding to `127.0.0.1` instead of `0.0.0.0`. Aligned advertised capabilities strictly to implemented handlers. |
| [`app/services/network_security.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/network_security.py) | Modified | Added `normalize_ip()` to unmap IPv4-mapped IPv6 literals. Added CIDR checks for `169.254.0.0/16` and `fe80::/10`. Rejected URL user-info credentials. Restricted global destinations to authorized service ports. Added `safe_execute_http()` with redirect revalidation. |
| [`app/services/inference.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/inference.py) | Modified | Changed default coordinator host in `generate_distributed_launch_command` from `0.0.0.0` to loopback `127.0.0.1`. |
| [`app/services/vps.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/vps.py) | Modified | Aligned default and fallback capability lists in VPS registration and probe methods to genuine worker capabilities (`health_check`, `test_task`, `echo_ping`, `network_probe`). |
| [`app/database.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/database.py) | Modified | Added `chat_conversations` table and `login_attempts` table to SQLite schema with supporting performance indexes. |
| [`app/routes/auth.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/routes/auth.py) | Modified | Implemented SQLite-backed rate limiting surviving server restarts. Enforced dual-keyed throttling on both client IP and account identity (`username`). Added automatic record pruning and bounded total records to 1,000. Added test compatibility shims. |
| [`app/routes/chat.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/routes/chat.py) | Modified | Replaced hardcoded `SESSION_ID = "default-session"` and frozen 20-message limit with persistent conversation management, user ownership isolation, sliding context budgeting (`build_context_messages`), and strictly chronological context ordering. |
| [`app/templates/chat.html`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/templates/chat.html) | Modified | Added conversation selector, "New Chat" button, active conversation title display, and explicit `session_id` form inputs. |
| [`app/main.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/main.py) | Modified | Explicitly disabled Uvicorn reload by default (`should_reload = False`). Requires explicit `QC_DEV_RELOAD=1` in development mode. |
| [`README.md`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/README.md) | Modified | Updated test badge to 85 passed. Corrected distributed inference claims to prioritize 0.5B-3B quantized models on 4GB hardware. Added warnings against public llama.cpp RPC exposure. |
| [`requirements-lock.txt`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/requirements-lock.txt) | Added | Pinned exact 40 installed package versions to provide a reproducible production build environment. |
| [`tests/test_vps_security.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_vps_security.py) | Added | 6 tests covering VPS token validation, timing-safe authentication, loopback binding security, capability reflection, and shell task rejection. |
| [`tests/test_ssrf_advanced.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_ssrf_advanced.py) | Added | 8 adversarial tests covering IPv4-mapped IPv6, link-local, user-info rejection, mixed DNS answers, and redirect revalidation. |
| [`tests/test_auth_ratelimit.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_auth_ratelimit.py) | Added | 5 tests covering IP and account throttling, SQLite persistence, bounded record retention, and proxy header anti-spoofing. |
| [`tests/test_chat_isolation.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_chat_isolation.py) | Added | 4 tests covering conversation isolation, context budgeting with newest messages, chronological context ordering, and legacy session migration. |
| [`tests/test_vps_worker.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_vps_worker.py) | Modified | Updated test setup to supply a secure test token compliant with hardened validation rules. |
