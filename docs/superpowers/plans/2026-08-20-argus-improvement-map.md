# Argus Improvement Map Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the conceptual Argus visual overview with a portable,
operator-first Improvement Map that makes practical symptoms, evidence sources,
next actions, and verification gates easy to navigate.

**Architecture:** Keep a single static HTML artifact. Replace its page copy and
major visual sections with semantic access/evidence, status, symptom, and
improvement-board sections; support them with inline responsive CSS. The page
links out to canonical repository documents instead of duplicating commands or
claiming current telemetry.

**Tech Stack:** Semantic HTML5, inline CSS, Google Fonts, Python standard
library HTML parsing, Git.

## Global Constraints

- Modify only `docs/argus-visual-overview.html` and this plan in the isolated
  `codex/argus-homelab-mcp-repair` worktree.
- Produce one self-contained HTML page; no JavaScript, build tooling, images,
  separate assets, secrets, private hostnames/IPs, token-shaped text, or live
  health claims.
- Keep the existing README discovery link unchanged.
- Use relative clickable links to `README.md`, `CONTEXT.md`,
  `docs/mcp-clients.md`, `docs/operations.md`, and
  `docs/operations-status.md`.
- Treat the historical deployment ADR as historical only; Operations is the
  primary operational source.
- Preserve the editorial mood while making the information dense, skimmable,
  responsive, and useful without JavaScript.

---

### Task 1: Replace the overview with the operator information architecture

**Files:**
- Modify: `docs/argus-visual-overview.html:1-354`

**Interfaces:**
- Consumes: the documented authority, status, and client contracts in
  `README.md`, `CONTEXT.md`, `docs/mcp-clients.md`, `docs/operations.md`, and
  `docs/operations-status.md`.
- Produces: semantic page sections with stable IDs: `access-evidence`,
  `status-semantics`, `symptom-guide`, `improvement-board`, `boundaries`, and
  `sources`.

- [ ] **Step 1: Replace the hero and thesis with an operator purpose**

  Change the title, meta description, eyebrow, hero heading, and thesis so the
  page says that it is an improvement map rather than a live dashboard. Use
  explicit evidence labels such as `documented contract`, `needs a live probe`,
  and `human-gated`.

  ```html
  <p class="eyebrow">Argus · operator improvement map</p>
  <h1>Make retrieval problems actionable.</h1>
  <p class="lede">Start with the symptom. Find the authority. Run the smallest safe check. Improve the system with evidence.</p>
  ```

- [ ] **Step 2: Render the access-and-evidence chain**

  Add `id="access-evidence"` and a three-stage diagram: caller surfaces;
  private authenticated authority; and authority-owned execution/evidence. In
  the caller stage, distinguish native MCP, HTTP, and CLI. In the authority
  stage, state that production callers do not own provider credentials,
  persistence, or local browsers.

  ```html
  <section class="section" id="access-evidence">
    <p class="kicker">01 · Access &amp; evidence</p>
    <h2>One authority keeps the evidence coherent.</h2>
    <div class="evidence-chain" aria-label="Callers delegate through private ingress to the Argus authority and evidence systems.">
      <article class="chain-stage">
        <p class="chip">caller surfaces</p>
        <h3>Native MCP · HTTP · CLI</h3>
        <p>Callers bring intent and scoped authorization, not a local broker.</p>
      </article>
      <span class="chain-arrow" aria-hidden="true">→</span>
      <article class="chain-stage authority-stage">
        <p class="chip">private authority</p>
        <h3>Authenticated execution</h3>
        <p>Policy, sessions, provider credentials, persistence, and browser capability remain authority-owned.</p>
      </article>
      <span class="chain-arrow" aria-hidden="true">→</span>
      <article class="chain-stage">
        <p class="chip">evidence out</p>
        <h3>Search · extraction · provenance</h3>
        <p>Results identify their provider and acquisition path so downstream systems can inspect them.</p>
      </article>
    </div>
  </section>
  ```

