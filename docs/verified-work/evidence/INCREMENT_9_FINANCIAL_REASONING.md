# Increment 9: Verified financial derivations

Isolated development candidate. Production deployment, restarts, hold removal and Russian River regeneration remain unauthorized and have not occurred.

## What changed

The version-bound claim contract can now include an approved financial specification. A closed financial document must preserve its approved total, expenditure identities/categories/amounts, and list of funding-rule claims. Funding names cannot silently replace cost lines, even if totals happen to match.

The reviewer freshly verifies each structured funding assertion through the existing native retrieval receipt, source applicability, and exact claim-text checks. It then applies the typed Decimal ledger to allocations, spending eligibility, committed funding, funding ceilings, category caps and match constraints. Declared conclusions must equal the computed result. Missing, malformed, unsupported or inconsistent inputs block the review.

The recorded derivation includes rule-claim hashes, a hash of the calculation inputs, per-cost/per-fund allocation totals, match basis/rate, required match, allocated eligible match, deficits, and reconciliation errors. Match remains a designation on an expenditure rather than another pool of money.

This path is integrated into fresh claim-document review and therefore its admission check and append-only invalidation notices. Changing approved contracts or canonical artifact versions requires revalidation. Existing general research certification holds remain.

## Scope of verified reasoning

This is a deterministic derivation from explicit structured premises. It does not infer funding rules from natural-language prose, prove the truth of a funding commitment, establish source authority merely from retrieval, solve an allocation problem, or certify an entire deliverable. Approved cost inputs are an explicit baseline, not independently audited invoices. Source authorship, authoritative interpretation, complete requirements and native producer certification need further work.

`financial_reasoning.verified` applies to this bounded financial check. Overall acceptance and certification remain false, and general requirement entailment remains unverified. A financially correct document does not waive the hold. The legacy freeform generation path does not automatically acquire this capability.

## Implementation and test evidence

- `app/services/research/financial_reasoning.py` implements the new structured verifier; `claim_contracts.py` invokes it during fresh version-bound review. `financial_ledger.py` now exposes intermediate derivations.
- 203 selected integration tests passed, including 22 financial reasoning tests and the prior native provenance, retrieval, recovery, applicability, admission and worker checks. See `increment9-integration-tests.txt`.
- 31 focused containment/provenance/negation/financial tests passed; see `candidate-tests.txt`.
- Adversarial cases include cost substitution, invented totals, incorrect conclusions, funding gaps, duplicate allocations, ineligible expenses, proposed funding, insufficient match, category caps, unsupported rules, nondecimal inputs and changed canonical artifact versions. An integer-cents oracle checks 121 allocations, and a separately specified total-cost match example expects a $25 deficit on a $100 grant at 20% of total cost.
- Native integration uses synthetic structured source cards and authenticated test worker/observer execution. It is not a live-source accuracy test or the complete repository regression suite.
- The initial focused run had one fixture database-lock failure (51 passed); committing the fixture write before worker authentication corrected it. The final full selected run passed. The earlier diagnostic log is retained as `increment9-focused-tests.txt`.
- Five existing warnings remain concerning TestClient/httpx deprecations and a duplicate OpenAPI operation ID.
- Original repository fingerprints matched the prior manifest. The cumulative patch passed `git apply --check --ignore-space-change` against preserved original files. Sources and tests are included under `candidate/`, with hashes in `candidate-manifest.json`.

## Next steps and approval gates

1. Add independent commitment/authority evidence and task-generation support for structured financial documents; model-proposed premises must never become approved rules automatically.
2. Extend structured derivations to other bounded reasoning operations, preserving evidence references, assumptions, contradictions, and explicit uncertainty. Use independently specified acceptance cases rather than model agreement as the criterion.
3. Keep feedback bounded: a failed derivation should create a targeted investigation or revision proposal, then revalidate the exact new artifact. Do not reinterpret failure as fulfillment.
4. Before production: complete repository regression and migration review, separate certification key authority, review operational rollback, and independently accept a new synthetic canary.

Explicit approval is required for deployment, production migrations, controller/worker restarts, hold removal, and a live canary. This candidate grants none of those permissions.
