# Project Orchestrator Security Architecture

## 1. Threat Model and Trust Boundaries

The Project Orchestrator coordinates distributed physical nodes, executes automated scripts, and queries local Large Language Models (LLMs). The architecture explicitly identifies four distinct trust boundaries:

```
┌────────────────────────────────────────────────────────┐
│  Untrusted Input: User Prompts & LLM Model Outputs    │
└───────────────────────────┬────────────────────────────┘
                            │ (Injection Filter & Allowlist)
┌───────────────────────────▼────────────────────────────┐
│  Trusted Core: Windows Controller (Engine & DB)        │
└───────────────┬────────────────────────┬───────────────┘
                │ (Mutual Worker Auth)   │ (Strict Evidence Path)
┌───────────────▼──────────────┐ ┌───────▼──────────────┐
│ Semi-Trusted Worker: VPS     │ │ Local Worker: Laptop │
│ (Sandboxed Python Execution) │ │ (Ollama Inference)   │
└──────────────────────────────┘ └──────────────────────┘
```

1. **Untrusted Input**: Natural language objectives, external web datasets, and model generations are treated as untrusted data.
2. **Trusted Core**: The FastAPI Windows controller hosts the state machine, SQLite database, scheduler lock, and artifact metadata.
3. **Semi-Trusted Workers**: Workers (Linux VPS and Linux Laptop) authenticate via per-node cryptographic tokens. They can execute assigned sandboxed tasks but have no direct access to the controller's database or private files.
4. **Local Model Inferences**: Models running on Ollama are non-deterministic and susceptible to prompt injection; their outputs can never be directly evaluated as executable code.

---

## 2. Prompt Injection and Code Execution Defenses

### 2.1 Separation of Code and Data
The orchestrator enforces a strict architectural boundary: **LLMs can never generate code or shell commands for execution.**

- **Action Allowlist**:
  - Plans only support predefined action types: `library_script`, `inference`, and `controller_step`.
  - The `library_script` action only accepts hardcoded script identifiers (e.g. `sys_health_basic`) located in `app/services/orchestration/script_library.py`.
  - The worker sandboxes reject any script submission whose content is not registered and validated on the controller.

### 2.2 Input Filtering and Regex Guardrails
The task graph validator (`app/services/orchestration/task_graph.py`) subjects all plan titles, descriptions, and user objectives to rigorous regex safety scanning:

- Rejects directive hijacking patterns (e.g., `ignore previous instructions`, `system override`, `disregard above`, `developer mode`).
- Rejects dangerous shell syntax (e.g., `rm -rf`, `curl ... | sh`, `wget ... | bash`, `nc -e`, `eval()`, `exec()`).
- Rejects system credential access attempts (e.g., `/etc/shadow`, `id_rsa`, `env`, `powershell -enc`).

### 2.3 Prompt Length Limits and Sandboxing
- LLM inference requests enforce a maximum prompt ceiling of 8,000 characters in `vps_agent/executor.py` to prevent memory exhaustion and buffer overflows in local model runtimes.
- Worker Python script executions are executed in unprivileged sub-processes with captured standard output and hard execution timeouts (default 120 seconds).

---

## 3. Worker Authentication and Lease Integrity

### 3.1 Cryptographic Worker Authentication
- Every physical worker (`infrastructure_nodes`) must authenticate using an unrevoked bearer token stored in `worker_auth_tokens`.
- Tokens are hashed using SHA-256 before verification; raw tokens are never logged or stored in plaintext.
- Revoking a worker token immediately invalidates polling, leasing, and task result submission across all endpoints.

### 3.2 Lease Idempotency and Race Condition Protection
- Tasks are leased using cryptographic lease UUIDs and deterministic idempotency keys (`orch:{task_id}:{attempt}`).
- If a worker crashes or loses network connectivity, the lease expires after 10 minutes.
- When an expired task is rescheduled, the engine updates the attempt counter. Any subsequent result submission from the stale worker lease is rejected by the database transaction.

---

## 4. Path Traversal and Artifact Integrity

### 4.1 Path Traversal Defense
The artifact manager (`app/services/orchestration/artifact_manager.py`) protects the controller file system from malicious path manipulation:

- Filenames are normalized and sanitized using `Path(filename).name` to strip directory components.
- Artifact file paths are resolved to their canonical absolute paths and verified to exist strictly within the authorized `evidence_dir` or `data_dir/orchestrator_artifacts`.
- Traversal payloads containing `../`, `..\\`, null bytes, or URL-encoded path separators are rejected with HTTP 400.

### 4.2 Cryptographic Integrity Verification
- When workers submit generated artifacts, the controller calculates the SHA-256 checksum and compares it against `expected_sha256`.
- Download responses deliver the `X-Artifact-SHA256` header, allowing clients to independently verify deliverable integrity.
- Deliverable downloads are served with `X-Content-Type-Options: nosniff` to prevent browser MIME-type sniffing vulnerabilities.

---

## 5. Web Application Security: Auth, CSRF, and Secrets

### 5.1 Session Authentication
- All orchestrator REST endpoints and UI views require the `qc_session` cookie issued to authenticated administrators.
- Unauthenticated requests are rejected with HTTP 401 or redirected to `/login`.

### 5.2 Strict CSRF Protection
- Mutating endpoints (`POST`, `PUT`, `DELETE`) require the `X-CSRF-Token` header.
- CSRF tokens are cryptographically derived from the session secret and verified via constant-time comparison in `app/auth.py`.
- Requests missing or carrying invalid CSRF tokens are rejected with HTTP 403 Forbidden.

### 5.3 Secret Protection
- The administrative password (`QC_ADMIN_PASSWORD`) and session secret (`QC_SECRET_KEY`) reside exclusively in `C:\Users\Matt C\.qwen-collective\qwen-collective.env`.
- Neither secret is ever exposed in API responses, project metadata, git repositories, or log files.

---

## 6. Resource Budgets and Denial-of-Service Prevention

The orchestrator enforces strict resource limits (`orchestration_resource_budgets`) to prevent runaways:

| Resource | Default Limit | Breach Behavior |
| :--- | :--- | :--- |
| **Max Tasks** | 50 tasks per project | Transitions run to `BLOCKED`; creates `budget` approval. |
| **Max Inference Tokens** | 50,000 tokens | Transitions run to `BLOCKED`; requires human sign-off. |
| **Max Total Runtime** | 3,600 seconds (1 hour) | Halted; marked `FAILED` or `BLOCKED`. |
| **Max Retries Per Task** | 2 retries | Task marked `failed`; run transitions to `BLOCKED`. |
| **Max Wall Clock Time** | 24 hours | Automatic cancellation. |

---

## 7. Auditability and Immutable Event Ledger

- Every state transition, plan revision, task dispatch, worker result, and human approval decision is recorded in `orchestration_events`.
- Events store `event_type`, `run_id`, `state_version`, `actor`, `payload_json`, and UTC timestamps.
- The ledger is append-only, providing a permanent forensic record of project execution.
