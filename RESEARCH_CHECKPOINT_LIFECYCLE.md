# Research Checkpoint Lifecycle Specification

## 1. Overview & Core Philosophy

The **Research Checkpoint** is a mandatory, auditable workflow gate implemented at every meaningful project stage boundary. Its governing philosophy is:

> **Continuously evaluate what we know, determine what we need to know, acquire trustworthy information, and incorporate it into ongoing work.**

Research checkpoints are deterministic, idempotent, and state-machine integrated. A stage cannot advance without recording a checkpoint evaluation.

---

## 2. Checkpoint Lifecycle Flow

```text
               STAGE ENTRY
                    │
                    ▼
       1. Define Information Needs
                    │
                    ▼
      2. Query Project Knowledge (Private)
                    │
                    ▼
     3. Query Collective Knowledge (Shared)
                    │
                    ▼
     4. Assess Evidence Quality & Freshness
                    │
         ┌──────────┴──────────┐
         ▼                     ▼
   [Needs Research]     [Knowledge Sufficient]
         │                     │
         ▼                     │
 5. External Web Search        │
         │                     │
         ▼                     │
 6. Source Fetch & Extract     │
         │                     │
         ▼                     │
 7. Verify Claims & Citations  │
         │                     │
         ▼                     │
 8. Integrate Knowledge Records│
         │                     │
         └──────────┬──────────┘
                    ▼
       9. Assemble Stage Context
                    │
                    ▼
          10. Execute Stage Work
                    │
                    ▼
        11. Record Knowledge Impacts
                    │
                    ▼
         12. Evaluate Deliverables
                    │
                    ▼
      13. Finalize Stage Checkpoint
                    │
                    ▼
             NEXT STAGE GATE
```

---

## 3. Durable Orchestration Events

Every checkpoint emits durable, immutable events recorded in the `orchestration_events` audit ledger:

| Event Identifier | Trigger Condition | Payload / Detail |
|---|---|---|
| `research_check_started` | Stage boundary entry. | Stage phase, project ID, objective context. |
| `knowledge_search_completed` | Local & collective KB search completes. | Number of matching records, count of fresh vs stale records. |
| `research_not_required` | Existing verified knowledge is sufficient. | Explicit rationale explaining why external search was skipped. |
| `research_required` | Required evidence is missing, stale, or contradictory. | Stated justification and targeted query strings. |
| `external_search_started` | Authorized search provider is dispatched. | Provider name, query strings, max documents budget. |
| `external_search_completed` | Search provider returns search candidates. | Candidate count, provider latency, response status. |
| `source_retrieved` | Source pages fetched, parsed, and extracted. | URL, canonical URL, SHA-256 hash, title, character count. |
| `claim_verified` | Factual assertions corroborated by evidence excerpts. | Claim text, category, confidence score, source reference. |
| `knowledge_integrated` | New structured knowledge records created in KB. | Count of records created, topic tags, project scope. |
| `knowledge_conflict_detected`| New evidence contradicts existing records. | Conflicting IDs, contradiction details, affected asset count. |
| `knowledge_promoted` | Policy-eligible finding proposed/approved for collective memory. | Promotion ID, promotion policy, reviewer signature. |
| `research_check_completed` | Checkpoint workflow fully completed. | Total records available, sources retrieved, execution time. |

---

## 4. Phase-Specific Checkpoint Behavior

### Phase A — Project Intake
- **Goal**: Establish the factual foundation and technological constraints before planning begins.
- **Action**: Parses objective, domain keywords, and unfamiliar dependencies. Searches collective knowledge. If unfamiliar or current standards are referenced, executes an authorized web search to ground initial assumptions.
- **Output**: Preliminary research context containing verified facts and source references.

### Phase B — Planning
- **Goal**: Prevent planning models from hallucinating nonexistent APIs or obsolete patterns.
- **Action**: Evaluates intake findings. If fresh, avoids redundant web searching. Injects verified facts into planning prompts.
- **Output**: Records `knowledge_impacts` linking adopted planning assumptions directly to knowledge record IDs.

### Phase C — Execution
- **Goal**: Provide assigned workers with bounded, evidence-linked technical context.
- **Action**: Validates that library APIs or external endpoints required by the task are uncontroverted. Assembles `<data source="verified_knowledge_base">` blocks inside prompt context.
- **Output**: Worker receives accurate parameters, avoiding trial-and-error hallucinations.

### Phase D — Evaluation
- **Goal**: Factual claims produced in deliverables must have traceable evidence.
- **Action**: Checks deliverables against source citations. Identifies uncorroborated assertions.
- **Output**: Flags unsupported claims; prevents deliverable sign-off until claims are verified.

### Phase E — Replanning
- **Goal**: Recover from task failure or external environmental drift.
- **Action**: Identifies which specific planning assumptions were invalidated. Executes targeted web research on alternatives.
- **Output**: Formulates evidence-backed revised plan while archiving original plan history.

### Phase F — Final Reporting
- **Goal**: Synthesize verified project deliverables and contribute generalizable knowledge.
- **Action**: Performs final citation audit. Discovers reusable technical findings eligible for collective promotion.
- **Output**: Submits promotion proposals to `knowledge_promotions` queue.

---

## 5. Fault Tolerance & Degraded Mode

- **Search Provider Unavailable**: If DuckDuckGo or SearXNG times out or returns an error, the checkpoint logs `research_degraded` and proceeds using existing verified knowledge.
- **Network Disconnection**: Controller automatically flags the checkpoint status as `degraded` rather than crashing the orchestration run.
- **Idempotency**: Checkpoints are keyed by `(project_id, stage_phase, run_id)`. Controller restarts safely re-read completed checkpoints from the database without duplicating searches or knowledge records.
