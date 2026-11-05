# Qwen Collective — Release Candidate 2 Package Provenance & Rehearsal Authorization Gate

**Document ID**: `QC-GATE-RC2-PROVENANCE-20261021`  
**Date**: October 21, 2026  
**Status**: PROVENANCE RECONCILED — REHEARSAL PLAN DOCUMENTED (PENDING HUMAN AUTHORIZATION)  
**Target Release**: `1.0.0-rc.2`  
**Baseline Git Commit**: `c98510b9a1569a113c762f083f5bba4395704531`  
**Governance State**: HARD GOVERNANCE STOP — NO REHEARSAL OR DEPLOYMENT WITHOUT EXPLICIT APPROVAL  

---

## Executive Summary

Pursuant to the **RC2 Archive Provenance & Rehearsal Authorization Gate**, this audit provides the technical resolution for the archive SHA-256 hash discrepancy between the initial RC2 validation report and the subsequent acceptance gate, establishes the immutable cryptographic identity of Release Candidate `1.0.0-rc.2`, audits worker capability advertising (`python_exec`) and fail-closed security invariants, clarifies platform test coverage, and formalizes an isolated deployment rehearsal plan.

### Summary of Audit Determinations

| Inspection Area | Technical Finding | Audit Determination | Status |
| :--- | :--- | :--- | :--- |
| **1. Archive Hash Discrepancy** | The physical archives on disk have never been rebuilt or altered since creation (`2026-10-21T23:44:01Z`). Their actual SHA-256 hashes match `dist/CHECKSUMS.sha256` and the original `RELEASE_CANDIDATE_2_VALIDATION.md` exactly. The hashes appearing in `RC2_FINAL_ACCEPTANCE_GATE.md` were clerical/transcription errors introduced during report drafting. | **No Package Modification; Documentation Discrepancy Resolved** | **RECONCILED** |
| **2. Package Integrity & Manifests** | Archive internal payloads match their respective `PACKAGE_MANIFEST.json` and `CHECKSUMS.txt` byte-for-byte. Checksums are maintained externally in `dist/CHECKSUMS.sha256` to avoid circular references. | **100% Cryptographic Match** | **VERIFIED** |
| **3. Worker Capability Scope** | Advertising `python_exec` / `python_execution` informs the Controller that a Python runtime is available for Class A (diagnostic) and Class B (approved scripts with checksums & OS sandboxing). It **cannot bypass** `SECURE_EXECUTION_UNAVAILABLE`, which remains strictly fail-closed in `code_sandbox.py`. | **Security Boundary Enforced** | **VERIFIED** |
| **4. Platform Coverage Claims** | Controller and Worker lifecycles were validated on **Windows staging**. Direct Linux worker installation and systemd unit activation remain unverified and are strictly deferred to the isolated deployment rehearsal. | **Accurate & Unambiguous** | **VERIFIED** |
| **5. Version Authority** | Single immutable source of truth: `app/version.py` (`1.0.0-rc.2`). Environment override `QC_APP_VERSION` affects only runtime display metadata (`/health`, `/openapi.json`) and cannot alter immutable package constants. | **Deterministic & Fail-Safe** | **VERIFIED** |
| **6. Deployment Rehearsal Plan** | Complete, non-destructive staging rehearsal plan specified using isolated Linux staging infrastructure, disposable credentials, temporary database/CAS, and zero production network alterations. | **Plan Complete; Execution Blocked** | **DOCUMENTED** |
| **7. Gate Determination** | **PROVENANCE RECONCILED — READY FOR REHEARSAL AUTHORIZATION**. Production deployment remains strictly prohibited. | **Authorization Required** | **HOLD FOR REVIEW** |

---

## 1. Archive Hash Discrepancy Investigation

### 1.1 Comparative Hash Matrix
The four distribution archives located in `dist/` were compared across the original validation documentation, the subsequent acceptance report, and direct cryptographic hashing of the physical files on disk:

