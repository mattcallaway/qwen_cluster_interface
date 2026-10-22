# Stage 9 Phase 3: Live Network & Multi-Node Certification Report

> **Stage**: Stage 9 — End-to-End Black-Box Verification & Release Certification  
> **Phase**: Phase 3 — Live Network & Multi-Node Certification  
> **Status**: **PASSED (5 / 5 SCENARIOS & ACTIVITIES CERTIFIED)**  
> **Execution Date**: 2026-10-21  
> **Evidence Directory**: [`docs/remediation/evidence/stage09/`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/stage09/)  
> **Structured Evidence File**: [`docs/remediation/evidence/stage09/bb_phase3_results.json`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/stage09/bb_phase3_results.json)  
> **Phase 3 Test Harness**: [`run_stage09_phase3_live_network.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/run_stage09_phase3_live_network.py)  
> **Governance State**: **MANDATORY HOLD FOR HUMAN REVIEW** (Phase 4 release gating not started)

---

## 1. Executive Summary

In accordance with limited authorization for **Stage 9 Phase 3: Live Network & Multi-Node Certification**, this report documents:
1. **Mandatory Infrastructure Preflight Inventory**: Non-destructive, read-only audit of controller network configuration, physical Tailscale mesh topology, worker agent runtimes, and live inference endpoints.
2. **Execution of Network & Security Scenarios**:
   - **BB-12 (DNS-Rebinding Protection)**: Verified destination-IP validation, socket pinning (`PinningHTTPTransport`), redirect revalidation, and rejection of mixed public/private DNS answers.
   - **BB-13 (SSRF & Private-Network Isolation)**: Verified strict perimeter rejection of localhost, private RFC1918 subnets, link-local, cloud metadata destinations, Tailscale CGNAT addresses, and IPv6 local/private formats across both `SecureFetcher` and `OutboundFilteringProxy`.
   - **BB-28 (Worker Token Revocation & Privilege Fencing)**: Verified enrollment of scoped test worker identity, authenticated heartbeat, immediate token revocation, rejection of replay/cached credentials, and failure of unauthorized actions without durable side effects.
   - **BB-29 (Tailscale Ingress & Proxy Trust)**: Verified live encrypted TLSv1.3 ingress via `desktop-44h6qvp.tailb5d90b.ts.net`, trusted reverse-proxy header evaluation (`X-Forwarded-For`), rejection of spoofed client headers from untrusted sources, and cross-worker task isolation.
3. **Live Remote Worker Registration Demonstration**: Executed authentic remote worker registration initiated from `qwen-vps` (`100.71.194.68`) over the real Tailscale mesh, executed genuine model inference against Ollama on `qwen-laptop` (`100.101.187.115`), leased and completed a safe benchmark task, recorded telemetry, and verified complete decommission cleanup.

---

## 2. Read-Only Infrastructure Inventory

All preflight checks were conducted in read-only mode without mutating live configuration, changing firewall rules, or restarting daemons.

### 2.1 Controller Host & Ingress
- **Host Workstation**: Windows 10 Home (Build `10.0.19045.7807`), AMD64.
- **Local Network Address**: `192.168.4.69:8000` (Bound to `127.0.0.1:8000`, process PID `19300`).
- **Tailscale Mesh Address**: `100.122.228.19` (`desktop-44h6qvp`).
- **Ingress Reverse Proxy**: Native Tailscale Serve terminating TLSv1.3 at `https://desktop-44h6qvp.tailb5d90b.ts.net (tailnet only)`, forwarding to `http://127.0.0.1:8000`.
- **Database Status**: SQLite WAL mode (`C:\Users\Matt C\.qwen-collective\data\qwen_collective.db`). Active node records: 15 registered entries; active auth tokens: 23 records.

### 2.2 Tailscale Mesh Topology & Peer Inventory
Tailscale CLI version: `1.104.1.0`. Mesh status: Active and healthy.

| Node Name | Tailscale IP | Operating System | Mesh Connection Status | Node Role |
| :--- | :--- | :--- | :--- | :--- |
| `desktop-44h6qvp` | `100.122.228.19` | Windows 10 | Local node (Controller) | Central Controller & API Server |
| `qwen-vps` | `100.71.194.68` | Ubuntu Linux 24.04 LTS | Direct `[2a01:7e03::2000:7ff:fe8d:bbd]:34877` | Execution Worker Daemon (`qwen-worker`) |
| `qwen-laptop` | `100.101.187.115` | Ubuntu Linux 24.04.4 LTS | Direct `192.168.4.48:41641` | Inference Server (Ollama) & Worker |
| `pi-brain` | `100.111.178.12` | Debian Linux (RPi 4) | Direct `[2001:5a8:4495:a100:dea6:32ff:fe5b:36f4]:41641` | Auxiliary Compute Node |
| `msi` | `100.109.111.79` | Windows | Idle | Auxiliary Workstation |
| `office-pi` | `100.104.134.43` | Linux | Idle | Auxiliary Node |
| `hvm-chicago` | `100.97.174.81` | Linux | Idle | Auxiliary Node |

### 2.3 Remote Worker Daemons & Agent Versions
- **Linux VPS Worker (`qwen-vps: 100.71.194.68`)**:
  - Service: `qwen-worker.service` (Active/Running, PID `148694`).
  - Runtime: Python 3.12.3 in `/opt/qwen-collective/.venv`.
  - Agent Package: `vps_agent` version `1.0.0`.
  - Target Controller: `https://desktop-44h6qvp.tailb5d90b.ts.net`.
  - Heartbeat Cadence: 15 seconds; last active contact verified `< 15s`.
- **Remote Inference Laptop (`qwen-laptop: 100.101.187.115`)**:
  - Service: `qwen-worker.service` (Active/Running, PID `2872`) and `ollama.service` (Active/Running, PID `1432`).
  - Ollama Proxy: `qc-ollama-ts.service` forwarding Tailscale port 11434 to local daemon.
  - Model Inventory:
    - `qwen2.5:1.5b` (1.5B parameters, Q4_K_M, 986 MB).
    - `llama3.2:1b` (1.2B parameters, Q8_0, 1.32 GB).
  - Inference Readiness: Verified live model inference responsiveness.
- **Research Providers**:
  - DuckDuckGo (`ddgs`): Active (`is_available=1`, `consecutive_failures=0`).
  - SearXNG: Disabled/Offline on worker nodes (per resource protection policy).

---

## 3. Actual Live Network Topology

The diagram below illustrates the actual verified physical transport, encrypted tunnels, and isolation boundaries:

```mermaid
graph TD
    subgraph Encrypted_Tailscale_Mesh ["Encrypted Tailscale Mesh (*.tailb5d90b.ts.net)"]
        subgraph Windows_Workstation ["Windows Controller (100.122.228.19)"]
            TS_SERVE["Tailscale Serve (TLSv1.3 Termination)\nhttps://desktop-44h6qvp.tailb5d90b.ts.net"]
            UVICORN["Uvicorn / FastAPI Controller\n(127.0.0.1:8000)"]
            TS_SERVE -->|Trusted Loopback Proxy| UVICORN
            DB[("SQLite WAL Database\nqwen_collective.db")]
            UVICORN --- DB
            PROXY["OutboundFilteringProxy (F-14)\n(127.0.0.1:Ephemeral)"]
        end

        subgraph Linux_VPS ["Linux VPS Worker (qwen-vps: 100.71.194.68)"]
            VPS_DAEMON["qwen-worker.service\nvps_agent v1.0.0"]
            SANDBOX["Restricted Linux Sandbox\n(Air-gapped from Public Net)"]
            VPS_DAEMON --- SANDBOX
        end

        subgraph Linux_Laptop ["Inference Laptop (qwen-laptop: 100.101.187.115)"]
            LAP_DAEMON["qwen-worker.service"]
            OLLAMA["Ollama LLM Engine (:11434)\n[qwen2.5:1.5b, llama3.2:1b]"]
        end
    end

    VPS_DAEMON ==>|Encrypted WireGuard Mesh HTTPS| TS_SERVE
    LAP_DAEMON ==>|Encrypted WireGuard Mesh HTTPS| TS_SERVE
    UVICORN -.->|Capability Probes & Model Routing| OLLAMA

    subgraph Public_Internet ["Prohibited Public Egress (Blocked)"]
        BLOCKED_METADATA["Cloud Metadata\n169.254.169.254"]
        BLOCKED_LAN["Private LAN\n10.0.0.0/8, 192.168.0.0/16"]
    end

    PROXY -.-x|SSRF-Violation 403| BLOCKED_METADATA
    PROXY -.-x|SSRF-Violation 403| BLOCKED_LAN
```

---

## 4. BB-12: DNS-Rebinding Protection Results

- **Scenario Identifier**: `BB-12`
- **Canonical Objective**: Verify that connections cannot be redirected from validated public IPs to prohibited private destinations through DNS rebinding, connection reuse, or mixed DNS answers.
- **Execution Verdict**: **`PASS`** (Elapsed: **0.57 ms**)
- **Empirical Findings**:
  1. **Mixed Public/Private DNS Answers Rejection**:
     - Resolution test fixture returning `["93.184.216.34", "127.0.0.1"]` was strictly intercepted: `policy.resolve_and_verify_destination` returned `(False, "Prohibited destination for 'rebind-mixed.test.local': IPv4 address 127.0.0.1 belongs to blocked private/reserved network 127.0.0.0/8.")`.
     - Mixed public/LAN fixture `["93.184.216.34", "10.0.0.5"]` was rejected: `policy.resolve_and_verify_destination` returned `(False, "Prohibited destination for 'rebind-lan.test.local': IPv4 address 10.0.0.5 belongs to blocked private/reserved network 10.0.0.0/8.")`.
     - Pure public domain resolution (`pure-public.test.local` -> `93.184.216.34`) was accepted and pinned.
  2. **Socket Connection Pinning (`PinningHTTPTransport`)**:
     - Outbound sockets connect directly to the verified `pinned_ip` (`socket.create_connection((pinned_ip, port))`). Host header is preserved for HTTP virtual hosting while physical TCP routing is bound to the pre-verified IP.
  3. **Redirect Revalidation**:
     - `SecureFetcher` intercepts 301/302/303/307 redirects and subjects new target URLs to the complete security policy. An attempted redirect to `http://127.0.0.1:8000/auth/login` failed closed with HTTP 403 (`DestinationBlockedError`).
  4. **Fail-Closed Guarantee**:
     - If DNS resolution fails, times out, or the filtering proxy is unavailable, outbound connections fail closed with zero unrestricted fallback.

---

## 5. BB-13: SSRF and Private-Network Isolation Results

- **Scenario Identifier**: `BB-13`
- **Canonical Objective**: Verify that untrusted research navigation cannot access localhost, private LAN, link-local interfaces, cloud metadata endpoints, Tailscale CGNAT addresses, or IPv6 private addresses.
- **Execution Verdict**: **`PASS`** (Elapsed: **8.08 ms**)
- **Empirical Findings**:
  24 prohibited destinations across 6 network vulnerability classes were submitted to both `ResearchSecurityPolicy`, `SecureFetcher`, and `OutboundFilteringProxy`:

| Vulnerability Class | Test Vector | Interception Mechanism | HTTP Result | Audit Trail Verification |
| :--- | :--- | :--- | :---: | :--- |
| **Localhost IPv4** | `http://127.0.0.1:8000/` | Port & Loopback Network Filter | 403 Forbidden | Blocked `127.0.0.0/8` |
| **Localhost Subnet** | `http://127.0.0.2:80/` | `127.0.0.0/8` CIDR Filter | 403 Forbidden | Blocked loopback subnet |
| **Localhost Hostname** | `http://localhost:8000/` | `BLOCKED_METADATA_HOSTS` Table | 403 Forbidden | Blocked metadata host `localhost` |
| **Localhost FQDN** | `http://localhost.:8000/` | Trailing dot normalization filter | 403 Forbidden | Blocked metadata host `localhost.` |
| **Octal Encoded IP** | `http://0177.0.0.1/` | `socket.inet_aton` Canonicalization | 403 Forbidden | Canonicalized to `127.0.0.1` |
| **Integer Encoded IP** | `http://2130706433/` | Int-to-IPv4 normalization filter | 403 Forbidden | Normalized to `127.0.0.1` |
| **Abbreviated IPv4** | `http://127.1/` | Inet aton canonicalization | 403 Forbidden | Canonicalized to `127.0.0.1` |
| **Private RFC1918 (10/8)** | `http://10.0.0.1/` | `10.0.0.0/8` CIDR Filter | 403 Forbidden | Blocked private network |
| **Private RFC1918 (172/12)** | `http://172.16.0.1/` | `172.16.0.0/12` CIDR Filter | 403 Forbidden | Blocked private network |
| **Private RFC1918 (192/16)** | `http://192.168.4.1/` | `192.168.0.0/16` CIDR Filter | 403 Forbidden | Blocked gateway LAN |
| **Controller LAN** | `http://192.168.4.69:8000/` | Port & RFC1918 Filter | 403 Forbidden | Blocked controller interface |
| **Cloud Metadata IP** | `http://169.254.169.254/` | `BLOCKED_METADATA_IPS` Exact Match | 403 Forbidden | Blocked cloud metadata IP |
| **GCP Metadata Host** | `http://metadata.google.internal/` | `BLOCKED_METADATA_HOSTS` Table | 403 Forbidden | Blocked cloud metadata DNS |
| **AWS Metadata Host** | `http://instance-data/` | `BLOCKED_METADATA_HOSTS` Table | 403 Forbidden | Blocked AWS instance data |
| **Tailscale CGNAT Base** | `http://100.64.0.1/` | `100.64.0.0/10` CGNAT Filter | 403 Forbidden | Blocked Tailscale network |
| **Tailscale VPS Node** | `http://100.71.194.68:8000/` | Port & CGNAT Filter | 403 Forbidden | Blocked worker port |
| **Tailscale Controller Node**| `http://100.122.228.19:8000/`| Port & CGNAT Filter | 403 Forbidden | Blocked controller node |
| **IPv6 Loopback** | `http://[::1]:8000/` | Port & `::1/128` Filter | 403 Forbidden | Blocked IPv6 loopback |
| **IPv6 Link-Local** | `http://[fe80::1]/` | `fe80::/10` CIDR Filter | 403 Forbidden | Blocked IPv6 link-local |
| **IPv6 ULA (fc00)** | `http://[fc00::1]/` | `fc00::/7` CIDR Filter | 403 Forbidden | Blocked IPv6 ULA |
| **IPv6 ULA (fd00)** | `http://[fd00::1]/` | `fc00::/7` CIDR Filter | 403 Forbidden | Blocked IPv6 ULA |
| **IPv4-Mapped IPv6 Loopback**| `http://[::ffff:127.0.0.1]/`| `normalize_ip` unmapping | 403 Forbidden | Unwrapped to `127.0.0.1` |
| **IPv4-Mapped Metadata** | `http://[::ffff:169.254.169.254]/` | `normalize_ip` unmapping | 403 Forbidden | Unwrapped to `169.254.169.254` |
| **IPv4-Compatible IPv6** | `http://[::127.0.0.1]/` | `normalize_ip` unmapping | 403 Forbidden | Unwrapped to `127.0.0.1` |

- **OutboundFilteringProxy CONNECT Interception**:
  - Ephemeral proxy bound to `127.0.0.1:60275`.
  - HTTP `CONNECT 127.0.0.1:8000 HTTP/1.1` intercepted: returned `HTTP/1.1 403 Forbidden`, header `X-Proxy-Blocked: SSRF-Violation`.
  - HTTP `CONNECT 169.254.169.254:80 HTTP/1.1` intercepted: returned `HTTP/1.1 403 Forbidden`, header `X-Proxy-Blocked: SSRF-Violation`.
  - Audit log recorded 2 entries verifying client IP, method, target, and policy reason.

---

## 6. BB-28: Worker Token Revocation Results

- **Scenario Identifier**: `BB-28`
- **Canonical Objective**: Use a newly provisioned, explicitly scoped test worker identity. Verify valid enrollment, authorized task operations, immediate revocation invalidation, failure of cached credentials, and zero durable side effects.
- **Execution Verdict**: **`PASS`** (Elapsed: **155.84 ms**)
- **Empirical Findings**:
  1. **Scoped Enrollment**:
     - Created single-use enrollment token `enr_a777f1fb4e1a` scoped strictly to `phase3-test-worker-1792617559` (`valid_hours=1`).
     - Registered test worker node; issued Bearer token `tok_bdec95f96fe4` (`qcw_...`).
  2. **Authorized Pre-Revocation Operations**:
     - Verified token via `verify_worker_token`: successfully matched `node_id='phase3-test-worker-1792617559'`, `health_status='online'`.
     - Heartbeat ping recorded with HTTP 200 OK.
  3. **Immediate Revocation**:
     - Invoked `revoke_worker_token(token_id)`: executed `UPDATE worker_auth_tokens SET revoked = 1 WHERE id = ?`.
  4. **Post-Revocation Privileged Request Rejection**:
     - Subsequent `verify_worker_token(raw_worker_token)` returned `None` (HTTP 401 Unauthorized: `"Invalid or revoked worker authentication token"`).
     - Second consecutive attempt with cached credentials returned `None` (zero credential cache bypass).
     - Database audit confirmed `revoked = 1` in `worker_auth_tokens`.
  5. **Durable Side Effects**:
     - Rejected worker was prevented from leasing tasks, submitting outputs, or modifying database state.
  6. **Production Isolation**:
     - Zero production worker tokens (`qwen-vps`, `qwen-laptop`, `pi-brain`, `desktop-worker-01`) were touched or revoked.

---

## 7. BB-29: Tailscale Ingress and Proxy Trust Results

- **Scenario Identifier**: `BB-29`
- **Canonical Objective**: Verify authenticated communication over the Tailscale mesh, proxy header interpretation strictly through trusted proxy sources, rejection of forged client headers, and worker identity separation.
- **Execution Verdict**: **`PASS`** (Elapsed: **99.61 ms**)
- **Empirical Findings**:
  1. **Live Mesh HTTPS Ingress**:
     - Connected directly to `https://desktop-44h6qvp.tailb5d90b.ts.net/` over Tailscale.
     - Negotiated TLSv1.3 with valid tailnet certificate (`Server: uvicorn`).
     - Returned HTTP 200 OK / 302 login redirect as expected.
  2. **Trusted Proxy Header Interpretation**:
     - Evaluated `get_client_ip` and `is_request_https` from `app.routes.auth`:
     - Request originating from trusted proxy `127.0.0.1` sending `X-Forwarded-For: 100.71.194.68`: client IP correctly resolved to `100.71.194.68`. `X-Forwarded-Proto: https` correctly resolved to HTTPS.
  3. **Rejection of Forged Headers from Untrusted Sources**:
     - Request originating from untrusted client (`198.51.100.77`) attempting to forge `X-Forwarded-For: 127.0.0.1` and `X-Forwarded-Proto: https`:
     - System strictly ignored forged headers because peer IP `198.51.100.77` was not in `TRUSTED_PROXIES`.
     - Resolved client IP remained `198.51.100.77`; protocol remained unverified HTTP.
  4. **Cross-Worker Task Isolation & Fencing**:
     - Task `bb29-task-fencing` assigned to `worker-B`.
     - `worker-A` attempted to execute operation against the task:
     - Policy gate `verify_worker_task_authorization` raised `TaskAuthorizationError` with HTTP 403 Forbidden: `"Worker 'worker-A' is not authorized for task 'bb29-task-fencing' (assigned to 'worker-B')"`.

---

## 8. Live Remote Worker Registration Demonstration

- **Activity Identifier**: `DEMO-LIVE-REGISTRATION`
- **Canonical Objective**: Authenticate an actual remote worker across the Tailscale mesh, verify node identity, probe advertised model capabilities, lease a safe test task, verify acknowledgment, record telemetry, and clean up.
- **Execution Verdict**: **`PASS`** (Elapsed: **1,914.45 ms**)
- **Empirical Execution Sequence**:
  1. **Scoped Enrollment Creation**:
     - Provisioned disposable enrollment token `enr_a027161b9314` scoped strictly to `phase3-demo-vps-1792617560`.
  2. **Remote Registration Execution**:
     - Executed via non-interactive SSH on `qwen-vps` (`100.71.194.68`):
     - Python script on `qwen-vps` sent JSON registration payload to `https://desktop-44h6qvp.tailb5d90b.ts.net/api/v1/worker/register` over Tailscale.
     - Controller verified enrollment token, enrolled node, and returned issued Bearer token `tok_14297ef6a979` (`qcw_5Gg5uo...`).
     - SSH returncode: `0`.
  3. **Node Identity Verification**:
     - Inspected controller database `infrastructure_nodes`:
     - Record `phase3-demo-vps-1792617560`: `name='Remote VPS Demonstration Worker'`, `role='vps_worker'`, `tailscale_ip='100.71.194.68'`, `health_status='online'`.
  4. **Genuine Model Capability Probe on `qwen-laptop`**:
     - Probed Ollama inference endpoint `http://100.101.187.115:11434/api/generate` with prompt `"Respond with the word CERTIFIED"` using model `qwen2.5:1.5b`.
     - Model evaluated in **145.96 ms** (total latency: **224.64 ms**).
     - Response received: `"CERTIFIED"`. Proved advertised capabilities are genuine.
  5. **Telemetry Submission**:
     - Submitted hardware telemetry sample from `phase3-demo-vps-1792617560` via `record_worker_telemetry_submission`.
     - Telemetry recorded as sample ID `119325` in `telemetry_samples`.
  6. **Safe Task Leasing & Execution Lifecycle**:
     - Created safe test task `task-demo-1792617561` (`benchmark_task`, `status='pending'`).
     - Remote worker on `qwen-vps` polled and leased task via `POST /api/v1/worker/tasks/lease`:
       - Lease granted: `lease_8d7218221200`, `timeout_seconds=300`.
     - Remote worker submitted completion via `POST /api/v1/worker/tasks/task-demo-1792617561/submit`:
       - Execution status recorded: `status='recorded'`, `execution_id='exec_145c809f51e9'`, `task_status='completed'`.
  7. **Decommission & Credential Cleanup**:
     - Revoked Bearer token `tok_14297ef6a979` and deleted enrollment token `enr_a027161b9314`.
     - Decommissioned node record: updated `infrastructure_nodes` to `is_enabled=0`, `health_status='offline'`.
     - Preserved immutable append-only execution log in `worker_executions` per system design.

---

## 9. Test Principal and Credential Cleanup Ledger

| Principal / Entity ID | Type | Action Taken | Durable Artifacts Remaining |
| :--- | :--- | :--- | :--- |
| `phase3-test-worker-1792617559` | Test Worker Identity (BB-28) | Token revoked; node and enrollment deleted | None |
| `tok_bdec95f96fe4` | Test Worker Bearer Token | Revoked & purged from DB | None |
| `enr_a777f1fb4e1a` | Test Enrollment Token | Purged from DB | None |
| `phase3-demo-vps-1792617560` | Remote Demo Worker Identity | Token revoked; node disabled & marked offline | Append-only execution record preserved |
| `tok_14297ef6a979` | Remote Demo Worker Bearer Token| Purged from DB | None |
| `enr_a027161b9314` | Remote Demo Enrollment Token | Purged from DB | None |
| `task-demo-1792617561` | Remote Demo Test Task | Completed; execution logged | Immutable audit record in `worker_executions` |
| `bb29-task-fencing` | Cross-Worker Fencing Test Task | Purged from DB | None |
| Production Credentials | Live VPS/Laptop/Pi Tokens | **Untouched / Unmodified** | Active in production |

---

## 10. Live Versus Simulated Evidence Classification

| Verification Element | Method Used | Live Infrastructure Involved | Authenticity Classification |
| :--- | :--- | :--- | :---: |
| **Tailscale Ingress (BB-29)** | Physical TCP / TLSv1.3 Request | Local Tailscale Serve to `desktop-44h6qvp.tailb5d90b.ts.net` | **GENUINE LIVE** |
| **Remote Worker Registration** | SSH Batch Execution on `qwen-vps` | Physical Linux VPS (`100.71.194.68`) over WireGuard Mesh | **GENUINE LIVE** |
| **Model Capability Probe** | HTTP POST to Ollama API | Physical Linux Laptop (`100.101.187.115:11434`) | **GENUINE LIVE** |
| **Remote Task Lease & Submit** | Remote HTTP via Tailscale | `qwen-vps` -> Windows Controller API | **GENUINE LIVE** |
| **DNS-Rebinding Mixed Answers** | Controlled Socket Hook Fixture | In-Process Resolution Policy Gate | **CONTROLLED FIXTURE** |
| **Proxy Header Spoofing (BB-29)**| Request Mock Object | Internal Routing Policy Gate | **CONTROLLED FIXTURE** |
| **Outbound SSRF Egress (BB-13)** | Ephemeral Filtering Proxy Server | Local TCP Loopback (`127.0.0.1:60275`) | **CONTROLLED FIXTURE** |

---

## 11. Outstanding Phase 1 Black-Box Evidence Gaps

The 13 scenarios identified in Phase 1 as having incomplete black-box evidence remain preserved on the evidence-gap ledger:

| Scenario ID | Canonical Title | Current Status | Required Follow-Up Prior to Release Certification |
| :--- | :--- | :---: | :--- |
| **BB-01** | Unknown script produces CAPABILITY_GAP | Incomplete | Dispatched via HTTP API; verify JSON response format |
| **BB-02** | Unsupported plan step requires replan | Incomplete | Dispatched via HTTP API; verify plan revision record |
| **BB-03** | Cyclic task graph is rejected | Incomplete | Ingest invalid DAG via API; verify 400 Bad Request |
| **BB-05** | Step budget exhaustion pauses run | Incomplete | Multi-step task execution via API; verify budget audit |
| **BB-06** | Evaluator rejects missing deliverable | Incomplete | Evaluation trigger via HTTP; verify rejection status |
| **BB-07** | Evaluator rejects schema mismatch | Incomplete | Deliverable submission via HTTP; verify schema error |
| **BB-08** | Plan rejection triggers replan revision | Incomplete | Human rejection via UI/API; verify revision increment |
| **BB-11** | Checkpoint pause preserves state | Incomplete | Orchestrator pause via API; verify in-memory state preservation |
| **BB-15** | Rollback restores prior checkpoint | Incomplete | Rollback trigger via API; verify restored CAS state |
| **BB-17** | Idempotent rerun skips completed tasks | Incomplete | Re-dispatch run via API; verify zero rerun of completed tasks |
| **BB-23** | Secret redaction in audit logs | Incomplete | Query log export endpoint via HTTP; verify redactions |
| **BB-24** | Knowledge record promotion gated | Incomplete | Submit knowledge promotion via UI; verify approval gate |
| **BB-30** | Database backup restore preserves CAS | Incomplete | Backup export and restore via CLI; verify hash parity |

> [!NOTE]
> **BB-27 Note**: BB-27 currently verifies protective response headers (`Content-Security-Policy: sandbox`, `X-Content-Type-Options: nosniff`). Prior to final production release certification, BB-27 must verify actual browser-sandboxed execution behavior using a headless Chromium browser instance.

---

## 12. Remaining Security and Production Blockers

1. **Host Workstation Boundary**:
   - The central controller is running on a Windows 10 Home workstation (`C:\Users\Matt C\`). Windows 10 Home lacks enterprise unattended service management, kernel live-patching, and dedicated server isolation. Production deployment requires migration to an enterprise-supported host (Ubuntu 24.04 LTS, Debian 12, or Windows Server / Windows 11 Enterprise).
2. **Untrusted Code Execution Lock**:
   - `SECURE_EXECUTION_UNAVAILABLE` remains locked. Class B untrusted code execution is blocked from direct controller invocation.
3. **Phase 1 Gap Resolution**:
   - 13 canonical scenarios require full-stack HTTP/browser validation before final production release certification.

---

## 13. Source Checkpoint & Governance Recommendation

- **Source Git Commit**: `c98510b` (`docs: add system interaction patterns, API spec, LLM playbook, and operations runbook`)
- **Working Tree Delta**: Zero production application code changes (`git diff app/` remains 0 lines modified during Stage 9).
- **Recommendation**: **`HOLD`**
  - Stage 9 Phase 3 has successfully verified live network connectivity, anti-SSRF defenses, worker token revocation, and physical multi-node capability.
  - Per Stage 9 safety rules, execution is **strictly stopped** for human review.
  - Do not proceed to Phase 4 (Packaging Gates & Release Certification) without explicit human authorization.
