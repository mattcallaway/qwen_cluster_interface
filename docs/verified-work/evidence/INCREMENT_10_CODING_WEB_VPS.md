# Increment 10: Coding, web-app and VPS capability inspection

Development review candidate, 2026-10-27. No production deployment, restart, hold removal, live VPS access, model inference or Russian River regeneration occurred.

## Actionable findings

1. The native controller supports inference and a fixed script library; it does not yet expose a typed multi-file code/build/browser acceptance loop. Inference can draft code, but that does not prove functionality.
2. The software acceptance path could accept static syntax/structure checks. The candidate now enforces a mandatory independent-software-acceptance hold at `evaluate_deliverable_acceptance`, regardless of caller-provided flags. The static helper remains diagnostic.
3. Worker script stdout was merged over the execution envelope, allowing application output to replace recorded return codes, isolation labels and artifact lists. Reserved execution/authority fields now remain controlled by the worker; raw script claims are retained under `application_output`.
4. Artifact collection now excludes symlinks, hardlinks and oversized files and limits collection to 64 files / 4 MB total / 1 MB each. It remains top-level and requires descriptor-based race protection and streaming-output/process-tree limits before hostile-code certification.
5. The inspected standalone worker validates manifest fields and script checksum but does not authenticate its HMAC signature. Controller authorization is a separate existing boundary. Complete worker-side authenticity, expiry and lease binding require follow-up before broadening permissions.
6. `code_sandbox.py` deliberately reports no independently verified OS containment backend. The repository contains a scoped VPS launcher, but its live installation and confinement were not verified here. Do not lift execution holds on the strength of this source inspection.

## Implemented local pilot

The fixed reviewed `web_status_pilot` library script emits a synthetic task-status page and metadata. It has no live data, dependency downloads, credentials, server or deployment commands. The fixture can use the existing approved library-script path after appropriate approval; this increment did not dispatch it to a live worker.

The actual local rehearsal used a developer subprocess with reviewed fixture code. Its evidence explicitly records `native_execution=false`, `generated_by_collective=false`, `os_containment_verified=false`, and `certification_eligible=false`. This must not be described as an autonomous model coding run, OS isolation proof, native execution provenance, or completed VPS integration.

A separate Chromium rehearsal checked the title, initial inventory/count, blocked filter, filtered task/count, reset, mobile width, absence of page errors and absence of HTTP requests. The screenshot was visually inspected. The first launch found a bundled Playwright/browser revision mismatch; selecting the already-installed Chromium executable resolved it without downloads.

## Evidence

- 216 selected integration tests passed, including 13 new pilot/acceptance/output-boundary tests. `increment10-integration-tests.txt` contains the result; this is not the full repository suite.
- The earlier focused run passed 45 tests; `increment10-focused-tests.txt`.
- 10 browser assertions passed; `web-pilot/browser-checks.json`.
- Reviewed fixture execution/output hashes: `web-pilot/execution.json`, `result.json`, `index.html`. Visual evidence: `web-pilot/preview.png`.
- Reproducible local harnesses are included in `candidate/docs/verified-work/rehearsal/`; they expect this review workspace layout and installed Python/Node/Chromium. They are developer evidence tools, not deployment scripts.
- Existing TestClient/httpx and duplicate OpenAPI operation-ID warnings remain.
- 31 focused containment/provenance/negation/financial checks also passed; `candidate-tests.txt`.
- 21 overlay documentation links/entrypoints verified; `documentation-checks.json`. Historical document links were not globally audited.
- Original repository fingerprints matched the prior manifest. The cumulative patch passed applicability checking against preserved original files. The updated standalone worker ZIP is a development bundle, not an installation.

## Integrated documentation

The candidate preserves the existing root README, canonical `TECHMAP.md`, legacy `TECH_MAP.md` pointer, and `docs/remediation/README.md`, adding explicit links to `docs/verified-work/README.md` and its technical overlay. The work register indexes increments 1–10, copies their reports and selected evidence into the documentation tree, states current holds, and preserves historical stage statuses. No changes were written to the production/original repository.

The overlay maps source files to actual capability, identifies unverified assumptions in earlier architectural claims, and records the next coding/VPS stages. Future increments must update the register, map, evidence and package manifest together.

## Next implementation and approval gates

Next: typed multi-file model code proposals, bundle hashes, safe staging, independent test authority, authenticated execution manifests, and a verified containment backend. Then exercise a bounded new synthetic project through the actual controller → lease → worker → versioned output → independent browser/behavior acceptance flow. A developer fixture cannot substitute for that canary.

Read-only VPS readiness inspection may precede that canary, using existing approved access. Installation, running generated code, starting/stopping services, changing network rules, migrating/deploying production, restarting workers/controllers, and removing holds require explicit approval for the concrete operation. No production release is currently recommended.
