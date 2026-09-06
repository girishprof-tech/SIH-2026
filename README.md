# SIH-2026
Project for Smart India Hackathon 2026: Decentralized Edge-AI Multi-Robot Warehouse Coordination.

## Warehouse Demo Model

The live demo uses a heterogeneous fleet of three AMR classes:

- **Goods-to-Person** AMRs retrieve items from the central shelving area and deliver them to the outbound export dock.
- **Sorting** AMRs collect batches from the inbound import dock and route them to the sorting zone.
- **Scanning & Audit** AMRs patrol audit checkpoints and report inventory observations.

The 30x30 warehouse has a multi-cell import dock on the west side, a multi-cell export dock on the east side, and six perimeter charging stations. Space-time reservations and peer arbitration allow multiple AMRs to queue at shared dock approaches while avoiding cell and swap collisions.

## User-Facing Jobs

The frontend's **Demo Scenario** button starts the simulation, submits staggered examples of all three job types, injects a temporary obstacle, and enables packet-loss chaos mode. Jobs can also be submitted directly:

```http
POST /api/job
Content-Type: application/json

{"job_type":"fetch_item","item_id":"SKU-1042","urgency":4}
```

Supported `job_type` values are `fetch_item`, `sort_batch`, and `audit_checkpoint`. The response identifies the selected `robot_type`, assigned `robot_id`, and either a `task_id` or `audit_id`.

## Decentralized Metrics Architecture
In this decentralized architecture, each robot operates as an autonomous OS process executing local pathfinding, reservation claiming, and peer conflict negotiation:
- **`planner_latency_ms`**: Measured directly on each decentralized robot node by high-resolution timing (`time.perf_counter`) across all local `find_path` invocations (initial route planning, dropoff routing, deadlock livelock avoidance detours, and yield replans). This value is forwarded via per-robot telemetry frames and aggregated by the FastAPI telemetry forwarder as a true rolling average across the active fleet.
- **`last_tick_processing_ms`**: Measured as the wall-clock execution loop latency of the telemetry forwarder reading and dispatching snapshots.
- **`active_conflicts`**: Sourced from real peer collision detections logged across the multi-robot fleet.
- **`replans`**: Cumulative count of real conflict resolution detour and yield replans executed across the fleet.
