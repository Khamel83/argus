# Argus Visual Overview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and publish one portable, self-contained HTML overview that visually explains Argus without duplicating operational configuration.

**Architecture:** `docs/argus-visual-overview.html` is a static editorial page with inline CSS and inline SVG/CSS diagrams. It summarizes the README, context glossary, and MCP client guide through visual panels, then links to those source documents for authoritative detail.

**Tech Stack:** semantic HTML5, inline CSS, inline SVG, Google Fonts, standard-library HTML parsing for validation.

## Global Constraints

- Create exactly one standalone HTML artifact and no companion CSS, JavaScript, image, or build files.
- Keep all content portable: omit hostnames, IP addresses, tokens, environment values, and live health claims.
- Preserve every external URL as a clickable link and use relative links for repository documents.
- Use the editorial palette: white ground, near-black ink, cobalt accent.
- Do not use a generator or install dependencies.

---

### Task 1: Render the portable Argus overview

**Files:**
- Create: `docs/argus-visual-overview.html`
- Modify: `README.md`
- Read: `README.md`, `CONTEXT.md`, `docs/mcp-clients.md`, `docs/superpowers/specs/2026-08-19-argus-visual-overview-design.md`

**Interfaces:**
- Consumes: the project’s documented architecture, routing policy, extraction chain, provenance model, and client-boundary guidance.
- Produces: a directly openable HTML document whose diagrams and copy make no deployment-specific assertion.

- [x] **Step 1: Add the self-contained document shell and editorial tokens**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Argus · Retrieval Infrastructure for AI Agents</title>
  <meta name="description" content="A portable visual overview of Argus search, extraction, provenance, and AI-agent access." />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo+Black&family=Inter+Tight:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" />
</head>
```

Define CSS custom properties for `--ground: #ffffff`, `--ink: #101114`,
`--accent: #0029ff`, and responsive desktop/mobile layout rules.

- [x] **Step 2: Add the architecture and policy visual sections**

```html
<section class="section" id="system-map">
  <p class="kicker">01 · System map</p>
  <h2>One authority. Many ways in.</h2>
  <div class="flow" aria-label="Callers flow through transport adapters to the Argus authority, then to broker, extraction, persistence, and providers.">
    <div class="node">CLI<br><span>scripts &amp; operators</span></div>
    <div class="node">HTTP<br><span>service integrations</span></div>
    <div class="node">MCP<br><span>interactive agents</span></div>
    <div class="arrow">→</div>
    <div class="node authority">Authenticated HTTP authority</div>
    <div class="arrow">→</div>
    <div class="node">Search broker<br><span>policy, budgets, ranking</span></div>
  </div>
</section>
```

Add adjacent visual panels for free/monthly/one-time provider tiers, the
extraction fallback ladder, and topology/provenance fields. Use explanatory
labels rather than provider tokens, endpoint values, or status data.

- [x] **Step 3: Add access guidance and source links**

```html
<section class="section" id="access">
  <p class="kicker">05 · Choose the surface</p>
  <div class="access-grid">
    <article><h3>HTTP</h3><p>For services, jobs, and direct integrations.</p></article>
    <article><h3>MCP</h3><p>For interactive agent harnesses over an authenticated authority.</p></article>
    <article><h3>CLI</h3><p>For local operator workflows and development.</p></article>
  </div>
  <p>Read the authoritative guides: <a href="../README.md">README</a>, <a href="../CONTEXT.md">context</a>, <a href="mcp-clients.md">MCP client setup</a>, and <a href="adr/0001-canonical-deployment.md">deployment history</a>.</p>
</section>
```

Include a footer that identifies the page as an `/html-everything` render,
links to `https://github.com/iharnoor/html-everything`, and names the four
source documents. Do not emit a bare external URL. Add a compact link to the
new page beside the README status line so the visual overview is discoverable.

- [x] **Step 4: Check content boundaries before visual review**

Run:

```bash
rg -n -i 'sk_[A-Za-z0-9]|xai-[A-Za-z0-9]|gho_[A-Za-z0-9]|AKIA[A-Z0-9]|AIza[A-Za-z0-9]|https?://[^<]+' docs/argus-visual-overview.html
```

Expected: no credential-shaped value; each `https://` occurrence is inside an
`href` attribute or documented font/preconnect metadata.

### Task 2: Verify, commit, and publish the final documentation artifact

**Files:**
- Verify: `docs/argus-visual-overview.html`
- Verify: `docs/superpowers/specs/2026-08-19-argus-visual-overview-design.md`
- Verify: `docs/superpowers/plans/2026-08-19-argus-visual-overview.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: the rendered standalone HTML artifact.
- Produces: a parseable, visually opened, committed, and pushed branch.

- [x] **Step 1: Validate HTML structure and source links**

Run:

```bash
python3 -c 'from html.parser import HTMLParser; HTMLParser().feed(open("docs/argus-visual-overview.html").read())'
rg -n 'href="(\.\./README\.md|\.\./CONTEXT\.md|mcp-clients\.md|adr/0001-canonical-deployment\.md)"' docs/argus-visual-overview.html
```

Expected: Python exits `0` and all four relative source links are present.

- [x] **Step 2: Open and inspect the rendered page**

Run:

```bash
open docs/argus-visual-overview.html
```

Expected: the page opens with a readable desktop layout; the responsive CSS
keeps flow diagrams and grids legible on narrow screens.

- [x] **Step 3: Commit only the visual documentation artifacts**

Run:

```bash
git add README.md docs/argus-visual-overview.html docs/superpowers/specs/2026-08-19-argus-visual-overview-design.md docs/superpowers/plans/2026-08-19-argus-visual-overview.md
git diff --cached --check
git commit -m "docs: add Argus visual overview"
```

- [x] **Step 4: Push the completed branch**

Run:

```bash
git push -u origin codex/argus-homelab-mcp-repair
git status --short
git log -1 --oneline
```

Expected: the branch has an upstream remote, the worktree is clean, and the
latest commit is the visual overview.
