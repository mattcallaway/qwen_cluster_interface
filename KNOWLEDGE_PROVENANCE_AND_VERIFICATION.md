# Knowledge Provenance & Verification Specification

## 1. Provenance Requirements

Every research-derived claim in Qwen Collective maintains complete, unbroken traceability back to its originating primary source. No factual statement is stored or elevated without supporting metadata:

- **Original Source URL**: The exact HTTP/HTTPS endpoint queried.
- **Canonical URL**: The normalized, tracking-parameter-stripped canonical address.
- **Source Title & Publisher**: Extracted HTML title tag and domain author or organization.
- **Retrieval Timestamp**: ISO 8601 UTC timestamp of retrieval.
- **Supporting Excerpt**: The bounded, verbatim passage from which the assertion was extracted.
- **Cryptographic Content Hash**: SHA-256 hash of the extracted document body ensuring source integrity.
- **Task & Checkpoint Association**: Foreign key links to the originating `stage_research_checkpoint` and project.

---

## 2. Verification Levels & Methods

### Verification Levels
| Status | Meaning |
|---|---|
| `unverified` | Extracted from a source but lacking corroboration or formal review. |
| `supported` | Direct, exact quote or verifiable excerpt linked in `research_evidence`. |
| `corroborated` | Confirmed independently across multiple authoritative domains. |
| `contradicted` | Material conflict identified between this record and another authoritative record. |
| `outdated` | Superseded by newer API version documentation or statutory updates. |
| `insufficient_evidence` | Claim evaluated during stage verification but lacked verifiable supporting text. |

### Verification Methods
1. **Deterministic Excerpt Alignment**: Verifies that the extracted assertion directly matches or accurately paraphrases the bounded text excerpt stored in `research_evidence`.
2. **Multi-Source Corroboration**: Cross-references findings across distinct domains (e.g., official docs + GitHub releases).
3. **Formal Operator Audit**: Authorized human review recording an explicit rationale and timestamp.

---

## 3. Contradiction & Supersession Architecture

When new evidence conflicts with established knowledge:
1. **Never Silently Overwrite**: Both historical and new findings are preserved in the knowledge base.
2. **Explicit Contradiction Link**: The conflicting record points to the earlier record via `contradicts` or `superseded_by`.
3. **Impact Flagging**: `flag_affected_impacts()` identifies all tasks, plans, and deliverables that previously depended on the invalidated claim and marks them as affected.
4. **Auditability**: Evaluators can inspect the exact divergence in the Evidence Inspection UI.

---

## 4. Evidence Inspection in the User Interface

Users can inspect provenance anywhere a knowledge record appears:
- Clicking **Inspect Evidence** expands a structured drawer displaying:
  - Direct clickable link to the source.
  - Document title and publisher.
  - Exact supporting passage excerpt.
  - SHA-256 hash verifying that the source text has not mutated.
  - Verification method and historical timestamp.
