# Qwen Collective — Web Retrieval & Browser Security Specification

## 1. Threat Model & Security Principles

When an AI collective interfaces with the public internet, it encounters distinct security challenges:
1. **Server-Side Request Forgery (SSRF)**: An AI prompt or malicious external document attempts to pivot into internal controllers, cluster nodes, LAN devices, or cloud metadata services.
2. **DNS Rebinding Attacks**: An attacker controls a domain whose DNS initially resolves to a benign public IP, but rebinds during fetch to `127.0.0.1` or private IP ranges.
3. **Indirect Prompt Injection**: Hostile external web pages contain hidden adversarial instructions designed to hijack LLM system prompts, exfiltrate tokens, or alter project states.
4. **Denial of Service & Resource Exhaustion**: Infinite HTTP redirects, multi-gigabyte files, decompression bombs, or slowloris connections.
5. **Sandbox Escape & Process Compromise**: Malicious JavaScript attempting Chromium sandbox escapes or unrestricted OS command execution.

---

## 2. Server-Side Request Forgery (SSRF) Defenses

All URL fetching (`SecureFetcher`) and browser rendering (`HeadlessBrowserService`) enforce mandatory validation via `ResearchSecurityPolicy`.

### Prohibited Destination Ranges
The network defense layer resolves every target hostname to an IP address prior to connection and unconditionally drops any destination falling into:
- **IPv4 Loopback**: `127.0.0.0/8`
- **IPv4 Private LAN (RFC 1918)**: `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`
- **IPv4 Link-Local**: `169.254.0.0/16`
- **Cloud Instance Metadata Services**: `169.254.169.254`, `metadata.google.internal`
- **Tailscale CGNAT**: `100.64.0.0/10`
- **IPv6 Local / Link-Local / ULA**: `::1`, `fe80::/10`, `fc00::/7`
- **Broadcast & Documentation**: `255.255.255.255`, `0.0.0.0/8`, `192.0.2.0/24`

### Port & Scheme Restrictions
- Only schemes `http://` and `https://` are permitted.
- Only destination ports `80` and `443` are allowed. All internal ports (e.g. `8000`, `9000`, `22`, `6379`) are rejected before socket creation.

### DNS Rebinding Protection
Hostnames are resolved to IP addresses at validation time, and the destination IP is pinned or re-verified before establishing the TCP connection.

### Strict Redirect Revalidation
HTTP redirects (`301`, `302`, `303`, `307`, `308`) are never followed blindly. Each hop:
1. Decrements the redirect counter (maximum 5 hops).
2. Runs the new `Location` URL through full SSRF and destination IP validation.
3. Aborts immediately if a public URL redirects to an internal, private, or loopback address.

---

## 3. Streaming Reads & Resource Bounds

To prevent memory exhaustion:
- HTTP responses are read as a stream using `httpx.stream()`.
- Chunk size is checked incrementally. Once the cumulative body exceeds **5 MiB** (`5,242,880 bytes`), reading terminates immediately and returns an HTTP 413 / content size warning.
- Hard timeouts: 15.0 seconds total per document fetch, 10.0 seconds connection timeout.

---

## 4. Isolated Headless Browser Security (Playwright Chromium)

When client-side JavaScript rendering is required, the browser runs under strict isolation:
- **Dedicated Unprivileged Identity**: Chromium executes in headless mode without access to host system tokens or user profiles.
- **Project Isolation**: Every browser session creates a new, disposable browser context (`browser.new_context()`). Cookies, storage, and cache are destroyed immediately upon task completion. No cross-project cookie sharing.
- **Route-Level Request Interception**:
  The browser registers a universal route listener:
  ```python
  def _intercept_route(route):
      url = route.request.url
      is_valid, reason, _ = policy.validate_url(url)
      if not is_valid:
          route.abort("accessdenied")
          return
      route.continue_()
  ```
  Any sub-resource (images, scripts, iframes, AJAX calls) requested by client-side JavaScript targeting internal LAN, loopback, or cloud metadata endpoints is immediately aborted at the network level.
- **Download Prevention**: Arbitrary downloads, filesystem writes, and popup windows are blocked.

---

## 5. Indirect Prompt Injection & Untrusted Content Handling

External web pages must **never** be treated as system instructions or trusted code.

1. **Adversarial Pattern Scanning**:
   Retrieved text is passed through `ResearchSecurityScanner.screen_for_injection_patterns()`. Patterns such as:
   - `"ignore previous instructions"`
   - `"system prompt overrides"`
   - `"disregard all previous directives"`
   - `"execute privileged shell"`
   are detected and flagged.

2. **Delineation in Prompts**:
   When retrieved evidence is presented to language models for synthesis or evaluation, it is strictly enclosed in structured XML/Markdown boundary blocks:
   ```markdown
   === BEGIN RETRIEVED UNTRUSTED WEB EVIDENCE ===
   Source: [Example Source] (URL: https://example.org)
   Content:
   ...
   === END RETRIEVED UNTRUSTED WEB EVIDENCE ===
   ```
   System instructions explicitly instruct models to analyze the bounded evidence as passive data and never execute instructions found within it.

3. **No Direct Model Network Access**:
   Language models have zero direct access to network sockets or shell commands. All network interaction occurs exclusively through registered, typed controller tools with deterministic validation.
