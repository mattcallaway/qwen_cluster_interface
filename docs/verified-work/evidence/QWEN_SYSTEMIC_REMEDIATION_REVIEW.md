# Qwen Collective systemic remediation review

October 26, 2026. **Decision: candidate ready for review; production deployment NO-GO.**

The real repository and existing SQLite runtime records were inspected. An isolated containment candidate and a typed financial reconciliation prototype were implemented and tested. This is not a completed end-to-end remediation or a claim that production is fixed. The Russian River project was not regenerated.

## Evidence and boundaries

Repository: `C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective`.
HEAD: `5d3d90a21a33b1a8b9b0bc6c81387bd9f40a60fa`. The checkout contains extensive tracked edits and untracked quality-first modules. The candidate was copied from actual working-tree source, not reconstructed from the audit or HEAD. No original source files were edited. Original file hashes in `candidate-manifest.json` are preconditions for any later application.

Runtime database: `C:\Users\Matt C\.qwen-collective\data\qwen_collective.db`, inspected using SQLite URI `mode=ro`, `query_only=ON`, and a read transaction. The application's database helper was deliberately avoided because it sets journal mode WAL. The final snapshot is in `runtime-readonly-inspection.json`.

The snapshot found 45 orchestration runs, 100 orchestration tasks, 154 tasks, 136 leases, 130 executions, and 80 research sources. Counts for the audited project were zero in orchestration runs, tasks, and research sources; its specified run had zero orchestration tasks. This supports the native-pipeline bypass finding for this database. It does not prove that no other database contains records.

The same snapshot shows `pi-brain` and `qwen-vps` disabled. VPS policy includes `operational_hold=true`, `allow_operational_execution=false`, and `allow_web_fetch=false`. Enabled desktop and laptop nodes retain inference-only restrictions. No holds, services, environments, CAS blobs, historical project records, or network access policies were changed. No inference or project execution was requested.

Process inventory access was denied. Consequently the loaded controller/worker code revision and current service PIDs were not verified. Disk source must not be equated with running code. The historical PIDs in the reference conversation are not current-runtime evidence.

## Actionable source findings

| Priority | Actual implementation | Finding and required correction |
|---|---|---|
| P0 | `app/services/orchestration/evaluator.py`, `evaluate_report`, `evaluate_deliverable_contract` | Caller-supplied source dictionaries can resolve markers; a claim audit is optional, and `claims_grounded` defaults true. A fake source and a fabricated assertion were accepted in a fresh test. Remove implicit grounding and require controller-owned evidence records tied to the exact output. |
| P0 | Same evaluator, `evaluate_report` | Native task/lease/execution provenance is not mandatory for report acceptance. A nonempty plain report can be ACCEPTED without any native execution. Require the entire native chain and artifact identity before certification. |
| P0 | `scripts/execute_russian_river_resilience_project.py` | The script constructs its own source dictionaries and contracts, invokes evaluation, then registers artifacts. Calling application functions from a developer script is not authenticated worker execution. Keep scripts able to submit requests only; remove their certification and production-write authority. |
| P0 | `app/services/research/repair.py`, `repair_deliverable` | Global regex replacement maps Measure M to Measure F and matching terms to a hardcoded policy, without establishing the replacement from evidence or preserving assertion scope. It also falls back to the first plausible source sentence. Disable these paths pending whole-claim, evidence-backed repair. |
| P1 | `app/services/orchestration/verification.py`, `compute_multi_grant_allocation` | Each grant independently reuses the full eligible cost pool; there is no conserved allocation matrix. Float arithmetic, permissive eligibility defaults, and the label `awarded` compound the problem. Replace with typed costs, funds and explicit expenditures; distinguish proposed from committed resources. |
| P1 | `app/services/research/citations.py`, `validate_citations` | Source text or a hash can count as content, and `body_markdown.find(marker)` locates the first occurrence for a source. Later contradictory uses of the same source can escape equivalent checks. Verify every claim occurrence and material uncited assertion, with immutable retrieval evidence. |
| P1 | Evaluator, category completeness | Any word from a category may satisfy that category. Headings and incidental vocabulary are not evidence coverage. Require stable requirement IDs mapped to verified claim IDs. |
| P1 | Evaluator, `evaluate_project_objective_fulfillment` | A configured inference action is treated as executed without checking completed leases/receipts. With configured deliverables and an empty tested list, success flags can remain true. Aggregate only exact, current accepted deliverable revisions and completed execution attestations. |
| P1 | `app/services/research/citations.py` and evaluator | Citation report serialization emits `is_valid`, `contradicted_citations`, etc.; the optional evaluator audit expects `valid`, `contradicted`, etc. Replace informal dictionaries with a versioned validated schema. |

