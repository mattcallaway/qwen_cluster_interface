# Qwen Collective — Autonomous Web Research User Guide

## 1. Quick Start: Creating a Research Project

To initiate an autonomous research project in the Qwen Collective UI:

1. Navigate to **Projects** -> **Create Project**.
2. Enter an objective requiring external factual research. For example:
   > *"Research the latest developments in renewable energy storage, compare major battery technologies, identify reputable sources, and produce a detailed report with citations."*
3. Define success criteria:
   > *"Retrieve at least 3 authoritative sources, verify cost per MWh and density claims, and generate an audited report with references."*
4. Click **Create Project**.

---

## 2. What Happens Behind the Scenes

The Project Orchestrator automatically:
1. **Detects Research Intent**: Analyzes keywords in the objective and activates the research task planner.
2. **Generates Research Strategy**: Constructs targeted queries, selects relevant technical domains, and allocates project budgets.
3. **Executes Web Searches**: Queries DuckDuckGo / SearXNG, scrubs sensitive tokens, and deduplicates URLs by canonical domain.
4. **Retrieves Web Pages**: Fetches HTML, PDFs, and data documents over secure HTTP with streaming byte bounds.
5. **Headless Browser Fallback**: If a page is a JavaScript Single Page Application, Playwright Chromium headless renders the DOM in an isolated sandbox.
6. **Extracts Evidence**: Discards ads and boilerplate navigation, extracting clean article text, tables, headings, and metadata.
7. **Verifies Factual Claims**: Records assertions in the Evidence Ledger and checks corroboration across multiple independent domains.
8. **Synthesizes Deliverable & Audits Citations**: Generates a structured markdown deliverable with inline numerical citations (`[1]`, `[2]`), deterministically verifying every citation against the Source Library.

---

## 3. Using the Dedicated Research Tab

In the Project Details view, click the **🌐 Research Engine** button.

### Dashboard Sections
1. **Research Metrics Bar**: Displays total queries executed, documents retrieved, verified claims, citations generated, and byte consumption.
2. **Resource Budget Gauge**: Shows query and document limits, remaining allowances, and browsing time consumed.
3. **Recent Search Activity**: A live feed of queries executed, provider used, result counts, duration, and execution status.
4. **Project Source Library**: A searchable table of all ingested sources with titles, publisher names, canonical URLs, publication dates, and quality scores.
   - Click **Inspect Source** on any row to open the full inspection modal showing the source's content hash, retrieval method, and extracted clean text.
5. **Evidence Ledger**: Displays all extracted claims, categorization (`fact`, `inference`, `estimate`, `opinion`), verification state badges (`SUPPORTED`, `CORROBORATED`, `CONTRADICTED`), and attached evidence excerpts.
6. **Autonomous Operations Controls**:
   - **Formulate Strategy**: Re-evaluates research questions and generates fresh queries.
   - **Run Autonomous Cycle**: Executes the full discovery-to-citation pipeline.
   - **Export Citations**: Downloads citations in Markdown, BibTeX, or JSON format.

---

## 4. Source Inspection & Verification Audit

Users can inspect why a particular source was cited:
- Click on any citation reference (e.g. `[1]`) in the Deliverables tab.
- The inspection view displays:
  - Original and canonical URL
  - Retrieval timestamp and method (`http` or `browser`)
  - Publisher and detected author
  - SHA-256 content hash
  - Verbatim excerpt matching the cited claim
  - Contradiction notices if conflicting information was detected
