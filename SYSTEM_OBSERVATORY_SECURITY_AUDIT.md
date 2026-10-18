# Qwen Collective — System Observatory Security Audit
**Security Architecture, Threat Model, Authentication Controls & Data Protection**

## 1. Threat Model & Security Posture

The System Observatory handles cluster health metrics, system performance telemetry, and model execution accounting across distributed nodes. The security audit evaluated ten core threat categories:

| Threat Category | Potential Risk | Mitigation & Enforcement in System Observatory | Status |
|---|---|---|---|
| **1. Unauthenticated Telemetry Ingestion** | Attacker floods telemetry endpoints to corrupt monitoring state or fill disk. | All worker submission endpoints (`/api/v1/worker/telemetry`, `/api/v1/worker/heartbeat`) require cryptographic Bearer token validation via `get_authenticated_worker`. | **ENFORCED** |
| **2. Node Spoofing & Impersonation** | Compromised worker reports falsified metrics or degrades status of other nodes. | Strict identity binding: the controller verifies that the `node_id` in the telemetry payload matches the cryptographically verified `node_id` bound to the Bearer token. Any mismatch triggers HTTP 403 Forbidden. | **ENFORCED** |
| **3. Private Prompt & Data Leakage** | Telemetry logs raw model prompts or proprietary project data. | Inference telemetry strictly logs numerical metadata (`prompt_tokens`, `gen_tokens`, `eval_duration_ms`, `tokens_per_second`). Raw prompts and generated texts are NEVER stored in telemetry tables. | **ENFORCED** |
| **4. Secret & Credential Exposure** | Sensitive environment variables or keys leak into process monitoring. | Process monitoring is restricted to numerical memory RSS and CPU utilization. Command-line arguments, environment blocks, and credential tokens are excluded from collector payloads. | **ENFORCED** |
| **5. Remote Shell & Arbitrary File Reads** | Monitoring probes trigger unauthorized command execution on nodes. | Telemetry collectors strictly use non-destructive read-only OS interfaces (`/proc/stat`, `/proc/meminfo`, `GlobalMemoryStatusEx`, `shutil.disk_usage`). No remote shell execution is permitted via metrics endpoints. | **ENFORCED** |
| **6. Public Port Exposure** | New listening ports opened on worker machines increasing network attack surface. | Architecture is strictly outbound push over established Tailscale/HTTP channels. Zero listening ports are opened on Linux VPS or Linux laptop nodes. | **ENFORCED** |
| **7. Denial of Service via Large Payloads** | Massive JSON payloads cause memory exhaustion on controller. | Payload bounds enforced via Pydantic model validation (`TelemetrySamplePayload`); bounded string lengths and capped list sizes. | **ENFORCED** |
| **8. Administrative CSRF on Controls** | Cross-site request forgery acknowledges or alters alerts. | State-changing admin endpoints enforce active admin session authentication and CSRF token validation. | **ENFORCED** |
| **9. Database Query Injection** | Malicious node IDs or filters inject SQL syntax into time-series queries. | All database interactions utilize parameterized SQLite queries (`?` parameter markers) without raw SQL string formatting. | **ENFORCED** |
| **10. Alert Denial / Starvation** | Flapping metrics cause continuous alert spam or resource exhaustion. | Hysteresis duration requirements and cooldown periods prevent alert flapping and suppress duplicate notifications. | **ENFORCED** |

---

## 2. Authentication & Authorization Verification

### 2.1 Token Verification Workflow
```text
[ Worker HTTP Request ]
       |
       v
Header: "Authorization: Bearer qcw_..."
       |
       v
`get_authenticated_worker` Dependency
  - Computes SHA256 of incoming raw token
  - Queries `worker_auth_tokens` table for active, unrevoked token
  - Extracts verified `node_id`
       |
       +---> If invalid: HTTP 401 Unauthorized
       |
       v
`submit_worker_telemetry` Endpoint
  - Compares `worker["node_id"]` with payload `req.node_id`
       |
       +---> If mismatch: HTTP 403 Forbidden (Node Spoofing Rejected)
       |
       v
Safe Ingestion into `telemetry_samples`
```

### 2.2 Automated Test Validation
In `tests/test_observatory.py`:
- `test_worker_telemetry_authentication_and_spoofing` verifies:
  1. Requests without Bearer tokens return HTTP 401 Unauthorized.
  2. Tokens for Node A attempting to report metrics for Node B return HTTP 403 Forbidden with `"cannot report telemetry"`.
  3. Legitimate requests from authenticated nodes are accepted and verified in the database.

---

## 3. Data Privacy & Confidentiality Audit

1. **Inference Telemetry (`inference_telemetry`)**:
   - Stores: `request_id`, `node_id`, `model`, `prompt_tokens`, `gen_tokens`, `durations`, `tokens_per_second`.
   - **Does NOT store**: User prompts, system prompts, model completions, API keys, or conversation histories.
2. **Hardware Telemetry (`telemetry_samples`)**:
   - Stores: CPU %, RAM MB, Swap MB, Disk GB, Temperatures, Battery %, Process RSS.
   - **Does NOT store**: File paths, directory listings, user session tokens, browsing activity, or command arguments.
3. **CSV Export (`/api/v1/observatory/export`)**:
   - Generated CSV exports contain only aggregated operational metrics. Zero credentials or secret tokens are present in exported data.

---

## 4. Operational Resilience & Denial-of-Service Defense

1. **Bounded Timeouts**: All hardware collection probes (e.g. `nvidia-smi`, `GlobalMemoryStatusEx`, file reads) have strict 1.5–2.0 second timeouts.
2. **Worker Non-Interference**: Telemetry push runs as a low-priority background operation. If the controller is temporarily unreachable, the worker continues executing tasks normally and does not block task leasing.
3. **Database Write Budgeting**:
   - Telemetry rollups incrementally consolidate 1-minute and 1-hour records.
   - Scheduled retention pruning purges raw samples older than 7 days, maintaining bounded SQLite storage size.

---

## 5. Security Audit Conclusion

The System Observatory implementation satisfies all distributed systems security requirements:
- **Authentication**: Strict Bearer token validation with node-specific identity binding.
- **Isolation**: Outbound-only push; zero public listening ports.
- **Privacy**: Strict exclusion of private prompts and command-line secrets.
- **Integrity**: Parameterized SQL queries; append-only immutable execution ledgers.
