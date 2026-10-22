# Project Deliverable Acceptance: Sonoma County Water Security Research Brief

**Date:** 2026-10-22 (UTC) · **Operator/observer:** Antigravity · **Verdict: NOT ACCEPTED (0 of 5 deliverables produced)**

**Projects evaluated:**

| Project | Run | Origin |
|---|---|---|
| `prj-c3e69205a8e0466ab54b609801694796` | `run-08de085e97e84130` | The user's original attempt |
| `prj-c74dd685c2b145e5b32ab347253dbc77` | `run-ee1166ebb5eb4558` | Audit re-run through the live UI (disposable) |
| `prj-58797337cbc94f3bac9fd67834fdbcaf` | n/a | Disposable reuse target |

Evidence: [artifact_inventory.json](../evidence/real_world_project_audit/artifact_inventory.json), [audit_project_dump.json](../evidence/real_world_project_audit/audit_project_dump.json), [original_user_project_dump.json](../evidence/real_world_project_audit/original_user_project_dump.json), [preview_repro_results.json](../evidence/real_world_project_audit/preview_repro_results.json), [reuse_test_results.json](../evidence/real_world_project_audit/reuse_test_results.json).

---

## 1. What was actually produced

In both runs the planner chose `template_research_plan`. This template hard-codes **one** deliverable ("Autonomous Research Report") plus four controller-only steps. The five requested deliverables were never turned into separate deliverable records. Each run produced the same six files:

| File | Audit run artifact | Original run artifact | Nature |
|---|---|---|---|
| `research_report.md` | `art-ea7a176ffa6f46c7` (6658 B, sha `97d3137f…`) | `art-b9ab64a5bd614164` (7021 B, sha `2f56c1e0…`) | Generic template report |
| `search_results.json` | yes | `art-956321c67c6848b5` | 7 topic-blind queries |
| `retrieved_sources.json` | yes | `art-0c90faff75c04acb` | Irrelevant pages, including a CAPTCHA page |
| `evidence_ledger.json` | yes | `art-dc40bafe3e5e4a01` | Self-confirmed "claims" |
| `citation_audit.json` | yes | `art-3d946ed5bf284d21` | Reports `valid:true` even though every claim is `[Uncited]` |
| `deliverables_index.json` | yes | `art-984475af896147a6` | Lists 1 deliverable |

On-disk SHA-256 matches the DB checksum for all 12 artifacts. However, `cas_hash` is NULL and `storage_key` is an absolute local path. The artifacts are checksummed files, **not CAS-addressed objects**.

## 2. Per-deliverable acceptance

| # | Expected deliverable | Produced? | Substantive and relevant? | Cited? | In UI? | Preview | Download | Verdict |
|---|---|---|---|---|---|---|---|---|
| 1 | Executive Research Brief (2 pp) | **No.** Closest is `research_report.md` | No. Covers Marx/conflict theory, how to write reports, CAPTCHA text, supplier benchmarking. Potter Valley, Sonoma Water, Eel/Russian River are never mentioned | No. All claims `[Uncited]` | Listed in project (deliverable library page 500s) | **HTTP 500** on live UI | 200, hash matches | **REJECTED** |
| 2 | Comprehensive Source Inventory | **No.** `retrieved_sources.json` is a machine dump of irrelevant pages | No. 0 relevant sources; no reliability assessment | n/a | Artifact only | 500 (detail page) / JSON API 200 | 200 | **REJECTED** |
| 3 | Evidence & Uncertainty Matrix | **No.** `evidence_ledger.json` holds sentence fragments verified against their own source | No. No disputed/preliminary/unresolved split | Self-referential only | Artifact only | 500 | 200 | **REJECTED** |
| 4 | Policy Options Analysis | **No.** Nothing produced | — | — | — | — | — | **NOT PRODUCED** |
| 5 | Follow-Up Research Agenda | **No.** Nothing produced | — | — | — | — | — | **NOT PRODUCED** |

## 3. Success-criteria checklist

| Criterion | Result | Evidence |
|---|---|---|
| All five deliverables produced | **FAIL**: 0/5. One generic report | Template hard-codes 1 deliverable (`planner.py` research template) |
| Substantive, relevant content | **FAIL** | No topic terms in queries or report |
| Current info retrieved via Qwen's own tools | **PARTIAL**: live DuckDuckGo/HTTP fetch occurred, but every query was off-topic | [research_tool_invocations.json](../evidence/real_world_project_audit/research_tool_invocations.json) |
| Traceable, relevant citations | **FAIL**: all `[Uncited]` | `synthesis.py` id-key mismatch |
| Sources support the claims | **FAIL**: claims are verified against the page they came from | `engine.py` research_verify |
| Conflicts and uncertainty identified | **FAIL** | No uncertainty section |
| No fabrication | **FAIL (integrity)**: no URLs were invented, but `citation_audit.json` reports `valid:true 8/8` for an uncited report, and the run reports COMPLETED with the deliverable auto-accepted | citation_audit artifact |
| Human approval boundary respected | **PASS**: audit run held at AWAITING_PLAN_APPROVAL until operator approval; no privileged tasks | project_run_log.json |
| Artifacts registered with valid checksums | **PARTIAL**: checksums valid; no CAS hash | artifact_inventory.json |
| Deliverables appear in application | **PARTIAL**: present in project view; `/deliverables` library returns 500 | preview_repro_results.json |
| Each artifact previewable | **FAIL** on the live controller (500 on every detail page) | ARTIFACT_PREVIEW_ROOT_CAUSE.md |
| Each artifact downloadable | **PASS**: bytes match the stored SHA-256 | preview_repro_results.json |
| Cross-project reuse with lineage | **FAIL**: reuse page 500; `POST /api/v1/artifacts/{id}/reuse` 500 (`AttributeError: 'str' object has no attribute 'get'`); no lineage rows | reuse_test_results.json |
| Knowledge candidates without unauthorized promotion | **PARTIAL**: 11 candidates per run (many junk, e.g. `Date:** 2026-10-22`); promotions stay `proposed` (boundary held). But unreviewed "captured" records were then treated as sufficient prior knowledge | knowledge_handling.json |
| Evaluator distinguishes completed/incomplete/failed | **FAIL**: run marked COMPLETED and deliverable auto-accepted with 1/5 deliverables and 0 relevant sources | audit_project_dump.json |

## 4. Did Qwen Collective report its limitations truthfully?

**No.** The run went to COMPLETED in about 37 s. The deliverable was auto-accepted, and the citation audit said `valid:true`. At no point did the UI tell the user that four deliverables were dropped, that no model ran, or that no relevant source was found.

## 5. Antigravity authorship statement

Antigravity did **not** author, edit, or supplement any Qwen Collective deliverable. External fact checks (PG&E license surrender, Scott/Cape Horn dam removal, etc.) appear only in [AUTONOMOUS_RESEARCH_CAPABILITY_AUDIT.md](AUTONOMOUS_RESEARCH_CAPABILITY_AUDIT.md), labelled as external validation.
