# Independent Test Adequacy Review for Broader Software Contracts

**Date:** 2026-10-28  
**Scope:** Architecture and evaluation criteria for independent test suites beyond narrow integer arithmetic.  
**Status:** Review candidate; non-production development primitive.

---

## 1. Executive Summary & Deficiencies of Integer-Only Profiles

Prior increments (14–16) established Linux kernel containment, transient systemd unit scoping, and authenticated black-box HMAC verification using `qc.integer_cases.v1`. While valuable for verifying the mechanics of input mounting (`/run/qc-input.json`) and observation signing, integer arithmetic (`a + b`) suffers from major adequacy limitations:

1. **Trivial Constant Space:** Models can guess or superficially pass by hardcoding arithmetic operators or narrow returns without demonstrating real problem solving.
2. **No Data Structure Coverage:** Real software contracts manipulate structured objects, arrays, nested schemas, strings, nulls, and booleans.
3. **No Error / Boundary Handling:** Integer profiles do not test malformed inputs, missing fields, schema violations, or edge conditions (e.g. empty strings, zero-length arrays, Unicode characters).
4. **No Proof of Adequacy:** Passing a 2–3 case integer test suite does **not** establish software correctness, general model reliability, or release readiness.

---

## 2. Broader Declarative Profile: `qc.json_io_cases.v1`

To support broader software contracts without allowing the generated code to self-evaluate or see oracle answers, we have implemented `qc.json_io_cases.v1`:

### Schema Specification
```json
{
  "schema": "qc.json_io_cases.v1",
  "cases": [
    {
      "id": "case_identifier_alphanumeric",
      "requirement_id": "bound_requirement_id",
      "input": { "any": "valid bounded JSON structure" },
      "expected": { "any": "valid expected JSON output" }
    }
  ]
}
```

### Constraints & Invariants
- **Case Density:** Bounded between 2 and 16 independent cases per deliverable contract.
- **Payload Limits:** Maximum byte size of 32,768 bytes per case input and expected answer.
- **Closed Property Set:** Each case must strictly define `id`, `requirement_id`, `input`, and `expected`.
- **Case ID Format:** Must match `^[a-zA-Z0-9_-]{1,64}$` with no duplicates.
- **Requirement Coverage:** Every approved requirement in `qc.software_contract.v1` must have at least one covering case.
- **Strict Typing:** In `evaluate_observations`, `type(answer) is type(case['expected'])` is strictly enforced to prevent cross-type truthy comparisons (e.g., Python `False == 0`).

---

## 3. Test Adequacy Evaluation Criteria

For any software contract to be certified, the independent test suite must satisfy four mandatory criteria:

### A. Equivalence Partitioning & Boundary Value Analysis
Each requirement must partition the input domain and supply test cases from every partition:
- **Nominal Cases:** Typical, well-formed input structures exercising standard business logic.
- **Extremes & Limits:** Empty strings (`""`), empty collections (`[]`, `{}`), maximum allowed payload bounds.
- **Types & Encodings:** Multi-byte UTF-8, accented characters, special punctuation, numeric boundaries (0, negative integers, floats).

### B. Fault Injection & Negative Assertions
A robust contract must test that the candidate handles invalid inputs deterministically without uncontrolled crashes:
- Missing required keys in `/run/qc-input.json`.
- Incompatible value types (e.g., string where number expected).
- Contracted error responses: candidate must exit cleanly with an error payload rather than crashing with unhandled tracebacks.

### C. Mutation Resistance (Kill Rate > 90%)
To verify that the test suite is genuinely discerning and not superficial:
- **Null Mutation:** Candidate returns empty or null response $\rightarrow$ Must FAIL.
- **Constant Mutation:** Candidate returns a static pre-computed dictionary $\rightarrow$ Must FAIL across diverse inputs.
- **Inversion Mutation:** Inverting logic (e.g. `>` to `<` or addition to subtraction) $\rightarrow$ Must FAIL.

### D. Independent Authorship & Strict Isolation
- **No Self-Grading:** The model generating code must **never** author, inspect, or modify its acceptance tests.
- **No Test Oracle Leakage:** Expected values and test input distributions must never appear in worker prompts or repair feedback.
- **Out-of-Process Evaluation:** Acceptance is evaluated exclusively outside the sandbox by the controller and root verifier.

---

## 4. Empirical Mutation Testing & Kill Rate Verification

To independently verify that the approved test suites reject plausible defective implementations, we executed [`tests/test_independent_test_adequacy_mutations.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective-integration/tests/test_independent_test_adequacy_mutations.py).

Seven deliberate mutant implementations were tested against the declarative test oracle:

| Mutant ID | Deliberate Defect Injected | Expected Behavior | Observed Result | Killed By Case |
| :--- | :--- | :--- | :--- | :--- |
| `MUTANT_CONSTANT_ANSWER` | Hardcodes nominal answer `{"valid": True, "words": 2, "length": 11, "preview": "hello world"}` for all inputs | Reject across non-nominal inputs | **KILLED** | `case_empty` |
| `MUTANT_UNHANDLED_MALFORMED` | Implements nominal logic but crashes with unhandled exception on missing key or wrong type | Reject uncontrolled crash | **KILLED** | `case_malformed_type` |
| `MUTANT_BAD_WHITESPACE` | Counts words via naive character split ignoring variable whitespace (`"  "` counted as words) | Reject incorrect word count | **KILLED** | `case_boundary_spaces` |
| `MUTANT_BYTE_VS_CHAR_UNICODE`| Measures UTF-8 byte length instead of unicode character length | Reject byte-based length mismatch | **KILLED** | `case_unicode` |
| `MUTANT_COERCED_TYPES` | Returns `1` instead of `True`, and floats `2.0` instead of integers `2` | Strict comparator rejects type coercion | **KILLED** | `case_nominal` |
| `MUTANT_FORGED_FLAGS` | Returns `{"passed": True, "status": "OK"}` ignoring contracted schema | Reject invalid schema / missing fields | **KILLED** | `case_nominal` |
| `MUTANT_MISSING_PREVIEW` | Omits the required `preview` string field | Reject missing mandatory field | **KILLED** | `case_nominal` |

**Mutation Score:** 7 of 7 mutants killed (**100.0% kill rate**). The reference implementation passed all 6 cases (6/6).

---

## 5. Operational Gate

Passing `qc.json_io_cases.v1` or broader declarative profiles proves that candidate code satisfies specific declared cases. It does **not** grant production deployment authority or authorize lifting administrative holds. Production release requires operator sign-off on a concrete rollout plan.

