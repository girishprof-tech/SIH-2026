# SIH-2026
Project for Smart India Hackathon 2026: Decentralized Edge-AI Multi-Robot Warehouse Coordination.

## Decentralized Metrics Architecture
In this decentralized architecture, each robot operates as an autonomous OS process executing local pathfinding, reservation claiming, and peer conflict negotiation:
- **`planner_latency_ms`**: Measured directly on each decentralized robot node by high-resolution timing (`time.perf_counter`) across all local `find_path` invocations (initial route planning, dropoff routing, deadlock livelock avoidance detours, and yield replans). This value is forwarded via per-robot telemetry frames and aggregated by the FastAPI telemetry forwarder as a true rolling average across the active fleet.
- **`last_tick_processing_ms`**: Measured as the wall-clock execution loop latency of the telemetry forwarder reading and dispatching snapshots.
- **`active_conflicts`**: Sourced from real peer collision detections logged across the multi-robot fleet.
- **`replans`**: Cumulative count of real conflict resolution detour and yield replans executed across the fleet.
