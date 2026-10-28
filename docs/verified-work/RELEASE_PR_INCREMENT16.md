# Draft Release Pull Request: Increment 16 Verified Integration

**PR Title:** `feat(verification): Increment 16 protected Linux sandbox containment, out-of-process verification, and native model canary`  
**Base Branch:** `main`  
**Head Branch:** `integration/verified-work-increment16`  
**Commit Range:** `5d3d90a21a33b1a8b9b0bc6c81387bd9f40a60fa...HEAD`  
**Status:** DRAFT / FOR OPERATOR REVIEW ONLY (Production deployment and hold removal held fail-closed)  

---

## 1. Summary of Changes

This Pull Request integrates Increment 16 into the Qwen Collective codebase. It establishes:
1. **Linux Kernel Sandbox Containment (`vps_agent/linux_sandbox.py`):**
   - Transient systemd unit scoping (`qc-sandbox-<job_id>.service`) using `systemd-run`.
   - Immutable rootfs squashfs isolation with read-only root mount (`RootImageOptions=ro,nosuid,nodev`).
   - Dynamic user isolation (`DynamicUser=yes`, unprivileged execution, empty capability sets).
   - Strict network egress denial (`IPAddressDeny=any`, `PrivateNetwork=yes`, `RestrictAddressFamilies=AF_UNIX`).
   - Hard cgroup v2 resource limits (CPUQuota=100%, MemoryMax=256M, TasksMax=32).
   - Strict deadline termination (`RuntimeMaxSec`, `KillMode=control-group`, `SendSIGKILL=yes`) with empty cgroup verification.
2. **Out-of-Process Black-Box Verifier (`vps_agent/independent_verifier.py`):**
   - Independent test suites outside candidate container boundary (`qc.integer_cases.v1` and `qc.json_io_cases.v1`).
   - Authenticated HMAC observation signing over private per-run keys (`qc.protected_verifier_receipt.v1`).
   - Consume-before-execute root audit directory preventing nonce replay.
   - Strict output bounding (64 KB) and strict type equality checking.
3. **Controller Code Proposal Review Architecture (`app/services/orchestration/`):**
   - Version-bound code proposal reviews and native execution binding verification.
   - Additive append-only audit schema (`software_review_items`).
   - Rejection of stale historical review records when native execution bindings change.

---

## 2. Verification Evidence Index

All claims in this release package are backed by inspectable, reproducible cryptographic evidence:

| Verification Stage | Evidentiary Artifact | Key Findings |
| :--- | :--- | :--- |
| **Candidate Parity** | [`candidate-manifest.json`](file:///C:/Users/Matt%20C/Documents/Codex/2026-10-26/referenced-chatgpt-conversation-this-is-an/outputs/candidate-manifest.json) | 153 of 153 candidate files matched exact SHA-256 digests; 0 drift. |
| **Acceptance Matrix** | [`ACCEPTANCE_MATRIX.md`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective-integration/docs/verified-work/ACCEPTANCE_MATRIX.md) | Pre-repair finite acceptance matrix defining REQ-1 through REQ-9. |
| **Defect Reproduction** | [`tests/test_json_false_positive_reproduction.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective-integration/tests/test_json_false_positive_reproduction.py) | Confirmed false positive defect: unfixed code accepted `1` for `True` and `4.0` for `4`. Output saved in `evidence/json_false_positive_reproduction.txt`. |
| **Strict Comparator** | [`tests/test_strict_json_comparator.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective-integration/tests/test_strict_json_comparator.py) | 10 adversarial suites passing. Recursive check enforces `bool != int`, `int != float`, key order, finiteness, and depth bounds. |
| **Unit Test Suite** | [`tests/test_protected_verification.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective-integration/tests/test_protected_verification.py) | 54 of 54 tests passing; verifies grant lifecycle, replay rejection, and JSON I/O schemas. |
| **Mutation Testing** | [`tests/test_independent_test_adequacy_mutations.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective-integration/tests/test_independent_test_adequacy_mutations.py) | 100% kill rate (7/7 mutants killed: constant answer, unhandled crash, bad whitespace, byte vs char, coerced types, forged flags, missing field). |
| **Migration Robustness** | [`tests/test_migration_robustness_and_backup.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective-integration/tests/test_migration_robustness_and_backup.py) | 50 concurrent transactions (25 reads, 25 writes) during schema migration (0 errors, <1ms latency). Trigger immutability verified. Rollback preserves audit history without dropping table. |
| **Live Integer Canary** | [`evidence/laptop-native-canary-f706a49a...json`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective-integration/docs/verified-work/evidence/laptop-native-canary-f706a49af04d4182a27a5887d1f9fc85.json) | Real leased model inference (`qwen2.5:1.5b`), forged flag rejection, bounded revision pass, cgroup empty. |
| **Live Broader JSON Canary** | [`evidence/laptop-native-canary-json-io-a2c5c1c0...json`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective-integration/docs/verified-work/evidence/laptop-native-canary-json-io-a2c5c1c06d0e4130a637855d070f6877.json) | 6 cases evaluated (nominal, empty, unicode, malformed type, missing key, boundary spaces). Model failed naive attempt $\rightarrow$ bounded revision without test leakage $\rightarrow$ 6/6 passed. Replay denied (`FileExistsError`). |
| **VPS Sandbox Rehearsal** | [`evidence/vps-dryrun-rehearsal.json`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective-integration/docs/verified-work/evidence/vps-dryrun-rehearsal.json) | Staged rehearsal on `qwen-vps` (Ubuntu 24.04). 11 transient units verified safe; `qwen-worker.service` PID 270445 untouched. |
| **Per-Node Rollout Plan** | [`PER_NODE_ROLLOUT_AND_MIGRATION_PLAN.md`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective-integration/docs/verified-work/PER_NODE_ROLLOUT_AND_MIGRATION_PLAN.md) | Disaggregated node realities (Windows `desktop-worker-01` vs Linux `qwen-vps`/`qwen-laptop`), staged sequence, and disaster recovery runbooks. |
| **Operator Approval Gate** | [`OPERATOR_APPROVAL_GATE.md`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective-integration/docs/verified-work/OPERATOR_APPROVAL_GATE.md) | Defines prerequisite checklist and fail-closed hold boundaries. |

---

## 3. Disaggregated Rollout & Governance Statement

- **No Blanket Hold Removal:** Administrative holds on `pi-brain`, `desktop-worker-01`, and `qwen-vps` will **not** be lifted simultaneously. Each node will be staged sequentially following individual node acceptance.
- **Rollback Invariant:** Rollback procedures must preserve audit history. The audit table `software_review_items` must never be dropped during an application rollback.
- **Epistemic Modesty:** Successful canary results demonstrate satisfaction of the specific contracted test suites under out-of-process verification. They do **not** constitute proof of general model reliability or proof of zero information leakage.
- **Production Gate:** Merging this PR, updating production services, migrating production databases, and lifting node dispatch holds require **separate, explicit operator approval**.
