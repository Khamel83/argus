# Obscura: What to Take for Argus

**Source:** https://github.com/h4ckf0r0day/obscura  
**Date:** 2026-04-24

---

## What Obscura Is

Headless browser engine written in Rust. Real V8 JavaScript, CDP (Chrome DevTools Protocol) compatible. Drop-in replacement for headless Chrome. Distributable as a single ~70MB binary.

| Metric       | Obscura | Headless Chrome |
|--------------|---------|-----------------|
| Memory       | 30 MB   | 200+ MB         |
| Page load    | 85 ms   | ~500 ms         |
| Startup      | instant | ~2s             |
| Anti-detect  | built-in | none           |
| Playwright   | yes     | yes             |

Key capability: **stealth mode** (`--stealth`) randomizes GPU/canvas/audio/battery fingerprints per-session, sets `navigator.webdriver = undefined`, blocks 3,520 tracker domains, masks native function signatures.

---

## The Problem It Solves in Argus

Argus's auth extraction is "blocked by datacenter IP bot detection" (memory record). The current Playwright step (Step 4) launches full headless Chrome — detectable, heavy (200MB+), slow to start. This is the right layer to fix.

Additionally, Argus targets Raspberry Pi 3 (1GB RAM). Chromium is borderline there. 30MB vs 200MB is the difference between working and OOM.

---

## What to Take

### 1. Obscura as CDP Backend for Playwright (highest value)

Playwright already supports `chromium.connectOverCDP()`. If Obscura is running as a CDP server (`obscura serve --port 9222 --stealth`), the existing `playwright_extractor.py` needs one change:

```python
# Instead of:
_browser = await _playwright_instance.chromium.launch(headless=True, args=['--no-sandbox'])

# If Obscura is running:
_browser = await _playwright_instance.chromium.connectOverCDP("ws://127.0.0.1:9222")
```

This gives stealth + speed + low memory with **zero protocol change**. Playwright stays as the Python API layer; Obscura is just the engine underneath.

Graceful degradation: if `ARGUS_OBSCURA_CDP_URL` is set and connectable → use Obscura. Otherwise → fall back to full Chrome launch.

### 2. New extractor step: obscura_extractor.py (good fit)

Add `obscura fetch <URL> --dump text --stealth` as a subprocess-based extractor. Insert it **between Playwright and Jina** in the chain, or **replace the current Playwright step** when Obscura is available. The `LP.getMarkdown` CDP method gives clean DOM-to-Markdown, which is often better than the current `innerText` approach in `playwright_extractor.py`.

CLI path:
```python
result = subprocess.run(
    ["obscura", "fetch", url, "--dump", "text", "--stealth", "--quiet"],
    capture_output=True, text=True, timeout=15
)
```

### 3. LP.getMarkdown via CDP (nice to have)

Obscura exposes a non-standard CDP method `LP.getMarkdown` that converts the rendered DOM to Markdown. This is cleaner than the current JS `innerText` evaluation. Accessible via Playwright's `page.evaluate` on the CDP session, or via raw CDP call.

---

## What to Skip

- **Batch/parallel scraping (`obscura scrape`)** — Argus extracts specific URLs, doesn't bulk-scrape sites.
- **Puppeteer/Node.js API** — Argus is Python; Playwright CDP integration covers this.
- **Custom tracker blocklists** — stealth mode handles this automatically.

---

## Implementation Plan

1. Add `ARGUS_OBSCURA_CDP_URL` env var (default: empty = disabled)
2. Modify `playwright_extractor.py`: if env var set, use `connectOverCDP`, else launch Chrome
3. Add `obscura_extractor.py` as optional Step 4a (subprocess-based, stealth CLI)
4. Document Obscura as optional Tier 2 dependency (Pi 4, Mac Mini, VMs with 50MB to spare)
5. Add `obscura serve --stealth --workers 2` to the docker-compose stack as an optional service

---

## Fit with Argus Architecture

Argus's chain philosophy: local-first, free-first, graceful degradation. Obscura is:
- Local (self-hosted binary, no API key)
- Free (Apache 2.0)
- Degrades gracefully (falls back to full Playwright if not present)
- Pi-friendly (30MB vs 200MB)
- Directly addresses bot detection, the current blocker for auth extraction

This is a clean addition to Step 3–4 of the extraction chain with no impact on the external API fallback steps.
