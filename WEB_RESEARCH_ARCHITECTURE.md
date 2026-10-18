# Qwen Collective — Autonomous Web Research & Browsing Engine Architecture

## 1. Overview & System Mission

The **Autonomous Web Research & Browsing Engine** is a first-class subsystem natively integrated into the Qwen Collective Project Orchestrator. It empowers AI agents to autonomously plan research, formulate targeted search queries, discover reputable public web sources, safely retrieve documents, automate headless browser interaction when client-side JavaScript is mandatory, extract structured facts and evidence, corroborate claims against independent domains, maintain an auditable project-isolated Source Library, and produce publication-ready deliverables with verifiable numerical inline citations.

---

## 2. High-Level Architecture Diagram

```mermaid
graph TD
    User([User / Project Request]) --> Planner[Orchestrator Intake & Planner]
    Planner -->|Identifies Research Intent| ResearchPlan[DAG of Research Tasks]
    
    subgraph Execution on controller-local
        SearchManager[Search Manager & Provider Router]
        DuckDuckGo[DuckDuckGo / DDGS Provider]
        SearXNG[SearXNG Provider]
        ApiSearch[Custom API Provider]
        
        SecureFetcher[Secure HTTP Fetcher (Streaming Reads)]
        HeadlessBrowser[Playwright Chromium Headless]
        Extractor[Document Extractor: HTML, PDF, JSON]
        SourceLibrary[(Project Source Library: SQLite & Disk)]
        EvidenceLedger[(Evidence Ledger: Claims & Citations)]
        CitationEngine[Citation Audit Engine]
        Synthesizer[Research Synthesizer]
    end
    
    ResearchPlan --> SearchManager
    SearchManager --> DuckDuckGo
    SearchManager --> SearXNG
    SearchManager --> ApiSearch
    
    SearchManager -->|Real Public URLs| SecureFetcher
    SecureFetcher -->|SPA / Dynamic JS Detected| HeadlessBrowser
    SecureFetcher --> Extractor
    HeadlessBrowser --> Extractor
    
    Extractor --> SourceLibrary
    SourceLibrary --> EvidenceLedger
    EvidenceLedger --> CitationEngine
    CitationEngine --> Synthesizer
    Synthesizer --> Deliverables[Project Deliverables & Artifacts]
    
    subgraph Isolated Sandboxes
        VPS[qwen-vps (No Public Net)]
        Laptop[qwen-laptop (No Public Net)]
    end
```

---

## 3. Core Subsystems & Responsibilities

### 3.1. Research Planner & Strategy Generator (`planner.py`)
- **Intent Recognition**: Automatically analyzes project intake prompts for phrases like `"research"`, `"compare"`, `"report"`, `"sources"`, `"citations"`, `"latest developments"`.
- **DAG Formulation**: Decomposes the research mission into sequential, interdependent stages:
  1. `research_search`: Formulate and run search queries.
  2. `research_fetch`: Fetch public web pages, PDFs, and data.
  3. `research_browser_read`: Optional headless browser execution for SPAs or dynamic JavaScript.
  4. `research_extract`: Parse titles, metadata, tables, headings, and clean text.
  5. `research_verify`: Link material claims to extracted source excerpts and evaluate corroboration across multiple second-level domains.
  6. `research_synthesize`: Generate structured markdown deliverable with inline numerical citations (`[N]`).
  7. `research_citation_audit`: Deterministically verify that every citation resolves to an ingested source record and that all claims are supported.
- **Budget Tracking (`ResearchBudget`)**: Enforces hard resource limits per project:
  - `max_queries` (default: 15)
  - `max_docs` (default: 30)
  - `max_browsing_seconds` (default: 120.0s)
  - `max_tokens` (default: 60,000)

