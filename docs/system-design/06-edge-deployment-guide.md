# Edge Hardware Deployment Guide (SIH26123)

### Edge-AI Based Distributed Fleet Coordination for AMRs in Smart Warehouses
**Target Platforms:** Raspberry Pi 4 Model B (ARM64) & NVIDIA Jetson Nano (ARM64)  
**Operating System:** Ubuntu Server 22.04 LTS 64-bit (`aarch64`)  
**Coordination Policy:** Decentralized Peer-to-Peer with NumPy-GNN Priority Arbitration  

---

## 1. Hardware & System Requirements

| Specification | Primary Target: Raspberry Pi 4 | Secondary Target: NVIDIA Jetson Nano |
| :--- | :--- | :--- |
| **SoC / Architecture** | Broadcom BCM2711, Quad-core Cortex-A72 @ 1.5 GHz | NVIDIA Maxwell, Quad-core Cortex-A57 @ 1.43 GHz |
| **System Memory** | 2 GB or 4 GB LPDDR4-3200 | 4 GB 64-bit LPDDR4 25.6 GB/s |
| **Storage** | 32 GB Class 10 / A1 MicroSD or USB 3.0 SSD | 32 GB MicroSD or 16 GB eMMC |
| **Networking** | 2.4 GHz and 5.0 GHz IEEE 802.11ac Wi-Fi / GbE | Gigabit Ethernet / Dual-band M.2 Wi-Fi module |
| **Power Supply** | 5V / 3A USB-C (Standard industrial DC-DC buck) | 5V / 4A Barrel Jack power supply |

### Why Zero-PyTorch Edge Footprint Matters:
Traditional deep learning stacks require PyTorch (`~1.8 GB` disk, `~450 MB` idle RAM) and CUDA runtime libraries that overwhelm small edge microcomputers. By exporting trained PyTorch GNN model weights to a standalone, vectorized NumPy array file (`priority_gnn_weights.npz`), each AMR node executes full GNN forward passes in **< 1.0 ms** while consuming **only 13.6 MB RAM**.

---

## 2. Network Topology & Port Allocations

In a decentralized warehouse deployment, robots form an ad-hoc or local mesh network. Each AMR is assigned an IP address within the fleet subnet and listens on a dedicated UDP port:

| AMR Node | Hostname | Static IP | UDP Listening Port | Function |
| :--- | :--- | :--- | :---: | :--- |
| **AMR-01** | `amr-01.local` | `192.168.10.101` | `9001` | Goods-to-Person Autonomous Transporter |
| **AMR-02** | `amr-02.local` | `192.168.10.102` | `9002` | Goods-to-Person Autonomous Transporter |
| **AMR-03** | `amr-03.local` | `192.168.10.103` | `9003` | Goods-to-Person Autonomous Transporter |
| **...** | ... | ... | ... | ... |
| **AMR-10** | `amr-10.local` | `192.168.10.110` | `9010` | Goods-to-Person Autonomous Transporter |

- **Protocol:** UDP Unicast / Broadcast over local subnet with HMAC-SHA256 signature verification and monotonic sequence counter replay guard.
- **Heartbeat / Tick Rate:** 10 Hz (`100 ms` control cycle).
- **Latency Budget:** Planning + Priority Arbitration + Conflict Resolution consumes `< 2.0 ms` per tick.

---

## 3. Deployment Option A: Bare-Metal Ubuntu Setup

### Step 1: Base System Preparation
Flash **Ubuntu Server 22.04 LTS (64-bit)** onto the MicroSD card using Raspberry Pi Imager. Connect to the board via SSH:
```bash
sudo apt-get update && sudo apt-get install -y \
    python3-pip \
    python3-venv \
    iproute2 \
    procps \
    git

# Disable swap to protect flash storage and avoid unexpected paging latencies
sudo swapoff -a
```