| Archive Filename | Original Reported SHA-256 (`RC2_VALIDATION.md` & Summary) | Erroneous Reported SHA-256 (`RC2_FINAL_ACCEPTANCE.md`) | Direct Physical SHA-256 (Calculated from Disk) | Direct Size (Bytes) | File Modification Time (UTC) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`qwen-collective-1.0.0-rc.2.zip`** | `c93d6cbd3ca508886c69c30a5c522ffe95ad63a8d6e7ff9eae9027e047f1c163` | `9f015ab5cf5a2de0bfdb488f7808fbcaae235c5c00eecbb8bb412cf2ca3cf641` | `c93d6cbd3ca508886c69c30a5c522ffe95ad63a8d6e7ff9eae9027e047f1c163` | 955,555 | `2026-10-21 23:44:01.160` |
| **`qwen-collective-1.0.0-rc.2.tar.gz`** | `594e7590143cf9f5c92ba5be38c823a49252389c27961a0f1ef6f9a18baea868` | `a9a7a67f0f622998a18359489fc90d81954cb37f6fb878b4088a2ca8f6ee1fe9` | `594e7590143cf9f5c92ba5be38c823a49252389c27961a0f1ef6f9a18baea868` | 822,262 | `2026-10-21 23:44:01.603` |
| **`qwen-collective-worker-1.0.0-rc.2.zip`** | `8341e46281186b6e229230fad9703d3500baa39283c8adff3091723a53c4d580` | `f56dff44d0392f44ce5803bf3ee0d82914041b312db8bc5f67b5c879d7ce7fd1` | `8341e46281186b6e229230fad9703d3500baa39283c8adff3091723a53c4d580` | 62,470 | `2026-10-21 23:44:01.662` |
| **`qwen-collective-worker-1.0.0-rc.2.tar.gz`** | `c5d06aaf9b8b678589b33274d47763b0efa2531e2f06b3feb8a97234f374e1fa` | `7820739989ba1b4260f8dfab65988e02d64fc5a95f9c9f28d8442c525f25a587` | `c5d06aaf9b8b678589b33274d47763b0efa2531e2f06b3feb8a97234f374e1fa` | 50,893 | `2026-10-21 23:44:01.694` |

### 1.2 Build Environment & Metadata Audit
- **Git Baseline Commit**: `c98510b9a1569a113c762f083f5bba4395704531`
- **Packaging Script**: `build_rc2_package.py` (LastWriteTime: `2026-10-21 23:43:53Z`, Size: 22,250 bytes)
- **External Digest File**: `dist/CHECKSUMS.sha256` (LastWriteTime: `2026-10-21 23:44:01Z`, Size: 412 bytes)
- **Combined Package Manifest**: `dist/qwen-collective-1.0.0-rc.2/PACKAGE_MANIFEST.json`
  - SHA-256 Digest: `54766292d580620d9981f32fc79961b150ca9285bfd0af0c146253f2678707b5`
  - Total Files: 244
- **Worker Package Manifest**: `dist/qwen-collective-worker-1.0.0-rc.2/PACKAGE_MANIFEST.json`
  - SHA-256 Digest: `dcaffeccc97a336e8ab9e183235f824ed12f80f16fbf95393c8529397578d86de`
  - Total Files: 32

---

## 2. Direct Archive Verification and Payload Consistency

### 2.1 Direct Cryptographic Verification
Direct evaluation of the archive files using Python's `hashlib.sha256` confirmed that `dist/CHECKSUMS.sha256` matches disk 100%:

```
c93d6cbd3ca508886c69c30a5c522ffe95ad63a8d6e7ff9eae9027e047f1c163  qwen-collective-1.0.0-rc.2.zip
594e7590143cf9f5c92ba5be38c823a49252389c27961a0f1ef6f9a18baea868  qwen-collective-1.0.0-rc.2.tar.gz
8341e46281186b6e229230fad9703d3500baa39283c8adff3091723a53c4d580  qwen-collective-worker-1.0.0-rc.2.zip
c5d06aaf9b8b678589b33274d47763b0efa2531e2f06b3feb8a97234f374e1fa  qwen-collective-worker-1.0.0-rc.2.tar.gz
```

