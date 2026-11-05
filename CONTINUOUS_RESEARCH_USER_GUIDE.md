# Continuous Research & Persistent Knowledge User Guide

## 1. Introduction: How Research Works in Qwen Collective

In Qwen Collective, web research and knowledge integration are **continuous, native components of every project**. You never need to manually run a separate research tool and paste results into prompts.

Whenever a project moves through its lifecycle—from intake to planning, task execution, and evaluation—the system automatically:
1. Evaluates what facts are needed for that stage.
2. Checks whether verified answers already exist in the **Project Knowledge Base** or the shared **Collective Memory**.
3. Reuses existing knowledge whenever it is fresh and trustworthy.
4. Searches the internet using authorized research tools when current or missing information is required.
5. Preserves all discoveries with exact source URLs, publishers, excerpts, and timestamps.
6. Injects verified findings directly into task prompts and evaluates deliverables against supporting evidence.

---

## 2. Navigating Project Research & Knowledge

Within any project detail page (`/projects/{project_id}`), select the **Knowledge** tab. You will find:

### A. Knowledge Impact Alert Banner (If Any Assets Affected)
If any previous planning assumption, task, or deliverable in the project relied on a fact that was later contradicted or superseded, a warning banner appears at the top:
> **⚠️ Knowledge Impact Alert:** 2 planning decisions or deliverables rely on knowledge that has been contradicted or superseded.

### B. Continuous Research Activity & Checkpoints
A chronological ledger of every research checkpoint executed for this project:
- **Stage**: Phase where the checkpoint occurred (`Intake`, `Planning`, `Execution`, `Evaluation`).
- **Decision**: Whether the system **Reused Knowledge** or **Executed Search**.
- **Reason & Queries**: Detailed rationale and exact queries submitted.
- **Sources Retrieved**: Clickable links to documents retrieved.
- **Evidence Gaps**: Any remaining unverified questions.

### C. Structured Knowledge & Evidence Base
Canonical, evidence-informed knowledge records:
- **Subject & Statement**: The concise, verified factual assertion.
- **Scope**: `Project` (private to this project) or `Collective` (shared across all projects).
- **Verification Status**: `Verified`, `Supported`, `Corroborated`, `Contradicted`, or `Superseded`.
- **Inspect Evidence**: Click to expand a drawer showing the original URL, publisher, date, verbatim excerpt, and SHA-256 integrity hash.
- **Promote**: Button to propose verified project findings to the collective queue.

### D. Collective Promotion Contributions
Tracks findings from this project that have been proposed, approved, or queued for collective memory.

---

## 3. The Dedicated Knowledge Base (`/knowledge`)

Navigate to `/knowledge` from the top navigation bar to explore the system-wide knowledge center:

### Sub-Views
1. **Records**: Full-text search across all stored knowledge using SQLite FTS5. Filter by scope (`All`, `Collective`, `Project`), type, or verification status.
2. **Promotions**: The collective promotion review queue. Displays proposed findings awaiting approval with quality and privacy check results.
3. **Impacts**: Real-time audit of all project planning decisions, task contexts, and deliverables linked to knowledge records.
4. **Policies**: Active freshness thresholds (e.g., breaking news 1 day, software APIs 14 days, regulations 90 days).
5. **Vault**: File-based markdown narratives and architectural decision records.

---

## 4. Administrative Controls: Reviewing Promotions

Authorized operators can review proposed collective findings:
1. Navigate to `/knowledge?view=promotions`.
2. Review the proposed statement, source excerpt, and privacy check status.
3. Click **Approve** to immediately elevate the record to `scope = 'collective'`, making it available to all present and future projects.
4. Or click **Reject** with an optional rationale if the finding is deemed too project-specific.
