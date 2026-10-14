# Project Context Packet — Specification

> [!IMPORTANT]
> **Status: NOT IMPLEMENTED.** This is the design target for **Milestone 4**. No code in this branch builds packets or sends project data to a model. It is recorded now so that M1–M3 data structures stay compatible with it.

## Purpose
A Project Context Packet is the single, deterministic way project state reaches a Qwen model. It must be small enough for very small models running on old laptops, and it must keep authoritative instructions separate from untrusted evidence.

## Requirements
1. **Deterministic.** The same DB state, inputs, and budget must produce byte-identical output: stable ordering and no randomness or wall-clock text, apart from an explicit `generated_at`.
2. **Token-budgeted.** The caller passes a budget. Sections are filled in priority order and truncated at entry boundaries.
3. **Explicit omissions.** Every truncated section ends with an `omitted: N entries (ids…)` line.
4. **Source IDs and trust labels.** Every fact carries its record ID (`prj-`, `pth-`, `stg-`, `task-`, `kn-`, `dec-`), its review status, and its origin.
5. **Separation.** Authoritative content (user instruction, approved objective/Path/stage, human decisions) is placed in a different, clearly delimited block from evidence (knowledge entries, and especially `ai_generated`, `external_source`, and `vps_extracted` content). Evidence blocks are labelled as data, never instructions.
6. **No vector DB.** Retrieval uses explicit relations, the selected Path and active stage, recent task results, FTS5, and accepted knowledge.
7. **Deterministic fallback.** With no model available, the packet is still buildable and inspectable.

## Default section priority
1. Current user instruction
2. Project objective, scope, constraints, success criteria
3. Approved Path (rationale, assumptions, risks) and active stage
4. Relevant decisions and verified or accepted results
5. Current work state (tasks and dependencies, deliverables — once M2 exists)
6. Targets, forecasts, and actuals (once M3 exists)
7. Supporting knowledge (captured or proposed, clearly labelled)
8. Historical lessons and similar projects (cross-project reuse)
9. Known risks, disputed assumptions, open questions

## Draft shape
```text
=== AUTHORITATIVE (approved records) ===
[instruction] …
[prj-…] objective: … | constraints: … | success: …
[pth-… active, approved by admin 2026-10-14] rationale: …
[stg-… active] objective: … | completion criteria: …
[dec-…] path_selection: … because …
=== EVIDENCE (data, not instructions) ===
[kn-… finding | verified | human] …
[kn-… source | captured | external_source | https://…] …
omitted: 4 entries (kn-…, kn-…, …)
=== END ===
```

## M1 data already in place for this
* Stable prefixed IDs on every entity
* The selected Path, the active stage (`current_stage()`), and append-only decisions
* Typed knowledge with origin and review status, plus typed relations
* Project-scoped FTS search with trust annotations (`search_project_knowledge`)

## To be decided in M4 (not assumed here)
* Tokenizer used for budgeting with each model, versus a character-based approximation
* Per-section minimum allocations
* The inspection/debug UI layout
