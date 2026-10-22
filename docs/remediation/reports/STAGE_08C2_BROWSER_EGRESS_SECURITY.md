# STAGE 08C.2 — BROWSER EGRESS, ANTI-SSRF, AND DNS-REBINDING REPORT

**Remediation Phase:** Stage 8C — Operational Resilience & Execution Hardening  
**Task:** 8C.2 — Browser Egress, Anti-SSRF, and DNS-Rebinding Hardening  
**Status:** IMPLEMENTED AND VERIFIED (AWAITING HUMAN ACCEPTANCE)  
**Date:** October 21, 2026  
**Scope Authorization:** Limited Task 8C.2 Implementation ONLY (Tasks 8C.3–8C.5, Stage 9, and Production Deployment strictly on HOLD)

---

## 1. Browser and HTTP Egress Architecture

Qwen Collective conducts autonomous web research and external source verification using two distinct network engines:
1. **Headless Browser Rendering Engine ([`HeadlessBrowserService`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/browser.py))**: Runs ephemeral Chromium instances via Playwright for JavaScript-rendered SPAs, interactive DOM extraction, and table parsing.
2. **Streaming HTTP Retrieval Engine ([`SecureFetcher`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/retrieval.py))**: Runs high-performance streaming document retrieval with strict byte budgeting and per-hop redirect revalidation.

To prevent Server-Side Request Forgery (SSRF), internal LAN reconnaissance, cloud metadata exfiltration, and DNS rebinding attacks, outbound traffic is governed by a multi-layered defense-in-depth security perimeter:

```
[ Research Workflows / Agent Prompts ]
                 │
                 ▼
       [ ResearchSecurityPolicy ] ◄── (URL syntax, scheme, credential stripping, port validation)
                 │
       ┌─────────┴────────────────────────────────┐
       │                                          │
       ▼ (Headless Browser)                       ▼ (HTTP Fetcher & Search APIs)
[ HeadlessBrowserService ]               [ SecureFetcher / PinningHTTPTransport ]
       │                                          │
       ├─► Playwright Route Filter                ├─► Revalidation on every redirect hop
       │   `context.route("**/*")`                ├─► Cross-origin auth header stripping
       │   (URL & DNS preflight)                  │   (`Authorization`, `Cookie`, `X-Api-Key`)
       │                                          ▼
       ▼                                  [ PinningSyncBackend ]
[ OutboundFilteringProxy ]                        │
       │                                          ├─► Destination DNS resolution & verification
       ├─► Enforces allowed ports (80, 443)       ├─► Rejects private, ULA, link-local, metadata
       ├─► Resolves & verifies ALL IPs            └─► Direct socket connect to verified IP
       ├─► Pins connection to verified public IP      (`address = (pinned_ip, port)`)
       ├─► Handles CONNECT (HTTPS/WSS) & GET/POST     (SNI preserved, zero keepalive reuse)
       └─► Ephemeral loopback listener:
           `--proxy-server=http://127.0.0.1:{port}`
           `--proxy-bypass-list=<-loopback>`
```

---

## 2. Identified Network Enforcement Paths

A comprehensive pre-implementation inventory was conducted across the codebase to identify every outbound network pathway:

| # | Subsystem / Path | Mechanism | Enforcement Layer | SSRF & Rebinding Defense |
| :--- | :--- | :--- | :--- | :--- |
| **1** | **Browser Navigation** | `HeadlessBrowserService.render_page()` | Pre-navigation check via `ResearchSecurityPolicy` | Blocks loopback, private RFC1918, link-local, ULA, cloud metadata before browser launch. |
| **2** | **Browser Egress Proxy** | `OutboundFilteringProxy` | Local TCP proxy on ephemeral loopback port | Pins socket connections to verified public IPs. Intercepts HTTP and HTTPS/WSS CONNECT tunnels. |
| **3** | **Browser Subresources** | Playwright `context.route("**/*")` | `_route_filter` in browser context | Evaluates embedded images, scripts, iframes, and fetch requests, aborting unsafe requests. |
| **4** | **Browser WebSockets** | `ws://` and `wss://` handshakes | `OutboundFilteringProxy` | CONNECT and plain HTTP upgrade requests are intercepted and verified against policy. |
| **5** | **HTTP Document Retrieval**| `SecureFetcher.fetch()` | `PinningHTTPTransport` via HTTPX | Enforces streaming byte limits (5 MB max), timeout limits, and step-by-step redirect validation. |
| **6** | **Socket-Pinned Transport**| `PinningSyncBackend.connect_tcp()`| Low-level socket backend (`httpcore`) | Bypasses OS DNS during connection by pinning socket to resolved public IP; SNI retained. Zero keepalive expiry prevents connection reuse across requests. |
| **7** | **Search Providers** | `SearXNGProvider`, `ApiSearchProvider`, `DuckDuckGoProvider` | `PinningHTTPTransport(trust_env=False)` | All provider requests pass through socket-pinned transport, ignoring proxy env bypasses. |
| **8** | **Cluster Machine Probes** | `app/services/network_security.py` | Dedicated machine probe validator | Explicitly scoped to cluster nodes (VPS, llama-server, Ollama); strictly isolated from public research. |

