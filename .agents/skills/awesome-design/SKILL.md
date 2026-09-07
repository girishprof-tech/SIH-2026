---
name: awesome-design
description: Curated industrial control room design patterns, component architectures, status badge indicators, 2D canvas styling, and high-density telemetry tables.
---

# Awesome Design: Component Patterns for Industrial Warehouse AMRs

## 1. Top Control Bar (Mission Status Header)
- Compact, fixed-height 48px header.
- Left: Brand icon + System Title + Fleet Mode Badge (`AUTONOMOUS 10 AMRS`).
- Center: Simulation Clock & Controls (Tick counter `#142`, Step, Resume/Pause, Speed slider).
- Right: WebSocket vitality pulse + Quick Action Buttons (Demo Scenario, Add Obstacle, Chaos Mode).

## 2. Grid Canvas (2D Warehouse Map)
- Grid lines: ultra-thin 1px with subtle crosshairs or coordinate notches along borders (0 to 29).
- Static Obstacles: Slate racks with textured hatch pattern or drop shadow.
- AMRs: Distinct SVG/Canvas markers with:
  - Directional heading indicator (wedge / arrow).
  - Robot ID label badge.
  - State halo (Green=Idle, Blue=Moving, Orange=Negotiating/Yielding, Red=E-Stop).
  - Planned path trail: subtle dashed cyan vector line.

## 3. Telemetry Sidebar (Fleet & Robots)
- Compact card per AMR showing:
  - ID + Class (`GOODS_TO_PERSON`, `SORTING`, `SCANNING_AUDIT`).
  - Battery meter (colored progress bar: Green >50%, Amber 20-50%, Red <20%).
  - Position `(X, Y)` and current action (`MOVED`, `WAITING`, `CHARGING`).
  - Priority score chip.

## 4. Live Telemetry Metrics Panel
- 4-grid key performance indicators:
  - Tick Processing Latency (ms)
  - Planner Latency (ms)
  - Active Conflicts (count)
  - Cumulative Replans (count)
