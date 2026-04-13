# Competitive Improvement Backlog

Tracked from competitive research (April 2026).
Items from `docs/research/mcp-search-competitors/research.md` "Where Competitors Are Stronger" section.

## Completed

- [x] **AI-powered answers** — Valyu Answer MCP tool added (`valyu_answer`). Returns synthesized answers with citations via SSE. Replaces the Perplexity Sonar gap.
- [x] **More extraction providers** — Valyu Contents ($0.001/URL) and Firecrawl (1 credit/page) added to 9-step extraction chain.
- [x] **More search providers** — Valyu Search added as tier 3 provider across all modes.
- [x] **GitHub integration** — GitHub provider added (tier 0, free, 30 req/min with token). Searches repositories. In discovery and research modes.

## Open (from competitive research)

### High Impact
- [ ] **GitHub integration** — mcp-omnisearch has GitHub search. Argus does not. Add GitHub code/repo search as a provider.
- [ ] **MCP Marketplace presence** — Tavily is on Databricks MCP Marketplace. Argus is on PyPI but not in MCP marketplaces. Submit to `mcp.so` and `registry.modelcontextprotocol.io`.
- [ ] **Ease of setup** — `npx one-search-mcp` is simpler. Consider a `pipx install argus-search && argus mcp serve` one-liner in README.

### Medium Impact
- [ ] **Local search without backend** — DuckDuckGo works with zero infra, but research notes "Argus needs SearXNG for free search" is a perception issue. Ensure README leads with DuckDuckGo zero-config.
- [ ] **Documentation polish** — Competitors have professional docs, SDKs, enterprise support. Argus has project docs. Consider readthedocs or similar.

### Low Priority / Future
- [ ] **Chinese search engines** — one-search-mcp supports Zhipu, Bocha, Baidu, Sogou. Niche requirement.
- [ ] **Brand recognition** — Marketing effort, not code. Blog posts, Reddit presence, etc.

## Not Actionable (Aspirational)

These were identified by the explore agent but NOT in the original research docs:
- Observability dashboard (separate project)
- Knowledge graph API
- Structured extraction with schemas
- SOC 2 / enterprise compliance
- Kubernetes/edge deployment
