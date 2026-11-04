# Stage 4 Remediation Report: Evidence Verification, Source Provenance & Citation Honesty

**Document ID:** `STAGE_04_RESEARCH_INTEGRITY.md`  
**Current Date:** 2026-10-19  
**Active Git Branch:** `feature/unified-artifact-deliverables`  
**Baseline Git Commit:** `c98510b`  
**Current Active Stage:** **Stage 4 (Evidence Verification, Source Provenance & Citation Honesty)**  
**Overall Program Status:** `READY_FOR_REVIEW`  
**Next Authorized Action:** Awaiting human review and explicit authorization (`CONTINUE STAGE 5`)

---

## 1. Executive Summary

Stage 4 development has completed all required remediations under the Stage 4 mandate and the mandatory **Pre-Stage 4 Execution Safety Restriction**:

1. **Mandatory Pre-Stage 4 Execution Safety Restriction:**
   - Disclosed and corrected the security posture of `app/services/orchestration/code_sandbox.py`. Process-level subprocess isolation with monkey-patching does NOT constitute an OS-enforced container sandbox.
   - Automatic execution of untrusted generated code and test fixtures is disabled by default via `is_os_enforced_sandbox_available() -> False`.
   - When dynamic execution is required for deliverable acceptance, `evaluate_code_deliverable()` returns `BLOCKED` with structured reason `SECURE_EXECUTION_UNAVAILABLE`.
   - Static AST syntax validation continues to work with explicit disclaimer (`"code_syntax_valid"` static validation; dynamic execution unverified).
   - Scheduler, engine, and UI cannot bypass `BLOCKED` to `ACCEPTED` or `COMPLETED`.
   - Process isolation risk reclassified as `HIGH` (RSK-15 in `RISK_REGISTER.md`).
   - Findings **F-12** and **F-14** are maintained as `PARTIALLY_FIXED`.
2. **Finding F-06 (Claim Extraction & Grounded Confidence):**
   - Implemented `extract_claims_with_spans()` in `app/services/research/extraction.py`. Preserves exact character offsets (`start_char`, `end_char`), line numbers (`start_line`, `end_line`), exact quotes, and surrounding context passages.
   - Eliminated hardcoded float literal `confidence = 0.9`. Implemented `compute_defensible_confidence()` in `app/services/research/verification.py`, calculating grounded confidence based on measurable statistics, definite factual relations, source quality weighting, independent domain counts, and contradiction penalties.
3. **Finding F-07 (Strict Citation Validation & Zero-Citation Rejection):**
   - Implemented strict zero-citation rejection in `app/services/research/citations.py` and `app/services/orchestration/evaluator.py`. Reports with 0 citations fail validation with `is_valid = False` and structured error `MISSING_REQUIRED_CITATIONS`.
   - Implemented claim-level evidence grounding checks: citations must resolve to registered sources, sources must be successfully retrieved, and text surrounding citations must demonstrate lexical/contextual grounding against referenced sources.
4. **Finding F-08 (Authoritative Domain Parsing, Syndication Deduplication, and Contradictions):**
   - Implemented `extract_registered_domain()` in `app/services/research/verification.py`. Accurately parses multi-part ccSLDs (`.co.uk`, `.com.au`, `.co.jp`, `.ac.in`, `.gov.uk`, etc.) and multi-tenant platforms (`github.io`, `pages.dev`, `vercel.app`, `substack.com`) where tenant subdomains represent independent publisher entities (`alice.github.io` != `bob.github.io`).
   - Implemented wire syndication deduplication: articles from different domains sharing `content_hash` or identical article titles count as 1 source. Claim verification remains `supported`, not `corroborated`.
   - Preserved contradictions: contradicted claims retain structured status `contradicted` rather than being averaged away, with confidence penalized to 0.15.
5. **Finding F-09 (Research Session Scope Isolation):**
   - Added Database Migration 14 (`app/migrations.py`): added `research_run_id TEXT` column and index `idx_research_sources_run` to `research_sources`.
   - Scoped search queries, search results, sources, and claims in `app/services/orchestration/engine.py` strictly to `research_run_id` (`rrun-{orch_run_id}`).
   - `SourceLibrary.list_sources()` and `EvidenceLedger.list_claims()` filter strictly by `research_run_id`.
   - `CitationEngine.validate_citations()` and `evaluate_report()` flag and reject cross-run citation contamination.

