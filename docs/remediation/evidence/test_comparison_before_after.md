# Complete Before/After Test-ID Inventory & Module Reconciliation

## 1. Discrepancy Root Cause Analysis (The 28 Missing Prior Test Cases)

In the previous Stage 4 submission report, the executive summary reported **616 collected tests**, but the module breakdown table contained 61 named module rows whose arithmetic sum was **588 tests**, leaving **28 test cases** seemingly unaccounted for.

### Root Cause: JUnit XML Classname Dot-Splitting
Pytest's JUnit XML reporter formats class-based test cases with a compound classname attribute:
```xml
<testcase classname="tests.test_stage04_research_integrity.TestClaimExtractionAndConfidence" name="test_extract_claims_with_spans_character_offsets" ... />
```
When parsed with a naive string substitution or module accumulator:
1. `tests/test_stage04_research_integrity.py` contains **5 separate test classes** totaling **23 tests**:
   - `TestSecurityExecutionRestriction`: 3 tests
   - `TestClaimExtractionAndConfidence`: 4 tests
   - `TestCitationValidationAndZeroCitationRejection`: 6 tests
   - `TestDomainParsingAndCorroboration`: 6 tests
   - `TestResearchRunIsolation`: 4 tests
2. `tests/test_stage04_review_corrections.py` originally contained **6 separate test classes** totaling **31 tests**:
   - `TestSandboxBypassElimination`: 4 tests
   - `TestFactualEntailmentAdversarialCases`: 8 tests
   - `TestCitationGroundingInDeliverables`: 3 tests
   - `TestConfidenceSemantics`: 4 tests
   - `TestPublicSuffixAndIndependence`: 6 tests
   - `TestDatabaseMigrationAndRunIsolationEndToEnd`: 6 tests
3. In the prior reporting script, class-scoped test cases in multi-class files were either split into pseudo-submodules or aggregated into a truncated placeholder row, causing the 28 class-scoped test cases to be dropped from the individual module tally while remaining in the global pytest collected count ($588 + 28 = 616$).

### Reconciled Structure
Normalizing the JUnit XML `classname` directly to the module file on disk (`tests/{parts[1]}.py`):
- Exactly **61 test module files** exist on disk (`tests/test_*.py`).
- Every test in JUnit XML maps 1-to-1 to its canonical source file.
- Zero aggregated placeholder rows exist.
- Prior suite collected count: **616 tests** (615 passed, 1 baseline failure).
- Current suite collected count: **624 tests** (623 passed, 1 baseline failure).

---

## 2. Before / After Inventory Comparison

| Metric | Prior Submission Report | Stage 4 Final Gate Reconciliation | Delta |
|---|:---:|:---:|:---:|
| **Test Files on Disk** | 61 | 61 | 0 |
| **Reported Modules in Table** | 61 (summed to 588) | 61 (summed to 624) | +36 (+28 prior omitted + 8 new) |
| **Aggregated Placeholder Rows** | 1 (implicit remainder) | 0 (strictly zero) | -1 |
| **Total Tests Collected** | 616 | 624 | +8 |
| **Tests Passed** | 615 | 623 | +8 |
| **Tests Failed** | 1 (`test_knowledge_page`) | 1 (`test_knowledge_page`) | 0 (baseline unchanged) |
| **Tests Skipped** | 0 | 0 | 0 |
| **Total Test Execution Time** | ~175s | 192.0s | +17.0s |

---

## 3. Inventory of Newly Added Tests (+8 Tests)

The 8 new tests were added to `tests/test_stage04_review_corrections.py` to directly verify the Stage 4 Final Gate directives:

1. **`tests/test_stage04_review_corrections.py::TestDeliverableCompletionGateFourEntailmentStates::test_entailment_supported_passes_completion_gate`**
   - **Directive:** Test factual entailment state `SUPPORTED` through real deliverable completion gate.
   - **Verification:** All 3 layers pass, citation audit succeeds, deliverable decision is `ACCEPTED`.
2. **`tests/test_stage04_review_corrections.py::TestDeliverableCompletionGateFourEntailmentStates::test_entailment_contradicted_blocks_and_rejects_completion_gate`**
   - **Directive:** Test factual entailment state `CONTRADICTED` through real deliverable completion gate.
   - **Verification:** Fails goal layer check `citation_contradiction`, decision is `REJECTED`, run transitions to `BLOCKED`.
3. **`tests/test_stage04_review_corrections.py::TestDeliverableCompletionGateFourEntailmentStates::test_entailment_insufficient_evidence_blocks_and_rejects_completion_gate`**
   - **Directive:** Test factual entailment state `INSUFFICIENT_EVIDENCE` through real deliverable completion gate.
   - **Verification:** Fails goal layer check `citation_insufficient_evidence`, decision is `REJECTED`, run transitions to `BLOCKED`.
4. **`tests/test_stage04_review_corrections.py::TestDeliverableCompletionGateFourEntailmentStates::test_entailment_unverified_requires_human_review`**
   - **Directive:** Test factual entailment state `UNVERIFIED` through real deliverable completion gate.
   - **Verification:** Fails goal layer check `citation_unverified`, decision is `NEEDS_HUMAN_REVIEW`, blocking auto-acceptance.
5. **`tests/test_stage04_review_corrections.py::TestMultiSessionVersionedObservationsAndPSL::test_psl_dataset_info_and_fallback`**
   - **Directive:** Identify actual maintained Public Suffix List dataset and metadata.
   - **Verification:** Reports canonical upstream URL (`https://publicsuffix.org/list/public_suffix_list.dat`), rules count, and fallback status.
6. **`tests/test_stage04_review_corrections.py::TestMultiSessionVersionedObservationsAndPSL::test_psl_dataset_update_and_sha256_verification`**
   - **Directive:** Verify PSL update mechanism with SHA-256 validation and fail-closed error handling.
   - **Verification:** Valid updates succeed and take effect dynamically; invalid SHA-256 checksums raise `ValueError` and fail closed.
7. **`tests/test_stage04_review_corrections.py::TestMultiSessionVersionedObservationsAndPSL::test_multisession_versioned_observations_same_url_same_run`**
   - **Directive:** Demonstrate multiple independent research sessions within one orchestration run, including two versioned observations of the same URL.
   - **Verification:** Session 1 records v1 ($H_1$), Session 2 records v2 ($H_2$); both version records, session IDs, and distinct content files are preserved in `get_source_versions()`.
8. **`tests/test_stage04_review_corrections.py::TestMultiSessionVersionedObservationsAndPSL::test_explicit_cross_run_source_reuse_ledger`**
   - **Directive:** Demonstrate immutable provenance and explicit reuse ledger.
   - **Verification:** Unauthorized cross-run citation fails with isolation error; explicit entry in `research_source_reuses` authorizes citation while preserving original provenance.