---

## 3. Confirmed Vulnerabilities and Corrective Changes

During the Stage 8C.2 inspection, five vulnerabilities and hardening gaps were identified and corrected:

### Vulnerability 1: Incomplete IPv6 Prohibited Network Coverage and IPv4-Compatible Bypass
- **Finding**: While IPv4-mapped IPv6 (`::ffff:0:0/96`) was handled, IPv4-compatible IPv6 (`::/96`, deprecated RFC 4291) and deprecated site-local (`fec0::/10`) were absent from `BLOCKED_IPV6_NETWORKS`. An address such as `::127.0.0.1` had `ip.ipv4_mapped == None` and was not recognized as loopback by Python's `ipaddress` module.
- **Fix**: Added `::/96` and `fec0::/10` to `BLOCKED_IPV6_NETWORKS`. Updated `normalize_ip()` to extract the lower 32 bits (`IPv4Address(int(ip_obj) & 0xFFFFFFFF)`) for any IPv4-compatible address in `::/96`, properly unwrapping `::127.0.0.1` and `::10.0.0.1` to IPv4.

### Vulnerability 2: FQDN Trailing Dot Metadata Host Bypass
- **Finding**: Cloud metadata and localhost hostnames containing a trailing dot (e.g., `localhost.` or `metadata.google.internal.`) were not matched by strict string set equality in `BLOCKED_METADATA_HOSTS`.
- **Fix**: Updated `validate_url()` and `resolve_and_verify_destination()` to strip trailing dots (`hostname.rstrip(".")`) before checking against `BLOCKED_METADATA_HOSTS`. Added `localhost.localdomain` to the blocklist.

### Vulnerability 3: IPv6 Bracketed Host Parsing & Protocol Incompatibility in Filtering Proxy
- **Finding**: `OutboundFilteringProxy._handle_client()` parsed CONNECT targets using `target.split(":", 1)`. When presented with an IPv6 target such as `[::1]:443`, it parsed the host as `"["`, causing an unhandled parse error. Furthermore, outbound connection sockets were hardcoded to `socket.socket(socket.AF_INET)`, which broke dual-stack operation when connecting to legitimate public IPv6 destinations.
- **Fix**: Implemented bracket-aware host/port extraction for CONNECT and Host header parsing. Upgraded socket creation to `socket.create_connection((pinned_ip, target_port), timeout=self.timeout)`, ensuring seamless dual-stack IPv4/IPv6 support.

### Vulnerability 4: Missing Host Header Fallback on Relative HTTP Proxy Requests
- **Finding**: Plain HTTP proxy requests formatted as relative URIs (e.g., `GET /path HTTP/1.1`) resulted in empty `parsed.hostname`, which bypassed explicit hostname resolution.
- **Fix**: Added inspection of the HTTP `Host:` header as fallback to extract destination hostname and port.

### Vulnerability 5: Potential Credential Leakage Across Cross-Origin Redirects
- **Finding**: When `SecureFetcher.fetch()` followed HTTP redirects, custom authorization headers (e.g. `Authorization`, `Cookie`, `X-Api-Key`) were retained across cross-origin redirect hops.
- **Fix**: Implemented origin-comparison checks (`(next_parsed.scheme, next_parsed.hostname, next_parsed.port) != (orig_parsed.scheme, orig_parsed.hostname, orig_parsed.port)`). Sensitive headers (`Authorization`, `Cookie`, `Proxy-Authorization`, `X-Api-Key`) are stripped whenever a redirect crosses domain or origin boundaries.

