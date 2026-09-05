# SIH Warehouse Fleet Simulation — Central Server Loss & Local Coordination

## Final demo behaviour

This project simulates a **30 × 30 warehouse** with **18 AMRs**. Every robot has a complete logistics task:

**START → PICKUP BOX → CARRY BOX → DROP AT DESTINATION → TASK COMPLETED**

At simulation **tick 12**, a **kill switch event disables the central/main server**. This does **not** kill or permanently stop the robots.

After the central server connection is lost:

1. Every robot still knows its own pickup/drop destination and task state.
2. Robots broadcast local status (position, battery and task state) to nearby/fleet peers.
3. Each robot locally calculates a route to its current goal.
4. Robots share their intended next cell.
5. Same-cell conflicts are negotiated using robot priority.
6. The yielding robot locally recalculates an alternate path.
7. Occupied cells cause `WAITING_FOR_CELL` until safe movement is possible.
8. Head-on swaps are detected and prevented.
9. Robots continue until all boxes are delivered.

## Important architecture note

The kill switch in this MVP represents **loss of the central route-management server**. The AMRs continue operating in a distributed/local-coordination mode. This matches the intended resilience demonstration: robots retain mission goals and coordinate with each other after central control is unavailable.

## Run the simulation

```bash
python simulation.py
```

This regenerates:

- `sim_output.json` — tick-by-tick simulation data

## View the animation

Open this file directly in Chrome/Edge:

```text
warehouse_simulation.html
```

The HTML contains the generated simulation data and provides Play, Pause, Step and Restart controls.

## Run tests

```bash
python -m pytest -q
```

## Project files

- `grid.py` — 30×30 warehouse grid and obstacle logic
- `pathfinder.py` — Space-Time A* pathfinding implementation
- `reservations.py` — reservation management utilities
- `simulation.py` — final pickup/carry/drop + central-server-loss + local coordination simulation
- `warehouse_simulation.html` — interactive visual playback
- `sim_output.json` — generated simulation output
- `demo.py` — basic pathfinding demonstration
- `test_pathfinder.py` — pathfinding tests
- `test_multi_robot_stress.py` — multi-robot stress tests
- `requirements.txt` — Python dependencies

## Final verified scenario

- 18 robots
- 30×30 warehouse
- 18 pickup boxes
- 18 delivery destinations
- central server loss at tick 12
- local communication and intent sharing
- conflict negotiation
- dynamic local replanning
- occupied-cell waiting
- head-on swap prevention
- all 18 tasks completed
