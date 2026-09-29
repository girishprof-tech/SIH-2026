#!/usr/bin/env bash
# ==============================================================================
# SIH-2026: Backend Bootstrap & Startup Script (Linux / macOS)
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${ROOT_DIR}"
echo "[SIH-2026] Working Directory: ${ROOT_DIR}"

if [ ! -f ".venv/bin/activate" ]; then
    echo "[SIH-2026] Virtual environment not found. Creating .venv..."
    python3 -m venv .venv || python -m venv .venv
fi

echo "[SIH-2026] Activating virtual environment..."
# shellcheck source=/dev/null
source .venv/bin/activate

echo "[SIH-2026] Verifying dependencies..."
python -m pip install -q -r requirements.txt

echo "[SIH-2026] Launching backend server on port 8000..."
exec python -m uvicorn app.main:app --app-dir backend/backend --port 8000
