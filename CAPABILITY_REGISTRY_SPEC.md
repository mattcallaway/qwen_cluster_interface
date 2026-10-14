# Qwen Collective — Capability Registry Specification

## 1. Overview & Purpose

The **Capability Registry** provides a typed, deterministic inventory of everything each machine in the collective can do. It ensures that planning models (Qwen) and human operators never rely on assumptions about software availability, memory capacity, or tool reliability.

Every capability must have a clear identity, a defined providing machine, input/output schemas, required software, security classifications, approval policies, and an auditable record of discovery and verification.

---

## 2. Capability Schema Definition

```sql
CREATE TABLE IF NOT EXISTS infrastructure_capabilities (
    id TEXT PRIMARY KEY,                       -- e.g. cap_vps_python_exec, cap_ctrl_llm_inference
    node_id TEXT NOT NULL,                     -- Foreign key to infrastructure_nodes(id)
    name TEXT NOT NULL,                        -- e.g. "Isolated Python 3.12 Script Runner"
    description TEXT,                          -- Human and AI-readable capability summary
    category TEXT NOT NULL,                    -- Enum category (see Section 3)
    required_software TEXT,                    -- e.g. "python>=3.10, playwright"
    supported_operations_json TEXT DEFAULT '[]', -- List of supported operations: e.g. ["run_script", "validate_syntax"]
    input_schema_json TEXT DEFAULT '{}',       -- JSON schema of supported input payloads
    output_schema_json TEXT DEFAULT '{}',      -- JSON schema of expected execution outputs
    version TEXT DEFAULT '1.0.0',              -- Capability version
    discovery_status TEXT NOT NULL DEFAULT 'declared', -- declared, discovered, missing
    discovery_method TEXT NOT NULL DEFAULT 'config_declared', -- config_declared, agent_probe, hardware_scan, local_inspection
    discovered_at TEXT,                        -- ISO 8601 timestamp of last physical detection
    verification_status TEXT NOT NULL DEFAULT 'unverified', -- unverified, verified, failed, revoked
    verification_method TEXT DEFAULT 'side_effect_free_probe', -- dry_run_test, side_effect_free_probe, manual_audit, health_check
    last_verified_at TEXT,                     -- ISO 8601 timestamp of last successful verification
    verification_error TEXT,                   -- Error message if last verification attempt failed
    operational_status TEXT NOT NULL DEFAULT 'active', -- active, disabled, degraded, unavailable
    required_permissions_json TEXT DEFAULT '[]', -- Required privileges (e.g. ["net_egress_https", "read_vault"])
    approval_policy TEXT NOT NULL DEFAULT 'always_requires_human_approval', -- always_requires_human_approval, pre_authorized, restricted
    resource_limits_json TEXT DEFAULT '{}',    -- JSON bounds: e.g. {"timeout_sec": 300, "max_memory_mb": 2048}
    security_classification TEXT NOT NULL DEFAULT 'isolated_sandbox', -- trusted_internal, isolated_sandbox, untrusted_external
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (node_id) REFERENCES infrastructure_nodes(id) ON DELETE CASCADE
);
```

---

## 3. Standard Capability Categories

| Category | Typical Providers | Example Operation | Security & Approval Baseline |
| :--- | :--- | :--- | :--- |
| `ai_inference` | Controller, Local GPU Node, RPC compute | Text generation, embedding, reasoning | Pre-authorized local; bounded token budget |
| `web_retrieval` | VPS, Controller | Public URL fetching, markdown conversion | Pre-authorized with SSRF & domain guards |
| `search` | VPS, Controller | SearXNG / Web search aggregation | Pre-authorized with rate limiting |
| `browser_automation` | VPS Worker | Playwright / Chromium headless rendering | Human authorization required |
| `python_execution` | VPS Worker, Controller | Bounded script execution | Human authorization required |
| `approved_shell_execution` | VPS Worker, Linux Node | Controlled tool command (curl, git) | Human authorization required |
| `file_processing` | Controller, VPS | Data parsing, CSV transformation | Bounded sandbox |
| `document_extraction` | VPS Worker | PDF text extraction, OCR | Bounded sandbox |
| `data_transformation` | Controller, VPS | JSON formatting, schema validation | Pre-authorized deterministic |
| `api_integration` | VPS Worker | External REST service webhooks | Human authorization required |
| `scheduled_execution` | VPS Worker | Systemd timer recurring jobs | Human authorization required |
| `persistent_service` | VPS Worker | Long-running monitoring daemon | Human authorization required |
| `monitoring` | VPS, Controller | Health telemetry, resource sampling | Pre-authorized background probe |
| `software_deployment` | Controller -> VPS | Package installation, service rollout | Strict human authorization required |
| `reporting` | Controller | Retrospective & metric reports | Pre-authorized internal |

---

## 4. Verification Policies & Side-Effect Free Probes

1. **No Side Effects:** Capability verification tests must be strictly read-only or self-cleaning in memory.
   * *Example:* Python verification executes `python --version` and syntax check of an empty function `def test(): pass`, never writing to arbitrary disk locations.
   * *Example:* Web retrieval verification performs a loopback ping or hits a known innocuous localhost health endpoint.
2. **Deterministic Status Transitions:**
   * If a verification probe returns exit code 0 and valid output: `verification_status` becomes `verified`.
   * If a verification probe times out or raises an error: `verification_status` becomes `failed`, with `verification_error` recorded and an entry created in `infrastructure_reconciliations`.
3. **Immutability of Audit Trails:**
   * Every verification execution logs a row in `infrastructure_verifications` recording capability ID, node ID, test name, result status, stdout/stderr summary, and timestamps.

---

## 5. Specification Status

* **Status:** Implemented in Milestone 1. Verified via automated database and route test suites.
