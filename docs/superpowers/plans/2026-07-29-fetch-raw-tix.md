# Fetch Raw Tix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add and deploy Argus raw browser fetch compatibility, then wire Tix to real SeatGeek event URLs and live inventory.

**Architecture:** A thin authenticated FastAPI route delegates to a bounded raw-browser service. The service reuses Argus's managed Playwright browser, enforces SSRF and redirect safety, captures same-site inventory JSON when present, and otherwise returns rendered HTML. Tix consumes the stable response and parses direct or embedded listing JSON.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, Playwright, pytest, httpx, SQLAlchemy

## Global Constraints

- No paid extractors or providers may be invoked.
- Preserve existing Argus extraction behavior.
- Accept both `X-API-Key` and Bearer authentication through existing middleware.
- Never return `status: "ok"` with an empty body.
- Use the stored Tix event URL.

---

### Task 1: Argus compatibility contract

**Files:**
- Create: `argus/api/routes_fetch_raw.py`
- Create: `argus/raw_fetch.py`
- Modify: `argus/api/schemas.py`
- Modify: `argus/api/main.py`
- Test: `tests/test_fetch_raw_api.py`

**Interfaces:**
- Consumes: managed browser from `argus.extraction.playwright_extractor`
- Produces: `POST /api/fetch-raw`

- [ ] Write API and service tests for auth, request validation, JSON preference,
      HTML fallback, explicit upstream errors, empty-body rejection, and unsafe
      redirects.
- [ ] Run `uv run --no-sync pytest tests/test_fetch_raw_api.py -q` and verify
      the tests fail because the route and service do not exist.
- [ ] Implement the request/response schemas, raw fetch service, and thin route.
- [ ] Run the focused tests and existing API/architecture tests until green.
- [ ] Commit the Argus implementation with explicit paths.

### Task 2: Tix live boundary

**Files:**
- Modify: `ticket_sniper/argus/client.py`
- Modify: `ticket_sniper/listings/collector.py`
- Modify: `ticket_sniper/sources/seatgeek.py`
- Test: `tests/test_argus_contract.py`
- Test: `tests/test_listing_collection.py`

**Interfaces:**
- Consumes: Argus `FetchRawResponse.body`
- Produces: persisted normalized live SeatGeek listings

- [ ] Write tests proving the client preserves explicit failures, the collector
      uses `SourceEvent.event_url`, and the parser accepts direct and
      script-embedded listing JSON.
- [ ] Run the focused Tix tests and verify the expected RED failures.
- [ ] Implement only the client, stored-URL lookup, and parser changes required
      by those tests.
- [ ] Run focused and full Tix tests until green.
- [ ] Commit the Tix implementation with explicit paths.

### Task 3: Publish and prove

**Files:**
- Modify documentation only if the shipped contract differs from this design.

**Interfaces:**
- Consumes: merged Argus and Tix revisions
- Produces: live evidence from the production endpoints and Tix database

- [ ] Run the full Argus suite, Ruff, formatting, architecture checks, and
      `git diff --check`.
- [ ] Push Argus, open and merge its PR, then allow the existing immutable
      promotion workflow to deploy the exact image.
- [ ] Verify `/api/health`, auth rejection, auth success, and a real SeatGeek
      `/api/fetch-raw` request remotely.
- [ ] Tune only the bounded JSON selection/parser logic if the observed live
      SeatGeek shape requires it, then repeat tests and promotion.
- [ ] Push and merge Tix, configure its existing Argus URL/key, and run one live
      listing collection.
- [ ] Verify at least one listing is persisted and report exact revisions and
      evidence.