These findings concern program behavior. Legal and biological statements in the supplied audit were not independently revalidated, and were not embedded as new program truth.

## Implemented candidate

The review patch contains four source files; the candidate source copies are also provided separately.

1. **Native report provenance gate.** New `certification.py` joins the run, orchestration task, current task, completed lease, successful zero-exit execution, and valid artifact. Project, run, worker, lease, idempotency key, dispatched node and SHA-256 of the evaluated UTF-8 output must agree. Missing identity, missing tables or query failure returns false. `evaluate_report` applies the gate, including calls through `evaluate_deliverable_contract`.
2. **Research certification containment.** An unconditional failed `trusted_claim_evidence_complete` check prevents research reports from being certified using supplied sources or audit booleans. It intentionally remains unavailable until the trusted claim-evidence path exists. Missing provenance or claim evidence yields BLOCKED with score 0.0. This is an explicit safety hold, not a completed evidence verifier.
3. **Repair containment.** A matching prohibited pattern causes an unresolved result with the original text preserved, no claimed repair success, and no invented replacement. The arbitrary first-source-sentence fallback was removed. Ambiguous negation is preserved and blocked rather than reinterpreted.
4. **Decimal ledger prototype.** New `financial_ledger.py` uses typed immutable cost, fund and allocation records. Monetary inputs must be finite, nonnegative exact-cent Decimal values or decimal strings. It checks project totals, unique references, per-cost and per-fund conservation, explicit category eligibility, committed status, match kind, grant-versus-total match basis and aggregate category caps. A match is a designation on an expenditure, not additional funding. Capital gaps and match deficits are reported separately.

The ledger prototype is not wired into prompts, contract parsing, generation, rendering or certification. Its evidence IDs are required identifiers, not independently validated policy evidence. It does not yet model federal origin restrictions, jurisdiction/date applicability, match eligibility by cost category, multi-currency rules, waiver authorities or rule precedence. These require the next increment.

The report gate is deliberately conservative: current callers that lack final artifact/run linkage will block, including some non-research reports. The native query establishes recorded provenance, not factual correctness. It does not yet rehash CAS files, bind the exact producer attempt and contract revision, verify lease timing/retry history, or verify that a receipt was signed. No successful end-to-end controller/worker run was performed.

Other acceptance paths (code/general artifacts, project aggregation, database writes) are not comprehensively sealed by this patch. A script with production database access can still write records or statuses directly. Preventing that requires an operating-system/process privilege boundary; Python call-site checks cannot establish one. Do not represent this candidate as closing every certification bypass.

## Test evidence

`baseline-tests.txt`: six independently constructed regression tests against the original repository all failed, demonstrating acceptance without native execution, fake source acceptance, supplied-audit acceptance, database-failure acceptance, and unsafe repair behavior. These are expected failures that reproduce the defects, not an unsuccessful candidate run.

`candidate-tests.txt`: **23 tests passed** against the isolated candidate. One test also checks ten mutations of the native chain (missing lease/receipt, wrong worker/key/run/node, failed execution/task, expired lease and unverified artifact). Ledger tests cover exact decimal arithmetic, invalid money, total mismatch, duplicated match expenditures, ineligible permitting, proposed funding, match-rate basis, separate overlapping deficits, unknown references and aggregate caps. A positive fixture proves the recorded native chain can pass; a positive ledger fixture proves balanced funding can pass. Even a passing native chain cannot waive the research hold.

