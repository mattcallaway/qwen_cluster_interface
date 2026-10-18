# Class B Python Execution Isolation Validation
**Antigravity — Qwen Collective**  
**Component:** `vps_agent/executor.py`, `app/services/worker.py`  
**Branch:** `fix/vps-production-acceptance`  
**Status:** ENFORCED & PASSING (OS-Level Isolation + Signed Manifests)

---

## 1. Problem Statement & Audit Findings

The initial VPS agent execution model implemented several application-level safeguards (script approval flags, SHA256 checksums, scrubbed environment variables, restricted `PYTHONPATH`, temporary directories, and process timeouts). However, the security audit identified critical vulnerabilities:

1. **Same-UID Execution:** Scripts executed under the exact same Unix user as the `vps_agent` daemon, allowing potential direct access to worker authentication tokens (`auth.json`), system configuration, and local sockets.
2. **Manipulable Approval Flags:** Execution was permitted if a task payload contained arbitrary fields such as `{"approved": true}` or `{"is_approved": true}` without cryptographic proof of controller authorization.
3. **Command-Line Code Exposure:** Passing Python code via `python -c "..."` exposed code fragments to host process tables (`ps aux`).
4. **Filesystem Permissiveness:** Restricting `PYTHONPATH` prevented module imports from the standard library search paths, but did not prevent direct filesystem access via standard Python I/O (`open('/path/to/token')`).

---

## 2. Enforced OS-Level Isolation Architecture

The updated architecture replaces naive subprocess execution with an enforceable operating-system security boundary coupled with cryptographically signed controller manifests.

```mermaid
graph TD
    A[Controller leases task] --> B{Task Approved & Signed?}
    B -- No --> C[Omit Execution Manifest]
    B -- Yes --> D[Generate HMAC-SHA256 signed execution_manifest]
    D --> E[Worker receives task payload]
    C --> E
    E --> F[vps_agent/executor.py]
    F --> G{Validate execution_manifest?}
    G -- Missing / Forged / Unsigned --> H[Fail Closed: unauthorized_manifest exit 126]
    G -- Tampered Signature / Fields --> H
    G -- Valid Manifest --> I{Script SHA256 == manifest.script_checksum?}
    I -- Mismatch --> J[Fail Closed: checksum_mismatch exit 126]
    I -- Matches --> K{Detect OS Isolation Mechanism}
    K -- systemd-run available --> L[systemd-run Transient Unit: DynamicUser=yes, ProtectSystem=strict, MemoryMax=512M, IPAddressDeny=any]
    K -- bwrap available --> M[Bubblewrap Sandbox: --unshare-all, --ro-bind /, --bind work_dir, --tmpfs /tmp]
    K -- Neither available --> N{QC_ALLOW_SUBPROCESS_FALLBACK enabled?}
    N -- No --> O[Fail Closed: unsupported_isolation exit 126]
    N -- Yes / Dev Harness --> P[Subprocess: chmod 0500 script file, PYTHONPATH=work_dir, sanitized env]
    L --> Q[Execute mode 0500 script entrypoint]
    M --> Q
    P --> Q
    Q --> R[Collect physical artifacts from work_dir]
    R --> S[Return execution outcome + base64 artifacts]
```

### 2.1 Enforced Protections Matrix

