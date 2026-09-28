import json
from pathlib import Path

# Let's inspect logs/telemetry_state.json or logs/repro_20robot
log_path = Path("c:/Users/STAR/OneDrive/Desktop/SIH-2026/logs/repro_20robot")
p = Path("c:/Users/STAR/OneDrive/Desktop/SIH-2026/logs/telemetry_state.json")
if p.exists():
    data = json.loads(p.read_text(encoding="utf-8"))
    serialized = json.dumps(data, separators=(",", ":"))
    print(f"telemetry_state.json size: {len(serialized)} bytes ({len(serialized)/1024:.2f} KB)")
    print(f"Number of robots: {len(data.get('robots', []))}")
