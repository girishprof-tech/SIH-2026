# 01 — System Architecture Overview

## Executive Summary
The **SIH-2026 Edge-AI Autonomous Mobile Robot (AMR) Fleet Coordination System** solves Problem Statement 123 using a **fully decentralized, peer-to-peer coordination architecture**. Unlike traditional centralized warehouse management systems (where a single orchestrator computes all paths and commands robots as dumb actuators), each AMR in this system is an **autonomous intelligent agent running in its own operating system process**.

The centralized components (FastAPI backend and React frontend) operate strictly as a **non-authoritative telemetry viewer and task dispatcher**. If the centralized server crashes, is killed, or suffers network disconnection, the decentralized AMR fleet continues operating, arbitrating, navigating, and completing missions without interruption.

---

## C4 Container & Architecture Boundary Diagram

The diagram below illustrates the strict boundary between the **Centralized Telemetry/UI Plane** and the **Decentralized Edge-AI Robot Execution Plane**.

```mermaid
graph TB
    subgraph Centralized_Plane ["CENTRALIZED TELEMETRY & MANAGEMENT PLANE (Non-Authoritative)"]
        direction TB
        UI["React Frontend Dashboard<br/><i>(Vite, TypeScript, Canvas 2D)</i><br/>• ControlBar & Status Badges<br/>• 30x30 Warehouse GridCanvas<br/>• Live Fleet Metrics & Task Panels"]
        
        API["FastAPI Telemetry Server<br/><i>(uvicorn app.main:app)</i><br/>• REST Dispatcher: POST /api/job<br/>• WebSocket Endpoint: /ws/fleet<br/>• Pure Telemetry Forwarder Loop"]
        
        TBus["TelemetryBus Aggregator<br/><i>(app.services.telemetry_bus)</i><br/>• Drains mp.Queue<br/>• Atomic Write: logs/telemetry_state.json"]
        
        Orch["FleetOrchestrator<br/><i>(app.services.fleet_orchestrator)</i><br/>• Process Lifecycle Supervisor<br/>• Pause / Resume Events"]
        
        UI <== "WebSocket Push: TICK_UPDATE (every tick)<br/>REST Polling: /api/chaos, /health (5s)" ==> API
        API <--> Orch
        TBus -->|"Atomic File Read"| API
    end

    subgraph IPC_Bridge ["INTER-PROCESS COMMUNICATION (IPC) BOUNDARY"]
        TQueue[("multiprocessing.Queue<br/><i>(telemetry_queue)</i>")]
    end

    subgraph Decentralized_Plane ["DECENTRALIZED EDGE-AI AMR FLEET PLANE (Authoritative Core)"]
        direction TB
        
        subgraph AMR_01 ["AMR Node 01 (OS Process: PID 101)"]
            N1["RobotNode Core<br/>• FSM (IDLE, EN_ROUTE, CONFLICT, etc.)<br/>• Space-Time A* Pathfinder<br/>• Local Reservation Table"]
            U1["UDP Transport Socket<br/>Port: 9001"]
            N1 <--> U1
        end

        subgraph AMR_02 ["AMR Node 02 (OS Process: PID 102)"]
            N2["RobotNode Core<br/>• FSM (IDLE, EN_ROUTE, CONFLICT, etc.)<br/>• Space-Time A* Pathfinder<br/>• Local Reservation Table"]
            U2["UDP Transport Socket<br/>Port: 9002"]
            N2 <--> U2
        end

        subgraph AMR_N ["AMR Node N (OS Process: PID 100+N)"]
            NN["RobotNode Core<br/>• Heterogeneous Capabilities<br/>• GOODS_TO_PERSON / SORTING / AUDIT<br/>• Battery & E-Stop Guards"]
            UN["UDP Transport Socket<br/>Port: 9000+N"]
            NN <--> UN
        end

        %% Peer to Peer Mesh
        U1 <===>|"UDP Datagrams (HMAC-SHA256 Signed)<br/>• RESERVATION_CLAIM (Phase 1)<br/>• Conflict Arbitration (Phase 2)<br/>• ReplayGuard (Monotonic Seq)"| U2
        U2 <===>|"Peer-to-Peer Mesh Network"| UN
        U1 <===>|"Peer-to-Peer Mesh Network"| UN
    end

    %% Telemetry Flow
    N1 -.->|"Push State Frame"| TQueue
    N2 -.->|"Push State Frame"| TQueue
    NN -.->|"Push State Frame"| TQueue
    TQueue -.-> TBus
    API -.->|"Dispatch Task via UDP"| U1
    API -.->|"Dispatch Task via UDP"| U2
    API -.->|"Dispatch Task via UDP"| UN

    classDef centralized fill:#1e293b,stroke:#38bdf8,stroke-width:2px,color:#f8fafc;
    classDef decentralized fill:#0f172a,stroke:#10b981,stroke-width:2px,color:#f8fafc;
    classDef boundary fill:#334155,stroke:#f59e0b,stroke-dasharray: 5 5,stroke-width:2px,color:#f8fafc;
    class Centralized_Plane centralized;
    class Decentralized_Plane decentralized;
    class IPC_Bridge boundary;
```

