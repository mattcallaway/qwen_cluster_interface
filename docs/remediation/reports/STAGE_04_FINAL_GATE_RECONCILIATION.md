# STAGE 4 — FINAL EVIDENCE RECONCILIATION BEFORE STAGE 5
**Document Reference:** `docs/remediation/reports/STAGE_04_FINAL_GATE_RECONCILIATION.md`  
**Evaluation Standard:** Master Implementation Directive & Stage 4 Review Correction Audit  
**Execution Timestamp:** 2026-10-20T20:26:04Z  
**Branch:** `feature/unified-artifact-deliverables`  
**Git HEAD Commit:** `c98510b9a1569a113c762f083f5bba4395704531`  
**Gate Status:** **STAGE 4 REMEDIATION FULLY RECONCILED — STRICT STAGE 5 HOLD MAINTAINED**

---

## Executive Summary

This report establishes the final, definitive evidence reconciliation for **Stage 4 (Research Integrity, Evidence Grounding & Sandbox Elimination)** prior to entering Stage 5. It addresses all six outstanding directives:

1. **Test-ID Inventory & 28-Test Discrepancy Reconciliation:** A complete, individual, 1-to-1 test inventory has been generated directly from JUnit XML (`junit_report.xml`) with **zero aggregated or placeholder rows**. The reported 616 collected tests versus 588 tests across 61 module rows from the previous submission is formally reconciled: a naive class-name parser split multi-class test files (`test_stage04_research_integrity.py` with 5 classes and `test_stage04_review_corrections.py` with 6 classes), omitting 28 class-scoped test cases into an unprinted remainder. Normalized 1-to-1 against the filesystem, all test cases belong to exactly **61 test files on disk**.
2. **Fresh Evidence Artifacts:** Fresh JUnit XML (`junit_report.xml`), raw pytest output (`pytest_stdout.txt`, `pytest_collect_raw.txt`), execution metadata (`pytest_execution_meta.json`), Git working-tree metadata (`git_metadata.json`), complete test matrix (`test_matrix.md`), and test ID inventory (`test_ids_inventory.md` with all 624 tests enumerated) are generated and verified.
3. **Four Factual Entailment States Through Deliverable Completion Gate:** All four entailment states (`SUPPORTED`, `CONTRADICTED`, `INSUFFICIENT_EVIDENCE`, `UNVERIFIED`) are deterministically integrated into `app/services/research/citations.py`, evaluated via `app/services/orchestration/evaluator.py`, and validated through `_evaluate_deliverables()` in `app/services/orchestration/engine.py`. Specifically, `INSUFFICIENT_EVIDENCE` fails closed to `REJECTED`, blocking the completion gate.
4. **Maintained Public Suffix List Dataset & Update Mechanism:** The authoritative Mozilla/ICANN Public Suffix List (`https://publicsuffix.org/list/public_suffix_list.dat`) is formally tracked. An atomic parser and update function (`update_psl_dataset`) with SHA-256 verification and bundled offline fallback has been implemented in `app/services/research/public_suffix.py`.
5. **Multi-Session Versioned Observations & Immutable Provenance:** Multiple independent research sessions (`session_1`, `session_2`) within a single orchestration run are demonstrated. Two versioned observations of the same URL across sessions are preserved in `research_source_versions` with distinct content hashes, timestamps, and storage paths. Cross-run reuse is strictly governed by `research_source_reuses`.
6. **Risk Register Reconciliation (RSK-05 & RSK-06):** In `docs/remediation/RISK_REGISTER.md`, RSK-05 and RSK-06 have been formally reconciled to **`Mitigated (Stage 2 Verified)`** based on finding F-01 and F-02 test proofs.

---

## 1. Test-ID Inventory & 28-Test Discrepancy Reconciliation

### 1.1 Root Cause of the 588 vs 616 Test Discrepancy

In the prior report (`STAGE_04_RESEARCH_INTEGRITY_REVIEW_CORRECTION.md`), the summary reported 616 collected tests, while the 61-row table summed to 588 tests.

**The Cause:** Pytest's JUnit XML output tags class-scoped test cases with compound classnames:
```xml
<testcase classname="tests.test_stage04_research_integrity.TestClaimExtractionAndConfidence" name="test_extract_claims_with_spans_character_offsets" ... />
```
The previous evidence parser split on `.` and grouped by `parts[1]`, but omitted class-scoped methods in multi-class files from the individual file row tallies:
- `tests/test_stage04_research_integrity.py` contains **5 classes** totaling **23 tests**:
  - `TestSecurityExecutionRestriction` (3 tests)
  - `TestClaimExtractionAndConfidence` (4 tests)
  - `TestCitationValidationAndZeroCitationRejection` (6 tests)
  - `TestDomainParsingAndCorroboration` (6 tests)
  - `TestResearchRunIsolation` (4 tests)