### Step 2: Clone and Setup Python Runtime
```bash
cd /opt
sudo git clone https://github.com/organization/SIH-2026.git amr-fleet
cd /opt/amr-fleet

# Create lightweight Python virtual environment
python3 -m venv --system-site-packages venv
source venv/bin/activate

# Install strictly edge-lightweight runtime dependencies
pip install --no-cache-dir \
    numpy>=1.26.0 \
    scipy>=1.11.0 \
    psutil>=5.9.0 \
    pydantic>=2.7.0 \
    pydantic-settings>=2.0.0 \
    cryptography>=42.0.0
```

### Step 3: Configure Environment Variables
Create `/etc/amr/node.env` on each AMR:
```bash
sudo mkdir -p /etc/amr
sudo tee /etc/amr/node.env > /dev/null << 'EOF'
ROBOT_ID=AMR-01
ROBOT_PORT=9001
HOST=0.0.0.0
START_POS=2,4
URGENCY=3
BATTERY_PCT=100.0
TICK_INTERVAL_S=0.10
COORDINATION_POLICY=decentralized
SECRET_KEY=sih2026-edge-robot-shared-secret
PYTHONPATH=/opt/amr-fleet:/opt/amr-fleet/backend:/opt/amr-fleet/backend/backend:/opt/amr-fleet/conflict-engine:/opt/amr-fleet/archive/pathfinding
EOF
```

### Step 4: Configure systemd Production Service
Install the systemd unit file at `/etc/systemd/system/amr-node.service`:
```ini
[Unit]
Description=Autonomous AMR Decentralized Coordination Node (SIH26123)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/amr-fleet
EnvironmentFile=/etc/amr/node.env
ExecStart=/opt/amr-fleet/venv/bin/python3 -m app.services.robot_node
Restart=always
RestartSec=2s

# Resource sandboxing & realtime scheduling guarantees
CPUSchedulingPolicy=rr
CPUSchedulingPriority=20
Nice=-10
MemoryMax=512M
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable amr-node.service
sudo systemctl start amr-node.service
```

---

## 4. Deployment Option B: Containerized OCI Deployment

For air-gapped industrial installations, deploy via Docker container using the pre-optimized edge Containerfile (`Dockerfile.edge`):

```bash
# Build multi-arch or native arm64 edge image
docker build -f backend/Dockerfile.edge -t sih26123/amr-node:latest .

# Run standalone container with host networking for ultra-low latency UDP
docker run -d \
  --name amr-01-node \
  --restart always \
  --network host \
  --memory=512m \
  --cpus=2.0 \
  -e ROBOT_ID=AMR-01 \
  -e ROBOT_PORT=9001 \
  -e START_POS=2,4 \
  -e TICK_INTERVAL_S=0.10 \
  -e COORDINATION_POLICY=decentralized \
  sih26123/amr-node:latest
```

---

## 5. Verification & Live Validation

### 1. Monitor Node Status & Telemetry
```bash
# Inspect real-time execution logs
journalctl -u amr-node.service -f -n 50

# Verify UDP socket binding
ss -u -a -p | grep 9001
```

### 2. Verify P2P Wire Traffic
Capture decentralized reservation claims and contract-net bids:
```bash
sudo tcpdump -i any udp port 9001 -X -s 0
```

### 3. Run In-Situ Edge Resource Profiler
Run the edge constraint test suite directly on the hardware:
```bash
cd /opt/amr-fleet
./scripts/emulate_edge_constraints.sh

# Review the benchmark outputs
cat benchmark_results/edge_resource_profile.md
```

---

## 6. Resilience & Single Point of Failure (SPOF) Invariance

In this architecture, **there is no central server in the real-time loop**:
1. If the central warehouse management server crashes or loses network connectivity, all AMRs continue path execution, obstacle avoidance, and contract-net task bidding without interruption.
2. If any peer AMR goes offline or suffers network drops, nearby AMRs detect missing tick reports and enter *Degraded Speed Mode* (50% speed throttle, holding position if unconfirmed within 2 cells) to guarantee **strictly zero collisions**.
3. All critical job transitions are committed to an append-only JSONL journal (`data/job_log.jsonl`) prior to network acknowledgment.
