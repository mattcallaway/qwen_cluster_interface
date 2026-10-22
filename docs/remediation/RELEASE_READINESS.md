# Production Release Readiness & Gate Criteria

This document tracks cumulative go/no-go readiness, production deployment gates, and verification criteria across the Qwen Collective remediation program.

---

## 1. Stage Gate Assessment & Readiness Status

Aligned with the canonical 9-stage roadmap:

| Gate | Canonical Stage Scope | Status | Criteria Met | Remaining Gaps / Blockers |
|:---:|---|:---:|:---:|---|
| **Gate 0** | Baseline, Reconnaissance, Architecture | **READY_FOR_REVIEW** | 18 findings verified; 55-module test baseline captured (460 pass, 2 fail); empirical F-10 verification completed; OS audit completed; control plane aligned. | Awaiting human authorization to begin Stage 1. |
| **Gate 1** | Security & Authorization Trust Boundaries (F-10, F-14, F-15) | `NOT_STARTED` | None | Requires worker authz/lease fencing, anti-SSRF IP pinning, admin CSRF enforcement. |
| **Gate 2** | Semantically Safe Planning & Prompt Fidelity (F-01..F-04, F-18) | `NOT_STARTED` | None | Requires zero silent action conversions, dynamic token budgeting, and capability registry. |
| **Gate 3** | Execution Reliability, Scheduler Fencing & Acceptance (F-12, F-13) | `NOT_STARTED` | None | Requires async controller dispatch, lease generation fencing, objective evaluation. |
| **Gate 4** | Evidence Verification & Citation Honesty (F-06..F-09, F-14) | `NOT_STARTED` | None | Requires run-scoped evidence, claim extraction, zero-citation pass elimination. |
| **Gate 5** | Continuous Research Throughout Lifecycle (F-05, F-02, F-04) | `NOT_STARTED` | None | Requires multi-phase research sidecar and research replanning. |
| **Gate 6** | Immutable CAS Artifacts, 1-Click Deliverables & Reuse (F-11, F-16) | `NOT_STARTED` | None | Requires full CAS unification, immutable versions, reuse UI. |
| **Gate 7** | Knowledge Vault, Review Governance & Learning (F-18, F-06..F-11) | `NOT_STARTED` | None | Requires knowledge trust tiers, review promotion lifecycle, contradiction handling. |
| **Gate 8** | API Contracts, UX, Operations, Backups & Docs (F-16, F-17) | `NOT_STARTED` | None | Requires route compatibility aliases and full disaster recovery restore proof. |
| **Gate 9** | End-to-End Validation, Adversarial Tests & Release (BB-01..BB-30) | `NOT_STARTED` | None | Requires full regression suite pass (0 failures), 30 scenario black-box matrix clean, release signoff. |

---

## 2. Production Rollout Prerequisites (Pre-Release Checklist)

Before any remediated code may be merged to `main` or deployed to production services (Windows controller or Linux worker fleet), the following conditions must be satisfied:

1. **Zero High/Critical Vulnerabilities:** All 18 audit findings (F-01 through F-18) must have verified status `FIXED` with passing unit and integration tests.
2. **Clean Test Suite:** 100% of tests in `tests/` must pass (`pytest tests` exit code 0; 0 failed, 0 errors). Preexisting failures in `test_orchestrator_units.py` and `test_web_routes.py` must be cleanly resolved.
3. **Black-Box Scenario Matrix:** All 30 scenarios (BB-01 through BB-30) must pass in Stage 9.
4. **Database Migration Safety:** All schema modifications must be additive, forward-only, and verified against a snapshot of the live database with `PRAGMA foreign_key_check` returning zero errors.
5. **Verified Atomic Recovery:** The backup and recovery runbook (`STAGE_08_OPERATIONS_API_UI.md`) must be validated by successfully restoring a complete archive (DB + CAS blobs + vault) to an empty directory.
6. **Controller Host OS Mitigation:** Operational risk RSK-13 (Windows 10 22H2 EOS) must be reviewed, and production migration to a supported OS documented.
7. **No Breaking API Changes:** All documented endpoints in `API_AND_INTEGRATION_SPECIFICATION.md` must remain accessible via canonical routes or backward-compatible aliases.
8. **Explicit Human Authorization:** Final release deployment requires explicit review of `STAGE_09_FINAL.md` and affirmative signoff from the cluster administrator.