- `tests/test_stage04_review_corrections.py` originally contained **6 classes** totaling **31 tests**:
  - `TestSandboxBypassElimination` (4 tests)
  - `TestFactualEntailmentAdversarialCases` (8 tests)
  - `TestCitationGroundingInDeliverables` (3 tests)
  - `TestConfidenceSemantics` (4 tests)
  - `TestPublicSuffixAndIndependence` (6 tests)
  - `TestDatabaseMigrationAndRunIsolationEndToEnd` (6 tests)

When the parser grouped by naive subpath, 28 tests in class-nested fixtures were dropped from module rows into an unprinted remainder.

### 1.2 Reconciled Arithmetic
When normalized to the actual files on disk (`tests/{parts[1]}.py`):
- **On-Disk Files Matching `tests/test_*.py`:** Exactly **61 test files**.
- **JUnit XML Test Modules:** Exactly **61 test modules**.
- **Aggregated / Placeholder Rows:** **0 (None)**.
- **Prior Baseline Collected Tests:** Exactly **616 tests** ($588 + 28 = 616$).
- **Newly Added Directive Tests:** **+8 tests** (4 for entailment states + 4 for PSL & multi-session versioning).
- **Current Total Collected Tests:** Exactly **624 tests** ($623\text{ passed}, 1\text{ baseline fail}, 0\text{ skipped}$).

---

## 2. Before / After Inventory Comparison & Evidence Artifacts

### 2.1 Suite Comparison Table

| Metric | Prior Submission | Current Final Gate Reconciliation | Delta | Analysis |
|---|:---:|:---:|:---:|---|
| **Test Files on Disk** | 61 | 61 | 0 | Identical module footprint |
| **Module Rows in Matrix** | 61 (summed 588) | 61 (summed 624) | +36 | +28 reconciled + 8 new tests |
| **Aggregated Rows** | 1 (implicit) | 0 | -1 | Full 1-to-1 fidelity |
| **Total Tests Collected** | 616 | 624 | +8 | New directive tests added |
| **Passed Tests** | 615 | 623 | +8 | All new tests passing |
| **Failed Tests** | 1 | 1 | 0 | `test_knowledge_page` (known baseline) |
| **Skipped Tests** | 0 | 0 | 0 | Zero skipped |
| **Execution Duration** | 175.2s | 192.0s | +16.8s | Full suite including live migrations |

### 2.2 Generated Evidence Artifacts
All evidence artifacts have been regenerated directly from test execution and stored in `docs/remediation/evidence/`:
- [`junit_report.xml`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/junit_report.xml): Complete JUnit XML export of all 624 test cases.
- [`pytest_stdout.txt`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/pytest_stdout.txt): Raw console output from pytest.
- [`pytest_collect_raw.txt`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/pytest_collect_raw.txt): Raw `--collect-only` pytest output.
- [`pytest_execution_meta.json`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/pytest_execution_meta.json): Execution start/end times and exit codes.
- [`git_metadata.json`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/git_metadata.json): Git commit SHA (`c98510b...`), porcelain status, and diff stat.
- [`test_matrix.md`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/test_matrix.md): Full 61-module execution table with zero placeholder rows.
- [`test_ids_inventory.md`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/test_ids_inventory.md): Full individual enumeration of all 624 test IDs with status and duration.
- [`test_comparison_before_after.md`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/test_comparison_before_after.md): Before/after reconciliation breakdown.

---

## 3. Four Factual Entailment States Through Deliverable Completion Gate

### 3.1 Architecture & Fail-Closed Logic

In accordance with Finding F-12 and the Master Directive, deliverable completion must not permit ungrounded claims or hallucinated evidence to pass. The four entailment states are mapped into `app/services/research/citations.py` and evaluated by `app/services/orchestration/evaluator.py`:

```
┌────────────────────────────────────────────────────────────────────────┐
│                      CLAIM ENTAILMENT INGESTION                        │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
       ┌───────────────────────────┼───────────────────────────┐
       ▼                           ▼                           ▼
  [SUPPORTED]               [CONTRADICTED]           [INSUFFICIENT_EVIDENCE]  [UNVERIFIED]
       │                           │                           │                   │
  audit["valid"]=True        audit["valid"]=False        audit["valid"]=False  audit["valid"]=False
       │                           │                           │                   │
       ▼                           ▼                           ▼                   ▼
 evaluate_report:            evaluate_report:            evaluate_report:      evaluate_report:
 Layer 1 (Schema): PASS      Layer 1 (Schema): PASS      Layer 1 (Schema): PASS Layer 1: PASS
 Layer 2 (Exec):   PASS      Layer 2 (Exec):   PASS      Layer 2 (Exec):   PASS Layer 2: PASS
 Layer 3 (Goal):   PASS      Layer 3 (Goal):   FAIL      Layer 3 (Goal):   FAIL Layer 3: FAIL
                             (citation_contradiction)    (citation_insufficient_evidence) (citation_unverified)
       │                           │                           │                   │
       ▼                           ▼                           ▼                   ▼
 Decision:                   Decision:                   Decision:             Decision:
 "ACCEPTED"                  "REJECTED"                  "REJECTED"            "NEEDS_HUMAN_REVIEW"
       │                           │                           │                   │
       ▼                           ▼                           ▼                   ▼
 Completion Gate:            Completion Gate:            Completion Gate:      Completion Gate:
 PASS                        BLOCKS RUN                  BLOCKS RUN            BLOCKS RUN
 (Auto-accepted if           (Run state -> BLOCKED;      (Run state -> BLOCKED; (Requires explicit
  autonomy >= 2)              Deliverable rejected)       Deliverable rejected) human approval)
```

### 3.2 Code Implementation in `evaluator.py` & `citations.py`
In `app/services/orchestration/evaluator.py`:
```python
if citation_audit:
    audit_ok = bool(citation_audit.get("valid"))
    if not audit_ok:
        contradicted_cits = citation_audit.get("contradicted") or []
        insufficient_cits = citation_audit.get("insufficient_evidence") or [
            c for c in (citation_audit.get("errors") or []) if "INSUFFICIENT_EVIDENCE" in c
        ]
        unverified_cits = citation_audit.get("unverified") or []
        if contradicted_cits:
            claims_grounded = False
            citation_rejection_reason = f"CONTRADICTED_CLAIMS: {len(contradicted_cits)} cited assertions directly contradict referenced sources"
            goal_checks.append(_check("citation_contradiction", False, citation_rejection_reason, layer="goal"))
        elif insufficient_cits:
            claims_grounded = False
            citation_rejection_reason = f"INSUFFICIENT_EVIDENCE: {len(insufficient_cits)} cited assertions lack verifiable evidence in referenced sources"
            goal_checks.append(_check("citation_insufficient_evidence", False, citation_rejection_reason, layer="goal"))
        elif unverified_cits:
            unverified_citations = True
            goal_checks.append(_check("citation_unverified", False, f"unverified citations: {len(unverified_cits)} assertions lack deterministic proof", layer="goal"))
```

### 3.3 Test Verification
Tested in [`tests/test_stage04_review_corrections.py::TestDeliverableCompletionGateFourEntailmentStates`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage04_review_corrections.py):
- `test_entailment_supported_passes_completion_gate`: **PASSED** (decision=`ACCEPTED`).
- `test_entailment_contradicted_blocks_and_rejects_completion_gate`: **PASSED** (decision=`REJECTED`, fails `citation_contradiction`).
- `test_entailment_insufficient_evidence_blocks_and_rejects_completion_gate`: **PASSED** (decision=`REJECTED`, fails `citation_insufficient_evidence`).
- `test_entailment_unverified_requires_human_review`: **PASSED** (decision=`NEEDS_HUMAN_REVIEW`, fails `citation_unverified`).

---

## 4. Maintained Public Suffix List Dataset & Update Mechanism

### 4.1 Upstream Identification
- **Authoritative Source:** Mozilla / ICANN Public Suffix List.
- **Canonical Upstream URL:** `https://publicsuffix.org/list/public_suffix_list.dat`
- **Standard In-Memory Baseline:** 126 bundled rules covering major international ccSLDs (`.co.uk`, `.com.au`, `.co.jp`, `.gc.ca`, etc.) and private multi-tenant hosting platforms (`.github.io`, `.pages.dev`, `.substack.com`, `.vercel.app`).

