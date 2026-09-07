---
name: image-to-code
description: Translating UI screenshot designs, mockups, and layouts into semantic React code, clean CSS custom property tokens, and canvas render routines.
---

# Image-to-Code: Design Token Translation Guide

## Token Structure: CSS Custom Properties
Maintain a single source of truth in `:root` inside `styles.css`:
```css
:root {
  --bg-app: #080c14;
  --bg-panel: #0f172a;
  --bg-card: #1e293b;
  --border-subtle: rgba(148, 163, 184, 0.15);
  --border-strong: rgba(56, 189, 248, 0.4);
  
  --text-main: #f8fafc;
  --text-dim: #94a3b8;
  --text-muted: #64748b;
  
  --color-primary: #38bdf8;
  --color-success: #10b981;
  --color-warning: #f59e0b;
  --color-danger: #ef4444;
  
  --font-mono: 'JetBrains Mono', 'Roboto Mono', monospace;
  --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
}
```

## Canvas Render Matching
When rendering to HTML5 Canvas:
- Compute exact pixel coordinates: `x * cellSize`, `y * cellSize`.
- Draw background grid with clean lines: `ctx.strokeStyle = var(--border-subtle)`.
- Center robot indicators with antialiased circle + arrow pointing along `heading`.