- [ ] **Step 3: Add status semantics and the symptom-to-action guide**

  Add `id="status-semantics"` with five cards: `healthy`, `degraded`,
  `unready`, `unknown`, and `disabled`. Explain liveness, readiness, and full
  operator status as distinct evidence layers. Add `id="symptom-guide"` with
  six branches: connection/transport error, MCP `401`, liveness while retrieval
  fails, provider/extraction failure, ChatGPT private access, and a red
  development test. Every branch must name a meaning, non-mutating probe
  category, responsible surface, and a document link.

  ```html
  <article class="symptom-card">
    <p class="chip">symptom · MCP returns 401</p>
    <h3>Ingress answered; caller authorization did not.</h3>
    <dl>
      <div><dt>Safe check</dt><dd>Inspect the caller's scoped credential configuration.</dd></div>
      <div><dt>Source</dt><dd><a href="mcp-clients.md">MCP client setup</a></dd></div>
    </dl>
  </article>
  ```

- [ ] **Step 4: Add the improvement board and hard boundaries**

  Add `id="improvement-board"` with six cards: client onboarding and
  acceptance, status and observability, provider reliability and spend,
  documentation navigation, development baseline health, and external-boundary
  clarity. Each card must contain `pain`, `evidence`, `next action`,
  `verification gate`, and `planning state`. Add `id="boundaries"` that
  clearly separates a human-gated ChatGPT private-MCP tunnel from retired or
  external infrastructure that is not a fallback.

  ```html
  <article class="improvement-card">
    <p class="state ready">ready to plan</p>
    <h3>Provider reliability &amp; spend</h3>
    <dl>
      <div><dt>Pain</dt><dd>Configured capacity can still be unreachable, cooling down, or exhausted.</dd></div>
      <div><dt>Evidence</dt><dd>Capability, reachability, health, cooldown, and balance are separate signals.</dd></div>
      <div><dt>Next action</dt><dd>Define a no-spend acceptance matrix.</dd></div>
      <div><dt>Gate</dt><dd>Record a controlled request outcome with provenance.</dd></div>
    </dl>
  </article>
  ```

- [ ] **Step 5: Replace the source index and footer**

  Make `docs/operations.md` the primary operations link. Add Operations Status
  as its own link. If the historical ADR remains, label it `historical only`.
  Make the footer say the document is static, portable, and not a live status
  board.

- [ ] **Step 6: Run source-level structural checks**

  Run:

  ```bash
  python3 - <<'PY'
  from html.parser import HTMLParser
  from pathlib import Path

  class Check(HTMLParser):
      def __init__(self):
          super().__init__()
          self.ids, self.hrefs = set(), []
      def handle_starttag(self, tag, attrs):
          data = dict(attrs)
          if "id" in data:
              self.ids.add(data["id"])
          if tag == "a" and "href" in data:
              self.hrefs.append(data["href"])

  page = Path("docs/argus-visual-overview.html").read_text()
  check = Check(); check.feed(page)
  assert {"access-evidence", "status-semantics", "symptom-guide", "improvement-board", "boundaries", "sources"} <= check.ids
  assert {"../README.md", "../CONTEXT.md", "mcp-clients.md", "operations.md", "operations-status.md"} <= set(check.hrefs)
  print(f"ids={len(check.ids)} links={len(check.hrefs)}")
  PY
  ```

  Expected: a nonzero `ids=` count and at least five links, with exit status
  zero.

- [ ] **Step 7: Commit the content architecture**

  ```bash
  git add docs/argus-visual-overview.html
  git commit -m "docs: make Argus visual overview operational"
  ```

### Task 2: Add responsive operator-map styling

**Files:**
- Modify: `docs/argus-visual-overview.html:12-167`

**Interfaces:**
- Consumes: the section IDs and class names produced by Task 1.
- Produces: a desktop layout that supports evidence scanning and a narrow
  layout where every diagram, status card, symptom card, and improvement card
  remains readable.

