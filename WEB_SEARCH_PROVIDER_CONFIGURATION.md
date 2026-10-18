# Qwen Collective — Web Search Provider Configuration Guide

## 1. Overview

The Qwen Collective Autonomous Research Subsystem provides a pluggable, provider-agnostic search interface (`app.services.research.providers.base.SearchProvider`). This enables the Project Orchestrator to execute public web queries without coupling business or research logic to a specific search engine.

Supported providers:
1. **DuckDuckGo / DDGS (`DuckDuckGoProvider`)**: Default, zero-configuration provider requiring no API keys.
2. **SearXNG (`SearXNGProvider`)**: Open-source, privacy-preserving metasearch engine exposing a clean JSON API.
3. **Generic Commercial API (`ApiSearchProvider`)**: Pluggable provider for Bing Web Search, Google Custom Search JSON API, Brave Search, or Tavily.

---

## 2. Default Provider: DuckDuckGo (`ddgs`)

### Characteristics
- Zero configuration, no accounts or tokens required.
- Fast multi-region search with automatic categorization.
- Graceful dual-engine fallback: uses the modern `ddgs` package with automatic fallback to `duckduckgo_search`.

### Configuration in `.env`
```bash
# Research Search Provider Selection: 'duckduckgo' (default), 'searxng', or 'custom_api'
RESEARCH_SEARCH_PROVIDER=duckduckgo

# Timeout per search query in seconds (default: 15.0)
RESEARCH_SEARCH_TIMEOUT=15.0

# Fallback provider in case primary provider experiences rate limiting or errors
RESEARCH_SEARCH_FALLBACK_PROVIDER=none
```

---

## 3. Self-Hosted Provider: SearXNG

SearXNG aggregates results from dozens of search engines (Google, Bing, DuckDuckGo, Wikipedia, arXiv, etc.) without tracking or IP profiling.

### Resource Evaluation & Placement
- **Linux Workers (`qwen-vps`, `qwen-laptop`)**: Both nodes have constrained RAM and strict sandbox policies. **DO NOT** run SearXNG on either worker node.
- **Windows Controller (`controller-local`)**: Can run SearXNG via Docker Desktop or a lightweight container on the local network or a dedicated host on the Tailscale network.

### SearXNG Docker Setup
```bash
docker run -d --name searxng \
  -p 8080:8080 \
  -v ./searxng:/etc/searxng \
  -e "BASE_URL=http://localhost:8080/" \
  -e "INSTANCE_NAME=qwen-collective-searxng" \
  searxng/searxng:latest
```

Ensure `settings.yml` in SearXNG enables JSON output:
```yaml
search:
  formats:
    - html
    - json
```

### Configuration in `.env`
```bash
RESEARCH_SEARCH_PROVIDER=searxng
RESEARCH_SEARXNG_URL=http://localhost:8080
RESEARCH_SEARXNG_CATEGORIES=general,science,it
RESEARCH_SEARCH_TIMEOUT=20.0
```

---

## 4. Dedicated Commercial Search API

For high-throughput enterprise environments requiring guaranteed SLAs, configure `ApiSearchProvider`:

### Configuration in `.env`
```bash
RESEARCH_SEARCH_PROVIDER=custom_api
RESEARCH_API_SEARCH_URL=https://api.tavily.com/search
RESEARCH_API_SEARCH_KEY=tvly-xxxxxxxxxxxxxxxxxxxx
RESEARCH_API_AUTH_HEADER=Authorization
RESEARCH_API_AUTH_PREFIX=Bearer 
```

---

## 5. Security & Privacy Safeguards

1. **Query Secret Scrubbing**:
   Before any query is transmitted across the network, it is evaluated by `ResearchSecurityScanner.scrub_search_query()`.
   - Private IPv4 addresses (`10.x.x.x`, `192.168.x.x`, `172.16-31.x.x`)
   - Bearer tokens, JWTs, and API keys (`sk-[A-Za-z0-9]{20,}`, `ghp_[A-Za-z0-9]{20,}`)
   - Passwords and secret parameters
   Matched credentials are stripped and redacted to `[REDACTED_SECRET]` before hitting external APIs.

2. **Domain Diversity Ranking**:
   Results from multiple queries are clustered and deduplicated by canonical domain. No single domain can dominate more than 40% of the candidate sources, ensuring balanced perspective and independent corroboration.

3. **Auditing & Telemetry**:
   Every executed query, duration, provider used, and retrieved URL count is recorded in the `research_queries` database table and reflected in the System Observatory telemetry.
