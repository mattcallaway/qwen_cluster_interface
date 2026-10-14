# Qwen Collective — VPS Agent Protocol Specification

## 1. Overview & Communication Philosophy

The VPS agent protocol provides a resilient, secure, pull-based communication channel between remote execution workers (such as the VPS) and the central Windows Controller.

```mermaid
sequenceDiagram
    autonumber
    participant Worker as VPS Worker Agent
    participant Controller as Windows Controller
    participant DB as SQLite DB
    participant SCM as Human / Operator

    Worker->>Controller: POST /api/worker/v1/register (Node Specs & Declared Caps)
    Controller->>DB: Store / Update Node Record
    Controller-->>Worker: 200 OK (Assigned Worker Session & Secret)

    loop Periodic Heartbeat & Health (Every 30s)
        Worker->>Controller: POST /api/worker/v1/heartbeat (Resource Telemetry)
        Controller-->>Worker: 200 OK (Pending Tasks Flag: true/false)
    end

    opt Authorized Task Available
        Worker->>Controller: POST /api/worker/v1/lease-task (Capability Match)
        Controller->>DB: Check Lease & Capability Verification
        Controller-->>Worker: 200 OK (Task Payload, Idempotency Key, Limits)
        
        Worker->>Worker: Execute in Sandboxed Environment
        
        Worker->>Controller: POST /api/worker/v1/complete-task (Artifacts, Logs, Exit Code)
        Controller->>DB: Store Execution Record & Verify Evidence
        Controller-->>Worker: 200 OK (Task Acknowledged)
    end
```

---

## 2. Security Invariants

1. **Outbound Pull Architecture:**
   * Workers initiate all connections outbound over Tailscale WireGuard mesh networking.
   * No listening ports need to be exposed on the public internet.
2. **Mutual Identity Authentication:**
   * Workers authenticate with a dedicated, high-entropy token (`QC_AUTH_TOKEN`, >= 32 characters).
   * Constant-time comparison prevents timing analysis attacks.
3. **Leasing & Expiration Guards:**
   * Work is leased with a finite lease duration (e.g. 5 minutes).
   * If a worker crashes or drops connection, the controller lease expires and the task is safely recovered without duplicate execution.
4. **Idempotency Keys:**
   * Every job dispatch contains an idempotency key. Duplicate submissions or reconnections will not re-execute side effects.

---

## 3. Protocol Message Endpoints (Planned for Milestone 2)

* `POST /api/worker/v1/register`: Advertises hardware specs, OS version, runtimes, and declared capabilities.
* `POST /api/worker/v1/heartbeat`: Reports CPU, RAM, disk, active jobs, and connection state.
* `POST /api/worker/v1/lease-task`: Atomically claims an eligible task matching verified capabilities.
* `POST /api/worker/v1/heartbeat-task`: Renews task lease during long-running operations.
* `POST /api/worker/v1/complete-task`: Submits stdout/stderr references, deliverables, measurements, and exit codes.

---

## 4. Status

* **Status:** Architectural specification complete. Milestone 1 establishes the controller registry; Milestone 2 implements the protocol agent.
