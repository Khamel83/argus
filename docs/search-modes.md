# Search Modes

Argus has four search modes. Each mode uses a different provider chain and early-stop threshold, tuned for the type of search you're doing.

## Choosing a Mode

| I need to... | Use |
|-------------|-----|
| Find pages related to a topic | `discovery` |
| Find where a dead/moved URL went | `recovery` |
| Fact-check a claim with a few live sources | `grounding` |
| Explore a topic broadly, many angles | `research` |

If you don't know which to pick, use `discovery`. It's the default and works well for general-purpose queries.

## Mode Details

### discovery (default)

**Chain:** searxng → brave → exa → tavily → serper
**Early-stop:** 5 results from a single provider

Use when you want to find related pages, canonical documentation, or authoritative sources for a topic. Starts with SearXNG (free, self-hosted), so most queries cost nothing. Falls through to paid providers only if SearXNG returns fewer than 5 results.

**Example:** "python async best practices" — you want a handful of good pages, not an exhaustive list.

### recovery

**Chain:** searxng → brave → serper → tavily → exa
**Early-stop:** 3 results from a single provider

Use when you have a URL that's dead, moved, or returning errors and you want to find where the content went. Argus searches for the URL itself (plus optional title/domain hints) across providers. Only needs 3 results since you're looking for one specific page.

**Example:** `https://old-blog.com/article-that-moved` — you know the content existed, you need to find its new location.

### grounding

**Chain:** brave → serper → searxng
**Early-stop:** 3 results from a single provider

Use when you need a small number of live, authoritative sources to verify a claim. Starts with Brave and Serper (which tend to return authoritative results), uses only 3 providers total, and stops after 3 results. Designed for fact-checking, not exploration.

**Example:** "did python 3.13 remove GIL" — you want 2-3 reliable sources confirming or denying, not 20 blog posts.

### research

**Chain:** tavily → exa → brave → serper
**Early-stop:** 8 results from a single provider

Use when you want to explore a topic broadly. Starts with Tavily and Exa (which are tuned for AI/research queries and return longer snippets), uses a higher threshold of 8 results before stopping. Falls through more aggressively to build a diverse result set.

**Example:** "techniques for reducing LLM hallucination" — you want many perspectives and approaches, not just the top 3 links.

## How Fallback Works

Providers are tried **one at a time** in chain order. The broker moves to the next provider when the current one:

- Returns an **error** (HTTP error, timeout, exception)
- Returns **zero results**
- Is **unavailable** (missing key, disabled, budget exhausted, in cooldown)

The broker does **not** inspect result quality to decide whether to fall through — only errors and empty responses trigger fallback.

Once a single provider returns at least the threshold number of results, the broker stops and does not try further providers. If multiple providers contributed results (because early providers returned some results but below threshold), all results are merged via [Reciprocal Rank Fusion](routing-contract.md).

## Cost

SearXNG is free (self-hosted). Modes that start with SearXNG (`discovery`, `recovery`) typically cost nothing unless SearXNG fails or returns too few results. Modes that start with paid providers (`grounding` starts with Brave, `research` starts with Tavily) always consume at least one paid query.

All providers have free tiers — see [providers.md](providers.md) for limits.
