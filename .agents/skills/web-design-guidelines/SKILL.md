---
name: web-design-guidelines
description: Modern web design guidelines, WCAG AA accessibility, layout tokens, typography scale, spacing grids, and responsive industrial dashboards.
---

# Web Design Guidelines

## Color Tokens & Contrast Ratio (WCAG AA Compliance)
- Normal text requires at least **4.5:1** contrast ratio against background.
- Large text (>=18pt or bold >=14pt) requires at least **3:1** contrast ratio.
- UI components and graphical objects require at least **3:1** contrast ratio.

## Industrial Warehouse Control Room Palette
- Background Base: `#080c14` (Deep obsidian slate)
- Panel Surface: `#0f172a` (Dark navy slate)
- Card Surface: `#1e293b` (Subtle elevated surface)
- Border Subtle: `rgba(148, 163, 184, 0.15)`
- Border Focused: `rgba(56, 189, 248, 0.5)`
- Text Primary: `#f8fafc` (Slate 50)
- Text Secondary: `#94a3b8` (Slate 400)
- Text Muted: `#64748b` (Slate 500)
- Accent Primary: `#38bdf8` (Sky 400 - telemetry active)
- Status Success: `#10b981` (Emerald 500 - idle / connected / ready)
- Status Warning: `#f59e0b` (Amber 500 - yielding / turning)
- Status Danger: `#ef4444` (Rose 500 - conflict / e-stop)

## Spacing & Grid Rhythm
- Base increment: `4px`
- Gaps: `4px`, `8px`, `12px`, `16px`, `24px`
- Border radius: `6px` for controls, `8px` for panels, `12px` for main modal containers
