# Competitive Improvement Backlog

Tracked from competitive research (April 2026).
Items from `docs/research/mcp-search-competitors/research.md` "Where Competitors Are Stronger" section.

## Completed

- [x] **AI-powered answers** — Valyu Answer MCP tool added (`valyu_answer`). Returns synthesized answers with citations via SSE. Replaces the Perplexity Sonar gap.
- [x] **More extraction providers** — Valyu Contents ($0.001/URL) and Firecrawl (1 credit/page) added to 9-step extraction chain.
- [x] **More search providers** — Valyu Search added as tier 3 provider across all modes.
- [x] **GitHub integration** — GitHub provider added (tier 0, free, 30 req/min with token). Searches repositories. In discovery and research modes.
- [x] **Ease of setup** — README zero-config section strengthened. One-liner install + search. pipx instructions added. MCP setup section clarified.

## Open (from competitive research)

### High Impact
- [ ] **MCP Marketplace presence** — Three registries, all need browser action or public deployment:
  - **Smithery** (smithery.ai/new): Needs public HTTPS URL serving MCP (Streamable HTTP). Argus supports SSE transport — would need deployment to a public host first (Vercel, Railway, etc.). Smithery auto-scans the URL for tools/metadata.
  - **mcp.so** (mcp.so/submit): Web form — needs Name + URL. Browser submission. No GitHub issue path found.
  - **mcpservers.org** (mcpservers.org/submit): Web form — free listing or $39 premium for priority review. This is the awesome-mcp-servers repo (no PRs accepted).
  - **Official MCP Registry** (registry.modelcontextprotocol.io): TypeScript SDK only — no Python support yet.
  - **Blocker**: All three need either (a) Argus deployed to a public HTTPS URL, or (b) manual browser submission. Neither is a code change.

### Medium Impact
- [ ] **Local search without backend** — DuckDuckGo works with zero infra, but research notes "Argus needs SearXNG for free search" is a perception issue. README now leads with zero-config. Consider adding a "Quick Start" badge or section at the very top.
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
