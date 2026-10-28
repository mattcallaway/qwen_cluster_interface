# Increment 5: native retrieval receipts and verified feedback

**Completed in the isolated candidate. Production remains unchanged and research certification remains blocked.**

## Implemented behavior

The new controller-side `NativeRetrievalVerifier` accepts neither a source dictionary nor a worker's success flag as sufficient retrieval evidence. It requires a successful completed native task/lease/execution chain scoped to the project and research run. The chain must agree on worker identity, idempotency key, current task, dispatched node, current approved plan revision, planned action, selected project path and stage. The completed result must fall within the lease's recorded validity window. Superseded tasks, changed plan actions and inactive project/run/path states cannot be attested.

The queued `research_fetch` task must reference a durable feedback proposal and a URL bound to the approved action. A submitted retrieval result names that requested URL, final URL, raw-content hash and extracted-content hash. Before signing anything, the controller independently fetches the queued URL through the existing secure fetcher and extractor. Both observations must agree on the exact raw and extracted bytes and final URL. Dynamic content differences remain unresolved; the verifier does not smooth over mismatches.

Native observations require HTTPS at every redirect. The existing fetcher's public-destination checks, per-redirect policy checks, socket-pinning transport, TLS verification, byte limits and timeouts remain in use. This increment supports plain text and HTML extraction, with empty/challenge/browser-fallback responses rejected. PDF and browser-backed attestation require a later adapter.

The controller receipt preserves the raw response bytes, extracted text, their separate SHA-256 hashes, URL/redirect chain, HTTP outcome, observation time, observer node, worker, task, lease, execution, idempotency key, action and worker-output hashes, approved plan hash/revision and producer/lease attempts. An injected controller-only HMAC key signs the receipt. No production key is read, generated, saved or installed by this code.

Receipt verification checks the signature, content hashes, scope and current native context again. A later revoked credential, disabled node, hold, plan change or producer-attempt change prevents the receipt from being used as authenticated evidence under this conservative policy. Historical snapshots remain stored; they are not silently deleted.

## Permissions and budgets

The worker leasing gateway now denies `research_fetch` unless `allow_web_fetch` is explicitly true. Existing inference-only desktop/Pi restrictions are not expanded. An enabled controller observer must also have explicit web-fetch permission and no operational hold. Permissions and identities are checked again after the external observation to catch changes during verification.

The manager's native-retrieval bridge checks available retrieval budget, while the verifier independently checks persisted document/time allowances. A successful new controller observation consumes one document allowance and its measured duration in the caller's transaction. Reusing a valid receipt performs no new HTTP fetch and does not charge again. The quota update is conditional on remaining persisted capacity. Proposal/query counters continue to mean what they did before.

Failed observation/retry accounting is not yet complete: a failed transaction rolls back its counter changes, and a dedicated failed-attempt reservation/backoff policy still needs implementation before automatic retries are enabled. No automatic retry daemon or live dispatch loop is enabled here.

## Feedback integration

The existing research manager has an explicit observation-to-feedback bridge. A verified receipt creates an immutable native snapshot in the feedback ledger. Assessments count its support as an authenticated source quotation only when a configured controller verifier can revalidate its receipt. Restarting without that authority cannot retain the authenticated designation merely by trusting a database label.

The follow-up feedback method can receive that same verifier and revalidate receipts without refetching content. A source quotation with authenticated retrieval moves the next proposed path from retrieval-authenticity investigation to claim-applicability investigation. Contradictions and missing requirements retain the earlier gap/feedback behavior.

**Authenticated retrieval is not factual entailment or permission to certify.** Source authority, publisher independence, jurisdiction, dates, legal interpretation, completeness of the material-claim inventory and deterministic financial derivations are not established by this receipt. All resulting assessments remain ineligible for certification; no probability confidence is invented.

## Test evidence

`increment5-integration-tests.txt`: **85 passed**:

- 29 new native-retrieval cases;
- 26 prior feedback-ledger cases;
- 30 prior objective, blocked-reporting and worker-authorization cases.

`candidate-tests.txt`: **30 focused containment, provenance, aggregation and Decimal-ledger checks passed** after the final changes.

Together these are 115 passing checks across two runs, not a claim that the entire repository regression suite passes. A Starlette/httpx deprecation warning remains.

The positive native test uses the actual project-path/task approval services, authenticated HTTP lease and submission routes, lease/result service records, secure fetcher, extractor, controller receipt verifier and ledger. A test worker performs retrieval and submits its hashes. The controller independently observes a second response; tests assert both HTTP requests occur. Subsequent receipt reuse and feedback revalidation perform no third fetch.

Adversarial cases cover missing native executions, forged worker hashes, source changes between observations, signature/body tampering, wrong signing keys, receipt replay, disabled/revoked workers, missing web permission, controller holds, holds changed during observation, private-address redirects, HTTPS downgrade, stale/superseded tasks, approved-action drift, changed producer attempts, late lease completion, exhausted budgets and absent signing authority after restart.

All source responses are synthetic and delivered by an in-memory HTTP transport. DNS is stubbed only for synthetic `.example` names; the normal URL policy remains active. Outbound sockets are blocked by the test runner. Controller planning/binding rows are prepared by test fixtures, and the worker is simulated through the real HTTP API. **The live controller scheduler and packaged worker daemon were not executed end to end.** The new operation is not yet implemented in the standalone worker executor or release packaging; deploying these files alone would not enable worker retrieval.

An initial fixture attempted to edit an approved plan and was rejected by the repository's immutability trigger. The fixture was corrected to create a separate approved revision. The trigger and production policy were not weakened.

## Remaining release blockers and next stage

1. Implement the dedicated retrieval operation in the standalone worker package and a controller scheduler adapter that maps feedback proposals into reviewed native actions without bypassing approval gates. Test an actual isolated scheduler/worker process loop, not fixture-prepared controller bindings.
2. Complete failed-attempt budget reservations/backoff, concurrency/transaction-boundary review and resource accounting. The current explicit manager bridge holds a database transaction while observing a bounded external response; production lock duration needs review.
3. Provision the observer identity and signing key under an approved privilege-separation plan. This in-process implementation does not establish an OS boundary against a developer script or administrator with database/key access. Key rotation and historical-verification policy also remain to be designed.
4. Bind claims and decisions to exact deliverable/contract versions; complete source authority/applicability and claim-inventory verification; integrate typed financial generation and acceptance. A receipt must not become a substitute for these checks.
5. Prepare reviewed schema migrations, a full regression result, release manifest, backup and rollback rehearsal before requesting deployment/restart approval.

No production source, schema, credentials, node holds, services or historical Russian River data was changed. No live external retrieval or model inference was performed. The review patch and original-byte manifest are regenerated; the package remains a development handoff, not an installer. **Production release status: NO-GO.**