The suite uses in-memory SQLite fixtures, synthetic finance data and actual imported evaluator/repair code. Environment-file loading is mocked to avoid loading production credentials. No production helper, inference server, startup hook or migration is invoked. This is an independently constructed test suite, not an audit performed by a separate reviewer. The historical full regression suite and native end-to-end suite have not been run; compatibility is unproven.

Patch applicability was checked against separate copies of the two original files using `git apply --check --ignore-space-change`. That check passed. A plain check failed on the original repair file's line endings; the supplied patch normalizes text line endings. Verify original-byte hashes first and review any whitespace-tolerant application in an isolated checkout. No patch was applied to the real repository.

To reproduce from this chat's workspace, use the repository's Python environment:

```powershell
& 'C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective\.venv\Scripts\python.exe' outputs/test_containment.py
```

The default test root is the prepared `work/qwen-candidate` copy. Set `QWEN_TEST_ROOT` to another isolated checkout to test it. Select `ContainmentTests` when reproducing baseline failures against the original repository; the new provenance and ledger modules are absent there.

## Incremental implementation design and release gates

### Increment 1: review containment and compatibility

Review the supplied diff against its original-byte hashes. Rebase into an isolated checkout including the user's current changes; do not reset or overwrite the dirty repository. Add coverage for every accepting entry point, export path and promotion transition. Preserve all existing authorization checks in `lease_task_for_worker`, result submission and node synchronization. Fix failing compatibility tests by creating real provenance fixtures or retaining honest blocked outcomes, never by adding a script/testing exemption to production acceptance.

Acceptance gate: no malformed/missing evidence becomes ACCEPTED or FULFILLED, and control-flow tests show failure propagation through deliverables and project aggregation. Existing worker-authorization, upload, replay, hold-preservation and quarantine tests must still pass.

### Increment 2: make certification a controller-owned transaction

Add append-only, versioned certification records containing project/run/task/lease/execution/worker identities, producer attempt, plan and contract hashes, artifact hash, evaluator build, evidence-manifest hash, verdict and reason codes. Require a registered controller-issued task and authenticated authorized worker lease before generation, even for a single local inference node. Successful controller-only steps need their own explicit event type; they must never be mislabeled worker execution.

Certification must bind final bytes and current revision. Rehash the actual CAS object, verify the successful receipt and exact attempt, and commit the decision plus promotion atomically. Repairs produce new artifact versions and invalidate old attestations. Project status is derived from all required current certification records; a numeric score cannot override a failed mandatory gate. Missing, conflicting, expired or unreadable evidence remains BLOCKED/NEEDS_REVIEW.

Developer scripts may enqueue through authenticated APIs and read results. They must not possess certification signing authority or direct database/CAS promotion write permissions. Use distinct process identities and OS ACLs (or a separate certification service), with private signing keys readable only by that service. Record imports as external/unverified. A hash proves byte identity, not origin or truth.

Tests: script direct-call attempts; forged/self-signed receipts; output tampering; stale attempts; replayed leases; revoked worker; disabled/quarantined worker; time-of-check/time-of-use changes; missing CAS; cross-project/run evidence; concurrent promotion; database/schema failure. Require zero false accepts across the negative suite and a valid native positive control.

### Increment 3: trusted retrieval and full claim coverage

Persist retrieval events with requested/final URL, retrieval time, HTTP result, MIME type, immutable raw snapshot hash and extraction hash, jurisdiction, effective/version dates and source type. Keep developer/user seeds labeled as supplied material; they may guide research but cannot establish external authority. Source refresh must respect current network holds; missing capability blocks rather than silently enabling nodes.

