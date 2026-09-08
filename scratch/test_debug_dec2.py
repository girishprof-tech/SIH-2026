import os, sys
from pathlib import Path
ROOT_DIR = Path(".").resolve()
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))
from app.transport.loopback_transport import LoopbackNetworkHub, LoopbackTransport
from app.services.robot_node import RobotNode
from app.security.hmac_envelope import sign_payload

open_lanes = [1, 2, 3, 4, 6, 7, 8, 9, 11, 12, 13, 14, 16, 17, 18, 19, 21, 22, 23, 24, 25, 26, 27, 28]

def get_scenario(seed, fleet_size):
    half = fleet_size // 2
    obstacles = []
    for y in (5, 10, 15, 20):
        for x in range(3, 27):
            if x not in (7, 14, 21): obstacles.append((x, y))
    cfgs = []
    for i in range(fleet_size):
        rid = f"AMR-{i+1:02d}"
        if i < half:
            s = (2, open_lanes[i])
            g = (27, open_lanes[(i + 7) % len(open_lanes)])
        else:
            idx = i - half
            s = (27, open_lanes[idx])
            g = (2, open_lanes[(idx + 7) % len(open_lanes)])
        cfgs.append({"robot_id": rid, "start": s, "goal": g, "urgency": 1, "battery_pct": 100.0})
    return {"fleet_size": fleet_size, "seed": seed, "obstacles": obstacles, "robots_config": cfgs}

sc = get_scenario(1000, 15)
os.environ["COORDINATION_POLICY"] = "decentralized"
hub = LoopbackNetworkHub()
peer_ports = {c["robot_id"]: 9000 + i for i, c in enumerate(sc["robots_config"])}
nodes = {c["robot_id"]: RobotNode(
    robot_id=c["robot_id"], start_pos=c["start"], goal_pos=c["goal"],
    urgency=c["urgency"], battery_pct=c["battery_pct"],
    obstacles=sc["obstacles"], port=peer_ports[c["robot_id"]], peer_ports=peer_ports,
    transport=LoopbackTransport(c["robot_id"], hub=hub), log_dir=ROOT_DIR/"logs"/"debug", tick_interval_s=0.0,
    enable_idle_audit=False
) for c in sc["robots_config"]}

for n in nodes.values():
    claim = {
        "type": "RESERVATION_CLAIM", "robot_id": n.robot.robot_id, "robot_type": n.robot_type,
        "tick": 0, "position": list(n.robot.position), "intended_pos": list(n.robot.position),
        "heading": n.robot.heading.value, "priority_score": n.robot.priority_score,
        "state": n.fsm.state.value, "wait_ticks": 0, "path": [], "charger_target": None
    }
    env = sign_payload(claim, secret_key=n.secret_key, seq=n.seq)
    for p in n.peer_ports:
        if p != n.robot.robot_id: n.transport.send(p, env)
for n in nodes.values(): n._drain_inbox(0)

for tick in range(60):
    for rid, n in nodes.items():
        if tick in (58, 59) and rid in ("AMR-08", "AMR-11"):
            print(f"Tick {tick} {rid} START: pos={n.robot.position}, path={n.robot.path[:3]}")
        frame = n.step(tick)
        if tick in (58, 59) and rid in ("AMR-08", "AMR-11"):
            print(f"Tick {tick} {rid} END: pos={n.robot.position}, act={frame['action']}, conf={frame['conflict']}")

for n in nodes.values(): n.close()