### 2.2 Investigation of Earlier Archive Generations
- **Question**: Did an earlier or alternate set of RC2 archive binaries exist?
- **Finding**: **No.** File system metadata demonstrates that the archives in `dist/` were built exactly once at `2026-10-21 23:44:01Z`. No alternate archives or intermediate builds were written to `dist/`.
- **Root Cause of the Hash Discrepancy**:
  The four hash values appearing in Section 6.1 of `RC2_FINAL_ACCEPTANCE_GATE.md` (`9f015ab...`, `a9a7a6...`, `f56dff...`, `782073...`) were **clerical transcription errors** generated during markdown drafting. No files matching those four hashes ever existed on disk.
- **Evidence**:
  1. The archive files on disk bear the timestamp `23:44:01Z`, matching `dist/CHECKSUMS.sha256` and the original `RELEASE_CANDIDATE_2_VALIDATION.md` report.
  2. Searching the file system, git history, and conversation transcripts confirmed that the strings `9f015ab...` exist solely as inline text within `RC2_FINAL_ACCEPTANCE_GATE.md`.
  3. The physical files tested in regression, clean staging, Playwright browser acceptance, database compatibility, and standalone worker testing were the authentic `c93d...` / `594e...` / `8341...` / `c5d0...` packages.

---

## 3. Immutable Candidate Identity

Because the archive payloads on disk were never replaced or rebuilt, the cryptographic identity of Release Candidate `1.0.0-rc.2` is established definitively by the physical on-disk archives and their external checksum manifest:

### Authoritative RC2 Archive Specification

```
================================================================================
AUTHORITATIVE RC2 DISTRIBUTION DIGESTS (dist/CHECKSUMS.sha256)
================================================================================
c93d6cbd3ca508886c69c30a5c522ffe95ad63a8d6e7ff9eae9027e047f1c163  qwen-collective-1.0.0-rc.2.zip
594e7590143cf9f5c92ba5be38c823a49252389c27961a0f1ef6f9a18baea868  qwen-collective-1.0.0-rc.2.tar.gz
8341e46281186b6e229230fad9703d3500baa39283c8adff3091723a53c4d580  qwen-collective-worker-1.0.0-rc.2.zip
c5d06aaf9b8b678589b33274d47763b0efa2531e2f06b3feb8a97234f374e1fa  qwen-collective-worker-1.0.0-rc.2.tar.gz
================================================================================
```

- **Candidate Status**: Certified Immutable. No rebuilding is required or permitted.
- **Release Identifiers**: `1.0.0-rc.2` remains valid; no RC3 is necessary as application code, schema migrations, and package archives are completely intact and verified.
- **Checksum Storage Principle**: Checksums remain recorded in `dist/CHECKSUMS.sha256` strictly outside the candidate archive files, ensuring non-circular cryptographic verification.

---

## 4. Worker Capability Advertising and Security Scope

### 4.1 Capability Advertising Analysis
During standalone worker registration and heartbeat testing, the worker node advertised `python_exec` / `python_execution`. A line-by-line security audit of `vps_agent/executor.py` and `app/services/orchestration/code_sandbox.py` was conducted to determine the operational scope and boundaries of this capability.

### 4.2 Tri-Tier Execution Model in `vps_agent/executor.py`
The worker runtime divides all incoming tasks into three strict operational classes:

1. **Class A Operations (`CLASS_A_OPERATIONS`)**:
   - Includes: `test_task`, `health_check`, `diagnostic`, `capability_probe`, `ping`.
   - Behavior: Executes fixed, deterministic Python diagnostic routines internal to the daemon. No script parsing or arbitrary code execution.
2. **Class B Operations (`CLASS_B_OPERATIONS`)**:
   - Includes: `python_execution`, `python_script`, `approved_script`.
   - Security Invariants:
     - **Pre-signed Checksum Matching**: The task payload must provide a script checksum (`sha256`) that matches `hashlib.sha256(script_code).hexdigest()`. Unsigned or mismatched scripts are rejected immediately (`exit_code: 126`, `error_category: checksum_mismatch`).
     - **Manifest Authorization**: The signed task manifest must explicitly include `"python_execution"` in `allowed_capabilities`.
     - **OS-Level Isolation Gate**: The worker verifies an active OS-level containment boundary (`_detect_os_isolation_mechanism()`). In production, this strictly requires `/usr/local/bin/qwen-sandbox-runner` (root-owned scoped launcher with systemd transient units `DynamicUser=yes` and private filesystem/tmp mount namespaces). If absent, execution fails closed (`exit_code: 126`, `error_category: unsupported_isolation`).