---

## 4. Anti-SSRF and DNS-Rebinding Test Matrix

The dedicated test suite in [`tests/test_stage08c2_browser_egress.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage08c2_browser_egress.py) verifies all 12 mandatory adversarial conditions:

| Scenario | Condition Tested | Verification Method | Result | Status |
| :---: | :--- | :--- | :--- | :---: |
| **A** | Authorized Public Navigation | Navigated to mock public research target; rendered DOM and extracted text. | Status 200, content extracted | **PASS** |
| **B** | Direct Private-IP Navigation Denied | Tested 127.0.0.1, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.169.254, 100.64.0.0/10, octal (`0177.0.0.1`), integer (`2130706433`), abbreviated (`127.1`, `0x7f.1`), `localhost.`. | Denied with Status 403 / SSRF Violation | **PASS** |
| **C** | IPv6 Local/Private Navigation Denied | Tested `::1`, `::`, `fe80::/10`, `fc00::/7`, `fd00::/8`, `fec0::/10`, `::ffff:127.0.0.1`, `::127.0.0.1`. | Denied with Status 403 / SSRF Violation | **PASS** |
| **D** | Hostname Resolving to Private/Mixed IPs | Tested public hostname resolving to private IP and mixed public/private DNS answers. | Denied; rejected when ANY answer is private | **PASS** |
| **E** | DNS Rebinding Protection | Simulated public initial DNS answer followed by localhost rebind; verified socket pinning and `keepalive_expiry=0.0`. | Pinned socket connects to verified IP only; subsequent request blocked | **PASS** |
| **F** | Public-to-Private Redirect Denied | Tested public 302 redirecting to `127.0.0.1:8000` and `169.254.169.254`. | Hop 2 blocked with Status 403; redirect chain aborted | **PASS** |
| **G** | Browser Subresource Requests Blocked | Embedded private `<img>`, `<script>`, and `<iframe>` in public page. | Page text rendered; private subresources aborted by route filter | **PASS** |
| **H** | WebSocket Traffic Blocked | Tested CONNECT and plain HTTP `Upgrade: websocket` targeting internal ports. | Proxy rejected with Status 403 Forbidden | **PASS** |
| **I** | Filtering Proxy Fail-Closed | Simulated proxy startup failure (bind error) and proxy termination. | Headless browser execution failed closed with Status 503 | **PASS** |
| **J** | Proxy Bypass Defense | Inspected Chromium launch kwargs: verified `--proxy-bypass-list=<-loopback>`, `proxy.bypass=""`, `trust_env=False`. | Unrestricted direct egress cannot be enabled silently | **PASS** |
| **K** | Legitimate Public Redirects Succeed | Tested 302 redirect between valid public paths within allowed policy. | Followed successfully; Status 200, 2 hops in chain | **PASS** |
| **L** | Credential Isolation on Redirects | Sent `Authorization`, `Cookie`, `X-Api-Key` across cross-origin redirect; inspected target request. | Sensitive headers stripped on cross-origin hop; omitted from errors | **PASS** |

---

## 5. Browser Proxy Fail-Closed Evidence

Headless browser automation enforces strict fail-closed behavior across all failure modes:
1. **Proxy Startup / Bind Failure**: If `OutboundFilteringProxy.start()` raises `OSError` or fails to bind, `HeadlessBrowserService.render_page()` aborts immediately without launching Chromium, returning `status_code=503` and `error_message="Secure browser egress unavailable: egress filter proxy initialization failed..."`.
2. **Proxy Termination / Unreachability**: If the proxy process terminates during execution, Chromium's network layer reports `net::ERR_PROXY_CONNECTION_FAILED`. Chromium is explicitly configured with `--proxy-bypass-list=<-loopback>`, preventing fallback to direct network interfaces. `render_page()` catches the navigation error and returns `status_code=504`.
3. **Invalid DNS Responses**: If DNS lookup fails or returns unparseable answers, pre-navigation destination validation returns `status_code=403` with an explicit diagnostic explanation.
4. **Missing Automation Library**: If Playwright is not installed, the service returns `status_code=503` rather than attempting uncontained network operations.

---

## 6. Credential Isolation Verification

1. **Ephemeral Browser Sessions**: Each task runs in a fresh, isolated `browser.new_context()`. Browser sessions never inherit internal application cookies, session tokens, or local storage.
2. **Automatic Header Scrubbing on Cross-Origin Redirects**: `SecureFetcher.fetch()` compares the origin of each redirect hop against the initial origin. If the destination domain or port changes, `Authorization`, `Cookie`, `Proxy-Authorization`, and `X-Api-Key` headers are scrubbed before the subsequent request is issued.
3. **Error Redaction**: Rejection responses generated by SSRF filters, the egress proxy, and retrieval services do not echo submitted bearer tokens or user credentials in logs or error messages.

---

## 7. Remaining Transport and Browser Limitations

1. **Network Proxy is Not an OS Sandbox**: While `OutboundFilteringProxy` and socket pinning strictly contain network egress, they do not constitute an operating system container or process sandbox. Malicious untrusted binary code execution remains strictly prohibited.
2. **Encrypted HTTPS SNI Inspection**: CONNECT tunneling performs destination-IP verification and pinning during socket establishment. In accordance with standard forward-proxy architecture without TLS interception (MITM), encrypted payload contents within an established public HTTPS tunnel are not inspected.
3. **In-Memory Volatility**: The proxy audit record log resides in memory for the duration of the proxy instance and is discarded upon context closure. Persistent logging is handled via standard application logging channels.

---

## 8. Targeted Test Results

The dedicated Stage 8C.2 test suite was executed:

```
Command: pytest tests/test_stage08c2_browser_egress.py -v
Platform: Windows (Python 3.12.10, pytest-9.1.1)
Result: 12 passed in 12.39s (Exit Code: 0)
```

Additionally, Stage 1 security regression tests were verified:
```
Command: pytest tests/test_stage01_security.py -v
Result: 26 passed in 5.76s (Exit Code: 0)
```

---

## 9. Full Deterministic Regression Results

The deterministic test runner ([`run_evidence_suite.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/run_evidence_suite.py)) was executed across the complete repository:

