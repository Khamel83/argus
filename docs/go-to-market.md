# Go-to-Market Plan

## Timeline

### Week 1: Foundation
- [x] llms.txt + llms-full.txt
- [x] GitHub topics (mcp-server, search-api, ai-agents, llm-tools, search-broker, python, fastapi, web-search)
- [x] pyproject.toml metadata (keywords, classifiers, URLs)
- [x] README badges + MCP install section (Claude Code, Cursor, VS Code)
- [x] CONTRIBUTING.md + .github/ templates
- [ ] Submit to MCP Server Registry (https://modelcontextprotocol.io/registry)
- [ ] Submit to mcp-get (https://github.com/punkpeye/mcp-get)

### Week 2: Launch
- [ ] Hacker News post — see angle below
- [ ] One technical blog post on RRF for search aggregation (evergreen content)
- [ ] Submit to Python Weekly, PyCoder's Weekly

### Week 3+: Sustain
- [ ] Reddit: r/LocalLLaMA, r/ClaudeAI, r/ChatGPTCoding
- [ ] Monitor GitHub Issues for feature requests and bugs
- [ ] Respond to every issue within 48 hours

## HN Post Angle

**Title:** "How I cut my agent's search costs by 70% using a Python broker with RRF ranking"

**Hook:** The cost-reliability arbitrage story — use Serper ($1/1k) as primary with Exa/Tavily ($5-10/1k) as fallback. RRF ranking merges results so you get better quality than any single provider.

**Technical depth:** Brief explanation of Reciprocal Rank Fusion (k=60) and how it boosts results that appear across multiple providers.

**Don't say:** "I made a tool." Say: "Here's a technique" and the tool is the implementation.

## Reddit Angles

**r/LocalLLaMA:** "I built a search broker so my local LLM can use 5 search APIs with automatic fallback — no vendor lock-in, budget enforcement included"

**r/ClaudeAI:** "Argus: MCP search server with 5 providers, RRF ranking, and budget tracking — one config block and Claude Code gets reliable web search"

## Key Metrics

| Timeline | Target |
|----------|--------|
| Month 1 | 50-200 stars |
| Month 3 | 500-1,000 stars |
| Month 6 | 1,000-3,000 stars |
| Real success metric | Active MCP registry installs |

## What NOT to Do

| Don't | Why |
|-------|-----|
| Build a UI | Backend tool. A UI is a distraction. |
| X/Twitter | No following = screaming into a void. |
| Weekly updates | One good post > weekly noise. |
| Build a website | README + GitHub is enough. |
| Chase press coverage | They won't cover a 0-star repo. |
| Chase Product Hunt | Dead for dev tools in 2026. |

## Competitive Position

Argus is the only search broker with: multi-provider routing + RRF ranking + budget enforcement + health tracking + MCP native + zero external database dependencies. The MCP search category on GitHub has ~500 repos — all single-provider. Nothing else does multi-provider routing as a service.

## Distribution Channels

1. **MCP Server Registry** — primary discovery path for AI agent builders
2. **mcp-get** — CLI-based MCP server discovery
3. **llms.txt** — AI agents can "read" the repo automatically
4. **GitHub topics** — drives Explore and search discovery
5. **HN + Reddit** — initial user acquisition
