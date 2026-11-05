# Increment 4: durable evidence feedback and targeted research paths

**Implemented and tested in the isolated candidate. Research certification remains blocked. No production changes or restarts were made.**

## What now works

The candidate has an append-only, project/run-scoped feedback ledger and explicit integration methods on the existing research planner and manager. Requirements identify the question and affected deliverable; claims link to those requirements. Captured source snapshots retain exact extracted text, its computed SHA-256, source identity, URL, registered retrieval time and reported raw-content hash. The extracted-text hash is kept separate because the existing source library may report a hash of raw HTML or PDF bytes.

Quotation verification checks a complete source assertion against an entire snapshot line and records exact character offsets and the snapshot digest. It does not infer truth from matching keywords, a substring, a model verdict or a supplied confidence number. The negated sentence “It is false that …” cannot support its embedded positive assertion through substring matching.

Each feedback pass records an immutable assessment with a verifier version. A later snapshot containing the explicit counterstatement reopens the claim as CONFLICT while retaining the earlier support and assessment. Old snapshots remain available after a source file changes. There is no automatic conflict resolution or deletion of inconvenient evidence.

The planner creates durable PROPOSED paths for missing claim evidence, requirements with no registered claim, conflicting assertions and missing authenticated-retrieval provenance. Each path carries its parent run, requirement, affected deliverable and reason. Paths grant no execution authority and require native dispatch; they do not directly search, run a model, change holds or alter production acceptance states.

The existing research manager exposes an explicit `prepare_evidence_feedback` bridge. By default it uses the persisted project research budget. Existing query/document/depth/iteration/time/token limits constrain planning, and the ledger independently bounds proposal cycles. Proposal generation does not falsely increment executed-query counters. Repeated proposals are suppressed across reconnects; duplicate or unrelated material does not restart the same unresolved path. An exhausted budget leaves the conflict or gap visible in the assessment.

## Tested feedback behavior

| Input change | Ledger result | Planner result |
|---|---|---|
| Registered requirement and assertion, no support | MISSING_EVIDENCE; confidence UNKNOWN | Targeted research proposal linked to the requirement and deliverable |
| Snapshot containing the complete assertion | SUPPORTED_QUOTATION; confidence SOURCE_QUOTATION_ONLY | Proposal to establish authenticated retrieval provenance |
| Additional identical content at another URL | One distinct content family; no confidence increase | No duplicate proposal or extra cycle |
| Unrelated snapshot | Claim revision and unresolved status unchanged | No repeated proposal |
| Snapshot containing the explicit counterstatement | CONFLICT; confidence CONFLICTED | New conflict-investigation path, with prior support retained |
| Budget exhausted | Current gaps/conflicts remain visible | No new paths; explicit budget-exhaustion reason |

There is deliberately no percentage confidence. Publisher independence and empirical probability remain unknown. Distinct content is not evidence of independent authorship. Every assessment and claim is ineligible for certification.

## Verification evidence

`increment4-integration-tests.txt`: **56 passed**:

- 26 new feedback tests, covering support, contradiction, duplicate and unrelated evidence, exact offsets, negation, snapshot persistence, append-only enforcement, scope isolation, path restrictions, source-path escape, failed/malformed sources, missing schema, restart deduplication, caller rollback, persisted budget enforcement and the manager bridge;
- 30 existing objective, blocked-reporting and worker-authorization checks from the prior increment.

`candidate-tests.txt`: **30 focused containment, native-provenance, project aggregation and Decimal-ledger checks passed** in the final candidate.

This is 86 passing checks across those two runs, not the entire repository suite. Tests use synthetic source material and temporary databases, with production environment loading and outbound network connections blocked. One existing Starlette/httpx deprecation warning remains. An initial pytest temporary-directory permission problem was corrected by using a fresh workspace-local test directory; it was a harness issue, not a feedback defect.

## Boundaries that remain explicit

1. **Registered source provenance is not authenticated retrieval.** The existing `SourceLibrary.add_source` can ingest supplied text while giving it fetched-looking metadata. The adapter labels snapshots REGISTERED_SOURCE and requires a follow-up authenticity path. It does not call that externally verified evidence. No calibration or independent-source confidence is invented.
2. **Quotation support is not general factual entailment.** Applicability, legal interpretation, surrounding context, dates, jurisdiction, paraphrase, structured calculations and implicit contradictions need additional verifiers. Counterstatements must be registered explicitly; the system does not yet discover arbitrary semantic contradictions.
3. **Claim inventory is explicit.** This increment verifies registered requirements/claims; it does not demonstrate that every material statement in a final report has been enumerated. A missing claim-completeness proof cannot authorize certification.
4. **This is an opt-in integration.** The planner and manager methods work against the ledger, but the legacy research cycle does not automatically import heuristic verdicts or execute these proposals. Automatic native scheduling, task completion feedback and final artifact invalidation still need a tested adapter.
5. **Schema installation is explicit.** `install_feedback_schema` is used by development tests, not application startup. The injected connection's caller owns the transaction. Production deployment needs a reviewed migration and rollback plan; no production schema was installed.
6. **No new authority boundary is claimed.** Append-only triggers guard ordinary application writes, not an administrator able to alter the database schema. Controller-owned retrieval/certification credentials and operating-system privilege separation remain outstanding.

## Next increment and release gate

Implement authenticated retrieval receipts and bind raw source bytes, extraction bytes, URL/redirect history, retrieval outcome and source/version identity to a native execution record. A supplied-text source must remain distinguishable from an actual retrieval. Then test a complete isolated loop: a ledger gap creates a proposal, the existing native scheduler dispatches an authorized retrieval task, verified results update the ledger, and conflicting or stale evidence invalidates the affected deliverable revision.

Keep the research hold until that loop, complete claim inventory and source/claim verification have valid positive controls as well as adversarial rejection tests. The financial ledger prototype still needs integration. Production source, schema, credentials, node enablement, deployment and restarts each retain their explicit approval gates. **Release status remains NO-GO.**

The regenerated review package includes the source changes, tests, original-byte hash manifest, patch and logs. Earlier increment reports are historical; this report describes the latest feedback work.
