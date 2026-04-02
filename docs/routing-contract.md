# Argus Routing Contract

This document describes exactly how the broker routes queries, falls through to fallback providers, and applies ranking. It is authoritative — if the code and this doc disagree, the code wins.

---

## Fallback Triggers

A provider is tried next in the chain when the current provider produces:
- **An error** (HTTP error, timeout, exception)
- **Empty results** (zero results returned)
- **Unavailable** (key missing, config-disabled, budget-exhausted, in cooldown, manually disabled)

There is no "weak output" concept. The broker does not inspect result quality or count to trigger fallback — only error or empty output.

Fallback is **sequential**, not parallel. Providers are tried one at a time in chain order.

---

## Search Modes and Chains

| Mode | Chain | Max results threshold |
|------|-------|-----------------------|
| `discovery` | searxng → brave → exa → tavily → serper | 5 |
| `recovery` | searxng → brave → serper → tavily → exa | 3 |
| `grounding` | brave → serper → searxng | 3 |
| `research` | tavily → exa → brave → serper | 8 |

The executor stops as soon as it has at least the threshold number of results from a single provider. If a provider returns results but fewer than the threshold, fallback still fires.

---

## Result Fusion (RRF)

When **multiple providers** contribute results, Reciprocal Rank Fusion (RRF) is applied:

- **k = 60** (constant) — controls the weight of early ranks
- Score formula: `sum(1 / (k + rank))` across all providers for each URL
- Applied **post-deduplication** — duplicate URLs are collapsed before fusion

When only **one provider** contributed, results are returned in the provider's native rank order (score still set to the RRF value from rank 0 only).

`max_results` is applied **after fusion** to the final ranked list.

---

## Deduplication

URLs are normalized before deduplication:
- Scheme and netloc lowercased
- `www.` prefix stripped
- Trailing slash removed
- Query params sorted
- Common tracking params removed (`utm_*`, `ref=`, `fbclid`, `gclid`)

The first occurrence (highest RRF score) is kept; duplicates are discarded.

---

## Provider Skip Conditions

A provider is skipped without being counted as a failure when it is:
- `disabled_by_config` (`ARGUS_<PROVIDER>_ENABLED=false`)
- `unavailable_missing_key` (enabled but `ARGUS_<PROVIDER>_API_KEY` is blank)
- `budget_exhausted` (monthly spend ≥ limit)
- `manually_disabled` (set via admin API or CLI)
- `temporarily_disabled_after_failures` (in cooldown window)

Failures only accrue when a provider was available and attempted but returned an error or empty result.

---

## Session Context

When a `session_id` is provided, the current query may be context-enriched with recent prior queries. Context enrichment only fires for short follow-up queries (≤ 4 words, no question words). Longer queries are passed through unchanged.

Context is bounded by `ARGUS_SESSION_MAX_CONTEXT_CHARS` (default: 2000). Sessions expire after `ARGUS_SESSION_TTL_HOURS` (default: 168). Turn history is trimmed to `ARGUS_SESSION_MAX_TURNS` (default: 20) oldest-first.
