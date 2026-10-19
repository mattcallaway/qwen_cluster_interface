# Qwen Collective — Artifact & Knowledge Base Integration

## 1. Overview

In Qwen Collective, finished work is not allowed to sit dormant as inert files. The **Deliverable-to-Knowledge Pipeline** bridges the gap between raw file outputs and structured cognitive memory, ensuring that valuable empirical findings, architecture decisions, benchmarks, and research conclusions are actively digested, indexed, and made available to inform future planning.

---

## 2. The Extraction Lifecycle

```
[Deliverable Accepted]
          │
          ▼
[Extract NLP Findings] ──► Concepts, Metrics, Key Claims, Assertions
          │
          ▼
[Filter & Deduplicate] ──► Deduplicate against existing project knowledge
          │
          ▼
[Store Project Knowledge] ──► Linked via artifact_knowledge_links
          │
          ▼
[Evaluate Promotion] ──► Policy, quality assessment, privacy check
          │
          ▼
[Collective Knowledge Base] (Optional, human-governed or policy-approved)
```

### Knowledge States of an Artifact
Every artifact in the `artifacts` table maintains an explicit `knowledge_status`:
- `not_processed`: Output has not undergone semantic extraction.
- `processing`: Natural-language parser or model extractor is actively evaluating content.
- `extracted`: Concept claims have been extracted and registered into the project knowledge store.
- `needs_verification`: Extracted assertions require human review or empirical cross-checking.
- `verified`: Claims substantiated by test execution or secondary validation.
- `promotion_eligible`: Clean, non-private finding ready for collective promotion.
- `promoted`: Incorporated into the global collective intelligence base.
- `rejected`: Extraction failed quality checks or contained spurious information.

---

## 3. Dual-Scope Knowledge Architecture

The system maintains a strict separation between **Project-Scoped** and **Collective-Scoped** intelligence:

| Dimension | Project Knowledge | Collective Knowledge |
|---|---|---|
| **Scope** | Confined to the originating project (`scope = 'project'`) | Global intelligence accessible by all projects (`scope = 'collective'`) |
| **Privacy** | May contain project-specific details, names, internal schemas | Stripped of credentials, tokens, proprietary secrets, and private IDs |
| **Ingestion** | Automatic upon Deliverable acceptance | Governed by promotion policies and human review |
| **Provenance** | Direct link to specific artifact version (`artifact_version_number`) | Retains originating project and source artifact audit hash |

### Promotion Governance & Privacy Safeguards
Automatic promotion is conservatively restricted. Before any knowledge record can be promoted:
1. **Automated Secret Screening**: Scans statements against `_PRIVATE_INDICATORS` regex (`password`, `secret`, `token`, `internal_ip`, `credential`, `private_key`).
2. **Quality & Evidence Threshold**: Must possess high confidence, verified status, and supporting evidence references.
3. **Audit Trail**: Every promotion creates a record in `knowledge_promotions` tracking reviewer identity, approval timestamp, and justification rationale.

---

## 4. Provenance Tracing & Retraction Management

Knowledge items retain bidirectional links to their source artifacts:
```
Knowledge Record (know-123)
      │
      ├── source_artifact_id: art-456
      ├── artifact_version: 2
      └── sha256: 8a7f9b2c...
```

### Impact Tracking (`knowledge_impacts`)
When another project references a knowledge item during its planning phase or task execution, an impact record is logged.
If the originating artifact is later found to be flawed, revised, or retracted:
1. The source knowledge record is flagged as `superseded` or `contradicted`.
2. `flag_affected_impacts()` identifies all downstream planning decisions and active tasks that relied upon it.
3. Downstream project managers receive an alert in the project interface, prompting a targeted re-evaluation without silent historical rewrites.
