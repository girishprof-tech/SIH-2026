# ==============================================================================
# Multi-Stage Production Build: SIH-2026 Autonomous Fleet System
# Unified Backend + 3D Frontend Container
# ==============================================================================

# ── Stage 1: Build React 3D Frontend ──────────────────────────────────────────
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# ── Stage 2: Python 3.11 Backend & Unified Runtime ────────────────────────────
FROM python:3.11-slim AS runner

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend, models, algorithms, and data
COPY backend/ ./backend/
COPY conflict-engine/ ./conflict-engine/
COPY maps/ ./maps/
COPY data/ ./data/

# Copy built frontend assets from stage 1
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# Set python environment variables
ENV PYTHONPATH="/app:/app/backend:/app/backend/backend:/app/conflict-engine"
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

EXPOSE 8000

# Launch server dynamically binding to $PORT (standard on Render/Railway/Fly/CloudRun)
CMD ["sh", "-c", "uvicorn app.main:app --app-dir backend/backend --host 0.0.0.0 --port ${PORT:-8000}"]
