# Design System — Argus Dashboard

## Direction

**Personality:** Precision & Density
**Foundation:** cool (gray-950 base — terminal dark, not blue-tinted)
**Depth:** borders-only

### Intent
The user is a developer or operator. They open this dashboard to check budget burn, query volume, provider health, and machine attribution — then get back to work. The interface should read like a terminal: dense, immediate, no decoration. Information leads; chrome recedes.

## Tokens

### Spacing
Base: 4px (Tailwind default grid)
Scale: 4 (p-1), 8 (p-2), 12 (p-3), 16 (p-4), 24 (p-6)
Section gap: mt-8 (32px) between major sections
Grid gap: gap-3 (12px) for card grids, gap-6 (24px) for side-by-side panels

### Colors


### Radius
Scale — three tiers:
- 4px ()  — inline elements: badges, alert banners
- 8px () — container surfaces: cards, tables, modals, chart boxes
- pill () — progress bars only

### Typography
Font: system-ui stack (ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto)
Mono: ui-monospace (font-mono) — machine names, timestamps, code snippets

Scale:
- text-xl font-bold (20px/700) — nav brand
- text-xl font-semibold (20px/600) — page h1
- text-lg font-semibold (18px/600) — section headings (h2)
- text-sm (14px/400) — body, table cells, labels
- text-xs uppercase (12px/400) — table headers, badge labels
- text-xs font-mono (12px) — timestamps, machine IDs

## Component Patterns

### Nav Bar
- bg-gray-900, border-b border-gray-800
- max-w-7xl mx-auto px-4 py-3
- Brand: text-xl font-bold tracking-wider
- Meta items: text-sm text-gray-400, gap-4

### Budget Card
- bg-gray-900 border border-gray-800 rounded-lg p-4
- Header: provider name (font-semibold text-gray-100) + tier (text-xs text-gray-500) + badge (right-aligned)
- Progress bar: full-width bg-gray-800 rounded-full h-2, fill rounded-full colored by status
- Footer: remaining + today (text-xs text-gray-400, justify-between)

### Budget Status Badge
- px-2 py-0.5 text-xs font-bold rounded
- Colors: bg-green-700 (OK), bg-yellow-700 (over_pace), bg-yellow-600 (warning), bg-red-700 (exhausted)
- All badge types use py-0.5 (tight for dense tool)

### Alert Banner
- p-3 border rounded, flex items-center gap-3
- Exhausted: bg-red-950 border-red-800, badge bg-red-700
- Over pace: bg-yellow-950 border-yellow-800, badge bg-yellow-700
- Badge: px-2 py-0.5 text-xs font-bold rounded uppercase

### Data Table
- Container: bg-gray-900 border border-gray-800 rounded-lg overflow-hidden
- Header row: bg-gray-800 text-gray-300 text-xs uppercase
- Cell padding: px-3 py-2
- Row divider: divide-y divide-gray-800
- Numeric values: text-right text-gray-200
- Timestamps: text-gray-400 font-mono text-xs

### Chart Container
- bg-gray-900 border border-gray-800 rounded-lg p-4
- Chart.js config: tick color #9ca3af (gray-400), grid color #1f2937 (gray-800), legend color #d1d5db (gray-300)

### Login Card
- max-w-md mx-auto mt-16 — centered, vertically spaced
- bg-gray-900 border border-gray-800 rounded-lg p-6
- NO shadow — borders-only strategy, same as all other cards
- Input: bg-gray-950 border border-gray-700 rounded px-3 py-2 (inset signaling via gray-700 border + darker bg)
- Submit: bg-blue-600 hover:bg-blue-500, full-width, rounded, transition

## Depth Strategy

**Borders-only throughout.** No box-shadows on any surface. This is non-negotiable for the Precision & Density direction — shadows add visual weight without information value in a terminal-dark tool.

Exception: none. Not even subtle shadows on cards.

The login card historically had shadow-lg — this was removed in the 2026-05-22 sweep to enforce consistency.

## Decisions

| Decision | Rationale | Date |
|----------|-----------|------|
| Borders-only depth | Dense ops tool; shadows add visual weight without info value | 2026-05-22 |
| 4px spacing base | Tight enough for data tables, divisible by common UI sizes | 2026-05-22 |
| 3-tier radius scale | 4px badges/alerts, 8px containers, pill progress bars — matches element type, not random | 2026-05-22 |
| gray-950 base (not pure black) | Reduces eye strain vs #000000; terminal-adjacent not cinema-dark | 2026-05-22 |
| py-0.5 badge padding (both badge types) | Precision & Density — tight vertical badges read as metadata, not CTAs | 2026-05-22 |
| font-mono for machine names + timestamps | Machine identifiers are data, not prose; mono aids scanning | 2026-05-22 |