- [ ] **Step 1: Add CSS for evidence labels, semantic cards, and definition lists**

  Add classes for `.evidence-label`, `.status-grid`, `.status-card`,
  `.symptom-grid`, `.symptom-card`, `.improvement-grid`, `.improvement-card`,
  `.state`, and `.boundary-card`. Use the existing cobalt, ink, muted gray,
  and pale-blue palette; use text and borders rather than meaning-by-color
  alone.

  ```css
  .status-grid, .symptom-grid, .improvement-grid { display: grid; gap: 12px; }
  .status-grid { grid-template-columns: repeat(5, minmax(0, 1fr)); }
  .symptom-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .improvement-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .symptom-card dl, .improvement-card dl { margin: 16px 0 0; }
  .symptom-card dt, .improvement-card dt { font: 500 10px/1.2 "JetBrains Mono", monospace; letter-spacing: .1em; text-transform: uppercase; }
  ```

- [ ] **Step 2: Add responsive breakpoints for information density**

  At the existing medium breakpoint, collapse the evidence chain to one
  column, rotate its arrows, and change the status and improvement grids to
  two columns. At the narrow breakpoint, collapse every grid to one column and
  preserve an adequate text size and visible focus outline.

  ```css
  @media (max-width: 860px) {
    .evidence-chain { grid-template-columns: 1fr; }
    .chain-arrow { transform: rotate(90deg); }
    .status-grid, .improvement-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  }
  @media (max-width: 560px) {
    .status-grid, .symptom-grid, .improvement-grid { grid-template-columns: 1fr; }
  }
  ```

- [ ] **Step 3: Check CSS selectors and visual fallbacks**

  Run:

  ```bash
  for selector in evidence-chain status-grid symptom-grid improvement-grid boundary-card; do
    rg -q "\\.${selector}" docs/argus-visual-overview.html
  done
  rg -n '@media|focus-visible|grid-template-columns' docs/argus-visual-overview.html
  ```

  Expected: exit status zero and output that shows the medium and narrow
  viewport rules.

- [ ] **Step 4: Commit the responsive styling**

  ```bash
  git add docs/argus-visual-overview.html
  git commit -m "docs: style Argus improvement map"
  ```

### Task 3: Verify portability, links, and rendered artifact

**Files:**
- Modify: `docs/superpowers/plans/2026-08-20-argus-improvement-map.md`
- Verify: `docs/argus-visual-overview.html`

**Interfaces:**
- Consumes: completed static HTML and its relative documentation links.
- Produces: recorded verification evidence and a clean worktree after the
  verification commit.

- [ ] **Step 1: Run static safety and markup verification**

  Run:

  ```bash
  git diff --check HEAD~2..HEAD
  ! rg -n 'ARGUS_[A-Z_]+=|Bearer [A-Za-z0-9._-]{12,}|https?://[^" ]*\\.ts\\.net|(?:[0-9]{1,3}\\.){3}[0-9]{1,3}' docs/argus-visual-overview.html
  python3 - <<'PY'
  from html.parser import HTMLParser
  from pathlib import Path

  class Check(HTMLParser):
      def __init__(self):
          super().__init__(); self.links = []
      def handle_starttag(self, tag, attrs):
          if tag == "a":
              href = dict(attrs).get("href")
              if href: self.links.append(href)

  check = Check(); check.feed(Path("docs/argus-visual-overview.html").read_text())
  assert len(check.links) >= 6
  assert "operations.md" in check.links
  assert "operations-status.md" in check.links
  print(f"clickable_links={len(check.links)}")
  PY
  ```

  Expected: all commands exit zero and the final command prints at least six
  clickable links.

- [ ] **Step 2: Open the rendered page and record renderer outcome**

  Run:

  ```bash
  open docs/argus-visual-overview.html
  ```

  If a visual browser renderer is available, inspect desktop and a narrow
  viewport. If no renderer is installed, record that source/markup checks
  passed but screenshot verification was unavailable; do not describe it as a
  visual pass.

- [ ] **Step 3: Check the final branch state and commit plan completion**

  Mark each completed checkbox in this plan, then run:

  ```bash
  git add docs/superpowers/plans/2026-08-20-argus-improvement-map.md
  git commit -m "docs: record improvement map verification"
  git status --short
  git log --oneline -4
  ```

  Expected: a clean worktree, with commits for the operational map, its style,
  and verified plan completion.