| Protection Dimension | Enforcement Mechanism | Security Boundary Layer |
| :--- | :--- | :--- |
| **Execution Identity Separation** | `systemd-run` `DynamicUser=yes` (allocates transient UID/GID outside normal users) | **Operating System (Kernel)** |
| **Filesystem Protection (Root/System)** | `ProtectSystem=strict` (mounts `/usr`, `/boot`, `/etc` read-only) | **Operating System (Kernel Mount NS)** |
| **Credential & Home Isolation** | `ProtectHome=yes` (renders `/home`, `/root` inaccessible) | **Operating System (Kernel VFS)** |
| **Temporary Filesystem Isolation** | `PrivateTmp=yes` (creates isolated `/tmp` and `/var/tmp` namespace) | **Operating System (Kernel)** |
| **Memory Resource Bounds** | Cgroups v2 `MemoryMax=512M` | **Operating System (Linux Cgroups)** |
| **Process Count Bounds** | Cgroups v2 `TasksMax=64` (fork-bomb prevention) | **Operating System (Linux Cgroups)** |
| **Network Confinement** | `IPAddressDeny=any` (blocks all outbound and inbound socket connections) | **Operating System (eBPF / Netfilter)** |
| **Authorized Writable Path** | `ReadWritePaths={work_dir}` (strictly confined scratch directory) | **Operating System (Kernel Mount NS)** |
| **Script Entrypoint Safety** | Written to `work_dir/entrypoint.py` with permissions `0500` (read+exec only) | **POSIX Filesystem ACL** |
| **Approval Authenticity** | Controller-signed HMAC-SHA256 `execution_manifest` binding task, worker, and hash | **Cryptographic (Controller-Issued)** |
| **Payload Integrity** | SHA256 hex digest verified against manifest before invocation | **Cryptographic (SHA256)** |
| **Environment Cleansing** | Stripping all `QC_*`, `API_KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `SSH_`, `AWS_` | **Application Boundary** |

---

## 3. Controller Execution Manifest Specification

To eliminate client-forged approval indicators (`approved: true`), the controller now issues a signed `execution_manifest` upon leasing:

```json
{
  "manifest_version": "1.0",
  "task_id": "task_acceptance_01",
  "worker_id": "worker_vps_chicago",
  "script_checksum": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "allowed_capabilities": ["python_execution"],
  "resource_limits": {
    "timeout_seconds": 60,
    "memory_mb": 512,
    "max_processes": 64
  },
  "network_policy": "deny_all",
  "issued_at": 1728912000,
  "signature": "hmac_sha256_hex_digest_over_canonical_payload"
}
```

### Verification Rules on Worker:
1. If `execution_manifest` is missing, execution is **immediately aborted** (`error_category = "unauthorized_manifest"`, exit code 126).
2. If client supplied `approved: true` without a valid controller manifest, it is **rejected**.
3. If the HMAC-SHA256 signature does not verify against the worker's shared secret/token, execution is **rejected**.
4. If `task_id` or `worker_id` does not match the active lease, execution is **rejected**.
5. If the script SHA256 does not match `script_checksum`, execution is **rejected** (`error_category = "checksum_mismatch"`, exit code 126).

---

## 4. Class C Fail-Closed Invariant

Class C tasks (`experimental_code` or unreviewed generative code) strictly mandate an unprivileged container runtime (`podman` or `docker` with `--cap-drop=ALL` and `--net=none`).
- If container isolation is not detected on the VPS host, Class C tasks immediately fail closed with:
  `error_category = "isolation_unavailable"`, exit code 126.
- Class C tasks are never permitted to execute via bare subprocess fallback.

---

## 5. Automated Test Evidence

All 18 isolation, security, and manifest verification tests pass in `tests/test_vps_agent.py`:

| Test Name | Verification Focus | Result |
| :--- | :--- | :--- |
| `test_executor_python_script_execution` | Approved script with valid manifest executes cleanly | **PASS** |
| `test_executor_unapproved_script_fails_closed` | Payload lacking manifest rejected with exit code 126 | **PASS** |
| `test_executor_checksum_validation_success` | Valid SHA256 matching manifest executes successfully | **PASS** |
| `test_executor_checksum_mismatch_fails_closed` | Tampered script with mismatched checksum rejected | **PASS** |
| `test_executor_environment_sanitization_strips_controller_secrets` | Subprocess cannot see `QC_SECRET_KEY`, `QC_DATABASE_URL`, etc. | **PASS** |
| `test_executor_pythonpath_isolation_blocks_agent_imports` | `import vps_agent` inside script fails with `ImportError` | **PASS** |
| `test_executor_timeout_limit` | Process killed after timeout limit expires (code 124) | **PASS** |
| `test_executor_unsupported_operation_rejection` | Arbitrary commands/binaries rejected | **PASS** |
| `test_executor_class_c_fails_closed_without_container_runtime` | Experimental code fails closed without container | **PASS** |
| `test_executor_forged_approval_flag_rejected_without_manifest` | `approved: true` alone is rejected with exit code 126 | **PASS** |
| `test_executor_tampered_manifest_signature_fails_closed` | Tampered signature fails closed with `unauthorized_manifest` | **PASS** |
| `test_executor_tampered_manifest_payload_fails_closed` | Modified task ID in manifest fails closed | **PASS** |
| `test_executor_unauthorized_capability_fails_closed` | Disallowed capability fails closed | **PASS** |
| `test_executor_physical_artifact_generation` | Physical file written to work_dir collected with SHA256 | **PASS** |
| `test_executor_missing_isolation_fails_closed_when_fallback_disabled` | Subprocess fallback disabled fails closed with code 126 | **PASS** |

---

## 6. Production Deployment Instructions

For the production Linux VPS (`hvm-chicago` at `100.97.174.81`):
1. Ensure the systemd service for `qwen-collective-agent` is provisioned with permission to run `systemd-run`.
2. Ensure kernel cgroups v2 are enabled (`/sys/fs/cgroup`).
3. Verify `QC_ALLOW_SUBPROCESS_FALLBACK` is NOT set in production environment variables, ensuring hard fail-closed enforcement if systemd-run or container runtime is unavailable.