Persist material claims with exact output span/hash, subject, predicate, object/value/unit, polarity, modality, temporal/jurisdictional scope, requirement IDs, source snapshot IDs, supporting offsets, verifier version and outcome. Inventory every material factual occurrence, including repeated citations, tables, conclusions and uncited claims. Unknown claim classification is unresolved, not automatically nonfactual. Support verbatim quotations and reproducible typed calculations first; uncertain paraphrase or legal applicability remains UNVERIFIED. Keyword overlap and a model's confidence are never final support. Validate current applicable authoritative documents and contradictory sources rather than treating prompts as truth.

Tests: one citation supporting the first sentence but contradicting the second; uncited asserted deadline; invented URL; same words/opposite predicate; negation, exception, requirement/recommendation, proposed/awarded, units, year/jurisdiction and source-version mismatches; missing required authority; contaminated source text; verifier exceptions. The research hold may be removed only after end-to-end tests demonstrate full claim coverage and mandatory trusted verification.

### Increment 4: integrate typed financial contracts

Preserve canonical cost IDs/categories from intake; funding sources occupy a separate table. Require explicit source-backed constraints with applicability and evidence IDs. Model funding status, cash/in-kind, federal/nonfederal origin, allowed expenditure and match categories, ceilings, percentage bases and documented waivers. Allocate explicit cents to cost/source pairs; prohibit duplicate expenditure and reuse of contribution units beyond their capacity. Derive totals, caps, match obligations and gaps from that ledger. Render tables from the validated object, then parse/compare the final artifact to ensure narrative and table figures still agree. Any unsupported financial statement is subject to the same claim gate.

Tests: supplied $4.09m cost rows against a $3.25m scope; lost/substituted cost IDs; $450k available versus $250k required; double-used in-kind; ineligible permitting; federal matching restrictions; aggregate design/admin caps; zero and total-cost match bases; proposed funds presented as committed; gap double counting; rounding boundaries; nonfinite/negative values; final rendered table drift. Fixtures are synthetic until policy rules have verified authoritative evidence.

### Increment 5: evidence-backed whole-claim repair

Replace immutable exact claim spans with whole propositions supported by trusted source offsets. Preserve or explicitly verify polarity, modality, scope, exceptions, table relationships and source identity. Negated mentions, quotations and historical comparisons must not trigger a global entity replacement. Ambiguous cases remain unresolved. Validate every occurrence affected by an edit, re-run the full evidence/ledger/native chain, and write a new version plus before/after hashes. Bound attempts and stop on repeated content or verifier nonprogress. Invalid output can be stored as a draft, never certified.

Tests: “No mentions of Measure M,” “not Measure M but Measure F,” quoted wrong claims, double negation, names appearing in several contexts, unrelated replacement passages, table row drift, repair loops and regressed previously valid claims. Require both safe refusal and supported successful repair controls.

## Explicit approval gates

| Gate | Reviewable prerequisite | Authorization required before action |
|---|---|---|
| Local candidate review | This patch, original-byte manifest, baseline and candidate logs, limitations | No production authority implied. Isolated development and tests remain within the requested scope. |
| Production-source integration | Rebased final patch; compatibility and all accepting-path tests; exact target and rollback package | Explicit approval before changing a checkout used by live production/imports. Current deliverable remains isolated. |
| Database/privilege changes | Reviewed migration/ACL/service-identity plan, backup and rollback verified in an isolated copy | Explicit approval before production schema/data/ACL/key changes. |
| Deployment or restart | Full regression plus isolated native end-to-end acceptance, signed release manifest, holds diff, rollback rehearsal, verified loaded-version plan | Explicit approval for named controller/worker release and restart. No node unquarantine or network-policy change implied. |
| New production research | Native canary with zero false acceptance; trusted evidence/ledger/repair gates active; declared source access capabilities | Explicit approval for a new canary project only. Russian River remains untouched. |

No deployment approval is requested now: the candidate is intentionally incomplete and the prerequisites above have not been met.
