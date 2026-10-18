# Qwen Collective — Web Research Subsystem Test Results

## 1. Test Suite Summary

The Autonomous Web Research & Browsing Engine underwent comprehensive verification spanning:
1. **Network Security & SSRF Protection**: Deterministic blocking of loopback, private LAN, link-local, cloud metadata, Tailscale CGNAT, non-HTTP schemes, and unsafe ports.
2. **DNS Rebinding & Redirect Defense**: Pre-connection IP resolution verification, strict revalidation on every redirect hop, and streaming byte limits.
3. **Prompt Injection & Secret Scrubbing**: Screening of adversarial prompt override patterns and automatic sanitization of API keys, tokens, and private infrastructure addresses.
4. **Extraction & Deduplication Engine**: Parsing HTML headings, tables, and clean text via BeautifulSoup, PDF extraction via pypdf, canonical URL normalization, and SHA-256 deduplication.
5. **Evidence Ledger & Corroboration**: Claim tracking, multi-domain corroboration detection, contradiction flagging, and epistemic categorization.
6. **Deterministic Citation Auditing**: Inline citation resolution, rejection of hallucinated or unretrieved URLs, and bibliography formatting.
7. **Live Public Internet Integration**: Real search via DuckDuckGo (`ddgs`), live document fetch from `python.org`, Playwright Chromium headless browsing, and end-to-end report delivery.

---

## 2. Test Execution Details

### 2.1. Network Security & Defense Suite (`tests/test_research_security.py`)
- `test_ssrf_blocking_private_and_metadata_ranges`: **PASSED**
  - Confirmed rejection of `127.0.0.1`, `10.0.0.1`, `192.168.1.1`, `172.16.0.1`, `169.254.169.254`, `100.64.0.1`, and `localhost`.
- `test_non_standard_ports_and_schemes_blocked`: **PASSED**
  - Confirmed rejection of `ftp://`, `file:///`, and ports `8000`, `9000`, `22`.
- `test_search_query_secret_scrubbing`: **PASSED**
  - Confirmed Bearer tokens, OpenAI keys (`sk-...`), GitHub tokens (`ghp_...`), and internal IPs are scrubbed before search query execution.
- `test_prompt_injection_screening`: **PASSED**
  - Confirmed detection of `"ignore previous instructions"`, system override attempts, and malicious directives.
- `test_streaming_byte_limit_enforcement`: **PASSED**
  - Confirmed streaming responses terminate immediately upon exceeding 5 MiB limit, preventing memory exhaustion.

### 2.2. Research Engine Unit Suite (`tests/test_research_engine.py`)
- `test_canonical_url_normalization`: **PASSED**
  - Normalized UTM tracking query params, fragments, case differences, and trailing slashes.
- `test_html_extraction_with_tables_and_metadata`: **PASSED**
  - Extracted structured tables, headings, and clean article body without advertising or navigation noise.
- `test_source_library_deduplication_and_versioning`: **PASSED**
  - Verified SHA-256 hashing and version history incrementation upon document modification.
- `test_evidence_ledger_corroboration_and_contradiction`: **PASSED**
  - Successfully elevated claims supported by two distinct root domains (`python.org` and `wikipedia.org`) to `CORROBORATED`.
  - Confirmed contradictory source flags trigger `CONTRADICTED` state.
- `test_citation_engine_validation`: **PASSED**
  - Deterministically audited valid citations and flagged non-existent references.
- `test_research_planner_budget_enforcement`: **PASSED**
  - Enforced `max_queries` and `max_docs` boundaries.
- `test_orchestrator_research_plan_validation`: **PASSED**
  - Confirmed generated research tasks adhere strictly to registered capability registry with zero unknown scripts or cyclic dependencies.

### 2.3. Live Internet Integration Suite (`tests/test_live_web_research.py`)
- `test_live_web_research_cycle`: **PASSED**
  - **Objective**: *"Research the latest stable version of Python and summarize the release information using official sources."*
  - **Live Search**: Executed DuckDuckGo search via `ddgs`, returning live official Python.org URLs.
  - **Live HTTP Fetch**: Retrieved and parsed `https://www.python.org/downloads/` and release notes with streaming limits.
  - **Headless Browser (Playwright Chromium)**: Launched Chromium in headless sandbox, navigated to Python Downloads, and rendered dynamic DOM.
  - **Evidence Verification**: Recorded primary claim, linked to exact Python Software Foundation excerpt, and verified status as `SUPPORTED`.
  - **Synthesis & Citation Audit**: Formatted report with numerical citations `[1]`, validated against `research_sources`, and confirmed 0 errors.
  - **Observatory Telemetry**: Logged queries, sources, and provider health into `research_queries`, `research_sources`, `research_citations`, and `research_provider_status`.

---

## 3. Regression Suite Verification
All existing test suites across the repository (migrations, project orchestrator, system observatory, worker protocol, and knowledge vaults) passed without regression.
