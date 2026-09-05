# Performance Analysis — SIH2026 Backend

## How Latency Is Measured

All latency measurements use `time.monotonic()` — a high-resolution, monotonically increasing clock unaffected by system clock adjustments.

### Key Measurements

| Metric | Measured By |
|--------|-------------|
| `last_tick_processing_ms` | Full time from tick start → broadcast complete |
| `planner_latency_ms` | Per-plan A* execution time |
| `broadcast_latency_ms` | JSON serialize + WebSocket send time |
| `conflict_resolution_latency_ms` | ConflictManager.detect_and_resolve() time |
| `task_injection_latency_ms` | POST /api/task/inject handler time |

All metrics use **Exponential Moving Average (EMA)** with α=0.3 to smooth noise:
```
ema_new = α × sample + (1 - α) × ema_old
```

---

## Architecture Latency Optimizations

### 1. Hot State In Memory
Robot positions, paths, reservations, conflicts — all live in Python dicts.
No database in the simulation tick pipeline.

### 2. Monotonic Timing
```python
tick_duration = cfg.SIM_TICK_MS / 1000.0
start = time.monotonic()
await simulate()
elapsed = time.monotonic() - start
await asyncio.sleep(max(0, tick_duration - elapsed))
```

### 3. Single Serialization
The tick payload is serialized to JSON **once** and sent to all WebSocket clients as the same string object. No per-client re-serialization.

### 4. O(1) Reservation Lookups
Reservation table: `dict[(x, y, t)] → robot_id`  
All checks are hash lookups — no linear scans.

### 5. Precomputed World Geometry
`WorldConfig.walkable_cells`, `static_obstacles`, and `charging_stations` are computed **once at startup** as `frozenset` objects. Per-tick access is O(1).

### 6. Non-Blocking Broadcast
WebSocket sends use `asyncio.wait_for(..., timeout=0.05)`.  
Clients that lag more than 50ms are disconnected — they never stall the simulation.

### 7. Localized Conflict Detection
Only robots within 2-cell Manhattan radius are compared.  
With 10 robots in a 30×30 grid, average comparisons ≈ 2–3 pairs (not 45).

### 8. EMA Metrics — Zero Allocation
Telemetry recording is simple float arithmetic — no allocations, no locks.

---

## Benchmark Commands

```bash
cd backend
python -m app.tests.benchmark
```

This runs:
1. **Planner benchmark** — 50 Space-Time A* plans
2. **Reservation benchmark** — 100 robots × 30-step paths
3. **Tick benchmark** — 50 ticks for 10/25/50/100 robot fleets

Results saved to `benchmark_results.json`.

---

## Expected Performance

Based on the architecture analysis (actual benchmark results will differ by hardware):

### Planner (Space-Time A* on 30×30 grid)
- Typical path (Manhattan distance ~20): **< 2ms**
- Complex path with many reservations: **< 5ms**
- No path found (exhausted horizon): **< 10ms**

### Reservation Table
- `reserve_path()` for 30-step path: **< 0.1ms**
- `is_reserved()` check: **< 0.001ms** (dict lookup)

### Tick Processing (estimated)
| Fleet | Conflict | Planning | Movement | Total |
|-------|----------|----------|----------|-------|
| 10 robots | 0.2ms | 1.0ms | 0.1ms | ~3ms |
| 25 robots | 0.5ms | 2.5ms | 0.3ms | ~6ms |
| 50 robots | 1.5ms | 5.0ms | 0.5ms | ~10ms |
| 100 robots | 5.0ms | 10.0ms | 1.0ms | ~20ms |

All fleet sizes operate well within the 500ms tick budget.

> **Note:** These are architectural estimates. Run `python -m app.tests.benchmark` for actual measured numbers on your hardware.

---

## Scaling Notes

The current architecture supports 100+ robots without structural changes:
- Dictionary-based robot lookup: O(1) by robot_id
- Reservation table: O(1) per check
- Conflict detection: O(N²) pairs but bounded by radius — effective O(N) for sparse fleets
- Planner: independent per-robot — parallelizable if needed

For 200+ robots, consider:
- Spatial indexing (grid-cell based proximity) for conflict detection
- Batch planning with parallelism (asyncio.gather for independent robots)
- Incremental reservation purging (already implemented)