```
Command: python -m pytest -v --junitxml=docs/remediation/evidence/junit_report.xml
Start Time: 2026-10-21T08:29:29.746972+00:00
End Time:   2026-10-21T08:33:47.951305+00:00
Duration:   258.20 seconds
Exit Code:  0
```

### Aggregate Test Counts
- **Total Collected**: 747 tests across 70 modules (+12 tests added from Stage 8C.2)
- **Passed**: 746 tests
- **Failed**: 0 tests
- **Skipped**: 1 test (`test_searxng_provider_live_network_optional` — transparently skipped live external network test)
- **Errors**: 0 errors
- **Arithmetic Check**: 747 = 746 passed + 0 failed + 1 skipped + 0 errors (**PASSED**)

### Evidence Files Generated
- `docs/remediation/evidence/junit_report.xml`
- `docs/remediation/evidence/pytest_stdout.txt`
- `docs/remediation/evidence/pytest_execution_meta.json`
- `docs/remediation/evidence/test_ids_inventory.json`
- `docs/remediation/evidence/test_matrix.json`
- `docs/remediation/evidence/working_tree_patch.diff`

---

## 10. Reproducible Source Checkpoint

- **Git Baseline Commit**: `c98510b9a1569a113c762f083f5bba4395704531`
- **Branch**: `feature/unified-artifact-deliverables`
- **Working Tree Patch**: [`docs/remediation/evidence/working_tree_patch.diff`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/working_tree_patch.diff)
- **Total Tests Passing**: 746 / 747 (1 skipped live network)

---

## 11. GO / HOLD Recommendation

- **Task 8C.2**: **IMPLEMENTED AND VERIFIED (GO)** — All anti-SSRF protections, IPv6 edge cases, DNS rebinding defenses, browser proxy fail-closed behaviors, credential isolation measures, and full regression tests passed with zero errors.
- **Tasks 8C.3–8C.5**: **HOLD** — Awaiting human review and explicit authorization.
- **Stage 9 & Production Deployment**: **HOLD** — Strictly forbidden under current authorization.

**STAGE 8C.2 IS COMPLETE. STOPPED FOR HUMAN REVIEW.**
