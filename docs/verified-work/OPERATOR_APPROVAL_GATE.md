# Operator Approval Gate & Production Safety Hold Policy

**Document:** `docs/verified-work/OPERATOR_APPROVAL_GATE.md`  
**Date:** 2026-10-28  
**Scope:** Qwen Collective Cluster Deployment & Rollout Governance  
**Status:** Strict Fail-Closed / Active Protective Holds  

---

## 1. Executive Policy

All modifications to production databases (`qwen_collective.db`), systemd service definitions, daemon runtime states, and administrative safety holds across the entire Qwen Collective cluster remain **STRICTLY FAIL-CLOSED**.

Under no circumstances will automated agents, background workers, or candidate code modify:
1. **Production Databases:** SQLite database files, schema migrations, operational run logs, or live artifact stores on the primary controller (`pi-brain`), workers (`desktop-worker-01`, `qwen-laptop`), or VPS (`qwen-vps`).
2. **Production Service Units:** Active daemon configurations or unit files (`qwen-worker.service`, `ollama.service`, `qc-ollama-ts.service`, etc.).
3. **Administrative Safety Holds:** Node dispatch flags, capability registry locks, or isolation barriers.

Any modification to these assets requires **separate, explicit, interactive operator approval** following verification of all safety prerequisites.

---

## 2. Safety Holds Inventory Across Cluster Nodes

| Node Identity | Node Role | Administrative Safety Holds | Current Status |
| :--- | :--- | :--- | :--- |
| **`pi-brain`** (100.99.144.119) | Controller / DB Core | Database write barriers, release migration locks, worker dispatch holds | **ACTIVE (FAIL-CLOSED)** |
| **`desktop-worker-01`** (100.100.103.111) | Desktop Worker | Native execution dispatch disabled (`is_enabled: 0`), operational hold active | **ACTIVE (FAIL-CLOSED)** |
| **`qwen-laptop`** (100.101.187.115) | Leased Inference Node | Production dispatch disabled; ephemeral sandbox test-host policies auto-expire (10 min) | **ACTIVE (FAIL-CLOSED)** |
| **`qwen-vps`** (100.71.194.68) | VPS Sandbox / Worker | Production daemon (`qwen-worker.service` PID 270445) untampered; sandbox unlinked; network egress deny active | **ACTIVE (FAIL-CLOSED)** |

---

## 3. Prerequisite Checklist Before Operator Gate Can Open

Before an operator grants approval for any production rollout or schema migration, each of the following evidentiary artifacts must be verified and signed off:

- [x] **Prerequisite 1: Independent Test Adequacy Review**
  - **Artifact:** [`docs/verified-work/TEST_ADEQUACY_REVIEW.md`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective-integration/docs/verified-work/TEST_ADEQUACY_REVIEW.md)
  - **Verification:** Evaluated broader declarative software contract profile (`qc.json_io_cases.v1`) beyond integer arithmetic. Verified strict typing, payload boundaries (32KB), equivalence partitioning, negative assertion fault injection, and mutation resistance criteria. 54 unit tests passing in integration suite.
- [x] **Prerequisite 2: Non-Production VPS Sandbox Dry-Run Rehearsal**
  - **Artifact:** [`docs/verified-work/evidence/vps-dryrun-rehearsal.json`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective-integration/docs/verified-work/evidence/vps-dryrun-rehearsal.json)
  - **Verification:** Executed live on Ubuntu 24.04.3 LTS VPS (`100.71.194.68`). Demonstrated rootfs squashfs isolation, DynamicUser isolation, read-only rootfs mount, network egress denial (`IPAddressDeny=any`), deadline timeout SIGKILL enforcement, output overflow bounding, and nonce replay rejection. Verified that all 11 transient systemd units cleaned up to empty cgroups (`populated 0`, `pids.current == 0`) and existing production service (`qwen-worker.service` PID 270445) remained completely unaffected.
- [x] **Prerequisite 3: Separate Explicit Operator Approval Ceremony**
  - **Status:** **APPROVED BY OPERATOR** (2026-10-28)
  - **Authorized Target Release Commit:** `71a4ed8b2b54786e07cbfabeee027ddd39b1ea82`
  - **Maintenance Window:** Initiated 2026-10-28T03:18:36-07:00
  - **Rollback Invariant Verified:** Non-destructive application rollback strictly preserves `software_review_items`. Cold snapshot backup required prior to schema migration.

---

## 4. Rollback and Disaster Recovery Protocol

If an approved production rollout encounters anomalous behavior:
1. **Immediate Service Halt:** Stop candidate worker units via `systemctl stop <unit>`.
2. **Revert Working Directory:** Check out previously verified baseline commit (`cbffe432dbeb77e0a078a9ac2460f8b3cd17adca`).
3. **Database Restore:** Restore pre-migration backup snapshot from cold storage (`/opt/qwen-collective/data/backups/`).
4. **Safety Re-Arm:** Ensure all node dispatch holds remain `is_enabled: 0` until root cause analysis is published.

---

## 5. Compliance Statement

No production database was modified, no production service was interrupted or reloaded, and no administrative safety hold was bypassed or disabled during this integration, test adequacy review, or VPS dry-run rehearsal.
