# Research: ThirdStreet LAUSD RSS free-only extraction (Issue #249)

## Question

Does `https://3rdstes.lausd.org/apps/news/rss?categoryid=23143` need an Argus extraction or transport repair, a documented bounded unavailable outcome, or a source-specific operator action?

Scope of this report: the 2026-11-18 UTC observation on `POST /api/v2/extract` with `free_only=true`, `content_type=webpage`, caller `third-street`. No paid providers were enabled. No browser framework was added. The production authority response body and step ledger were not available here.

## Sources or evidence

### Issue and source snapshot

- Issue: [Khamel83/argus#249](https://github.com/Khamel83/argus/issues/249), open, labels `bug` and `needs-triage`, created `2026-11-18T02:24:24Z`. Body is the bounded retrieval report quoted in the task. The only comment is a G2K `execute` / `research-v1` receipt, not additional retrieval evidence.
- Inspected source: worktree `g2k/issue-249` at `eea874aad74267b07eee2928076c9d836cc2520e`, tracking fetched `origin/main` (`HEAD` symref `refs/heads/main`). This is not proof that the operated authority was running that commit.
- Observation claims used as given, not re-executed against the authority: news index returned `degraded` with a quality-passing Wayback artifact; the RSS URL returned HTTP 502 / `extraction_failed` with no accepted artifact; paid stages were skipped; authentication and capability negotiation succeeded; caller authority was ready but degraded.

### Contract: 502 is the closed status for no accepted artifact

`argus/contracts/outcomes.py` maps `CanonicalOutcome.EXTRACTION_FAILED` to HTTP 502. `degraded` maps to 200. ADR 0006 says the same: "extraction produced no above-floor artifact" is `extraction_failed` / 502; "accepted above-floor partial evidence" is `degraded` / 200 (`docs/adr/0006-http-mcp-compatibility-contract.md`). ADR 0005 says an eligible chain that produces no accepted artifact is `extraction_failed`, not a successful empty extraction.

`argus/operations/accepted.py` `extract` returns the finalizer outcome when present. A result error without an accepted outcome becomes `extraction_failed`. Disposition `partial` becomes `degraded`. HTTP status is applied later by `http_status_for`. The observed 502 is that mapping. It is not, by itself, an upstream 502 from LAUSD or a wrong route.

`free_only=true` is enforced before paid dispatch. `external_policy_reason` in `argus/extraction/extractor.py` returns `"free_only"` for every external step when that flag is set, and the chain records `policy_skipped` without calling Jina, Valyu, Firecrawl, or You.com. Skipped paid stages in the report match this code. They are not evidence that a paid stage would have retrieved the feed, and this report did not call those stages.

`content_type` on `ExtractRequest` is only `article` or `webpage` (`argus/api/schemas.py`). `webpage` lowers the quality floor from 100 to 50 words (`argus/extraction/quality_gate.py`). It does not select an RSS parser. There is no feed content type.

Archive provenance does not itself force `degraded`. `ExtractionFinalizer._artifact_decision` returns `success` when quality passed and completeness is true, `degraded` / `partial` when quality passed, completeness is false, and `partial_allowed` is true, and `extraction_failed` otherwise. The extract plan sets `partial_allowed=True`. The issue's `degraded` plus a quality-passing Wayback artifact is the partial-artifact case. The authority receipt's completeness signals were not available, so the exact rejection code is not restated from a live body. [INFERENCE] it is `incomplete_content` if the finalizer path above ran.

A 4xx from the guarded client is not turned into article text. `_extract_trafilatura` calls `raise_for_status()`. `GuardedHTTPStatusError` is a sibling of `GuardedAcquisitionError`, not a subclass, so the 403 leaves the extractor, is recorded as a failed attempt, and the chain continues. The Cloudflare interstitial is not an accepted artifact on this path.

### Public origin and archive probes

Probes below were unauthenticated GETs from this environment at about `2026-11-18T03:00:27Z`. They are not the authority's receipt.

| Target | Result |
|---|---|
| `https://3rdstes.lausd.org/apps/news/` with `User-Agent: Argus/1.0` | HTTP 403, `server: cloudflare`, HTML title `Attention Required! \| Cloudflare`, `content-type: text/html` |
| `https://3rdstes.lausd.org/apps/news/rss?categoryid=23143` with `Argus/1.0` and with a Chrome-like browser User-Agent | Same 403 interstitial. User-Agent did not change the block |
| `https://3rdstes.lausd.org/apps/news/rss` | Same 403 |
| Wayback availability for the news index | closest snapshot status 200, timestamp `20251207170743`, URL `http://web.archive.org/web/20251207170743/https://3rdstes.lausd.org/apps/news/` |
| Wayback availability for the exact RSS URL, the HTTP variant, the queryless RSS URL, and `.../apps/news/?categoryid=23143` | `archived_snapshots` empty |
| CDX `3rdstes.lausd.org/apps/news/*`, status 200, collapsed | six HTML rows, all `20251206`–`20251207`: the index, two articles, those articles with `categoryId=23143`, and `/apps/news/category/23143`. No RSS or XML row |
| CDX `3rdstes.lausd.org/*rss*` | empty array |

The archived news index (65,059 bytes from `web.archive.org`) contains the registered feed as an alternate link:

`application/rss+xml` → `https://3rdstes.lausd.org/apps/news/rss?categoryid=23143`

That proves the URL was the site's Schoolwires-style news feed for category 23143 on 2025-12-07. It does not prove a current feed document exists in Wayback or is reachable from this network.

### What the free chain can accept

Installed Trafilatura `bare_extraction` on the 4,549-byte 403 body produced 80 words of challenge text (`This website is using a security service...`). The webpage floor is 50 words, so that text could pass a quality gate if an extractor returned it. The trafilatura step does not return it: `raise_for_status()` runs first.

The same function returned no text for a minimal RSS 2.0 document with a channel title and two item descriptions. `normalize_trafilatura_result` then returns `None`, and `_extract_trafilatura` records `trafilatura: no content extracted`. That is a real feed-parser gap for a 200 XML body. It is not the proven cause of the 2026-11-18 failure: this environment never received XML, and the issue does not include an origin status or a parse error from the authority.

Wayback extraction looks up the exact URL, accepts only a closest snapshot with status `200`, then runs Trafilatura on the archived HTML (`argus/extraction/wayback_extractor.py`). An empty availability object becomes `wayback: not archived`. There is no query-stripping or category-page substitution. Stripping `categoryid` would not have helped: the queryless RSS URL is also unarchived.

Production Obscura fails closed with `browser_policy_unavailable` (`argus/extraction/obscura_extractor.py`). Local-step typed failures are not copied onto the final result unless a paid-step causal failure exists. Paid steps were policy-skipped, so that browser-policy code would not replace `extraction_failed` / 502. This report did not launch a browser. Whether the authority's Playwright or residential step ran is unknown.

### ThirdStreet registration

`g2k-workspace` read of `Khamel83/third-street` `main` (`data.json` sha256 `cac6eab21c21738cb859404d131333b706ced1c0cc92b99b672e0d234342be97`, `DATA_UPDATES.md`, `README.md`, `HANDOFF.md`):

- `data.json` `meta.sources` are Gmail, WhatsApp, Smore, and YumYummi. Item `sourceUrl` values are Smore, Google forms/sheets, SignUpGenius, Totem, and `https://lausd.yumyummi.com/webapp/third-st-el-nnc/weekly/lunch`. The RSS URL is not in `data.json`.
- `DATA_UPDATES.md` says Muse updates `data.json` only and that source URLs must be HTTPS without credentials. It does not define an Argus feed extractor.
- The issue's "registered source" is the retrieval report's name for that URL, not a row found in `data.json`.

## Conclusion

The RSS URL does not need a transport repair, and the 2026-11-18 observation does not justify an extraction-code change.

HTTP 502 / `extraction_failed` with no accepted artifact is the documented bounded outcome when the eligible free chain finishes without an above-floor artifact. `degraded` on the news index is the documented 200 outcome for a quality-passing partial artifact. Paid-stage skips are the `free_only` policy. Those three facts match the source contract. They do not show a mis-mapped status or a dropped successful extract.

The split between the two URLs is explained by source availability, not by a query-string transport bug:

- The HTML news index has a December 2025 Wayback snapshot. That can be a quality-passing archive artifact. It is not current coverage. The snapshot is about eleven months before the observation.
- The exact RSS URL has no Wayback snapshot in the availability API, and CDX has no RSS/XML capture for this host's news path. Live fetch from this environment is a Cloudflare 403 for both the page and the feed, including with a browser User-Agent. Guarded extraction does not accept that interstitial.

A feed parser would not have changed this observation unless some free fetch had returned the XML. None did here. A parser gap exists only as a latent limit: Trafilatura produced no text from a minimal RSS 2.0 sample. That is not evidence the authority received a feed and failed to parse it.

Current coverage of this registered URL is an operator limit. Free-only static extraction plus existing archive lookup cannot supply it. ThirdStreet's last-good retention, and its refusal to treat the news-page archive as current feed coverage, matches the evidence.

Unknown, and not filled in:

- Authority egress, residential-fallback configuration, and per-step diagnostics for the 2026-11-18 call.
- Whether any non-datacenter client can currently fetch the RSS XML.
- The operated image's source commit versus `eea874aa`.
- The authority rejection code and completeness signals. Classification above uses the issue text plus this source, not a copied response body.

## Next action

Record the RSS URL as bounded unavailable for free-only extraction. Do not open an Argus transport or extractor change for this observation. Do not enable paid providers or add a browser.

Operator follow-up, outside this report: keep ThirdStreet on last-good data. If current school-news coverage is required, use an already-authorized egress that can pass this host's Cloudflare challenge, or register a different source that is actually fetched and archived. The December 2025 HTML index, category page, and article snapshots are not substitutes for the feed and are not current. Do not treat a later archive hit on the HTML index as proof that the RSS URL recovered.