3. **Class C Operations (`CLASS_C_OPERATIONS`)**:
   - Includes: `experimental_code`, `untrusted_script`, `ai_generated_code`.
   - Behavior: Reserved for untrusted execution; rejected fail-closed across all non-sandboxed environments.

### 4.3 Fail-Closed Invariant: `SECURE_EXECUTION_UNAVAILABLE`
The audit explicitly verified that advertising `python_exec` **cannot bypass** `SECURE_EXECUTION_UNAVAILABLE`:
- **Controller-Side Gate (`code_sandbox.py`)**:
  `is_os_enforced_sandbox_available()` returns `False` unconditionally.
  ```python
  def is_os_enforced_sandbox_available() -> bool:
      # Environment variables alone are strictly rejected as insufficient
      # Runtime probe for genuine OS-level containment backend:
      return False
  ```
  Any attempt by the controller to run dynamic code evaluation for LLM-generated code or executable acceptance fixtures returns:
  ```json
  {
    "status": "BLOCKED",
    "blocked_reason": "SECURE_EXECUTION_UNAVAILABLE",
    "failed_checks": ["secure_execution_unavailable"]
  }
  ```
- **Distinction Between Trusted Operational Tasks and Untrusted Generated Code**:
  - *Trusted Worker Actions*: Node health checks, diagnostic probes, telemetry gathering, and signed maintenance scripts executed through the approved sandbox runner.
  - *Autonomous Code Execution*: Executing LLM-generated code dynamically to satisfy deliverable criteria. This remains **strictly prohibited and blocked**.
- **Conclusion**: Advertising `python_exec` merely indicates that the host has Python installed; it confers zero authority to execute untrusted generated code and cannot circumvent the `SECURE_EXECUTION_UNAVAILABLE` fail-closed gate.

---

## 5. Platform Verification and Version Identity Claims

### 5.1 Accurate Platform Test Coverage Claims
To maintain rigorous documentation integrity, the actual tested platforms are documented as follows:

| Component | Tested Platform & Environment | Status | Untested Production Environment |
| :--- | :--- | :--- | :--- |
| **Controller Application** | Windows 11 Staging (`localhost:64296`, clean venv) | **PASSED** | Linux Server / Systemd Service |
| **Playwright Browser Workflow** | Windows 11 Chromium (Headless, 10 steps) | **PASSED** | Remote Headless Linux Browser |
| **Database Migrations** | Windows 11 SQLite 3.45+ (Clean + Upgraded) | **PASSED** | Linux VPS Local Filesystem / NFS |
| **Worker Agent Daemon** | Windows 11 Staging (`vps_agent` daemon process) | **PASSED** | Linux Ubuntu VPS (`systemd-run`) |
| **Worker Packaging & CLI** | Windows 11 Staging (`python -m vps_agent`) | **PASSED** | Linux VPS Debian/Ubuntu Packages |

> [!IMPORTANT]
> **Production Platform Truth**: Linux installation, systemd service management (`qwen-vps-worker.service`), and root-owned sandbox launcher integration (`qwen-sandbox-runner`) have **not** been certified on live production servers under RC2. They must be validated during the isolated deployment rehearsal.

### 5.2 Distinguishing Immutable Build Version from Environment Overrides
- **Authoritative Installed Build Version**: Defined in `app/version.py` (`__version__ = "1.0.0-rc.2"`) and `vps_agent/__init__.py`. These are hardcoded Python constants embedded within the packaged distribution files.
- **Display / Protocol Override**: `app/config.py` allows `QC_APP_VERSION` to provide custom display or CI build tracking for `/health` and `/openapi.json`.
- **Audit Verification**:
  - If `QC_APP_VERSION="custom-build-99"` is set in the environment, `/health` reports `"version": "custom-build-99"`.
  - However, `app.version.__version__` and worker registration handshakes verify against the internal package constant.
  - An environment override cannot silently alter internal schema migrations, worker capability matrices, or protocol validation rules.

