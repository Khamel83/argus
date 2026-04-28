# Argus Gap-Fill & Usage Plan

## Goal
Close the 3 real gaps vs. scraping-style aggregators, and establish Argus as the default search tool in daily workflows.

## Why Argus exists (competitive anchor)
The OpenClaw multi-search skill and similar "16 engines no API key" tools are prompt hacks that scrape HTML with user-agent spoofing. They break on rate limits, CAPTCHA updates, and DOM changes. Argus is production infrastructure: API-backed providers, 9-step content extraction, credit tracking, MCP/HTTP/CLI/Python interfaces, and graceful degradation. The tradeoff is explicit: Argus requires real API keys for paid providers, but SearXNG + DuckDuckGo + GitHub cover the free floor stably.

## Real gaps to close

### Gap 1: WolframAlpha provider (calculations, unit conversions, facts)
- The competitor lists WolframAlpha as a distinct use case we don't cover
- Free tier: 2,000 calls/month via Simple API (no key needed for basic queries)
- Add `argus/providers/wolfram.py` — Tier 0 (free), mode: `grounding` only
- Verify: `argus search -q "how many liters in a gallon" --mode grounding` returns Wolfram result
- Route: grounding mode only (not discovery/research — Wolfram is narrow-domain)

### Gap 2: Language detection → engine routing
- Chinese queries should prefer Chinese-capable engines; currently routed identically to English
- Add `langdetect` (lightweight, no API) to query preprocessing in `argus/broker/router.py`
- If `lang == 'zh'`: elevate SearXNG (which has Baidu/Bing CN inside it) to position 1, annotate result metadata
- Verify: `argus search -q "人工智能最新进展"` returns results with `lang: zh` metadata
- Does NOT require new provider adapters — SearXNG already proxies Chinese engines

### Gap 3: Improve the "free engine" story in docs and CLI output
- We have SearXNG (which internally aggregates 70+ engines including Ecosia, Qwant, Startpage, Yahoo) but we don't say that
- The competitor claims 16 named engines; we implicitly have more via SearXNG but hide this
- Update `argus health` output to show "SearXNG (aggregates 70+ engines including Ecosia, Qwant, Startpage)" 
- Update README provider table to list SearXNG's included engines as a footnote
- Verify: `argus health` output clearly communicates the SearXNG engine count

## Usage habits (how to use Argus more)

### Wire it into daily workflows
- [ ] Add `argus mcp serve` to the system MCP config so every Claude session has search tools available (it's already in CLAUDE.md; confirm it's running as a service on oci-dev)
- [ ] Use `mcp__argus__search_web` instead of WebSearch for all research queries in sessions
- [ ] Use `mcp__argus__extract_content` instead of WebFetch for reading URLs
- [ ] Use `mcp__argus__recover_url` when a URL 404s before giving up

### Decision rule for this session
| Task | Tool to reach for first |
|------|------------------------|
| Web search | `mcp__argus__search_web` |
| Read a URL | `mcp__argus__extract_content` |
| Dead URL | `mcp__argus__recover_url` |
| Expand short URL | `mcp__argus__expand_links` |
| Q&A from web content | `mcp__argus__valyu_answer` |
| Check provider health | `mcp__argus__search_health` |

## Tasks
- [ ] Task 1: Add WolframAlpha provider → `argus/providers/wolfram.py`, register in `argus/broker/router.py` grounding mode, Tier 0 → Verify: grounding search returns Wolfram result
- [ ] Task 2: Add `langdetect` language detection in broker query preprocessing → Verify: Chinese query elevates SearXNG and annotates `lang` in results
- [ ] Task 3: Update `argus health` + README to surface SearXNG's 70+ aggregated engines → Verify: `argus health` output lists engine names
- [ ] Task 4: Confirm `argus mcp serve` is running as a persistent systemd/launchd service on oci-dev → Verify: MCP tools available in new Claude sessions without manual start

## Done When
- [ ] WolframAlpha returns results for calculation/fact queries in grounding mode
- [ ] Chinese queries route intelligently via SearXNG with lang metadata
- [ ] `argus health` communicates the real engine count (not just "SearXNG")
- [ ] Argus MCP tools are the first-reach tools in every session (not WebSearch/WebFetch)
