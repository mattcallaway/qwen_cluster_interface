# Knowledge Promotion Policy & Governance

## 1. Overview & Core Principles

The Collective Knowledge Base is a shared repository of validated technical facts, library behaviors, historical lessons learned, and procedural guidance accessible to all projects within Qwen Collective.

To prevent pollution, confidential leakage, or unverified claims entering the shared repository, a formal promotion governance workflow is enforced.

```
       PROJECT DISCOVERY
              │
              ▼
   1. SOURCE & EVIDENCE VALIDATION
              │
              ▼
   2. REUSABILITY ASSESSMENT
              │
              ▼
   3. PRIVACY & CONFIDENTIALITY CHECK
              │
              ▼
   4. PROMOTION ELIGIBILITY DECISION
        ┌─────┴─────┐
        ▼           ▼
   [Automatic]   [Manual Review]
        │           │
        ▼           ▼
   COLLECTIVE   PROMOTION QUEUE
     MEMORY     (Human Sign-off)
```

---

## 2. Epistemological State Hierarchy

Knowledge records must pass through explicit, non-interchangeable states:

1. **Discovered**: Raw page content or document encountered in an external source.
2. **Extracted**: Discrete factual claim parsed from the source text.
3. **Supported**: Claim associated with a direct, verifiable excerpt in `research_evidence`.
4. **Corroborated**: Independent supporting evidence identified from multiple distinct sources.
5. **Accepted**: Formally verified and adopted by a project for planning or deliverables.
6. **Promoted**: Formally evaluated and elevated to `scope = 'collective'` for cross-project reuse.

---

## 3. Eligibility Criteria for Promotion

A project knowledge record is eligible for collective promotion only if it satisfies **all** of the following requirements:

1. **Generalizable Utility**: The finding represents a standard software capability, API behavior, algorithm characteristic, or operational lesson learned—not project-private data.
2. **Verification Standard**: The record must possess `verification_status` of `supported` or `corroborated`. Unverified hypotheses and estimates are ineligible.
3. **Traceable Provenance**: The record must retain at least one verifiable source ID referencing an authentic URL and document excerpt.
4. **Privacy Clearance**: The record statement, summary, and excerpt must pass regex and heuristic checks ensuring no sensitive strings, API keys, credentials, internal IPs, or confidential identifiers are present.

---

## 4. Automatic vs. Manual Promotion Policies

### A. Conservative Automatic Promotion
A record is automatically promoted (`status = 'approved'`, `reviewer = 'system_auto_promotion'`) if and only if:
- Knowledge type is `fact` or `procedure`.
- Verification status is `corroborated`.
- Confidence assessment is `high` or `verified`.
- At least **two** independent source references are documented.
- Zero privacy or confidentiality indicators are flagged.

### B. Manual Promotion Queue
Any record that is proposed but does not satisfy automatic criteria is queued in `knowledge_promotions` (`status = 'proposed'`).
- High-level architecture decisions.
- Single-source technical findings.
- Project retrospectives and strategic lessons learned.
- Content flagged with ambiguous domain terminology.

An authorized operator must review the proposal in `/knowledge?view=promotions` and supply a recorded decision and rationale.

---

## 5. Privacy & Data Protection Safeguards

The promotion engine applies strict token scanning (`_PRIVATE_INDICATORS`) preventing any record containing terms such as:
- `password`, `secret`, `api_key`, `token`, `credential`
- `internal_ip`, `192.168.`, `10.`, `172.16.`
- `confidential`, `proprietary`, `private_key`

If any indicator is detected, `evaluate_promotion_eligibility()` immediately marks `eligible = False`, halting promotion.

---

## 6. Supersession & Demotion

Promoting a finding does not imply that it is permanently true. When newer documentation invalidates a collective record:
1. An operator or stage checkpoint supersedes the record (`verification_status = 'outdated'`).
2. The record remains in the database with historical provenance but is excluded from active planning queries.
3. All projects that relied on the record receive immediate impact alerts.