### 3.2. Provider-Independent Search Layer (`search.py`, `providers/`)
- Abstract base class `SearchProvider` with unified `search()` signature.
- **DuckDuckGo Provider (`duckduckgo.py`)**: Zero-configuration default utilizing `ddgs` with graceful fallback to `duckduckgo_search`.
- **SearXNG Provider (`searxng.py`)**: Self-hosted metasearch JSON API integration with domain filtering, category selection, and pagination.
- **Generic Search API Provider (`api_provider.py`)**: Standard commercial search API adapter supporting configurable headers, endpoints, and bearer tokens.
- **Query Scrubbing**: Every query passes through `scrub_search_query()` to strip API keys, Bearer tokens, private RFC-1918 IPs, and credentials before reaching the public internet.
- **Deduplication & Domain Diversity**: Normalizes URLs and ranks results across diverse root domains to avoid echo chambers.

### 3.3. Secure URL Retrieval & Headless Browsing (`retrieval.py`, `browser.py`)
- **SSRF & Private Network Defense**:
  - Rejects `127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16` (cloud metadata), `100.64.0.0/10` (Tailscale Carrier-Grade NAT), IPv6 loopback/link-local/ULA.
  - Pre-connection DNS resolution validation with revalidation on every HTTP redirect hop (maximum 5 redirects).
  - Port restriction (only TCP 80 and 443 permitted).
- **Streaming Byte Limits**:
  - Responses streamed with 5 MiB maximum limit to protect against zip bombs or resource exhaustion.
  - Global read/connection timeouts (15.0 seconds).
- **Headless Browser Fallback (Playwright Chromium)**:
  - Spawns ephemeral, unprivileged browser contexts without shared cookies or profile persistence.
  - Route-level network interception (`page.route("**/*")`) blocking internal/LAN/metadata destinations attempted by client-side scripts.
  - Triggers automatically upon detecting SPA skeletons (`id="root"`, `<noscript>`, dynamic hydration tags) or HTTP 403 barriers.

### 3.4. Document Extraction (`extraction.py`)
- **HTML Parsing**: Clean extraction using BeautifulSoup4, stripping navigation bars, footers, advertisement containers, cookie banners, and social popups.
- **PDF Extraction**: Integrated `pypdf` reader extracting page text, tables, and document metadata.
- **Metadata Discovery**: Extracts canonical URL, OpenGraph tags, Dublin Core tags, publication date, author, publisher, and structured tables.

### 3.5. Source Library & Provenance (`sources.py`)
- Stored in SQLite (`research_sources`, `research_source_versions`) and disk (`data/research_sources/{project_id}/{source_id}.txt`).
- **Deduplication**: SHA-256 content hashing and canonical URL normalization (`normalize_canonical_url`).
- **Versioning**: Preserves snapshot history if a URL's content changes over time.
- **Quality Heuristics**: Evaluates top-level domains (`.gov`, `.edu`, `.org`), SSL security, publication dates, and extracted word count.

### 3.6. Evidence Ledger & Fact Verification (`verification.py`)
- **Claim States**: `UNVERIFIED`, `SUPPORTED`, `CORROBORATED`, `CONTRADICTED`, `INSUFFICIENT_EVIDENCE`, `OUTDATED`.
- **Corroboration Logic**: Automatically checks if a claim is independently supported by sources from two or more distinct root domains.
- **Contradiction Detection**: Explicitly tracks opposing excerpts and flags disputes.

### 3.7. Citation Engine & Synthesis (`citations.py`, `synthesis.py`)
- Formats structured inline citations (`[1]`, `[2]`).
- Appends complete References section with title, canonical URL, publisher, publication date, and retrieval timestamp.
- **Deterministic Citation Audit**: Ensures every cited index resolves to an actual database record in `research_sources`. Reports with dangling or unsupported citations are rejected before deliverable acceptance.

### 3.8. Sandbox Isolation Model
- Remote workers (`qwen-vps`, `qwen-laptop`) remain completely air-gapped from the public internet in their existing execution sandboxes.
- Web retrieval, search, and browser automation tasks execute strictly on `controller-local`, supervised by the Orchestrator.
