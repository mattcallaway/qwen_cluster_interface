# Autonomous Research Capability Audit

**Scope:** The Sonoma County Water Security project. Two runs were examined: the user's original run (`prj-c3e69205…`, run `run-08de085e97e84130`) and an identical re-run through the live UI during this audit (`prj-c74dd685…`, run `run-ee1166ebb5eb4558`, evidence dir `orch_run-ee1166ebb5eb4558`).
**Evidence:** `research_tool_invocations.json`, `original_user_project_dump.json`, `audit_project_dump.json`, and the run evidence directories under `~/.qwen-collective/data/evidence/`.

---

## 1. Verdict

| Capability | Status | Classification |
| :--- | :--- | :--- |
| Live web search invocation | **Occurs.** Real DuckDuckGo requests: 7 per run, 25–30 results | Works mechanically |
| Topic-relevant query formulation | **Fails.** 0 of 6 execution queries contain "Potter Valley", "Sonoma", "water", "Eel", "PG&E", or "FERC" | **CONFIRMED DEFECT** |
| Page retrieval / extraction | Partial. httpx fetch works; the headless-browser fallback fails on every attempt | CONFIRMED DEFECT + CONFIGURATION ISSUE |
| Anti-bot / CAPTCHA detection | **Fails.** "Checking your browser - reCAPTCHA" pages are ingested as sources | CONFIRMED DEFECT |
| Claim extraction | Extracts arbitrary sentences, including boilerplate ("A required part of this site couldn't load.") | CONFIRMED DEFECT |
| Claim verification | **Self-confirming.** Each claim is "verified" against the same page it was extracted from. Topic relevance is never checked (`relevance_score` hard 0.9, `is_heuristic=1`) | CONFIRMED DEFECT |
| Inline citations | **None.** Every claim row renders `[Uncited]`, because synthesis matches `s["id"]` against an evidence `source_id` from a different key space | CONFIRMED DEFECT |
| Citation audit | Reported `valid: true, 8/8 resolved` while every claim was uncited | CONFIRMED DEFECT (live/stale code) — see §5 |
| Uncertainty / limitation reporting | **Fails.** "Limitations" lists only generic budget text. It never states that zero relevant sources were found | CONFIRMED DEFECT |
| LLM-based analysis or synthesis | **Not performed.** 0 model calls in either run. The report is assembled by string templates | CAPABILITY NOT IMPLEMENTED for the research path |
| Multi-deliverable research output | Not supported. The research template hard-codes one "Autonomous Research Report" | CAPABILITY NOT IMPLEMENTED |

**Net result:** genuine web searching occurred. However, it searched for the wrong things, verified irrelevant text against itself, and reported success.

---

## 2. Query formulation: root cause (reproduced deterministically)

`app/services/research/planner.py`:

- `_extract_main_question` turns the objective into `"What are the key facts, developments, and findings regarding Conduct a comprehensive, evidence-based investigation into …?"`.
- `_generate_queries` keeps only the **first 7 non-stopword tokens** of that question, so the topic words (token ~15+) are truncated away.
- `_generate_subquestions` returns 3 of 4 subquestions that are **fixed generic strings** with no topic words: "competing technologies…", "quantitative benchmarks…", and "limitations, criticisms…". The fourth uses only `objective[:60]`, which is "Conduct a comprehensive, evidence-based investigation into ".

Executing `ResearchPlanner().generate_strategy(objective)` offline produced exactly the queries recorded in the live DB:

| # | Query sent to DuckDuckGo (live DB, both runs) | Relevant? |
| :--- | :--- | :--- |
| 0 (intake checkpoint) | `Conduct a comprehensive, evidence-based investigation into the current status of` | No |
| 1 | `developments findings Conduct a comprehensive evidence based` | No |
| 2 | `technical industry state Conduct a comprehensive` | No |
| 3 | `competing technologies approaches key figures` | No |
| 4 | `quantitative benchmarks costs verified statistics exist` | No |
| 5 | `limitations criticisms conflicting perspectives` | No |
| 6 | `developments findings Conduct a official documentation report` | No |

Result titles included "Evidence-Based Research Series", "Technical Audit 101", "U.S.-China Competition in Emerging Technologies", "Supplier Price Benchmarking", and "Conflict Theory in Sociology".

**Scope:** this affects every objective whose topic words do not appear within the first ~7 content words. That covers most naturally phrased research requests.

## 3. Retrieval