All **23 dedicated Stage 4 tests** in [`tests/test_stage04_research_integrity.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage04_research_integrity.py) pass with 100% success. Cumulative regression tests across Stages 1 through 4 (123 tests total) pass with 0 errors and 0 regressions.

---

## 2. Audit Findings Status & Invariant Reconciliation

| Finding ID | Title | Prior State | Stage 4 State | Reconciled Status | Verification Test Suite |
|:---:|---|:---:|:---:|:---:|---|
| **F-06** | Synthesis Evidence Hallucination & Paragraph Chunking | `CONFIRMED_STATICALLY` | `FIXED` | **FIXED** | `test_stage04_research_integrity.py::TestClaimExtractionAndConfidence` |
| **F-07** | Unenforced Citation Invariant & Zero-Citation Pass | `REPRODUCED` | `FIXED` | **FIXED** | `test_stage04_research_integrity.py::TestCitationValidationAndZeroCitationRejection` |
| **F-08** | Naive Domain Parsing & Synthetic Confidence Scoring | `CONFIRMED_STATICALLY` | `FIXED` | **FIXED** | `test_stage04_research_integrity.py::TestDomainParsingAndCorroboration` |
| **F-09** | Research Session Scope Leaks Across Run Boundaries | `CONFIRMED_STATICALLY` | `FIXED` | **FIXED** | `test_stage04_research_integrity.py::TestResearchRunIsolation` |
| **F-12** | Deliverable Acceptance Inversion & Shallow Evaluation | `FIXED` (Stage 3) | `PARTIALLY_FIXED` | **PARTIALLY_FIXED** | `test_stage03_execution_safety.py`, `test_stage04_research_integrity.py::TestSecurityExecutionRestriction` |
| **F-14** | DNS Rebinding Vulnerability in Crawler/Browser | `PARTIALLY_FIXED` | `PARTIALLY_FIXED` | **PARTIALLY_FIXED** | `test_stage01_security.py` |

### Architectural Invariant Reconciliation

- **I-07 (Honest Evidence):** **ENFORCED.** Claims are extracted with character/line source spans, citations require demonstrable claim-level textual support, and zero-citation reports are strictly rejected.
- **I-08 (Honest Completion):** **ENFORCED.** Deliverable acceptance requires objective evaluation checks. Reports cannot pass without citations. Generated code dynamic execution is disabled fail-closed (`SECURE_EXECUTION_UNAVAILABLE`).
- **I-10 (Full Lineage & Run Isolation):** **ENFORCED.** Migration 14 scopes research sources to `research_run_id`. Research queries, search results, and claims cannot bleed across sequential runs of the same project.
- **I-14 (Explicit Degraded States):** **ENFORCED.** When secure OS containment is unavailable, execution returns `BLOCKED` with structured reason `SECURE_EXECUTION_UNAVAILABLE`. Contradictions retain explicit `contradicted` status and are never averaged away.

---

## 3. Detailed Workstream Implementations

### Workstream 4A: Pre-Stage 4 Security Restriction & Execution Safety

- **Problem:** Subprocess isolation with `-I -B` and monkey-patching socket/subprocess functions does not enforce OS-level boundaries on untrusted generated code.
- **Remediation (`app/services/orchestration/code_sandbox.py`):**
  - `is_os_enforced_sandbox_available()` returns `False` by default. Can only be enabled in trusted development/test environments via explicit environment variable `QC_ENABLE_OS_ENFORCED_SANDBOX="1"`.
  - `run_isolated_code_evaluation()` returns `SandboxResult(status="BLOCKED", success=False, error_message="SECURE_EXECUTION_UNAVAILABLE: ...")`.
  - `evaluate_code_deliverable()` in `app/services/orchestration/evaluator.py`:
    - Performs static syntax parsing with `ast.parse`.
    - Emits `code_syntax_valid` in `goal_checks` with explicit disclaimer: `(static validation; dynamic execution unverified)`.
    - Appends `secure_execution_environment` failed check.
    - Sets `decision = "BLOCKED"` and `blocked_reason = "SECURE_EXECUTION_UNAVAILABLE"`.
  - Engine and UI treat `BLOCKED` as a non-terminal non-accepted state; runs cannot complete with blocked deliverables.
- **Traceability & Governance:**
  - Added **RSK-15** to `RISK_REGISTER.md` (Severity: High, Residual Risk: Low fail-closed).
  - Maintained **F-12** as `PARTIALLY_FIXED`.

### Workstream 4B: Finding F-06 — Claim Extraction & Grounded Confidence

- **Problem:** Previous implementation split text naively by paragraphs, took `p.split(".")[0]` as candidate claims, and assigned a hardcoded float literal `confidence = 0.9`.
- **Remediation:**
  - In `app/services/research/extraction.py`:
    - Defined `FactualClaimSpan` dataclass holding `claim_text`, `category`, `start_char`, `end_char`, `start_line`, `end_line`, `context_passage`, `exact_quote`, `confidence_score`, and `source_id`.
    - Implemented `extract_claims_with_spans(text, source_id, source_quality, max_claims)`:
      - Uses boundary-preserving regex sentence splitter.
      - Filters out short fragments (<25 chars), markdown headers (`#`), bullet lists (`*`, `-`), table markup (`|`), and raw URLs.
      - Requires measurable metrics (numbers, percentages, currencies, dates) or definite relational assertions (`achieved`, `outperformed`, `increased by`, `decreased by`, `published by`, etc.).
      - Computes 1-indexed character offsets matching `text[start_char:end_char]`.
      - Computes line numbers and extracts surrounding context passage (up to 150 chars before and after).
  - In `app/services/research/verification.py`:
    - Implemented `compute_defensible_confidence(...)`:
      - Contradicted claims drop to `0.15`.
      - Insufficient evidence drops to `0.0`.
      - Single-source supported claims scale dynamically between `0.50` and `0.80` based on source quality score, exact quote presence, and evidence strength.
      - Multi-domain corroborated claims scale between `0.70` and `0.98` based on independent domain count and quality.
      - Eliminates synthetic `0.9` constants.

### Workstream 4C: Finding F-07 — Strict Citation Invariant & Zero-Citation Rejection

- **Problem:** `validate_citations()` returned `is_valid = True` when a report contained zero citations. Deliverables lacking citations could pass evaluation.
- **Remediation:**
  - In `app/services/research/citations.py`:
    - If `len(citation_indices) == 0`, `validate_citations()` returns `is_valid = False` with error `MISSING_REQUIRED_CITATIONS: Report contains 0 citations. All research deliverables must cite authoritative sources.`
    - Validates that every citation marker resolves to a registered source in the project source library.
    - Validates that referenced sources were successfully fetched (`status != 'failed'` and non-empty content).
    - Validates claim-level lexical grounding: checks for keyword overlap between the text surrounding the citation marker and the source text. Citations lacking demonstrable backing content fail validation.
    - Validates research run isolation: citations to sources from conflicting run IDs fail validation.
  - In `app/services/orchestration/evaluator.py`:
    - `evaluate_report()` enforces `citation_coverage` goal check. Zero-citation reports fail this check and receive `decision = "REJECTED"`.

### Workstream 4D: Finding F-08 — Authoritative Domain Parsing, Syndication & Contradictions

- **Problem:** `verification.py` parsed domains naively using `parts[-2:]`, treating `news.bbc.co.uk` as `co.uk`, and treating different tenant subdomains (`alice.github.io` vs `bob.github.io`) as the same domain. Syndicated wire stories across different domains falsely counted as multi-source corroboration. Contradictions were averaged away.
- **Remediation (`app/services/research/verification.py`):**
  - Defined `MULTI_PART_PUBLIC_SUFFIXES` covering ccSLD second-level domains (`co.uk`, `gov.uk`, `ac.uk`, `com.au`, `co.jp`, `ac.in`, `gov.in`, `com.sg`, `co.za`, etc.).
  - Defined `MULTI_TENANT_SUFFIXES` covering multi-tenant platforms (`github.io`, `gitlab.io`, `pages.dev`, `vercel.app`, `substack.com`, etc.).
  - Implemented `extract_registered_domain(url)`:
    - Extracts `bbc.co.uk` from `https://news.bbc.co.uk/articles/123`.
    - Extracts `alice.github.io` and `bob.github.io` as distinct independent publisher entities.
    - Extracts `google.com` from `https://deepmind.google.com`.
  - Syndicated Wire Content Deduplication:
    - In `recompute_claim_verification()`, tracks seen `content_hash` values and article titles.
    - Sources from distinct domains that share the same `content_hash` or title are deduplicated into a single corroborating entity.
    - Two syndicated republishings result in `status = "SUPPORTED"`, NOT `"CORROBORATED"`.
  - Contradiction Surfacing:
    - When `record_contradiction()` is called, `recompute_claim_verification()` assigns `status = "CONTRADICTED"`.
    - Contradictions are preserved and never averaged away. Confidence drops to `0.15`.

### Workstream 4E: Finding F-09 — Research Run Scope Isolation

- **Problem:** Orchestration engine queries joined with `research_sources` on `project_id = ?`, bleeding search results and sources from earlier runs into subsequent runs of the same project.
- **Remediation:**
  - Added Migration 14 (`app/migrations.py`):
    ```sql
    ALTER TABLE research_sources ADD COLUMN research_run_id TEXT;
    CREATE INDEX idx_research_sources_run ON research_sources(research_run_id);
    ```
  - In `app/services/orchestration/engine.py`:
    - `_run_research_controller_step` defines `rrun_id = f"rrun-{orch_run_id}"`.
    - Search results query filters strictly on `q.research_run_id = ?`.
    - Source library listing filters strictly on `research_run_id = ?`.
    - Extraction, verification, and report synthesis pass `research_run_id = ?`.
  - In `app/services/research/sources.py`:
    - `add_source()`, `record_source()`, and `list_sources()` persist and filter by `research_run_id`.
  - In `app/services/research/verification.py`:
    - `record_claim()`, `add_claim()`, and `list_claims()` persist and filter by `research_run_id`.
  - In `app/services/research/citations.py`:
    - `validate_citations()` verifies that referenced sources belong to the active `research_run_id`.

---

## 4. Test & Verification Evidence

### Stage 4 Test Suite Execution Summary

Command: `pytest tests/test_stage04_research_integrity.py -v`

| Test Class | Test Name | Target Finding / Security Gate | Result |
|---|---|:---:|:---:|
| `TestSecurityExecutionRestriction` | `test_os_enforced_sandbox_is_unavailable_by_default` | Pre-Stage 4 Restriction | **PASSED** |
| `TestSecurityExecutionRestriction` | `test_run_isolated_code_evaluation_blocks_execution` | Pre-Stage 4 Restriction | **PASSED** |
| `TestSecurityExecutionRestriction` | `test_evaluate_code_deliverable_blocks_execution` | Pre-Stage 4 Restriction | **PASSED** |
| `TestClaimExtractionAndConfidence` | `test_extract_claims_with_spans_character_offsets` | F-06 | **PASSED** |
| `TestClaimExtractionAndConfidence` | `test_extract_claims_filters_noise_and_short_fragments` | F-06 | **PASSED** |
| `TestClaimExtractionAndConfidence` | `test_extract_claims_empty_or_minimal_text` | F-06 | **PASSED** |
| `TestClaimExtractionAndConfidence` | `test_compute_defensible_confidence_calculation` | F-06 | **PASSED** |
| `TestCitationValidationAndZeroCitationRejection` | `test_zero_citation_report_fails_validation` | F-07 | **PASSED** |
| `TestCitationValidationAndZeroCitationRejection` | `test_unresolved_citation_fails` | F-07 | **PASSED** |
| `TestCitationValidationAndZeroCitationRejection` | `test_citation_to_failed_source_fails` | F-07 | **PASSED** |
| `TestCitationValidationAndZeroCitationRejection` | `test_citation_lacking_claim_level_support_fails` | F-07 | **PASSED** |
| `TestCitationValidationAndZeroCitationRejection` | `test_grounded_citation_passes` | F-07 | **PASSED** |
| `TestCitationValidationAndZeroCitationRejection` | `test_evaluate_report_zero_citations_rejected` | F-07 | **PASSED** |
| `TestDomainParsingAndCorroboration` | `test_extract_registered_domain_public_suffixes` | F-08 | **PASSED** |
| `TestDomainParsingAndCorroboration` | `test_extract_registered_domain_multi_tenant` | F-08 | **PASSED** |
| `TestDomainParsingAndCorroboration` | `test_extract_registered_domain_standard_gtld` | F-08 | **PASSED** |
| `TestDomainParsingAndCorroboration` | `test_syndicated_content_does_not_falsely_corroborate` | F-08 | **PASSED** |
| `TestDomainParsingAndCorroboration` | `test_independent_sources_corroborate_claim` | F-08 | **PASSED** |
| `TestDomainParsingAndCorroboration` | `test_contradiction_is_preserved_and_penalizes_confidence` | F-08 | **PASSED** |
| `TestResearchRunIsolation` | `test_source_library_scoping_by_research_run_id` | F-09 | **PASSED** |
| `TestResearchRunIsolation` | `test_claims_scoping_by_research_run_id` | F-09 | **PASSED** |
| `TestResearchRunIsolation` | `test_citation_audit_rejects_cross_run_contamination` | F-09 | **PASSED** |
| `TestResearchRunIsolation` | `test_evaluate_report_rejects_cross_run_source_contamination` | F-09 | **PASSED** |

**Summary:** 23 passed, 0 failed, 0 errors in 1.18s.

### Cumulative Stage Regression Test Suite

Command: `pytest tests/test_stage01_security.py tests/test_stage02_planning.py tests/test_stage03_execution.py tests/test_stage03_execution_safety.py tests/test_stage04_research_integrity.py -v`

- `test_stage01_security.py`: 26 passed
- `test_stage02_planning.py`: 36 passed
- `test_stage03_execution.py`: 14 passed
- `test_stage03_execution_safety.py`: 24 passed (including 4 new security restriction regression tests)
- `test_stage04_research_integrity.py`: 23 passed
- **Total:** **123 passed**, 0 failed, 0 errors in 62.40s. Zero regressions across all prior remediation stages.

---

## 5. Security & Risk Register Reconciliation

1. **RSK-15 Added:** Process Isolation vs Genuine OS Container Sandbox.
   - Severity: High
   - Mitigation: Fail-closed execution gate (`is_os_enforced_sandbox_available() -> False`); deliverable evaluation returns `BLOCKED` with `SECURE_EXECUTION_UNAVAILABLE`; scheduler/UI cannot bypass `BLOCKED` into `ACCEPTED` or `COMPLETED`.
   - Residual Risk: Low (Fail-closed execution barrier).
2. **RSK-07 Updated:** Zero-Citation Research Validation.
   - Status: Mitigated (Stage 4 Verified).
   - Residual Risk: Low.
3. **RSK-08 Updated:** Shallow Deliverable Auto-Acceptance.
   - Status: Mitigated (Stage 3 & Stage 4 Verified).
   - Residual Risk: Low.
4. **F-12 & F-14 Preserved as PARTIALLY_FIXED:**
   - F-12: Dynamic code execution disabled fail-closed pending verified OS container backend.
   - F-14: DNS rebinding local proxy filter verified; awaiting live browser integration in staging.

---

## 6. Next Steps & Stop for Review

In strict accordance with remediation governance protocols:
1. **Zero Merges:** No code has been merged into `main`. All work remains on `feature/unified-artifact-deliverables`.
2. **Zero Daemon Restarts:** Background daemons, live workers, and production services remain untouched.
3. **Strict Stop for Review:** Development is halted. We await human review of this Stage 4 report and explicit written instruction (`CONTINUE STAGE 5`) before proceeding with Stage 5 (Research Workflow Throughout Project Lifecycle).
