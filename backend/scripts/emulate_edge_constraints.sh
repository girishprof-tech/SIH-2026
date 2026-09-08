#!/usr/bin/env bash
# emulate_edge_constraints.sh — Run edge hardware profiling on Linux/Pi/Jetson
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

export PYTHONPATH="${ROOT_DIR}:${ROOT_DIR}/backend:${ROOT_DIR}/backend/backend:${ROOT_DIR}/conflict-engine:${ROOT_DIR}/archive/pathfinding:${PYTHONPATH:-}"
export COORDINATION_POLICY="decentralized"

echo "================================================================================"
echo "SIH26123: Launching Edge Hardware Constraints Emulation & Profiler"
echo "Target Platform: Raspberry Pi 4 / NVIDIA Jetson Nano (ARM64)"
echo "================================================================================"

python3 "${SCRIPT_DIR}/emulate_edge_constraints.py" "$@"