---

## Component Responsibilities

| Plane | Component | Technology | Primary Responsibility | Failure Consequence |
| :--- | :--- | :--- | :--- | :--- |
| **Centralized** | **React Frontend** | React 18, TypeScript, Canvas 2D | Real-time warehouse rendering, operator controls, job creation, obstacle injection, and telemetry graphs. | Operator loses visual dashboard; robots continue moving. |
| **Centralized** | **FastAPI Server** | FastAPI, Uvicorn, Asyncio | Pure telemetry viewer forwarding JSON frames from disk to WebSocket; job intake API (`POST /api/job`). | WebSocket closes; robots continue autonomous execution. |
| **Centralized** | **TelemetryBus** | Python Thread, `mp.Queue` | Drains non-blocking state frames pushed by robots and writes atomic snapshots to `logs/telemetry_state.json`. | Telemetry snapshots cease; robot navigation unaffected. |
| **Centralized** | **FleetOrchestrator** | Python Multiprocessing | Supervisor for starting and stopping robot OS child processes and allocating UDP ports. | Only invoked at startup/shutdown; not in hot path. |
| **Decentralized** | **RobotNode** | Python Process, Dataclasses, FSM | Independent decision-making agent. Runs Space-Time A* pathfinding, local reservation tracking, battery management, and task fulfillment. | If one robot fails, other robots detect the missing heartbeat and route around it. |
| **Decentralized** | **Conflict Engine** | Deterministic Python Module | Symmetric 2-phase conflict detection (`detect_peer_conflict`) and arbitration (`resolve_peer_conflict`) using dynamic priority formula. | Runs locally on every node; zero single point of failure. |
| **Decentralized** | **UDP Transport** | OS UDP Sockets (`SOCK_DGRAM`) | High-speed peer-to-peer communication between robots on ports `9001`–`9010`. Includes HMAC-SHA256 signatures and timestamp/sequence replay guards. | Bounded packet loss handled via degraded-mode heuristics. |

---

## Centralized vs. Decentralized Boundary Verification

A critical architectural requirement for SIH-2026 is verifying that the edge AMRs are **genuinely decentralized** and do not rely on FastAPI for navigation decisions.

### Crash Resilience Proof (`test_decentralization.py`)
1. 5 AMR processes are spawned with intersecting trajectories through a narrow corridor.
2. The central FastAPI server (`uvicorn`) is started, connected, and then **forcefully killed (`SIGKILL`)**.
3. While FastAPI is dead and unreachable:
   - AMRs detect mutual head-on conflicts over peer-to-peer UDP.
   - Dynamic priority scores are calculated locally.
   - The lower-priority robot yields and steps into a side nook.
   - The higher-priority robot passes cleanly.
   - All events are logged independently to `/logs/robot_AMR-XX.log`.
4. FastAPI is restarted; it immediately reconnects to the live fleet and resumes streaming telemetry without restarting or disturbing the robots.

```
[Decentralization Test Result]
FastAPI Server Killed at Tick 25  -> AMRs continue ticking autonomously.
Peer Conflict Arbitrated at Tick 38 -> AMR-02 yields to AMR-01 via UDP.
FastAPI Server Restarted at Tick 50 -> Reconnects to ongoing fleet operation.
Zero Centralized Dependency Verified: PASSED.
```
