# Continuous Research & Persistent Knowledge Architecture

## 1. Executive Summary & Architectural Mission

The Qwen Collective continuous research architecture transforms the system from an episodic task executor into an **evidence-informed, continuously learning distributed execution network**. In this paradigm:

1. **Every project lifecycle stage** executes a mandatory, auditable **Research Checkpoint** before advancing.
2. The system deterministically evaluates existing knowledge before initiating any external web retrieval.
3. Information discovery is not isolated to an initial "planning report"; findings actively inform prompt assembly, task constraints, acceptance criteria, deliverable verification, and replanning.
4. Discoveries retain full cryptographic and source provenance (canonical URLs, publishers, timestamps, raw excerpts, and content hashes).
5. Two logically distinct knowledge scopes ensure that confidential project details remain strictly isolated, while verified, generalizable technical findings can be formally elevated to the **Collective Knowledge Base**.
6. **Knowledge-Impact Tracking** records precisely which decisions, tasks, and deliverables relied on each piece of knowledge, automatically flagging downstream assets if evidence is later contradicted or superseded.

```
       PROJECT OR STAGE BOUNDARY
                  │
                  ▼
       DEFINE INFORMATION NEEDS
                  │
                  ▼
       SEARCH PROJECT KNOWLEDGE
                  │
                  ▼
      SEARCH COLLECTIVE KNOWLEDGE
                  │
                  ▼
      ASSESS EVIDENCE & FRESHNESS
                  │
        ┌─────────┴─────────┐
        ▼                   ▼
 [Needs Research]    [Knowledge Fresh]
        │                   │
        ▼                   │
 AUTHORIZED WEB SEARCH      │
        │                   │
        ▼                   │
 SOURCE VERIFICATION        │
        │                   │
        ▼                   │
 INTEGRATE KNOWLEDGE        │
        │                   │
        └─────────┬─────────┘
                  ▼
      ASSEMBLE BOUNDED CONTEXT
                  │
                  ▼
        EXECUTE STAGE WORK
                  │
                  ▼
      RECORD KNOWLEDGE IMPACTS
                  │
                  ▼
        STAGE EVALUATION GATE
```

---

## 2. Dual-Scope Knowledge Architecture

The architecture enforces strict boundary isolation between two knowledge scopes:

### A. Project-Scoped Knowledge Base
- **Scope**: Private to a specific `project_id`.
- **Contents**: Project-specific assumptions, domain requirements, extracted web findings, trade-offs, architecture decisions, and task observations.
- **Isolation Guarantee**: Queries performed in Project B can never inspect or retrieve Project A's project-scoped records. Foreign keys guarantee cascading cleanup if a project is deleted.

### B. Collective-Scoped Knowledge Base
- **Scope**: Shared across all collective projects (`scope = 'collective'`).
- **Contents**: Promoted, verified technical facts, standard library behaviors, API characteristics, historical lessons learned, and reusable benchmarks.
- **Access Policy**: Readily retrievable by any project during research checkpoints and planning context assembly.

---

## 3. Mandatory Stage Research Checkpoints

Research checkpoints are deterministic workflow gates executed at every stage boundary:

| Stage Phase | Information Need Assessed | Knowledge Evaluation | Decision Logic |
|---|---|---|---|
| **Intake** | Project objectives, domain entities, technological dependencies. | Searches project & collective memory for matching topics. | Executes web search if unfamiliar or current specs needed; synthesizes initial context. |
| **Planning** | Technology versions, API capabilities, external constraints. | Evaluates freshness of intake findings. | Reuses existing verified records; avoids redundant searching. Enriches planning prompt with verified facts. |
| **Execution** | Detailed task requirements, command flags, library syntax. | Checks whether dependent knowledge is fresh and uncontroverted. | Supplies bounded, evidence-linked `<data source="verified_knowledge_base">` packet to worker. |
| **Evaluation** | Material factual claims produced in deliverables. | Compares claims against source excerpts in the knowledge base. | Verifies citations. Unsupported claims block deliverable acceptance. |
| **Replanning** | Root causes of task failures or environmental drift. | Identifies invalidated assumptions. | Executes targeted investigation of alternative approaches. |
| **Final Reporting** | Comprehensive deliverable synthesis & lessons learned. | Verifies all citations and assesses policy-eligible findings for collective promotion. | Submits generalizable discoveries to the collective promotion queue. |

---

## 4. Knowledge-Impact Tracking Architecture

A critical innovation of this architecture is **Knowledge-Impact Tracking** (`knowledge_impacts`):
Rather than treating knowledge as a passive archive, the system tracks every point of reliance:

- **Planning Decisions**: When the planner adopts an assumption based on a knowledge record, an impact row links `knowledge_id` to `planning_assumption`.
- **Task Executions**: When a task's inference prompt incorporates a knowledge record, an impact row links `knowledge_id` to `task_id`.
- **Deliverables**: When a deliverable cites a factual assertion, an impact row links `knowledge_id` to `deliverable_id`.

### Contradiction and Supersession Propagation
When a newer source invalidates an older claim:
1. `record_contradiction()` or `supersede_knowledge_record()` updates the target record status to `contradicted` or `outdated`.
2. `flag_affected_impacts()` triggers immediately across the entire database.
3. Every dependent plan, task, or deliverable is marked `is_affected = 1` with a human-readable `affected_reason`.
4. The Project Orchestrator and project UI immediately display prominent warning banners, and the evaluator flags affected deliverables for re-review.

---

## 5. Security & Least-Privilege Execution Model

- **Worker Sandboxing**: Worker nodes (`qwen-vps`, `qwen-laptop`) operate with network restriction. Sandboxed tasks cannot initiate arbitrary, unmonitored external network connections.
- **Centralized Research Tools**: All search operations route through registered, authorized search providers (SearXNG, DuckDuckGo, secure fetcher) running on `controller-local`.
- **Data vs Instruction Delimitation**: Retrieved web data is treated as untrusted. When assembled into inference prompts by `ContextManager`, facts are isolated within `<data source="verified_knowledge_base">` blocks with explicit instructions preventing model prompt injections from overriding orchestration logic.
