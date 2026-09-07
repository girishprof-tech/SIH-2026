# Member 2 — Warehouse Pathfinding & Decentralized Local Coordination

## What this package contains

Core reusable algorithms:

- `grid.py` — generic warehouse grid, obstacles and obstacle-aware distance cache.
- `pathfinder.py` — Space-Time A* with 4-direction movement, turn ticks, reservations and swap prevention.
- `reservations.py` — reserve/release/prune reservation helpers.

Demonstration and verification:

- `simulation.py` — 18-AMR decentralized pickup/delivery simulation.
- `warehouse_simulation.html` — standalone animated playback.
- `sim_output.json` — tick-by-tick simulation data.
- `test_pathfinder.py`, `test_multi_robot_stress.py` — core algorithm tests.
- `test_simulation_safety.py` — simulation safety regression test.

## Important design: no predefined robot routes

The decentralized simulation does **not** give robots a stored full path.
Each tick, a robot knows only:

- current position
- pickup/drop goal
- static warehouse map
- nearby robot messages

It computes and negotiates only its **next move**.

## Kill switch behaviour

At tick `12`:

1. The central server is declared offline.
2. Robots retain their own pickup/drop tasks.
3. No central route is supplied.
4. Nearby robots broadcast position and intended next move.
5. Contested targets are negotiated by deterministic local priority with aging.
6. A losing robot tries another safe next move or waits.
7. A final atomic safety check prevents same-cell collisions and head-on swaps.

### Critical safety fix

A robot is never allowed to blindly enter a cell just because another robot *might* leave it. The simulator validates all moves together before executing the tick.

## Run

```bash
pip install -r requirements.txt
python simulation.py
```

Open `warehouse_simulation.html` in a browser to view the animation.

## Tests

```bash
python -m pytest -q
```

The final package currently verifies:

- no same-cell collisions among active robots
- no head-on swaps
- all 18 pickup → delivery tasks complete
- kill switch transition to local coordination
- no predefined route following in the simulation

## Integration

For the main team backend, the reusable production modules are primarily:

```text
grid.py
pathfinder.py
reservations.py
```

`simulation.py` and the HTML are demonstration/test layers and can be kept under a `pathfinding/` or `testing/` area of the team repository.
