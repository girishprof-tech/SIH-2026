---
name: taste
description: Visual design critique, aesthetic taste evaluation, and industrial control room UI analysis. Use to audit interfaces for generic AI tropes, poor typography hierarchy, inconsistent density, and visual polish.
---

# Taste: Design Critique & Aesthetic Evaluation Skill

## Core Philosophy
Great operational software is not "generic SaaS". It is purposeful, high-density, authoritative, and visually calm. It feels like high-reliability aerospace, mission control, or automated logistics equipment—not an AI-generated template.

## The Antidotes to "AI Vibe-Coded" UIs
1. **No Gratuitous Neon / Gimmicky Glows:** Avoid random bright cyan/purple gradients everywhere. Use glowing accents solely for operational alerts (e.g. active conflict, battery critical, emergency stop).
2. **Deliberate Typography Hierarchy:**
   - Primary operational data (tick, coordinates, battery, robot ID) in crisp tabular monospace (e.g. JetBrains Mono, Roboto Mono).
   - Headings in disciplined, condensed sans-serif (e.g. Inter, Outfit, Segoe UI).
   - Explicit font weight contrast: 700 for key metrics, 400-500 for labels, uppercase tracking on micro-labels (`tracking-wider`).
3. **Information Density with Purpose:**
   - Operators need high density without clutter.
   - 1px border dividers (`rgba(255,255,255,0.08)`) instead of heavy card shadows.
   - Consistent 4px/8px grid rhythm.
4. **Authoritative Industrial Palette:**
   - Deep slate/zinc canvas (`#090d16` / `#0f172a`).
   - Surface elevations: Dark slate panels (`#131c2e`, `#1b263b`).
   - Signal colors calibrated to ISO / industrial conventions:
     - Operational Active: Amber/Gold (`#f59e0b`) & Safety Green (`#10b981`).
     - Alert / Conflict: Safety Red/Crimson (`#ef4444`).
     - Robot Identification: Vivid cyan/blue (`#06b6d4`, `#3b82f6`).