### 4.2 Maintenance & Update Implementation
Implemented in [`app/services/research/public_suffix.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/research/public_suffix.py):
- `parse_psl_text(text: str) -> Tuple[Set[str], Set[str], Dict[str, Any]]`: Parses plain-text PSL syntax, strips wildcards (`*.`) and exception rules (`!`), separates `ICANN DOMAINS` from `PRIVATE DOMAINS`, and computes SHA-256.
- `update_psl_dataset(source, expected_sha256=None, save_to_disk=True) -> Dict[str, Any]`:
  - Validates source (HTTP URL, local file path, or bytes).
  - Verifies SHA-256 integrity when expected hash is provided (rejecting corrupted data).
  - Performs atomic in-memory swap of active suffix sets (`_CUSTOM_ICANN_SUFFIXES`, `_CUSTOM_PRIVATE_SUFFIXES`).
  - Persists verified snapshot to `settings.data_dir / "psl" / "public_suffix_list.dat"`.
  - Fails closed / retains bundled fallback if download or parsing fails.
- `get_psl_dataset_info() -> Dict[str, Any]`: Returns active dataset status, upstream URL, rules counts, and fallback indicator.

### 4.3 Test Verification
Tested in [`tests/test_stage04_review_corrections.py::TestMultiSessionVersionedObservationsAndPSL`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage04_review_corrections.py):
- `test_psl_dataset_info_and_fallback`: **PASSED**.
- `test_psl_dataset_update_and_sha256_verification`: **PASSED** (valid update loads custom suffixes dynamically; mismatched SHA-256 raises `ValueError`).

---

## 5. Multiple Independent Research Sessions Within One Orchestration Run

### 5.1 Architecture & Versioned Observations

In complex research runs, the orchestrator may execute multiple independent research sessions (e.g., preliminary scanning vs deep extraction). If both sessions observe the same URL:
1. The source record in `research_sources` maintains its immutable canonical identity and original run ID (`research_run_id`).
2. When content changes between observations, `research_source_versions` records an incremental version row (`version_number = 2`) with the new content hash ($H_2$), content length, storage path, retrieved timestamp, and the specific session ID (`session_id`).
3. Both versions exist on disk as immutable files (`{source_id}.txt` and `{source_id}_v2.txt`), enabling full auditability.

### 5.2 Cross-Run Isolation & Explicit Reuse Ledger
If another research run (e.g., `run_beta`) seeks to cite a source retrieved during `run_alpha`:
- Direct citation without authorization **fails closed** with `violates research run isolation`.
- To authorize reuse, an operator records an explicit entry in `research_source_reuses` via `reuse_source_in_run()`.
- The origin run provenance (`origin_run_id`) remains unaltered in `research_sources`.

### 5.3 Test Verification
Tested in [`tests/test_stage04_review_corrections.py::TestMultiSessionVersionedObservationsAndPSL`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/tests/test_stage04_review_corrections.py):
- `test_multisession_versioned_observations_same_url_same_run`: **PASSED** (Session 1 records v1, Session 2 records v2 under the same orchestration run; `get_source_versions()` verifies 2 distinct version records and files).
- `test_explicit_cross_run_source_reuse_ledger`: **PASSED** (Unauthorized citation fails; explicit entry in `research_source_reuses` allows citation while preserving immutable origin provenance).

---

## 6. Risk Register Reconciliation (RSK-05 and RSK-06)

In [`docs/remediation/RISK_REGISTER.md`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/RISK_REGISTER.md):
- **RSK-05: Plan Repair Silent Action Conversion (Finding F-01):**
  - **Previous Status:** `Open`
  - **Reconciled Status:** **`Mitigated (Stage 2 Verified)`**
  - **Evidence:** Verified by `tests/test_stage02_planning.py::test_unknown_script_rejected_explicitly_without_substitution` and cycle detection tests. Silent substitutions in `task_graph.py` and `planner.py` have been eliminated and replaced with `PlanValidationError`.
- **RSK-06: Hallucinated Script Execution (Finding F-02):**
  - **Previous Status:** `Open`
  - **Reconciled Status:** **`Mitigated (Stage 2 Verified)`**
  - **Evidence:** Verified by `tests/test_stage02_planning.py::test_unknown_script_rejected_explicitly_without_substitution`. Dynamic capability injection and strict manifest validation prevent hallucinated script names from passing plan validation.

---

## 7. Exact Source Code Changes Made

```diff
diff --git a/app/services/research/public_suffix.py b/app/services/research/public_suffix.py
+UPSTREAM_PSL_URL = "https://publicsuffix.org/list/public_suffix_list.dat"
+_CUSTOM_ICANN_SUFFIXES: Set[str] = set()
+_CUSTOM_PRIVATE_SUFFIXES: Set[str] = set()
+_PSL_DATASET_META: Dict[str, Any] = {...}
+
+def parse_psl_text(text: str) -> Tuple[Set[str], Set[str], Dict[str, Any]]: ...
+def update_psl_dataset(source: Union[str, Path, bytes], expected_sha256: Optional[str] = None, save_to_disk: bool = True) -> Dict[str, Any]: ...
+def get_psl_dataset_info() -> Dict[str, Any]: ...
+
 def parse_registered_domain(url_or_host: str) -> Tuple[str, str, bool]:
+    private_suffixes = _CUSTOM_PRIVATE_SUFFIXES | _PRIVATE_TENANT_SUFFIXES
+    icann_suffixes = _CUSTOM_ICANN_SUFFIXES | _ICANN_PUBLIC_SUFFIXES
     ...

diff --git a/app/services/research/citations.py b/app/services/research/citations.py
@@ -43,4 +43,6 @@ class CitationValidationReport:
+    supported_citations: Optional[List[str]] = None
     contradicted_citations: Optional[List[str]] = None
+    insufficient_evidence_citations: Optional[List[str]] = None
     unverified_citations: Optional[List[str]] = None
@@ -236,8 +236,14 @@ class CitationEngine:
                 entailment = verify_claim_against_source(claim_sentence, src_text)
+                if entailment.status == "SUPPORTED":
+                    supported.append(f"[{idx}]")
-                if entailment.status == "CONTRADICTED":
+                elif entailment.status == "CONTRADICTED":
                     contradicted.append(f"[{idx}]")
+                elif entailment.status == "INSUFFICIENT_EVIDENCE":
+                    insufficient.append(f"[{idx}]")
+                    errors.append(f"INSUFFICIENT_EVIDENCE: ...")
+                elif entailment.status == "UNVERIFIED":
                     unverified.append(f"[{idx}]")

diff --git a/app/services/orchestration/evaluator.py b/app/services/orchestration/evaluator.py
@@ -326,4 +326,9 @@ def evaluate_report(...):
+        insufficient_cits = citation_audit.get("insufficient_evidence") or [
+            c for c in (citation_audit.get("errors") or []) if "INSUFFICIENT_EVIDENCE" in c
+        ]
+        elif insufficient_cits:
+            claims_grounded = False
+            citation_rejection_reason = f"INSUFFICIENT_EVIDENCE: {len(insufficient_cits)} cited assertions lack verifiable evidence in referenced sources"
+            goal_checks.append(_check("citation_insufficient_evidence", False, citation_rejection_reason, layer="goal"))

diff --git a/app/services/research/sources.py b/app/services/research/sources.py
@@ -63,6 +63,7 @@ class SourceLibrary:
         research_run_id: Optional[str] = None,
+        session_id: Optional[str] = None,
     ) -> Dict[str, Any]:
+    def get_source_versions(self, project_id: str, source_id: str) -> List[Dict[str, Any]]: ...

diff --git a/docs/remediation/RISK_REGISTER.md b/docs/remediation/RISK_REGISTER.md
-| **RSK-05** | Semantic | **Plan Repair Silent Action Conversion (F-01)** ... | Open |
-| **RSK-06** | Semantic | **Hallucinated Script Execution (F-02)** ... | Open |
+| **RSK-05** | Semantic | **Plan Repair Silent Action Conversion (F-01)** ... | Mitigated (Stage 2 Verified) |
+| **RSK-06** | Semantic | **Hallucinated Script Execution (F-02)** ... | Mitigated (Stage 2 Verified) |
```

---

## 8. Final Compliance Verification & Stage 5 Hold

### 8.1 Invariant Checklist
- [x] **I-01 (Strict Plan Validity):** Verified; no silent fallbacks.
- [x] **I-02 (Capability Integrity):** Verified; registered scripts only.
- [x] **I-04 (Worker Authorization & Lease Fencing):** Verified in Stage 1.
- [x] **I-07 (Evidence Grounding):** Verified; reports with 0 citations fail closed; claim entailment strictly audited.
- [x] **I-08 (Objective Acceptance):** Verified; all four entailment states tested through deliverable gate.
- [x] **I-10 (Project & Run Isolation):** Verified; source scoping and explicit reuse ledger enforced.
- [x] **I-14 (Fail-Closed Execution):** Verified; un-enforced sandboxes block execution with `SECURE_EXECUTION_UNAVAILABLE`.

### 8.2 Strict Review Hold Before Stage 5
- **Branch:** `feature/unified-artifact-deliverables` remains unmerged into `main`.
- **Daemons / Deployments:** No background daemons or production deployments have been initiated.
- **Next Stage:** Workstream 5 (Continuous Research & Persistent Knowledge Integration) will commence only upon explicit operator authorization.
