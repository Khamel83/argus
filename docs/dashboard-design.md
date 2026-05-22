# Dashboard Design System

Argus uses a documented design system for its dashboard UI. The canonical source is [`.interface-design/system.md`](../.interface-design/system.md) at the repo root.

## Quick Reference

**Direction:** Precision & Density  
**Depth:** Borders-only (no shadows anywhere)  
**Spacing base:** 4px (Tailwind default grid)  
**Color foundation:** gray-950 (terminal dark)

## Files

The dashboard consists of four Jinja2 templates in `argus/api/templates/`:

| File | Purpose |
|------|---------|
| `base.html` | HTML shell, CDN imports (Tailwind, HTMX, Chart.js), nav bar |
| `dashboard.html` | Main dashboard — chart, machines table, provider activity table |
| `_budget_section.html` | HTMX-refreshing budget cards and alert banners |
| `login.html` | Admin key login form |

## Contributing

When adding new UI components:

1. Read [`.interface-design/system.md`](../.interface-design/system.md) before writing markup.
2. Match the component patterns documented there (cards, badges, tables).
3. Use only values from the spacing/radius/color token tables.
4. Do not introduce shadows — borders-only is the enforced strategy.
5. Run `uv run pytest tests/ -v --tb=short` to catch any HTML snapshot regressions.

If you add a new pattern used 2+ times, document it in [`.interface-design/system.md`](../.interface-design/system.md) under Component Patterns.
