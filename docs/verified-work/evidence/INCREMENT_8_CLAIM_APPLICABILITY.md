# Increment 8: Version-bound claim applicability

Status: isolated development candidate; production NO-GO. No production deployment, restart, hold removal, or Russian River regeneration occurred.

## Implemented

- Claim review binds the current approved plan and contract to the canonical artifact ID, version, content hash, CAS bytes, and recorded producer metadata. Changed bindings require a new review.
- A closed JSON document format inventories every claim occurrence. Unknown prose, unregistered claims, changed claim text, and missing required claim coverage fail review. Headings are limited to nonassertive labels.
- The supported evidence profile is deliberately narrow: an authenticated native snapshot containing a structured scope card and one exact source assertion. Program, jurisdiction, effective dates, source URL, and snapshot age must satisfy the approved contract. General prose does not establish applicability.
- Applicable contrary evidence, expired evidence, changed artifact bytes or versions, missing CAS content, and changed plan or producer metadata invalidate a previously complete review. Append-only notices preserve the historical record.
- Admission recalculates reviews rather than trusting cached success or supplied audit flags. Internal revalidation and certification-review proposals cannot become web-fetch tasks.

## Important limits

`coverage_complete` means mechanical document inventory and applicable quotation support. It does **not** establish that a quotation answers the requirement, prove general factual or legal entailment, or authorize certification. Reviews explicitly retain `requirement_entailment_verified=false`, `accepted=false`, `certification_eligible=false`, and BLOCKED status.

Only the explicit claim-contract schema uses this new admission branch. Legacy freeform documents are not automatically converted or semantically checked. Existing research certification holds remain necessary. Historical approvals are not rewritten. The finance prototype is not yet integrated into document generation or final certification.

## Verification

- 181 selected integration tests passed, including 33 new claim-contract cases, native retrieval, worker execution, retrieval recovery, feedback, admission, authorization, and version consistency.
- 31 focused containment, provenance, negation, and Decimal financial checks passed.
- Integration tests used synthetic evidence and isolated databases; network access was blocked except test-local worker communication. These are not live production or real-source accuracy results.
- Existing dependency warnings remain: TestClient/httpx deprecations and a duplicate OpenAPI operation ID.
- Original repository fingerprints matched the previous review manifest. The cumulative patch passed applicability checking against the preserved original files.

Evidence: `increment8-integration-tests.txt`, `candidate-tests.txt`, `candidate-manifest.json`, and source/tests in `candidate/`. The cumulative `qwen-containment.patch` and `qwen-review-package.zip` contain the review candidate.

## Next increment and approval gates

1. Continue isolated development: integrate typed financial claim requirements with the Decimal ledger and exact source/contract bindings. A document must not replace expenditure lines with funding names or treat quotation coverage as financial correctness. Add independent adversarial cases before changing acceptance.
2. Before a production candidate: review migrations, complete repository regression checks, separate receipt/certification key authority, and resolve remaining general claim entailment and producer certification limits.
3. Explicit user approval remains required to deploy or migrate production, restart a controller/worker, enable Pi or desktop holds, or run a live canary. Current test success does not grant any of those approvals.

An approved live canary should use a new synthetic project, bounded requests/time, preserved holds, a documented rollback, and independent acceptance. It must not regenerate the Russian River project.
