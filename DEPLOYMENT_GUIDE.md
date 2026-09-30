# SIH-2026 Fleet System — Fast Deployment Guide

This project has been unified into a **Single Fullstack Architecture**:
The FastAPI backend directly serves the production 3D React dashboard, API endpoints, and real-time WebSockets on a **single port** with **0 CORS errors** and **auto-detected URLs**.

---

## 🚀 Option 1: Render.com (Recommended Free Cloud Deployment ~3 mins)

Render natively supports WebSockets and Docker with free SSL and automatic deployments.

### Steps:
1. **Push your code to GitHub**:
   ```bash
   git add .
   git commit -m "Prepare production deployment"
   git push origin main
   ```
2. Go to [dashboard.render.com](https://dashboard.render.com/) and click **New +** > **Web Service**.
3. Connect your **SIH-2026** GitHub repository.
4. Render will automatically detect the root `Dockerfile` and `render.yaml`.
   - **Runtime**: Docker
   - **Plan**: Free
   - **Port**: 8000
5. Click **Create Web Service**.
6. In ~2–3 minutes, Render builds the container and gives you a live URL:
   `https://sih2026-fleet-control.onrender.com`

---

## ⚡ Option 2: Instant Public Tunnel in 30 Seconds (Cloudflare Tunnel)

If you need a live public HTTPS URL **right now** for a presentation or demo without signing up for any cloud services:

1. **Start the backend locally**:
   ```powershell
   python -m uvicorn app.main:app --app-dir backend/backend --port 8000
   ```
2. **Open a new terminal and run Cloudflare Tunnel**:
   ```powershell
   npx -y cloudflared tunnel --url http://localhost:8000
   ```
   *(Or download `cloudflared.exe` from Cloudflare)*
3. It will print a public URL like:
   `https://random-words-here.trycloudflare.com`
4. Anyone on the internet (or on a smartphone) can open that URL to interact with the full 3D simulation and live WebSockets!

---

## 🚂 Option 3: Railway.app (Fast Cloud Alternative ~2 mins)

1. Install the Railway CLI (or use [railway.app](https://railway.app)):
   ```bash
   npm i -g @railway/cli
   railway login
   ```
2. In the project root, run:
   ```bash
   railway up
   ```
3. Generate domain:
   ```bash
   railway domain
   ```
4. Done! Your app is live with persistent WebSocket support.

---

## 🐳 Option 4: Docker / Self-Hosted VPS

If deploying to an Ubuntu/Debian VPS or testing locally with Docker:

```bash
docker compose up --build -d
```
The application will be running at `http://localhost:8000` (or `http://YOUR_SERVER_IP:8000`).

---

## 🛠️ Verification Checklist

- [x] **Frontend Production Build**: `npm run build` generates optimized chunks in `frontend/dist/`.
- [x] **Unified Static Serving**: `app.main:app` automatically detects `frontend/dist/` and mounts `/assets` + SPA routing.
- [x] **Dynamic Protocol & Origin**: `frontend/src/api.ts` automatically switches between `http://` / `ws://` and `https://` / `wss://` based on `window.location`.
- [x] **Container Ready**: Root `Dockerfile` multi-stage build bundles Node 20 frontend build + Python 3.11 runtime.