---

## 6. Isolated Deployment Rehearsal Plan

This section documents—but does **not** execute—the technical plan for conducting an isolated deployment rehearsal on disposable staging infrastructure.

### 6.1 Rehearsal Architecture & Infrastructure Requirements
The rehearsal will execute on a completely isolated staging sandbox without touching live infrastructure:

```
[ Isolated Staging Controller ] (Disposable Port 64310)
  ├── Environment: Isolated temporary data directory (/tmp/qc_rehearsal_data)
  ├── Database: Freshly initialized or restored test DB (qwen_rehearsal.db)
  ├── CAS Store: Isolated filesystem CAS (/tmp/qc_rehearsal_cas)
  ├── Credentials: Ephemeral JWT secret (QC_JWT_SECRET=rehearsal-temp-secret)
  └── Networking: Loopback / Dedicated staging port (NO Tailscale ACL changes)
         ▲
         │ (HTTP / Temporary Auth Token)
         ▼
[ Isolated Linux Worker Environment ]
  ├── OS: Disposable Ubuntu 22.04 / 24.04 VM or Container
  ├── Package: dist/qwen-collective-worker-1.0.0-rc.2.tar.gz
  ├── Service: Transient systemd unit or isolated foreground process
  ├── Sandbox: qwen-sandbox-runner / systemd-run DynamicUser
  └── Capabilities: Advertised Class A / Class B; Fail-Closed Class C
```

### 6.2 Pre-Flight Safety Guarantees
1. **Zero Production Network Modification**: No changes to production Tailscale ACLs, DNS records, or firewall rules.
2. **Zero Production Secret Exposure**: Rehearsal uses disposable random secrets (`QC_JWT_SECRET`, staging worker tokens). Production `.env` files are never loaded.
3. **Zero Production Data Overwrite**: The rehearsal controller is restricted to `/tmp/qc_rehearsal_data` with no access to live production databases.
4. **Zero Production Node Interference**: The live Windows controller, live production VPS, inference laptop, and office machines are untouched.

### 6.3 Rehearsal Procedure (Step-by-Step)

#### Phase 1: Controller Staging Deployment
1. Extract `dist/qwen-collective-1.0.0-rc.2.tar.gz` into `/tmp/qc_rehearsal_controller`.
2. Configure isolated environment variables:
   ```bash
   export QC_DATA_DIR="/tmp/qc_rehearsal_data"
   export QC_CAS_DIR="/tmp/qc_rehearsal_cas"
   export QC_JWT_SECRET="rehearsal-ephemeral-secret-32bytes"
   export QC_WORKER_SHARED_SECRET="rehearsal-worker-enroll-token"
   export QC_ENV="staging"
   export PORT=64310
   ```
3. Initialize database and verify schema version (`PRAGMA user_version == 18`, 102 tables).
4. Launch controller service on port `64310` and verify `/health` returns `1.0.0-rc.2`.

#### Phase 2: Linux Worker Staging Deployment
1. Transfer `dist/qwen-collective-worker-1.0.0-rc.2.tar.gz` to the isolated Linux staging VM.
2. Verify package SHA-256 against `dist/CHECKSUMS.sha256` (`c5d06aaf9b8b...`).
3. Extract archive to `/opt/qwen-collective-worker-rehearsal`.
4. Validate worker configuration:
   ```bash
   python3 -m vps_agent --validate
   ```
5. Enroll worker against staging controller (`http://staging-controller:64310`) using `QC_WORKER_SHARED_SECRET`.
6. Confirm issuance of `worker_id` and JWT token.