- 7–8 sources were "retrieved" per run, and the document budget was reached at 10 fetches.
- Keyword scan of the extracted text of the audit run's sources: **0** mentions of "Potter Valley", "Sonoma", "Eel River", "Russian River", "PG&E", "FERC", or "Lake Mendocino". One source mentioned "water" twice, incidentally.
- **Headless browser fallback failed on every attempt:** `BrowserType.launch: Executable doesn't exist at C:\Windows\system32\config\systemprofile\AppData\Local\ms-playwright\chromium_…`. The service runs as **LocalSystem**, whose profile has no Playwright browser install. JS-rendered pages (sciencedirect, springer, pmc) therefore produced anti-bot shells.
  - Classification: CONFIGURATION ISSUE (service account) combined with CONFIRMED DEFECT (the failure is logged, but the anti-bot shell is still accepted as a source).

## 4. Verification and evidence quality (sampled from the user's run)

| Claim (as reported) | Reported status | Actual |
| :--- | :--- | :--- |
| "Checking your browser before accessing pmc." | SUPPORTED (0.8) | CAPTCHA interstitial text |
| "A required part of this site couldn't load." | SUPPORTED | Error boilerplate |
| "Conflict theory is most commonly associated with … Karl Marx" | SUPPORTED | True but irrelevant |
| "In today's competitive manufacturing landscape…" | SUPPORTED | Marketing copy, irrelevant |
| "An investigation without a clear objective drifts." | SUPPORTED | Generic how-to text |

No significant factual claim about the Potter Valley Project, Sonoma Water, or water supply exists anywhere in either report. There was therefore nothing to independently verify. **Invented URLs:** none found; every cited URL was actually retrieved. The defect is **irrelevance**, not fabrication.

## 5. Citation audit discrepancy (stale-runtime effect)

The live run wrote `citation_audit.json = {"valid": true, "total_citations": 8, "resolved_citations": 8}`. Running the **current on-disk** `extract_citation_indices` on the same report returns `[]` (zero inline markers). Under the current code, this would trigger `MISSING_REQUIRED_CITATIONS` and fail the deliverable.

The live process (started 2026-10-18) is therefore running an older citation validator that counted the reference-list entries. This is **PROBABLE**: the in-memory code cannot be read without restarting the service. Even with the newer validator, the root query defect would remain. The run would then fail truthfully instead of passing falsely.

## 6. Knowledge integration

- At intake, the research checkpoint wrote **6 knowledge records** from the irrelevant pages. Examples: "An investigation without a clear objective drifts." and "Investigation Report Example: How to Write…". They were written with `verification_status = supported`, `verification_method = deterministic`, and `trust_tier = captured`.
- At project completion, 5 more records were "extracted" from the report itself, with statements like `"Date:** 2026-10-22"` and `"1. Executive Summary"`, all marked `supported`.
- **Promotion boundary held:** all 10 `knowledge_promotions` rows remain `status = proposed`, with `reviewed_by = NULL`. Nothing was promoted to a reusable or trusted tier.
- **But unreviewed knowledge was reused as established fact.** On re-planning, the checkpoint logged `"Existing verified knowledge (6 fresh record(s)) is sufficient for intake"` and **skipped research**. During this audit, the same message appeared for an unrelated drought/reservoir objective (`prj-bc2bca9d…`). `captured` records labelled `supported` are being treated as sufficient prior knowledge across objectives.
  - Classification: CONFIRMED DEFECT.

## 7. Observed vs simulated

| Activity | Observed live? |
| :--- | :--- |
| DuckDuckGo search HTTP requests | ✅ (service log + `research_queries` rows with durations) |
| httpx page fetches | ✅ (`httpx: HTTP Request: GET … 200`) |
| Headless browser fetch | ❌ failed every time (executable missing for LocalSystem) |
| SearXNG / API providers | Not invoked |
| LLM summarization or analysis of sources | ❌ never invoked |
| Cross-source corroboration | ❌ claims are single-source, self-matched |

## 8. External validation (performed by Antigravity, not Qwen Collective)

A single independent search confirms that relevant authoritative material is publicly available and would have been found by a topic-bearing query. That material includes:

- PG&E's License Surrender Application and Decommissioning Plan before FERC (Scott Dam / Cape Horn Dam removal).
- The Eel-Russian Project Authority (Sonoma Water, Mendocino IWPC, Sonoma County, Round Valley Indian Tribes) proposing the New Eel-Russian Facility to continue a modified diversion.
- Parallel NEPA (FERC) and CEQA (State Water Board / ERPA) review.
- Interim FERC flow variances.

None of this appears in Qwen Collective's output. This external research was **not** inserted into any Qwen Collective deliverable.
