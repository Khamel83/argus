# Argus Visual Overview Design

## Purpose

Create a portable, self-contained visual overview that lets a new operator or
AI-client integrator understand Argus without reading the full README first.
It complements the README and operational documentation; it does not replace
them or become a second configuration source of truth.

## Artifact

- Path: `docs/argus-visual-overview.html`
- Discovery: `README.md` links to the overview beside the project status.
- Format: one HTML file with inline CSS and inline SVG/CSS diagrams.
- External dependency: Google Fonts only, as permitted by the
  `/html-everything` recipe.
- Source material: `README.md`, `CONTEXT.md`, and `docs/mcp-clients.md`.
- Mood: editorial — white ground, cobalt accent, dense but readable
  technical-magazine layout.

## Content Model

1. **Thesis** — Argus is retrieval infrastructure for AI agents: search,
   extraction, provenance, and routing policy behind CLI, HTTP, and MCP.
2. **System map** — A portable left-to-right diagram:
   callers → CLI / HTTP / MCP adapter → authenticated HTTP authority → broker,
   session store, extraction chain, and provider pool. The visual explicitly
   states that production MCP is a stateless HTTP adapter, not a local broker.
3. **Search policy** — A tiered routing panel showing free, recurring, and
   one-time providers, plus the budget/health/provenance gates around them.
4. **Extraction ladder** — A directional fallback flow across the documented
   extractors, ending in archive recovery, with quality/completeness gates.
5. **Topology and provenance** — A small map explaining residential versus
   datacenter egress, adaptive domain memory, and the egress/machine/source
   fields attached to results.
6. **How to connect** — A portable access matrix for scripts (HTTP), interactive
   agent harnesses (MCP), and local CLI use. It intentionally names no private
   hostname, IP address, token, or deployment-specific health status.
7. **Further reading** — Clickable local-document links for the README, context,
   MCP guide, and canonical deployment ADR.

## Boundaries

- No tokens, environment values, private hostnames, raw tailnet IPs, or live
  health claims.
- No JavaScript, build tool, generated images, or separate asset files.
- No copy of command snippets that could become stale configuration guidance.
- The page uses relative repository links, so it works from the checkout and
  renders correctly in GitHub after push.

## Verification

1. Parse the output as HTML and inspect that it has no secret-shaped strings.
2. Confirm all external and repository references use clickable `<a href>`
   links, with no bare URL text.
3. Render/open the file locally and inspect the desktop and narrow layouts.
4. Confirm the committed diff contains only the visual overview, this design,
   its implementation plan, and existing accepted repair files.