#### Phase 3: Operational Task Lifecycle Rehearsal
1. **Heartbeat & Telemetry Verification**: Confirm worker transmits steady heartbeats every 10s and appears healthy in `/api/v1/nodes`.
2. **Class A Task Verification**: Dispatch diagnostic task; verify execution in <1s and result submission.
3. **Class B Task Verification**: Dispatch pre-signed Python transformation task; verify execution under sandbox isolation and output artifact recording.
4. **Class C Fail-Closed Verification**: Attempt dispatch of untrusted generated code; verify controller immediately returns `BLOCKED` with `SECURE_EXECUTION_UNAVAILABLE`.
5. **Token Revocation Test**: Invoke token revocation; confirm worker is immediately denied API access (HTTP 401).

#### Phase 4: Backup Freshness & Rollback Rehearsal
1. Trigger automated staging backup script (`scripts/backup-windows.ps1` or Linux backup equivalent).
2. Validate snapshot creation, WAL checkpointing, and integrity verification.
3. Simulate worker failure / daemon restart; verify automatic reconnection.
4. Execute clean teardown script: stop worker, stop controller, purge `/tmp/qc_rehearsal_*`.

### 6.4 Quantitative Pass/Fail and Termination Criteria

| Rehearsal Stage | Success Criteria (PASS) | Termination / Failure Criteria (HOLD/FAIL) |
| :--- | :--- | :--- |
| **Package Extraction** | Clean extraction; SHA-256 matches `dist/CHECKSUMS.sha256` | Checksum mismatch; missing required files |
| **Database Setup** | Schema v18 initialized; 102 tables; 0 FK violations | Migration error; table count mismatch |
| **Worker Enrollment** | HTTP 200; valid JWT token issued; node online | HTTP 401/403; registration failure |
| **Heartbeat Cadence** | >= 3 consecutive heartbeats recorded within 30s | Node marked offline; missed heartbeats |
| **Task Lifecycle** | Diagnostic and Class B task complete with exit code 0 | Task lease failure; unhandled execution exception |
| **Security Gates** | Untrusted code returns `SECURE_EXECUTION_UNAVAILABLE` | Any arbitrary untrusted code executes |
| **Clean Teardown** | All test processes terminated; zero orphaned files | Process hung; unkillable child processes |

---

## 7. Final Acceptance & Governance Determination

### 7.1 Provenance Gate Determination
The archive SHA-256 hash discrepancy between `RELEASE_CANDIDATE_2_VALIDATION.md` and `RC2_FINAL_ACCEPTANCE_GATE.md` has been thoroughly investigated and definitively resolved:
1. The physical archives on disk are intact, valid, and unchanged since creation (`2026-10-21 23:44:01Z`).
2. The hashes in `RELEASE_CANDIDATE_2_VALIDATION.md` and `dist/CHECKSUMS.sha256` represent the true cryptographic identity of RC2.
3. The alternate hashes in `RC2_FINAL_ACCEPTANCE_GATE.md` were clerical transcription errors that have been corrected in this authoritative gate record.
4. No application code changes, package rebuilds, or release re-numbering are required.

### 7.2 Gate Recommendation

```
================================================================================
FINAL GATE DECISION: REHEARSAL AUTHORIZATION RECOMMENDED (ON HOLD)
================================================================================
Release Candidate 1.0.0-rc.2 package provenance is 100% reconciled.
The deployment rehearsal plan is complete and safety-bounded.

GOVERNANCE STATUS:
STRICT HOLD — AWAITING EXPLICIT HUMAN AUTHORIZATION TO COMMENCE REHEARSAL.

NO CODE CHANGES, REBUILDS, OR PRODUCTION ACTIONS ARE AUTHORIZED.
================================================================================
```

---

## 8. Hard Governance Boundary

In strict compliance with release engineering instructions:
- **No production deployment** has been performed or scheduled.
- **No staging deployment** has been initiated without authorization.
- **No live infrastructure** (VPS, inference laptop, office network, Tailscale mesh) has been altered.
- **No archive replacement or rebuild** was executed; existing archives were preserved intact.
- **No application source code** was modified under this authorization.
- **No production credentials or secrets** were rotated or accessed.
- **No untrusted generated code** was executed; `SECURE_EXECUTION_UNAVAILABLE` remains active.

**AUDIT COMPLETE — HALTED FOR HUMAN REVIEW AND REHEARSAL AUTHORIZATION.**
